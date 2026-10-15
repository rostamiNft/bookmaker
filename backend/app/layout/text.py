"""Font management, Persian/Arabic shaping, bidi and measurement-based wrapping.

Important detail for RTL: lines are wrapped on the *logical* text (measuring the
shaped width), and bidi reordering is applied per line afterwards. Reordering a
whole paragraph first and then wrapping reverses the line order, a common bug."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import arabic_reshaper
from bidi.algorithm import get_display
from fontTools.ttLib import TTFont as FTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ..config import FONTS_DIR
from ..schemas import RTL_LANGUAGES

FONT_FILES = {
    "Andika": FONTS_DIR / "Andika-Regular.ttf",
    "Andika-Bold": FONTS_DIR / "Andika-Bold.ttf",
    "Vazirmatn": FONTS_DIR / "Vazirmatn-Regular.ttf",
    "Vazirmatn-Bold": FONTS_DIR / "Vazirmatn-Bold.ttf",
    "DejaVuSans": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}
_registered: set[str] = set()
_cmaps: dict[str, set[int]] = {}


def register_fonts() -> list[str]:
    from pathlib import Path
    for name, path in FONT_FILES.items():
        if name in _registered:
            continue
        if Path(path).is_file():
            pdfmetrics.registerFont(TTFont(name, str(path)))
            _cmaps[name] = set(FTFont(str(path), lazy=True).getBestCmap().keys())
            _registered.add(name)
    return sorted(_registered)


def font_chain(lang: str, bold: bool = False) -> list[str]:
    register_fonts()
    b = "-Bold" if bold else ""
    chain = [f"Vazirmatn{b}", f"Andika{b}"] if is_rtl(lang) else [f"Andika{b}", f"Vazirmatn{b}"]
    chain.append("DejaVuSans")
    return [f for f in chain if f in _registered]


def is_rtl(lang: str) -> bool:
    return (lang or "").split("-")[0] in RTL_LANGUAGES


_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
_reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False, "support_ligatures": True})


_LRE, _PDF = "\u202a", "\u202c"
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,:;!?'\"/+&()\-]*")


def _balanced(run: str) -> str:
    while run and (run[-1] in " .,:;!?'\"-/" or (run[-1] == ")" and run.count("(") < run.count(")"))):
        run = run[:-1]
    while run.count("(") > run.count(")"):
        run = run[:run.rfind("(")].rstrip()
    return run


def embed_ltr_runs(text: str) -> str:
    """Wrap balanced Latin runs in LRE..PDF so that brackets/punctuation inside English
    phrases keep LTR direction inside RTL paragraphs (the classic "(CPU :label" bug)."""
    out, pos = [], 0
    for m in _LATIN_RUN.finditer(text):
        run = _balanced(m.group(0))
        if not run or m.start() < pos:
            continue
        out.append(text[pos:m.start()])
        out.append(_LRE + run + _PDF)
        pos = m.start() + len(run)
    out.append(text[pos:])
    return "".join(out)


def shape(text: str, rtl: bool) -> str:
    """Return visual-order text for drawing (bidi reordering + bracket mirroring)."""
    if not _ARABIC.search(text):
        return text if not rtl else get_display(embed_ltr_runs(text), base_dir="R")
    src = embed_ltr_runs(text) if rtl else text
    reshaped = _reshaper.reshape(src)
    return get_display(reshaped, base_dir="R" if rtl else "L").replace(_LRE, "").replace(_PDF, "")


def logical_shaped(text: str) -> str:
    """Contextual glyph forms without reordering (for width measurement)."""
    return _reshaper.reshape(text) if _ARABIC.search(text) else text


def runs(text: str, chain: list[str]) -> list[tuple[str, str]]:
    """Split text into (font, substring) runs using the first font that has each glyph."""
    out: list[tuple[str, str]] = []
    for ch in text:
        cp = ord(ch)
        font = next((f for f in chain if cp in _cmaps.get(f, ())), None)
        if font is None:
            font = chain[-1]
        if out and out[-1][0] == font:
            out[-1] = (font, out[-1][1] + ch)
        else:
            out.append((font, ch))
    return out


def missing_glyphs(text: str, chain: list[str]) -> list[str]:
    miss = []
    for ch in set(text):
        if ch.isspace() or ord(ch) in (0x200c, 0x200d, 0x200e, 0x200f):
            continue
        if not any(ord(ch) in _cmaps.get(f, ()) for f in chain):
            miss.append(ch)
    return miss


def measure(text: str, chain: list[str], size: float) -> float:
    return sum(pdfmetrics.stringWidth(s, f, size) for f, s in runs(text, chain))


@dataclass
class Line:
    logical: str
    visual: str
    width: float


@dataclass
class Paragraph:
    lines: list[Line] = field(default_factory=list)
    size: float = 12
    leading: float = 16
    overflow_word: str = ""

    @property
    def height(self) -> float:
        return len(self.lines) * self.leading


def wrap(text: str, chain: list[str], size: float, max_width: float, rtl: bool, leading_factor: float) -> Paragraph:
    para = Paragraph(size=size, leading=size * leading_factor)
    for block in text.split("\n"):
        words_ = block.split()
        cur: list[str] = []
        for w in words_:
            trial = " ".join(cur + [w])
            if measure(logical_shaped(trial), chain, size) <= max_width or not cur:
                if not cur and measure(logical_shaped(w), chain, size) > max_width:
                    para.overflow_word = w  # single word wider than the box
                cur.append(w)
            else:
                line = " ".join(cur)
                para.lines.append(Line(line, shape(line, rtl), measure(logical_shaped(line), chain, size)))
                cur = [w]
        if cur:
            line = " ".join(cur)
            para.lines.append(Line(line, shape(line, rtl), measure(logical_shaped(line), chain, size)))
    return para


def fit(text: str, chain: list[str], box_w: float, box_h: float, max_size: float, min_size: float, rtl: bool,
        leading_factor: float, step: float = 0.5) -> Paragraph | None:
    """Largest font size at which the text fits the box, or None."""
    size = max_size
    while size >= min_size - 1e-6:
        p = wrap(text, chain, size, box_w, rtl, leading_factor)
        if p.height <= box_h and not p.overflow_word:
            return p
        size -= step
    return None


@lru_cache(maxsize=1)
def available_fonts() -> tuple:
    return tuple(register_fonts())
