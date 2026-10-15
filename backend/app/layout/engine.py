"""Content-driven layout engine.

Produces, for every PDF page, a list of positioned elements computed from the
page's real content (text length, image aspect, language direction, age group).
Nothing is hard-coded for one sample: text is fitted by measurement, images are
placed at their exact aspect ratio (never stretched, never cropped), and every
layout is geometrically verified (inside safe area, no overlaps, no overflow)."""
from __future__ import annotations

from dataclasses import dataclass, field

from reportlab.lib.pagesizes import A4, A5, LETTER, landscape, portrait

from ..schemas import BookSettings
from .text import Paragraph, fit, font_chain, is_rtl, missing_glyphs, wrap

PAGE_SIZES = {"A4": A4, "A5": A5, "Letter": LETTER, "Square8": (576.0, 576.0)}
FONT_RANGE = {"3-5": (17, 30), "6-8": (14, 23), "9-12": (12, 18)}
MIN_READABLE = 9.0
ASPECTS = {"4:3": 4 / 3, "3:2": 3 / 2, "1:1": 1.0, "3:4": 3 / 4, "16:9": 16 / 9}


class LayoutError(Exception):
    def __init__(self, code: str, message: str, page: int | None = None):
        super().__init__(message)
        self.code, self.page = code, page


@dataclass
class Element:
    kind: str  # image | text | rect | line | pagenum
    x: float
    y: float  # bottom-left origin (PDF coordinates)
    w: float
    h: float
    data: dict = field(default_factory=dict)

    def box(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class PageLayout:
    kind: str  # cover | title | copyright | story | end | back
    number: int | None
    width: float
    height: float
    elements: list[Element] = field(default_factory=list)
    background: tuple | None = None
    notes: list[str] = field(default_factory=list)


def page_size(s: BookSettings) -> tuple[float, float]:
    base = PAGE_SIZES[s.page_size]
    if s.page_size == "Square8":
        return base
    return landscape(base) if s.orientation == "landscape" else portrait(base)


def margins(W: float, H: float) -> float:
    return max(28.0, round(min(W, H) * 0.065, 1))


def text_element(para: Paragraph, x, y, w, h, rtl: bool, align: str, chain: list[str], valign: str = "middle", color=(0.17, 0.13, 0.11)) -> Element:
    used = para.height
    top = y + h - (h - used) / 2 if valign == "middle" else y + h
    lines = []
    for i, ln in enumerate(para.lines):
        baseline = top - (i + 1) * para.leading + para.leading * 0.28
        if align == "center":
            lx = x + (w - ln.width) / 2
        elif rtl:
            lx = x + w - ln.width
        else:
            lx = x
        lines.append({"text": ln.visual, "logical": ln.logical, "x": round(lx, 2), "baseline": round(baseline, 2),
                      "width": round(ln.width, 2), "top": round(baseline + para.size * 0.95, 2), "bottom": round(baseline - para.size * 0.3, 2)})
    return Element("text", x, y, w, h, {"size": para.size, "leading": para.leading, "chain": chain, "lines": lines, "color": color, "rtl": rtl})


class LayoutEngine:
    def __init__(self, s: BookSettings):
        self.s = s
        self.W, self.H = page_size(s)
        self.m = margins(self.W, self.H)
        self.rtl = is_rtl(s.language)
        self.chain = font_chain(s.language)
        self.bold = font_chain(s.language, bold=True)
        self.leading = 1.75 if self.rtl else 1.42
        self.aspect = ASPECTS[s.aspect_ratio]
        self.footer = 22.0 if s.page_numbers else 0.0

    # ------------------------------------------------------------------ story
    def story_page(self, number: int, text: str, has_image: bool = True, image_path: str | None = None) -> PageLayout:
        if self.s.layout_style == "full_bleed" and has_image and image_path:
            pl = self.full_bleed_page(number, text, image_path)
            if pl.kind == "story":
                return pl
            fallback_note = pl.notes[0]
        else:
            fallback_note = ""
        pl = self._classic_story_page(number, text, has_image)
        if fallback_note:
            pl.notes.append(fallback_note)
        return pl

    def full_bleed_page(self, number: int, text: str, image_path: str) -> PageLayout:
        """Illustration covers the whole page (into the bleed); text sits on a translucent
        panel placed over the calmer band (top or bottom) of the picture. Falls back to the
        classic layout when the picture would lose >30% to cropping or the text needs too
        much of the page. Returns a PageLayout of kind 'fallback' with the reason otherwise."""
        from PIL import Image, ImageFilter, ImageStat
        W, H, m = self.W, self.H, self.m
        with Image.open(image_path) as im:
            sw, sh = im.size
            pa, ia = W / H, sw / sh
            loss = 1 - min(pa / ia, ia / pa)
            if loss > 0.30:
                return PageLayout("fallback", number, W, H, notes=[f"full_bleed→classic: {loss:.0%} of the picture would be cropped"])
            g = im.convert("L")
            if ia > pa:
                nw = int(sh * pa)
                g = g.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
            else:
                nh = int(sw / pa)
                g = g.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
            g.thumbnail((240, 240))
            edges = g.filter(ImageFilter.FIND_EDGES)
            band = int(edges.height * 0.36)
            busy_top = ImageStat.Stat(edges.crop((0, 0, edges.width, band))).mean[0]
            busy_bot = ImageStat.Stat(edges.crop((0, edges.height - band, edges.width, edges.height))).mean[0]
        lo, hi = FONT_RANGE[self.s.age_group]
        if self.rtl:
            lo, hi = lo + 1, hi + 1
        miss = missing_glyphs(text, self.chain)
        if miss:
            raise LayoutError("missing_glyphs", f"No font contains: {''.join(miss)!r}", number)
        at_top = busy_top < busy_bot
        pad = max(12.0, m * 0.45)
        px, pw = m * 0.7, W - 1.4 * m
        tw = pw - 2 * pad
        align = "center" if self.s.age_group == "3-5" else "start"
        for frac in (0.2, 0.25, 0.3, 0.35, 0.4):
            ph = H * frac
            para = fit(text, self.chain, tw, ph - 2 * pad, hi, lo, self.rtl, self.leading)
            if not para:
                continue
            ph = min(ph, para.height + 2 * pad + 6)  # shrink the panel to the text actually set
            if at_top:
                py = H - m * 0.7 - ph
            else:
                py = m * 0.7 + self.footer
            pl = PageLayout("story", number, W, H)
            pl.elements.append(Element("image", 0, 0, W, H, {"slot": "page", "frame": False, "full_bleed": True}))
            pl.elements.append(Element("rect", px, py, pw, ph, {"fill": (1, 0.99, 0.96), "alpha": 0.88, "radius": 14}))
            pl.elements.append(text_element(para, px + pad, py + pad, tw, ph - 2 * pad, self.rtl, align, self.chain))
            if self.s.page_numbers:
                self._pagenum(pl, number, panel=True)
            pl.notes.append(f"layout=full_bleed panel={'top' if at_top else 'bottom'} panel_frac={ph / H:.2f} font={para.size} crop={loss:.0%}")
            return pl
        return PageLayout("fallback", number, W, H, notes=["full_bleed→classic: text needs more than 40% of the page"])

    def _classic_story_page(self, number: int, text: str, has_image: bool = True) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        lo, hi = FONT_RANGE[self.s.age_group]
        if self.rtl:
            lo, hi = lo + 1, hi + 1  # Persian glyphs read smaller at equal point size
        cx0, cy0 = m, m + self.footer
        cw, ch = W - 2 * m, H - 2 * m - self.footer
        gap = max(14.0, ch * 0.035)
        align = "center" if self.s.age_group == "3-5" else "start"
        miss = missing_glyphs(text, self.chain)
        if miss:
            raise LayoutError("missing_glyphs", f"No font contains: {''.join(miss)!r}", number)
        attempts = []
        landscape_page = W > H * 1.05
        # candidate layouts, most image-generous first
        candidates = []
        if landscape_page and self.aspect <= 1.6:
            for frac in (0.58, 0.52, 0.46):
                candidates.append(("side", frac))
        for frac in (0.66, 0.6, 0.54, 0.48, 0.42):
            candidates.append(("stack", frac))
        for mode, frac in candidates:
            if mode == "stack":
                ih = min(ch * frac, cw / self.aspect)
                iw = ih * self.aspect
                ix, iy = cx0 + (cw - iw) / 2, cy0 + ch - ih
                tx, ty, tw, th = cx0 + cw * 0.04, cy0, cw * 0.92, ch - ih - gap
            else:
                iw = min(cw * frac, ch * self.aspect)
                ih = iw / self.aspect
                img_left = not self.rtl
                ix = cx0 if img_left else cx0 + cw - iw
                iy = cy0 + (ch - ih) / 2
                tw = cw - iw - gap
                tx = cx0 + iw + gap if img_left else cx0
                ty, th = cy0, ch
            if th <= 0 or tw <= 0:
                continue
            para = fit(text, self.chain, tw, th, hi, lo, self.rtl, self.leading)
            attempts.append((mode, frac, para.size if para else None))
            if para:
                pl = PageLayout("story", number, W, H)
                if has_image:
                    pl.elements.append(Element("image", ix, iy, iw, ih, {"slot": "page", "frame": True}))
                pl.elements.append(text_element(para, tx, ty, tw, th, self.rtl, align, self.chain))
                self._pagenum(pl, number)
                pl.notes.append(f"layout={mode} image_frac={frac} font={para.size}")
                return pl
        raise LayoutError("text_overflow", f"Text does not fit even at {lo}pt with the smallest image ({len(text)} chars).", number)

    def _pagenum(self, pl: PageLayout, number: int | None, panel: bool = False):
        if self.s.page_numbers and number is not None:
            label = str(number)
            if self.rtl:
                label = label.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
            para = wrap(label, self.chain, 10, 60, self.rtl, 1.2)
            if panel:  # readable on top of a full-bleed picture
                pl.elements.append(Element("rect", self.W / 2 - 16, self.m * 0.55 - 3, 32, 20, {"fill": (1, 0.99, 0.96), "alpha": 0.85, "radius": 9}))
            pl.elements.append(text_element(para, self.W / 2 - 30, self.m * 0.55, 60, 16, self.rtl, "center", self.chain,
                                            color=(0.45, 0.4, 0.38)))
            pl.elements[-1].kind = "pagenum"

    # ------------------------------------------------------------------ front/back matter
    def _fit_block(self, text, x, y, w, h, hi, lo, bold=False, align="center", color=(0.17, 0.13, 0.11), page=None):
        chain = self.bold if bold else self.chain
        para = fit(text, chain, w, h, hi, lo, self.rtl, 1.3 if not self.rtl else 1.55)
        if para is None:
            raise LayoutError("text_overflow", f"Text block does not fit: {text[:40]!r}", page)
        return text_element(para, x, y, w, h, self.rtl, align, chain, color=color)

    def cover(self, title: str, author: str, style: str, has_image: bool) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        pl = PageLayout("cover", None, W, H)
        if style == "illustrated" and has_image:
            pl.elements.append(Element("image", 0, 0, W, H, {"slot": "cover", "frame": False, "full_bleed": True}))
            band_h = H * 0.24
            pl.elements.append(Element("rect", m * 0.6, H - band_h - m * 0.6, W - m * 1.2, band_h, {"fill": (1, 0.99, 0.96), "alpha": 0.86, "radius": 16}))
            pl.elements.append(self._fit_block(title, m * 1.2, H - band_h - m * 0.6 + band_h * 0.12, W - m * 2.4, band_h * 0.76, 46, 16, bold=True))
            if author:
                pl.elements.append(Element("rect", W * 0.2, m * 0.8, W * 0.6, 40, {"fill": (1, 0.99, 0.96), "alpha": 0.86, "radius": 12}))
                pl.elements.append(self._fit_block(author, W * 0.22, m * 0.8 + 4, W * 0.56, 32, 18, 10))
        else:
            pl.background = (0.98, 0.95, 0.88) if style == "framed" else (0.93, 0.96, 0.99)
            th = H * 0.2
            pl.elements.append(self._fit_block(title, m, H - m - th, W - 2 * m, th, 44, 16, bold=True))
            if has_image:
                avail_h = H - 2 * m - th - 80
                iw = min(W - 2 * m, avail_h * self.aspect) * (0.9 if style == "minimal" else 1.0)
                ih = iw / self.aspect
                pl.elements.append(Element("image", (W - iw) / 2, m + 70 + (avail_h - ih) / 2, iw, ih, {"slot": "cover", "frame": style == "framed"}))
            if author:
                pl.elements.append(self._fit_block(author, m, m, W - 2 * m, 50, 20, 10))
        return pl

    def title_page(self, title: str, author: str, publisher: str) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        pl = PageLayout("title", None, W, H)
        pl.elements.append(self._fit_block(title, m, H * 0.5, W - 2 * m, H * 0.25, 40, 16, bold=True))
        y = H * 0.38
        if author:
            pl.elements.append(self._fit_block(author, m, y, W - 2 * m, 40, 20, 11))
        if publisher:
            pl.elements.append(self._fit_block(publisher, m, m + 10, W - 2 * m, 30, 13, 9, color=(0.4, 0.36, 0.34)))
        return pl

    def copyright_page(self, lines: list[str]) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        pl = PageLayout("copyright", None, W, H)
        text = "\n".join(lines)
        para = fit(text, self.chain, W - 2 * m, H * 0.45, 10, 7, self.rtl, 1.5)
        if para is None:
            raise LayoutError("text_overflow", "Copyright page text does not fit")
        el = text_element(para, m, m, W - 2 * m, H * 0.45, self.rtl, "start", self.chain, valign="bottom", color=(0.35, 0.32, 0.3))
        pl.elements.append(el)
        return pl

    def end_page(self, text: str, has_image: bool) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        pl = PageLayout("end", None, W, H)
        pl.elements.append(self._fit_block(text, m, H * 0.42, W - 2 * m, H * 0.16, 48, 20, bold=True))
        return pl

    def back_cover(self, blurb: str, publisher: str, has_image: bool) -> PageLayout:
        W, H, m = self.W, self.H, self.m
        pl = PageLayout("back", None, W, H)
        pl.background = (0.96, 0.93, 0.86)
        top = H - m
        if has_image:
            iw = (W - 2 * m) * 0.78
            ih = iw / self.aspect
            pl.elements.append(Element("image", (W - iw) / 2, top - ih, iw, ih, {"slot": "back", "frame": True}))
            top -= ih + 30
        pl.elements.append(self._fit_block(blurb, m * 1.5, m + 60, W - 3 * m, top - m - 60, 22, 11))
        footer = (publisher + " · " if publisher else "") + "Bookmaker"
        pl.elements.append(self._fit_block(footer, m, m, W - 2 * m, 24, 10, 8, color=(0.45, 0.4, 0.38)))
        return pl


# ---------------------------------------------------------------------- verification
def _overlap(a, b, tol=0.5) -> bool:
    return a[0] < b[2] - tol and b[0] < a[2] - tol and a[1] < b[3] - tol and b[1] < a[3] - tol


def verify_layout(pl: PageLayout, margin: float) -> list[dict]:
    """Geometric checks: text inside safe area & its box, no overlaps, readable sizes."""
    issues = []

    def add(code, msg, sev="error"):
        issues.append({"code": code, "severity": sev, "message": msg, "page": pl.number, "page_kind": pl.kind, "stage": "layout"})

    W, H = pl.width, pl.height
    texts = [e for e in pl.elements if e.kind in ("text", "pagenum")]
    images = [e for e in pl.elements if e.kind == "image"]
    for e in pl.elements:
        x0, y0, x1, y1 = e.box()
        if x0 < -0.01 or y0 < -0.01 or x1 > W + 0.01 or y1 > H + 0.01:
            add("out_of_page", f"{e.kind} element outside the page {e.box()}")
    for t in texts:
        size = t.data["size"]
        if size < MIN_READABLE - 1e-6 and t.kind == "text" and pl.kind == "story":
            add("font_too_small", f"Font size {size}pt below {MIN_READABLE}pt")
        for ln in t.data["lines"]:
            if ln["width"] > t.w + 0.6:
                add("line_overflow", f"Line wider than its box ({ln['width']:.1f} > {t.w:.1f})")
            if ln["x"] < t.x - 0.6 or ln["x"] + ln["width"] > t.x + t.w + 0.6:
                add("line_outside_box", "Line drawn outside its text box")
            if ln["bottom"] < t.y - 1.0 or ln["top"] > t.y + t.h + 1.0:
                add("vertical_overflow", "Text overflows its box vertically")
            if t.kind == "text" and not any(i.data.get("full_bleed") for i in images):
                if ln["x"] < margin * 0.5 - 0.5 or ln["x"] + ln["width"] > W - margin * 0.5 + 0.5:
                    add("outside_safe_area", "Text enters the page margin")
        for im in images:
            if im.data.get("full_bleed"):
                continue  # text over full-bleed cover sits on its own panel
            if _overlap(t.box(), im.box()):
                add("overlap", "Text box overlaps an image")
    if any(i.data.get("full_bleed") for i in images):
        panels = [e.box() for e in pl.elements if e.kind == "rect" and e.data.get("alpha", 1) >= 0.75]
        for t in texts:
            ls = t.data["lines"]
            if not ls:
                continue
            tb = (min(l["x"] for l in ls), min(l["bottom"] for l in ls), max(l["x"] + l["width"] for l in ls), max(l["top"] for l in ls))
            inside = any(p[0] - 0.5 <= tb[0] and p[1] - 0.5 <= tb[1] and tb[2] <= p[2] + 0.5 and tb[3] <= p[3] + 0.5 for p in panels)
            if not inside:
                add("text_on_picture", "Text over a full-bleed picture without a readable panel behind it")
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            if _overlap(images[i].box(), images[j].box()):
                add("overlap", "Two images overlap")
    if pl.kind == "story":
        if not any(t.kind == "text" and t.data["lines"] for t in texts):
            add("empty_text", "Story page has no text")
    return issues
