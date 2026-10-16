#!/usr/bin/env python3
"""Regenerate the sample books in samples/ through the real HTTP API.

Runs the whole pipeline (story -> Character Bible -> illustrations -> layout -> PDF)
with the built-in engines, then exports EPUB/HTML/video and writes, for every book:

    samples/<name>.pdf          the finished book
    samples/<name>-report.json  the project as the API returns it (QA report included)
    samples/<name>-pages.jpg    a contact sheet rendered from the PDF with pdfium
    samples/<name>.epub         (narrated books only) EPUB 3 with media overlays

Usage:  cd backend && ../.venv/bin/python ../scripts/make_samples.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.pipeline.worker import Worker  # noqa: E402

SAMPLES = ROOT / "samples"


def contact_sheet(pdf_bytes: bytes, out: Path, cols: int = 5, width: int = 300) -> int:
    """Grid of all pages, rendered with pdfium (the same reader the app uses)."""
    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(pdf_bytes)
    ims = [doc[i].render(scale=width / max(1, doc[i].get_size()[0])).to_pil().convert("RGB") for i in range(len(doc))]
    doc.close()
    rows = (len(ims) + cols - 1) // cols
    cw, ch = max(i.width for i in ims), max(i.height for i in ims)
    sheet = Image.new("RGB", (cols * cw, rows * ch), (250, 247, 242))
    for n, im in enumerate(ims):
        sheet.paste(im, ((n % cols) * cw, (n // cols) * ch))
    sheet.save(out, quality=82)
    return len(ims)


def build(c, w, name: str, settings: dict, exports: tuple[str, ...] = ()) -> dict:
    t0 = time.monotonic()
    r = c.post("/api/projects", json={"settings": settings, "start": True})
    if r.status_code != 201:
        raise SystemExit(f"{name}: create failed {r.status_code}: {r.text[:300]}")
    pid = r.json()["id"]
    if not w.drain(900):
        raise SystemExit(f"{name}: pipeline did not finish")
    d = c.get(f"/api/projects/{pid}").json()
    if d["status"] not in ("completed", "completed_with_warnings"):
        raise SystemExit(f"{name}: status is {d['status']}: {d.get('error')}")
    pdf = c.get(f"/api/projects/{pid}/pdf").content
    (SAMPLES / f"{name}.pdf").write_bytes(pdf)
    contact_sheet(pdf, SAMPLES / f"{name}-pages.jpg")
    (SAMPLES / f"{name}-report.json").write_text(json.dumps(
        {k: d[k] for k in ("title", "settings", "qa_report", "pages", "characters")}, ensure_ascii=False, indent=1))
    qa = (d.get("qa_report") or {}).get("summary") or {}
    print(f"  {name}.pdf: {len(pdf)/1e6:.2f} MB, {len(d['pages'])} story pages, "
          f"QA errors={qa.get('errors')} warnings={qa.get('warnings')}, valid={qa.get('pdf_valid')}")
    for fmt in exports:
        t = time.monotonic()
        r = c.post(f"/api/projects/{pid}/exports", json={"format": fmt})
        if r.status_code not in (200, 202):
            print(f"  ! {name}: export {fmt} failed {r.status_code}: {r.text[:200]}")
            continue
        info = r.json()
        ext = {"epub": "epub", "html": "html", "video": "webm"}.get(fmt, fmt)
        data = c.get(f"/api/projects/{pid}/exports/{fmt}").content
        (SAMPLES / f"{name}.{ext}").write_bytes(data)
        extra = ""
        if fmt == "epub":
            v = info.get("validation") or {}
            extra = f", EPUBCheck {v.get('checker')} {'✓' if v.get('ok') else '✖'} ({v.get('errors')} errors)"
            if info.get("media_overlays"):
                extra += f", {info['media_overlays']} media overlays ({info.get('media_duration_s', 0):.0f}s)"
        if fmt == "video":
            extra = (f", {info.get('duration_s')}s, {info.get('codec_video')}+{info.get('codec_audio')}, "
                     f"timing={info.get('timing_source')}")
        print(f"  {name}.{ext}: {len(data)/1e6:.2f} MB{extra} ({time.monotonic()-t:.0f}s)")
    print(f"  total {time.monotonic()-t0:.0f}s")
    return d


def main() -> None:
    """Pass sample names on the command line to regenerate only those."""
    only = set(sys.argv[1:])
    SAMPLES.mkdir(exist_ok=True)
    base_fa = {"language": "fa", "page_count": 14, "age_group": "6-8", "illustration_style": "watercolor",
               "author": "کتاب‌ساز", "publisher": "نشر نمونه", "aspect_ratio": "4:3", "page_size": "A4",
               "topic": "یک ستاره‌ی کوچک از آسمان می‌افتد و باید پیش از صبح به خانه برگردد", "seed": 21,
               "humor_level": 2}
    base_en = {"language": "en", "page_count": 14, "age_group": "6-8", "illustration_style": "gouache",
               "author": "Bookmaker", "publisher": "Sample Press", "aspect_ratio": "4:3", "page_size": "A4",
               "topic": "a girl who follows a stream that has stopped singing", "seed": 33}

    with TestClient(app) as c:
        w = Worker(max_books=1)
        if not only or "sample-fa-lost-star" in only:
            print("Persian book with narration (PDF + EPUB with media overlays + video):")
            build(c, w, "sample-fa-lost-star", {**base_fa, "narration_audio": True}, ("epub", "video"))
        if not only or "sample-en-silent-stream" in only:
            print("English book:")
            build(c, w, "sample-en-silent-stream", base_en)
        if not only or "sample-fa-print-fullbleed-cmyk" in only:
            print("Persian print edition (full-bleed, 3 mm bleed, crop marks, CMYK):")
            build(c, w, "sample-fa-print-fullbleed-cmyk",
                  {**base_fa, "layout_style": "full_bleed", "bleed_mm": 3, "crop_marks": True, "color_mode": "cmyk",
                   "aspect_ratio": "3:4", "output_profile": "screen"})
        if not only or "sample-fa-print-pdfx4" in only:
            print("Persian PDF/X-4 edition (ICC-based CMYK, output intent):")
            build(c, w, "sample-fa-print-pdfx4",
                  {**base_fa, "print_standard": "pdfx4", "aspect_ratio": "3:4", "page_size": "A5",
                   "output_profile": "screen"})
        if not only or "sample-fa-speech-bubbles" in only:
            print("Persian book with speech bubbles inside the art:")
            build(c, w, "sample-fa-speech-bubbles",
                  {**base_fa, "speech_bubbles": True, "text_amount": "short", "layout_style": "full_bleed",
                   "aspect_ratio": "3:4"})


if __name__ == "__main__":
    main()
