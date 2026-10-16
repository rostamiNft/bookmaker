import pytest

from app.db import init_db, session_scope
from app.logging_setup import redact
from app.models import ProviderConfig, UsageRecord
from app.providers.base import AuthError, RateLimitError, TransientError
from app.providers.image_http import ComfyUIImage, OpenAICompatImage, fill_placeholders, nearest_size
from app.providers.llm_http import OllamaLLM, OpenAICompatLLM
from app.providers.registry import Router, build, seed_builtins, validate_config
from app.security import decrypt_secret, encrypt_secret, mask_secret, safe_join


@pytest.fixture(autouse=True, scope="module")
def db():
    init_db()
    with session_scope() as s:
        seed_builtins(s)


def test_secret_encryption_masking_and_redaction():
    enc = encrypt_secret("sk-live-1234567890abcdef")
    assert enc.startswith("enc:") and "1234567890" not in enc
    assert decrypt_secret(enc) == "sk-live-1234567890abcdef"
    assert mask_secret(enc) == "sk-…cdef"
    assert encrypt_secret("env:MY_KEY") == "env:MY_KEY"
    assert "sk-***" in redact("error with key sk-abcdefghijklmnop")
    assert "abc123secret" not in redact('{"api_key": "abc123secret"}')
    assert "Bearer ***" in redact("Authorization: Bearer abcdefghijkl")


def test_safe_join_blocks_traversal(tmp_path):
    from fastapi import HTTPException
    assert safe_join(tmp_path, "a/b.png") == (tmp_path / "a/b.png").resolve()
    for bad in ("../x", "/etc/passwd", "a/../../x"):
        with pytest.raises(HTTPException):
            safe_join(tmp_path, bad)


def test_config_validation():
    assert validate_config("llm", "comfyui", "", "") != []
    assert validate_config("image", "openai_compatible", "ftp://x", "m") != []
    assert validate_config("llm", "ollama", "http://localhost:11434", "llama3") == []


def _add(**kw):
    with session_scope() as s:
        c = ProviderConfig(**kw)
        s.add(c)
        s.flush()
        return c.id


def test_router_retries_rate_limits_then_succeeds():
    pid = _add(name="flaky", kind="llm", provider_type="ollama", base_url="http://x", model="m", params={"max_retries": 3, "backoff_base": 1.0})
    sleeps, n = [], {"i": 0}

    def fn(prov, cfg):
        n["i"] += 1
        if n["i"] < 3:
            raise RateLimitError("429", 429, retry_after=0.25)
        return "ok"

    assert Router("proj-r", sleep=sleeps.append).run(pid, "llm", "t", fn) == "ok"
    assert sleeps == [0.25, 0.25]
    with session_scope() as s:
        rows = s.query(UsageRecord).filter_by(project_id="proj-r").all()
        assert [r.success for r in rows] == [False, False, True]


def test_router_does_not_retry_auth_errors_and_uses_fallback():
    fb = _add(name="backup", kind="llm", provider_type="ollama", base_url="http://y", model="m")
    main = _add(name="main", kind="llm", provider_type="ollama", base_url="http://x", model="m", fallback_id=fb)
    seen = []

    def fn(prov, cfg):
        seen.append(cfg.name)
        if cfg.name == "main":
            raise AuthError("401", 401)
        return cfg.name

    assert Router("p2", sleep=lambda s: None).run(main, "llm", "t", fn) == "backup"
    assert seen == ["main", "backup"]


def test_router_gives_up_after_bounded_retries():
    pid = _add(name="down", kind="llm", provider_type="ollama", base_url="http://x", model="m", params={"max_retries": 2})
    calls = []

    def fn(prov, cfg):
        calls.append(1)
        raise TransientError("503", 503)

    with pytest.raises(TransientError):
        Router("p3", sleep=lambda s: None).run(pid, "llm", "t", fn)
    assert len(calls) == 3


def test_budget_is_enforced():
    pid = _add(name="paid", kind="llm", provider_type="ollama", base_url="http://x", model="m", params={"pricing": {"input_per_1k": 1000}})
    with session_scope() as s:
        s.add(UsageRecord(project_id="pb", cost_usd=5.0))
    from app.providers.registry import BudgetExceeded
    with pytest.raises(BudgetExceeded):
        Router("pb", budget_usd=1.0).run(pid, "llm", "t", lambda p, c: "x")


# ---------------- real HTTP against mock services
def test_openai_compatible_llm_and_vision(mock_server):
    llm = OpenAICompatLLM(mock_server.url + "/v1", "sk-test", "mock-llm")
    assert llm.check()["model_available"] is True
    res = llm.complete("Exactly 8 story pages", "sys", json_mode=True)
    assert '"Mila Builds a Boat"' in res.text and res.input_tokens == 1500
    assert '"relevance"' in llm.describe(b"\x89PNG", "check").text
    with pytest.raises(AuthError):
        OpenAICompatLLM(mock_server.url + "/v1", "bad-key", "mock-llm").complete("x")


def test_ollama_adapter(mock_server):
    o = OllamaLLM(mock_server.url, "llama3.1:8b")
    assert o.check()["model_available"]
    assert o.complete("hi").text == "Hello from Ollama"


def test_openai_images_with_references_and_retry(mock_server):
    mock_server.state.reset()
    img = OpenAICompatImage(mock_server.url + "/v1", "k", "gpt-image-1")
    r = img.generate("a cat", "text", 1200, 900, reference_images=[b"ref1", b"ref2"])
    assert r.data[:4] == b"\x89PNG" and r.meta["api_size"] == [1536, 1024] and r.meta["used_references"]
    assert mock_server.state.calls[-1] == ("edit", "1536x1024", 2)
    mock_server.state.image_errors = [429, 500]
    pid = _add(name="img", kind="image", provider_type="openai_compatible", base_url=mock_server.url + "/v1", model="dall-e-3", params={"max_retries": 3})
    r = Router("pi", sleep=lambda s: None).run(pid, "image", "t", lambda p, c: p.generate("x", "", 1024, 1024))
    assert r.data[:4] == b"\x89PNG"


def test_comfyui_workflow_placeholders_and_polling(mock_server):
    wf = {"a": {"inputs": {"seed": "{{seed}}", "text": "style: {{prompt}}"}}}
    assert fill_placeholders(wf, {"seed": 5, "prompt": "cat"}) == {"a": {"inputs": {"seed": 5, "text": "style: cat"}}}
    c = ComfyUIImage(mock_server.url, "model.safetensors", {"poll_interval": 0.1})
    assert c.check()["ok"]
    r = c.generate("a fox", "text", 1000, 750, seed=3, reference_images=[b"\x89PNGref"])
    assert r.data[:4] == b"\x89PNG"
    assert ("comfy_prompt", 1024, 768, True) in mock_server.state.calls  # numeric placeholders stay numbers, sizes snapped to 64
    assert any(c[0] == "comfy_upload" for c in mock_server.state.calls)


def test_nearest_size():
    assert nearest_size(1200, 900, [(1024, 1024), (1536, 1024), (1024, 1536)]) == (1536, 1024)


def test_build_offline_providers():
    with session_scope() as s:
        cfg = s.get(ProviderConfig, "builtin-procedural-image")
        assert build(cfg).check()["ok"]
