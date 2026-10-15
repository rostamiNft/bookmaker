"""LLM adapters: Ollama, any OpenAI-compatible endpoint (OpenAI, OpenRouter,
Groq, Together, LM Studio, vLLM, llama.cpp server, LocalAI ...) and Anthropic."""
from __future__ import annotations

import base64

from .base import ConfigError, LLMProvider, LLMResult, VisionProvider, VisionResult
from .http_util import request


class OllamaLLM(LLMProvider, VisionProvider):
    def __init__(self, base_url: str, model: str, params: dict | None = None, timeout: float = 300):
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.model = model or "llama3.1:8b"
        self.params = params or {}
        self.timeout = timeout

    def complete(self, prompt, system="", json_mode=False, temperature=0.8, max_tokens=4000):
        body = {"model": self.model, "prompt": prompt, "system": system, "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens, **self.params.get("options", {})}}
        if json_mode:
            body["format"] = "json"
        r = request("POST", f"{self.base_url}/api/generate", json=body, timeout=self.timeout).json()
        return LLMResult(text=r.get("response", ""), input_tokens=r.get("prompt_eval_count", 0) or 0,
                         output_tokens=r.get("eval_count", 0) or 0, model=self.model)

    def describe(self, image, prompt, mime="image/png"):
        body = {"model": self.model, "prompt": prompt, "stream": False, "images": [base64.b64encode(image).decode()]}
        r = request("POST", f"{self.base_url}/api/generate", json=body, timeout=self.timeout).json()
        return VisionResult(text=r.get("response", ""), input_tokens=r.get("prompt_eval_count", 0) or 0,
                            output_tokens=r.get("eval_count", 0) or 0, model=self.model)

    def check(self):
        r = request("GET", f"{self.base_url}/api/tags", timeout=10).json()
        names = [m.get("name") for m in r.get("models", [])]
        return {"ok": True, "models": names[:50], "model_available": self.model in names or any(n.split(":")[0] == self.model for n in names)}


class OpenAICompatLLM(LLMProvider, VisionProvider):
    def __init__(self, base_url: str, api_key: str, model: str, params: dict | None = None, timeout: float = 180):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.params = params or {}
        self.timeout = timeout
        if not self.model:
            raise ConfigError("A model name is required for OpenAI-compatible providers")

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, prompt, system="", json_mode=False, temperature=0.8, max_tokens=4000):
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        body = {"model": self.model, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens}
        if json_mode and self.params.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        body.update(self.params.get("extra_body", {}))
        r = request("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body, timeout=self.timeout).json()
        usage = r.get("usage") or {}
        text = r["choices"][0]["message"].get("content") or ""
        return LLMResult(text=text, input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
                         model=r.get("model", self.model), raw=None)

    def describe(self, image, prompt, mime="image/png"):
        b64 = base64.b64encode(image).decode()
        msgs = [{"role": "user", "content": [{"type": "text", "text": prompt},
                                              {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]
        body = {"model": self.model, "messages": msgs, "max_tokens": 800, "temperature": 0}
        r = request("POST", f"{self.base_url}/chat/completions", headers=self._headers(), json=body, timeout=self.timeout).json()
        usage = r.get("usage") or {}
        return VisionResult(text=r["choices"][0]["message"].get("content") or "", input_tokens=usage.get("prompt_tokens", 0),
                            output_tokens=usage.get("completion_tokens", 0), model=self.model)

    def check(self):
        r = request("GET", f"{self.base_url}/models", headers=self._headers(), timeout=15).json()
        ids = [m.get("id") for m in r.get("data", [])]
        return {"ok": True, "models": ids[:50], "model_available": (self.model in ids) if ids else None}


class AnthropicLLM(LLMProvider, VisionProvider):
    def __init__(self, base_url: str, api_key: str, model: str, params: dict | None = None, timeout: float = 180):
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.params = params or {}
        self.timeout = timeout
        if not api_key:
            raise ConfigError("Anthropic requires an API key")

    def _headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    def _send(self, content, system="", temperature=0.8, max_tokens=4000):
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": temperature,
                "messages": [{"role": "user", "content": content}]}
        if system:
            body["system"] = system
        r = request("POST", f"{self.base_url}/v1/messages", headers=self._headers(), json=body, timeout=self.timeout).json()
        text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        u = r.get("usage") or {}
        return text, u.get("input_tokens", 0), u.get("output_tokens", 0)

    def complete(self, prompt, system="", json_mode=False, temperature=0.8, max_tokens=4000):
        if json_mode:
            prompt += "\n\nRespond with a single valid JSON object and nothing else."
        t, i, o = self._send(prompt, system, temperature, max_tokens)
        return LLMResult(text=t, input_tokens=i, output_tokens=o, model=self.model)

    def describe(self, image, prompt, mime="image/png"):
        content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(image).decode()}},
                   {"type": "text", "text": prompt}]
        t, i, o = self._send(content, temperature=0, max_tokens=800)
        return VisionResult(text=t, input_tokens=i, output_tokens=o, model=self.model)

    def check(self):
        r = request("GET", f"{self.base_url}/v1/models", headers=self._headers(), timeout=15).json()
        ids = [m.get("id") for m in r.get("data", [])]
        return {"ok": True, "models": ids[:50], "model_available": self.model in ids if ids else None}
