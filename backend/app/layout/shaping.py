"""Line typesetting: Unicode bidi levels -> word tokens -> visual order -> shaped pieces.

Two shaping engines produce the same "piece" structure, so justification, kashida and
drawing are shared:

* ``harfbuzz`` (default when ``uharfbuzz`` is installed): every RTL token that contains
  Arabic-script letters is shaped by HarfBuzz with an explicit direction/script/language,
  giving the font's own contextual forms, lam-alef and other ligatures and GPOS mark
  positioning. ReportLab draws the resulting glyph ids through its ShapedStr support.
* ``basic``: arabic-reshaper presentation forms + python-bidi visual reordering (the
  previous engine; used as fallback when uharfbuzz is missing).

HarfBuzz does *not* do bidi reordering, so digits or Latin words inside a Persian
token are separated by bidi level first and shaped as their own LTR runs.

A piece is a dict:
  {"k": "space", "w": width}
  {"k": "text",  "w": width, "runs": [(font, visual_string), ...]}
  {"k": "glyphs","w": width, "font": font, "g": [[gid, x_adv, x_off, y_off], ...]}
(glyph metrics in 1/1000 em, widths in points at the line's size).
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import bidi.algorithm as _ba
from reportlab.pdfbase import pdfmetrics

try:  # Apache-2.0, optional
    import uharfbuzz as hb
except Exception:  # pragma: no cover - exercised when the wheel is unavailable
    hb = None

ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
TATWEEL = "\u0640"
_IGNORABLE = {0x200C, 0x200D, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069}


def harfbuzz_available() -> bool:
    return hb is not None


def resolve_engine(shaping: str) -> str:
    if shaping == "basic" or hb is None:
        return "basic"
    return "harfbuzz"


# --------------------------------------------------------------------------- bidi
def bidi_levels(text: str, base_rtl: bool) -> list[int]:
    """Resolved embedding level for every character of ``text`` (UAX #9 via python-bidi's
    reference implementation, which drops BN/explicit codes in rule X9; those characters
    inherit the level of the previous character so indices stay aligned)."""
    st = _ba.get_empty_storage()
    st["base_level"] = 1 if base_rtl else 0
    st["base_dir"] = "R" if base_rtl else "L"
    _ba.get_embedding_levels(text, st, False, False)
    _ba.explicit_embed_and_overrides(st, False)
    _ba.resolve_weak_types(st, False)
    _ba.resolve_neutral_types(st, False)
    _ba.resolve_implicit_levels(st, False)
    kept = iter(st["chars"])
    levels: list[int] = []
    prev = st["base_level"]
    for ch in text:
        if unicodedata.bidirectional(ch) in _ba.X9_REMOVED:
            levels.append(prev)
            continue
        c = next(kept, None)
        prev = c["level"] if c else prev
        levels.append(prev)
    return levels


def _reorder(items: list, levels: list[int]) -> list:
    """Rule L2: from the highest level down to the lowest odd level, reverse every
    maximal sequence of items at that level or higher."""
    if not items:
        return items
    items = list(items)
    levels = list(levels)
    hi = max(levels)
    lo_odd = min(lv for lv in levels if lv % 2) if any(lv % 2 for lv in levels) else hi + 1
    for lv in range(hi, lo_odd - 1, -1):
        i = 0
        while i < len(items):
            if levels[i] >= lv:
                j = i
                while j < len(items) and levels[j] >= lv:
                    j += 1
                items[i:j] = items[i:j][::-1]
                levels[i:j] = levels[i:j][::-1]
                i = j
            else:
                i += 1
    return items


# --------------------------------------------------------------------------- fonts
def font_for(ch: str, chain: list[str], cmaps: dict) -> str:
    cp = ord(ch)
    return next((f for f in chain if cp in cmaps.get(f, ())), chain[-1])


@lru_cache(maxsize=16)
def _hb_font(font_name: str):
    ttf = pdfmetrics.getFont(font_name)
    if not getattr(ttf, "shapable", False):
        return None
    hbf = ttf.hbFont(1000)  # also installs ttf.hbAddPrivate
    return hbf


def _hb_shape(text: str, font_name: str, rtl: bool, lang: str) -> list[list[float]] | None:
    hbf = _hb_font(font_name)
    if hbf is None:
        return None
    ttf = pdfmetrics.getFont(font_name)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.direction = "rtl" if rtl else "ltr"
    if ARABIC.search(text):
        buf.script = "Arab"
    else:
        buf.guess_segment_properties()
        buf.direction = "rtl" if rtl else "ltr"
    buf.language = lang or ("fa" if rtl else "en")
    hb.shape(hbf, buf, {"kern": True, "liga": True})
    out = []
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        src = text[info.cluster] if info.cluster < len(text) else ""
        if src and ord(src) in _IGNORABLE and pos.x_advance == 0:
            continue  # ZWNJ/ZWJ: invisible zero-width "space" glyph, drop it
        out.append([info.codepoint, ttf.pdfScale(pos.x_advance), ttf.pdfScale(pos.x_offset), ttf.pdfScale(pos.y_offset)])
    return out


def shaped_string(font_name: str, glyphs: list[list[float]]):
    """Build a ReportLab ShapedStr (glyph ids mapped to cmap or private-use chars)."""
    from reportlab.pdfbase.ttfonts import ShapeData, ShapedStr
    ttf = pdfmetrics.getFont(font_name)
    hbf = _hb_font(font_name)
    chars, data = [], []
    for gid, xa, xo, yo in glyphs:
        gid = int(gid)
        try:
            u = ttf.face.glyphToChar[gid][0]
        except (KeyError, IndexError):
            u = ttf.hbAddPrivate(hbf.glyph_to_string(gid), gid, xa)
        chars.append(chr(u))
        data.append(ShapeData(0, xa, 0, xo, yo, ttf.face.charWidths.get(u, xa)))
    # every glyph its own cluster: textobject then positions each one exactly
    data = [d._replace(cluster=i) for i, d in enumerate(data)]
    return ShapedStr("".join(chars), shapeData=data)


# --------------------------------------------------------------------------- pieces
def _text_piece(visual: str, chain: list[str], cmaps: dict, size: float) -> dict:
    runs: list[list] = []
    for ch in visual:
        f = font_for(ch, chain, cmaps)
        if runs and runs[-1][0] == f:
            runs[-1][1] += ch
        else:
            runs.append([f, ch])
    w = sum(pdfmetrics.stringWidth(s, f, size) for f, s in runs)
    return {"k": "text", "w": w, "runs": [tuple(r) for r in runs]}


def space_width(chain: list[str], size: float) -> float:
    return pdfmetrics.stringWidth(" ", chain[0], size)


def typeset_harfbuzz(line: str, chain: list[str], cmaps: dict, size: float, rtl: bool, lang: str, embed) -> list[dict]:
    src = embed(line) if rtl else line
    levels = bidi_levels(src, rtl)
    # tokens: (text, level, is_space) split at spaces and level changes; explicit codes dropped
    toks: list[list] = []
    for ch, lv in zip(src, levels):
        if ord(ch) in _IGNORABLE and ch not in "\u200c\u200d":
            continue
        sp = ch == " "
        if toks and not sp and not toks[-1][2] and toks[-1][1] == lv:
            toks[-1][0] += ch
        else:
            toks.append([ch, lv, sp])
    pieces_lv = []
    for text, lv, sp in toks:
        if sp:
            pieces_lv.append(({"k": "space", "w": space_width(chain, size)}, lv))
            continue
        tok_rtl = lv % 2 == 1
        # split by font; the order of font sub-runs follows the token direction
        sub: list[list] = []
        for ch in text:
            f = font_for(ch, chain, cmaps)
            if sub and sub[-1][0] == f:
                sub[-1][1] += ch
            else:
                sub.append([f, ch])
        for f, s in sub:
            glyphs = _hb_shape(s, f, tok_rtl, lang) if ARABIC.search(s) else None
            if glyphs is not None and not _extractable(f, glyphs):
                # HarfBuzz picked a glyph the font's cmap does not name (a contextual
                # variant). ReportLab then maps it to a private-use code and the letter
                # is lost when text is copied or searched, so this run uses the
                # presentation-forms path instead - both give identical visuals here.
                glyphs = None
            if glyphs is not None:
                w = sum(g[1] for g in glyphs) * size / 1000
                pieces_lv.append(({"k": "glyphs", "w": w, "font": f, "g": glyphs}, lv))
            else:
                pieces_lv.append((_text_piece(_basic_visual(s, tok_rtl), chain, cmaps, size), lv))
    # L2 on the token sequence. Sub-runs of a token share its level, so they move together
    ordered = _reorder([p for p, _ in pieces_lv], [lv for _, lv in pieces_lv])
    return _merge_text(ordered)


def _merge_text(pieces: list[dict]) -> list[dict]:
    """Join adjacent non-space pieces into one 'word' group so justification only
    stretches real spaces: groups are lists of pieces drawn back to back."""
    out: list[dict] = []
    for p in pieces:
        if p["k"] == "space":
            out.append(p)
        elif out and out[-1]["k"] == "word":
            out[-1]["parts"].append(p)
            out[-1]["w"] += p["w"]
        else:
            out.append({"k": "word", "w": p["w"], "parts": [p]})
    return out


@lru_cache(maxsize=8)
def _cmap_glyphs(font_name: str) -> frozenset:
    """Glyph ids the font's cmap names (i.e. the PDF's ToUnicode will be a real character)."""
    try:
        face = pdfmetrics.getFont(font_name).face
        return frozenset(face.charToGlyph.values())
    except Exception:
        return frozenset()


def _extractable(font_name: str, glyphs) -> bool:
    known = _cmap_glyphs(font_name)
    return bool(known) and all(g[0] in known for g in glyphs)


def _basic_visual(s: str, tok_rtl: bool) -> str:
    """Presentation forms in visual order (the pre-HarfBuzz path)."""
    from .text import logical_shaped
    v = logical_shaped(s)
    if tok_rtl:
        v = "".join(_ba.MIRRORED.get(c, c) for c in reversed(v))
    return "".join(c for c in v if ord(c) not in _IGNORABLE)


def typeset_basic(visual: str, chain: list[str], cmaps: dict, size: float) -> list[dict]:
    pieces: list[dict] = []
    for i, word in enumerate(visual.split(" ")):
        if i:
            pieces.append({"k": "space", "w": space_width(chain, size)})
        if word:
            pieces.append(_text_piece(word, chain, cmaps, size))
    return _merge_text(pieces)


def pieces_width(pieces: list[dict]) -> float:
    return sum(p["w"] for p in pieces)


# --------------------------------------------------------------------------- kashida
# Joining classes (Persian + Arabic letters used in children's books).
_DUAL = set("بپتثجچحخسشصضطظعغفقکگلمنهیئيكىـ")
_RIGHT = set("اآأإٱدذرزژوؤة")
_BEH = set("بپتثنیئي")
_HAH = set("جچحخ")
_SEEN = set("سشصض")
_TAH = set("طظ")
_AIN = set("عغ")
_FEH = set("فق")
_ALEF = set("اآأإٱ")
_REH = set("رزژ")
_DAL = set("دذ")
_KAF = set("کگك")


def kashida_points(word: str) -> list[tuple[int, int]]:
    """Positions (insert *after* index i) where a tatweel may stretch the word, with a
    priority (higher first). Simplified naskh rules after aliftype/raqim-kashida (MIT),
    which follow Benatia et al. 2006: never after lam (would break lam-alef) and never
    before hah/sad/ain/waw or before a final feh/qaf/yeh; best before a final heh and
    between tooth letters and tah."""
    letters = [c for c in word]
    res = []
    for i in range(len(letters) - 1):
        a, b = letters[i], letters[i + 1]
        if a not in _DUAL or (b not in _DUAL and b not in _RIGHT) or a == TATWEEL or b == TATWEEL:
            continue
        if a == "ل" or b in _HAH or b in "صضعغو":
            continue
        final = i + 1 == len(letters) - 1 or (i + 2 < len(letters) and not ARABIC.match(letters[i + 2]))
        if final and b in "فقیيى":
            continue
        if final and b in "هة":
            pr = 9
        elif (a in _BEH or a in _HAH or a in _AIN or a in _FEH) and b in _TAH:
            pr = 9
        elif (a in _SEEN or a in _TAH) and (b in _ALEF or b in _BEH or b in _REH or b in _SEEN or b in _TAH or b in _KAF or b in "لن"):
            pr = 6
        elif a == "م" and (b in _TAH or b in _DAL or b in _REH):
            pr = 6
        else:
            pr = 3
        res.append((i, pr))
    return res


def kashida_plan(line: str, n: int, per_word: int = 2) -> str:
    """Insert ``n`` tatweels into ``line`` following kashida priorities, at most
    ``per_word`` per word, spreading over words before stacking in one word."""
    words = line.split(" ")
    cands = []
    for wi, w in enumerate(words):
        for idx, pr in kashida_points(w):
            cands.append((pr, wi, idx))
    if not cands or n <= 0:
        return line
    cands.sort(key=lambda t: (-t[0], t[1], t[2]))
    used: dict[int, list[int]] = {}
    count = 0
    for rnd in range(per_word):
        for pr, wi, idx in cands:
            if count >= n:
                break
            spots = used.setdefault(wi, [])
            if len(spots) != rnd or idx in spots:
                continue
            spots.append(idx)
            count += 1
    for wi, spots in used.items():
        w = words[wi]
        for idx in sorted(spots, reverse=True):
            w = w[: idx + 1] + TATWEEL + w[idx + 1:]
        words[wi] = w
    return " ".join(words)


def kashida_capacity(line: str, per_word: int = 2) -> int:
    return sum(min(per_word, len(kashida_points(w))) for w in line.split(" "))
