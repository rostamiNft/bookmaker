"""Render PageLayouts to a real PDF with ReportLab (embedded TrueType subsets,
document metadata, language tag, images resampled to the output DPI)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.graphics.shapes import FILL_EVEN_ODD
from reportlab.pdfgen import canvas

from .engine import Element, PageLayout
from .shaping import shaped_string
from .text import runs

DPI = {"screen": 150, "print": 300}
MM = 72 / 25.4


def rgb_to_cmyk(r: float, g: float, b: float) -> tuple[float, float, float, float]:
    """Device conversion with full grey-component replacement (no ICC profile)."""
    k = 1 - max(r, g, b)
    if k >= 0.999:
        return 0.0, 0.0, 0.0, 1.0
    return ((1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k)


def image_to_cmyk(im: Image.Image) -> Image.Image:
    import numpy as np
    a = np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0
    k = 1 - a.max(axis=2)
    d = np.maximum(1 - k, 1e-6)
    c = (1 - a[..., 0] - k) / d
    m = (1 - a[..., 1] - k) / d
    y = (1 - a[..., 2] - k) / d
    out = np.stack([c, m, y, k], axis=2)
    return Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype("uint8"), "CMYK")


def prepare_image(path: str | Path, w_pt: float, h_pt: float, profile: str, cache: dict, color_mode: str = "rgb",
                  icc_path: str = "") -> ImageReader:
    """Resample to exactly the pixels needed for the box at the profile DPI (never stretched:
    the box already has the image's aspect ratio). JPEG for compact files.
    icc_path: convert through this CMYK profile (PDF/X) instead of the naive formula."""
    key = (str(path), round(w_pt), round(h_pt), profile, color_mode, icc_path)
    if key in cache:
        return cache[key]
    dpi = DPI[profile]
    tw, th = max(1, int(w_pt / 72 * dpi)), max(1, int(h_pt / 72 * dpi))
    with Image.open(path) as im:
        im = im.convert("RGB")
        sw, sh = im.size
        # crop (centre) only if aspect differs > 0.5% (full-bleed cover); otherwise just resize
        box_a, img_a = w_pt / h_pt, sw / sh
        if abs(box_a - img_a) / box_a > 0.005:
            if img_a > box_a:
                nw = int(sh * box_a)
                im = im.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
            else:
                nh = int(sw / box_a)
                im = im.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
        if im.width > tw or im.height > th:
            im = im.resize((tw, th), Image.LANCZOS)
        if color_mode == "cmyk":
            if icc_path:
                from .pdfx import convert_image_icc
                im = convert_image_icc(im, icc_path)
            else:
                im = image_to_cmyk(im)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90 if profile == "screen" else 94, optimize=True)
    buf.seek(0)
    reader = ImageReader(buf)
    cache[key] = reader
    return reader


def set_fill(c: canvas.Canvas, rgb, color_mode: str, cmyk_fn=None):
    if color_mode == "cmyk":
        c.setFillColorCMYK(*(cmyk_fn or rgb_to_cmyk)(*rgb))
    else:
        c.setFillColorRGB(*rgb)


def set_stroke(c: canvas.Canvas, rgb, color_mode: str, cmyk_fn=None):
    if color_mode == "cmyk":
        c.setStrokeColorCMYK(*(cmyk_fn or rgb_to_cmyk)(*rgb))
    else:
        c.setStrokeColorRGB(*rgb)


def _utf16_hex(s: str) -> str:
    return "FEFF" + s.encode("utf-16-be").hex().upper()


def draw_text(c: canvas.Canvas, el: Element, color_mode: str = "rgb", cmyk_fn=None):
    set_fill(c, el.data.get("color", (0.17, 0.13, 0.11)), color_mode, cmyk_fn)
    size = el.data["size"]
    chain = el.data["chain"]
    rtl = el.data.get("rtl")
    for ln in el.data["lines"]:
        # Persian/Arabic glyphs are drawn in visual order (HarfBuzz glyph ids or reshaped
        # presentation forms); the ActualText span gives copy/paste, search and screen
        # readers the logical text.
        actual = rtl and ln.get("logical") and (ln["logical"] != ln["text"] or ln.get("kashidas"))
        if actual:
            c._code.append(f"/Span <</ActualText <{_utf16_hex(ln['logical'])}>>> BDC")
        parts = ln.get("parts")
        if parts is None:  # layouts produced before the piece-based typesetter
            x = ln["x"]
            for font, s in runs(ln["text"], chain):
                c.setFont(font, size)
                c.drawString(x, ln["baseline"], s)
                x += c.stringWidth(s, font, size)
        else:
            for part in parts:
                x = part["x"]
                if part["k"] == "glyphs":
                    t = c.beginText(x, ln["baseline"])
                    t.setFont(part["font"], size)
                    t.textOut(shaped_string(part["font"], part["g"]))
                    c.drawText(t)
                    continue
                for font, s in part["runs"]:
                    c.setFont(font, size)
                    c.drawString(x, ln["baseline"], s)
                    x += c.stringWidth(s, font, size)
        if actual:
            c._code.append("EMC")


def _tail_path(c: canvas.Canvas, tail):
    p = c.beginPath()
    p.moveTo(tail[0][0], tail[0][1])
    for pt in tail[1:]:
        p.lineTo(pt[0], pt[1])
    p.close()
    return p


def draw_bubble(c: canvas.Canvas, el: Element, color_mode: str, page_w: float, page_h: float, cmyk_fn=None):
    """Rounded balloon + tail, drawn as one shape: fill first (tail, then body, so the
    joint disappears), then stroke each part clipped to the *outside* of the other,
    which leaves a clean comic outline with an opening where the tail leaves the body."""
    x, y, w, h = el.x, el.y, el.w, el.h
    r = el.data.get("radius", min(w, h) * 0.42)
    tail = el.data.get("tail") or []
    c.saveState()
    set_fill(c, el.data.get("fill", (1, 1, 1)), color_mode, cmyk_fn)
    c.setFillAlpha(el.data.get("alpha", 1.0))
    set_stroke(c, el.data.get("stroke", (0.2, 0.16, 0.14)), color_mode, cmyk_fn)
    c.setLineWidth(el.data.get("line_width", 1.3))
    if tail:
        c.drawPath(_tail_path(c, tail), stroke=0, fill=1)
    body = c.beginPath()
    body.roundRect(x, y, w, h, r)
    c.drawPath(body, stroke=0, fill=1)
    if tail:
        c.saveState()                       # tail edges, clipped to outside the body
        clip = c.beginPath()
        clip.rect(0, 0, page_w, page_h)
        clip.roundRect(x, y, w, h, r)
        c.clipPath(clip, stroke=0, fill=0, fillMode=FILL_EVEN_ODD)
        c.drawPath(_tail_path(c, tail), stroke=1, fill=0)
        c.restoreState()
        c.saveState()                       # body outline, clipped to outside the tail
        clip = c.beginPath()
        clip.rect(0, 0, page_w, page_h)
        tp = _tail_path(c, tail)
        clip.moveTo(tail[0][0], tail[0][1])
        for pt in tail[1:]:
            clip.lineTo(pt[0], pt[1])
        clip.close()
        del tp
        c.clipPath(clip, stroke=0, fill=0, fillMode=FILL_EVEN_ODD)
        c.drawPath(body, stroke=1, fill=0)
        c.restoreState()
    else:
        c.drawPath(body, stroke=1, fill=0)
    c.restoreState()


def draw_crop_marks(c: canvas.Canvas, o: float, W: float, H: float, bleed: float, color_mode: str, cmyk_fn=None):
    """Trim marks in the slug area, offset beyond the bleed so they never print on the page."""
    if color_mode == "cmyk":
        c.setStrokeColorCMYK(1, 1, 1, 1)  # registration
    else:
        c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.25)
    gap, length = bleed + 3, 12
    for x in (o, o + W):
        for y, sign in ((o, -1), (o + H, 1)):
            c.line(x, y + sign * gap, x, y + sign * (gap + length))  # vertical mark
    for y in (o, o + H):
        for x, sign in ((o, -1), (o + W, 1)):
            c.line(x + sign * gap, y, x + sign * (gap + length), y)


def render_pdf(layouts: list[PageLayout], images: dict[str, str], out_path: str | Path, *, title: str, author: str,
               subject: str, language: str, keywords: list[str], profile: str = "screen", bleed_mm: float = 0.0,
               crop_marks: bool = False, color_mode: str = "rgb", pdfx: bool = False,
               icc_profile: str = "") -> dict:
    """bleed_mm > 0 enlarges the media box by the bleed on every side, extends full-bleed
    pictures and page backgrounds into it, and writes TrimBox/BleedBox. crop_marks adds a
    slug area with trim marks. color_mode="cmyk" writes DeviceCMYK images and colours
    (naive conversion). pdfx=True forces CMYK, converts through an ICC profile, and adds
    the PDF/X-4 structures (OutputIntent, XMP, TrimBox, /ID, Trapped) + a self-check."""
    out_path = Path(out_path)
    tmp = out_path.with_suffix(".tmp.pdf")
    icc_path, condition, icc_name = "", "", ""
    cmyk_fn = None
    if pdfx:
        from .pdfx import PdfxError, icc_supported, resolve_icc, rgb_to_cmyk_icc
        if not icc_supported():
            raise PdfxError("Pillow was built without ImageCms; ICC-based PDF/X output is unavailable")
        p, condition, icc_name = resolve_icc(icc_profile)
        icc_path, color_mode = str(p), "cmyk"
        cmyk_fn = lambda r, g, b: rgb_to_cmyk_icc(r, g, b, icc_path)
    W, H = layouts[0].width, layouts[0].height
    b = max(0.0, float(bleed_mm)) * MM
    o = b + (18.0 if crop_marks else 0.0)  # offset of the trim box inside the media box
    MW, MH = W + 2 * o, H + 2 * o
    from .text import font_chain
    base_font = font_chain(language)[0]
    # initialFontName: otherwise ReportLab references a non-embedded Helvetica on every page
    boxes = {}
    if o > 0:
        boxes = {"trimBox": (o, o, o + W, o + H), "bleedBox": (o - b, o - b, o + W + b, o + H + b)}
    c = canvas.Canvas(str(tmp), pagesize=(MW, MH), lang=language, pageCompression=1, invariant=0,
                      initialFontName=base_font, initialFontSize=10, **boxes)
    c.setTitle(title)
    c.setAuthor(author or "")
    c.setSubject(subject[:500])
    c.setKeywords(", ".join(keywords))
    c.setCreator("Bookmaker (open-source children's book generator)")
    cache: dict = {}
    placed = []
    for idx, pl in enumerate(layouts):
        c.saveState()
        c.translate(o, o)
        if pl.background:
            set_fill(c, pl.background, color_mode)
            c.rect(-b, -b, pl.width + 2 * b, pl.height + 2 * b, stroke=0, fill=1)
        for el in pl.elements:
            if el.kind == "image":
                key = f"{pl.kind}:{pl.number}" if pl.kind == "story" else el.data.get("slot", pl.kind)
                path = images.get(key)
                if not path:
                    continue
                x, y, w, h = el.x, el.y, el.w, el.h
                if el.data.get("full_bleed") and b > 0:  # extend edges that touch the trim into the bleed
                    x0 = x - b if x <= 0.5 else x
                    y0 = y - b if y <= 0.5 else y
                    x1 = x + w + b if x + w >= pl.width - 0.5 else x + w
                    y1 = y + h + b if y + h >= pl.height - 0.5 else y + h
                    x, y, w, h = x0, y0, x1 - x0, y1 - y0
                reader = prepare_image(path, w, h, profile, cache, color_mode, icc_path)
                c.drawImage(reader, x, y, w, h, preserveAspectRatio=True, anchor="c", mask=None)
                if el.data.get("frame"):
                    set_stroke(c, (0.35, 0.27, 0.22), color_mode)
                    c.setLineWidth(1.2)
                    c.roundRect(el.x, el.y, el.w, el.h, 6, stroke=1, fill=0)
                placed.append({"page": idx + 1, "key": key})
            elif el.kind == "rect":
                c.saveState()
                set_fill(c, el.data.get("fill", (1, 1, 1)), color_mode)
                c.setFillAlpha(el.data.get("alpha", 1))
                c.roundRect(el.x, el.y, el.w, el.h, el.data.get("radius", 0), stroke=0, fill=1)
                c.restoreState()
            elif el.kind == "bubble":
                draw_bubble(c, el, color_mode, pl.width, pl.height, cmyk_fn)
            elif el.kind in ("text", "pagenum"):
                draw_text(c, el, color_mode, cmyk_fn)
        c.restoreState()
        if crop_marks:
            draw_crop_marks(c, o, W, H, b, color_mode, cmyk_fn)
        c.showPage()
    c.save()
    tmp.replace(out_path)  # atomic: a half-written PDF never replaces a good one
    out = {"pages": len(layouts), "images_placed": placed, "bytes": out_path.stat().st_size, "media_size": (MW, MH),
           "trim_size": (W, H), "trim_offset": o, "bleed_pt": b, "color_mode": color_mode}
    if pdfx:
        from .pdfx import apply_pdfx, check_pdfx4
        out["pdfx"] = apply_pdfx(out_path, title=title, author=author, subject=subject, keywords=keywords,
                                 language=language, icc_path=icc_path, condition=condition, profile_name=icc_name)
        out["pdfx"]["check"] = check_pdfx4(out_path)
        out["bytes"] = out_path.stat().st_size
    return out
