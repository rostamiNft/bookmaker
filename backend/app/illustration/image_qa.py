"""Image quality checks that do not trust the generator.

Checks: decodable/non-empty file, resolution, aspect ratio, blank or flat image,
near-duplicates across pages (perceptual dHash), stray text (OCR, optional),
character presence and colour consistency against the Character Bible.
Optional vision-model review is done in pipeline (if a vision provider is set)."""
from __future__ import annotations

import io
import logging
import math
import threading

from PIL import Image, ImageStat

from ..config import settings
from .palette import hex2rgb

log = logging.getLogger(__name__)

MIN_SHORT_SIDE = 512


def dhash(img: Image.Image, size: int = 8) -> int:
    g = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(g.get_flattened_data()) if hasattr(g, "get_flattened_data") else list(g.getdata())
    bits = 0
    for r in range(size):
        for c in range(size):
            bits = (bits << 1) | (1 if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


_ocr = None
_ocr_lock = threading.Lock()


def get_ocr():
    global _ocr
    if not settings.enable_ocr:
        return None
    with _ocr_lock:
        if _ocr is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                _ocr = RapidOCR()
            except Exception as e:  # optional dependency
                log.warning("OCR unavailable: %s", e)
                _ocr = False
    return _ocr or None


def detect_text(img: Image.Image) -> list[str] | None:
    """Return text fragments found in the image, or None if OCR is unavailable."""
    eng = get_ocr()
    if eng is None:
        return None
    import numpy as np
    small = img.convert("RGB")
    if max(small.size) > 1024:
        small.thumbnail((1024, 1024))
    with _ocr_lock:
        res, _ = eng(np.array(small))
    found = []
    for item in res or []:
        txt, score = item[1], float(item[2])
        # ignore single characters / low confidence blobs (texture noise)
        letters = sum(1 for ch in txt if ch.isalpha())
        if score >= 0.75 and len(txt.strip()) >= 3 and letters >= 2:
            found.append(txt.strip())
    return found


def color_distance(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def palette_coverage(img: Image.Image, box, palette: list[str], tol: float = 60.0) -> float:
    """Fraction of expected signature colours that are present in the region."""
    if not palette:
        return 1.0
    region = img.convert("RGB").crop(box) if box else img.convert("RGB")
    region = region.resize((max(8, region.width // 4), max(8, region.height // 4)))
    q = region.quantize(colors=24).convert("RGB")
    colors = [c for _, c in (q.getcolors(4096) or [])]
    hits = 0
    for hx in palette:
        target = hex2rgb(hx)
        if any(color_distance(target, c) < tol for c in colors):
            hits += 1
    return hits / len(palette)


def check_image(data: bytes | None, target_aspect: float, *, expected_chars: list[str] | None = None,
                meta: dict | None = None, palettes: dict[str, list[str]] | None = None,
                time_of_day: str = "day", ocr: bool = True) -> dict:
    issues: list[dict] = []

    def add(code, sev, msg):
        issues.append({"code": code, "severity": sev, "message": msg, "stage": "image"})

    if not data:
        add("missing", "error", "No image data / file is empty (0 bytes).")
        return {"ok": False, "issues": issues, "metrics": {}}
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        add("corrupt", "error", f"Image cannot be decoded: {e.__class__.__name__}")
        return {"ok": False, "issues": issues, "metrics": {}}
    w, h = img.size
    metrics = {"width": w, "height": h, "format": img.format, "bytes": len(data)}
    if min(w, h) < MIN_SHORT_SIDE:
        add("low_resolution", "error", f"Resolution {w}x{h} is below {MIN_SHORT_SIDE}px on the short side.")
    aspect = w / h
    metrics["aspect"] = round(aspect, 4)
    if abs(aspect - target_aspect) / target_aspect > 0.02:
        add("aspect_mismatch", "error", f"Aspect {aspect:.3f} differs from target {target_aspect:.3f}.")
    stat = ImageStat.Stat(img.convert("L"))
    std = stat.stddev[0]
    metrics["luma_std"] = round(std, 2)
    metrics["luma_mean"] = round(stat.mean[0], 2)
    if std < 12:
        add("blank", "error", f"Image is nearly uniform (std {std:.1f}); likely blank or failed render.")
    if stat.mean[0] < 12 or stat.mean[0] > 248:
        add("exposure", "error", f"Image is almost completely {'black' if stat.mean[0] < 12 else 'white'}.")
    metrics["dhash"] = format(dhash(img), "016x")
    # stray text
    if ocr:
        txt = detect_text(img)
        if txt is None:
            metrics["ocr"] = "unavailable"
        else:
            metrics["ocr_text"] = txt
            if txt:
                add("text_in_image", "error", f"Unwanted text detected in illustration: {txt[:3]}")
    # character presence + consistency (needs layout metadata from the renderer)
    boxes = (meta or {}).get("character_boxes")
    if expected_chars:
        if boxes is not None:
            for cid in expected_chars:
                if cid not in boxes:
                    add("character_missing", "error", f"Character '{cid}' is not in the illustration.")
                    continue
                b = boxes[cid]
                bw, bh = b[2] - b[0], b[3] - b[1]
                if bw < w * 0.03 or bh < h * 0.06:
                    add("character_too_small", "warning", f"Character '{cid}' is very small.")
                if b[0] < -2 or b[1] < -2 or b[2] > w + 2 or b[3] > h + 2:
                    add("character_cropped", "warning", f"Character '{cid}' extends outside the frame.")
                pal = (palettes or {}).get(cid, [])
                tol = 60 if time_of_day == "day" else 95  # night/sunset lighting shifts colours
                cov = palette_coverage(img, [max(0, b[0]), max(0, b[1]), min(w, b[2]), min(h, b[3])], pal, tol)
                metrics.setdefault("palette_coverage", {})[cid] = round(cov, 2)
                if cov < 0.5:
                    add("character_inconsistent", "error", f"'{cid}' signature colours not found (coverage {cov:.0%}).")
            extra = [k for k in boxes if k not in expected_chars]
            if extra:
                add("unexpected_character", "error", f"Unexpected characters drawn: {extra}")
        elif palettes:
            # AI images: no boxes -> weaker whole-image colour heuristic, reported as warning
            for cid in expected_chars:
                cov = palette_coverage(img, None, palettes.get(cid, []), 70)
                metrics.setdefault("palette_coverage", {})[cid] = round(cov, 2)
                if cov < 0.34:
                    add("character_colours_weak", "warning", f"Few of '{cid}' signature colours found (heuristic, {cov:.0%}); review recommended.")
    errors = [i for i in issues if i["severity"] == "error"]
    return {"ok": not errors, "issues": issues, "metrics": metrics}


def find_duplicates(hashes: dict[int, str], threshold: int = 5) -> list[tuple[int, int, int]]:
    """Return (page_a, page_b, distance) for near-identical illustrations."""
    items = sorted(hashes.items())
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = int(items[i][1], 16), int(items[j][1], 16)
            dist = hamming(a, b)
            if dist <= threshold:
                out.append((items[i][0], items[j][0], dist))
    return out


def fit_to_aspect(data: bytes, target_aspect: float) -> tuple[bytes, bool]:
    """Centre-crop (never stretch) an image to the target aspect ratio.
    Returns (bytes, changed). Used when an API only supports fixed sizes."""
    img = Image.open(io.BytesIO(data))
    img.load()
    w, h = img.size
    a = w / h
    if abs(a - target_aspect) / target_aspect <= 0.01:
        return data, False
    if a > target_aspect:
        nw = int(round(h * target_aspect))
        x0 = (w - nw) // 2
        img = img.crop((x0, 0, x0 + nw, h))
    else:
        nh = int(round(w / target_aspect))
        y0 = (h - nh) // 2
        img = img.crop((0, y0, w, y0 + nh))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), True
