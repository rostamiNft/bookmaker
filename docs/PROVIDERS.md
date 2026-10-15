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

## Vision reviewer (kind = `vision`, optional)
Any multimodal model via `ollama` (e.g. `qwen2.5vl`, `llava`), `openai_compatible` (e.g. `gpt-4o-mini`) or
`anthropic`. When set, each illustration that passes the automatic checks is also reviewed for relevance to the page
text, visible text/letters and anatomy problems. Failing reviews trigger regeneration (bounded).

## Hardware guidance

| Setup | Story | Images | Notes |
|---|---|---|---|
| No GPU, no internet | built-in engine | procedural illustrator | always works, ~35 s per 14-page book |
| No GPU + APIs | OpenAI-compatible/Anthropic | gpt-image-1 or a hosted SDXL/FLUX API | pay per use; set budget |
| 8–12 GB GPU | Ollama 7–14B | ComfyUI SD 1.5 / SDXL | 1–3 min per book image set |
| 16–24 GB GPU | Ollama 14–32B | ComfyUI SDXL/FLUX + IP-Adapter | best local quality |

Bookmaker never loads models itself; GPU memory is managed by Ollama/ComfyUI. Keep `BOOKMAKER_MAX_CONCURRENT_BOOKS=1`
on a single GPU; external image calls are already sequential per book.
