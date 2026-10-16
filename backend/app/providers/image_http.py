"""Image adapters for external services. None of them is required: the built-in
procedural illustrator works without GPU or network."""
from __future__ import annotations

import base64
import copy
import json
import time
import uuid

from .base import ConfigError, ImageProvider, ImageResult, ProviderError, TransientError


def _b64img(item: str) -> bytes:
    return base64.b64decode(item.split(",", 1)[-1])
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

    supports_inpainting = True

    def mask_convention(self) -> str:
        # OpenAI: transparent = edit. stable-diffusion.cpp / LocalAI style servers: white = edit.
        return self.params.get("mask_convention", "alpha")

    def inpaint(self, image, mask_png, prompt, negative_prompt="", seed=None):
        from PIL import Image
        import io
        w, h = Image.open(io.BytesIO(image)).size
        sizes = self.params.get("sizes") or OPENAI_SIZES.get(self.model, OPENAI_SIZES["default"])
        aw, ah = nearest_size(w, h, [tuple(s) for s in sizes])
        files = [("image[]", ("image.png", image, "image/png")), ("mask", ("mask.png", mask_png, "image/png"))]
        data = {"model": self.model, "prompt": prompt + (f"\nAvoid: {negative_prompt}" if negative_prompt else ""), "size": f"{aw}x{ah}", "n": "1"}
        r = request("POST", f"{self.base_url}/images/edits", headers=self._h(), data=data, files=files, timeout=self.timeout).json()
        item = (r.get("data") or [{}])[0]
        if item.get("b64_json"):
            out = base64.b64decode(item["b64_json"])
        elif item.get("url"):
            out = request("GET", item["url"], timeout=self.timeout).content
        else:
            raise ProviderError("Image edit API returned no image data")
        return ImageResult(data=out, model=self.model, seed=seed, meta={"api_size": [aw, ah], "inpaint": True})

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


# Inpainting with standard ComfyUI nodes. {{image}} and {{mask}} are uploaded file names
# (mask: white = repaint). Users can paste any workflow using the same placeholders.
DEFAULT_COMFY_INPAINT_WORKFLOW = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{checkpoint}}"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "{{image}}"}},
    "3": {"class_type": "LoadImage", "inputs": {"image": "{{mask}}"}},
    "4": {"class_type": "ImageToMask", "inputs": {"image": ["3", 0], "channel": "red"}},
    "5": {"class_type": "VAEEncodeForInpaint", "inputs": {"pixels": ["2", 0], "vae": ["1", 2], "mask": ["4", 0], "grow_mask_by": 6}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["1", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["1", 1]}},
    "8": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 25, "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras",
                                               "denoise": 1, "model": ["1", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
    "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
    "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": "bookmaker_inpaint", "images": ["9", 0]}},
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

    supports_inpainting = True

    def inpaint(self, image, mask_png, prompt, negative_prompt="", seed=None):
        wf = self.params.get("inpaint_workflow") or DEFAULT_COMFY_INPAINT_WORKFLOW
        wf = json.loads(wf) if isinstance(wf, str) else wf
        values = {"prompt": prompt, "negative": negative_prompt, "seed": int(seed or 0), "checkpoint": self.params.get("inpaint_checkpoint") or self.model,
                  "image": self._upload(image), "mask": self._upload(mask_png)}
        res = self._run(fill_placeholders(copy.deepcopy(wf), values), values["seed"], False)
        res.meta["inpaint"] = True
        return res

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        # SD models want multiples of 64
        w = max(512, int(round(width / 64)) * 64)
        h = max(512, int(round(height / 64)) * 64)
        values = {"prompt": prompt, "negative": negative_prompt, "width": w, "height": h,
                  "seed": int(seed if seed is not None else 0), "checkpoint": self.model}
        for i, img in enumerate(reference_images or [], start=1):
            values[f"ref_image_{i}"] = self._upload(img)
        wf = fill_placeholders(copy.deepcopy(self.workflow), values)
        return self._run(wf, values["seed"], bool(reference_images))

    def _run(self, wf: dict, seed: int, used_refs: bool) -> ImageResult:
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
                    return ImageResult(data=data, model=self.model or "comfyui-workflow", seed=seed,
                                       meta={"prompt_id": pid, "used_references": used_refs})
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
        endpoint, used = "txt2img", False
        if reference_images and unit:
            u = dict(unit)
            u["image"] = base64.b64encode(reference_images[0]).decode()
            body["alwayson_scripts"] = {"controlnet": {"args": [u]}}
            used = True
        elif reference_images and self.params.get("extra_images_reference"):
            # stable-diffusion.cpp server: reference images for edit-capable models (Flux Kontext, Qwen-Image-Edit ...)
            body["extra_images"] = [base64.b64encode(x).decode() for x in reference_images[:4]]
            used = True
        elif reference_images and self.params.get("use_img2img_reference"):
            endpoint = "img2img"
            body["init_images"] = [base64.b64encode(reference_images[0]).decode()]
            body["denoising_strength"] = float(self.params.get("reference_denoise", 0.75))
            used = True
        r = request("POST", f"{self.base_url}/sdapi/v1/{endpoint}", json=body, timeout=self.timeout).json()
        imgs = r.get("images") or []
        if not imgs:
            raise ProviderError("A1111 returned no images")
        return ImageResult(data=_b64img(imgs[0]), model=self.model or "a1111", seed=seed, meta={"used_references": used, "endpoint": endpoint})

    supports_inpainting = True

    def inpaint(self, image, mask_png, prompt, negative_prompt="", seed=None):
        from PIL import Image
        import io
        w, h = Image.open(io.BytesIO(image)).size
        body = {"prompt": prompt, "negative_prompt": negative_prompt, "init_images": [base64.b64encode(image).decode()],
                "mask": base64.b64encode(mask_png).decode(), "width": w, "height": h, "seed": int(seed if seed is not None else -1),
                "denoising_strength": float(self.params.get("inpaint_denoise", 0.75)), "inpainting_fill": 1, "inpaint_full_res": True,
                "mask_blur": 4, "steps": self.params.get("steps", 25), "cfg_scale": self.params.get("cfg_scale", 6.5),
                "sampler_name": self.params.get("sampler", "DPM++ 2M Karras")}
        if self.model:
            body["override_settings"] = {"sd_model_checkpoint": self.model}
        r = request("POST", f"{self.base_url}/sdapi/v1/img2img", json=body, timeout=self.timeout).json()
        imgs = r.get("images") or []
        if not imgs:
            raise ProviderError("img2img returned no images")
        return ImageResult(data=_b64img(imgs[0]), model=self.model or "a1111", seed=seed, meta={"inpaint": True})

    def check(self):
        r = request("GET", f"{self.base_url}/sdapi/v1/sd-models", timeout=10).json()
        return {"ok": True, "models": [m.get("title") for m in r][:50]}


class FastSDCPUImage(ImageProvider):
    """FastSD CPU (rupeshs/fastsdcpu, MIT) REST API: `python src/app.py --api`.
    Latent-consistency / SD-Turbo models generate a 512px image in seconds on a CPU,
    so AI illustrations are possible without GPU or paid API (quality is lower)."""
    supports_reference_images = True

    def __init__(self, base_url: str, model: str = "", params: dict | None = None, timeout: float = 900):
        self.base_url = (base_url or "http://127.0.0.1:8000").rstrip("/")
        self.model = model
        self.params = params or {}
        self.timeout = timeout

    def _body(self, prompt, negative_prompt, width, height, seed):
        m = 64 if self.params.get("snap_to_64", True) else 8
        w = max(256, int(round(width / m)) * m)
        h = max(256, int(round(height / m)) * m)
        body = {"prompt": prompt, "negative_prompt": negative_prompt, "image_width": w, "image_height": h,
                "inference_steps": int(self.params.get("steps", 4)), "guidance_scale": float(self.params.get("guidance_scale", 1.0)),
                "number_of_images": 1, "seed": int(seed or 0), "use_seed": seed is not None,
                "use_openvino": bool(self.params.get("use_openvino", False)), "use_tiny_auto_encoder": bool(self.params.get("tiny_vae", False)),
                "diffusion_task": "text_to_image", "use_safety_checker": bool(self.params.get("safety_checker", False))}
        if self.model:
            body["openvino_lcm_model_id" if body["use_openvino"] else "lcm_model_id"] = self.model
        return body

    def generate(self, prompt, negative_prompt, width, height, seed=None, reference_images=None, scene=None, characters=None, style=""):
        # long side capped (CPU time grows quadratically); the pipeline upscales/crops to the page aspect
        cap = int(self.params.get("max_side", 768))
        scale = min(1.0, cap / max(width, height))
        body = self._body(prompt, negative_prompt, int(width * scale), int(height * scale), seed)
        used = False
        if reference_images and self.params.get("use_img2img_reference"):
            body.update({"diffusion_task": "image_to_image", "init_image": base64.b64encode(reference_images[0]).decode(),
                         "strength": float(self.params.get("reference_strength", 0.7))})
            used = True
        r = request("POST", f"{self.base_url}/api/generate", json=body, timeout=self.timeout).json()
        if r.get("error"):
            raise ProviderError(f"FastSD CPU error: {str(r['error'])[:300]}")
        imgs = r.get("images") or []
        if not imgs:
            raise ProviderError("FastSD CPU returned no images")
        return ImageResult(data=_b64img(imgs[0]), model=self.model or "fastsdcpu", seed=seed,
                           meta={"latency_s": r.get("latency"), "used_references": used, "size": [body["image_width"], body["image_height"]]})

    def check(self):
        info = request("GET", f"{self.base_url}/api/info", timeout=10).json()
        try:
            models = request("GET", f"{self.base_url}/api/models", timeout=10).json()
        except ProviderError:
            models = {}
        return {"ok": True, "device": info.get("device_type") or info.get("device_name"), "info": {k: info[k] for k in list(info)[:6]},
                "models": (models.get("lcm_models") or [])[:20]}


class IOPaintInpaint(ImageProvider):
    """IOPaint (Sanster/IOPaint, Apache-2.0; LaMa model Apache-2.0) server:
    `iopaint start --model=lama --device=cpu`. Object removal / region repair on CPU.
    It cannot create illustrations, only repair them."""
    kind = "inpaint"
    supports_generation = False
    supports_inpainting = True

    def __init__(self, base_url: str, model: str = "", params: dict | None = None, timeout: float = 300):
        self.base_url = (base_url or "http://127.0.0.1:8080").rstrip("/")
        self.model = model
        self.params = params or {}
        self.timeout = timeout

    def generate(self, *a, **kw):
        raise ConfigError("IOPaint only repairs regions of existing images; choose another image provider for generation")

    def inpaint(self, image, mask_png, prompt, negative_prompt="", seed=None):
        body = {"image": base64.b64encode(image).decode(), "mask": base64.b64encode(mask_png).decode(), "prompt": prompt,
                "negative_prompt": negative_prompt, "sd_seed": int(seed if seed is not None else 42),
                "hd_strategy": self.params.get("hd_strategy", "Crop")}
        r = request("POST", f"{self.base_url}/api/v1/inpaint", json=body, timeout=self.timeout)
        if not r.content or not r.headers.get("content-type", "").startswith("image/"):
            raise ProviderError("IOPaint returned no image")
        return ImageResult(data=r.content, mime=r.headers.get("content-type"), model=self.model or "iopaint", seed=seed, meta={"inpaint": True})

    def check(self):
        r = request("GET", f"{self.base_url}/api/v1/server-config", timeout=10).json()
        cur = request("GET", f"{self.base_url}/api/v1/model", timeout=10).json()
        return {"ok": True, "model": cur.get("name"), "plugins": [p.get("name") for p in r.get("plugins", [])][:10]}
