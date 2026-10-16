import io

from PIL import Image, ImageDraw, ImageFont

from app.illustration.image_qa import check_image, detect_text, find_duplicates, fit_to_aspect
from app.illustration.scene import render_reference_sheet, render_scene
from app.providers.offline import ProceduralImage
from app.schemas import BookSettings
from app.story.offline_engine import generate_plan


def _plan():
    return generate_plan(BookSettings(topic="star", page_count=14, seed=7))


def png_bytes(img):
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


def test_every_page_renders_with_expected_characters_and_passes_qa():
    plan = _plan()
    chars = [c.model_dump() for c in plan.characters]
    pal = {c["id"]: c["palette"] for c in chars}
    prov = ProceduralImage()
    hashes = {}
    for p in plan.pages:
        r = prov.generate("", "", 800, 600, seed=p.number, scene=p.scene.model_dump(), characters=chars)
        assert set(r.meta["character_boxes"]) == set(p.characters_present)
        assert not r.meta["props_skipped"], r.meta["props_skipped"]
        q = check_image(r.data, 4 / 3, expected_chars=p.characters_present, meta=r.meta, palettes=pal, time_of_day=p.scene.time, ocr=False)
        assert q["ok"], (p.number, q["issues"])
        hashes[p.number] = q["metrics"]["dhash"]
    assert find_duplicates(hashes) == []


def test_character_consistency_detects_wrong_colours():
    plan = _plan()
    chars = {c.id: c.model_dump() for c in plan.characters}
    scene = plan.pages[1].scene.model_dump()
    img, meta = render_scene(scene, chars, 800, 600, "flat_vector", seed=1)
    wrong = {cid: ["#00ff00", "#ff00ff", "#00ffff"] for cid in meta["character_boxes"]}
    q = check_image(png_bytes(img), 4 / 3, expected_chars=list(meta["character_boxes"]), meta=meta, palettes=wrong, ocr=False)
    assert any(i["code"] == "character_inconsistent" for i in q["issues"])


def test_missing_and_extra_characters_detected():
    plan = _plan()
    chars = {c.id: c.model_dump() for c in plan.characters}
    img, meta = render_scene(plan.pages[1].scene.model_dump(), chars, 800, 600, seed=1)
    q = check_image(png_bytes(img), 4 / 3, expected_chars=["hero", "comp", "helper"], meta=meta, ocr=False)
    assert any(i["code"] == "character_missing" for i in q["issues"])
    q = check_image(png_bytes(img), 4 / 3, expected_chars=["hero"], meta=meta, ocr=False)
    assert any(i["code"] == "unexpected_character" for i in q["issues"])


def test_qa_rejects_empty_corrupt_blank_small_and_wrong_aspect():
    assert check_image(b"", 1.0)["issues"][0]["code"] == "missing"
    assert check_image(b"not an image", 1.0)["issues"][0]["code"] == "corrupt"
    blank = png_bytes(Image.new("RGB", (800, 600), (240, 240, 240)))
    codes = {i["code"] for i in check_image(blank, 4 / 3, ocr=False)["issues"]}
    assert "blank" in codes
    small = png_bytes(Image.effect_noise((300, 200), 60).convert("RGB"))
    codes = {i["code"] for i in check_image(small, 4 / 3, ocr=False)["issues"]}
    assert {"low_resolution", "aspect_mismatch"} <= codes


def test_ocr_detects_stray_text_when_available():
    img = Image.new("RGB", (900, 600), (200, 230, 250))
    ImageDraw.Draw(img).text((80, 200), "WRONG WORDS", fill=(0, 0, 0), font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 70))
    found = detect_text(img)
    if found is None:
        return  # OCR optional dependency not installed
    assert any("WORDS" in t.upper() for t in found)
    q = check_image(png_bytes(img), 1.5)
    assert any(i["code"] == "text_in_image" for i in q["issues"])


def test_fit_to_aspect_crops_without_stretching():
    data = png_bytes(Image.effect_noise((1536, 1024), 50).convert("RGB"))
    out, changed = fit_to_aspect(data, 4 / 3)
    im = Image.open(io.BytesIO(out))
    assert changed and abs(im.width / im.height - 4 / 3) < 0.01 and im.height == 1024


def test_reference_sheet():
    plan = _plan()
    img, meta = render_reference_sheet(plan.characters[0].model_dump())
    assert img.size == (1024, 768) and len(meta["reference_boxes"]) == 3
