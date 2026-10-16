from __future__ import annotations

import io
import json
import os
import shutil
import time
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Character, HistoryEntry, Job, Page, PageRevision, Project, ProviderConfig, StageLog, UsageRecord
from ..pipeline.store import absf, add_history, current_plan, project_dir, renumber, snapshot_page
from ..pipeline.worker import JobConflict, enqueue
from ..presets import PRESETS
from ..providers.registry import PROVIDER_TYPES, build, cost_of, estimate, get_defaults, validate_config
from ..schemas import BookSettings, CharacterSpec, Scene
from ..security import encrypt_secret, mask_secret, require_admin, require_auth, safe_join
from ..story.offline_engine import MAX_PAGES, MIN_PAGES

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ------------------------------------------------------------------ helpers
def get_project(db: Session, pid: str, user: str = "local") -> Project:
    p = db.get(Project, pid)
    if p is None or p.owner_id != user:
        raise HTTPException(404, "Book not found")
    return p


def file_url(pid: str, relpath: str) -> str | None:
    return f"/api/projects/{pid}/files/{relpath}" if relpath else None


def page_out(pid: str, p: Page) -> dict:
    return {"id": p.id, "number": p.number, "beat": p.beat, "text": p.text, "image_prompt": p.image_prompt, "scene": p.scene,
            "characters_present": p.characters_present, "image_url": file_url(pid, p.image_file), "image_status": p.image_status,
            "image_locked": p.image_locked, "image_meta": {k: v for k, v in (p.image_meta or {}).items() if k not in ("renderer_meta",)},
            "qa": p.qa or {}, "audio_url": file_url(pid, p.audio_file),
            "audio_meta": {k: v for k, v in (p.audio_meta or {}).items() if k != "key"},
            "words": list((p.audio_meta or {}).get("words") or []),
            "timing_source": (p.audio_meta or {}).get("timing_source", ""),
            "duration_s": float((p.audio_meta or {}).get("duration_s") or 0),
            "inpaints": len((p.image_meta or {}).get("inpaints", []))}


def project_summary(p: Project) -> dict:
    cover = (p.stage_state or {}).get("cover", {}).get("file", "")
    return {"id": p.id, "title": p.title, "author": p.author, "language": p.language, "age_group": p.age_group, "status": p.status,
            "stage": p.stage, "progress": p.progress, "archived": p.archived, "version": p.version, "has_pdf": bool(p.pdf_file),
            "created_at": p.created_at.isoformat() + "Z", "updated_at": p.updated_at.isoformat() + "Z", "error": p.error,
            "cover_url": file_url(p.id, cover), "qa_summary": (p.qa_report or {}).get("summary"),
            "page_count": (p.settings or {}).get("page_count")}


def provider_out(c: ProviderConfig) -> dict:
    return {"id": c.id, "name": c.name, "kind": c.kind, "provider_type": c.provider_type, "base_url": c.base_url, "model": c.model,
            "api_key": mask_secret(c.api_key), "has_api_key": bool(c.api_key), "params": {k: v for k, v in (c.params or {}).items() if k != "workflow"},
            "has_workflow": bool((c.params or {}).get("workflow")), "enabled": c.enabled, "builtin": c.builtin, "fallback_id": c.fallback_id,
            "last_check": c.last_check}


EXPORT_FORMATS = {"epub": ("application/epub+zip", "epub"), "html": ("text/html; charset=utf-8", "html"),
                  "images": ("application/zip", "zip"), "video": ("video/webm", "webm"),
                  "video_mp4": ("video/mp4", "mp4")}


def export_info(p: Project) -> dict:
    return {k: v for k, v in ((p.stage_state or {}).get("exports") or {}).items() if k in EXPORT_FORMATS}


def book_data(db: Session, p: Project):
    from ..export.books import BookData, BookPage
    plan = current_plan(db, p)
    pages = db.scalars(select(Page).where(Page.project_id == p.id).order_by(Page.number)).all()
    cover = absf(p.id, (p.stage_state or {}).get("cover", {}).get("file", ""))
    return BookData(id=p.id, title=p.title or plan.title, language=p.language, author=(p.settings or {}).get("author", ""),
                    publisher=(p.settings or {}).get("publisher", ""), summary=plan.summary, blurb=plan.back_cover_blurb or plan.summary,
                    cover=cover if cover and cover.is_file() else None, version=p.version,
                    pages=[BookPage(number=x.number, text=x.text, image=absf(p.id, x.image_file), audio=absf(p.id, x.audio_file),
                                    alt=(x.image_prompt or "").split(".")[0],
                                    words=list((x.audio_meta or {}).get("words") or []),
                                    timing_source=(x.audio_meta or {}).get("timing_source", ""),
                                    duration_s=float((x.audio_meta or {}).get("duration_s") or 0)) for x in pages])


def job_conflict(e: JobConflict):
    raise HTTPException(409, str(e))


# ------------------------------------------------------------------ system
def _system_extras() -> dict:
    from ..db import db_info
    from ..export.books import epubcheck_available
    from ..export.video import video_available
    from ..locks import lock_info
    return {"database": db_info(), "locks": lock_info(), "sso": sso_status_public(),
            "smtp": {"configured": settings.smtp_configured},
            "media": {"epubcheck": epubcheck_available()[0], "video": video_available()}}


def sso_status_public() -> dict:
    if not settings.oidc_configured:
        return {"enabled": False}
    return {"enabled": True, "label": settings.oidc_label or settings.oidc_issuer}


@router.get("/system")
def system(db: Session = Depends(get_db)):
    import shutil as sh
    du = sh.disk_usage(settings.data_dir)
    used = sum(f.stat().st_size for f in settings.data_dir.rglob("*") if f.is_file())
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
    mem = {}
    try:
        for line in open("/proc/meminfo"):
            k, v = line.split(":")
            if k in ("MemTotal", "MemAvailable"):
                mem[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    q = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    usage = db.execute(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0), func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                              func.coalesce(func.sum(UsageRecord.output_tokens), 0), func.coalesce(func.sum(UsageRecord.images), 0),
                              func.count())).one()
    statuses = dict(db.execute(select(Project.status, func.count()).where(Project.archived.is_(False)).group_by(Project.status)).all())
    return {"queue": q, "projects": statuses, "data_bytes": used, "disk_free": du.free, "disk_total": du.total,
            "disk_limit_bytes": settings.max_disk_gb * 1e9, "load": load, "cpus": os.cpu_count(), "memory": mem,
            "limits": {"books": settings.max_concurrent_books, "images": settings.max_concurrent_images},
            "usage": {"cost_usd": round(float(usage[0]), 4), "input_tokens": int(usage[1]), "output_tokens": int(usage[2]),
                      "images": int(usage[3]), "calls": int(usage[4])},
            "offline_engine": {"min_pages": MIN_PAGES, "max_pages": MAX_PAGES}, "env": settings.env,
            "auth_enabled": settings.auth_mode != "none", "auth_mode": settings.auth_mode,
            **_system_extras()}


@router.get("/presets")
def presets():
    return PRESETS


@router.get("/options")
def options():
    s = BookSettings.model_json_schema()
    return {"schema": s, "provider_types": PROVIDER_TYPES}


# ------------------------------------------------------------------ providers
class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str
    provider_type: str
    base_url: str = Field("", max_length=300)
    api_key: str | None = Field(None, max_length=500)  # None = keep existing
    model: str = Field("", max_length=200)
    params: dict = {}
    workflow: str | None = None  # ComfyUI API-format workflow JSON
    enabled: bool = True
    fallback_id: str = ""


def _apply_provider(c: ProviderConfig, body: ProviderIn):
    errs = validate_config(body.kind, body.provider_type, body.base_url, body.model)
    if errs:
        raise HTTPException(422, "; ".join(errs))
    c.name, c.kind, c.provider_type, c.base_url, c.model, c.enabled = body.name, body.kind, body.provider_type, body.base_url.strip(), body.model.strip(), body.enabled
    params = {k: v for k, v in (body.params or {}).items() if k not in ("api_key", "workflow")}
    if body.workflow is not None:
        if body.workflow.strip():
            try:
                params["workflow"] = json.loads(body.workflow)
            except json.JSONDecodeError as e:
                raise HTTPException(422, f"Workflow is not valid JSON: {e}")
    elif (c.params or {}).get("workflow"):
        params["workflow"] = c.params["workflow"]
    c.params = params
    if body.api_key is not None:
        c.api_key = encrypt_secret(body.api_key.strip())
    c.fallback_id = body.fallback_id if body.fallback_id != c.id else ""


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)):
    return {"providers": [provider_out(c) for c in db.scalars(select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.created_at))],
            "defaults": get_defaults(db), "types": PROVIDER_TYPES}


@router.post("/providers", status_code=201, dependencies=[Depends(require_admin)])
def create_provider(body: ProviderIn, db: Session = Depends(get_db)):
    c = ProviderConfig()
    _apply_provider(c, body)
    db.add(c)
    db.commit()
    return provider_out(c)


@router.put("/providers/{prov_id}", dependencies=[Depends(require_admin)])
def update_provider(prov_id: str, body: ProviderIn, db: Session = Depends(get_db)):
    c = db.get(ProviderConfig, prov_id)
    if not c:
        raise HTTPException(404, "Provider not found")
    if c.builtin:
        raise HTTPException(400, "Built-in providers cannot be modified")
    _apply_provider(c, body)
    db.commit()
    return provider_out(c)


@router.delete("/providers/{prov_id}", dependencies=[Depends(require_admin)])
def delete_provider(prov_id: str, db: Session = Depends(get_db)):
    c = db.get(ProviderConfig, prov_id)
    if not c:
        raise HTTPException(404, "Provider not found")
    if c.builtin:
        raise HTTPException(400, "Built-in providers cannot be deleted")
    from ..models import AppSetting
    d = db.get(AppSetting, "defaults")
    if d and prov_id in (d.value or {}).values():
        raise HTTPException(409, "Provider is a default; choose another default first")
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.post("/providers/{prov_id}/check", dependencies=[Depends(require_admin)])
def check_provider(prov_id: str, db: Session = Depends(get_db)):
    from ..logging_setup import redact
    c = db.get(ProviderConfig, prov_id)
    if not c:
        raise HTTPException(404, "Provider not found")
    t0 = time.monotonic()
    try:
        res = build(c).check()
        res = {"ok": bool(res.get("ok", True)), **res}
    except Exception as e:
        res = {"ok": False, "error": redact(f"{e.__class__.__name__}: {e}")[:400]}
    res["latency_ms"] = int((time.monotonic() - t0) * 1000)
    res["checked_at"] = datetime.utcnow().isoformat() + "Z"
    c.last_check = res
    db.commit()
    return res


class DefaultsIn(BaseModel):
    llm: str = ""
    review_llm: str = ""
    image: str = ""
    vision: str = ""
    tts: str = ""
    inpaint: str = ""  # "" = built-in classical (OpenCV) repair


@router.put("/providers-defaults", dependencies=[Depends(require_admin)])
def set_defaults(body: DefaultsIn, db: Session = Depends(get_db)):
    from ..models import AppSetting
    for kinds, pid in (("llm", body.llm), ("image", body.image), ("vision", body.vision), ("llm", body.review_llm), ("tts", body.tts),
                       (("inpaint", "image"), body.inpaint)):
        if pid:
            c = db.get(ProviderConfig, pid)
            if not c or c.kind not in (kinds if isinstance(kinds, tuple) else (kinds,)):
                raise HTTPException(422, f"{pid} is not a {kinds} provider")
            if c.kind == "image" and c.provider_type == "fastsdcpu" and kinds == ("inpaint", "image"):
                raise HTTPException(422, "FastSD CPU cannot repair regions")
    if not body.llm or not body.image:
        raise HTTPException(422, "A default story provider and image provider are required")
    row = db.get(AppSetting, "defaults")
    row.value = body.model_dump()
    db.commit()
    return row.value


@router.get("/usage")
def usage(request: Request, db: Session = Depends(get_db), project_id: str = ""):
    from ..security import current_user
    q = select(UsageRecord).order_by(UsageRecord.id.desc()).limit(200)
    u = current_user(request)
    if u["role"] != "admin":  # regular accounts only see calls made for their own books
        q = q.where(UsageRecord.project_id.in_(select(Project.id).where(Project.owner_id == u["id"])))
    if project_id:
        q = q.where(UsageRecord.project_id == project_id)
    return [{"provider": u.provider_name, "kind": u.kind, "model": u.model, "purpose": u.purpose, "input_tokens": u.input_tokens,
             "output_tokens": u.output_tokens, "images": u.images, "duration_ms": u.duration_ms, "cost_usd": u.cost_usd,
             "success": u.success, "error": u.error, "at": u.created_at.isoformat() + "Z", "project_id": u.project_id} for u in db.scalars(q)]


# ------------------------------------------------------------------ projects
class CreateIn(BaseModel):
    settings: BookSettings
    start: bool = True
    confirm_cost: bool = False


@router.post("/estimate")
def estimate_cost(body: BookSettings, db: Session = Depends(get_db)):
    d = get_defaults(db)
    llm = db.get(ProviderConfig, body.llm_provider_id or d["llm"])
    img = db.get(ProviderConfig, body.image_provider_id or d["image"])
    tts = db.get(ProviderConfig, body.tts_provider_id or d.get("tts") or "") if body.narration_audio else None
    est = estimate(body.model_dump(), llm, img, tts)
    warn = []
    if (llm is None or llm.provider_type == "offline") and not (MIN_PAGES <= body.page_count <= MAX_PAGES):
        warn.append(f"The offline story engine supports {MIN_PAGES}-{MAX_PAGES} pages; connect an LLM provider for {body.page_count} pages.")
    if llm and llm.provider_type == "offline" and body.language not in ("en", "fa"):
        warn.append("The offline engine writes English and Persian only.")
    est["warnings"] = warn
    est["providers"] = {"llm": llm.name if llm else None, "image": img.name if img else None, "tts": tts.name if tts else None}
    return est


@router.get("/projects")
def list_projects(db: Session = Depends(get_db), user: str = Depends(require_auth), archived: bool = False, status: str = "", q: str = ""):
    stmt = select(Project).where(Project.owner_id == user, Project.archived.is_(archived)).order_by(Project.created_at.desc())
    if status:
        stmt = stmt.where(Project.status.in_(status.split(",")))
    if q:
        stmt = stmt.where(Project.title.ilike(f"%{q[:100]}%"))
    return [project_summary(p) for p in db.scalars(stmt)]


@router.post("/projects", status_code=201)
def create_project(body: CreateIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    est = estimate_cost(body.settings, db)
    if est["warnings"]:
        raise HTTPException(422, " ".join(est["warnings"]))
    if est["paid"] and est["cost_max_usd"] > 0 and not body.confirm_cost:
        raise HTTPException(402, {"message": "This run uses paid APIs. Confirm the cost estimate to continue.", "estimate": est})
    used = sum(f.stat().st_size for f in settings.projects_dir.rglob("*") if f.is_file())
    if used > settings.max_disk_gb * 1e9:
        raise HTTPException(507, "Disk quota for book projects is exhausted; delete or archive books first.")
    s = body.settings
    p = Project(owner_id=user, title=s.title, author=s.author, language=s.language, age_group=s.age_group, settings=s.model_dump(),
                status="draft")
    db.add(p)
    db.flush()
    add_history(db, p.id, "project_created", {"settings": s.model_dump(), "estimate": est}, actor="user")
    db.commit()
    project_dir(p.id)
    if body.start:
        enqueue(p.id, "generate_book")
    db.refresh(p)
    return project_summary(p)


@router.get("/projects/{pid}")
def project_detail(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    pages = db.scalars(select(Page).where(Page.project_id == pid).order_by(Page.number)).all()
    chars = db.scalars(select(Character).where(Character.project_id == pid)).all()
    jobs = db.scalars(select(Job).where(Job.project_id == pid).order_by(Job.created_at.desc()).limit(10)).all()
    version = p.version
    renders = []
    rdir = project_dir(pid) / "render" / f"v{version}"
    if rdir.is_dir():
        renders = [{"page": int(f.stem.split("-")[1]), "url": file_url(pid, f"render/v{version}/{f.name}"),
                    "thumb": file_url(pid, f"render/v{version}/thumb-{f.stem.split('-')[1]}.jpg")} for f in sorted(rdir.glob("page-*.jpg"))]
    sp = p.story_plan or {}
    return {**project_summary(p), "settings": p.settings, "stage_state": p.stage_state,
            "plan": {k: sp.get(k) for k in ("summary", "moral", "theme", "engine", "locations", "back_cover_blurb", "arc")},
            "qa_report": p.qa_report, "pages": [page_out(pid, x) for x in pages],
            "characters": [{"id": c.id, "char_id": c.char_id, "name": c.name, "role": c.role, "spec": c.spec,
                            "ref_url": file_url(pid, c.ref_image_file), "ref_approved": c.ref_approved} for c in chars],
            "jobs": [{"id": j.id, "kind": j.kind, "status": j.status, "attempts": j.attempts, "message": j.message,
                      "created_at": j.created_at.isoformat() + "Z"} for j in jobs],
            "renders": renders, "pdf_url": f"/api/projects/{pid}/pdf" if p.pdf_file else None,
            "cast_url": file_url(pid, (p.stage_state or {}).get("characters", {}).get("cast_file", "")),
            "cast_ai_url": file_url(pid, (p.stage_state or {}).get("characters", {}).get("ai_cast_file", "")),
            "relative_heights": (p.stage_state or {}).get("characters", {}).get("relative_heights", {}),
            "exports": export_info(p)}


class ProjectPatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    author: str | None = Field(None, max_length=120)
    archived: bool | None = None
    settings: dict | None = None  # partial settings (applied on next rebuild)


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectPatch, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    changes = {}
    if body.title is not None:
        changes["title"] = [p.title, body.title]
        p.title = body.title.strip()
    if body.author is not None:
        p.author = body.author.strip()
        st = dict(p.settings)
        st["author"] = p.author
        p.settings = st
    if body.archived is not None:
        p.archived = body.archived
        changes["archived"] = body.archived
    if body.settings:
        allowed = {"page_size", "orientation", "page_numbers", "back_cover", "cover_style", "output_profile", "publisher", "author", "max_image_attempts",
                   "budget_usd", "layout_style", "bleed_mm", "crop_marks", "color_mode", "narration_audio", "tts_provider_id", "narration_voice",
                   "narration_speed", "cast_reference", "review_outline"}
        bad = set(body.settings) - allowed
        if bad:
            raise HTTPException(422, f"These settings cannot be changed after creation: {sorted(bad)}")
        merged = BookSettings(**{**p.settings, **body.settings})
        p.settings = merged.model_dump()
        changes["settings"] = body.settings
    add_history(db, pid, "project_updated", changes, actor="user")
    db.commit()
    return project_summary(p)


@router.delete("/projects/{pid}")
def delete_project(pid: str, confirm: str = Query("", description="must equal the book id"), db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    if confirm != pid:
        raise HTTPException(400, "Deletion must be confirmed with ?confirm=<book id>")
    if db.scalar(select(Job.id).where(Job.project_id == pid, Job.status == "running")):
        raise HTTPException(409, "Stop the running task before deleting")
    src = settings.projects_dir / pid
    if src.exists():  # move to trash instead of deleting files (recoverable)
        dst = settings.trash_dir / f"{pid}-{int(time.time())}"
        shutil.move(str(src), str(dst))
        (dst / "project.json").write_text(json.dumps({"title": p.title, "settings": p.settings, "story_plan": p.story_plan}, ensure_ascii=False))
    db.delete(p)
    db.commit()
    return {"ok": True, "trash": True}


@router.post("/projects/{pid}/start")
def start(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    p.pause_requested = False
    if p.status == "cancelled":
        p.status = "draft"
    db.commit()
    try:
        j = enqueue(pid, "generate_book")
    except JobConflict as e:
        job_conflict(e)
    return {"job_id": j.id}


@router.post("/projects/{pid}/pause")
def pause(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    queued = db.scalar(select(Job).where(Job.project_id == pid, Job.status == "queued"))
    if queued:
        queued.status = "cancelled"
        p.status = "paused"
    else:
        p.pause_requested = True
    add_history(db, pid, "pause_requested", actor="user")
    db.commit()
    return {"ok": True}


@router.post("/projects/{pid}/cancel")
def cancel(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    for j in db.scalars(select(Job).where(Job.project_id == pid, Job.status == "queued")):
        j.status = "cancelled"
    p.status = "cancelled"
    add_history(db, pid, "cancelled", actor="user")
    db.commit()
    return {"ok": True}


def _enqueue(pid, kind, payload=None):
    try:
        return {"job_id": enqueue(pid, kind, payload).id}
    except JobConflict as e:
        job_conflict(e)


@router.post("/projects/{pid}/rebuild")
def rebuild(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    return _enqueue(pid, "rebuild_pdf")


@router.post("/projects/{pid}/cover/regenerate")
def regen_cover(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    return _enqueue(pid, "regenerate_cover")


# ------------------------------------------------------------------ pages
class PagePatch(BaseModel):
    text: str | None = Field(None, max_length=3000)
    image_prompt: str | None = Field(None, max_length=3000)
    scene: dict | None = None
    image_locked: bool | None = None
    image_status: str | None = Field(None, pattern="^(approved|rejected)$")


@router.patch("/projects/{pid}/pages/{page_id}")
def patch_page(pid: str, page_id: str, body: PagePatch, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    detail = {"page": pg.number}
    if (body.text is not None and body.text.strip() != pg.text) or (body.image_prompt is not None and body.image_prompt != pg.image_prompt):
        snapshot_page(db, pg, "before_edit", actor="user")
    if body.text is not None and body.text.strip() != pg.text:
        if not body.text.strip():
            raise HTTPException(422, "Page text cannot be empty")
        detail["text"] = {"before": pg.text, "after": body.text.strip()}
        pg.text = body.text.strip()
    if body.image_prompt is not None and body.image_prompt != pg.image_prompt:
        detail["image_prompt"] = {"before": pg.image_prompt, "after": body.image_prompt}
        pg.image_prompt = body.image_prompt
    if body.scene is not None:
        sc = Scene(**{**(pg.scene or {}), **body.scene})
        known = {c.char_id for c in db.scalars(select(Character).where(Character.project_id == pid))}
        if any(c not in known for c in sc.characters):
            raise HTTPException(422, "Scene references unknown characters")
        pg.scene = sc.model_dump()
        pg.characters_present = list(sc.characters)
        detail["scene"] = body.scene
    if body.image_locked is not None:
        pg.image_locked = body.image_locked
        detail["locked"] = body.image_locked
    if body.image_status:
        pg.image_status = body.image_status
        if body.image_status == "approved":
            pg.image_locked = True
        detail["image_status"] = body.image_status
    add_history(db, pid, "page_edited", detail, actor="user")
    db.commit()
    return page_out(pid, pg)


class PageAdd(BaseModel):
    after: int = Field(..., ge=0)
    text: str = Field(..., min_length=1, max_length=3000)
    image_prompt: str = Field("", max_length=3000)
    copy_scene_from: int | None = None


@router.post("/projects/{pid}/pages", status_code=201)
def add_page(pid: str, body: PageAdd, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    pages = db.scalars(select(Page).where(Page.project_id == pid).order_by(Page.number)).all()
    if len(pages) >= 40:
        raise HTTPException(422, "Maximum 40 story pages")
    src = next((x for x in pages if x.number == (body.copy_scene_from or body.after)), pages[-1] if pages else None)
    for x in pages:
        if x.number > body.after:
            x.number += 1
    np_ = Page(project_id=pid, number=min(body.after + 1, len(pages) + 1), beat="rising", text=body.text.strip(),
               image_prompt=body.image_prompt or (src.image_prompt if src else ""), scene=dict(src.scene) if src else {},
               characters_present=list(src.characters_present) if src else [])
    db.add(np_)
    db.flush()
    renumber(db, pid)
    st = dict(p.settings)
    st["page_count"] = len(pages) + 1
    p.settings = st
    add_history(db, pid, "page_added", {"page": np_.number, "text": np_.text}, actor="user")
    db.commit()
    return page_out(pid, np_)


@router.delete("/projects/{pid}/pages/{page_id}")
def delete_page(pid: str, page_id: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    count = db.scalar(select(func.count()).select_from(Page).where(Page.project_id == pid))
    if count <= 2:
        raise HTTPException(422, "A book needs at least 2 story pages")
    add_history(db, pid, "page_deleted", {"page": pg.number, "text": pg.text, "image": pg.image_file}, actor="user")
    db.delete(pg)
    db.flush()
    renumber(db, pid)
    st = dict(p.settings)
    st["page_count"] = count - 1
    p.settings = st
    db.commit()
    return {"ok": True}


class ReorderIn(BaseModel):
    order: list[str]


@router.post("/projects/{pid}/pages/reorder")
def reorder(pid: str, body: ReorderIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    pages = {x.id: x for x in db.scalars(select(Page).where(Page.project_id == pid))}
    if set(body.order) != set(pages) or len(body.order) != len(pages):
        raise HTTPException(422, "Order must list every page exactly once")
    before = [pages[i].number for i in body.order]
    for n, pid_ in enumerate(body.order, start=1):
        pages[pid_].number = n
    add_history(db, pid, "pages_reordered", {"new_order_old_numbers": before}, actor="user")
    db.commit()
    return {"ok": True}


class RegenImageIn(BaseModel):
    prompt: str = Field("", max_length=3000)


@router.post("/projects/{pid}/pages/{page_id}/regenerate-image")
def regen_image(pid: str, page_id: str, body: RegenImageIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    return _enqueue(pid, "regenerate_image", {"page_id": page_id, "prompt": body.prompt})


class RegenTextIn(BaseModel):
    instruction: str = Field("", max_length=600)


@router.post("/projects/{pid}/pages/{page_id}/regenerate-text")
def regen_text(pid: str, page_id: str, body: RegenTextIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    return _enqueue(pid, "regenerate_text", {"page_id": page_id, "instruction": body.instruction})


@router.post("/projects/{pid}/approve-outline")
def approve_outline(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Review gate: the user has read/edited the story text; continue with illustration."""
    p = get_project(db, pid, user)
    if not db.scalar(select(Page.id).where(Page.project_id == pid).limit(1)):
        raise HTTPException(409, "There is no story plan to approve yet")
    st = dict(p.stage_state or {})
    st["outline"] = {"approved": True, "at": time.time()}
    p.stage_state = st
    add_history(db, pid, "outline_approved", actor="user")
    db.commit()
    return _enqueue(pid, "generate_book")


@router.post("/projects/{pid}/pages/{page_id}/inpaint")
async def inpaint_page(pid: str, page_id: str, mask: UploadFile = File(...), prompt: str = Form("", max_length=600),
                       engine: str = Form("default", max_length=40), db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Region repair: `mask` is a PNG where painted (white or opaque) pixels mark the area to repair.
    engine: "default" (configured default), "classical" (built-in OpenCV) or a provider id."""
    from ..illustration.inpaint import MaskError, load_mask, validate_mask
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    src = absf(pid, pg.image_file)
    if not src or not src.is_file():
        raise HTTPException(409, "This page has no illustration yet")
    if engine not in ("default", "classical"):
        cfg = db.get(ProviderConfig, engine)
        if not cfg or not cfg.enabled or cfg.kind not in ("image", "inpaint") or cfg.provider_type == "fastsdcpu":
            raise HTTPException(422, "Choose an image or inpainting provider that can repair regions")
    data = await mask.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "Mask too large")
    try:
        with Image.open(src) as im:
            size = im.size
        cov = validate_mask(load_mask(data, size))
    except MaskError as e:
        raise HTTPException(422, str(e))
    name = f"uploads/mask-p{pg.number:02d}-{int(time.time() * 1000)}.png"
    load_mask(data, size).save(project_dir(pid) / name)  # normalised, metadata-free copy
    out = _enqueue(pid, "inpaint_image", {"page_id": page_id, "mask": name, "prompt": prompt.strip(), "engine": engine})
    return {**out, "mask_coverage": round(cov, 4)}


class NarrateIn(BaseModel):
    page_id: str = ""
    force: bool = False
    confirm_cost: bool = False


@router.post("/projects/{pid}/narrate")
def narrate(pid: str, body: NarrateIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    if body.page_id:
        pg = db.get(Page, body.page_id)
        if not pg or pg.project_id != pid:
            raise HTTPException(404, "Page not found")
    # paid TTS: announce the cost before spending money (same contract as book creation)
    p = db.get(Project, pid)
    d = get_defaults(db)
    tts = db.get(ProviderConfig, (p.settings or {}).get("tts_provider_id") or d.get("tts") or "")
    if tts is not None:
        pages = db.scalars(select(Page).where(Page.project_id == pid)).all()
        todo = [x for x in pages if (not body.page_id or x.id == body.page_id) and (body.force or not x.audio_file)]
        chars = sum(len(x.text or "") for x in todo)
        cost = cost_of(tts, chars=chars)
        if cost > 0 and not body.confirm_cost:
            raise HTTPException(402, json.dumps({"message": f"Narration uses a paid TTS service ({tts.name}): about {chars} characters, ~${cost:.4f}. "
                                                            "Confirm the cost to continue.", "estimate": {"tts_chars": chars, "tts_cost_usd": round(cost, 4)}}))
    return _enqueue(pid, "narrate", {"page_id": body.page_id, "force": body.force})


def revision_out(pid: str, r: PageRevision) -> dict:
    return {"id": r.id, "number": r.number, "reason": r.reason, "actor": r.actor, "text": r.text, "image_prompt": r.image_prompt,
            "image_url": file_url(pid, r.image_file), "image_status": r.image_status, "at": r.created_at.isoformat() + "Z",
            "qa_ok": (r.qa or {}).get("image", {}).get("ok") if r.qa else None}


@router.get("/projects/{pid}/pages/{page_id}/revisions")
def page_revisions(pid: str, page_id: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    if not pg or pg.project_id != pid:
        raise HTTPException(404, "Page not found")
    rows = db.scalars(select(PageRevision).where(PageRevision.page_id == page_id).order_by(PageRevision.id.desc())).all()
    return [revision_out(pid, r) for r in rows]


class RestoreIn(BaseModel):
    restore_text: bool = True
    restore_image: bool = True
    rebuild: bool = True


@router.post("/projects/{pid}/pages/{page_id}/revisions/{rev_id}/restore")
def restore_revision(pid: str, page_id: str, rev_id: int, body: RestoreIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Undo: bring back the text and/or illustration of an earlier snapshot. The current
    state is snapshotted first, so a restore can itself be undone."""
    get_project(db, pid, user)
    pg = db.get(Page, page_id)
    rev = db.get(PageRevision, rev_id)
    if not pg or pg.project_id != pid or not rev or rev.page_id != page_id:
        raise HTTPException(404, "Revision not found")
    if db.scalar(select(Job.id).where(Job.project_id == pid, Job.status.in_(["queued", "running"]))):
        raise HTTPException(409, "Wait until the running task has finished")
    if body.restore_image and rev.image_file and not (absf(pid, rev.image_file) and absf(pid, rev.image_file).is_file()):
        raise HTTPException(410, "The image file of this revision no longer exists")
    snapshot_page(db, pg, "before_restore", actor="user")
    if body.restore_text and rev.text:
        pg.text = rev.text
        pg.image_prompt = rev.image_prompt or pg.image_prompt
    if body.restore_image and rev.image_file:
        pg.image_file = rev.image_file
        pg.image_status = rev.image_status if rev.image_status in ("generated", "approved", "failed") else "generated"
        meta = dict(pg.image_meta or {})
        meta.update({k: v for k, v in (rev.image_meta or {}).items() if k not in ("next_variant", "attempts")})
        pg.image_meta = meta
        pg.qa = rev.qa or {}
    add_history(db, pid, "page_restored", {"page": pg.number, "revision": rev.id, "text": body.restore_text, "image": body.restore_image}, actor="user")
    db.commit()
    out = {"ok": True, "page": page_out(pid, pg)}
    if body.rebuild:
        out.update(_enqueue(pid, "rebuild_pdf") or {})
    return out


# ------------------------------------------------------------------ characters
class CharPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    spec: dict | None = None
    regenerate_images: bool = True


@router.patch("/projects/{pid}/characters/{char_row}")
def patch_character(pid: str, char_row: str, body: CharPatch, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    c = db.get(Character, char_row)
    if not c or c.project_id != pid:
        raise HTTPException(404, "Character not found")
    old_name = c.name
    spec = dict(c.spec)
    if body.spec:
        for k in ("appearance", "outfit"):
            if k in body.spec:
                spec[k] = {**spec.get(k, {}), **body.spec[k]}
        for k in ("description", "personality", "distinctive", "palette", "age", "pronoun"):
            if k in body.spec:
                spec[k] = body.spec[k]
        if "relationships" in body.spec and isinstance(body.spec["relationships"], dict):
            known = {x.char_id for x in db.scalars(select(Character).where(Character.project_id == pid))}
            rels = {k: v for k, v in (spec.get("relationships") or {}).items() if k.startswith("_")}  # keep internal keys
            for other, how in body.spec["relationships"].items():
                if other not in known or other == c.char_id:
                    raise HTTPException(422, f"Unknown character in relationships: {other}")
                if str(how).strip():
                    rels[other] = str(how).strip()[:80]
            spec["relationships"] = rels
    if body.name:
        spec["name"] = body.name.strip()
    spec = CharacterSpec(**spec).model_dump()
    # keep the signature palette in sync with edited colours
    app, out = spec["appearance"], spec["outfit"]
    if app.get("species") in ("human", "human_old"):
        spec["palette"] = [x for x in [app.get("skin"), app.get("hair_color"), out.get("top_color"), out.get("accessory_color")] if x]
    else:
        spec["palette"] = [x for x in [app.get("skin"), out.get("accessory_color")] if x]
    c.spec = spec
    c.name = spec["name"]
    visual_change = body.spec and any(k in body.spec for k in ("appearance", "outfit", "description"))
    if visual_change and set(body.spec.get("appearance", {})) <= {"scale"} and not any(k in body.spec for k in ("outfit", "description")):
        # size only: the reference sheet stays valid; pages and cast line-up must be redrawn
        visual_change = "scale"
    affected = 0
    if body.name and old_name != c.name:
        for pg in db.scalars(select(Page).where(Page.project_id == pid)):
            if old_name in pg.text:
                pg.text = pg.text.replace(old_name, c.name)
                affected += 1
    if visual_change:
        if visual_change != "scale":
            c.ref_image_file, c.ref_approved = "", False
        st = dict(p.stage_state or {})
        st["characters"] = {"status": "pending"}
        st["images"] = {"status": "pending"}
        st.pop("cover", None)
        p.stage_state = st
        if body.regenerate_images:
            for pg in db.scalars(select(Page).where(Page.project_id == pid)):
                if c.char_id in (pg.characters_present or []) and not pg.image_locked:
                    pg.image_status = "pending"
    add_history(db, pid, "character_edited", {"char_id": c.char_id, "changes": body.spec or {}, "renamed_pages": affected,
                                              "images_invalidated": bool(visual_change)}, actor="user")
    db.commit()
    return {"ok": True, "pages_renamed": affected, "images_invalidated": bool(visual_change)}


ALLOWED_UPLOAD = {"PNG", "JPEG", "WEBP"}


@router.post("/projects/{pid}/characters/{char_row}/reference")
async def upload_reference(pid: str, char_row: str, file: UploadFile = File(...), db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    c = db.get(Character, char_row)
    if not c or c.project_id != pid:
        raise HTTPException(404, "Character not found")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB")
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ALLOWED_UPLOAD:
            raise HTTPException(415, "Only PNG, JPEG or WebP images are accepted")
        im.verify()
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > 40_000_000:
            raise HTTPException(413, "Image dimensions too large")
        im = im.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(415, "File is not a valid image")
    # re-encode: strips metadata and anything that is not pixels; random-free deterministic name
    path = project_dir(pid) / "refs" / f"{c.char_id}-upload.png"
    im.save(path, format="PNG")
    c.ref_image_file = f"refs/{path.name}"
    c.ref_approved = True
    add_history(db, pid, "reference_uploaded", {"char_id": c.char_id}, actor="user")
    db.commit()
    return {"ok": True, "ref_url": file_url(pid, c.ref_image_file)}


# ------------------------------------------------------------------ history, logs, files
@router.get("/projects/{pid}/history")
def history(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    rows = db.scalars(select(HistoryEntry).where(HistoryEntry.project_id == pid).order_by(HistoryEntry.id.desc()).limit(300))
    return [{"id": h.id, "at": h.created_at.isoformat() + "Z", "actor": h.actor, "action": h.action, "detail": h.detail} for h in rows]


@router.get("/projects/{pid}/logs")
def logs(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    rows = db.scalars(select(StageLog).where(StageLog.project_id == pid).order_by(StageLog.id.desc()).limit(300))
    return [{"id": r.id, "at": r.created_at.isoformat() + "Z", "stage": r.stage, "status": r.status, "message": r.message, "data": r.data} for r in rows]


@router.get("/projects/{pid}/files/{path:path}")
def project_file(pid: str, path: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    get_project(db, pid, user)
    target = safe_join(project_dir(pid), path)
    if not target.is_file() or target.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".json", ".mp3", ".wav"):
        raise HTTPException(404, "File not found")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})


@router.get("/projects/{pid}/pdf")
def download_pdf(pid: str, inline: bool = False, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    f = absf(pid, p.pdf_file)
    if not f or not f.is_file():
        raise HTTPException(404, "No PDF has been built yet")
    safe_title = "".join(ch for ch in (p.title or "book") if ch.isalnum() or ch in " -_").strip()[:60] or "book"
    return FileResponse(f, media_type="application/pdf", filename=f"{safe_title}-v{p.version}.pdf",
                        content_disposition_type="inline" if inline else "attachment")


@router.get("/projects/{pid}/export")
def export_project(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Backup: zip with project metadata, plan, QA report, images and the latest PDF."""
    p = get_project(db, pid, user)
    plan = current_plan(db, p).model_dump()
    buf = io.BytesIO()
    root = project_dir(pid)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps({"id": p.id, "title": p.title, "settings": p.settings, "plan": plan, "qa_report": p.qa_report,
                                               "version": p.version}, ensure_ascii=False, indent=1))
        for sub in ("images", "refs", "pdf"):
            for f in (root / sub).glob("*"):
                if f.is_file():
                    z.write(f, f"{sub}/{f.name}")
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="book-{pid[:8]}.zip"'})


class ExportIn(BaseModel):
    format: str = Field(..., pattern="^(epub|html|images|video|video_mp4)$")
    validate_epub: bool = True
    confirm_gpl: bool = Field(False, description="MP4/H.264 links the GPL-2+ x264 encoder: confirm you accept that")


@router.post("/projects/{pid}/exports")
def build_export(pid: str, body: ExportIn, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """Build an EPUB 3 / single-file HTML reader / images ZIP from the current pages (+ narration if present)."""
    from ..export.books import build_epub, build_html, build_images_zip, build_video, validate_epub
    p = get_project(db, pid, user)
    if db.scalar(select(Job.id).where(Job.project_id == pid, Job.status.in_(["queued", "running"]))):
        raise HTTPException(409, "Wait until the running task has finished")
    book = book_data(db, p)
    if not book.pages:
        raise HTTPException(409, "The book has no pages yet")
    missing = [x.number for x in book.pages if not (x.image and x.image.is_file())]
    ext = EXPORT_FORMATS[body.format][1]
    out = project_dir(pid) / "exports" / f"book-v{p.version}.{ext}"
    t0 = time.monotonic()
    if body.format == "epub":
        info = build_epub(book, out)
        info["validation"] = validate_epub(out) if body.validate_epub else {"checked": False, "ok": None, "note": "skipped"}
    elif body.format == "html":
        info = build_html(book, out)
    elif body.format in ("video", "video_mp4"):
        try:
            import av  # noqa: F401
        except ImportError:
            raise HTTPException(409, "Video export needs the optional package: pip install av")
        if body.format == "video_mp4" and not body.confirm_gpl:
            raise HTTPException(402, {"message": "MP4/H.264 is encoded with x264, which is licensed GPL-2+. "
                                                 "Use WebM/VP9 (royalty-free, default) or confirm the GPL terms.",
                                      "estimate": {"container": "mp4", "video": "libx264 (GPL-2+)", "audio": "aac"}})
        info = build_video(book, out, fmt="webm" if body.format == "video" else "mp4")
        missing_audio = [x.number for x in book.pages if not (x.audio and x.audio.is_file())]
        info["pages_without_audio"] = missing_audio
    else:
        info = build_images_zip(book, out)
    info.update({"file": f"exports/{out.name}", "version": p.version, "built_at": datetime.utcnow().isoformat() + "Z",
                 "seconds": round(time.monotonic() - t0, 2), "pages_without_image": missing})
    st = dict(p.stage_state or {})
    ex = dict(st.get("exports") or {})
    ex[body.format] = info
    st["exports"] = ex
    p.stage_state = st
    add_history(db, pid, "export_built", {"format": body.format, "bytes": info["bytes"],
                                          "valid": (info.get("validation") or {}).get("ok")}, actor="user")
    db.commit()
    return info


@router.get("/projects/{pid}/lora-dataset")
def lora_dataset(pid: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    """ZIP with a ready-to-train LoRA dataset (kohya / ai-toolkit) for every character.

    Bookmaker does not train models: it prepares the images, the captions (trigger word
    first) and the trainer configs. Training needs a GPU and kohya ss or ai-toolkit."""
    from ..export.lora import build_dataset
    p = get_project(db, pid, user)
    plan = current_plan(db, p)
    chars = db.scalars(select(Character).where(Character.project_id == pid)).all()
    pages = db.scalars(select(Page).where(Page.project_id == pid).order_by(Page.number)).all()
    by_id = {c.char_id: c for c in chars}
    samples: dict[str, list] = {}
    for ch in chars:
        paths = []
        if ch.ref_image_file:
            f = absf(pid, ch.ref_image_file)
            if f and f.is_file():
                paths.append(f)
        samples[ch.char_id] = paths
    for pg in pages:                       # every illustration the character appears in
        f = absf(pid, pg.image_file)
        if not (f and f.is_file()):
            continue
        for cid in (pg.characters_present or []):
            if cid in samples:
                samples[cid].append(f)
    specs = [dict(c.spec or {}) | {"id": c.char_id, "name": c.name} for c in chars]
    out = project_dir(pid) / "exports" / f"lora-dataset-v{p.version}.zip"
    info = build_dataset(specs, samples, (p.settings or {}).get("illustration_style", "watercolor"), out)
    if not info["characters"]:
        raise HTTPException(409, "No character images yet: generate the illustrations first")
    st = dict(p.stage_state or {})
    ex = dict(st.get("exports") or {})
    ex["lora"] = info
    st["exports"] = ex
    p.stage_state = st
    add_history(db, pid, "lora_dataset_built", {"images": info["images"], "characters": len(info["characters"])}, actor="user")
    db.commit()
    safe_title = "".join(ch for ch in (p.title or "book") if ch.isalnum() or ch in " -_").strip()[:60] or "book"
    return FileResponse(out, media_type="application/zip", filename=f"{safe_title}-lora-dataset.zip")


@router.get("/projects/{pid}/exports/{fmt}")
def download_export(pid: str, fmt: str, db: Session = Depends(get_db), user: str = Depends(require_auth)):
    p = get_project(db, pid, user)
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(404, "Unknown format")
    info = export_info(p).get(fmt)
    f = absf(pid, info["file"]) if info else None
    if not f or not f.is_file():
        raise HTTPException(404, "Build this export first")
    safe_title = "".join(ch for ch in (p.title or "book") if ch.isalnum() or ch in " -_").strip()[:60] or "book"
    suffix = {"images": "-images.zip"}.get(fmt, f".{EXPORT_FORMATS[fmt][1]}")
    return FileResponse(f, media_type=EXPORT_FORMATS[fmt][0], filename=f"{safe_title}-v{info['version']}{suffix}")
