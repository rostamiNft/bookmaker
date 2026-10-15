"""End-to-end tests through the HTTP API with the real worker and pipeline."""
import io
import json

import pytest
from PIL import Image
from pypdf import PdfReader

from app.config import settings
from app.db import session_scope
from app.models import Job, Page, Project


def create(c, **s):
    body = {"settings": {"page_count": 8, "seed": 3, **s}, "start": True}
    r = c.post("/api/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def detail(c, pid):
    return c.get(f"/api/projects/{pid}").json()


@pytest.fixture(scope="module")
def book(app_client, worker):
    pid = create(app_client, topic="a lost star", language="en", page_count=10, author="Tester")
    assert worker.drain(300)
    return pid


def test_full_book_generation_and_pdf_download(app_client, book):
    d = detail(app_client, book)
    assert d["status"] in ("completed", "completed_with_warnings"), d["qa_report"].get("summary")
    assert d["qa_report"]["summary"]["errors"] == 0 and d["qa_report"]["summary"]["pdf_valid"]
    assert len(d["pages"]) == 10 and all(p["image_url"] for p in d["pages"])
    assert {c["char_id"] for c in d["characters"]} >= {"hero", "comp"} and all(c["ref_url"] for c in d["characters"])
    assert len(d["renders"]) == 15
    r = app_client.get(f"/api/projects/{book}/pdf")
    assert r.status_code == 200 and r.content[:5] == b"%PDF-"
    reader = PdfReader(io.BytesIO(r.content))
    assert len(reader.pages) == 15 and reader.metadata["/Author"] == "Tester"
    img = app_client.get(d["pages"][0]["image_url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"
    hist = [h["action"] for h in app_client.get(f"/api/projects/{book}/history").json()]
    assert {"story_planned", "character_bible_ready", "illustrations_generated", "pdf_built", "qa_completed"} <= set(hist)


def test_regenerate_single_image_only_touches_that_page(app_client, worker, book):
    before = detail(app_client, book)
    target = before["pages"][4]
    r = app_client.post(f"/api/projects/{book}/pages/{target['id']}/regenerate-image", json={})
    assert r.status_code == 200
    assert worker.drain(120)
    after = detail(app_client, book)
    changed = [a["number"] for a, b in zip(after["pages"], before["pages"]) if a["image_url"] != b["image_url"]]
    assert changed == [5]
    assert after["version"] == before["version"] + 1


def test_edit_reorder_add_delete_and_rebuild(app_client, worker, book):
    d = detail(app_client, book)
    p1 = d["pages"][0]
    r = app_client.patch(f"/api/projects/{book}/pages/{p1['id']}", json={"text": "A brand new first page written by the user."})
    assert r.status_code == 200
    order = [p["id"] for p in d["pages"]]
    order[1], order[2] = order[2], order[1]
    assert app_client.post(f"/api/projects/{book}/pages/reorder", json={"order": order}).status_code == 200
    assert app_client.post(f"/api/projects/{book}/pages/reorder", json={"order": order[:-1]}).status_code == 422
    r = app_client.post(f"/api/projects/{book}/pages", json={"after": 3, "text": "An extra page added in the middle of the story."})
    assert r.status_code == 201 and r.json()["number"] == 4
    middle = detail(app_client, book)["pages"][6]  # never the last page: that is the story's ending (QA would flag it)
    assert app_client.delete(f"/api/projects/{book}/pages/{middle['id']}").status_code == 200
    assert app_client.post(f"/api/projects/{book}/rebuild").status_code == 200
    assert worker.drain(200)
    d2 = detail(app_client, book)
    assert d2["pages"][0]["text"].startswith("A brand new first page")
    assert d2["pages"][3]["text"].startswith("An extra page")
    assert [p["number"] for p in d2["pages"]] == list(range(1, 11))
    assert all(p["image_url"] for p in d2["pages"])  # the new page got its own illustration
    assert d2["qa_report"]["summary"]["pdf_valid"]


def test_character_edit_invalidates_and_redraws_affected_images(app_client, worker, book):
    d = detail(app_client, book)
    hero = next(c for c in d["characters"] if c["char_id"] == "hero")
    r = app_client.patch(f"/api/projects/{book}/characters/{hero['id']}", json={"spec": {"outfit": {"top_color": "#123456"}}})
    assert r.json()["images_invalidated"]
    with session_scope() as s:
        pend = s.query(Page).filter(Page.project_id == book, Page.image_status == "pending").count()
    assert pend > 0
    app_client.post(f"/api/projects/{book}/rebuild")
    assert worker.drain(200)
    d2 = detail(app_client, book)
    hero2 = next(c for c in d2["characters"] if c["char_id"] == "hero")
    assert "#123456" in hero2["spec"]["palette"]
    assert all(p["image_status"] in ("generated", "approved") for p in d2["pages"])
    assert d2["qa_report"]["summary"]["errors"] == 0


def test_persian_book(app_client, worker):
    pid = create(app_client, topic="شجاعت در تاریکی شب", language="fa", page_count=8)
    assert worker.drain(300)
    d = detail(app_client, pid)
    assert d["status"].startswith("completed"), d["qa_report"]["summary"]
    assert d["qa_report"]["pdf"]["info"]["lang"] == "fa"
    assert any("Vazirmatn" in f["name"] for f in d["qa_report"]["pdf"]["info"]["fonts"])


def test_pause_and_resume(app_client, worker):
    pid = create(app_client, topic="kite friendship", page_count=12, fantasy_level=0)
    # run the plan stage, then request a pause mid-images
    jid = worker.claim()
    from app.pipeline.worker import run_job
    from app.pipeline import orchestrator
    orig = orchestrator.Pipeline.generate_page_image
    count = {"n": 0}

    def gen(self, page_id, variant_offset=0):
        count["n"] += 1
        if count["n"] == 3:  # with 2 image workers, call 3 starts only after a page finished
            with session_scope() as s:
                s.get(Project, pid).pause_requested = True
        return orig(self, page_id, variant_offset)

    orchestrator.Pipeline.generate_page_image = gen
    try:
        run_job(jid)
    finally:
        orchestrator.Pipeline.generate_page_image = orig
    d = detail(app_client, pid)
    assert d["status"] == "paused"
    done = sum(1 for p in d["pages"] if p["image_status"] == "generated")
    assert 0 < done < 12
    images_before = {p["id"]: p["image_url"] for p in d["pages"] if p["image_url"]}
    assert app_client.post(f"/api/projects/{pid}/start").status_code == 200
    assert worker.drain(300)
    d = detail(app_client, pid)
    assert d["status"].startswith("completed")
    for p in d["pages"]:  # already generated images were not regenerated on resume
        if p["id"] in images_before:
            assert p["image_url"] == images_before[p["id"]]


def test_recovery_after_crash(app_client, worker):
    pid = create(app_client, topic="nature stream", page_count=8)
    with session_scope() as s:  # simulate a crash: job marked running but no worker owns it
        j = s.query(Job).filter(Job.project_id == pid).one()
        j.status = "running"
        j.attempts = 1
    from app.pipeline.worker import recover_interrupted
    assert recover_interrupted() >= 1
    assert worker.drain(300)
    assert detail(app_client, pid)["status"].startswith("completed")


def test_duplicate_jobs_are_rejected(app_client, worker, book):
    d = detail(app_client, book)
    pg = d["pages"][0]["id"]
    assert app_client.post(f"/api/projects/{book}/pages/{pg}/regenerate-image", json={}).status_code == 200
    assert app_client.post(f"/api/projects/{book}/pages/{pg}/regenerate-text", json={}).status_code == 409
    assert worker.drain(120)


def test_image_provider_failure_falls_back(app_client, worker, mock_server):
    mock_server.state.reset()
    mock_server.state.always_500 = True
    r = app_client.post("/api/providers", json={"name": "Broken image API", "kind": "image", "provider_type": "openai_compatible",
                                               "base_url": mock_server.url + "/v1", "model": "gpt-image-1", "api_key": "sk-secret-value-123456",
                                               "params": {"max_retries": 1, "backoff_base": 0.01}, "fallback_id": "builtin-procedural-image"})
    prov = r.json()
    assert prov["api_key"] != "sk-secret-value-123456" and prov["api_key"].endswith("3456")
    pid = create(app_client, topic="star", page_count=6, image_provider_id=prov["id"])
    assert worker.drain(300)
    d = detail(app_client, pid)
    assert d["status"].startswith("completed"), d["qa_report"]["summary"]
    usage = app_client.get(f"/api/usage?project_id={pid}").json()
    assert any(not u["success"] for u in usage) and any(u["success"] and "procedural" in u["provider"].lower() for u in usage)
    assert all("sk-secret" not in (u["error"] or "") for u in usage)
    mock_server.state.always_500 = False


def test_book_with_llm_and_image_api_over_http(app_client, worker, mock_server):
    mock_server.state.reset()
    mock_server.state.chat_fail_first = 1
    llm = app_client.post("/api/providers", json={"name": "Mock LLM", "kind": "llm", "provider_type": "openai_compatible", "base_url": mock_server.url + "/v1",
                                                 "model": "mock-llm", "api_key": "sk-test", "params": {"pricing": {"input_per_1k": 0.001, "output_per_1k": 0.002}}}).json()
    img = app_client.post("/api/providers", json={"name": "Mock Images", "kind": "image", "provider_type": "openai_compatible", "base_url": mock_server.url + "/v1",
                                                 "model": "gpt-image-1", "api_key": "sk-test", "params": {"pricing": {"per_image": 0.01}}}).json()
    assert app_client.post(f"/api/providers/{llm['id']}/check").json()["ok"]
    body = {"settings": {"page_count": 8, "language": "en", "llm_provider_id": llm["id"], "image_provider_id": img["id"]}, "start": True}
    r = app_client.post("/api/projects", json=body)
    assert r.status_code == 402  # paid run needs explicit cost confirmation
    body["confirm_cost"] = True
    pid = app_client.post("/api/projects", json=body).json()["id"]
    assert worker.drain(300)
    d = detail(app_client, pid)
    assert d["title"] == "Mila Builds a Boat"
    assert d["plan"]["engine"] == "llm"
    assert d["qa_report"]["summary"]["pdf_valid"]
    assert any(c[0] == "edit" for c in mock_server.state.calls)  # character references were sent to the image API
    usage = app_client.get(f"/api/usage?project_id={pid}").json()
    assert sum(u["cost_usd"] for u in usage) > 0
    # LLM text rewrite of a single page
    pg = d["pages"][2]
    app_client.post(f"/api/projects/{pid}/pages/{pg['id']}/regenerate-text", json={"instruction": "make it calmer"})
    assert worker.drain(200)
    assert detail(app_client, pid)["pages"][2]["text"].startswith("Mila smiled")


def test_security_controls(app_client, book):
    assert app_client.get(f"/api/projects/{book}/files/../../bookmaker.db").status_code in (400, 404)
    assert app_client.get(f"/api/projects/{book}/files/plan.json").status_code == 200
    assert app_client.get("/api/projects/ffffffffffffffffffffffffffffffff").status_code == 404
    d = detail(app_client, book)
    cid = d["characters"][0]["id"]
    r = app_client.post(f"/api/projects/{book}/characters/{cid}/reference", files={"file": ("x.png", b"#!/bin/sh\nrm -rf /", "image/png")})
    assert r.status_code == 415
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (10, 200, 30)).save(buf, format="PNG")
    r = app_client.post(f"/api/projects/{book}/characters/{cid}/reference", files={"file": ("ok.png", buf.getvalue(), "image/png")})
    assert r.status_code == 200
    assert app_client.post("/api/projects", json={"settings": {"page_count": 99}}).status_code == 422
    assert app_client.post("/api/projects", json={"settings": {"page_count": 30}}).status_code == 422  # offline engine limit, reported
    old = settings.api_token
    settings.api_token = "s3cret"
    try:
        assert app_client.get("/api/projects").status_code == 401
        assert app_client.get("/api/projects", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    finally:
        settings.api_token = old


def test_delete_moves_to_trash(app_client, worker):
    pid = create(app_client, topic="kite", page_count=6, fantasy_level=0)
    assert worker.drain(200)
    assert app_client.delete(f"/api/projects/{pid}").status_code == 400  # needs confirmation
    assert app_client.delete(f"/api/projects/{pid}?confirm={pid}").status_code == 200
    assert app_client.get(f"/api/projects/{pid}").status_code == 404
    assert any(p.name.startswith(pid) for p in settings.trash_dir.iterdir())


def test_export_backup(app_client, book):
    r = app_client.get(f"/api/projects/{book}/export")
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "project.json" in names and any(n.startswith("pdf/") for n in names) and any(n.startswith("images/") for n in names)
    assert json.loads(z.read("project.json"))["plan"]["pages"]


def test_system_and_estimate(app_client):
    s = app_client.get("/api/system").json()
    assert "queue" in s and s["limits"]["books"] >= 1
    e = app_client.post("/api/estimate", json={"page_count": 12}).json()
    assert e["paid"] is False and e["images_min"] == 16
