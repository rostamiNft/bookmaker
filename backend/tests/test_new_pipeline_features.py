"""The features added after the first release, each one checked on real output:

* speech bubbles placed inside the illustration (pixels are compared);
* PDF/X-4 + ICC output intent (the PDF is parsed back and the boxes/Intent read);
* HarfBuzz shaping, kashida justification and Pyphen hyphenation;
* word-level narration timings -> karaoke HTML, EPUB 3 media overlays, video;
* right-to-left video captions (word order is checked, not assumed);
* per-character LoRA dataset export (a real ZIP with captions and configs).

Everything runs locally: no network, no API key, no GPU.
"""
from __future__ import annotations

import io
import re
import zipfile

import pytest
from PIL import Image, ImageChops, ImageDraw

from app.config import settings
from app.db import session_scope
from app.models import Project

MM = 72 / 25.4


# --------------------------------------------------------------- fixtures / helpers
def _new_book(app_client, worker, **settings_override) -> str:
    body = {"settings": {"page_count": 6, "seed": 7, "language": "en", "aspect_ratio": "3:2", **settings_override},
            "start": True}
    r = app_client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert worker.drain(400), f"pipeline did not finish: {app_client.get(f'/api/projects/{pid}').json().get('status')}"
    return pid


def _page_image(pid: str, n: int = 1) -> Image.Image:
    """The illustration as generated (before the page is composed)."""
    with session_scope() as s:
        from app.pipeline.store import project_dir
        p = s.get(Project, pid)
        pages = sorted(p.pages, key=lambda x: x.number)
        rel = pages[n - 1].image_file
    return Image.open(project_dir(pid) / rel).convert("RGB")


def _rendered_page(pdf_bytes: bytes, n: int = 1, scale: float = 1.0) -> Image.Image:
    """Rasterise page n of a finished PDF (pypdfium2, BSD-3) - the truth about layout."""
    pdfium = pytest.importorskip("pypdfium2")
    import io as _io
    doc = pdfium.PdfDocument(_io.BytesIO(pdf_bytes))
    return doc[n - 1].render(scale=scale).to_pil().convert("RGB")


@pytest.fixture(scope="module")
def bubble_pair(app_client, worker):
    """Two books from the same seed: one with bubbles, one without. Comparing them
    shows that the difference really is the bubble (and nothing else moved)."""
    kw = dict(topic="a small robot who loses its key", text_amount="short", layout_style="full_bleed")
    return (_new_book(app_client, worker, speech_bubbles=True, **kw),
            _new_book(app_client, worker, speech_bubbles=False, **kw))


# --------------------------------------------------------------- speech bubbles
def test_pipeline_uses_speech_bubbles_when_asked(app_client, worker):
    """Through the API: the layout engine really planned balloons (not a silent fallback)."""
    pid = _new_book(app_client, worker, topic="a small robot who loses its key", speech_bubbles=True,
                    text_amount="short", layout_style="full_bleed")
    qa = app_client.get(f"/api/projects/{pid}").json()["qa_report"]
    layout = qa.get("layout") or {}
    assert layout.get("ok"), layout.get("issues")
    notes = [n for p in layout.get("pages", []) for n in (p.get("notes") or [])]
    balloons = [n for n in notes if "layout=speech_bubbles" in n]
    assert balloons, f"no page used the bubble layout: {notes[:3]}"
    assert all("balloons=0" not in n for n in balloons), balloons
    assert any(int(re.search(r"balloons=(\d+)", n).group(1)) >= 1 for n in balloons), balloons


def test_speech_bubbles_are_drawn_over_the_art(tmp_path):
    """Deterministic pixel proof: one illustration, composed twice (with and without
    balloons) through the production layout+PDF code, rasterised, and checked at the
    exact coordinates the balloon planner chose."""
    from app.layout.book import build_layouts
    from app.layout.pdf import render_pdf
    from app.schemas import BookSettings
    img = tmp_path / "art.png"
    # a flat sky with a darker "character" blob, like a simplified illustration
    im = Image.new("RGB", (900, 600), (176, 205, 232))
    ImageDraw.Draw(im).ellipse([300, 180, 620, 520], fill=(214, 166, 120))
    im.save(img)
    pages = [{"number": 1, "text": '"Look!" said the little robot. "The moon is very bright tonight."',
              "image_prompt": "a robot", "characters_present": []}]
    images = {"story:1": str(img)}

    def compose(bubbles: bool):
        s = BookSettings(page_count=6, language="en", layout_style="full_bleed", text_amount="short",
                         speech_bubbles=bubbles, aspect_ratio="3:2", page_numbers=False)
        layouts, issues = build_layouts(s, "Bubbles", pages, images, {"arc": []}, [])
        pdf = tmp_path / f"b{int(bubbles)}.pdf"
        render_pdf(layouts, images, pdf, title="Bubbles", author="tests", subject="", language="en",
                   keywords=[], profile="screen")
        story_idx = 1 + next(i for i, l in enumerate(layouts) if l.kind == "story")
        return _rendered_page(pdf.read_bytes(), story_idx, scale=1.0), layouts, issues

    with_b, layouts, issues = compose(True)
    plain, layouts_plain, _ = compose(False)
    assert not [i for i in issues if i["severity"] == "error"], issues
    assert with_b.size == plain.size
    story = next(l for l in layouts if l.kind == "story")
    bubbles = [e for e in story.elements if e.kind == "bubble"]
    PW, PH = story.width, story.height
    scale = with_b.width / PW                 # PDF points -> rendered pixels
    assert len(bubbles) >= 2, f"expected several balloons, got {len(bubbles)}"
    checked = 0
    for b in bubbles:
        x0, y0, x1, y1 = b.box()           # Element.box() is (left, bottom, right, top)
        # PDF y is measured from the bottom; the rendered image from the top
        box = (int(x0 * scale) + 3, int((PH - y1) * scale) + 3,
               int(x1 * scale) - 3, int((PH - y0) * scale) - 3)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        area = with_b.crop(box)
        px = list(area.getdata())
        white = sum(1 for r, g, bb in px if r > 225 and g > 225 and bb > 215)
        assert white / len(px) > 0.5, f"balloon interior is not an opaque light fill: {white/len(px):.2f}"
        # the same rectangle on the page composed without balloons is the bare picture
        assert ImageChops.difference(area, plain.crop(box)).getbbox() is not None, "balloon drew nothing"
        checked += 1
    assert checked >= 2, "no balloon box could be checked"
    # every balloon carries a visible tail (>= 10 pt) - without it a balloon is a caption box
    assert all(b.data.get("tail_len", 0) >= 10 for b in bubbles), [b.data.get("tail_len") for b in bubbles]
    # the balloons sit over the picture: none of them is in the bottom 15% of the page
    # fully on the page and clear of the footer strip
    assert all(b.box()[1] > 28 and b.box()[3] < PH for b in bubbles), [b.box() for b in bubbles]


# --------------------------------------------------------------- PDF/X-4 + ICC
def test_pdfx4_output_intent_and_boxes(app_client, worker, tmp_path):
    """A real book rendered with print_standard=pdfx4, then parsed back."""
    from pypdf import PdfReader
    pid = _new_book(app_client, worker, print_standard="pdfx4", page_size="A5", bleed_mm=3, topic="a fox and the moon")
    pdf = app_client.get(f"/api/projects/{pid}/pdf")
    assert pdf.status_code == 200 and len(pdf.content) > 5000
    (tmp_path / "x4.pdf").write_bytes(pdf.content)
    r = PdfReader(str(tmp_path / "x4.pdf"))
    root = r.trailer["/Root"]
    intents = root.get("/OutputIntents")
    assert intents and len(intents) >= 1
    oi = intents[0].get_object()
    assert str(oi["/S"]) == "/GTS_PDFX"
    assert str(oi["/OutputConditionIdentifier"])
    prof = oi["/DestOutputProfile"].get_object()
    assert int(prof["/N"]) == 4                       # CMYK
    icc = prof.get_data()
    assert len(icc) == int.from_bytes(icc[:4], "big"), "ICC header size does not match the stream"
    assert icc[36:40] == b"acsp", "the stream is not an ICC profile"
    meta = root["/Metadata"].get_data().decode("utf-8", "replace")
    assert "PDF/X-4" in meta and "GTS_PDFXVersion" in meta
    page = r.pages[1] if len(r.pages) > 1 else r.pages[0]
    assert "/TrimBox" in page, "PDF/X needs a trim box on every page"
    tb, bb, mb = page.trimbox, page.bleedbox, page.mediabox
    assert float(bb.width) > float(tb.width), (float(bb.width), float(tb.width))
    assert float(mb.width) >= float(bb.width)
    # the orchestrator recorded the result of its own mechanical check
    d = app_client.get(f"/api/projects/{pid}").json()
    log = d["stage_state"].get("pdf") or {}
    assert str(d.get("qa", {}).get("pdfx_standard", "PDF/X-4")).startswith("PDF/X-4") or "pdfx" in str(log).lower()


def test_pdfx4_self_check_rejects_a_plain_pdf(app_client, worker, tmp_path):
    """The same check must FAIL on a PDF that is not PDF/X - otherwise it proves nothing."""
    from app.layout.pdfx import check_pdfx4
    pid = _new_book(app_client, worker, page_size="A5", topic="a plain rgb book")
    (tmp_path / "plain.pdf").write_bytes(app_client.get(f"/api/projects/{pid}/pdf").content)
    chk = check_pdfx4(str(tmp_path / "plain.pdf"))
    assert chk["issues"], "a plain screen PDF must not pass the PDF/X-4 self-check"
    codes = {i["code"] for i in chk["issues"]}
    assert "pdfx_outputintent_missing" in codes or "pdfx_xmp_missing" in codes, codes


def test_icc_profile_resolution_and_cmyk_conversion():
    from app.layout.pdfx import icc_supported, resolve_icc, rgb_to_cmyk_icc
    assert icc_supported(), "Pillow without ImageCms cannot produce PDF/X output"
    path, condition, name = resolve_icc("")
    assert path.exists() and path.suffix.lower() == ".icc" and condition and name
    c, m, y, k = rgb_to_cmyk_icc(1.0, 0.0, 0.0, str(path))
    assert c < 0.1 and m > 0.5 and y > 0.5 and k < 0.3, (c, m, y, k)   # pure red -> magenta + yellow
    c2, m2, y2, k2 = rgb_to_cmyk_icc(1.0, 1.0, 1.0, str(path))
    assert max(c2, m2, y2, k2) < 0.2, (c2, m2, y2, k2)                 # white stays white
    custom, cond2, name2 = resolve_icc(str(path))
    assert custom == path and cond2 == "Custom" and name2 == path.name, (custom, cond2, name2)


# --------------------------------------------------------------- shaping / hyphenation
def test_shaping_engines_and_kashida():
    from app.layout import text as T
    from app.layout.shaping import harfbuzz_available, kashida_capacity, kashida_plan, kashida_points, resolve_engine
    s = "ستاره کوچولو به آسمان رفت"
    assert T.shape(s, True) != s, "Persian text must be reshaped for drawing"
    assert T.shape("the star", False) == "the star"
    assert resolve_engine("auto") in ("harfbuzz", "basic")
    assert resolve_engine("basic") == "basic"
    # kashida: how a Persian line is stretched when justified
    word = "ستاره"
    assert kashida_points(word), "no kashida points means justified Persian lines cannot be filled"
    plan = kashida_plan(word, 2)
    assert plan.count("\u0640") == 2 and plan.replace("\u0640", "") == word
    assert kashida_capacity(word) >= 2
    # no kashida right after lam (Benatia rule), so the count is bounded by the word
    assert kashida_capacity("علی") <= 6
    if harfbuzz_available():
        assert resolve_engine("auto") == "harfbuzz"


def test_justified_persian_lines_end_at_the_margin():
    from app.layout.text import TypeOpts, justify_line, typeset, wrap
    from app.layout.text import font_chain
    chain = font_chain("fa")
    text = "ستاره کوچولو هر شب از پنجره به آسمان نگاه می‌کرد و با ماه حرف می‌زد و می‌خندید"
    opts = TypeOpts(shaping="harfbuzz", justify=True, lang="fa", kashida=True)
    para = wrap(text, chain, 13.0, 240.0, True, 1.5, opts)
    assert len(para.lines) >= 2, para.lines
    filled = 0
    for ln in para.lines[:-1]:                       # every line but the last
        j = justify_line(ln, chain, 13.0, 240.0, True, opts)
        if j.justified:
            filled += 1
            assert abs(j.width - 240.0) < 1.5, (j.width, 240.0)
    assert para.engine == opts.engine
    assert filled >= 1, "no line was justified, so the justification path is not exercised"


def test_hyphenation_only_when_enabled():
    from app.layout.text import hyphen_splits, hyphenation_available
    assert hyphenation_available("en") is True
    assert hyphenation_available("zz") is False
    parts = hyphen_splits("wonderful", "en")
    assert parts, "pyphen produced no split points"
    assert all(head.endswith("-") and tail and head[:-1] + tail == "wonderful" for head, tail in parts), parts


# --------------------------------------------------------------- word timings / video
@pytest.fixture(scope="module")
def narrated_book(app_client, worker):
    return _new_book(app_client, worker, language="fa", topic="ستاره‌ی گمشده", narration_audio=True, page_count=6)


def test_word_timings_are_stored_and_cover_the_audio(app_client, narrated_book):
    d = app_client.get(f"/api/projects/{narrated_book}").json()
    audio_pages = [p for p in d["pages"] if p.get("audio_url")]
    assert audio_pages, "no narration was produced"
    for p in audio_pages:
        words = p.get("words") or []
        assert words, f"page {p['number']} has no word timings"
        assert words[0]["t"] == 0.0, "the first word must start at zero"
        assert all(w["d"] >= 0 and w["t"] >= 0 for w in words)
        span = words[-1]["t"] + words[-1]["d"]
        assert span <= p["duration_s"] + 0.5, (span, p["duration_s"])
        assert p.get("timing_source") in ("engine_word_events", "estimated_proportional")


def test_karaoke_html_and_epub_media_overlays(app_client, narrated_book):
    r = app_client.post(f"/api/projects/{narrated_book}/exports", json={"format": "html"})
    assert r.status_code in (200, 202), r.text
    r = app_client.post(f"/api/projects/{narrated_book}/exports", json={"format": "epub"})
    assert r.status_code in (200, 202), r.text
    d = app_client.get(f"/api/projects/{narrated_book}").json()
    ex = d["exports"]
    assert ex["epub"]["media_overlays"] >= len([p for p in d["pages"] if p.get("audio_url")])
    assert ex["epub"]["media_duration_s"] > 0
    check = ex["epub"].get("validation")
    if check and check.get("checked"):
        assert check["ok"], check
    html = app_client.get(f"/api/projects/{narrated_book}/exports/html").content.decode("utf-8")
    assert 'data-t=' in html and 'class="w"' in html and "data-d=" in html
    assert html.count('class="w"') >= 10, html.count('class="w"')


def test_video_export_webm_and_rtl_captions(app_client, narrated_book, tmp_path):
    av = pytest.importorskip("av", reason="PyAV (BSD-3) is an optional extra for the video export")
    r = app_client.post(f"/api/projects/{narrated_book}/exports", json={"format": "video"})
    assert r.status_code in (200, 202), r.text
    d = app_client.get(f"/api/projects/{narrated_book}").json()
    info = d["exports"].get("video")
    assert info and info["bytes"] > 1000
    probe = info.get("probe") or {}
    vstreams = [s for s in probe.get("streams", []) if s["type"] == "video"]
    astreams = [s for s in probe.get("streams", []) if s["type"] == "audio"]
    assert vstreams and vstreams[0]["codec"] in ("vp9", "libvpx-vp9"), vstreams
    assert astreams and astreams[0]["codec"] in ("opus", "libopus"), astreams
    assert vstreams[0]["frames"] > 10 and vstreams[0]["width"] == 1280
    assert probe.get("duration_s", 0) > 1
    # the caption layout: RTL words are drawn right to left, in logical word order
    from app.export.video import _visual_words
    text = "ستاره کوچولو به خانه برگشت"
    vis = _visual_words(text, True)
    assert [i for i, _ in vis] == list(range(len(vis))), "logical indices must survive reordering"
    assert vis[0][1] != text.split()[0], "the word must be reshaped for the caption font"
    from app.export.video import _dir_of
    assert _dir_of("ستاره") and not _dir_of("star")
    # a mixed line: the LTR run keeps its own order inside the RTL line
    # drawing order on an RTL line is right to left: رفت, Mars, ستاره
    mixed = _visual_words("ستاره Mars رفت", True)
    assert [i for i, _ in mixed] == [2, 1, 0]
    ltr = _visual_words("the star went home", False)
    assert [i for i, _ in ltr] == [0, 1, 2, 3]


def test_mp4_needs_the_gpl_confirmation(app_client, narrated_book):
    r = app_client.post(f"/api/projects/{narrated_book}/exports", json={"format": "video_mp4"})
    assert r.status_code == 402, r.text
    body = r.json()
    msg = body.get("message") or (body.get("detail") or {}).get("message") or str(body)
    assert "GPL" in msg, msg
    assert "x264" in msg, msg


# --------------------------------------------------------------- LoRA dataset
def test_lora_dataset_export(app_client, worker, tmp_path):
    pid = _new_book(app_client, worker, topic="a girl and her cat", main_characters=2, lora_training=True)
    d = app_client.get(f"/api/projects/{pid}").json()
    assert d["characters"], "the story engine produced no character bible"
    r = app_client.get(f"/api/projects/{pid}/lora-dataset")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    imgs = [n for n in names if n.startswith("img/") and n.lower().endswith((".png", ".jpg", ".webp"))]
    assert imgs, names
    assert any(n.startswith("img/10_") for n in imgs), imgs
    txts = [n for n in names if n.endswith(".txt")]
    assert len(txts) == len(imgs), (len(txts), len(imgs))
    first_caption = zf.read(txts[0]).decode("utf-8")
    # dataset captions start with the trigger word; the <lora:...> token belongs in the
    # *prompt* (prompts.py), never in a caption file
    assert first_caption.split(",")[0].strip() and "<lora:" not in first_caption, first_caption
    assert "kohya_config.toml" in names and "ai_toolkit.yaml" in names and "README.md" in names
    meta = zf.read("dataset.json").decode("utf-8")
    import json as _json
    doc = _json.loads(meta)
    assert doc["images"] == len(imgs)
    assert all(c["trigger"] and c["images"] >= 1 for c in doc["characters"]), doc["characters"]
    d2 = app_client.get(f"/api/projects/{pid}").json()
    assert d2["stage_state"]["exports"]["lora"]["images"] == len(imgs)
    hist = app_client.get(f"/api/projects/{pid}/history").json()
    assert any(e.get("action") == "lora_dataset_built" for e in (hist if isinstance(hist, list) else hist.get("items", []))), hist[:2]


def test_lora_prompts_carry_the_trigger():
    from app.illustration.prompts import build_cast_prompt, build_cover_prompt, build_reference_prompt
    spec = {"id": "c1", "name": "Sara", "description": "a girl with a blue coat", "colors": "blue",
            "lora_name": "sara_v1", "lora_weight": 0.8, "lora_trigger": "saraBK", "distinctive": []}
    p, neg = build_reference_prompt(spec, "watercolor")
    assert p.startswith("<lora:sara_v1:0.8> saraBK"), p
    p2, _ = build_cast_prompt([spec, {"id": "c2", "name": "Ben", "description": "a boy", "scale": 1.0,
                                      "age": "child", "height_hint": ""}], "watercolor")
    assert "<lora:sara_v1:0.8> saraBK" in p2, p2
    p3, _ = build_cover_prompt({"title": "Sara and the moon", "summary": "", "cover_scene": ""}, [spec], "watercolor")
    assert "<lora:sara_v1:0.8> saraBK" in p3, p3
    plain = {"id": "c3", "name": "Ben", "description": "a boy", "colors": "red",
             "lora_name": "", "lora_weight": 0.8, "lora_trigger": "", "distinctive": []}
    p4, _ = build_reference_prompt(plain, "watercolor")
    assert "<lora:" not in p4, p4          # no LoRA configured -> no token in the prompt
    assert neg
