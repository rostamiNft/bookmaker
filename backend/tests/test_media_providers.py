"""Inpainting / CPU-diffusion / TTS adapters against real HTTP mock servers that follow
the documented protocols (FastSD CPU /api/generate, IOPaint /api/v1/inpaint,
A1111 /sdapi/v1/img2img, OpenAI /v1/images/edits + /v1/audio/speech, ComfyUI)."""
import base64
import io
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageDraw

from app.illustration.inpaint import MaskError, inpaint as classical_inpaint, load_mask, mask_png, validate_mask
from app.providers.base import ProviderError
from app.providers.image_http import Automatic1111Image, ComfyUIImage, FastSDCPUImage, IOPaintInpaint, OpenAICompatImage
from app.providers.tts import EspeakTTS, OpenAICompatTTS, audio_duration, encode_mp3, encode_wav

CALLS = []
media = FastAPI()


def _png(w, h, color=(200, 120, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 140, 200)).save(buf, format="JPEG")
    return buf.getvalue()


@media.get("/api/info")
def fs_info():
    return {"device_type": "cpu", "device_name": "Mock CPU", "os": "Linux", "platform": "x86_64", "processor": "mock"}


@media.get("/api/models")
def fs_models():
    return {"lcm_models": ["stabilityai/sd-turbo", "rupeshs/sdxs-512-0.9"], "openvino_models": []}


@media.post("/api/generate")
async def fs_generate(request: Request):
    b = await request.json()
    CALLS.append(("fastsd", b))
    if b.get("prompt") == "fail":
        return {"images": [], "latency": 0, "error": "model not loaded"}
    return {"images": [base64.b64encode(_jpeg(b["image_width"], b["image_height"])).decode()], "latency": 1.7, "error": ""}


@media.get("/api/v1/server-config")
def io_config():
    return {"plugins": [{"name": "RemoveBG"}], "modelInfos": [{"name": "lama"}]}


@media.get("/api/v1/model")
def io_model():
    return {"name": "lama", "is_erase_model": True}


@media.post("/api/v1/inpaint")
async def io_inpaint(request: Request):
    b = await request.json()
    img = Image.open(io.BytesIO(base64.b64decode(b["image"]))).convert("RGB")
    mask = Image.open(io.BytesIO(base64.b64decode(b["mask"]))).convert("L")
    CALLS.append(("iopaint", img.size, mask.size, mask.getextrema()))
    if img.size != mask.size:
        return JSONResponse({"error": "size mismatch"}, status_code=422)
    out = img.copy()
    out.paste((10, 200, 10), mask=mask)  # "repair" = fill the white area
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"X-Seed": "42"})


@media.post("/sdapi/v1/img2img")
async def a1111_img2img(request: Request):
    b = await request.json()
    m = Image.open(io.BytesIO(base64.b64decode(b["mask"]))).convert("L")
    CALLS.append(("img2img", b["width"], b["height"], m.getextrema(), b["denoising_strength"]))
    return {"images": [base64.b64encode(_png(b["width"], b["height"], (5, 5, 250))).decode()], "parameters": {}, "info": "{}"}


@media.post("/v1/audio/speech")
async def speech(request: Request):
    b = await request.json()
    CALLS.append(("speech", b["voice"], b["input"], b["response_format"]))
    if request.headers.get("authorization") != "Bearer tts-key":
        return JSONResponse({"error": "invalid key"}, status_code=401)
    pcm = b"\x00\x00" * 22050 * 2  # 2 s of silence
    return Response(encode_mp3(pcm, 22050) or encode_wav(pcm, 22050), media_type="audio/mpeg")


@pytest.fixture(scope="module")
def srv():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(media, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def _picture_and_mask(w=320, h=240):
    img = Image.new("RGB", (w, h), (240, 220, 180))
    d = ImageDraw.Draw(img)
    d.rectangle((0, h * 0.7, w, h), fill=(80, 150, 70))
    d.ellipse((120, 60, 180, 120), fill=(20, 20, 20))  # the "defect"
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse((110, 50, 190, 130), fill=255)
    a, b = io.BytesIO(), io.BytesIO()
    img.save(a, format="PNG")
    mask.save(b, format="PNG")
    return a.getvalue(), b.getvalue(), mask


def test_mask_loading_and_conventions():
    _, mpng, mask = _picture_and_mask()
    m = load_mask(mpng, (320, 240))
    assert 0.05 < validate_mask(m) < 0.15
    # alpha-painted mask (what the browser mask painter uploads) is understood too
    rgba = Image.new("RGBA", (160, 120), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle((10, 10, 40, 40), fill=(255, 0, 0, 255))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")
    m2 = load_mask(buf.getvalue(), (320, 240))
    assert m2.size == (320, 240) and validate_mask(m2) > 0
    with pytest.raises(MaskError):
        validate_mask(Image.new("L", (100, 100), 0))
    with pytest.raises(MaskError):
        validate_mask(Image.new("L", (100, 100), 255))  # repainting everything is not a repair
    alpha = Image.open(io.BytesIO(mask_png(m, "alpha")))
    assert alpha.mode == "RGBA" and alpha.getpixel((150, 90))[3] == 0 and alpha.getpixel((5, 5))[3] == 255
    white = Image.open(io.BytesIO(mask_png(m, "white"))).convert("L")
    assert white.getpixel((150, 90)) == 255 and white.getpixel((5, 5)) == 0


def test_classical_inpaint_only_changes_masked_area():
    img, mpng, mask = _picture_and_mask()
    out, info = classical_inpaint(img, mpng)
    assert info["changed_outside_mask"] == 0.0 and info["engine"] == "opencv-telea"
    px = Image.open(io.BytesIO(out)).convert("RGB").getpixel((150, 90))
    assert sum(px) > 300, f"dark defect should be filled with surrounding colours, got {px}"


def test_fastsdcpu_generation(srv):
    p = FastSDCPUImage(srv, "stabilityai/sd-turbo", {"steps": 2})
    r = p.generate("a fox in a meadow", "text", 1200, 900, seed=7)
    b = CALLS[-1][1]
    assert max(b["image_width"], b["image_height"]) <= 768 and b["image_width"] % 64 == 0
    assert b["use_seed"] and b["seed"] == 7 and b["lcm_model_id"] == "stabilityai/sd-turbo" and b["inference_steps"] == 2
    assert Image.open(io.BytesIO(r.data)).size == (b["image_width"], b["image_height"])
    assert p.check()["device"] == "cpu" and "stabilityai/sd-turbo" in p.check()["models"]
    with pytest.raises(ProviderError):
        p.generate("fail", "", 512, 512)
    # img2img with the character sheet as init image when enabled
    p2 = FastSDCPUImage(srv, "", {"use_img2img_reference": True})
    r2 = p2.generate("x", "", 512, 512, reference_images=[_png(64, 64)])
    assert CALLS[-1][1]["diffusion_task"] == "image_to_image" and r2.meta["used_references"]


def test_iopaint_inpaint(srv):
    img, mpng, mask = _picture_and_mask()
    p = IOPaintInpaint(srv, "lama")
    r = p.inpaint(img, mask_png(load_mask(mpng, (320, 240)), "white"), "")
    assert CALLS[-1][0] == "iopaint" and CALLS[-1][3] == (0, 255)
    out = Image.open(io.BytesIO(r.data)).convert("RGB")
    assert out.getpixel((150, 90)) == (10, 200, 10) and out.getpixel((5, 5)) == (240, 220, 180)
    assert p.check()["model"] == "lama"
    with pytest.raises(Exception):
        p.generate("x", "", 512, 512)


def test_a1111_inpaint(srv):
    img, mpng, _ = _picture_and_mask()
    p = Automatic1111Image(srv, "", {"inpaint_denoise": 0.6})
    r = p.inpaint(img, mask_png(load_mask(mpng, (320, 240)), "white"), "a clean sky")
    assert CALLS[-1] == ("img2img", 320, 240, (0, 255), 0.6)
    assert Image.open(io.BytesIO(r.data)).size == (320, 240)


def test_openai_edit_and_comfy_inpaint(mock_server):
    img, mpng, _ = _picture_and_mask()
    m = load_mask(mpng, (320, 240))
    p = OpenAICompatImage(mock_server.url + "/v1", "k", "gpt-image-1")
    r = p.inpaint(img, mask_png(m, p.mask_convention()), "repair")
    assert mock_server.state.calls[-1][0] == "edit_mask" and r.meta["inpaint"]
    c = ComfyUIImage(mock_server.url, "sd15-inpaint.safetensors", {"poll_interval": 0.1})
    r = c.inpaint(img, mask_png(m, "white"), "repair", seed=3)
    assert ("comfy_inpaint", 2) in mock_server.state.calls
    assert Image.open(io.BytesIO(r.data)).size == (320, 240)


def test_openai_compatible_tts(srv):
    t = OpenAICompatTTS(srv + "/v1", "tts-key", "kokoro", {"voices": {"fa": "fa_voice", "en": "af_heart"}})
    r = t.synthesize("سلام دوست من", "fa")
    assert CALLS[-1][:2] == ("speech", "fa_voice") and CALLS[-1][3] == "mp3"
    assert r.input_chars == len("سلام دوست من")
    if r.data[:4] != b"RIFF":
        assert 1.8 < r.duration_s < 2.3
    with pytest.raises(Exception):
        OpenAICompatTTS(srv + "/v1", "wrong", "kokoro").synthesize("hi", "en")


def test_audio_helpers():
    pcm = b"\x00\x10" * 16000
    wav = encode_wav(pcm, 16000)
    assert wav[:4] == b"RIFF" and abs(audio_duration(wav, "audio/wav") - 1.0) < 0.01
    mp3 = encode_mp3(pcm, 16000)
    if mp3 is not None:
        assert abs(audio_duration(mp3, "audio/mpeg") - 1.0) < 0.15


def test_offline_espeak_persian_and_english():
    ok, hint = EspeakTTS.available()
    if not ok:
        pytest.skip(hint)
    t = EspeakTTS({})
    fa = t.synthesize("ماه کوچولو در آسمان می‌درخشید و ستاره‌ها می‌خندیدند.", "fa")
    en = t.synthesize("The little moon was shining in the sky.", "en", speed=1.2)
    assert fa.duration_s > 1.5 and en.duration_s > 1.0
    assert fa.mime in ("audio/mpeg", "audio/wav") and len(fa.data) > 4000
    # thread safety: eSpeak is a global C library; concurrent calls must not crash or mix up audio
    out = []
    ths = [threading.Thread(target=lambda: out.append(t.synthesize("one two three four", "en").duration_s)) for _ in range(4)]
    [x.start() for x in ths]
    [x.join() for x in ths]
    assert len(out) == 4 and max(out) - min(out) < 0.05
