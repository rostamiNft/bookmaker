"""Independent PDF validation: re-open the file, inspect structure and render
every page. Nothing is assumed from the fact that rendering 'succeeded'."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import ImageStat
from pypdf import PdfReader


def _fonts_on_page(page) -> list[dict]:
    out = []
    res = page.get("/Resources") or {}
    fonts = res.get("/Font") or {}
    for _, ref in fonts.items():
        f = ref.get_object()
        base = str(f.get("/BaseFont"))
        embedded = False
        desc = f.get("/FontDescriptor")
        if desc is None and f.get("/DescendantFonts"):
            desc = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
        if desc is not None:
            d = desc.get_object()
            embedded = any(k in d for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        out.append({"name": base, "embedded": embedded, "has_tounicode": "/ToUnicode" in f})
    return out


def _images(page) -> list:
    res = page.get("/Resources") or {}
    xo = res.get("/XObject") or {}
    return [ref.get_object() for _, ref in xo.items() if ref.get_object().get("/Subtype") == "/Image"]


def _image_count(page) -> int:
    return len(_images(page))


def validate_pdf(path: str | Path, *, expected_pages: int, expected_size: tuple[float, float], page_kinds: list[str],
                 image_pages: list[int], title: str, language: str, ltr_samples: dict[int, str] | None = None,
                 render_dir: str | Path | None = None, max_mb: float = 150, trim: tuple[float, float, float] | None = None,
                 bleed_pt: float = 0.0, color_mode: str = "rgb", rtl_samples: dict[int, str] | None = None) -> dict:
    """trim=(W, H, offset): expect TrimBox at offset inside the media box (bleed/crop-mark output).
    rtl_samples: logical Persian text expected to be extractable (via ActualText) per page."""
    issues: list[dict] = []
    info: dict = {}

    def add(code, msg, page=None, sev="error"):
        issues.append({"code": code, "severity": sev, "message": msg, "page": page, "stage": "pdf"})

    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        add("missing", "PDF file missing or empty")
        return {"ok": False, "issues": issues, "info": info}
    size_mb = p.stat().st_size / 1e6
    info["size_mb"] = round(size_mb, 2)
    with p.open("rb") as fh:
        if fh.read(5) != b"%PDF-":
            add("bad_header", "File does not start with %PDF-")
    if size_mb > max_mb:
        add("too_large", f"PDF is {size_mb:.1f} MB (limit {max_mb} MB)")
    if size_mb < 0.01 * expected_pages:
        add("too_small", f"PDF is suspiciously small ({size_mb:.2f} MB)", sev="warning")
    try:
        r = PdfReader(str(p), strict=True)
    except Exception as e:
        add("unreadable", f"pypdf cannot parse the file: {e}")
        return {"ok": False, "issues": issues, "info": info}
    n = len(r.pages)
    info["pages"] = n
    if n != expected_pages:
        add("page_count", f"PDF has {n} pages, expected {expected_pages}")
    meta = r.metadata or {}
    info["metadata"] = {k: str(v) for k, v in meta.items()}
    if (meta.get("/Title") or "") != title:
        add("metadata_title", "Title metadata missing or wrong", sev="warning")
    lang = r.trailer["/Root"].get("/Lang")
    info["lang"] = str(lang) if lang else None
    if not lang:
        add("metadata_lang", "Document language not set", sev="warning")
    all_fonts = {}
    for i, page in enumerate(r.pages, start=1):
        mb = page.mediabox
        w, h = float(mb.width), float(mb.height)
        if abs(w - expected_size[0]) > 0.5 or abs(h - expected_size[1]) > 0.5:
            add("page_size", f"Page {i} is {w:.1f}x{h:.1f}pt, expected {expected_size[0]:.1f}x{expected_size[1]:.1f}", i)
        for f in _fonts_on_page(page):
            all_fonts[f["name"]] = f
            if not f["embedded"]:
                add("font_not_embedded", f"Font {f['name']} is not embedded", i)
        imgs = _images(page)
        if i in image_pages and not imgs:
            add("image_missing", f"Page {i} should contain an illustration but has none", i)
        if color_mode == "cmyk":
            for im in imgs:
                if str(im.get("/ColorSpace")) != "/DeviceCMYK":
                    add("not_cmyk", f"Page {i} has an image in {im.get('/ColorSpace')} (CMYK expected)", i)
        if trim:
            tb = page.trimbox
            want = (trim[2], trim[2], trim[2] + trim[0], trim[2] + trim[1])
            got = (float(tb.left), float(tb.bottom), float(tb.right), float(tb.top))
            if any(abs(a - b_) > 0.5 for a, b_ in zip(got, want)):
                add("trim_box", f"Page {i} TrimBox {tuple(round(v, 1) for v in got)} != expected {tuple(round(v, 1) for v in want)}", i)
            if bleed_pt > 0:
                bb = page.bleedbox
                if abs(float(bb.width) - (trim[0] + 2 * bleed_pt)) > 0.5:
                    add("bleed_box", f"Page {i} BleedBox width {float(bb.width):.1f} != trim + 2×bleed", i)
        if ltr_samples and i in ltr_samples:
            txt = (page.extract_text() or "").replace("\n", " ")
            words = [w for w in ltr_samples[i].split()[:4]]
            if not all(w.strip(".,!?'\"") in txt for w in words):
                add("text_not_extractable", f"Expected text not extractable on page {i}", i, sev="warning")
    info["fonts"] = list(all_fonts.values())
    if rtl_samples:
        # (1) a standard extractor (pypdf) returns the logical text; (2) every page carries
        # ActualText spans with the exact logical text (used by Acrobat/poppler-based tools;
        # pdfium-based viewers such as Chrome ignore ActualText and fall back to glyph order).
        import re
        import unicodedata
        norm = lambda t: "".join(unicodedata.normalize("NFKC", t).split()).replace("\u200c", "")
        info["rtl_extract_checked"], info["actualtext_spans"] = 0, 0
        for pno, want in rtl_samples.items():
            if not (1 <= pno <= n):
                continue
            page = r.pages[pno - 1]
            cs = page.get_contents()  # raw stream, read before extract_text re-parses it
            data = cs.get_data() if cs is not None else b""
            got = page.extract_text() or ""
            words = [w for w in want.split()[:3] if w]
            if not all(norm(w) in norm(got) for w in words):
                add("text_not_extractable", f"Persian text on page {pno} does not extract in logical order", pno, sev="warning")
            spans = [bytes.fromhex(h.decode()).decode("utf-16-be").lstrip("\ufeff") for h in re.findall(rb"/ActualText\s*<([0-9A-Fa-f]+)>", data)]
            info["actualtext_spans"] += len(spans)
            if not any(want.strip() == sp.strip() for sp in spans):
                add("actualtext_missing", f"Page {pno}: no ActualText span with the logical text of the first line", pno, sev="warning")
            info["rtl_extract_checked"] += 1

    # render every page: catches pages that fail to rasterise, blank and duplicate pages
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(p))
        hashes = []
        info["rendered"] = 0
        for i in range(len(doc)):
            pg = doc[i]
            bmp = pg.render(scale=72 / 72 * 1.0)
            im = bmp.to_pil().convert("RGB")
            stat = ImageStat.Stat(im.convert("L"))
            if stat.stddev[0] < 2.0:
                add("blank_page", f"Page {i + 1} renders blank", i + 1)
            hashes.append(hashlib.md5(im.tobytes()).hexdigest())
            if render_dir:
                Path(render_dir).mkdir(parents=True, exist_ok=True)
                big = pg.render(scale=110 / 72).to_pil().convert("RGB")
                big.save(Path(render_dir) / f"page-{i + 1:03d}.jpg", quality=85)
                big.thumbnail((240, 240))
                big.save(Path(render_dir) / f"thumb-{i + 1:03d}.jpg", quality=80)
            info["rendered"] += 1
        for i in range(1, len(hashes)):
            if hashes[i] == hashes[i - 1]:
                add("duplicate_page", f"Page {i + 1} is identical to page {i}", i + 1)
        doc.close()
    except ImportError:
        add("render_unavailable", "pypdfium2 not installed; pages were not rendered", sev="warning")
    except Exception as e:
        add("render_failed", f"Rendering failed: {e}")
    errors = [x for x in issues if x["severity"] == "error"]
    return {"ok": not errors, "issues": issues, "info": info}
