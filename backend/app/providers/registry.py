"""Provider registry + router: builds adapters from DB config, applies retries
with exponential backoff (honours Retry-After), per-provider pacing, fallback
chains, and records usage/cost for every call. Secrets never reach logs."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from sqlalchemy.orm import Session

from ..db import session_scope
from ..logging_setup import redact
from ..models import AppSetting, ProviderConfig, UsageRecord
from ..security import decrypt_secret
from .base import AuthError, ConfigError, ProviderError, RateLimitError
from .image_http import Automatic1111Image, ComfyUIImage, FastSDCPUImage, IOPaintInpaint, OpenAICompatImage
from .llm_http import AnthropicLLM, OllamaLLM, OpenAICompatLLM
from .offline import OfflineLLM, ProceduralImage
from .tts import EspeakTTS, OpenAICompatTTS

log = logging.getLogger("providers")

PROVIDER_TYPES = {
    "llm": ["offline", "ollama", "openai_compatible", "anthropic"],
    "image": ["offline", "openai_compatible", "comfyui", "automatic1111", "fastsdcpu"],
    "vision": ["ollama", "openai_compatible", "anthropic"],
    "inpaint": ["iopaint"],
    "tts": ["offline", "openai_compatible"],
}

BUILTIN_LLM_ID = "builtin-offline-story"
BUILTIN_IMAGE_ID = "builtin-procedural-image"
BUILTIN_TTS_ID = "builtin-espeak-tts"


def build(cfg: ProviderConfig, overrides: dict | None = None) -> Any:
    key = decrypt_secret(cfg.api_key)
    params = dict(cfg.params or {})
    if overrides:
        params.update(overrides)
    t, kind = cfg.provider_type, cfg.kind
    timeout = float(params.get("timeout", 300))
    if t == "offline":
        if kind == "tts":
            return EspeakTTS(params)
        return OfflineLLM() if kind == "llm" else ProceduralImage(params)
    if kind == "tts":
        if t == "openai_compatible":
            return OpenAICompatTTS(cfg.base_url, key, cfg.model, params, timeout)
        raise ConfigError(f"Unknown TTS provider type {t!r}")
    if t == "fastsdcpu":
        return FastSDCPUImage(cfg.base_url, cfg.model, params, timeout)
    if t == "iopaint":
        return IOPaintInpaint(cfg.base_url, cfg.model, params, timeout)
    if t == "ollama":
        return OllamaLLM(cfg.base_url, cfg.model, params, timeout)
    if t == "openai_compatible":
        if kind == "image":
            return OpenAICompatImage(cfg.base_url, key, cfg.model, params, timeout)
        return OpenAICompatLLM(cfg.base_url, key, cfg.model, params, timeout)
    if t == "anthropic":
        return AnthropicLLM(cfg.base_url, key, cfg.model, params, timeout)
    if t == "comfyui":
        return ComfyUIImage(cfg.base_url, cfg.model, params, timeout)
    if t == "automatic1111":
        return Automatic1111Image(cfg.base_url, cfg.model, params, timeout)
    raise ConfigError(f"Unknown provider type {t!r} for kind {kind!r}")


def validate_config(kind: str, provider_type: str, base_url: str, model: str) -> list[str]:
    errs = []
    if kind not in PROVIDER_TYPES:
        errs.append(f"kind must be one of {list(PROVIDER_TYPES)}")
    elif provider_type not in PROVIDER_TYPES[kind]:
        errs.append(f"provider_type for {kind} must be one of {PROVIDER_TYPES[kind]}")
    if provider_type not in ("offline",) and base_url and not base_url.startswith(("http://", "https://")):
        errs.append("base_url must start with http:// or https://")
    if provider_type in ("openai_compatible", "anthropic") and not model and kind != "tts":
        errs.append("model is required")
    return errs


def seed_builtins(s: Session) -> None:
    if not s.get(ProviderConfig, BUILTIN_LLM_ID):
        s.add(ProviderConfig(id=BUILTIN_LLM_ID, name="Built-in story engine (offline, no AI)", kind="llm", provider_type="offline",
                             builtin=True, model="templates-v1"))
    if not s.get(ProviderConfig, BUILTIN_IMAGE_ID):
        s.add(ProviderConfig(id=BUILTIN_IMAGE_ID, name="Built-in procedural illustrator (offline, CPU)", kind="image",
                             provider_type="offline", builtin=True, model="procedural-v1"))
    if not s.get(ProviderConfig, BUILTIN_TTS_ID):
        s.add(ProviderConfig(id=BUILTIN_TTS_ID, name="Built-in narrator (eSpeak NG, offline, optional)", kind="tts",
                             provider_type="offline", builtin=True, model="espeak-ng"))
    if not s.get(AppSetting, "defaults"):
        s.add(AppSetting(key="defaults", value={"llm": BUILTIN_LLM_ID, "review_llm": "", "image": BUILTIN_IMAGE_ID, "vision": "",
                                                "tts": BUILTIN_TTS_ID, "inpaint": ""}))
    else:
        row = s.get(AppSetting, "defaults")
        if "tts" not in (row.value or {}):
            row.value = {**(row.value or {}), "tts": BUILTIN_TTS_ID, "inpaint": ""}
    s.flush()


def get_defaults(s: Session) -> dict:
    row = s.get(AppSetting, "defaults")
    base = {"llm": BUILTIN_LLM_ID, "image": BUILTIN_IMAGE_ID, "vision": "", "review_llm": "", "tts": BUILTIN_TTS_ID, "inpaint": ""}
    return {**base, **(row.value or {})} if row else base


class _Pacer:
    """Minimum spacing between calls to one provider (requests-per-minute limit)."""

    def __init__(self):
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, pid: str, rpm: float):
        if not rpm or rpm <= 0:
            return
        gap = 60.0 / rpm
        with self._lock:
            now = time.monotonic()
            nxt = max(now, self._last.get(pid, 0) + gap)
            self._last[pid] = nxt
        if nxt > now:
            time.sleep(nxt - now)


pacer = _Pacer()


def cost_of(cfg: ProviderConfig, input_tokens=0, output_tokens=0, images=0, chars=0) -> float:
    pr = (cfg.params or {}).get("pricing") or {}
    return round(input_tokens / 1000 * float(pr.get("input_per_1k", 0)) + output_tokens / 1000 * float(pr.get("output_per_1k", 0))
                 + images * float(pr.get("per_image", 0)) + chars / 1000 * float(pr.get("per_1k_chars", 0)), 6)


class BudgetExceeded(ProviderError):
    pass


class Router:
    """Runs a provider call with retries and fallbacks; logs usage per project."""

    def __init__(self, project_id: str = "", budget_usd: float = 0.0, sleep=time.sleep):
        self.project_id = project_id
        self.budget = budget_usd
        self.sleep = sleep

    def spent(self) -> float:
        if not self.project_id:
            return 0.0
        with session_scope() as s:
            from sqlalchemy import func, select
            return float(s.scalar(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)).where(UsageRecord.project_id == self.project_id)) or 0)

    def run(self, provider_id: str, kind: str, purpose: str, fn: Callable[[Any, ProviderConfig], Any],
            overrides: dict | None = None, _visited: set | None = None):
        visited = _visited or set()
        with session_scope() as s:
            cfg = s.get(ProviderConfig, provider_id)
            if cfg is None or not cfg.enabled:
                raise ConfigError(f"Provider {provider_id!r} not found or disabled")
            s.expunge(cfg)
        visited.add(cfg.id)
        params = cfg.params or {}
        retries = int(params.get("max_retries", 3))
        base = float(params.get("backoff_base", 1.5))
        last_err: Exception | None = None
        if self.budget > 0 and cfg.provider_type != "offline":
            if self.spent() >= self.budget:
                raise BudgetExceeded(f"Project budget of ${self.budget:.2f} reached; refusing further paid calls")
        for attempt in range(retries + 1):
            t0 = time.monotonic()
            try:
                inst = build(cfg, overrides)
                pacer.wait(cfg.id, float(params.get("rpm", 0)))
                result = fn(inst, cfg)
                self._usage(cfg, purpose, result, int((time.monotonic() - t0) * 1000), True, "")
                return result
            except (AuthError, ConfigError) as e:
                last_err = e
                self._usage(cfg, purpose, None, int((time.monotonic() - t0) * 1000), False, str(e))
                break  # retrying will not help
            except ProviderError as e:
                last_err = e
                self._usage(cfg, purpose, None, int((time.monotonic() - t0) * 1000), False, str(e))
                if not e.retryable or attempt == retries:
                    break
                wait = e.retry_after if isinstance(e, RateLimitError) and e.retry_after else min(30.0, base ** attempt)
                log.warning("provider %s attempt %d failed (%s); retrying in %.1fs", cfg.name, attempt + 1, redact(str(e)), wait)
                self.sleep(wait)
            except NotImplementedError:
                raise
            except Exception as e:  # unexpected adapter error: treat as non-retryable
                last_err = e
                self._usage(cfg, purpose, None, int((time.monotonic() - t0) * 1000), False, f"{e.__class__.__name__}: {e}")
                break
        fb = cfg.fallback_id
        if fb and fb not in visited:
            log.warning("provider %s failed; falling back to %s", cfg.name, fb)
            return self.run(fb, kind, purpose, fn, overrides, visited)
        raise last_err if last_err else ProviderError("Provider call failed")

    def _usage(self, cfg: ProviderConfig, purpose: str, result, ms: int, ok: bool, err: str):
        it = getattr(result, "input_tokens", 0) or 0
        ot = getattr(result, "output_tokens", 0) or 0
        chars = getattr(result, "input_chars", 0) or 0
        if chars:
            it = chars  # narration: characters are recorded in the input column
        imgs = 1 if (ok and getattr(result, "data", None) is not None and not chars and cfg.kind in ("image", "inpaint")) else 0
        try:
            with session_scope() as s:
                s.add(UsageRecord(project_id=self.project_id, provider_id=cfg.id, provider_name=cfg.name, kind=cfg.kind,
                                  model=getattr(result, "model", "") or cfg.model, purpose=purpose, input_tokens=it, output_tokens=ot,
                                  images=imgs, duration_ms=ms, cost_usd=cost_of(cfg, 0 if chars else it, ot, imgs, chars),
                                  cost_is_estimate=True, success=ok, error=redact(err)[:1000]))
        except Exception:  # usage logging must never break generation
            log.exception("could not record usage")


def estimate(settings: dict, llm: ProviderConfig | None, image: ProviderConfig | None, tts: ProviderConfig | None = None) -> dict:
    """Pre-run estimate of calls, tokens, cost and time."""
    pages = int(settings.get("page_count", 12))
    img_attempts = int(settings.get("max_image_attempts", 3))
    chars = 3
    llm_calls = 0 if (llm is None or llm.provider_type == "offline") else 2 + max(1, pages // 6)
    in_tok = llm_calls * 2500
    out_tok = llm_calls * (350 * pages // max(1, llm_calls) + 600)
    images_min = pages + 1 + chars
    images_max = images_min + (img_attempts - 1) * (pages + 1)
    paid_llm = cost_of(llm, in_tok, out_tok) if llm else 0
    img_unit = cost_of(image, 0, 0, 1) if image else 0
    # narration: ~350 characters of text per page (upper bound for ages 6-12), billed per 1k characters
    tts_chars = pages * 350 if settings.get("narration_audio") and tts is not None else 0
    tts_cost = cost_of(tts, chars=tts_chars) if tts_chars else 0
    secs_per_img = 1.2 if (image is None or image.provider_type == "offline") else float((image.params or {}).get("seconds_per_image", 20))
    return {
        "pages": pages, "llm_calls": llm_calls, "input_tokens": in_tok, "output_tokens": out_tok,
        "images_min": images_min, "images_max": images_max,
        "tts_chars": tts_chars, "tts_cost_usd": round(tts_cost, 4),
        "cost_min_usd": round(paid_llm + img_unit * images_min + tts_cost, 4), "cost_max_usd": round(paid_llm * 1.5 + img_unit * images_max + tts_cost * 1.5, 4),
        "minutes_estimate": round((images_min * secs_per_img + llm_calls * 25 + pages * 1.5) / 60, 1),
        "paid": bool((paid_llm + img_unit + tts_cost) > 0),
        "pricing_configured": bool((llm and (llm.params or {}).get("pricing")) or (image and (image.params or {}).get("pricing"))
                                   or (tts_chars and (tts.params or {}).get("pricing"))),
    }
