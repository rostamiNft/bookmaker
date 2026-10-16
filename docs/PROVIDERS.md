# Connecting models (local and API)

Open **Models & providers → Add provider**, fill the form, click **Test connection**, then pick it as a default
(or per book in *New book*). Every provider has: retries (`Max retries`), timeout, requests-per-minute pacing,
pricing (for cost estimates and usage), and an optional **fallback provider** used when it fails.
The built-in offline providers are always available as the last fallback.

API keys: stored encrypted; or type `env:VARIABLE` to read the key from the server environment.

## Story models (kind = `llm`)

### Ollama (local)
```bash
ollama pull qwen2.5:14b        # good multilingual quality incl. Persian; 7B/8B models are faster but weaker in Persian
ollama serve                   # http://localhost:11434
```
Provider: type `ollama`, Base URL `http://localhost:11434`, Model `qwen2.5:14b`. JSON mode is used automatically.
CPU-only works but is slow (minutes per book). Expect a GPU with ≥12 GB VRAM for 14B models at good speed.

### OpenAI-compatible (OpenAI, OpenRouter, Groq, Together, LM Studio, vLLM, llama.cpp server, LocalAI …)
Type `openai_compatible`, Base URL e.g. `https://api.openai.com/v1`, `https://openrouter.ai/api/v1`,
`http://localhost:1234/v1` (LM Studio), `http://localhost:8080/v1` (llama.cpp `llama-server`), Model name, API key.
If a server rejects `response_format`, add `{"json_mode": false}` in *Advanced params (JSON)* and the prompt-level JSON
instructions are used.

### Anthropic
Type `anthropic`, Base URL `https://api.anthropic.com`, model name, API key.

## Image models (kind = `image`)

### ComfyUI (local GPU, recommended for open models)
1. Run ComfyUI (`python main.py --listen 127.0.0.1 --port 8188`) with a checkpoint (SDXL, SD 1.5, FLUX schnell …).
2. Provider: type `comfyui`, Base URL `http://127.0.0.1:8188`, Model = checkpoint file name.
3. Without a workflow, a built-in txt2img workflow (KSampler + CheckpointLoaderSimple) is used.
4. For **character consistency**, export your own workflow in *API format* (ComfyUI → "Save (API Format)") that uses
   IP-Adapter / PuLID / InstantID nodes and paste it into *ComfyUI workflow*. Placeholders replaced per image:
   `{{prompt}}` `{{negative}}` `{{width}}` `{{height}}` (snapped to multiples of 64) `{{seed}}` `{{checkpoint}}`
   `{{ref_image_1}}`, `{{ref_image_2}}` … (the approved Character Bible reference sheets of the characters on the
   page, uploaded via `/upload/image`; use them in `LoadImage` nodes).
   Licence note: InstantID/PuLID rely on InsightFace models that are licensed for non-commercial research only.

### AUTOMATIC1111 / Forge
Start with `--api`. Type `automatic1111`, Base URL `http://127.0.0.1:7860`, Model = checkpoint title (optional).
To use references, set `controlnet_unit` in *Advanced params (JSON)* to a ControlNet unit dict, e.g. an IP-Adapter unit; the
first reference image is injected as its `image`.

### OpenAI-compatible images (gpt-image-1, DALL·E 3, compatible gateways)
Type `openai_compatible`, kind `image`, model `gpt-image-1`. When references exist, `/images/edits` is called with up
to 4 reference images; otherwise `/images/generations`. The API only supports fixed sizes, so the nearest size is
requested and the result is **centre-cropped** to the book's aspect ratio (reported as a QA warning, never stretched).
Set pricing *per image* to get correct cost estimates; paid runs require confirmation in the UI.

### stable-diffusion.cpp server (CPU or GPU, MIT)
`sd-server` from [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp) runs SD 1.5/SDXL/FLUX
(GGUF) on CPU, Vulkan, Metal or CUDA. It exposes an AUTOMATIC1111-compatible API, so use type `automatic1111` with Base URL
`http://127.0.0.1:1234` (txt2img, img2img with references, img2img + mask for region repair). Its OpenAI-compatible
`/v1/images/generations` also works with type `openai_compatible` if you set `{"response_format": ""}` in *Advanced params*
(the server does not accept that field).

### FastSD CPU (CPU, MIT)
[rupeshs/fastsdcpu](https://github.com/rupeshs/fastsdcpu) generates 512×512 images in a few seconds on a modern CPU (LCM /
OpenVINO). Start its API server (`python src/app.py --api`, port 8000 by default; run Bookmaker on another port) and add
type `fastsdcpu`, Base URL `http://127.0.0.1:8000`. *Advanced params*: `steps` (default 1–4), `use_openvino`,
`lcm_model_id`, `tiny_vae`, `reference_strength` (img2img from the character reference sheet when present).
Quality is far below SDXL/FLUX; it is a "no GPU but still AI" option. Model weights keep their own licences.

## Region repair / inpainting (kind = `inpaint`, optional)
The **Repair a region** dialog (Pages tab) always offers the built-in **classical** engine: OpenCV Telea/Navier-Stokes
inpainting on CPU. It removes small artefacts (stray letters, smudges, an extra finger) but cannot draw new content.
To redraw a region from a prompt, pick any of:

| Engine | How to connect |
|---|---|
| IOPaint (LaMa, MAT, SD inpaint …; Apache-2.0, archived 2025 but working) | `iopaint start --model=lama --device=cpu --port=8080`; provider kind `inpaint`, type `iopaint`, URL `http://127.0.0.1:8080`. LaMa runs on CPU. `hd_strategy` param: `Crop` (default) / `Resize` / `Original` |
| AUTOMATIC1111 / Forge / sd.cpp | the existing `image` provider: img2img with `mask` (`inpaint_denoise` param, default 0.75) |
| ComfyUI | the existing `image` provider: built-in inpaint workflow (VAEEncodeForInpaint) or your own `inpaint_workflow` with `{{image}}`, `{{mask}}` placeholders |
| OpenAI-compatible | `/images/edits` with `mask` (transparent = repaint) |

Whatever the engine returns, the result is composited through the mask, so pixels outside it are guaranteed unchanged
(`changed_outside_mask` is measured and must be 0). The previous image is kept as a revision (undo).

## Narration / read-aloud (kind = `tts`, optional)
* **Built-in offline narrator**: eSpeak NG via the optional `espeakng-loader` wheel (+ `lameenc` for MP3);
  `pip install -r backend/requirements-media.txt`. Supports Persian and English (and ~100 other languages), robotic but
  intelligible, fully offline. **Licence: eSpeak NG is GPL-3.0.** It is loaded at runtime as a separate shared library and
  is not installed unless you choose to; if you redistribute Bookmaker bundled with it, comply with the GPL.
* **OpenAI-compatible `/v1/audio/speech`**: OpenAI `tts-1`/`gpt-4o-mini-tts`, or local servers such as
  [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) (Apache-2.0; Kokoro voices are English/European/Asian, no
  Persian) or LocalAI. Params: `voice`, `voices` (per language, e.g. `{"fa": "...", "en": "af_bella"}`), `format`.
  Set pricing `per_1k_chars` and the estimate/confirmation covers narration cost too (narration of a paid TTS asks for
  confirmation before any spend).
* Audio is stored per page (`audio/page-NN-<hash>.mp3`); edited text invalidates only that page's audio.
  It is embedded in the EPUB (media) and HTML reader and played in the Preview read-along.

## Exports
EPUB 3 (fixed-layout picture pages, text kept as real text, `dir=rtl` for Persian), validated by **EPUBCheck 5.3** when
`epubcheck` + `jdk4py` are installed (otherwise reported as "not checked", never as valid); single-file HTML reader;
images + text ZIP. Exports are versioned with the book and rebuilt on demand.

## Vision reviewer (kind = `vision`, optional)
Any multimodal model via `ollama` (e.g. `qwen2.5vl`, `llava`), `openai_compatible` (e.g. `gpt-4o-mini`) or
`anthropic`. When set, each illustration that passes the automatic checks is also reviewed for relevance to the page
text, visible text/letters and anatomy problems. Failing reviews trigger regeneration (bounded).

## Hardware guidance

| Setup | Story | Images | Notes |
|---|---|---|---|
| No GPU, no internet | built-in engine | procedural illustrator | always works, ~35 s per 14-page book |
| No GPU, local AI images | Ollama 1–4B (slow) | FastSD CPU / sd.cpp (SD 1.5 Q4) | seconds to minutes per image; lower quality |
| No GPU + APIs | OpenAI-compatible/Anthropic | gpt-image-1 or a hosted SDXL/FLUX API | pay per use; set budget |
| 8–12 GB GPU | Ollama 7–14B | ComfyUI SD 1.5 / SDXL | 1–3 min per book image set |
| 16–24 GB GPU | Ollama 14–32B | ComfyUI SDXL/FLUX + IP-Adapter | best local quality |

Bookmaker never loads models itself; GPU memory is managed by Ollama/ComfyUI. Keep `BOOKMAKER_MAX_CONCURRENT_BOOKS=1`
on a single GPU; external image calls are already sequential per book.
