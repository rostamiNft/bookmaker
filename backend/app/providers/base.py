"""Provider abstraction: every model backend implements one of these small
interfaces, so the pipeline never depends on a specific vendor."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    retryable = False

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class TransientError(ProviderError):
    retryable = True


class RateLimitError(TransientError):
    pass


class AuthError(ProviderError):
    pass


class ConfigError(ProviderError):
    pass


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: Any = None


@dataclass
class ImageResult:
    data: bytes
    mime: str = "image/png"
    model: str = ""
    seed: int | None = None
    meta: dict = field(default_factory=dict)  # e.g. character bounding boxes for QA


@dataclass
class VisionResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMProvider:
    kind = "llm"
    supports_json = True

    def complete(self, prompt: str, system: str = "", json_mode: bool = False, temperature: float = 0.8,
                 max_tokens: int = 4000) -> LLMResult:
        raise NotImplementedError

    def check(self) -> dict:
        raise NotImplementedError


class ImageProvider:
    kind = "image"
    supports_reference_images = False

    def generate(self, prompt: str, negative_prompt: str, width: int, height: int, seed: int | None = None,
                 reference_images: list[bytes] | None = None, scene: dict | None = None,
                 characters: list[dict] | None = None, style: str = "") -> ImageResult:
        raise NotImplementedError

    def check(self) -> dict:
        raise NotImplementedError


class VisionProvider:
    kind = "vision"

    def describe(self, image: bytes, prompt: str, mime: str = "image/png") -> VisionResult:
        raise NotImplementedError

    def check(self) -> dict:
        raise NotImplementedError
