"""Render PageLayouts to a real PDF with ReportLab (embedded TrueType subsets,
document metadata, language tag, images resampled to the output DPI)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .engine import Element, PageLayout
from .text import runs

DPI = {"screen": 150, "print": 300}


def prepare_image(path: str | Path, w_pt: float, h_pt: float, profile: str, cache: dict) -> ImageReader:
    """Resample to exactly the pixels needed for the box at the profile DPI (never stretched:
    the box already has the image's aspect ratio). JPEG for compact files."""
    key = (str(path), round(w_pt), round(h_pt), profile)
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
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90 if profile == "screen" else 94, optimize=True)
    buf.seek(0)
    reader = ImageReader(buf)
    cache[key] = reader
    return reader


def draw_text(c: canvas.Canvas, el: Element):
    r, g, b = el.data.get("color", (0.17, 0.13, 0.11))
    c.setFillColorRGB(r, g, b)
    size = el.data["size"]
    chain = el.data["chain"]
    for ln in el.data["lines"]:
        x = ln["x"]
        for font, s in runs(ln["text"], chain):
            c.setFont(font, size)
            c.drawString(x, ln["baseline"], s)
            x += c.stringWidth(s, font, size)


def render_pdf(layouts: list[PageLayout], images: dict[str, str], out_path: str | Path, *, title: str, author: str,
               subject: str, language: str, keywords: list[str], profile: str = "screen") -> dict:
    out_path = Path(out_path)
    tmp = out_path.with_suffix(".tmp.pdf")
    W, H = layouts[0].width, layouts[0].height
    from .text import font_chain
    base_font = font_chain(language)[0]
    # initialFontName: otherwise ReportLab references a non-embedded Helvetica on every page
    c = canvas.Canvas(str(tmp), pagesize=(W, H), lang=language, pageCompression=1, invariant=0,
                      initialFontName=base_font, initialFontSize=10)
    c.setTitle(title)
    c.setAuthor(author or "")
    c.setSubject(subject[:500])
    c.setKeywords(", ".join(keywords))
    c.setCreator("Bookmaker (open-source children's book generator)")
    cache: dict = {}
    placed = []
    for idx, pl in enumerate(layouts):
        if pl.background:
            c.setFillColorRGB(*pl.background)
            c.rect(0, 0, pl.width, pl.height, stroke=0, fill=1)
        for el in pl.elements:
            if el.kind == "image":
                key = f"{pl.kind}:{pl.number}" if pl.kind == "story" else el.data.get("slot", pl.kind)
                path = images.get(key)
                if not path:
                    continue
                reader = prepare_image(path, el.w, el.h, profile, cache)
                c.drawImage(reader, el.x, el.y, el.w, el.h, preserveAspectRatio=True, anchor="c", mask=None)
                if el.data.get("frame"):
                    c.setStrokeColorRGB(0.35, 0.27, 0.22)
                    c.setLineWidth(1.2)
                    c.roundRect(el.x, el.y, el.w, el.h, 6, stroke=1, fill=0)
                placed.append({"page": idx + 1, "key": key})
            elif el.kind == "rect":
                c.saveState()
                c.setFillColorRGB(*el.data.get("fill", (1, 1, 1)))
                c.setFillAlpha(el.data.get("alpha", 1))
                c.roundRect(el.x, el.y, el.w, el.h, el.data.get("radius", 0), stroke=0, fill=1)
                c.restoreState()
            elif el.kind in ("text", "pagenum"):
                draw_text(c, el)
        c.showPage()
    c.save()
    tmp.replace(out_path)  # atomic: a half-written PDF never replaces a good one
    return {"pages": len(layouts), "images_placed": placed, "bytes": out_path.stat().st_size}
