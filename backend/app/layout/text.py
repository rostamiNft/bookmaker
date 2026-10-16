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
class TypeOpts:
    """Typesetting options. shaping: auto|harfbuzz|basic; justify: stretch all but the last
    line of each paragraph to the box width (kashida first for Persian/Arabic, then word
    spaces); hyphenate: break long words at pyphen patterns (LTR languages only)."""
    shaping: str = "auto"
    justify: bool = False
    hyphenate: bool = False
    lang: str = "en"
    kashida: bool = True

    @property
    def engine(self) -> str:
        from .shaping import resolve_engine
        return resolve_engine(self.shaping)


DEFAULT_OPTS = TypeOpts()


@dataclass
class Line:
    logical: str
    visual: str
    width: float
    pieces: list | None = None
    justified: bool = False
    natural_width: float = 0.0
    kashidas: int = 0
    hyphenated: bool = False


@dataclass
class Paragraph:
    lines: list[Line] = field(default_factory=list)
    size: float = 12
    leading: float = 16
    overflow_word: str = ""
    engine: str = "basic"

    @property
    def height(self) -> float:
        return len(self.lines) * self.leading


@lru_cache(maxsize=20000)
def _word_width(word: str, chain: tuple, size: float, engine: str, rtl: bool, lang: str) -> float:
    from . import shaping as sh
    if engine == "harfbuzz" and sh.ARABIC.search(word):
        return sh.pieces_width(sh.typeset_harfbuzz(word, list(chain), _cmaps, size, rtl, lang, embed_ltr_runs))
    return measure(logical_shaped(word), list(chain), size)


def typeset(logical: str, chain: list[str], size: float, rtl: bool, opts: TypeOpts) -> tuple[str, list, float]:
    """(visual string, pieces, width) for one line."""
    from . import shaping as sh
    visual = shape(logical, rtl)
    if opts.engine == "harfbuzz":
        pieces = sh.typeset_harfbuzz(logical, chain, _cmaps, size, rtl, opts.lang, embed_ltr_runs)
    else:
        pieces = sh.typeset_basic(visual, chain, _cmaps, size)
    return visual, pieces, sh.pieces_width(pieces)


@lru_cache(maxsize=8)
def _hyphenator(lang: str):
    try:
        import pyphen
    except ImportError:
        return None
    code = {"en": "en_US", "de": "de_DE", "fr": "fr_FR", "es": "es_ES", "it": "it_IT", "nl": "nl_NL", "pt": "pt_PT"}.get(lang, lang)
    if code not in pyphen.LANGUAGES:
        return None
    return pyphen.Pyphen(lang=code, left=2, right=3)


def hyphenation_available(lang: str) -> bool:
    return not is_rtl(lang) and _hyphenator((lang or "en").split("-")[0]) is not None


_WORD_CORE = re.compile(r"^([\"'(\[«]*)([A-Za-zÀ-ÿ]+)(.*)$")


def hyphen_splits(word: str, lang: str) -> list[tuple[str, str]]:
    """Possible (head-, tail) splits of a word, longest head first."""
    h = _hyphenator(lang)
    m = _WORD_CORE.match(word)
    if not h or not m or len(m.group(2)) < 6:
        return []
    pre, core, post = m.groups()
    out = []
    for head, tail in h.iterate(core):
        out.append((pre + head + "-", tail + post))
    return out


def _finish_line(logical: str, chain, size, rtl, opts: TypeOpts, hyphenated=False) -> Line:
    visual, pieces, width = typeset(logical, chain, size, rtl, opts)
    return Line(logical, visual, width, pieces, natural_width=width, hyphenated=hyphenated)


def justify_line(line: Line, chain, size, max_width, rtl, opts: TypeOpts) -> Line:
    """Stretch a line to max_width. Persian/Arabic: up to ~60% of the slack with kashida
    (tatweel, max 2 per word, priority rules in shaping.kashida_points); the rest by
    widening word spaces. Lines whose spaces would grow beyond 4x are left unjustified."""
    from . import shaping as sh
    slack = max_width - line.width
    if slack <= 0.05:
        line.justified = True
        return line
    if opts.kashida and rtl and sh.ARABIC.search(line.logical):
        tw = _word_width(sh.TATWEEL, tuple(chain), size, opts.engine, rtl, opts.lang)
        cap = sh.kashida_capacity(line.logical)
        n = min(cap, int(slack * 0.6 / tw)) if tw > 0 else 0
        while n > 0:
            stretched = sh.kashida_plan(line.logical, n)
            visual, pieces, width = typeset(stretched, chain, size, rtl, opts)
            if width <= max_width + 0.01:
                line = Line(line.logical, visual, width, pieces, natural_width=line.natural_width, kashidas=n,
                            hyphenated=line.hyphenated)
                break
            n -= 1
    spaces = [p for p in line.pieces if p["k"] == "space"]
    slack = max_width - line.width
    if not spaces:
        line.justified = slack < 0.5
        return line
    extra = slack / len(spaces)
    if extra > 4 * spaces[0]["w"]:
        return line
    for p in spaces:
        p["w"] += extra
    line.width = sh.pieces_width(line.pieces)
    line.justified = True
    return line


def wrap(text: str, chain: list[str], size: float, max_width, rtl: bool, leading_factor: float,
         opts: TypeOpts | None = None) -> Paragraph:
    """max_width: a number, or a callable (line_index) -> width, used to typeset text
    inside non-rectangular shapes such as speech balloons."""
    opts = opts or TypeOpts(lang="fa" if rtl else "en")
    engine = opts.engine
    mw = max_width if callable(max_width) else (lambda _i: max_width)
    para = Paragraph(size=size, leading=size * leading_factor, engine=engine)
    ch = tuple(chain)
    ww = lambda w: _word_width(w, ch, size, engine, rtl, opts.lang)
    sp = measure(" ", chain, size)
    hyph = opts.hyphenate and not rtl and hyphenation_available(opts.lang)
    for block in text.split("\n"):
        words_ = block.split()
        block_lines: list[Line] = []
        cur: list[str] = []
        cur_w = 0.0
        last_hyph = 0
        i = 0
        while i < len(words_):
            w = words_[i]
            wv = ww(w)
            need = wv if not cur else cur_w + sp + wv
            if need <= mw(len(block_lines)):
                cur.append(w)
                cur_w = need
                i += 1
                continue
            # does not fit: try a hyphenated head on this line
            room = mw(len(block_lines)) - (cur_w + sp if cur else 0)
            if hyph and last_hyph < 2 and (not cur or room > mw(len(block_lines)) * 0.12):
                split = next(((hd, tl) for hd, tl in hyphen_splits(w, opts.lang) if ww(hd) <= room), None)
                if split:
                    cur.append(split[0])
                    block_lines.append(_finish_line(" ".join(cur), chain, size, rtl, opts, hyphenated=True))
                    words_[i] = split[1]
                    cur, cur_w, last_hyph = [], 0.0, last_hyph + 1
                    continue
            if not cur:  # a single word wider than the box
                para.overflow_word = w
                cur, cur_w = [w], wv
                i += 1
                continue
            block_lines.append(_finish_line(" ".join(cur), chain, size, rtl, opts))
            cur, cur_w, last_hyph = [], 0.0, 0
        if cur:
            block_lines.append(_finish_line(" ".join(cur), chain, size, rtl, opts))
        # shaping across a whole line can differ slightly from the sum of words: re-flow
        k = 0
        while k < len(block_lines):
            ln = block_lines[k]
            ws = ln.logical.split(" ")
            if ln.width > mw(k) + 0.01 and len(ws) > 1:
                block_lines[k] = _finish_line(" ".join(ws[:-1]), chain, size, rtl, opts)
                if k + 1 < len(block_lines) and not ln.hyphenated:
                    nxt = block_lines[k + 1]
                    block_lines[k + 1] = _finish_line(ws[-1] + " " + nxt.logical, chain, size, rtl, opts, nxt.hyphenated)
                else:
                    block_lines.insert(k + 1, _finish_line(ws[-1], chain, size, rtl, opts, ln.hyphenated))
                continue
            k += 1
        if opts.justify:
            for k in range(len(block_lines) - 1):
                block_lines[k] = justify_line(block_lines[k], chain, size, mw(k), rtl, opts)
        para.lines.extend(block_lines)
    return para


def fit(text: str, chain: list[str], box_w: float, box_h: float, max_size: float, min_size: float, rtl: bool,
        leading_factor: float, step: float = 0.5, opts: TypeOpts | None = None) -> Paragraph | None:
    """Largest font size at which the text fits the box, or None."""
    size = max_size
    while size >= min_size - 1e-6:
        p = wrap(text, chain, size, box_w, rtl, leading_factor, opts)
        if p.height <= box_h and not p.overflow_word:
            return p
        size -= step
    return None


@lru_cache(maxsize=1)
def available_fonts() -> tuple:
    return tuple(register_fonts())
