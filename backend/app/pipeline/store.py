"""Project storage helpers: per-project directories, current plan assembly, history."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Character, HistoryEntry, Page, Project, StageLog
from ..schemas import CharacterSpec, PageSpec, Scene, StoryPlan
from ..story.themes import THEMES

ROLE_SET = {"setup", "inciting", "rising", "climax", "resolution", "ending"}


def project_dir(pid: str) -> Path:
    d = settings.projects_dir / pid
    for sub in ("images", "refs", "pdf", "render", "uploads"):
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
