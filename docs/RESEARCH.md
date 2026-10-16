# Research: open-source building blocks and architecture decision

Repository facts (licence as detected by GitHub, last push date, stars, archived flag) were queried from the GitHub
REST API on **2026-10-15**; raw output: [`research/github_repos_2026-10-15.txt`](research/github_repos_2026-10-15.txt).
`NOASSERTION` means GitHub could not classify the licence file; in those cases the licence text/package metadata was read.
**Code licences ≠ model-weight licences.** Weight licences below come from the projects' published model cards and
must be re-checked on the model card before commercial use. Nothing here is legal advice.

## Environment constraints that shaped the decision

The development sandbox had 2 CPU cores, 3.8 GB RAM, **no GPU**, and outbound access only to GitHub, PyPI and npm
(no Hugging Face, no model APIs, no API keys). Consequences:

* No real LLM or diffusion model could be downloaded or called here. Model weights live on Hugging Face/Ollama
  registries, which were unreachable.
* The product therefore needed a **real, no-AI fallback path** (template story engine + procedural illustrator) so
  every acceptance step (story → images → PDF) is genuinely exercised, and an **API/service-first AI layer** that is
  tested against protocol-faithful mock servers.

## 1. Story generation / local LLMs

| Project | Repo | Licence | Last push | Stars | Use in Bookmaker |
|---|---|---|---|---|---|
| Ollama | ollama/ollama | MIT | 2026-10-15 | 183k | ✅ adapter (`/api/generate`, JSON mode, vision via `images`) |
| llama.cpp | ggml-org/llama.cpp | MIT | 2026-10-15 | 131k | ✅ via its OpenAI-compatible server |
| vLLM | vllm-project/vllm | Apache-2.0 | 2026-10-15 | 94k | ✅ via OpenAI-compatible adapter (GPU servers) |
| openai-python | openai/openai-python | Apache-2.0 | 2026-10-15 | 32k | ❌ not needed: plain `httpx` covers all OpenAI-compatible servers |
| LiteLLM | BerriAI/litellm | MIT (+ separate enterprise dir licence) | 2026-10-15 | 61k | ❌ heavy dependency; own small router suffices. Can sit *in front* of Bookmaker as an OpenAI-compatible proxy |
| LangGraph | langchain-ai/langgraph | MIT | 2026-10-15 | 43k | ❌ the pipeline is a fixed, resumable DAG; a DB-backed orchestrator is simpler to audit |

Model weight licences (verify on model cards): Llama 3.x: Llama Community Licence (acceptable-use policy, >700M MAU
clause); Qwen2.5: Apache-2.0 for most sizes (some sizes use the Qwen licence); Mistral 7B / Mixtral: Apache-2.0;
Gemma: Gemma Terms of Use. For Persian text quality, larger multilingual models (Qwen2.5 ≥14B, Llama 3.1 70B, or
hosted APIs) are recommended; small 7–8B models often produce weak Persian.

## 2. Image generation (local and API)

| Project | Repo | Licence | Last push | Stars | Use |
|---|---|---|---|---|---|
| ComfyUI | Comfy-Org/ComfyUI | GPL-3.0 | 2026-10-15 | 138k | ✅ HTTP adapter (`/prompt`, `/history`, `/view`, `/upload/image`), user-supplied API-format workflows with placeholders. Used as a **separate service** over HTTP, not linked |
| ComfyUI-Manager | Comfy-Org/ComfyUI-Manager | GPL-3.0 | 2026-10-15 | 16k | recommended for installing nodes |
| AUTOMATIC1111 webui | AUTOMATIC1111/stable-diffusion-webui | AGPL-3.0 | 2026-03-02 | 165k | ✅ HTTP adapter (`/sdapi/v1/txt2img`, ControlNet unit). Separate service. Activity has slowed |
| Forge | lllyasviel/stable-diffusion-webui-forge | AGPL-3.0 | 2025-07-31 | 13k | same API as A1111; last push > 1 year ago (maintenance risk) |
| diffusers | huggingface/diffusers | Apache-2.0 | 2026-10-15 | 35k | ❌ in-process GPU inference rejected: the server must run without GPU; use ComfyUI/A1111/API instead |
| stable-diffusion.cpp | leejet/stable-diffusion.cpp | MIT | 2026-10-15 | 7.7k | candidate for a future CPU adapter (slow: minutes per image on CPU) |
| FastSD CPU | rupeshs/fastsdcpu | MIT | 2026-07-24 | 2.2k | candidate CPU path (LCM/turbo models); not integrated |
| FLUX (reference code) | black-forest-labs/flux | Apache-2.0 | 2025-07-31 | 26k | via ComfyUI/API |
| Stability generative-models | Stability-AI/generative-models | MIT | 2025-12-16 | 27k | via ComfyUI/A1111 |
| Qwen-Image | QwenLM/Qwen-Image | Apache-2.0 | 2026-02-10 | 8.4k | via ComfyUI; strong at text rendering (not needed: text is typeset) |
| OmniGen / OmniGen2 | VectorSpaceLab/OmniGen(2) | MIT / Apache-2.0 | 2025-12 / 2026-03 | 4k | reference-based generation/editing candidate |

Weight licences (verify): SD 1.5: CreativeML OpenRAIL-M; SDXL: OpenRAIL++-M (use restrictions, commercial allowed);
SD 3.5: Stability AI Community Licence (revenue threshold); FLUX.1 [schnell]: Apache-2.0; **FLUX.1 [dev]: non-commercial**;
Qwen-Image: Apache-2.0 per model card. API outputs (OpenAI, etc.) are governed by each provider's terms.

## 3. Character consistency and reference-based generation

| Project | Repo | Licence | Last push | Notes |
|---|---|---|---|---|
| IP-Adapter | tencent-ailab/IP-Adapter | Apache-2.0 | 2024-06-28 | image-prompt adapter; code inactive since 2024 but widely used via ComfyUI nodes |
| ComfyUI_IPAdapter_plus | cubiq/ComfyUI_IPAdapter_plus | GPL-3.0 | 2025-04-14 | ComfyUI nodes; author announced maintenance mode (risk) |
| InstantID | instantX-research/InstantID | Apache-2.0 (code) | 2024-07-18 | **depends on InsightFace face models that are non-commercial**; best for photo-real faces, less for cartoons |
| PuLID | ToTheBeginning/PuLID | Apache-2.0 (code) | 2025-07-31 | same InsightFace caveat |
| StoryDiffusion | HVision-NKU/StoryDiffusion | Apache-2.0 | 2024-09-26 | consistent self-attention for comic/story sequences; research code, inactive |
| ControlNet | lllyasviel/ControlNet | Apache-2.0 | 2024-02-25 | pose/composition control (A1111 extension `Mikubill/sd-webui-controlnet`, GPL-3.0) |

Decision: Bookmaker keeps consistency **in the data** (Character Bible with fixed descriptions, colour palettes,
distinctive items, approved reference sheets, fixed seeds) and passes references to whichever provider supports them
(OpenAI `images/edits`, ComfyUI IP-Adapter workflows, A1111 ControlNet/IP-Adapter units). The procedural illustrator is
consistent by construction. QA verifies consistency (colour signatures per character; optional vision-LLM review)
instead of trusting prompts.

## 4. Layout, fonts and PDF

| Project | Licence | Use |
|---|---|---|
| ReportLab 5 (`reportlab` on PyPI) | BSD | ✅ PDF generation with TrueType embedding, `/Lang`, metadata |
| WeasyPrint (Kozea/WeasyPrint, BSD-3, active) | BSD-3-Clause | ❌ HTML/CSS route would give HarfBuzz shaping but adds Pango/system deps; measurement-based layout in ReportLab gives exact overflow control |
| pypdf (py-pdf/pypdf, BSD-3, active) | BSD-3-Clause | ✅ structural validation |
| pypdfium2 (pypdfium2-team, active) | BSD-3/Apache-2.0 | ✅ rasterising every page for QA and the in-app preview |
| python-arabic-reshaper (mpcabd) | MIT | ✅ Persian contextual forms |
| python-bidi (MeirKriheli) | **LGPL-3.0** | ✅ bidi reordering (used unmodified as a library) |
| fontTools | MIT | ✅ glyph coverage checks, WOFF→TTF conversion of Andika |
| Vazirmatn (rastikerdar/vazirmatn) | OFL-1.1 | ✅ Persian + Latin font (last release 2023; stable) |
| Andika (SIL, via @fontsource/andika) | OFL-1.1 | ✅ Latin literacy font designed for beginning readers |

## 5. Workflow, queue, web

| Project | Licence | Decision |
|---|---|---|
| FastAPI | MIT | ✅ REST API |
| SQLAlchemy + SQLite (WAL) | MIT / public domain | ✅ single-file DB for small deployments; models are portable to PostgreSQL |
| Celery (BSD) / RQ (BSD) + Redis | BSD | ❌ for v1: an extra service for a single-node app. The DB-backed queue gives durability, restart recovery, de-duplication and bounded concurrency. Swapping in Celery/RQ is possible because jobs are plain records |
| React 19 + Vite 8 | MIT | ✅ SPA, built once and served by FastAPI (single origin, no CORS) |

## 6. Automatic quality checks

| Project | Licence | Decision |
|---|---|---|
| RapidOCR (`rapidocr_onnxruntime`, models bundled in wheel) + onnxruntime | Apache-2.0 / MIT | ✅ optional stray-text detection on CPU (~0.5 s/image) |
| Tesseract | Apache-2.0 | ❌ needs system packages; RapidOCR detects text regions more robustly in illustrations |
| imagehash | BSD-2 | ❌ not needed: own 64-bit dHash (10 lines) |
| CLIP / open_clip | MIT | ❌ weights not downloadable here; relevance is checked by an optional vision LLM instead |

## Chosen architecture (summary)

FastAPI + SQLite + DB-backed worker; provider-agnostic adapters behind a router with retries, fallback, budget and
usage accounting; structured story/character data; independent QA at every stage with local, bounded repair;
measurement-based ReportLab layout with proper Persian shaping/bidi; React UI. It is the cheapest option to run
(no GPU, single process), easy to maintain (few dependencies, plain HTTP integrations), and scales up by pointing the
adapters at stronger local services (Ollama/ComfyUI on a GPU box) or hosted APIs.

---

## Second research round (2026-10-15): suggested repositories and the features they led to

All data verified with the GitHub REST API on 2026-10-15 (raw lists: `docs/research/suggested_repos_2026-10-15.txt`,
`docs/research/supporting_tools_2026-10-15.txt`). Only **ideas** were adopted; no code was copied (two of the projects have no
LICENSE file, so copying would not be allowed anyway).

| Project | ★ | Licence | Last push | Idea | Implemented in Bookmaker as |
|---|---|---|---|---|---|
| valleylmh/storybloom | 12 | MIT | 2026-10-06 | review the outline before paying for images; TTS read-along; ZIP/long-image export | `review_outline` gate (`awaiting_review` → approve), per-page narration, Preview read-along, EPUB/HTML/ZIP exports |
| zfrsgtcu/ComfyUI-ZFRNodes | 36 | MIT | 2026-08-31 | contact/asset sheet conditions every frame; inpaint studio | cast line-up sheet passed as a reference (`cast_reference`), mask-based region repair |
| mhubrich/storybook-ai-generator | 1 | no LICENSE file (README: Apache-2.0) | 2026-01-26 | relationship & scale rules, multi-view sheets | `appearance.scale` + relative heights enforced in every scene and prompt; scale slider in the UI |
| RishiDesai/CharForge | 88 | MIT | 2025-06-27 | one image → multiview sheet → LoRA | documented GPU path (train a LoRA externally, use it in a ComfyUI workflow); not integrated (needs GPU) |
| lincwang123-bot/moonlit-stories | 175 | MIT | 2026-09-04 | local TTS service, style key image | TTS provider kind (OpenAI-compatible `/v1/audio/speech`, e.g. Kokoro-FastAPI/LocalAI) |
| weaiw/storybook-generator-skill | 49 | MIT | 2026-07-06 | QA checklist | compared with our QA rules (no new rule needed) |
| naakaarafr/Storybook-Generator | 11 | MIT | 2025-06-12 | CrewAI agents + FLUX | nothing new (already a staged pipeline) |
| kart-io/picture-skills | 13 | no LICENSE file | 2026-01-19 | multi-character prompting | prompt wording ideas only |
| DoodleBook | – | – | – | – | **no repository found**; treated as unverified |

### Supporting tools selected (CPU-friendly, permissive where possible)

| Tool | ★ | Licence | Status | Used for | Notes |
|---|---|---|---|---|---|
| OpenCV `inpaint` (Telea/NS) | – | Apache-2.0 | active | built-in CPU region repair | removes artefacts; cannot draw new content |
| Sanster/IOPaint (+ LaMa) | 23.3k | Apache-2.0 | **archived 2025-04** | optional AI inpainting server (LaMa on CPU) | still works; no updates expected |
| leejet/stable-diffusion.cpp | 7.7k | MIT | active (2026-10-15) | CPU/GPU diffusion via A1111-compatible API | GGUF quantised models; weights keep their own licences |
| rupeshs/fastsdcpu | 2.2k | MIT | active (2026-07) | CPU image provider (`fastsdcpu`) | LCM/OpenVINO; low quality but local |
| remsky/Kokoro-FastAPI | 5.6k | Apache-2.0 | active | optional local TTS (OpenAI-compatible) | no Persian voice |
| espeak-ng (via espeakng-loader) | 7.0k / 10 | **GPL-3.0** / MIT | active | built-in offline narrator (optional install) | Persian supported; robotic voice; GPL → optional, documented |
| lameenc | 65 | LGPL-3.0 | active | MP3 encoding | optional |
| rhasspy/piper → OHF-Voice/piper1-gpl | 5.9k | MIT (archived) → **GPL-3.0** | – | not used | the maintained fork is GPL and Persian voices are on Hugging Face (unreachable here) |
| w3c/epubcheck (PyPI `epubcheck` + `jdk4py`) | 2.0k | BSD-3 / GPLv2+CPE | active | EPUB validation | ~70 MB optional; without it EPUBs are reported "not checked" |
| ebooklib | – | **AGPL-3.0** | – | **avoided** | EPUB is written directly with `zipfile` instead |
| StPageFlip | 888 | MIT | unmaintained since 2024-01 | not used | the HTML reader uses a small CSS page-turn instead |
| uharfbuzz | 113 | Apache-2.0 | active | **used**: HarfBuzz shaping (opt-in `shaping=harfbuzz`, auto when installed) | full OpenType contextual forms; falls back to arabic-reshaper |
| pyphen | 400 | GPL/LGPL/MPL tri-licence (installed dist carries MPL 1.1) | active | **used**: hyphenation for Latin scripts | opt-in per book (`hyphenation`) |
| pg8000 | 900 | BSD-3 (moved to Codeberg) | active | **used**: PostgreSQL driver for `BOOKMAKER_DATABASE_URL` | pure Python, no libpq; psycopg was rejected (LGPL-3) |
| redis-py | 19k | MIT | active | **used**: optional shared worker locks | optional; falls back to in-process locks |
| PyJWT | 8k | MIT | active | **used**: OIDC ID-token verification | kid-matched JWKS, cached + refetched |
| aiosmtpd | 1k | BSD-3 | active | **used in tests only**: real SMTP server for the password-reset test | – |
| pgserver | 200 | Apache-2.0 | active | **used in tests only**: real PostgreSQL 16 for the deployment test | bundles the postgres binaries, no Docker needed |
| PyAV 18.1 | 2k | BSD-3 (bundles FFmpeg 8.1.2 "LGPLv3+" **and GPL libx264/libx265**) | active | **used**: video export | default WebM VP9+Opus (royalty-free); MP4/H.264 only after `confirm_gpl` → 402 otherwise |
| kohya sd-scripts / ai-toolkit | 25k / 8k | Apache-2.0 / MIT | active | **used**: LoRA dataset export writes ready-to-run configs for both | training itself is out of scope (needs a GPU) |
| aliftype/raqim-kashida | – | MIT | active | **used**: kashida (tatweel) rules for justified Persian | Benatia et al. 2006 priorities re-implemented |
| pmjdebruijn/amethyst-cmyk-icc-profiles | – | CC0 | – | bundled alternative CMYK profile | experimental: a hard proof is recommended |
| hazm | 1.4k | MIT | active | not used | Persian NLP (normalisation) candidate |

### Datasets mentioned by the user (not verified, Hugging Face unreachable from the build environment)
TinyStories (CDLA-Sharing-1.0), Children-Stories-Collection, Story4Kids (Arabic), GlotStoryBook (Global Storybooks, CC BY),
Colibri, Picturing Stories. Bookmaker does not train models and does not ship any of them; they are listed as possible
fine-tuning/evaluation sources. Check each dataset card's licence before use (CC BY requires attribution; CDLA-Sharing
requires sharing derived data under the same terms).
