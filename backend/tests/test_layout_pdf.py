import io
import itertools

import pytest
from PIL import Image
from pypdf import PdfReader

from app.layout.book import build_layouts
from app.layout.engine import LayoutEngine, LayoutError, PageLayout, Element, page_size, verify_layout
from app.layout.pdf import render_pdf
from app.layout.pdf_qa import validate_pdf
from app.layout.text import font_chain, shape, wrap
from app.schemas import BookSettings
from app.story.offline_engine import generate_plan


@pytest.mark.parametrize("lang,size,ori,age,amount", list(itertools.product(["en", "fa"], ["A4", "A5", "Letter", "Square8"], ["portrait", "landscape"],
                                                                            ["3-5", "9-12"], ["short", "long"])))
def test_story_layouts_fit_and_verify(lang, size, ori, age, amount):
    s = BookSettings(language=lang, page_size=size, orientation=ori, age_group=age, text_amount=amount, page_count=12, seed=2, humor_level=3)
    plan = generate_plan(s)
    eng = LayoutEngine(s)
    for p in plan.pages:
        pl = eng.story_page(p.number, p.text)
        assert verify_layout(pl, eng.m) == []
        img = next(e for e in pl.elements if e.kind == "image")
        assert abs(img.w / img.h - 4 / 3) < 1e-6  # exact aspect: never stretched


def test_overflow_is_detected_not_hidden():
    s = BookSettings(page_size="A5", age_group="3-5")
    with pytest.raises(LayoutError) as e:
        LayoutEngine(s).story_page(1, "word " * 900)
    assert e.value.code == "text_overflow"


def test_verify_layout_detects_overlap_and_out_of_page():
    s = BookSettings()
    eng = LayoutEngine(s)
    pl = eng.story_page(1, "A short line of text for the page.")
    img = next(e for e in pl.elements if e.kind == "image")
    txt = next(e for e in pl.elements if e.kind == "text")
    txt.y = img.y + 5
    pl.elements.append(Element("image", -20, -20, 50, 50, {}))
    codes = {i["code"] for i in verify_layout(pl, eng.m)}
    assert {"overlap", "out_of_page"} <= codes


def test_persian_wrapping_keeps_logical_line_order():
    chain = font_chain("fa")
    text = "یک روز صبح سارا با دوستش به باغ رفت و آن‌ها یک پروانه‌ی رنگی دیدند که روی گل نشسته بود"
    para = wrap(text, chain, 14, 160, True, 1.7)
    assert len(para.lines) > 1
    assert para.lines[0].logical.startswith("یک روز")  # first line = beginning of the sentence
    assert " ".join(l.logical for l in para.lines) == text
    vis = shape("سلام", True)
    assert any(0xFE70 <= ord(c) <= 0xFEFF for c in vis)  # contextual presentation forms


def test_mixed_persian_english_bidi():
    v = shape("ابزارها: Built-in engine (offline, no AI)، Illustrator (offline, CPU)", True)
    assert v.startswith("Built-in engine (offline, no AI)") and "Illustrator (offline, CPU)" in v
    v = shape("قلم‌ها: Vazirmatn و Andika (مجوز SIL Open Font License 1.1).", True)
    assert v.startswith(".(SIL Open Font License 1.1 ")  # brackets enclose the phrase after mirroring
    assert "\u202a" not in v and "\u202c" not in v
    assert shape("تاریخ: 2026", True).startswith("2026")


def _build(lang, tmp_path, with_images=True):
    s = BookSettings(language=lang, page_count=8, seed=5, author="A")
    plan = generate_plan(s)
    images = {}
    if with_images:
        for p in plan.pages:
            f = tmp_path / f"p{p.number}.png"
            Image.effect_noise((800, 600), 64).convert("RGB").save(f)
            images[f"story:{p.number}"] = str(f)
        W, H = page_size(s)
        cf = tmp_path / "cover.png"
        Image.effect_noise((600, int(600 * H / W)), 64).convert("RGB").save(cf)
        images["cover"] = str(cf)
    layouts, issues = build_layouts(s, plan.title, [{"number": p.number, "text": p.text} for p in plan.pages], images, plan.model_dump(), ["test"])
    out = tmp_path / "book.pdf"
    render_pdf(layouts, images, out, title=plan.title, author="A", subject=plan.summary, language=lang, keywords=["k"])
    return s, plan, layouts, issues, out


@pytest.mark.parametrize("lang", ["en", "fa"])
def test_pdf_is_valid_complete_and_renderable(lang, tmp_path):
    s, plan, layouts, issues, out = _build(lang, tmp_path)
    assert issues == []
    W, H = page_size(s)
    image_pages = [i + 1 for i, l in enumerate(layouts) if any(e.kind == "image" for e in l.elements)]
    ltr = {i + 1: next(e for e in l.elements if e.kind == "text").data["lines"][0]["logical"] for i, l in enumerate(layouts) if l.kind == "story"} if lang == "en" else None
    v = validate_pdf(out, expected_pages=len(layouts), expected_size=(W, H), page_kinds=[l.kind for l in layouts], image_pages=image_pages,
                     title=plan.title, language=lang, ltr_samples=ltr, render_dir=tmp_path / "render")
    assert v["ok"], v["issues"]
    assert v["info"]["pages"] == 3 + 8 + 1 + 1  # cover, title, copyright, 8 story, end, back
    assert v["info"]["rendered"] == v["info"]["pages"]
    assert all(f["embedded"] for f in v["info"]["fonts"])
    assert v["info"]["lang"] == lang
    r = PdfReader(str(out))
    assert r.metadata["/Title"] == plan.title and r.metadata["/Author"] == "A"
    assert len(list((tmp_path / "render").glob("page-*.jpg"))) == v["info"]["pages"]


def test_pdf_validator_detects_wrong_page_count_and_missing_images(tmp_path):
    s, plan, layouts, issues, out = _build("en", tmp_path, with_images=False)
    W, H = page_size(s)
    v = validate_pdf(out, expected_pages=len(layouts) + 1, expected_size=(W, H), page_kinds=[], image_pages=[4], title=plan.title, language="en")
    codes = {i["code"] for i in v["issues"]}
    assert {"page_count", "image_missing"} <= codes and not v["ok"]


def test_pdf_validator_rejects_garbage(tmp_path):
    f = tmp_path / "x.pdf"
    f.write_bytes(b"hello")
    v = validate_pdf(f, expected_pages=1, expected_size=(1, 1), page_kinds=[], image_pages=[], title="", language="en")
    assert not v["ok"]
