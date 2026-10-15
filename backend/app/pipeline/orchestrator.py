"""Book generation pipeline. Every stage is resumable: its state is stored in
project.stage_state, approved/locked assets are never regenerated, and each
repair loop is bounded (attempts, time and budget)."""
from __future__ import annotations

import io
import json
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import select

from ..config import settings as app_settings
from ..db import session_scope
from ..illustration.image_qa import check_image, find_duplicates, fit_to_aspect
from ..illustration.prompts import build_cover_prompt, build_page_prompt, build_reference_prompt
from ..layout.book import build_layouts
from ..layout.engine import ASPECTS, page_size
from ..layout.pdf import render_pdf
from ..layout.pdf_qa import validate_pdf
from ..models import Character, Page, Project, ProviderConfig
from ..providers.base import ProviderError
from ..providers.offline import ProceduralImage
from ..providers.registry import Router, get_defaults
from ..schemas import BookSettings, StoryPlan
from ..story import offline_engine
from ..story.llm_engine import generate_plan_llm, regenerate_page_llm
from ..story.validator import validate_plan
from .store import absf, add_history, current_plan, project_dir, rel, stage_log

log = logging.getLogger("pipeline")

STAGES = ["plan", "characters", "images", "pdf", "qa"]
PROGRESS = {"plan": (0, 15), "characters": (15, 22), "images": (22, 82), "pdf": (82, 95), "qa": (95, 100)}
IMG_LONG_SIDE = {"screen": 1280, "print": 2048}


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class Pipeline:
    def __init__(self, project_id: str, job_id: str = ""):
        self.pid = project_id
        self.job_id = job_id
        with session_scope() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise ValueError("project not found")
            self.s = BookSettings(**(p.settings or {}))
            d = get_defaults(s)
            self.llm_id = self.s.llm_provider_id or d.get("llm")
            self.image_id = self.s.image_provider_id or d.get("image")
            self.vision_id = self.s.vision_provider_id or d.get("vision") or ""
            llm = s.get(ProviderConfig, self.llm_id) if self.llm_id else None
            img = s.get(ProviderConfig, self.image_id) if self.image_id else None
            self.llm_offline = llm is None or llm.provider_type == "offline"
            self.image_offline = img is None or img.provider_type == "offline"
            self.image_model_name = (img.name if img else "procedural") or "procedural"
            self.llm_model_name = (llm.name if llm else "offline") or "offline"
        self.router = Router(project_id, self.s.budget_usd)
        self.dir = project_dir(project_id)
        self.aspect = ASPECTS[self.s.aspect_ratio]
        self.seed = self.s.seed if self.s.seed is not None else offline_engine.settings_seed(self.s)

    # ------------------------------------------------------------------ helpers
    def _update(self, **kw):
        with session_scope() as s:
            p = s.get(Project, self.pid)
            for k, v in kw.items():
                setattr(p, k, v)

    def _state(self) -> dict:
        with session_scope() as s:
            return dict(s.get(Project, self.pid).stage_state or {})

    def _set_stage(self, stage: str, status: str, **extra):
        with session_scope() as s:
            p = s.get(Project, self.pid)
            st = dict(p.stage_state or {})
            st[stage] = {"status": status, "at": time.time(), **extra}
            p.stage_state = st
            p.stage = stage
            if status == "running":
                p.progress = float(PROGRESS[stage][0])
            elif status == "done":
                p.progress = float(PROGRESS[stage][1])

    def _progress(self, stage: str, frac: float):
        lo, hi = PROGRESS[stage]
        self._update(progress=round(lo + (hi - lo) * max(0.0, min(1.0, frac)), 1))

    def _log(self, stage, status, message="", data=None):
        with session_scope() as s:
            stage_log(s, self.pid, self.job_id, stage, status, message, data)
        log.info("[%s] %s %s: %s", self.pid[:8], stage, status, message)

    def checkpoint(self):
        with session_scope() as s:
            p = s.get(Project, self.pid)
            if p.status == "cancelled":
                raise Cancelled()
            if p.pause_requested:
                raise Paused()

    def llm_call(self, purpose: str):
        def call(prompt: str, system: str, json_mode: bool) -> str:
            return self.router.run(self.llm_id, "llm", purpose,
                                   lambda prov, cfg: prov.complete(prompt, system, json_mode, temperature=0.8, max_tokens=6000)).text
        return call

    # ------------------------------------------------------------------ run
    def run_full(self):
        self._update(status="running", error="", pause_requested=False)
        try:
            for stage in STAGES:
                st = self._state().get(stage, {})
                if st.get("status") == "done" and stage not in ("pdf", "qa"):
                    continue
                self.checkpoint()
                getattr(self, f"stage_{stage}")()
        except Paused:
            self._update(status="paused", pause_requested=False)
            self._log("pipeline", "warning", "Paused by user; progress is saved and can be resumed")
            with session_scope() as s:
                add_history(s, self.pid, "paused")
        except Cancelled:
            self._log("pipeline", "warning", "Cancelled by user")

    # ------------------------------------------------------------------ stage: plan
    def stage_plan(self):
        self._set_stage("plan", "running")
        with session_scope() as s:
            if s.scalar(select(Page.id).where(Page.project_id == self.pid).limit(1)):
                # pages already exist (resume after crash between commit and stage update)
                self._set_stage("plan", "done", note="existing pages kept")
                return
        report, notes = None, []
        plan: StoryPlan | None = None
        if not self.llm_offline:
            try:
                plan, meta = generate_plan_llm(self.llm_call("story_plan"), self.s, self.s.max_story_attempts,
                                               log=lambda ev, d: self._log("plan", "ok", ev, d))
                report = meta["final"]
                notes.append(f"LLM attempts: {len(meta['attempts'])}")
            except (ProviderError, ValueError) as e:
                self._log("plan", "error", f"LLM story generation failed: {e}")
                if self.s.page_count < offline_engine.MIN_PAGES or self.s.page_count > offline_engine.MAX_PAGES:
                    raise
                notes.append(f"LLM failed ({e.__class__.__name__}); used the offline engine as fallback")
                plan = None
        if plan is None:
            last_err = None
            for k in range(self.s.max_story_attempts):
                s2 = self.s.model_copy(update={"seed": self.seed + k * 7919})
                cand = offline_engine.generate_plan(s2)
                rep = validate_plan(cand, self.s)
                self._log("plan", "ok" if rep["ok"] else "warning", f"offline plan attempt {k + 1}",
                          {"errors": rep["errors"], "warnings": rep["warnings"]})
                plan, report = cand, rep
                if rep["ok"]:
                    break
                last_err = rep
            if report and not report["ok"]:
                notes.append("story still has validation errors after retries")
        with session_scope() as s:
            p = s.get(Project, self.pid)
            data = plan.model_dump()
            pages = data.pop("pages")
            p.story_plan = data
            if not p.title:
                p.title = plan.title
            for pg in pages:
                s.add(Page(project_id=self.pid, number=pg["number"], beat=pg["beat"], text=pg["text"], image_prompt=pg["image_description"],
                           scene=pg["scene"], characters_present=pg["characters_present"]))
            for c in plan.characters:
                s.add(Character(project_id=self.pid, char_id=c.id, name=c.name, role=c.role, spec=c.model_dump()))
            q = dict(p.qa_report or {})
            q["story_generation"] = report
            p.qa_report = q
            add_history(s, self.pid, "story_planned", {"engine": plan.engine, "pages": len(pages), "notes": notes})
        (self.dir / "plan.json").write_text(json.dumps(plan.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
        self._set_stage("plan", "done", engine=plan.engine, notes=notes)

    # ------------------------------------------------------------------ stage: characters
    def stage_characters(self):
        self._set_stage("characters", "running")
        with session_scope() as s:
            chars = [(c.id, c.char_id, c.spec, c.ref_image_file) for c in s.scalars(select(Character).where(Character.project_id == self.pid))]
        for i, (row_id, cid, spec, ref) in enumerate(chars):
            if ref and absf(self.pid, ref) and absf(self.pid, ref).exists():
                continue
            self.checkpoint()
            if self.image_offline:
                res = ProceduralImage().reference_sheet(spec, self.s.illustration_style)
                approved = True
            else:
                prompt, neg = build_reference_prompt(spec, self.s.illustration_style)
                res = self.router.run(self.image_id, "image", "character_reference",
                                      lambda prov, cfg: prov.generate(prompt, neg, 1024, 768, seed=self.seed, style=self.s.illustration_style,
                                                                      characters=[spec]))
                q = check_image(res.data, 4 / 3, ocr=False)
                approved = q["ok"] or any(x["code"] == "aspect_mismatch" for x in q["issues"])
            path = self.dir / "refs" / f"{cid}.png"
            path.write_bytes(res.data)
            with session_scope() as s:
                c = s.get(Character, row_id)
                c.ref_image_file = rel(self.pid, path)
                c.ref_approved = approved
            self._progress("characters", (i + 1) / max(1, len(chars)))
        with session_scope() as s:
            add_history(s, self.pid, "character_bible_ready", {"characters": [c[1] for c in chars]})
        self._set_stage("characters", "done")

    # ------------------------------------------------------------------ stage: images
    def _characters(self):
        with session_scope() as s:
            rows = s.scalars(select(Character).where(Character.project_id == self.pid)).all()
            return [r.spec for r in rows], {r.char_id: absf(self.pid, r.ref_image_file) for r in rows if r.ref_image_file and r.ref_approved}

    def _img_size(self, aspect: float) -> tuple[int, int]:
        long_side = IMG_LONG_SIDE[self.s.output_profile]
        if aspect >= 1:
            return long_side, int(round(long_side / aspect))
        return int(round(long_side * aspect)), long_side

    def _vision_review(self, data: bytes, page_text: str, expected: list[str]) -> list[dict]:
        if not self.vision_id:
            return []
        prompt = ("You are checking an illustration for a children's book page. Page text: " + page_text[:600] +
                  f"\nExpected characters: {expected}. Answer JSON: {{\"relevance\": 1-5, \"characters_visible\": [..], "
                  "\"has_text_or_letters\": bool, \"anatomy_problems\": bool, \"notes\": str}")
        try:
            res = self.router.run(self.vision_id, "vision", "image_review", lambda prov, cfg: prov.describe(data, prompt))
            from ..story.llm_engine import extract_json
            r = extract_json(res.text)
        except Exception as e:
            return [{"code": "vision_unavailable", "severity": "warning", "message": f"Vision review failed: {e}", "stage": "image"}]
        issues = []
        if int(r.get("relevance", 5)) < 3:
            issues.append({"code": "irrelevant", "severity": "error", "message": f"Vision model: low relevance ({r.get('relevance')}). {r.get('notes', '')}", "stage": "image"})
        if r.get("has_text_or_letters"):
            issues.append({"code": "text_in_image", "severity": "error", "message": "Vision model saw text in the image", "stage": "image"})
        if r.get("anatomy_problems"):
            issues.append({"code": "distortion", "severity": "error", "message": "Vision model reported anatomy/distortion problems", "stage": "image"})
        return issues

    def generate_page_image(self, page_id: str, variant_offset: int = 0) -> dict:
        with session_scope() as s:
            p = s.get(Page, page_id)
            page = {"id": p.id, "number": p.number, "text": p.text, "image_description": p.image_prompt, "scene": dict(p.scene or {}),
                    "characters_present": list(p.characters_present or []), "meta": dict(p.image_meta or {})}
        chars, refs = self._characters()
        palettes = {c["id"]: c.get("palette", []) for c in chars}
        w, h = self._img_size(self.aspect)
        prompt, neg = build_page_prompt(page, chars, self.s.illustration_style, self.s.text_in_image, self.s.aspect_ratio)
        attempts_log = list(page["meta"].get("attempts", []))
        base_variant = int(page["meta"].get("next_variant", 0)) + variant_offset
        final_q, final_path, final_res = None, None, None
        for attempt in range(self.s.max_image_attempts):
            self.checkpoint()
            variant = base_variant + attempt
            seed = self.seed + page["number"] * 101 + variant * 7
            ref_bytes = [refs[c].read_bytes() for c in page["characters_present"] if refs.get(c) and refs[c].exists()]
            try:
                res = self.router.run(self.image_id, "image", f"page_{page['number']}",
                                      lambda prov, cfg: prov.generate(prompt, neg, w, h, seed=seed, reference_images=ref_bytes or None,
                                                                      scene=page["scene"], characters=chars, style=self.s.illustration_style),
                                      overrides={"variant": variant})
            except ProviderError as e:
                attempts_log.append({"attempt": len(attempts_log) + 1, "variant": variant, "error": str(e)[:300]})
                final_q = {"ok": False, "issues": [{"code": "provider_error", "severity": "error", "message": str(e)[:300], "stage": "image"}]}
                continue
            data, cropped = fit_to_aspect(res.data, self.aspect)
            q = check_image(data, self.aspect, expected_chars=page["characters_present"], meta=res.meta if res.meta.get("character_boxes") is not None else None,
                            palettes=palettes, time_of_day=page["scene"].get("time", "day"))
            if cropped:
                q["issues"].append({"code": "center_cropped", "severity": "warning", "message": "Provider size differed; image was centre-cropped to the page aspect", "stage": "image"})
            if q["ok"]:
                vis = self._vision_review(data, page["text"], page["characters_present"])
                q["issues"] += vis
                q["ok"] = not any(i["severity"] == "error" for i in q["issues"])
            path = self.dir / "images" / f"page-{page['number']:02d}-v{variant}.png"
            path.write_bytes(data)
            attempts_log.append({"attempt": len(attempts_log) + 1, "variant": variant, "seed": seed, "file": rel(self.pid, path),
                                 "ok": q["ok"], "issues": [i["code"] for i in q["issues"]]})
            final_q, final_path, final_res = q, path, res
            if q["ok"]:
                break
            self._log("images", "warning", f"page {page['number']} attempt {attempt + 1} failed QA", {"issues": q["issues"]})
        with session_scope() as s:
            p = s.get(Page, page_id)
            meta = dict(p.image_meta or {})
            meta.update({"provider": self.image_model_name, "model": getattr(final_res, "model", ""), "prompt": prompt, "negative_prompt": neg,
                         "attempts": attempts_log[-12:], "next_variant": base_variant + self.s.max_image_attempts,
                         "renderer_meta": (final_res.meta if final_res else {}), "characters": page["characters_present"],
                         "dhash": (final_q or {}).get("metrics", {}).get("dhash")})
            p.image_meta = meta
            if final_path:
                p.image_file = rel(self.pid, final_path)
            p.image_status = "generated" if final_q and final_q["ok"] else "failed"
            p.qa = {**(p.qa or {}), "image": final_q}
        return final_q or {"ok": False}

    def generate_cover(self) -> dict:
        chars, refs = self._characters()
        W, H = page_size(self.s)
        page_aspect = W / H if self.s.cover_style == "illustrated" else self.aspect
        w, h = self._img_size(page_aspect)
        with session_scope() as s:
            pages = s.scalars(select(Page).where(Page.project_id == self.pid).order_by(Page.number)).all()
            pick = next((p for p in pages if p.beat in ("resolution", "celebration")), pages[-1] if pages else None)
            scene = dict(pick.scene) if pick else {}
        main = [c["id"] for c in chars if c.get("role") in ("protagonist", "companion")]
        scene.update({"characters": main, "shot": "medium" if page_aspect < 1 else "wide",
                      "props": [x for x in scene.get("props", []) if x not in ("crowd", "kids")],
                      "poses": {cid: ("wave" if c.get("role") == "protagonist" else "jump") for cid, c in ((c["id"], c) for c in chars) if cid in main},
                      "expressions": {cid: "happy" for cid in main}})
        if scene.get("time") == "night" and scene.get("location") == "bedroom":
            scene.update({"location": "meadow", "props": ["star_sky", "moon"]})
        with session_scope() as s:
            plan = current_plan(s, s.get(Project, self.pid)).model_dump()
        prompt, neg = build_cover_prompt(plan, chars, self.s.illustration_style)
        q = None
        for attempt in range(self.s.max_image_attempts):
            seed = self.seed + 9000 + attempt
            try:
                res = self.router.run(self.image_id, "image", "cover",
                                      lambda prov, cfg: prov.generate(prompt, neg, w, h, seed=seed, scene=scene, characters=chars,
                                                                      style=self.s.illustration_style,
                                                                      reference_images=[refs[c].read_bytes() for c in main if refs.get(c)] or None),
                                      overrides={"variant": attempt})
            except ProviderError as e:
                q = {"ok": False, "issues": [{"code": "provider_error", "severity": "error", "message": str(e)[:300], "stage": "image"}]}
                continue
            data, _ = fit_to_aspect(res.data, page_aspect)
            q = check_image(data, page_aspect, expected_chars=main, meta=res.meta if res.meta.get("character_boxes") is not None else None,
                            palettes={c["id"]: c.get("palette", []) for c in chars}, time_of_day=scene.get("time", "day"))
            path = self.dir / "images" / f"cover-v{attempt}.png"
            path.write_bytes(data)
            with session_scope() as s:
                p = s.get(Project, self.pid)
                st = dict(p.stage_state or {})
                st["cover"] = {"file": rel(self.pid, path), "ok": q["ok"], "issues": q["issues"], "aspect": page_aspect, "prompt": prompt}
                p.stage_state = st
            if q["ok"]:
                break
        return q or {"ok": False}

    def stage_images(self):
        self._set_stage("images", "running")
        with session_scope() as s:
            todo = [p.id for p in s.scalars(select(Page).where(Page.project_id == self.pid).order_by(Page.number))
                    if not p.image_locked and (p.image_status in ("pending", "rejected", "failed") or not p.image_file
                                               or not (absf(self.pid, p.image_file) and absf(self.pid, p.image_file).exists()))]
            total = s.query(Page).filter(Page.project_id == self.pid).count()
        done = total - len(todo)
        workers = 1 if not self.image_offline else max(1, app_settings.max_concurrent_images)
        workers = max(1, min(workers, app_settings.max_concurrent_images))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"img-{self.pid[:6]}") as ex:
            futs = {ex.submit(self.generate_page_image, pid): pid for pid in todo}
            try:
                for f in as_completed(futs):
                    f.result()  # propagate Paused/Cancelled
                    done += 1
                    self._progress("images", done / max(1, total + 1))
            except BaseException:
                for f in futs:  # stop queued pages immediately; finished pages stay saved
                    f.cancel()
                raise
        # near-duplicate illustrations: regenerate the later page once
        with session_scope() as s:
            hashes = {p.number: (p.image_meta or {}).get("dhash") for p in s.scalars(select(Page).where(Page.project_id == self.pid)) if (p.image_meta or {}).get("dhash")}
            by_num = {p.number: p.id for p in s.scalars(select(Page).where(Page.project_id == self.pid))}
            locked = {p.number for p in s.scalars(select(Page).where(Page.project_id == self.pid, Page.image_locked.is_(True)))}
        dups = find_duplicates(hashes)
        for a, b, dist in dups:
            target = b if b not in locked else a
            if target in locked:
                continue
            self._log("images", "warning", f"pages {a} and {b} look nearly identical (distance {dist}); regenerating page {target}")
            self.generate_page_image(by_num[target], variant_offset=1)
        st = self._state().get("cover", {})
        if not st.get("ok") or not (absf(self.pid, st.get("file", "")) and absf(self.pid, st.get("file", "")).exists()):
            self.generate_cover()
        self._set_stage("images", "done", duplicates=[list(d) for d in dups])
        with session_scope() as s:
            add_history(s, self.pid, "illustrations_generated", {"pages": len(todo), "duplicates_fixed": len(dups)})

    # ------------------------------------------------------------------ stage: pdf
    def _shorten_page(self, number: int) -> bool:
        with session_scope() as s:
            proj = s.get(Project, self.pid)
            plan = current_plan(s, proj)
            page = s.scalar(select(Page).where(Page.project_id == self.pid, Page.number == number))
            try:
                if plan.theme in offline_engine.THEMES:
                    new = offline_engine.regenerate_page(plan, self.s, number, page.text_variant, shorten=True)
                elif not self.llm_offline:
                    new = regenerate_page_llm(self.llm_call("shorten_text"), plan, self.s, number, shorten=True)
                else:
                    return False
            except Exception as e:
                self._log("pdf", "error", f"could not shorten page {number}: {e}")
                return False
            if new.text == page.text or len(new.text) >= len(page.text):
                return False
            add_history(s, self.pid, "text_shortened_for_layout", {"page": number, "before": page.text, "after": new.text}, actor="qa")
            page.text = new.text
            return True

    def stage_pdf(self):
        self._set_stage("pdf", "running")
        layouts, issues = None, []
        for round_ in range(3):
            with session_scope() as s:
                proj = s.get(Project, self.pid)
                plan = current_plan(s, proj)
                pages = s.scalars(select(Page).where(Page.project_id == self.pid).order_by(Page.number)).all()
                images = {}
                for p in pages:
                    f = absf(self.pid, p.image_file)
                    if f and f.exists():
                        images[f"story:{p.number}"] = str(f)
                cover = absf(self.pid, (proj.stage_state or {}).get("cover", {}).get("file", ""))
                if cover and cover.exists():
                    images["cover"] = str(cover)
                if pages and f"story:{pages[-1].number}" in images:
                    images["back"] = images[f"story:{pages[-1].number}"]
                page_dicts = [{"number": p.number, "text": p.text} for p in pages]
                title, version = proj.title or plan.title, proj.version + 1
            layouts, issues = build_layouts(self.s, title, page_dicts, images, plan.model_dump(), [self.llm_model_name, self.image_model_name])
            overflow = sorted({i["page"] for i in issues if i["code"] == "text_overflow" and i.get("page")})
            if not overflow or round_ == 2:
                break
            self._log("pdf", "warning", f"text overflow on pages {overflow}; shortening text (round {round_ + 1})")
            if not any([self._shorten_page(n) for n in overflow]):
                break
        out = self.dir / "pdf" / f"book-v{version}.pdf"
        info = render_pdf(layouts, images, out, title=title, author=self.s.author, subject=plan.summary, language=self.s.language,
                          keywords=["children's book", plan.theme, self.s.age_group], profile=self.s.output_profile)
        W, H = page_size(self.s)
        expected_story = len(page_dicts)
        image_pages = [i + 1 for i, l in enumerate(layouts) if any(e.kind == "image" and (e.data.get("slot") != "cover" or "cover" in images)
                                                                and (l.kind != "story" or f"story:{l.number}" in images) for e in l.elements)]
        ltr = None
        if self.s.language == "en":
            ltr = {i + 1: next(e for e in l.elements if e.kind == "text").data["lines"][0]["logical"] for i, l in enumerate(layouts) if l.kind == "story"}
        v = validate_pdf(out, expected_pages=len(layouts), expected_size=(W, H), page_kinds=[l.kind for l in layouts], image_pages=image_pages,
                         title=title, language=self.s.language, ltr_samples=ltr, render_dir=self.dir / "render" / f"v{version}")
        story_missing = [p["number"] for p in page_dicts if f"story:{p['number']}" not in images]
        for n in story_missing:
            issues.append({"code": "image_missing", "severity": "error", "message": "Story page has no illustration", "page": n, "stage": "image"})
        layout_map = [{"index": i + 1, "kind": l.kind, "number": l.number, "notes": l.notes} for i, l in enumerate(layouts)]
        with session_scope() as s:
            p = s.get(Project, self.pid)
            p.version = version
            p.pdf_file = rel(self.pid, out)
            q = dict(p.qa_report or {})
            q["layout"] = {"ok": not any(i["severity"] == "error" for i in issues), "issues": issues, "pages": layout_map}
            q["pdf"] = v
            q["pdf"]["expected_story_pages"] = expected_story
            p.qa_report = q
            add_history(s, self.pid, "pdf_built", {"version": version, "pages": info["pages"], "bytes": info["bytes"], "valid": v["ok"]})
        self._log("pdf", "ok" if v["ok"] else "error", f"PDF v{version}: {info['pages']} pages, {info['bytes'] / 1e6:.2f} MB, valid={v['ok']}",
                  {"issues": v["issues"][:20]})
        self._set_stage("pdf", "done", version=version)

    # ------------------------------------------------------------------ stage: qa
    def stage_qa(self):
        self._set_stage("qa", "running")
        with session_scope() as s:
            proj = s.get(Project, self.pid)
            plan = current_plan(s, proj)
            story = validate_plan(plan, self.s.model_copy(update={"page_count": len(plan.pages)}))
            pages = s.scalars(select(Page).where(Page.project_id == self.pid).order_by(Page.number)).all()
            image_issues = []
            for p in pages:
                q = (p.qa or {}).get("image") or {}
                for i in q.get("issues", []):
                    image_issues.append({**i, "page": p.number})
                if p.image_status == "failed" and not q.get("issues"):
                    image_issues.append({"code": "image_failed", "severity": "error", "message": "Image generation failed", "page": p.number, "stage": "image"})
            cover = (proj.stage_state or {}).get("cover", {})
            for i in cover.get("issues", []):
                image_issues.append({**i, "page": "cover"})
            rep = dict(proj.qa_report or {})
            layout = rep.get("layout", {"issues": []})
            pdfq = rep.get("pdf", {"issues": [], "ok": False})
            sections = {"story": story["issues"], "images": image_issues, "layout": layout.get("issues", []), "pdf": pdfq.get("issues", [])}
            errors = sum(1 for sec in sections.values() for i in sec if i.get("severity") == "error")
            warnings = sum(1 for sec in sections.values() for i in sec if i.get("severity") == "warning")
            pdf_ok = bool(pdfq.get("ok")) and bool(proj.pdf_file)
            if not pdf_ok:
                status = "failed"
            elif errors:
                status = "needs_review"
            elif warnings:
                status = "completed_with_warnings"
            else:
                status = "completed"
            rep.update({"story": story, "images": {"issues": image_issues}, "summary": {
                "status": status, "errors": errors, "warnings": warnings, "pdf_valid": pdf_ok, "story_pages": len(pages),
                "word_count": story.get("word_count"), "checked_at": time.time(),
                "engines": {"story": plan.engine, "images": self.image_model_name}}})
            proj.qa_report = rep
            proj.status = status
            proj.progress = 100.0
            proj.error = "" if status != "failed" else "PDF could not be validated"
            add_history(s, self.pid, "qa_completed", {"status": status, "errors": errors, "warnings": warnings}, actor="qa")
        self._set_stage("qa", "done", final_status=status)
        self._log("qa", "ok" if status.startswith("completed") else "warning", f"final status: {status} ({errors} errors, {warnings} warnings)")

    # ------------------------------------------------------------------ partial operations
    def regenerate_image(self, page_id: str, prompt_override: str = ""):
        self._update(status="running")
        with session_scope() as s:
            p = s.get(Page, page_id)
            if prompt_override:
                p.image_prompt = prompt_override
            p.image_status = "pending"
            p.image_locked = False
            add_history(s, self.pid, "image_regeneration_requested", {"page": p.number}, actor="user")
        q = self.generate_page_image(page_id)
        self.stage_pdf()
        self.stage_qa()
        return q

    def regenerate_cover(self):
        self._update(status="running")
        q = self.generate_cover()
        self.stage_pdf()
        self.stage_qa()
        return q

    def regenerate_text(self, page_id: str, instruction: str = ""):
        self._update(status="running")
        with session_scope() as s:
            proj = s.get(Project, self.pid)
            plan = current_plan(s, proj)
            page = s.get(Page, page_id)
            number, variant, old_text, old_desc = page.number, page.text_variant + 1, page.text, page.image_prompt
        if plan.theme in offline_engine.THEMES and (self.llm_offline or not instruction):
            new = offline_engine.regenerate_page(plan, self.s, number, variant)
        elif not self.llm_offline:
            new = regenerate_page_llm(self.llm_call("rewrite_page"), plan, self.s, number, instruction)
        else:
            raise ValueError("No language model is configured and this page is not from the offline engine; edit the text manually.")
        with session_scope() as s:
            page = s.get(Page, page_id)
            page.text = new.text
            page.text_variant = variant
            if new.image_description and new.image_description != old_desc:
                page.image_prompt = new.image_description
                if not page.image_locked and not plan.theme in offline_engine.THEMES:
                    page.image_status = "pending"
            add_history(s, self.pid, "text_regenerated", {"page": number, "before": old_text, "after": new.text}, actor="user")
            needs_image = page.image_status == "pending"
        if needs_image:
            self.generate_page_image(page_id)
        self.stage_pdf()
        self.stage_qa()

    def rebuild(self):
        self._update(status="running")
        # regenerate any pending/failed images first (e.g. after character edits), then pdf + qa
        self.stage_images()
        self.stage_pdf()
        self.stage_qa()
