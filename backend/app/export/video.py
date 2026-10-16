"""Read-along video export (PyAV / FFmpeg).

Every page becomes one shot: the illustration, the page text as a caption, and the
word currently being spoken highlighted (karaoke) from the narration timings. Audio
is the concatenation of the per-page narration, re-encoded to Opus.

Container/codec choice is deliberate and licence-driven:

* **WebM (VP9 + Opus)** is the default: both codecs are royalty-free and the bundled
  FFmpeg build ships them.
* **MP4 (H.264 + AAC)** is offered for compatibility. The bundled FFmpeg is built with
  ``--enable-version3`` on an LGPL base but links **libx264, which is GPL-2+**; choosing
  MP4 therefore produces a binary that is subject to the GPL. The UI and the API say so
  before anything is written, and nothing is enabled by default.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from fractions import Fraction

from PIL import Image, ImageDraw, ImageFont

DEFAULT_SIZE = (1280, 720)
FPS = 24
LEAD_S = 0.35          # silence before each page's narration
TAIL_S = 0.45          # silence after it


class VideoError(RuntimeError):
    pass


def video_available() -> bool:
    """True when PyAV (and therefore FFmpeg) is installed for the video export."""
    try:
        import av  # noqa: F401
        return True
    except Exception:
        return False


def ffmpeg_info() -> dict:
    """Which encoders the bundled FFmpeg actually offers (checked, never assumed)."""
    import av
    out = {"av_version": av.__version__, "ffmpeg": av.library_versions.get("libavcodec", ""),
           "video": {}, "audio": {}}
    for name in ("libvpx-vp9", "libx264", "h264", "mpeg4", "libsvtav1"):
        try:
            c = av.codec.Codec(name, "w")
            out["video"][name] = c.name
        except Exception:
            pass
    for name in ("libopus", "aac", "mp3", "flac"):
        try:
            c = av.codec.Codec(name, "w")
            out["audio"][name] = c.name
        except Exception:
            pass
    out["gpl"] = "libx264" in out["video"]
    return out


def _font(size: int, rtl: bool = False):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    from app.layout.text import FONT_FILES, register_fonts
    register_fonts()
    import importlib
    mod = importlib.import_module("app.layout.text")
    base = Path(mod.__file__).parent
    for rel in FONT_FILES.get("fa" if rtl else "en", []):
        f = base / "fonts" / rel if not (base / rel).exists() else base / rel
        cand = [f, base.parent.parent.parent / "assets" / "fonts" / Path(rel).name]
        for c in cand:
            if c.exists():
                return ImageFont.truetype(str(c), size)
    return ImageFont.load_default()


def _fit_image(im: Image.Image, box: tuple[int, int], bg=(250, 247, 242)) -> Image.Image:
    bw, bh = box
    im = im.convert("RGB")
    r = min(bw / im.width, bh / im.height)
    nw, nh = max(1, int(im.width * r)), max(1, int(im.height * r))
    im = im.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (bw, bh), bg)
    canvas.paste(im, ((bw - nw) // 2, (bh - nh) // 2))
    return canvas


def _wrap(draw, text, font, max_w):  # kept for tests/legacy callers
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _dir_of(tok: str) -> bool:
    """True when the token is right-to-left.

    Tested on the *logical* text: after shaping, Arabic letters live in the presentation
    forms block (U+FE70..U+FEFF / U+FB50..U+FDFF), which is outside the Arabic blocks.
    """
    return any("\u0590" <= ch <= "\u08ff" or "\ufb1d" <= ch <= "\ufdff" or "\ufe70" <= ch <= "\ufeff" for ch in tok)


def _visual_words(text: str, rtl: bool) -> list[tuple[int, str]]:
    """(logical word index, visual form) in *visual* (drawing) order.

    Each word is shaped on its own (Persian/Arabic contextual forms), then whole
    same-direction groups are reordered - the standard bidi rule at word granularity.
    Keeping the logical index next to the drawn word is what makes the karaoke
    highlight work in both directions: the timings are in spoken (logical) order.
    """
    from app.layout.text import shape
    items = [(i, shape(t, rtl) or t, _dir_of(t)) for i, t in enumerate(text.split())]
    if not rtl:
        return [(i, v) for i, v, _d in items]
    groups: list[tuple[bool, list]] = []
    for it in items:
        d = it[2]
        if not groups or groups[-1][0] != d:
            groups.append((d, []))
        groups[-1][1].append(it)          # words inside a group keep their logical order
    out: list[tuple[int, str]] = []
    for _d, grp in reversed(groups):      # groups are laid out right-to-left
        out.extend((i, v) for i, v, _d2 in grp)
    return out


def _caption_lines(d, text: str, font, max_w: float, rtl: bool) -> list[list[tuple[int, str, float]]]:
    """Wrap into lines of (logical index, visual word, width)."""
    space = d.textlength(" ", font=font)
    lines: list[list[tuple[int, str, float]]] = []
    cur: list[tuple[int, str, float]] = []
    w = 0.0
    for idx, vis in _visual_words(text, rtl):
        wd = d.textlength(vis, font=font)
        add = wd if not cur else w + space + wd
        if cur and add > max_w:
            lines.append(cur)
            cur, w = [(idx, vis, wd)], wd
        else:
            cur.append((idx, vis, wd))
            w = add
    if cur:
        lines.append(cur)
    return lines


def _caption_frame(base: Image.Image, text: str, timings: list[dict], t: float, rtl: bool, size):
    """Compose one frame: picture + caption box with the current word highlighted."""
    W, H = size
    im = base.copy()
    d = ImageDraw.Draw(im)
    if not text:
        return im
    font_size = max(20, int(H * 0.045))
    font = _font(font_size, rtl)
    pad = int(H * 0.03)
    max_w = W - 2 * pad - 40
    lines = _caption_lines(d, text, font, max_w, rtl)
    lh = int(font_size * 1.5)
    box_h = min(int(H * 0.32), lh * len(lines) + 2 * pad)
    top = H - box_h - int(H * 0.02)
    d.rounded_rectangle([pad, top, W - pad, top + box_h], radius=18, fill=(255, 253, 248))
    # highlight the word being spoken (timings are in spoken/logical order)
    cur = None
    for i, w in enumerate(timings or []):
        if w["t"] <= t < w["t"] + w["d"]:
            cur = i
            break
    if cur is None and timings and t >= timings[-1]["t"]:
        cur = len(timings) - 1
    space = d.textlength(" ", font=font)
    y = top + pad
    for line in lines:
        total = sum(wd for _i, _v, wd in line) + space * (len(line) - 1)
        # RTL: right-aligned, words advance leftwards. LTR: left-aligned centred block.
        x = (W + total) / 2 if rtl else (W - total) / 2
        for idx, vis, wd in line:
            px = x - wd if rtl else x
            if idx == cur:
                d.rounded_rectangle([px - 5, y - 3, px + wd + 5, y + font_size + 8], radius=7, fill=(255, 226, 130))
            d.text((px, y), vis, font=font, fill=(32, 28, 26))
            x = px - space if rtl else px + wd + space
        y += lh
    return im


def render_video(pages: list[dict], out_path: str | Path, *, fmt: str = "webm", size=DEFAULT_SIZE,
                 fps: int = FPS, progress=None) -> dict:
    """pages: [{image, audio, text, words, title}] -> video file + report.

    ``words`` may be empty (no narration): the page then gets a fixed 3 s shot."""
    import av
    import numpy as np

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = (fmt or "webm").lower()
    if fmt == "webm":
        vcodec, acodec, container = "libvpx-vp9", "libopus", "webm"
    elif fmt == "mp4":
        vcodec, acodec, container = "libx264", "aac", "mp4"
    else:
        raise VideoError(f"Unsupported container: {fmt}")
    if not pages:
        raise VideoError("Nothing to export: no pages")

    W, H = size
    container_ = av.open(str(out_path), mode="w", format=container)
    vstream = container_.add_stream(vcodec, rate=fps)
    vstream.width, vstream.height = W, H
    vstream.pix_fmt = "yuv420p"
    if vcodec == "libvpx-vp9":
        vstream.options = {"crf": "32", "b:v": "0", "deadline": "good", "cpu-used": "4", "row-mt": "1"}
    else:
        vstream.options = {"crf": "23", "preset": "veryfast", "pix_fmt": "yuv420p"}
    astream = container_.add_stream(acodec, rate=48000)
    astream.layout = "stereo" if acodec == "aac" else "stereo"
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)

    def flush_audio(q):
        for f in q:
            for p in astream.encode(f):
                container_.mux(p)

    def silence(seconds: float):
        """Zeroed frames: keeps the narration aligned with the shots (lead-in / tail)."""
        n = int(max(0.0, seconds) * 48000)
        while n > 0:
            k = min(1024, n)
            f = av.AudioFrame(format="fltp", layout="stereo", samples=k)
            f.sample_rate = 48000
            f.time_base = Fraction(1, 48000)
            for plane in f.planes:
                plane.update(bytes(plane.buffer_size))
            flush_audio([f])
            n -= k

    total = len(pages)
    report = {"pages": [], "duration_s": 0.0, "codec_video": vcodec, "codec_audio": acodec,
              "container": container, "size": [W, H], "fps": fps, "gpl_codecs": vcodec == "libx264"}
    clock = 0.0
    audio_q = []
    for i, pg in enumerate(pages):
        dur = float(pg.get("duration_s") or 0)
        timings = pg.get("words") or []
        if not dur:
            dur = 3.0
        shot = LEAD_S + dur + TAIL_S
        with Image.open(pg["image"]) as raw:
            base = _fit_image(raw, (W, H - int(H * 0.30)))
        base = base.crop((0, 0, W, H - int(H * 0.30)))
        canvas_bg = Image.new("RGB", (W, H), (250, 247, 242))
        canvas_bg.paste(base, (0, 0))
        rtl = bool(pg.get("rtl"))
        n = max(1, int(round(shot * fps)))
        for fi in range(n):
            t = fi / fps
            frame = _caption_frame(canvas_bg, pg.get("text", ""), timings, t - LEAD_S, rtl, (W, H))
            vf = av.VideoFrame.from_ndarray(np.asarray(frame), format="rgb24")
            for p in vstream.encode(vf):
                container_.mux(p)
        # audio: lead-in silence keeps the words aligned with the caption highlight
        silence(LEAD_S)
        ap = pg.get("audio")
        if ap and Path(ap).exists():
            with av.open(str(ap)) as inp:
                in_stream = inp.streams.audio[0]
                for frame in inp.decode(in_stream):
                    frame.pts = None
                    out = resampler.resample(frame)
                    for f in (out if isinstance(out, list) else [out]):
                        if f is not None:
                            audio_q.append(f)
                            if len(audio_q) > 4000:
                                flush_audio(audio_q)
                                audio_q = []
            flush_audio(audio_q)
            audio_q = []
        silence(TAIL_S)   # keep the last word on screen for a beat
        report["pages"].append({"page": pg.get("number", i + 1), "audio_s": round(dur, 2), "shot_s": round(shot, 2),
                                "words": len(timings), "start_s": round(clock, 2)})
        clock += shot
        if progress:
            progress((i + 1) / total)
    flush_audio(audio_q)
    for p in astream.encode(None):
        container_.mux(p)
    for p in vstream.encode(None):
        container_.mux(p)
    container_.close()
    report["duration_s"] = round(clock, 2)
    report["bytes"] = out_path.stat().st_size
    report["file"] = str(out_path)
    return report


def probe(path: str | Path) -> dict:
    """Read the result back with FFmpeg (duration, streams, codecs) - used by QA."""
    import av
    info: dict = {"path": str(path), "bytes": Path(path).stat().st_size}
    with av.open(str(path)) as c:
        info["duration_s"] = round(float(c.duration or 0) / 1_000_000, 2)
        info["streams"] = []
        for s in c.streams:
            if s.type == "video":
                n = s.frames or sum(1 for _ in c.demux(s))
                info["streams"].append({"type": "video", "codec": s.codec_context.name, "width": s.codec_context.width,
                                        "height": s.codec_context.height, "frames": n,
                                        "fps": float(s.average_rate) if s.average_rate else 0})
            elif s.type == "audio":
                info["streams"].append({"type": "audio", "codec": s.codec_context.name,
                                        "rate": s.codec_context.sample_rate, "channels": s.codec_context.channels})
    return info


def has_audio(path: str | Path) -> float:
    """Mean absolute amplitude of the decoded audio (0 = silent track)."""
    import av
    import numpy as np
    with av.open(str(path)) as c:
        if not c.streams.audio:
            return 0.0
        s = c.streams.audio[0]
        res = av.AudioResampler(format="s16", layout="mono", rate=16000)
        tot, n = 0.0, 0
        for frame in c.decode(s):
            for f in (res.resample(frame) or []):
                if f is None:
                    continue
                a = np.abs(f.to_ndarray().astype("float32")).mean()
                tot += a
                n += 1
        return float(tot / max(1, n)) / 32768.0
