"""Narration (text-to-speech) adapters.

* ``offline`` - eSpeak NG through the ``espeakng-loader`` wheel (bundles the
  library + voice data, incl. Persian and English). CPU only, instant, robotic
  but intelligible. eSpeak NG is GPL-3.0: it is an *optional* runtime dependency
  loaded dynamically, never shipped inside this repository.
* ``openai_compatible`` - ``POST {base}/audio/speech``: OpenAI, Kokoro-FastAPI
  (Apache-2.0), LocalAI, openedai-speech (Piper voices, incl. fa_IR), etc.

MP3 is produced with ``lameenc`` (LGPL, optional). Without it the offline engine
returns WAV."""
from __future__ import annotations

import array
import ctypes
import io
import threading
import wave

from .base import AudioResult, ConfigError, ProviderError, TTSProvider
from .http_util import request

ESPEAK_VOICES = {"fa": "fa", "en": "en-us", "ar": "ar", "de": "de", "fr": "fr", "es": "es", "tr": "tr"}


def encode_mp3(pcm16: bytes, sample_rate: int, bitrate: int = 64) -> bytes | None:
    try:
        import lameenc
    except ImportError:
        return None
    enc = lameenc.Encoder()
    enc.set_bit_rate(bitrate)
    enc.set_in_sample_rate(sample_rate)
    enc.set_channels(1)
    enc.set_quality(5)
    return bytes(enc.encode(pcm16) + enc.flush())


def encode_wav(pcm16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def audio_duration(data: bytes, mime: str) -> float:
    """Best-effort duration (WAV exact; MP3 via frame headers)."""
    try:
        if mime == "audio/wav":
            with wave.open(io.BytesIO(data)) as w:
                return w.getnframes() / w.getframerate()
        if mime == "audio/mpeg":
            return _mp3_duration(data)
    except Exception:
        pass
    return 0.0


def _mp3_duration(data: bytes) -> float:
    rates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
    srs = {0: 44100, 1: 48000, 2: 32000}
    i, total, n = 0, 0.0, len(data)
    if data[:3] == b"ID3":
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        i = 10 + size
    while i + 4 <= n:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            ver = (data[i + 1] >> 3) & 3  # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
            br_idx, sr_idx, pad = data[i + 2] >> 4, (data[i + 2] >> 2) & 3, (data[i + 2] >> 1) & 1
            if sr_idx == 3 or br_idx in (0, 15):
                i += 1
                continue
            sr = srs[sr_idx] // (1 if ver == 3 else 2 if ver == 2 else 4)
            br = rates[br_idx] if ver == 3 else [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0][br_idx]
            samples = 1152 if ver == 3 else 576
            flen = (samples // 8 * br * 1000) // sr + pad
            if flen <= 0:
                break
            total += samples / sr
            i += flen
        else:
            i += 1
    return total


class EspeakTTS(TTSProvider):
    _lock = threading.Lock()  # eSpeak NG keeps global state: one synthesis at a time
    _lib = None
    _rate = 0

    def __init__(self, params: dict | None = None):
        self.params = params or {}

    @classmethod
    def available(cls) -> tuple[bool, str]:
        try:
            import espeakng_loader  # noqa: F401
            return True, ""
        except ImportError:
            return False, "pip install espeakng-loader lameenc (optional, GPL-3.0 eSpeak NG)"

    @classmethod
    def _load(cls):
        if cls._lib is None:
            try:
                import espeakng_loader
            except ImportError as e:
                raise ConfigError("Offline narration needs the optional package espeakng-loader (pip install espeakng-loader lameenc)") from e
            lib = ctypes.cdll.LoadLibrary(espeakng_loader.get_library_path())
            lib.espeak_Initialize.restype = ctypes.c_int
            rate = lib.espeak_Initialize(1, 0, espeakng_loader.get_data_path().encode(), 0)  # AUDIO_OUTPUT_RETRIEVAL
            if rate <= 0:
                raise ProviderError("eSpeak NG could not be initialised")
            cls._lib, cls._rate = lib, rate
        return cls._lib, cls._rate

    def synthesize(self, text, language, voice="", speed=1.0):
        return self.synthesize_with_words(text, language, voice, speed)[0]

    def synthesize_with_words(self, text, language, voice="", speed=1.0):
        """Synthesize and return (AudioResult, word timings).

        eSpeak NG reports one WORD event per spoken word with its character range and
        its offset in the generated audio (ms) - that is what read-along highlighting
        and the video export use. Engines without word events fall back to
        :func:`app.export.timing.proportional_timings`."""
        voice = voice or ESPEAK_VOICES.get(language, language)
        with self._lock:
            lib, rate = self._load()
            buf = array.array("h")
            events: list = []
            CB = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
            INTS = ctypes.c_int * 6  # type, uid, text_position, length, audio_position, sample

            def _cb(wav, n, ev):
                if wav and n > 0:
                    buf.frombytes(ctypes.string_at(wav, n * 2))
                if ev:
                    arr = ctypes.cast(ev, ctypes.POINTER(INTS))
                    i = 0
                    while i < 4096:  # the list ends with espeakEVENT_LIST_TERMINATED
                        e = arr[i]
                        if e[0] == 0:
                            break
                        events.append((e[0], e[2], e[3], e[4]))
                        i += 1
                return 0
            cb = CB(_cb)
            lib.espeak_SetSynthCallback(cb)
            if lib.espeak_SetVoiceByName(voice.encode()) != 0:
                raise ConfigError(f"eSpeak NG has no voice {voice!r}")
            lib.espeak_SetParameter(1, int(max(80, min(260, 155 * speed * float(self.params.get("rate_factor", 1.0))))), 0)  # words/min
            lib.espeak_SetParameter(3, int(self.params.get("pitch", 55)), 0)
            raw = text.encode("utf-8")
            res = lib.espeak_Synth(ctypes.c_char_p(raw), len(raw) + 1, 0, 1, 0, 1, None, None)  # POS_CHARACTER, espeakCHARS_UTF8
            lib.espeak_Synchronize()
            if res != 0:
                raise ProviderError(f"eSpeak NG synthesis failed (code {res})")
        pcm = buf.tobytes()
        if not pcm:
            raise ProviderError("eSpeak NG produced no audio")
        mp3 = encode_mp3(pcm, rate)
        data, mime = (mp3, "audio/mpeg") if mp3 else (encode_wav(pcm, rate), "audio/wav")
        duration = round(len(buf) / rate, 2)
        res = AudioResult(data=data, mime=mime, model="espeak-ng", duration_s=duration, voice=voice, input_chars=len(text))
        from ..export.timing import timings_from_events
        words = timings_from_events(text, events, duration)
        return res, (words if len(words) > 1 else [])

    def check(self):
        ok, hint = self.available()
        if not ok:
            return {"ok": False, "error": hint}
        r = self.synthesize("Test", "en")
        return {"ok": True, "note": "eSpeak NG (offline, GPL-3.0, optional)", "format": r.mime, "mp3": r.mime == "audio/mpeg"}


class OpenAICompatTTS(TTSProvider):
    def __init__(self, base_url: str, api_key: str, model: str, params: dict | None = None, timeout: float = 180):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.api_key = api_key
        self.model = model or "tts-1"
        self.params = params or {}
        self.timeout = timeout

    def synthesize(self, text, language, voice="", speed=1.0):
        voices = self.params.get("voices") or {}
        v = voice or voices.get(language) or self.params.get("voice") or "alloy"
        body = {"model": self.model, "input": text, "voice": v, "response_format": "mp3", "speed": speed}
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        r = request("POST", f"{self.base_url}/audio/speech", headers=h, json=body, timeout=self.timeout)
        data = r.content
        if len(data) < 64:
            raise ProviderError("Speech API returned no audio")
        return AudioResult(data=data, mime="audio/mpeg", model=self.model, duration_s=round(_mp3_duration(data), 2), voice=v, input_chars=len(text))

    def check(self):
        h = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = request("GET", f"{self.base_url}/models", headers=h, timeout=15).json()
            ids = [m.get("id") for m in r.get("data", [])]
        except ProviderError:
            ids = []
        return {"ok": True, "models": ids[:50], "model_available": (self.model in ids) if ids else None}
