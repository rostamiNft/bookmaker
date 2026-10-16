"""PDF/X-4 output (opt-in).

What this module does -- and what it does not:

* it converts every colour and every picture to CMYK **through an ICC profile**
  (bundled ISOcoated_v2_300, CC0, or a profile the user points at), adds an
  OutputIntent, XMP metadata with ``pdfxid:GTS_PDFXVersion = PDF/X-4``, a
  TrimBox on every page, ``Trapped /False`` and a document /ID;
* it then runs :func:`check_pdfx4`, a self-check of the mechanical requirements
  (no DeviceRGB, all fonts embedded, OutputIntent + XMP + TrimBox present).

It is *not* a certification: a real preflight also validates ink coverage,
image resolution, transparency flattening rules and font licensing, which needs
a tool such as veraPDF or a commercial preflight. ``check_pdfx4`` says exactly
what it verified, and the report says "self-checked", never "certified".
"""
from __future__ import annotations

import datetime as dt
import io
import uuid
import zlib
from functools import lru_cache
from pathlib import Path

BUNDLED_ICC = Path(__file__).resolve().parents[3] / "assets" / "icc" / "ISOcoated_v2_300_mth.icc"
BUNDLED_NAME = "ISO Coated v2 300% (FOGRA39)"
BUNDLED_ID = "FOGRA39"


class PdfxError(RuntimeError):
    pass


def resolve_icc(profile: str = "") -> tuple[Path, str, str]:
    """(path, OutputConditionIdentifier, human name). Empty profile = bundled CC0 profile."""
    if profile:
        p = Path(profile).expanduser()
        if not p.is_file():
            raise PdfxError(f"ICC profile not found: {profile}")
        return p, "Custom", p.name
    if not BUNDLED_ICC.is_file():
        raise PdfxError(f"Bundled ICC profile missing: {BUNDLED_ICC}")
    return BUNDLED_ICC, BUNDLED_ID, BUNDLED_NAME


# ------------------------------------------------------------------ colour conversion
@lru_cache(maxsize=4)
def _transform(icc_path: str):
    from PIL import ImageCms
    srgb = ImageCms.createProfile("sRGB")
    out = ImageCms.getOpenProfile(icc_path)
    return ImageCms.buildTransform(srgb, out, "RGB", "CMYK",
                                   renderingIntent=ImageCms.Intent.PERCEPTUAL)


@lru_cache(maxsize=4096)
def rgb_to_cmyk_icc(r: float, g: float, b: float, icc_path: str) -> tuple[float, float, float, float]:
    """sRGB (0..1) -> CMYK (0..1) through the output profile (perceptual)."""
    from PIL import Image, ImageCms
    px = Image.new("RGB", (1, 1), (int(round(r * 255)), int(round(g * 255)), int(round(b * 255))))
    cmyk = ImageCms.applyTransform(px, _transform(icc_path))
    c, m, y, k = cmyk.getpixel((0, 0))
    return c / 255, m / 255, y / 255, k / 255


def convert_image_icc(im, icc_path: str):
    """RGB PIL image -> CMYK PIL image through the output profile."""
    from PIL import ImageCms
    return ImageCms.applyTransform(im.convert("RGB"), _transform(icc_path))


def icc_supported() -> bool:
    try:
        from PIL import ImageCms  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ post-processing
def _pdf_date(d: dt.datetime) -> str:
    return d.strftime("D:%Y%m%d%H%M%S") + d.strftime("%z").replace(":", "'")[:3] + "'00'"


def _xmp(title: str, author: str, subject: str, keywords: list[str], language: str,
         created: dt.datetime, doc_id: str, inst_id: str, producer: str) -> bytes:
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    kw = ", ".join(k for k in keywords if k)
    iso = created.isoformat()
    return f'''<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Bookmaker">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
    xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/"
    xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/">
   <dc:format>application/pdf</dc:format>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{esc(title)}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{esc(author)}</rdf:li></rdf:Seq></dc:creator>
   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{esc(subject)}</rdf:li></rdf:Alt></dc:description>
   <dc:language><rdf:Bag><rdf:li>{esc(language)}</rdf:li></rdf:Bag></dc:language>
   <dc:subject><rdf:Bag><rdf:li>{esc(kw)}</rdf:li></rdf:Bag></dc:subject>
   <xmp:CreatorTool>Bookmaker</xmp:CreatorTool>
   <xmp:CreateDate>{iso}</xmp:CreateDate>
   <xmp:ModifyDate>{iso}</xmp:ModifyDate>
   <pdf:Producer>{esc(producer)}</pdf:Producer>
   <pdf:Trapped>False</pdf:Trapped>
   <xmpMM:DocumentID>uuid:{doc_id}</xmpMM:DocumentID>
   <xmpMM:InstanceID>uuid:{inst_id}</xmpMM:InstanceID>
   <xmpMM:OriginalDocumentID>uuid:{doc_id}</xmpMM:OriginalDocumentID>
   <xmpMM:RenditionClass>default</xmpMM:RenditionClass>
   <pdfxid:GTS_PDFXVersion>PDF/X-4</pdfxid:GTS_PDFXVersion>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>
'''.encode("utf-8")


def apply_pdfx(path: str | Path, *, title: str, author: str, subject: str, keywords: list[str],
               language: str, icc_path: str | Path, condition: str, profile_name: str) -> dict:
    """Add the PDF/X-4 structures to an already rendered CMYK PDF (in place)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, ByteStringObject, DictionaryObject, FloatObject, \
        NameObject, NumberObject, StreamObject, TextStringObject

    icc_bytes = Path(icc_path).read_bytes()
    now = dt.datetime.now().astimezone()
    doc_id, inst_id = str(uuid.uuid4()).upper(), str(uuid.uuid4()).upper()
    producer = "Bookmaker (open-source children's book generator)"
    reader = PdfReader(str(path))
    writer = PdfWriter()
    writer.clone_document_from_reader(reader)
    writer._ID = ArrayObject([ByteStringObject(doc_id.encode()),  # noqa: SLF001 - no public setter
                              ByteStringObject(inst_id.encode())])

    icc = StreamObject()
    icc.set_data(zlib.compress(icc_bytes, 9))
    icc.update({NameObject("/Filter"): NameObject("/FlateDecode"),
                NameObject("/N"): NumberObject(4),
                NameObject("/Alternate"): NameObject("/DeviceCMYK"),
                NameObject("/Length1"): NumberObject(len(icc_bytes))})
    icc_ref = writer._add_object(icc)  # noqa: SLF001
    oi = DictionaryObject({NameObject("/Type"): NameObject("/OutputIntent"),
                           NameObject("/S"): NameObject("/GTS_PDFX"),
                           NameObject("/OutputConditionIdentifier"): TextStringObject(condition),
                           NameObject("/Info"): TextStringObject(profile_name),
                           NameObject("/RegistryName"): TextStringObject("http://www.color.org"),
                           NameObject("/DestOutputProfile"): icc_ref})
    oi_ref = writer._add_object(oi)  # noqa: SLF001

    for page in writer.pages:
        mb = page.mediabox
        if "/TrimBox" not in page:  # PDF/X needs a defined trim box on every page
            page[NameObject("/TrimBox")] = ArrayObject([FloatObject(float(mb.left)), FloatObject(float(mb.top)),
                                                        FloatObject(float(mb.right)), FloatObject(float(mb.bottom))])
        ois = page.get("/OutputIntents")
        if ois is None:
            page[NameObject("/OutputIntents")] = ArrayObject([oi_ref])
        else:
            ois.append(oi_ref)
    # catalog level: some preflight tools look here first
    cat = writer._root_object  # noqa: SLF001
    cat[NameObject("/OutputIntents")] = ArrayObject([oi_ref])

    meta = StreamObject()
    meta.set_data(_xmp(title, author, subject, keywords, language, now, doc_id, inst_id, producer))
    meta.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    cat[NameObject("/Metadata")] = writer._add_object(meta)  # noqa: SLF001

    info = {"/Title": title, "/Author": author, "/Subject": subject[:500], "/Creator": "Bookmaker",
            "/Producer": producer, "/Keywords": ", ".join(k for k in keywords if k),
            "/CreationDate": _pdf_date(now), "/ModDate": _pdf_date(now), "/Trapped": NameObject("/False")}
    writer.add_metadata(info)
    for k, v in info.items():
        if k == "/Trapped":
            writer._info.get_object()[NameObject("/Trapped")] = NameObject("/False")  # noqa: SLF001
    # remove the Date key pypdf adds from its own metadata handling (invalid in PDF)
    writer._info.get_object().pop(NameObject("/Date"), None)  # noqa: SLF001
    writer._info.get_object().pop(NameObject("/CreationDate"), None)  # noqa: SLF001
    writer._info.get_object().update({NameObject("/CreationDate"): TextStringObject(_pdf_date(now)),
                                      NameObject("/ModDate"): TextStringObject(_pdf_date(now))})

    buf = io.BytesIO()
    writer.write(buf)
    Path(path).write_bytes(buf.getvalue().replace(b"%PDF-1.", b"%PDF-1.6", 1) if buf.getvalue().startswith(b"%PDF-1.")
                           else buf.getvalue())
    return {"standard": "PDF/X-4", "output_condition": condition, "icc": Path(icc_path).name,
            "icc_bytes": len(icc_bytes), "document_id": doc_id}


# ------------------------------------------------------------------ self-check
def check_pdfx4(path: str | Path) -> dict:
    """Mechanical PDF/X-4 self-check (see module docstring: not a certification)."""
    import re
    from pypdf import PdfReader
    issues, info = [], {}
    r = PdfReader(str(path))
    raw = Path(path).read_bytes()
    info["version"] = raw[:8].decode("latin-1", "replace")
    if not info["version"].startswith("%PDF-1.6") and b"1.6" not in raw[:16]:
        issues.append({"code": "pdfx_version", "message": f"PDF header is {info['version']}, PDF/X-4 needs 1.6"})
    cat = r.trailer["/Root"]
    xmp = cat.get("/Metadata")
    if xmp is None:
        issues.append({"code": "pdfx_xmp_missing", "message": "No XMP metadata packet"})
    else:
        data = xmp.get_data().decode("utf-8", "replace")
        if "PDF/X-4" not in data:
            issues.append({"code": "pdfx_xmp_version", "message": "XMP does not declare GTS_PDFXVersion PDF/X-4"})
        info["xmp_bytes"] = len(data)
    intents = cat.get("/OutputIntents")
    if not intents:
        issues.append({"code": "pdfx_outputintent_missing", "message": "No /OutputIntent in the catalog"})
    else:
        oi = intents[0].get_object()
        prof = oi.get("/DestOutputProfile")
        n = prof.get_object().get("/N") if prof is not None else None
        info["output_condition"] = str(oi.get("/OutputConditionIdentifier", ""))
        info["output_profile_components"] = n
        if n != 4:
            issues.append({"code": "pdfx_outputintent_profile", "message": "DestOutputProfile is not a 4-component (CMYK) profile"})
    for i, page in enumerate(r.pages, 1):
        if "/TrimBox" not in page:
            issues.append({"code": "pdfx_trimbox_missing", "message": f"Page {i} has no TrimBox", "page": i})
        res = page.get("/Resources", {})
        for f in (res.get("/Font") or {}).values():
            fo = f.get_object()
            fd = fo.get("/FontDescriptor")
            fd = fd.get_object() if fd is not None else {}
            if not any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                issues.append({"code": "pdfx_font_not_embedded", "message": f"Page {i}: font {fo.get('/BaseFont')} is not embedded", "page": i})
        for xo in (res.get("/XObject") or {}).values():
            x = xo.get_object()
            cs = x.get("/ColorSpace")
            if cs is not None and str(cs) == "/DeviceRGB":
                issues.append({"code": "pdfx_rgb_image", "message": f"Page {i}: image uses DeviceRGB", "page": i})
    # content streams must not paint in DeviceRGB
    rgb_ops = 0
    for i, page in enumerate(r.pages, 1):
        data = page.get_contents()
        blob = data.get_data() if data is not None else b""
        rgb_ops += len(re.findall(rb"(\d|\s)\.?\d*\s+\.?\d*\s+\.?\d*\s+(rg|RG)\b", blob))
    info["rgb_operators"] = rgb_ops
    if rgb_ops:
        issues.append({"code": "pdfx_rgb_operator", "message": f"{rgb_ops} DeviceRGB colour operators in page content"})
    info["pages_checked"] = len(r.pages)
    return {"ok": not issues, "issues": issues, "info": info}
