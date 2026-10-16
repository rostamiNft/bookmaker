"""Additional output formats besides the print PDF.

* EPUB 3 (reflowable, one page per spread item, RTL page progression for Persian,
  embedded OFL fonts, optional narration audio per page). Validated with the W3C
  EPUBCheck when Java + epubcheck are installed (pip: ``epubcheck`` + ``jdk4py``).
* Self-contained HTML reader (one file: images and audio embedded, keyboard /
  swipe navigation, read-aloud with recorded narration or the browser's speech
  synthesis).
* Images ZIP (cover + numbered page illustrations, original resolution).

Everything is generated from the same authoritative data as the PDF (page table +
plan), never from the PDF itself."""
from __future__ import annotations

import base64
import html
import re
import io
import json
import subprocess
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from ..config import FONTS_DIR

RTL = {"fa", "ar", "he", "ur"}
UI = {
    "en": {"contents": "Contents", "cover": "Cover", "page": "Page", "end": "The End", "listen": "Listen", "read": "Read aloud",
           "stop": "Stop", "prev": "Previous", "next": "Next", "illustration": "Illustration for page"},
    "fa": {"contents": "فهرست", "cover": "جلد", "page": "صفحه", "end": "پایان", "listen": "شنیدن", "read": "بلند بخوان",
           "stop": "توقف", "prev": "قبلی", "next": "بعدی", "illustration": "تصویر صفحه‌ی"},
}


@dataclass
class BookPage:
    number: int
    text: str
    image: Path | None = None
    audio: Path | None = None
    alt: str = ""
    words: list = field(default_factory=list)      # word timings [{w,t,d}] when narration exists
    timing_source: str = ""                        # engine_word_events | estimated_proportional
    duration_s: float = 0.0


@dataclass
class BookData:
    id: str
    title: str
    language: str
    author: str = ""
    publisher: str = ""
    summary: str = ""
    blurb: str = ""
    cover: Path | None = None
    pages: list[BookPage] = field(default_factory=list)
    version: int = 1
    models: list[str] = field(default_factory=list)

    @property
    def rtl(self) -> bool:
        return self.language.split("-")[0] in RTL

    @property
    def ui(self) -> dict:
        return UI.get(self.language, UI["en"])


def _jpeg(path: Path, max_side: int = 1400, quality: int = 86) -> bytes:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True)
        return buf.getvalue()


def _digits(n: int, lang: str) -> str:
    s = str(n)
    return s.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")) if lang == "fa" else s


def _word_spans(text: str, words: list[dict]) -> str:
    """Text with one span per word carrying the narration timing (data-t/data-d).

    Timings are matched in document order (the k-th word of the page takes the k-th
    timing); a word the engine merged or skipped simply gets no timing. Everything is
    HTML-escaped, so the result can be interpolated into the template."""
    paras = []
    k = 0
    for para in (text or "").split("\n"):
        i, out, idx = 0, [], 0
        for m in re.finditer(r"\S+", para):
            out.append(html.escape(para[i:m.start()]))
            w = m.group()
            t = words[k] if k < len(words) and str(words[k].get("w", "")).strip() == w.strip(" .,!?؛،:") else None
            if t is not None:
                k += 1
            if t:
                out.append(f'<span class="w" data-t="{float(t.get("t", 0)):.3f}" data-d="{float(t.get("d", 0)):.3f}">{html.escape(w)}</span>')
            else:
                out.append(f'<span class="w">{html.escape(w)}</span>')
            i = m.end()
            idx += 1
        out.append(html.escape(para[i:]))
        body = "".join(out)
        if body.strip():
            paras.append(body)
    return "".join(f"<p>{x}</p>" for x in paras)


def _smil_clock(seconds: float) -> str:
    """EPUB/SMIL clock value, always the full H:MM:SS.mmm form."""
    seconds = max(0.0, float(seconds))
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{sec:06.3f}"


def _word_spans_ids(text: str, words: list[dict], prefix: str) -> str:
    """Like :func:`_word_spans` but every span gets an id, so a media overlay can
    point at it (EPUB 3 ``<text src="page-01.xhtml#p01w3"/>``)."""
    out, i, k, idx = [], 0, 0, 0
    for m in re.finditer(r"\S+", text):
        out.append(html.escape(text[i:m.start()]))
        w = m.group()
        t = words[k] if k < len(words) and str(words[k].get("w", "")).strip() == w.strip(" .,!?؛،:") else None
        if t is not None:
            k += 1
        wid = f"{prefix}{idx}"
        if t:
            out.append(f'<span id="{wid}" data-t="{float(t.get("t", 0)):.3f}" data-d="{float(t.get("d", 0)):.3f}">{html.escape(w)}</span>')
        else:
            out.append(f'<span id="{wid}">{html.escape(w)}</span>')
        i = m.end()
        idx += 1
    out.append(html.escape(text[i:]))
    return f"<p>{''.join(out)}</p>"


def _smil_end(words: list[dict], duration_s: float) -> float:
    """Where the last <par> of an overlay ends (the overlay's own duration)."""
    end = 0.0
    for w in words or []:
        t, d = float(w.get("t", 0) or 0), float(w.get("d", 0) or 0)
        if d > 0:
            end = max(end, min(t + d, duration_s) if duration_s else t + d)
    return end


def _smil(n: str, words: list[dict], duration_s: float, prefix: str) -> str:
    """EPUB 3 media overlay: one <par> per word, clipped to the word's time in the audio."""
    total = _smil_clock(_smil_end(words, duration_s) or duration_s)
    pars, k = [], 0
    for i, w in enumerate(words or []):
        t, d = float(w.get("t", 0) or 0), float(w.get("d", 0) or 0)
        if d <= 0:
            continue
        end = min(t + d, duration_s) if duration_s else t + d
        if end <= t:
            continue
        pars.append(f'<par id="par-{n}-{i}"><text src="page-{n}.xhtml#{prefix}{k}"/>'
                    f'<audio src="audio/page-{n}.mp3" clipBegin="{_smil_clock(t)}" clipEnd="{_smil_clock(end)}"/></par>')
        k += 1
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<smil xmlns="http://www.w3.org/ns/SMIL" xmlns:epub="http://www.idpf.org/2007/ops" version="3.0">\n'
            f'<head><metadata><meta property="media:duration">{total}</meta></metadata></head>\n'
            f'<body><seq id="seq-{n}" epub:textref="page-{n}.xhtml" epub:type="bodymatter chapter">\n'
            + "\n".join(pars) + "\n</seq>\n</body>\n</smil>\n")


def _paras(text: str) -> str:
    return "\n".join(f"<p>{html.escape(p.strip())}</p>" for p in text.split("\n") if p.strip())


def _alt(book: BookData, p: BookPage) -> str:
    return p.alt[:240] if p.alt else f"{book.ui['illustration']} {_digits(p.number, book.language)}"


def _font_files(book: BookData) -> list[tuple[str, Path]]:
    names = ["Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"] if book.rtl else ["Andika-Regular.ttf", "Andika-Bold.ttf"]
    return [(n, FONTS_DIR / n) for n in names if (FONTS_DIR / n).is_file()]


def _css(book: BookData, font_prefix: str) -> str:
    fam = "Vazirmatn" if book.rtl else "Andika"
    fonts = _font_files(book)
    faces = "".join(
        f"@font-face{{font-family:'{fam}';src:url('{font_prefix}{n}');font-weight:{'700' if 'Bold' in n else '400'};font-style:normal;}}\n"
        for n, _ in fonts)
    return faces + f"""
body{{font-family:'{fam}',serif;margin:0;padding:0 4%;line-height:{1.9 if book.rtl else 1.5};color:#2b211c;background:#fffdf8}}
h1{{text-align:center;font-weight:700;margin:1.2em 0 .4em}}
figure{{margin:1em 0;text-align:center}}
figure img{{max-width:100%;max-height:70vh;height:auto;border-radius:6px}}
.text{{font-size:1.25em}}
.num{{text-align:center;color:#8a7d75;font-size:.8em}}
audio{{width:100%;margin:.6em 0}}
.cover img{{width:100%;height:auto}}
.end{{text-align:center;font-size:2em;margin-top:30vh}}
nav ol{{list-style:none;padding:0}}
"""


# ---------------------------------------------------------------------- EPUB 3
def build_epub(book: BookData, out: Path) -> dict:
    lang = book.language
    dir_ = "rtl" if book.rtl else "ltr"
    ui = book.ui
    uid = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, f'bookmaker:{book.id}:v{book.version}')}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest, spine, nav = [], [], []
    files: dict[str, bytes] = {}

    def xhtml(title: str, body: str, extra_head: str = "") -> bytes:
        return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
                f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}" dir="{dir_}">\n'
                f'<head><meta charset="UTF-8"/><title>{html.escape(title)}</title>'
                f'<link rel="stylesheet" type="text/css" href="css/book.css"/>{extra_head}</head>\n<body>\n{body}\n</body></html>').encode("utf-8")

    files["OEBPS/css/book.css"] = _css(book, "../fonts/").encode()
    manifest.append('<item id="css" href="css/book.css" media-type="text/css"/>')
    for n, path in _font_files(book):
        files[f"OEBPS/fonts/{n}"] = path.read_bytes()
        manifest.append(f'<item id="font-{n.split(".")[0]}" href="fonts/{n}" media-type="font/ttf"/>')

    if book.cover and book.cover.is_file():
        files["OEBPS/images/cover.jpg"] = _jpeg(book.cover)
        manifest.append('<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
        files["OEBPS/cover.xhtml"] = xhtml(book.title, f'<section class="cover" epub:type="cover"><img src="images/cover.jpg" alt="{html.escape(book.title)}"/></section>')
        manifest.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover"/>')
        nav.append(("cover.xhtml", ui["cover"]))

    title_body = f'<section epub:type="titlepage"><h1>{html.escape(book.title)}</h1>'
    if book.author:
        title_body += f'<p class="num">{html.escape(book.author)}</p>'
    title_body += "</section>"
    files["OEBPS/title.xhtml"] = xhtml(book.title, title_body)
    manifest.append('<item id="title" href="title.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="title"/>')
    nav.append(("title.xhtml", book.title))

    audio_count = 0
    media_meta: list[str] = []
    overlays, total_audio, overlay_total = 0, 0.0, 0.0
    for p in book.pages:
        n = f"{p.number:02d}"
        body = f'<section epub:type="chapter" id="p{n}">'
        if p.image and p.image.is_file():
            files[f"OEBPS/images/page-{n}.jpg"] = _jpeg(p.image)
            manifest.append(f'<item id="img-{n}" href="images/page-{n}.jpg" media-type="image/jpeg"/>')
            body += f'<figure><img src="images/page-{n}.jpg" alt="{html.escape(_alt(book, p))}"/></figure>'
        overlay = bool(p.audio and p.audio.is_file() and p.audio.suffix == ".mp3" and p.words and p.duration_s > 0)
        ids = f"p{n}w" if overlay else ""
        if overlay:
            audio_count += 1
        body += f'<div class="text">{_word_spans_ids(p.text, p.words, ids) if overlay else _paras(p.text)}</div>'
        if p.audio and p.audio.is_file() and p.audio.suffix == ".mp3":
            files[f"OEBPS/audio/page-{n}.mp3"] = p.audio.read_bytes()
            manifest.append(f'<item id="aud-{n}" href="audio/page-{n}.mp3" media-type="audio/mpeg"/>')
            body += f'<audio controls="controls" src="audio/page-{n}.mp3">{html.escape(ui["listen"])}</audio>'
            # NOTE: no media:duration refines="#aud-NN" - EPUBCheck sums every refined
            # duration as an overlay document and then reports the total as wrong.
            if overlay:
                smil = _smil(n, p.words, p.duration_s, ids)
                files[f"OEBPS/overlay-{n}.smil"] = smil.encode("utf-8")
                manifest.append(f'<item id="ov-{n}" href="overlay-{n}.smil" media-type="application/smil+xml"/>')
                ov_end = _smil_end(p.words, p.duration_s)
                media_meta.append(f'<meta property="media:duration" refines="#ov-{n}">{_smil_clock(ov_end)}</meta>')
                overlays += 1
                total_audio += p.duration_s
                overlay_total += ov_end
        body += f'<p class="num">{_digits(p.number, lang)}</p></section>'
        files[f"OEBPS/page-{n}.xhtml"] = xhtml(f"{book.title} – {p.number}", body)
        mo = f' media-overlay="ov-{n}"' if overlay else ""
        manifest.append(f'<item id="page-{n}" href="page-{n}.xhtml" media-type="application/xhtml+xml"{mo}/>')
        spine.append(f'<itemref idref="page-{n}"/>')
        nav.append((f"page-{n}.xhtml", f"{ui['page']} {_digits(p.number, lang)}"))

    files["OEBPS/end.xhtml"] = xhtml(ui["end"], f'<section epub:type="backmatter"><p class="end">{html.escape(ui["end"])}</p>'
                                     + (f'<div class="text">{_paras(book.blurb)}</div>' if book.blurb else "") + "</section>")
    manifest.append('<item id="end" href="end.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="end"/>')

    nav_items = "\n".join(f'<li><a href="{h}">{html.escape(t)}</a></li>' for h, t in nav)
    files["OEBPS/nav.xhtml"] = xhtml(ui["contents"], f'<nav epub:type="toc" id="toc"><h1>{html.escape(ui["contents"])}</h1><ol>{nav_items}</ol></nav>')
    manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

    media_total = f'<meta property="media:duration">{_smil_clock(overlay_total)}</meta>' if overlays else ""
    media_active = '<meta property="media:active-class">-epub-media-overlay-active</meta>' if overlays else ""
    media_narrator = f'<meta property="media:narrator">{html.escape(book.author or "Bookmaker TTS")}</meta>' if overlays else ""
    creator = f"<dc:creator>{html.escape(book.author)}</dc:creator>" if book.author else ""
    publisher = f"<dc:publisher>{html.escape(book.publisher)}</dc:publisher>" if book.publisher else ""
    desc = f"<dc:description>{html.escape(book.summary[:900])}</dc:description>" if book.summary else ""
    access_modes = '<meta property="schema:accessMode">textual</meta><meta property="schema:accessMode">visual</meta>'
    if audio_count:
        access_modes += '<meta property="schema:accessMode">auditory</meta>'
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="{lang}" dir="{dir_}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{uid}</dc:identifier>
<dc:title>{html.escape(book.title)}</dc:title>
<dc:language>{lang}</dc:language>
{creator}{publisher}{desc}
<meta property="dcterms:modified">{modified}</meta>
{access_modes}
<meta property="schema:accessibilityFeature">alternativeText</meta>
<meta property="schema:accessibilityFeature">readingOrder</meta>
<meta property="schema:accessibilityHazard">none</meta>
<meta property="schema:accessibilitySummary">Picture book: every illustration has a text alternative; the story text is real text.</meta>
<meta name="generator" content="Bookmaker"/>
{chr(10).join(media_meta)}
{media_total}{media_active}{media_narrator}
</metadata>
<manifest>
{chr(10).join(manifest)}
</manifest>
<spine page-progression-direction="{dir_}">
{chr(10).join(spine)}
</spine>
</package>"""
    files["OEBPS/content.opf"] = opf.encode("utf-8")
    container = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)  # first, uncompressed
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name.endswith((".jpg", ".mp3")) else zipfile.ZIP_DEFLATED)
    tmp.replace(out)
    return {"format": "epub", "bytes": out.stat().st_size, "pages": len(book.pages), "audio_pages": audio_count,
            "media_overlays": overlays, "media_duration_s": round(total_audio, 2)}


def epubcheck_available() -> tuple[bool, str]:
    try:
        import epubcheck  # noqa: F401
        import jdk4py  # noqa: F401
        return True, ""
    except ImportError:
        return False, "pip install epubcheck jdk4py (optional: W3C EPUBCheck + a bundled Java runtime)"


def validate_epub(path: Path, timeout: int = 180) -> dict:
    """Run W3C EPUBCheck and summarise its JSON report. Never claims validity when the checker is missing."""
    ok, hint = epubcheck_available()
    if not ok:
        return {"checked": False, "ok": None, "note": hint}
    import epubcheck
    import jdk4py
    jar = Path(epubcheck.__file__).parent / "epubcheck.jar"
    report = path.with_suffix(".epubcheck.json")
    try:
        proc = subprocess.run([str(jdk4py.JAVA), "-jar", str(jar), str(path), "--json", str(report)], capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"checked": False, "ok": None, "note": f"EPUBCheck could not run: {e}"}
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"checked": False, "ok": None, "note": f"EPUBCheck produced no report (exit {proc.returncode}): {proc.stderr[-300:]}"}
    msgs = data.get("messages", [])
    count = lambda sev: sum(1 for m in msgs if m.get("severity") == sev)
    errors, fatal = count("ERROR"), count("FATAL")
    return {"checked": True, "ok": errors == 0 and fatal == 0, "errors": errors + fatal, "warnings": count("WARNING"),
            "checker": data.get("checker", {}).get("checkerVersion"),
            "messages": [{"severity": m.get("severity"), "id": m.get("ID"), "message": m.get("message"),
                          "where": (m.get("locations") or [{}])[0].get("path")} for m in msgs[:25]]}


# ---------------------------------------------------------------------- HTML reader
def build_html(book: BookData, out: Path, embed_audio: bool = True) -> dict:
    ui = book.ui
    lang, dir_ = book.language, ("rtl" if book.rtl else "ltr")
    fam = "Vazirmatn" if book.rtl else "Andika"
    fonts = "".join(
        f"@font-face{{font-family:'{fam}';src:url(data:font/ttf;base64,{base64.b64encode(p.read_bytes()).decode()});font-weight:{'700' if 'Bold' in n else '400'}}}"
        for n, p in _font_files(book))
    sheets = []
    if book.cover and book.cover.is_file():
        sheets.append(f'<section class="sheet cover"><img src="data:image/jpeg;base64,{base64.b64encode(_jpeg(book.cover, 1200)).decode()}" alt="{html.escape(book.title)}"></section>')
    sheets.append(f'<section class="sheet title"><h1>{html.escape(book.title)}</h1><p>{html.escape(book.author)}</p></section>')
    audio_count = 0
    for p in book.pages:
        img = ""
        if p.image and p.image.is_file():
            img = f'<img src="data:image/jpeg;base64,{base64.b64encode(_jpeg(p.image, 1200)).decode()}" alt="{html.escape(_alt(book, p))}">'
        aud = ""
        if embed_audio and p.audio and p.audio.is_file():
            mime = "audio/mpeg" if p.audio.suffix == ".mp3" else "audio/wav"
            aud = f'<audio preload="none" src="data:{mime};base64,{base64.b64encode(p.audio.read_bytes()).decode()}"></audio>'
            audio_count += 1
        body = _word_spans(p.text, p.words) if p.words else _paras(p.text)
        sync = " data-sync=\"1\"" if p.words else ""
        sheets.append(f'<section class="sheet page" data-n="{p.number}"{sync}>{img}<div class="text">{body}</div>{aud}'
                      f'<div class="num">{_digits(p.number, lang)}</div></section>')
    sheets.append(f'<section class="sheet end"><p class="the-end">{html.escape(ui["end"])}</p><div class="text">{_paras(book.blurb)}</div></section>')
    doc = f"""<!DOCTYPE html>
<html lang="{lang}" dir="{dir_}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="generator" content="Bookmaker"><title>{html.escape(book.title)}</title>
<style>{fonts}
*{{box-sizing:border-box}}body{{margin:0;font-family:'{fam}',sans-serif;background:#efe8dc;color:#2b211c;height:100vh;display:flex;flex-direction:column}}
main{{flex:1;display:flex;align-items:center;justify-content:center;perspective:1800px;overflow:hidden}}
.sheet{{display:none;width:min(92vw,760px);max-height:86vh;overflow:auto;background:#fffdf8;border-radius:10px;box-shadow:0 10px 30px #0003;padding:20px;text-align:center}}
.sheet.on{{display:block;animation:turn .45s ease}}
@keyframes turn{{from{{transform:rotateY({'-' if dir_ == 'rtl' else ''}35deg);opacity:.2}}to{{transform:none;opacity:1}}}}
.sheet img{{max-width:100%;max-height:55vh;border-radius:6px}}.cover img{{max-height:80vh}}
.text{{font-size:1.3rem;line-height:{1.9 if book.rtl else 1.5};text-align:start;margin-top:12px}}.text p{{margin:.3em 0}}
.text .hl{{background:#ffe9a8;border-radius:4px}}
.num{{color:#8a7d75;font-size:.85rem}}.the-end{{font-size:2.4rem;margin:20vh 0 2vh}}
nav{{display:flex;gap:8px;justify-content:center;padding:10px}}button{{font:inherit;padding:8px 16px;border-radius:20px;border:1px solid #b9a998;background:#fff;cursor:pointer}}
</style></head><body>
<main id="book">{''.join(sheets)}</main>
<nav><button id="prev">{html.escape(ui['prev'])}</button><button id="read">🔊 {html.escape(ui['read'])}</button>
<button id="next">{html.escape(ui['next'])}</button></nav>
<script>
const S=[...document.querySelectorAll('.sheet')];let i=0,reading=false;const rtl={str(book.rtl).lower()};const LANG='{lang}';
function show(k){{i=Math.max(0,Math.min(S.length-1,k));S.forEach((s,j)=>s.classList.toggle('on',j===i));}}
function stop(){{reading=false;speechSynthesis&&speechSynthesis.cancel();document.querySelectorAll('audio').forEach(a=>{{a.pause();a.currentTime=0}});clearHl();
 document.getElementById('read').textContent='🔊 {html.escape(ui['read'])}';}}
function clearHl(){{document.querySelectorAll('.w.hl').forEach(w=>w.classList.remove('hl'))}}
function hlAt(t){{const s=S[i];let best=null;s.querySelectorAll('.w[data-t]').forEach(w=>{{const a=+w.dataset.t,b=a+ +w.dataset.d;
 if(t>=a&&t<b)best=w}});if(best&&!best.classList.contains('hl')){{clearHl();best.classList.add('hl')}}}}
function readPage(){{if(!reading)return;const s=S[i];const a=s.querySelector('audio');const done=()=>{{clearHl();if(!reading)return;if(i<S.length-1){{show(i+1);setTimeout(readPage,600)}}else stop()}};
 if(a){{a.onended=done;if(s.dataset.sync){{a.ontimeupdate=()=>hlAt(a.currentTime)}}a.play().catch(done);return}}
 const t=s.querySelector('.text');if(!t||!t.innerText.trim()||!window.speechSynthesis){{setTimeout(done,1500);return}}
 const u=new SpeechSynthesisUtterance(t.innerText);u.lang=LANG==='fa'?'fa-IR':'en-US';u.onend=done;u.onerror=done;
 const ws=[...t.querySelectorAll('.w')];let cum=0;const offs=ws.map(w=>{{const o=cum;cum+=w.textContent.length+1;return o}});
 u.onboundary=e=>{{if(e.name!=='word')return;const k=offs.findIndex((o,j)=>e.charIndex>=o&&(j+1>=offs.length||e.charIndex<offs[j+1]));
  if(k>=0){{clearHl();ws[k].classList.add('hl')}}}};speechSynthesis.speak(u);}}
document.getElementById('read').onclick=()=>{{if(reading){{stop();return}}reading=true;document.getElementById('read').textContent='⏹ {html.escape(ui['stop'])}';readPage()}};
document.getElementById('next').onclick=()=>{{stop();show(i+1)}};document.getElementById('prev').onclick=()=>{{stop();show(i-1)}};
addEventListener('keydown',e=>{{if(e.key==='ArrowRight'){{stop();show(i+(rtl?-1:1))}}if(e.key==='ArrowLeft'){{stop();show(i+(rtl?1:-1))}}}});
let x0=null;addEventListener('touchstart',e=>x0=e.touches[0].clientX);addEventListener('touchend',e=>{{if(x0===null)return;const dx=e.changedTouches[0].clientX-x0;
 if(Math.abs(dx)>50){{stop();show(i+((dx<0)!==rtl?1:-1))}}x0=null}});
show(0);
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")
    return {"format": "html", "bytes": out.stat().st_size, "pages": len(book.pages), "audio_pages": audio_count}


def build_images_zip(book: BookData, out: Path) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        if book.cover and book.cover.is_file():
            z.write(book.cover, f"00-cover{book.cover.suffix}")
            n += 1
        for p in book.pages:
            if p.image and p.image.is_file():
                z.write(p.image, f"{p.number:02d}-page{p.image.suffix}")
                n += 1
            if p.audio and p.audio.is_file():
                z.write(p.audio, f"audio/{p.number:02d}-page{p.audio.suffix}")
        z.writestr("text.json", json.dumps({"title": book.title, "language": book.language,
                                            "pages": [{"number": p.number, "text": p.text} for p in book.pages]}, ensure_ascii=False, indent=1))
    return {"format": "images", "bytes": out.stat().st_size, "images": n}


# ---------------------------------------------------------------------- video
def build_video(book: BookData, out: Path, fmt: str = "webm", size=(1280, 720)) -> dict:
    """Read-along video: one shot per page, narration as the soundtrack, the spoken word
    highlighted in the caption. Timings come from the narration engine when it reports
    word events, otherwise from a proportional estimate (reported in the result)."""
    from .video import probe, render_video

    pages = []
    for p in book.pages:
        if not (p.image and p.image.is_file()):
            continue
        pages.append({"number": p.number, "image": str(p.image), "audio": str(p.audio) if p.audio and p.audio.is_file() else None,
                      "text": p.text, "words": p.words, "duration_s": p.duration_s, "rtl": book.rtl})
    if not pages:
        raise ValueError("No page images to put in the video")
    out.parent.mkdir(parents=True, exist_ok=True)
    rep = render_video(pages, out, fmt=fmt, size=size)
    rep["format"] = f"video_{fmt}"
    rep["pages"] = len(pages)
    rep["timing_source"] = next((p.timing_source for p in book.pages if p.timing_source), "")
    rep["probe"] = probe(out)
    return rep
