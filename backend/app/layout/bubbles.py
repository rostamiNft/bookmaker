"""Comic-style speech balloons drawn *inside* the artwork.

The picture fills the whole page and the page text is split into dialogue-sized
chunks; every chunk is typeset in a vector balloon with a tail pointing at the
character that is speaking (or, for AI pictures without character boxes, at the
busiest region of the image).

Placement is an explicit optimisation, not a guess:

* "protected" boxes are built from the renderer's ``character_boxes`` (exact for
  procedurally drawn art) or, when the picture came from an image model and no
  boxes exist, from an edge-density saliency map -- documented as a heuristic,
  it is *not* face detection;
* an edge-density map keeps balloons off busy areas so the art stays readable;
* candidate positions are scored against those maps plus balloon-balloon
  overlap, distance to the speaker and the page-number zone;
* the result carries its own measurements (face coverage, busy score, whether
  every text line fits the balloon outline) which ``verify_layout`` turns into
  pass/fail issues.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

from PIL import Image, ImageFilter

GRID = 48                     # edge-density map resolution
MAX_BALLOONS = 3
_CACHE: dict = {}

_SENTENCE = re.compile(r'(?<=[.!?؟!۔؛»"])\s+')


# ----------------------------------------------------------------- image analysis
def _analyse(path: str, char_boxes: dict | None) -> dict:
    with Image.open(path) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        small = im.resize((GRID, GRID), Image.BILINEAR)
        edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
        vals = list(edges.getdata())
        mx = max(vals) or 1
        busy = [min(1.0, v / mx) for v in vals]
    faces, speakers = [], []
    if char_boxes:
        for _cid, b in char_boxes.items():
            if not b or len(b) != 4:
                continue
            x0, y0, x1, y1 = [float(v) for v in b]
            bh = max(1.0, y1 - y0)
            cx = (x0 + x1) / 2
            half = (x1 - x0) * 0.34
            hy = y0 + bh * 0.44                       # head (face) region
            faces.append((max(0.0, cx - half) / iw, max(0.0, y0) / ih,
                          min(float(iw), cx + half) / iw, min(float(ih), hy) / ih))
            speakers.append((cx / iw, min(float(ih), hy + bh * 0.04) / ih))
    if not speakers:
        # heuristic: the strongest edge clusters in the upper two thirds are the
        # most likely place for the subject. Two peaks, well separated.
        cand = sorted(((busy[gy * GRID + gx], gx, gy)
                       for gy in range(int(GRID * 0.75)) for gx in range(GRID)), reverse=True)
        picked: list[tuple[int, int]] = []
        for v, gx, gy in cand[:GRID * GRID]:
            if v < 0.25:
                break
            if all(max(abs(gx - px), abs(gy - py)) > GRID * 0.22 for px, py in picked):
                picked.append((gx, gy))
            if len(picked) == 2:
                break
        if not picked:
            picked = [(GRID // 2, int(GRID * 0.45))]
        for gx, gy in picked:
            cx, cy = (gx + 0.5) / GRID, (gy + 0.5) / GRID
            speakers.append((cx, cy))
            faces.append((max(0.0, cx - 0.11), max(0.0, cy - 0.13), min(1.0, cx + 0.11), min(1.0, cy + 0.09)))
    return {"busy": busy, "faces": faces, "speakers": speakers, "size": (iw, ih)}


def analyse_image(path: str, char_boxes: dict | None = None) -> dict:
    """/analyse/ an illustration: edge-density map + protected (face) boxes + speaker points.
    All coordinates are normalised (0..1, y measured from the *top*). Cached per file."""
    if not path or not os.path.exists(path):
        return {"busy": [0.0] * (GRID * GRID), "faces": [], "speakers": [(0.5, 0.45)], "size": (1, 1)}
    try:
        mt = os.path.getmtime(path)
    except OSError:
        mt = 0
    key = (path, mt, tuple(sorted((k, tuple(v)) for k, v in (char_boxes or {}).items() if v)))
    if key not in _CACHE:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = _analyse(path, char_boxes)
    return _CACHE[key]


def _inter(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def busy_score(busy: list[float], box) -> float:
    x0, y0, x1, y1 = box
    gx0, gx1 = max(0, int(x0 * GRID)), min(GRID, int(x1 * GRID) + 1)
    gy0, gy1 = max(0, int(y0 * GRID)), min(GRID, int(y1 * GRID) + 1)
    if gx1 <= gx0 or gy1 <= gy0:
        return 0.0
    tot = n = 0
    for gy in range(gy0, gy1):
        row = gy * GRID
        for gx in range(gx0, gx1):
            tot += busy[row + gx]
            n += 1
    return tot / max(1, n)


# ----------------------------------------------------------------- text splitting
def split_speech(text: str, max_n: int = MAX_BALLOONS, min_len: int = 14) -> list[str]:
    """Split page text into dialogue chunks at sentence boundaries (max ``max_n``)."""
    parts = [p.strip() for p in _SENTENCE.split(text.strip()) if p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and (len(merged[-1]) < min_len or len(p) < min_len):
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    while len(merged) > max_n:  # merge the shortest neighbouring pair
        i = min(range(len(merged) - 1), key=lambda k: len(merged[k]) + len(merged[k + 1]))
        merged[i:i + 2] = [f"{merged[i]} {merged[i + 1]}"]
    return merged or [text.strip()]


# ----------------------------------------------------------------- balloon geometry
def width_at(w: float, h: float, r: float, pad: float, leading: float):
    """Available text width per line inside a rounded-rect balloon (line index -> pt)."""
    def f(i: int, n: int = 1) -> float:
        dy = abs(i - (n - 1) / 2) * leading + min(leading * 0.35, r * 0.5)
        over = dy - (h / 2 - r)
        if over <= 0:
            return w - 2 * pad
        t = min(r, over)
        inset = r - math.sqrt(max(0.0, r * r - t * t))
        return max(w * 0.3, w - 2 * pad - 2 * inset)
    return f


def fit_balloon(text: str, chain: list[str], size: float, rtl: bool, opts, max_w: float, max_h: float,
                leading_factor: float):
    """Typeset ``text`` and grow a balloon around it. -> (paragraph, w, h, r, pad) or None."""
    from .text import wrap
    leading = size * leading_factor
    pad = size * 0.85
    para = wrap(text, chain, size, max_w, rtl, leading_factor, opts)
    if not para.lines:
        return None
    w = min(max_w, max(l.width for l in para.lines) + 2 * pad + size)
    n = len(para.lines)
    for _ in range(6):
        h = n * leading + 2 * pad
        if h > max_h or w > max_w:
            return None
        r = min(w, h) * 0.42
        fn = width_at(w, h, r, pad, leading)
        para = wrap(text, chain, size, (lambda i: fn(i, n)), rtl, leading_factor, opts)
        h2 = len(para.lines) * leading + 2 * pad
        if len(para.lines) == n and abs(h2 - h) < 0.5:
            break
        n, h = len(para.lines), h2
    h = len(para.lines) * leading + 2 * pad
    if h > max_h:
        return None
    r = min(w, h) * 0.42
    # final safety: no line may stick out of the outline
    fn = width_at(w, h, r, pad, leading)
    for i, ln in enumerate(para.lines):
        if ln.width > fn(i, len(para.lines)) + 0.6:
            return None
    return para, w, h, r, pad


def tail_points(box, speaker, faces, W: float, H: float, min_len: float = 12.0, keep: float = 4.0) -> tuple[list, float]:
    """Triangle (3 points, PDF coordinates) from the balloon edge towards the speaker.

    Tries the direct line first, then progressively rotated directions, so the tail is
    always visible even when the speaker sits right next to the balloon; the tip is kept
    out of protected (face) boxes."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy = speaker[0] - cx, speaker[1] - cy
    if math.hypot(dx, dy) < 1.0:
        dx, dy = 0.0, -1.0
    ang0 = math.atan2(dy, dx)
    dist0 = math.hypot(dx, dy)
    face_boxes = [(f[0] * W, (1 - f[3]) * H, f[2] * W, (1 - f[1]) * H) for f in faces]
    best = None
    for delta in (0, 22, -22, 45, -45, 70, -70, 95, -95, 140, -140, 180):
        a = math.radians(delta)
        ux, uy = math.cos(ang0 + a), math.sin(ang0 + a)
        tx = (w / 2) / abs(ux) if abs(ux) > 1e-6 else float("inf")
        ty = (h / 2) / abs(uy) if abs(uy) > 1e-6 else float("inf")
        t = min(tx, ty)
        bx, by = cx + ux * t, cy + uy * t
        # distance from the outline point to the speaker along this direction
        dvec = (speaker[0] - bx, speaker[1] - by)
        along = dvec[0] * ux + dvec[1] * uy
        length = max(min_len, min(max(along * 0.55, 0.0), min(w, h) * 0.62)) if along > 0 else min_len
        # shrink until the tip is on the page and out of the faces - but never below
        # min_len: a short tail is worse than one that grazes a face box, because a
        # balloon without a visible tail reads as a caption box instead of speech.
        for _ in range(10):
            tipx, tipy = bx + ux * length, by + uy * length
            blocked = not (4.0 <= tipx <= W - 4.0 and 4.0 <= tipy <= H - 4.0) or \
                any(fb[0] - keep <= tipx <= fb[2] + keep and fb[1] - keep <= tipy <= fb[3] + keep for fb in face_boxes)
            if not blocked or length <= min_len:
                break
            length = max(min_len, length * 0.8)
        tipx, tipy = min(max(bx + ux * length, 4.0), W - 4.0), min(max(by + uy * length, 4.0), H - 4.0)
        clean = not any(fb[0] - keep <= tipx <= fb[2] + keep and fb[1] - keep <= tipy <= fb[3] + keep for fb in face_boxes)
        half = min(w, h) * 0.17
        px, py = -uy, ux
        base_inset = min(w, h) * 0.16
        p1 = (bx - ux * base_inset + px * half, by - uy * base_inset + py * half)
        p2 = (bx - ux * base_inset - px * half, by - uy * base_inset - py * half)
        cand = ([p1, p2, (tipx, tipy)], length, clean)
        if clean:
            return cand[0], cand[1]
        if best is None:
            best = cand
    return best[0], best[1]


# ----------------------------------------------------------------- page planning
@dataclass
class Balloon:
    box: tuple                       # x, y, w, h (PDF coordinates, y from bottom)
    radius: float
    pad: float
    tail: list
    text: str
    face_cover: float = 0.0          # 0..1 worst protected-box coverage
    busy: float = 0.0
    speaker: tuple = (0.0, 0.0)
    tail_len: float = 0.0


def plan_balloons(*, W: float, H: float, margin: float, footer: float, chunks: list[str], info: dict,
                  rtl: bool, chain: list[str], opts, size_hi: float, size_lo: float,
                  leading_factor: float) -> list[Balloon] | None:
    """Choose a balloon per chunk. Returns None when the text cannot be ballooned
    (caller then falls back to the regular panel layout)."""
    faces = info["faces"]
    speakers = info["speakers"]
    busy = info["busy"]
    mx, my = margin / W, margin / H
    footer_box = (0.34, 1.0 - (footer + 6) / H, 0.66, 1.0)
    out: list[Balloon] = []
    placed: list[tuple] = []
    for i, chunk in enumerate(chunks):
        speaker = speakers[i % len(speakers)]
        sp_pdf = (speaker[0] * W, (1 - speaker[1]) * H)
        fitted = None
        size = size_hi
        while size >= size_lo - 1e-6:
            fitted = fit_balloon(chunk, chain, size, rtl, opts, max_w=W * 0.52, max_h=H * 0.30,
                                 leading_factor=leading_factor)
            if fitted:
                break
            size -= 0.5
        if not fitted:
            return None
        para, w, h, r, pad = fitted
        placed_box = None
        best = None
        steps_x, steps_y = 16, 12
        for iy in range(steps_y + 1):
            ty = my + (1 - 2 * my - h / H) * iy / steps_y
            for ix in range(steps_x + 1):
                tx = mx + (1 - 2 * mx - w / W) * ix / steps_x
                if tx < mx - 1e-6 or ty < my - 1e-6 or tx + w / W > 1 - mx + 1e-6 or ty + h / H > 1 - my + 1e-6:
                    continue
                box_n = (tx, ty, tx + w / W, ty + h / H)
                s = 0.0
                for f in faces:
                    s -= 70.0 * _inter(box_n, f) / max(1e-6, (f[2] - f[0]) * (f[3] - f[1]))
                for p in placed:
                    s -= 400.0 * _inter(box_n, p)
                s -= 5.0 * busy_score(busy, box_n) * (w / W) * (h / H) * 12
                s -= 25.0 * _inter(box_n, footer_box) / max(1e-6, (footer_box[2] - footer_box[0]) * (footer_box[3] - footer_box[1]))
                cx, cy = (box_n[0] + box_n[2]) / 2, (box_n[1] + box_n[3]) / 2
                s -= 1.2 * math.hypot(cx - speaker[0], cy - speaker[1])
                if best is None or s > best[0]:
                    best, placed_box = (s, box_n), box_n
        if best is None:
            return None
        box_pdf = (placed_box[0] * W, (1 - placed_box[3]) * H, w, h)
        tail, tlen = tail_points(box_pdf, sp_pdf, faces, W, H)
        cover = max((_inter(placed_box, f) / max(1e-6, (f[2] - f[0]) * (f[3] - f[1])) for f in faces), default=0.0)
        out.append(Balloon(box=box_pdf, radius=r, pad=pad, tail=tail, text=chunk, face_cover=cover,
                           busy=busy_score(busy, placed_box), speaker=sp_pdf, tail_len=tlen))
        out[-1].para = para  # type: ignore[attr-defined]
        placed.append(placed_box)
    return out
