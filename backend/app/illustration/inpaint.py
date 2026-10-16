"""Region repair (inpainting) of illustrations.

The user paints a mask over a defect (stray letters, an extra hand, a smudge).
Only that region is replaced:

* ``classical`` - built-in, CPU only: OpenCV Telea / Navier-Stokes inpainting
  (Apache-2.0). Good for removing small objects and artefacts, it cannot draw
  new content.
* an AI provider (OpenAI-compatible ``/images/edits`` with mask, AUTOMATIC1111 /
  Forge / stable-diffusion.cpp ``img2img`` with mask, a ComfyUI inpaint workflow,
  or an IOPaint/LaMa server) - can redraw the region from a prompt.

Whatever the engine returns, the result is composited back onto the original
through a feathered mask, so pixels outside the painted region are guaranteed to
stay unchanged (verified by ``changed_outside_mask``)."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageChops, ImageFilter

MAX_COVERAGE = 0.6  # larger masks: regenerate the whole image instead


class MaskError(ValueError):
    pass


def load_mask(mask_bytes: bytes, size: tuple[int, int]) -> Image.Image:
    """Normalise a user mask to an L image of `size`: 255 = repair, 0 = keep.
    Accepts white-on-black masks and RGBA canvases (painted = opaque pixels)."""
    try:
        m = Image.open(io.BytesIO(mask_bytes))
        m.load()
    except Exception as e:
        raise MaskError(f"Mask is not a valid image: {e}") from e
    if m.mode in ("RGBA", "LA") or (m.mode == "P" and "transparency" in m.info):
        m = m.convert("RGBA")
        alpha = m.getchannel("A")
        lum = m.convert("L")
        # canvas masks: painted strokes are opaque; a fully opaque image is read by luminance
        mask = alpha if alpha.getextrema()[0] < 250 else lum
    else:
        mask = m.convert("L")
    mask = mask.resize(size, Image.NEAREST).point(lambda v: 255 if v >= 128 else 0)
    return mask


def coverage(mask: Image.Image) -> float:
    a = np.asarray(mask, dtype=np.uint8)
    return float((a > 0).mean())


def validate_mask(mask: Image.Image) -> float:
    cov = coverage(mask)
    if cov <= 0.0005:
        raise MaskError("The mask is empty: paint over the area that should be repaired")
    if cov > MAX_COVERAGE:
        raise MaskError(f"The mask covers {cov:.0%} of the image; regenerate the whole illustration instead")
    return cov


def grow(mask: Image.Image, px: int) -> Image.Image:
    if px <= 0:
        return mask
    return mask.filter(ImageFilter.MaxFilter(px * 2 + 1))


def composite(original: Image.Image, repaired: Image.Image, mask: Image.Image, feather: int = 4) -> Image.Image:
    """Blend `repaired` into `original` only inside the mask (feathered edge inside the mask)."""
    original = original.convert("RGB")
    repaired = repaired.convert("RGB")
    if repaired.size != original.size:
        repaired = repaired.resize(original.size, Image.LANCZOS)
    if feather > 0:
        # soft edge inside the mask only: blur, boost (thin strokes stay fully repaired), then hard zero outside
        soft = mask.filter(ImageFilter.GaussianBlur(feather)).point(lambda v: min(255, v * 2))
        soft = ImageChops.multiply(soft, mask)
    else:
        soft = mask
    return Image.composite(repaired, original, soft)


def changed_outside_mask(before: Image.Image, after: Image.Image, mask: Image.Image, tol: int = 2) -> float:
    """Fraction of pixels outside the mask whose value changed by more than `tol`."""
    a = np.asarray(before.convert("RGB"), dtype=np.int16)
    b = np.asarray(after.convert("RGB"), dtype=np.int16)
    m = np.asarray(mask, dtype=np.uint8) == 0
    if not m.any():
        return 0.0
    diff = np.abs(a - b).max(axis=2) > tol
    return float((diff & m).sum() / m.sum())


def classical(image: Image.Image, mask: Image.Image, method: str = "telea", radius: int = 0) -> Image.Image:
    import cv2
    rgb = np.asarray(image.convert("RGB"))
    m = np.asarray(grow(mask, 2), dtype=np.uint8)
    r = radius or max(3, int(round(min(image.size) * 0.006)))
    flag = cv2.INPAINT_NS if method == "ns" else cv2.INPAINT_TELEA
    out = cv2.inpaint(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), m, r, flag)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def mask_png(mask: Image.Image, convention: str = "white") -> bytes:
    """Encode a mask for an external API. 'white': white = repaint (SD/A1111/IOPaint/ComfyUI).
    'alpha': fully transparent = repaint, opaque elsewhere (OpenAI images/edits)."""
    if convention == "alpha":
        rgba = Image.new("RGBA", mask.size, (0, 0, 0, 255))
        rgba.putalpha(Image.eval(mask, lambda v: 255 - v))
        return to_png(rgba)
    return to_png(mask.convert("L"))


def inpaint(image_bytes: bytes, mask_bytes: bytes, engine_fn=None, method: str = "telea") -> tuple[bytes, dict]:
    """Run one repair. `engine_fn(image_png, mask_L) -> bytes` calls an AI provider; None = classical.
    Returns (png bytes, report)."""
    original = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    mask = load_mask(mask_bytes, original.size)
    cov = validate_mask(mask)
    if engine_fn is None:
        repaired = classical(original, mask, method)
        engine = f"opencv-{method}"
    else:
        data = engine_fn(to_png(original), mask)
        repaired = Image.open(io.BytesIO(data)).convert("RGB")
        engine = "provider"
    result = composite(original, repaired, mask)
    outside = changed_outside_mask(original, result, mask)
    inside_delta = float(np.abs(np.asarray(original, dtype=np.int16) - np.asarray(result, dtype=np.int16)).max(axis=2)[np.asarray(mask) > 0].mean())
    report = {"engine": engine, "mask_coverage": round(cov, 4), "changed_outside_mask": round(outside, 6),
              "mean_change_inside_mask": round(inside_delta, 2), "size": list(original.size)}
    return to_png(result), report
