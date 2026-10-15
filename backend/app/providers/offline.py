"""Built-in providers that need no network, GPU or API key."""
from __future__ import annotations

import io

from .base import ImageProvider, ImageResult, LLMProvider, LLMResult


class OfflineLLM(LLMProvider):
    """Marker provider: the pipeline routes story work to the offline story engine
    (story/offline_engine.py) instead of sending prompts."""

    def complete(self, prompt, system="", json_mode=False, temperature=0.8, max_tokens=4000):
        raise NotImplementedError("The offline engine does not accept free-form prompts")

    def check(self):
        from ..story.offline_engine import MAX_PAGES, MIN_PAGES
        return {"ok": True, "note": f"Built-in template story engine (no AI). Supports {MIN_PAGES}-{MAX_PAGES} pages, English and Persian."}


class ProceduralImage(ImageProvider):
    """Procedural vector illustrator. Draws from the structured scene + Character
    Bible, so characters are pixel-consistent across pages by construction."""
    supports_reference_images = False

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        from ..illustration.scene import render_reference_sheet, render_scene
        if scene is None:
            if not characters:
                raise ValueError("The procedural illustrator needs a structured scene or a character")
            # reference-sheet request (e.g. as fallback for an AI provider during the character stage)
            img, meta = render_reference_sheet(characters[0], width, height, style or "watercolor")
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return ImageResult(data=buf.getvalue(), model="procedural-v1", seed=seed, meta=meta)
        chars = {c["id"]: c for c in (characters or [])}
        variant = int(self.params.get("variant", 0))
        img, meta = render_scene(scene, chars, width, height, style or "watercolor", seed=int(seed or 0), variant=variant)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return ImageResult(data=buf.getvalue(), model="procedural-v1", seed=seed, meta=meta)

    def reference_sheet(self, character: dict, style: str = "watercolor") -> ImageResult:
        from ..illustration.scene import render_reference_sheet
        img, meta = render_reference_sheet(character, style=style)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return ImageResult(data=buf.getvalue(), model="procedural-v1", meta=meta)

    def check(self):
        return {"ok": True, "note": "Built-in procedural illustrator (no AI, CPU only)."}
