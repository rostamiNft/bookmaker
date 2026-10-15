"""Assemble the complete page sequence for a book and verify it."""
from __future__ import annotations

from datetime import date

from ..schemas import BookSettings
from .engine import LayoutEngine, LayoutError, PageLayout, verify_layout

END_TEXT = {"en": "The End", "fa": "پایان"}


def copyright_lines(title, author, publisher, lang, plan: dict, models: list[str]) -> list[str]:
    year = date.today().year
    if lang == "fa":
        lines = [title, f"نویسنده: {author}" if author else "", f"ناشر: {publisher}" if publisher else "",
                 f"تاریخ تولید: {year}",
                 "متن و تصاویر این کتاب با کمک ابزار متن‌باز Bookmaker تولید شده‌اند.",
                 "ابزارها/مدل‌ها: " + "، ".join(models),
                 "قلم‌ها: Vazirmatn و Andika (مجوز SIL Open Font License 1.1)."]
    else:
        lines = [title, f"Written by {author}" if author else "", f"Published by {publisher}" if publisher else "",
                 f"Created {year}",
                 "Text and illustrations were produced with the open-source Bookmaker toolkit.",
                 "Engines/models: " + ", ".join(models),
                 "Fonts: Andika and Vazirmatn (SIL Open Font License 1.1).",
                 "Legal status of machine-generated content varies by jurisdiction; no ownership claim is implied."]
    return [ln for ln in lines if ln]


def build_layouts(s: BookSettings, title: str, pages: list[dict], images: dict[str, str], plan: dict, models: list[str]) -> tuple[list[PageLayout], list[dict]]:
    """pages: [{number, text}] ; images: keys 'cover', 'back', 'story:N'. Returns (layouts, issues)."""
    eng = LayoutEngine(s)
    issues: list[dict] = []
    layouts: list[PageLayout] = []

    def guard(fn, *a, **kw):
        try:
            pl = fn(*a, **kw)
            layouts.append(pl)
            issues.extend(verify_layout(pl, eng.m))
        except LayoutError as e:
            issues.append({"code": e.code, "severity": "error", "message": str(e), "page": e.page, "stage": "layout"})

    guard(eng.cover, title, s.author, s.cover_style, "cover" in images)
    guard(eng.title_page, title, s.author, s.publisher)
    guard(eng.copyright_page, copyright_lines(title, s.author, s.publisher, s.language, plan, models))
    for p in pages:
        guard(eng.story_page, p["number"], p["text"], f"story:{p['number']}" in images, images.get(f"story:{p['number']}"))
    guard(eng.end_page, END_TEXT.get(s.language, "The End"), False)
    if s.back_cover:
        blurb = plan.get("back_cover_blurb") or plan.get("summary", "")
        guard(eng.back_cover, blurb, s.publisher, "back" in images)
    return layouts, issues
