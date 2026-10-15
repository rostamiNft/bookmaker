"""Outline review gate, revisions/undo, region repair (inpainting), narration,
print output (bleed / crop marks / CMYK / full-bleed layout / Persian ActualText),
EPUB/HTML/ZIP exports and user accounts - all through the HTTP API with the real worker."""
import io
import zipfile

import pytest
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

from app.config import settings
from app.db import session_scope
from app.models import Project

MM = 72 / 25.4


def detail(c, pid):
    return c.get(f"/api/projects/{pid}").json()


@pytest.fixture(scope="module")
def print_book(app_client, worker):
    """Persian book: outline review on, narration on, full-bleed layout, 3 mm bleed, crop marks, CMYK."""
    body = {"settings": {"page_count": 6, "seed": 5, "language": "fa", "topic": "ماه کوچولو", "review_outline": True,
                         "narration_audio": True, "layout_style": "full_bleed", "bleed_mm": 3, "crop_marks": True,
                         "color_mode": "cmyk", "aspect_ratio": "3:4", "page_size": "A5"}, "start": True}
    r = app_client.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert worker.drain(300)
    return pid


def test_outline_review_gate_then_approve(app_client, worker, print_book):
    # NOTE: runs first in this module; the fixture stopped at the review gate
    d = detail(app_client, print_book)
    assert d["status"] == "awaiting_review", d["status"]
    assert len(d["pages"]) == 6 and all(p["text"] for p in d["pages"])
    assert not any(p["image_url"] for p in d["pages"]), "no illustration may be made before approval"
    # the user edits the outline text before approving
    pg = d["pages"][0]
    r = app_client.patch(f"/api/projects/{print_book}/pages/{pg['id']}", json={"text": pg["text"] + " ستاره‌ها خندیدند."})
    assert r.status_code == 200, r.text
    r = app_client.post(f"/api/projects/{print_book}/approve-outline")
    assert r.status_code == 200, r.text
    assert worker.drain(400)
    d = detail(app_client, print_book)
    assert d["status"] in ("completed", "completed_with_warnings"), d["qa_report"].get("summary")
    assert d["pages"][0]["text"].endswith("ستاره‌ها خندیدند.")
    assert all(p["image_url"] for p in d["pages"])
    assert d["cast_url"], "cast line-up sheet must exist"


def test_print_pdf_bleed_cropmarks_cmyk_fullbleed(app_client, print_book):
    d = detail(app_client, print_book)
    q = d["qa_report"]
    assert q["summary"]["errors"] == 0 and q["summary"]["pdf_valid"], q["summary"]
    r = app_client.get(f"/api/projects/{print_book}/pdf")
    reader = PdfReader(io.BytesIO(r.content))
    W, H = 419.53, 595.28  # A5 portrait
    o = 3 * MM + 18
    p0 = reader.pages[3]  # first story page
    assert abs(float(p0.mediabox.width) - (W + 2 * o)) < 0.6 and abs(float(p0.mediabox.height) - (H + 2 * o)) < 0.6
    tb = p0.trimbox
    assert abs(float(tb.left) - o) < 0.5 and abs(float(tb.width) - W) < 0.6
    assert abs(float(p0.bleedbox.width) - (W + 6 * MM)) < 0.6
    cs = {str(x.get_object().get("/ColorSpace")) for _, x in p0["/Resources"]["/XObject"].items()}
    assert cs == {"/DeviceCMYK"}
    notes = " ".join(n for pg in q["layout"]["pages"] if pg["kind"] == "story" for n in pg["notes"])
    assert "layout=full_bleed" in notes
    assert q["pdf"]["info"]["print"]["color_mode"] == "cmyk"
    # Persian text extracts in logical order thanks to ActualText
    assert q["pdf"]["info"].get("rtl_extract_checked", 0) >= 6
    assert not [i for i in q["pdf"]["issues"] if i["code"] == "text_not_extractable"]
    assert q["pdf"]["info"]["actualtext_spans"] > 6
    import unicodedata
    text = unicodedata.normalize("NFKC", p0.extract_text())
    first_word = d["pages"][0]["text"].split()[0]
    assert first_word in text


def test_narration_audio_files(app_client, print_book):
    d = detail(app_client, print_book)
    from app.providers.tts import EspeakTTS
    if not EspeakTTS.available()[0]:
        pytest.skip("offline TTS (espeakng-loader) not installed")
    assert all(p["audio_url"] for p in d["pages"]), [p["audio_url"] for p in d["pages"]]
    a = app_client.get(d["pages"][0]["audio_url"])
    assert a.status_code == 200 and len(a.content) > 2000
    assert a.content[:3] == b"ID3" or (a.content[0] == 0xFF and a.content[1] & 0xE0 == 0xE0) or a.content[:4] == b"RIFF"
    assert d["pages"][0]["audio_meta"]["duration_s"] > 1


def test_revisions_and_restore(app_client, worker, print_book):
    d = detail(app_client, print_book)
    pg = d["pages"][1]
    original = pg["text"]
    r = app_client.patch(f"/api/projects/{print_book}/pages/{pg['id']}", json={"text": "متن تازه برای آزمایش."})
    assert r.status_code == 200
    revs = app_client.get(f"/api/projects/{print_book}/pages/{pg['id']}/revisions").json()
    assert revs and revs[0]["text"] == original
    r = app_client.post(f"/api/projects/{print_book}/pages/{pg['id']}/revisions/{revs[0]['id']}/restore",
                        json={"restore_text": True, "restore_image": False, "rebuild": False})
    assert r.status_code == 200, r.text
    assert detail(app_client, print_book)["pages"][1]["text"] == original
    # the restore itself is undoable
    revs2 = app_client.get(f"/api/projects/{print_book}/pages/{pg['id']}/revisions").json()
    assert revs2[0]["text"] == "متن تازه برای آزمایش." and revs2[0]["reason"] == "before_restore"


def test_inpaint_region_repair(app_client, worker, print_book):
    d = detail(app_client, print_book)
    pg = d["pages"][2]
    before = Image.open(io.BytesIO(app_client.get(pg["image_url"]).content)).convert("RGB")
    mask = Image.new("L", before.size, 0)
    w, h = before.size
    ImageDraw.Draw(mask).ellipse((w * 0.1, h * 0.1, w * 0.25, h * 0.22), fill=255)
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    # empty mask is rejected before any job is queued
    empty = io.BytesIO()
    Image.new("L", before.size, 0).save(empty, format="PNG")
    r = app_client.post(f"/api/projects/{print_book}/pages/{pg['id']}/inpaint", files={"mask": ("m.png", empty.getvalue(), "image/png")},
                        data={"engine": "classical"})
    assert r.status_code == 422
    r = app_client.post(f"/api/projects/{print_book}/pages/{pg['id']}/inpaint", files={"mask": ("m.png", buf.getvalue(), "image/png")},
                        data={"engine": "classical", "prompt": "remove the stray mark"})
    assert r.status_code == 200, r.text
    assert 0 < r.json()["mask_coverage"] < 0.1
    assert worker.drain(200)
    d2 = detail(app_client, print_book)
    pg2 = d2["pages"][2]
    assert pg2["image_url"] != pg["image_url"] and pg2["inpaints"] == 1
    after = Image.open(io.BytesIO(app_client.get(pg2["image_url"]).content)).convert("RGB")
    diff = ImageChops.difference(before, after).convert("L")
    outside = ImageChops.multiply(diff, Image.eval(mask, lambda v: 255 - v))
    assert outside.getbbox() is None, "pixels outside the mask must stay identical"
    assert diff.getbbox() is not None
    revs = app_client.get(f"/api/projects/{print_book}/pages/{pg['id']}/revisions").json()
    assert any(r_["image_url"] == pg["image_url"] for r_ in revs), "old image kept as a revision (undo)"
    assert d2["version"] > d["version"]


def test_inpaint_with_ai_provider(app_client, worker, print_book, mock_server):
    r = app_client.post("/api/providers", json={"name": "Mock edit API", "kind": "image", "provider_type": "openai_compatible",
                                                "base_url": mock_server.url + "/v1", "model": "gpt-image-1", "api_key": "k"})
    assert r.status_code == 201, r.text
    prov = r.json()["id"]
    d = detail(app_client, print_book)
    pg = d["pages"][3]
    w, h = Image.open(io.BytesIO(app_client.get(pg["image_url"]).content)).size
    mask = Image.new("RGBA", (w // 4, h // 4), (0, 0, 0, 0))  # browser-style alpha mask at a smaller size
    ImageDraw.Draw(mask).rectangle((5, 5, 30, 30), fill=(255, 0, 0, 200))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    r = app_client.post(f"/api/projects/{print_book}/pages/{pg['id']}/inpaint", files={"mask": ("m.png", buf.getvalue(), "image/png")},
                        data={"engine": prov, "prompt": "a small blue flower"})
    assert r.status_code == 200, r.text
    assert worker.drain(200)
    assert mock_server.state.calls[-1][0] == "edit_mask"
    hist = app_client.get(f"/api/projects/{print_book}/history").json()
    rep = next(x for x in hist if x["action"] == "image_repaired" and x["detail"]["page"] == pg["number"])
    assert rep["detail"]["engine"] == "Mock edit API" and rep["detail"]["changed_outside_mask"] <= 0.001
    usage = app_client.get("/api/usage").json()
    assert any((u.get("purpose") or "").startswith("inpaint_page") and u["success"] for u in usage)
    assert app_client.post(f"/api/projects/{print_book}/pages/{pg['id']}/inpaint", files={"mask": ("m.png", buf.getvalue(), "image/png")},
                           data={"engine": "no-such-provider"}).status_code == 422


def test_exports_epub_html_zip(app_client, print_book):
    from app.export.books import epubcheck_available
    r = app_client.post(f"/api/projects/{print_book}/exports", json={"format": "epub"})
    assert r.status_code == 200, r.text
    info = r.json()
    if epubcheck_available()[0]:
        assert info["validation"]["checked"] and info["validation"]["ok"], info["validation"]
    else:
        assert info["validation"]["checked"] is False and info["validation"]["ok"] is None
    e = app_client.get(f"/api/projects/{print_book}/exports/epub")
    assert e.status_code == 200 and e.headers["content-type"] == "application/epub+zip"
    z = zipfile.ZipFile(io.BytesIO(e.content))
    assert z.namelist()[0] == "mimetype" and z.read("mimetype") == b"application/epub+zip"
    opf = z.read("OEBPS/content.opf").decode()
    assert 'page-progression-direction="rtl"' in opf and "<dc:language>fa</dc:language>" in opf
    assert any(n.startswith("OEBPS/audio/") for n in z.namelist()) == (info["audio_pages"] > 0)
    r = app_client.post(f"/api/projects/{print_book}/exports", json={"format": "html"})
    assert r.status_code == 200
    h = app_client.get(f"/api/projects/{print_book}/exports/html").text
    assert 'dir="rtl"' in h and h.count('class="sheet page"') == 6 and "speechSynthesis" in h
    r = app_client.post(f"/api/projects/{print_book}/exports", json={"format": "images"})
    assert r.status_code == 200 and r.json()["images"] >= 6
    zz = zipfile.ZipFile(io.BytesIO(app_client.get(f"/api/projects/{print_book}/exports/images").content))
    assert "text.json" in zz.namelist()
    assert set(detail(app_client, print_book)["exports"]) == {"epub", "html", "images"}
    assert app_client.post(f"/api/projects/{print_book}/exports", json={"format": "docx"}).status_code == 422


def test_character_scale_changes_relative_height(app_client, worker, print_book):
    d = detail(app_client, print_book)
    comp = next(c for c in d["characters"] if c["char_id"] == "comp")
    before = d["relative_heights"].get("comp")
    r = app_client.patch(f"/api/projects/{print_book}/characters/{comp['id']}",
                         json={"spec": {"appearance": {"scale": 1.6}}, "regenerate_images": True})
    assert r.status_code == 200, r.text
    d2 = detail(app_client, print_book)
    assert next(c for c in d2["characters"] if c["char_id"] == "comp")["ref_url"], "size-only change keeps the reference sheet"
    assert app_client.post(f"/api/projects/{print_book}/start").status_code == 200
    assert worker.drain(300)
    d3 = detail(app_client, print_book)
    assert d3["relative_heights"]["comp"] > before * 1.3, (before, d3["relative_heights"])
    assert d3["status"] in ("completed", "completed_with_warnings"), d3["qa_report"]["summary"]


def test_accounts_mode(app_client):
    old = settings.auth_mode_env
    settings.auth_mode = "accounts"
    try:
        c = app_client
        assert c.get("/api/auth/status").json() == {"mode": "accounts", "needs_setup": True}
        assert c.get("/api/projects").status_code == 401
        r = c.post("/api/auth/setup", json={"username": "admin", "password": "short"})
        assert r.status_code == 422
        r = c.post("/api/auth/setup", json={"username": "admin", "password": "Adm1n-pass"})
        assert r.status_code == 200
        admin = {"Authorization": f"Bearer {r.json()['token']}"}
        assert c.post("/api/auth/setup", json={"username": "second", "password": "Adm1n-pass"}).status_code == 409
        owned = len(c.get("/api/projects", headers=admin).json())
        assert owned >= 1, "books created before accounts belong to the first admin"
        r = c.post("/api/auth/users", headers=admin, json={"username": "kid", "password": "Kid-pass-1", "role": "user"})
        assert r.status_code == 201
        tok = c.post("/api/auth/login", json={"username": "kid", "password": "Kid-pass-1"}).json()["token"]
        kid = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/projects", headers=kid).json() == []
        some = c.get("/api/projects", headers=admin).json()[0]["id"]
        assert c.get(f"/api/projects/{some}", headers=kid).status_code == 404  # no cross-user access
        assert c.get(f"/api/projects/{some}/pdf", headers=kid).status_code == 404
        assert c.post("/api/providers", headers=kid, json={"name": "x", "kind": "llm", "provider_type": "ollama"}).status_code == 403
        assert c.get("/api/auth/users", headers=kid).status_code == 403
        r = c.post("/api/projects", headers=kid, json={"settings": {"page_count": 6, "seed": 1}, "start": False})
        assert r.status_code == 201
        assert len(c.get("/api/projects", headers=kid).json()) == 1
        # token in query string (needed for <img>/<audio> tags)
        assert c.get(f"/api/projects?token={tok}").status_code == 200
        assert c.post("/api/auth/logout", headers=kid).status_code == 200
        assert c.get("/api/projects", headers=kid).status_code == 401
        for _ in range(5):
            assert c.post("/api/auth/login", json={"username": "kid", "password": "wrong-pass-1"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "kid", "password": "Kid-pass-1"}).status_code == 429
        admins = [u for u in c.get("/api/auth/users", headers=admin).json() if u["role"] == "admin"]
        assert c.patch(f"/api/auth/users/{admins[0]['id']}", headers=admin, json={"role": "user"}).status_code == 409
    finally:
        settings.auth_mode = old
        from sqlalchemy import update
        from app.models import AuthSession, User
        from app.security import login_throttle
        login_throttle.reset("kid")
        with session_scope() as s:
            s.execute(update(Project).values(owner_id="local"))
            s.query(AuthSession).delete()
            s.query(User).delete()


def test_paid_tts_cost_is_announced(app_client, worker):
    c = app_client
    r = c.post("/api/providers", json={"name": "Paid TTS", "kind": "tts", "provider_type": "openai_compatible",
                                        "base_url": "http://127.0.0.1:9/v1", "model": "tts-1",
                                        "params": {"pricing": {"per_1k_chars": 0.015}}})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    est = c.post("/api/estimate", json={"title": "x", "topic": "a brave cat", "page_count": 8, "narration_audio": True,
                                        "tts_provider_id": tid}).json()
    assert est["tts_chars"] == 8 * 350 and est["tts_cost_usd"] > 0 and est["paid"] is True
    assert est["providers"]["tts"] == "Paid TTS"
    off = c.post("/api/estimate", json={"title": "x", "topic": "a brave cat", "page_count": 8}).json()
    assert off["tts_cost_usd"] == 0
    # creating a paid narrated book requires confirmation
    r = c.post("/api/projects", json={"settings": {"title": "x", "topic": "a brave cat", "page_count": 8, "narration_audio": True,
                                                   "tts_provider_id": tid}})
    assert r.status_code == 402
    # the outline gate can be switched on after creation
    r = c.post("/api/projects", json={"settings": {"title": "y", "topic": "a brave cat", "page_count": 8, "review_outline": True}})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["id"]
    r = c.patch(f"/api/projects/{pid}", json={"settings": {"review_outline": False, "tts_provider_id": tid}})
    assert r.status_code == 200, r.text
    # narrate on a paid TTS answers 402 until the cost is confirmed (pages exist once the plan is written)
    assert worker.drain(400)
    d = c.get(f"/api/projects/{pid}").json()
    assert d["pages"], d["status"]
    r = c.post(f"/api/projects/{pid}/narrate", json={})
    assert r.status_code == 402 and "Confirm" in r.text, r.text
