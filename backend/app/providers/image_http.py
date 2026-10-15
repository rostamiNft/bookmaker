"""Image adapters for external services. None of them is required: the built-in
procedural illustrator works without GPU or network."""
from __future__ import annotations

import base64
import copy
import json
import time
import uuid

from .base import ConfigError, ImageProvider, ImageResult, ProviderError, TransientError
from .http_util import request

OPENAI_SIZES = {
    "dall-e-3": [(1024, 1024), (1792, 1024), (1024, 1792)],
    "dall-e-2": [(1024, 1024), (512, 512)],
    "default": [(1024, 1024), (1536, 1024), (1024, 1536)],  # gpt-image-* family
}


def nearest_size(width: int, height: int, sizes: list[tuple[int, int]]) -> tuple[int, int]:
    target = width / height
    return min(sizes, key=lambda s: abs((s[0] / s[1]) - target))


class OpenAICompatImage(ImageProvider):
    supports_reference_images = True

    def __init__(self, base_url: str, api_key: str, model: str, params: dict | None = None, timeout: float = 300):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model or "gpt-image-1"
        self.params = params or {}
        self.timeout = timeout

    def _h(self):
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        sizes = self.params.get("sizes") or OPENAI_SIZES.get(self.model, OPENAI_SIZES["default"])
        w, h = nearest_size(width, height, [tuple(s) for s in sizes])
        full_prompt = prompt + (f"\nAvoid: {negative_prompt}" if negative_prompt else "")
        use_refs = bool(reference_images) and self.params.get("use_edits_for_references", True)
        if use_refs:
            files = [("image[]", (f"ref{i}.png", img, "image/png")) for i, img in enumerate(reference_images[:4])]
            data = {"model": self.model, "prompt": full_prompt, "size": f"{w}x{h}", "n": "1"}
            r = request("POST", f"{self.base_url}/images/edits", headers=self._h(), data=data, files=files, timeout=self.timeout).json()
        else:
            body = {"model": self.model, "prompt": full_prompt, "size": f"{w}x{h}", "n": 1}
            if self.params.get("response_format", "b64_json") and not self.model.startswith("gpt-image"):
                body["response_format"] = "b64_json"
            body.update(self.params.get("extra_body", {}))
            r = request("POST", f"{self.base_url}/images/generations", headers={**self._h(), "Content-Type": "application/json"},
                        json=body, timeout=self.timeout).json()
        item = (r.get("data") or [{}])[0]
        if item.get("b64_json"):
            data = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            data = request("GET", item["url"], timeout=self.timeout).content
        else:
            raise ProviderError("Image API returned no image data")
        return ImageResult(data=data, model=self.model, seed=seed, meta={"api_size": [w, h], "used_references": use_refs})

    def check(self):
        r = request("GET", f"{self.base_url}/models", headers=self._h(), timeout=15).json()
        ids = [m.get("id") for m in r.get("data", [])]
        return {"ok": True, "models": ids[:50], "model_available": (self.model in ids) if ids else None}


# A minimal SD1.5/SDXL txt2img workflow in ComfyUI "API format". Users can paste
# any exported workflow and use the same {{placeholders}} (e.g. IP-Adapter nodes
# with "{{ref_image_1}}" for character consistency).
DEFAULT_COMFY_WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 25, "cfg": 6.5, "sampler_name": "dpmpp_2m",
                                               "scheduler": "karras", "denoise": 1, "model": ["4", 0], "positive": ["6", 0],
                                               "negative": ["7", 0], "latent_image": ["5", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}", "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "bookmaker", "images": ["8", 0]}},
}


def fill_placeholders(obj, values: dict):
    if isinstance(obj, dict):
        return {k: fill_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_placeholders(v, values) for v in obj]
    if isinstance(obj, str) and obj.startswith("{{") and obj.endswith("}}") and obj[2:-2] in values:
        return values[obj[2:-2]]  # keeps numeric types for numeric placeholders
    if isinstance(obj, str) and "{{" in obj:
        for k, v in values.items():
            obj = obj.replace("{{" + k + "}}", str(v))
    return obj


class ComfyUIImage(ImageProvider):
    supports_reference_images = True

    def __init__(self, base_url: str, model: str = "", params: dict | None = None, timeout: float = 600):
        self.base_url = (base_url or "http://127.0.0.1:8188").rstrip("/")
        self.params = params or {}
        self.model = model or self.params.get("checkpoint", "")
        self.timeout = timeout
        wf = self.params.get("workflow") or DEFAULT_COMFY_WORKFLOW
        self.workflow = json.loads(wf) if isinstance(wf, str) else wf

    def _upload(self, data: bytes) -> str:
        name = f"bookmaker_ref_{uuid.uuid4().hex[:10]}.png"
        r = request("POST", f"{self.base_url}/upload/image", files={"image": (name, data, "image/png")},
                    data={"overwrite": "true"}, timeout=60).json()
        return r.get("name", name)

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        # SD models want multiples of 64
        w = max(512, int(round(width / 64)) * 64)
        h = max(512, int(round(height / 64)) * 64)
        values = {"prompt": prompt, "negative": negative_prompt, "width": w, "height": h,
                  "seed": int(seed if seed is not None else 0), "checkpoint": self.model}
        for i, img in enumerate(reference_images or [], start=1):
            values[f"ref_image_{i}"] = self._upload(img)
        wf = fill_placeholders(copy.deepcopy(self.workflow), values)
        client_id = uuid.uuid4().hex
        r = request("POST", f"{self.base_url}/prompt", json={"prompt": wf, "client_id": client_id}, timeout=60).json()
        if r.get("node_errors"):
            raise ProviderError(f"ComfyUI rejected the workflow: {json.dumps(r['node_errors'])[:300]}")
        pid = r["prompt_id"]
        deadline = time.monotonic() + self.timeout
        poll = float(self.params.get("poll_interval", 1.0))
        while time.monotonic() < deadline:
            time.sleep(poll)
            hist = request("GET", f"{self.base_url}/history/{pid}", timeout=30).json()
            entry = hist.get(pid)
            if not entry:
                continue
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise ProviderError(f"ComfyUI execution error: {json.dumps(status.get('messages', []))[:300]}")
            for node in entry.get("outputs", {}).values():
                for im in node.get("images", []):
                    data = request("GET", f"{self.base_url}/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                                          "type": im.get("type", "output")}, timeout=60).content
                    return ImageResult(data=data, model=self.model or "comfyui-workflow", seed=values["seed"],
                                       meta={"prompt_id": pid, "used_references": bool(reference_images)})
        raise TransientError("ComfyUI timed out")

    def check(self):
        r = request("GET", f"{self.base_url}/system_stats", timeout=10).json()
        dev = (r.get("devices") or [{}])[0]
        return {"ok": True, "device": dev.get("name"), "vram_free": dev.get("vram_free"), "version": r.get("system", {}).get("comfyui_version")}


class Automatic1111Image(ImageProvider):
    """AUTOMATIC1111 / Forge web UI API (/sdapi/v1). Reference images are sent as
    img2img init images with low denoise when `use_img2img_reference` is set, or to
    a ControlNet/IP-Adapter unit when `controlnet_unit` is configured."""
    supports_reference_images = True

    def __init__(self, base_url: str, model: str = "", params: dict | None = None, timeout: float = 600):
        self.base_url = (base_url or "http://127.0.0.1:7860").rstrip("/")
        self.model = model
        self.params = params or {}
        self.timeout = timeout

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        w = max(512, int(round(width / 64)) * 64)
        h = max(512, int(round(height / 64)) * 64)
        body = {"prompt": prompt, "negative_prompt": negative_prompt, "width": w, "height": h,
                "seed": int(seed if seed is not None else -1), "steps": self.params.get("steps", 25),
                "cfg_scale": self.params.get("cfg_scale", 6.5), "sampler_name": self.params.get("sampler", "DPM++ 2M Karras")}
        if self.model:
            body["override_settings"] = {"sd_model_checkpoint": self.model}
        unit = self.params.get("controlnet_unit")
        if reference_images and unit:
            u = dict(unit)
            u["image"] = base64.b64encode(reference_images[0]).decode()
            body["alwayson_scripts"] = {"controlnet": {"args": [u]}}
        r = request("POST", f"{self.base_url}/sdapi/v1/txt2img", json=body, timeout=self.timeout).json()
        imgs = r.get("images") or []
        if not imgs:
            raise ProviderError("A1111 returned no images")
        return ImageResult(data=base64.b64decode(imgs[0].split(",", 1)[-1]), model=self.model or "a1111", seed=seed,
                           meta={"used_references": bool(reference_images and unit)})

    def check(self):
        r = request("GET", f"{self.base_url}/sdapi/v1/sd-models", timeout=10).json()
        return {"ok": True, "models": [m.get("title") for m in r][:50]}
