# Third-party components and licences

Versions are the ones installed and tested. Licences are taken from package metadata or the bundled licence files.

## Python (backend)

| Package | Version | Licence | Role |
|---|---|---|---|
| fastapi | 0.143.2 | MIT | REST API |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server |
| SQLAlchemy | 2.1.4 | MIT | ORM (SQLite) |
| pydantic | 2.14.1 | MIT | validation / schemas |
| python-multipart | 0.0.32 | Apache-2.0 | uploads |
| httpx | 0.28.1 | BSD-3-Clause | provider HTTP client |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | API-key encryption (Fernet) |
| reportlab | 5.0.1 | BSD | PDF generation |
| pillow | 12.3.0 | MIT-CMU | image processing, procedural illustrator |
| arabic-reshaper | 3.0.1 | MIT | Persian/Arabic contextual shaping |
| python-bidi | 0.6.11 | **LGPL-3.0** | bidirectional text (used as an unmodified library) |
| fonttools | 4.67.0 | MIT | glyph coverage, font conversion |
| pypdf | 6.20.1 | BSD-3-Clause | PDF validation |
| pypdfium2 | 5.14.0 | BSD-3-Clause / Apache-2.0 (+ PDFium deps) | PDF rasterisation for QA and preview |
| rapidocr_onnxruntime *(optional)* | 1.4.4 | Apache-2.0 | OCR text detection (models bundled) |
| onnxruntime *(optional)* | 1.31.0 | MIT | OCR inference |
| opencv-python-headless | 5.0.0.93 | Apache-2.0 | classical inpainting (region repair), OCR pre-processing |
| numpy | 2.4.6 | BSD-3-Clause and others | image arrays (mask repair, CMYK, QA) |
| epubcheck *(optional, media)* | 5.3.0 | BSD-3-Clause (EPUBCheck jar, W3C) | EPUB validation |
| jdk4py *(optional, media)* | 25.0.2.1 | GPLv2 per package metadata (bundled OpenJDK runtime is GPL-2.0 with Classpath Exception) | Java runtime for EPUBCheck, run as a subprocess |
| espeakng-loader *(optional, media)* | 0.2.4 | MIT (wrapper) — bundles **eSpeak NG, GPL-3.0** | offline narration; loaded at runtime via ctypes. Not installed by `requirements.txt`; install it only if GPL-3.0 terms are acceptable for your distribution |
| lameenc *(optional, media)* | 1.8.4 | **LGPL-3.0-or-later** (LAME) | MP3 encoding of narration; used unmodified as a dynamically loaded extension |
| pytest *(dev)* | 9.1.1 | MIT | tests |

## JavaScript (frontend)

| Package | Version | Licence |
|---|---|---|
| react / react-dom | 19.3.1 | MIT |
| vite | 8.3.4 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
| vitest *(dev)* | 5.0.5 | MIT |
| @testing-library/react *(dev)* | 16.3.3 | MIT |
| jsdom *(dev)* | 30.1.3 | MIT |
| puppeteer-core + @sparticuz/chromium *(test tooling only, not in the repo)* | 25.14.0 / 153.0.0 | Apache-2.0 / MIT (Chromium: BSD-3 + others) | used to run the browser end-to-end test |

## Fonts (bundled in `assets/fonts`, embedded as subsets in generated PDFs)

| Font | Licence | Source |
|---|---|---|
| Vazirmatn Regular/Bold | SIL Open Font License 1.1 (`assets/fonts/OFL-Vazirmatn.txt`) | github.com/rastikerdar/vazirmatn |
| Andika Regular/Bold (Latin subset, converted from the @fontsource WOFF files) | SIL Open Font License 1.1 (`assets/fonts/OFL-Andika.txt`) | SIL International via npm `@fontsource/andika` |
| DejaVu Sans (system fallback, not bundled) | Bitstream Vera / DejaVu licence | OS package |

OFL allows embedding in documents; the fonts may not be sold on their own.

## External services (not bundled, integrated over HTTP)

Ollama (MIT), llama.cpp (MIT), vLLM (Apache-2.0), LocalAI (MIT), stable-diffusion.cpp (MIT), FastSD CPU (MIT),
IOPaint (Apache-2.0; LaMa weights Apache-2.0), Kokoro-FastAPI (Apache-2.0), ComfyUI (GPL-3.0), AUTOMATIC1111 (AGPL-3.0),
Forge (AGPL-3.0), and hosted APIs
(OpenAI, Anthropic, OpenRouter …, governed by their terms of service). Bookmaker talks to them as separate processes
over their public HTTP APIs and does not include or link their code. **Model weights carry their own licences**
(e.g. FLUX.1-dev is non-commercial, SDXL uses OpenRAIL++-M, Llama uses the Llama Community Licence; InsightFace
models used by InstantID/PuLID are non-commercial). Check the model card before commercial use. See docs/RESEARCH.md.

## Ideas adopted from other projects (no code copied)

The following MIT-licensed or licence-unclear projects were studied (2026-10-15) and only **ideas** were re-implemented:
outline review before illustration and per-page audio (valleylmh/storybloom, MIT); cast/contact sheet as a consistency
reference and an inpaint step (zfrsgtcu/ComfyUI-ZFRNodes, MIT); character relationship/scale rules
(mhubrich/storybook-ai-generator: no LICENSE file, README says Apache-2.0, so nothing copied); QA checklists
(weaiw/storybook-generator-skill, MIT). See docs/RESEARCH.md and docs/research/.

## Print output caveat

"CMYK" output converts images with a naive (profile-less) RGB→CMYK conversion and sets DeviceCMYK. It is **not** a
PDF/X-1a/X-4 file and has no output intent/ICC profile; ask your printer to check it, or convert with their profile.

## Generated content

Story templates, illustrations drawn by the procedural renderer, and the UI were written for this project.
Machine-generated text/images from external models are subject to the respective provider/model terms; their
copyright status depends on jurisdiction, and Bookmaker makes no claim about ownership of generated output.
