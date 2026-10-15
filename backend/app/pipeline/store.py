"""Project storage helpers: per-project directories, current plan assembly, history."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Character, HistoryEntry, Page, PageRevision, Project, StageLog
from ..schemas import CharacterSpec, PageSpec, Scene, StoryPlan
from ..story.themes import THEMES

ROLE_SET = {"setup", "inciting", "rising", "climax", "resolution", "ending"}


def project_dir(pid: str) -> Path:
    d = settings.projects_dir / pid
    for sub in ("images", "refs", "pdf", "render", "uploads", "audio", "exports"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def rel(pid: str, path: Path) -> str:
    return str(Path(path).resolve().relative_to(project_dir(pid).resolve()))


def absf(pid: str, relpath: str) -> Path | None:
    if not relpath:
        return None
    p = (project_dir(pid) / relpath).resolve()
    if project_dir(pid).resolve() not in p.parents:
        return None
    return p


def role_of(beat: str, theme: str) -> str:
    if beat in ROLE_SET:
        return beat
    t = THEMES.get(theme)
    if t:
        for b in t["beats"]:
            if b["id"] == beat:
                return b["role"]
    return "rising"


def current_plan(s: Session, project: Project) -> StoryPlan:
    """The authoritative plan: metadata from story_plan, pages and characters from their tables."""
    sp = dict(project.story_plan or {})
    pages = s.scalars(select(Page).where(Page.project_id == project.id).order_by(Page.number)).all()
    chars = s.scalars(select(Character).where(Character.project_id == project.id)).all()
    theme = sp.get("theme", "")
    arc: dict[str, list[int]] = {}
    specs = []
    for p in pages:
        arc.setdefault(role_of(p.beat, theme), []).append(p.number)
        specs.append(PageSpec(number=p.number, beat=p.beat or "rising", text=p.text, image_description=p.image_prompt,
                              characters_present=list(p.characters_present or []), scene=Scene(**(p.scene or {}))))
    cspecs = [CharacterSpec(**c.spec) for c in chars] if chars else [CharacterSpec(**c) for c in sp.get("characters", [])]
    order = {"protagonist": 0, "companion": 1, "supporting": 2}
    cspecs.sort(key=lambda c: order.get(c.role, 3))
    return StoryPlan(title=project.title or sp.get("title", ""), subtitle=sp.get("subtitle", ""), summary=sp.get("summary", ""),
                     moral=sp.get("moral", ""), theme=theme, language=project.language, age_group=project.age_group,
                     characters=cspecs, locations=sp.get("locations", []), props=sp.get("props", []), arc=arc, pages=specs,
                     back_cover_blurb=sp.get("back_cover_blurb", ""), engine=sp.get("engine", ""))


def add_history(s: Session, pid: str, action: str, detail: dict | None = None, actor: str = "system") -> None:
    s.add(HistoryEntry(project_id=pid, action=action, detail=detail or {}, actor=actor))


def stage_log(s: Session, pid: str, job_id: str, stage: str, status: str, message: str = "", data: dict | None = None) -> None:
    s.add(StageLog(project_id=pid, job_id=job_id, stage=stage, status=status, message=message[:2000], data=data or {}))


def renumber(s: Session, pid: str) -> None:
    pages = s.scalars(select(Page).where(Page.project_id == pid).order_by(Page.number, Page.id)).all()
    for i, p in enumerate(pages, start=1):
        p.number = i


MAX_REVISIONS_PER_PAGE = 50


def snapshot_page(s: Session, page: Page, reason: str, actor: str = "system") -> PageRevision | None:
    """Store the page's current state before it changes (undo/restore). Identical
    consecutive states are stored once; the oldest snapshots beyond the cap are pruned
    (image files are never deleted, so every kept snapshot stays restorable)."""
    last = s.scalar(select(PageRevision).where(PageRevision.page_id == page.id).order_by(PageRevision.id.desc()).limit(1))
    if last and last.text == page.text and last.image_file == page.image_file and last.image_prompt == page.image_prompt:
        return None
    rev = PageRevision(project_id=page.project_id, page_id=page.id, number=page.number, reason=reason, actor=actor, text=page.text,
                       image_prompt=page.image_prompt, image_file=page.image_file, image_status=page.image_status,
                       image_meta={k: v for k, v in (page.image_meta or {}).items() if k != "renderer_meta"}, qa=page.qa or {})
    s.add(rev)
    s.flush()
    ids = s.scalars(select(PageRevision.id).where(PageRevision.page_id == page.id).order_by(PageRevision.id.desc())).all()
    for old in ids[MAX_REVISIONS_PER_PAGE:]:
        s.delete(s.get(PageRevision, old))
    return rev
