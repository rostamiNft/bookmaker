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
| uharfbuzz *(optional)* | 0.56.3 | Apache-2.0 | HarfBuzz shaping (opt-in `shaping=harfbuzz`, automatic when installed) |
| pyphen *(optional)* | 0.18.1 | **tri-licence: GPL-2.0-or-later OR LGPL-2.1-or-later OR MPL-1.1** (the distribution ships `COPYING.GPL`, `COPYING.LGPL` and `COPYING.MPL`; you may choose the terms that suit you). Used unmodified as a library, and **off by default**: hyphenation is a per-book setting | hyphenation patterns for Latin scripts |
| pg8000 *(optional, deploy)* | 1.31.5 | BSD-3-Clause | PostgreSQL driver (pure Python, no libpq) - `requirements-deploy.txt` |
| redis *(optional, deploy)* | 8.1.0 | MIT | optional shared worker locks |
| PyJWT *(optional, deploy)* | 2.15.1 | MIT | OIDC ID-token verification (single sign-on) |
| PyAV *(optional)* | 18.1.0 | BSD-3-Clause (verified from the wheel metadata) - bundles FFmpeg 8.1.2, built as **LGPLv3+** and, in this build, **linking libx264/libx265, which are GPL**). WebM/VP9+Opus output is royalty-free; MP4/H.264 output is subject to the GPL and is therefore behind an explicit confirmation (HTTP 402 without it) | video export |
| pgserver *(dev/test only)* | 0.1.4 | Apache-2.0 (verified from the wheel's `LICENSE`) | starts a real PostgreSQL 16 for `tests/test_postgres_redis.py` |
| aiosmtpd *(dev/test only)* | 1.4.6 | Apache-2.0 | real SMTP server for the password-reset test |
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

* `color_mode: cmyk` (without PDF/X) converts images with a naive, profile-less RGB→CMYK conversion and sets
  DeviceCMYK. It is **not** a PDF/X file and has no output intent; ask your printer to check it.
* `print_standard: pdfx4` converts through a real ICC profile and adds the PDF/X-4 structures
  (`/OutputIntent` with `DestOutputProfile`, XMP, `/ID`, `Trapped`, TrimBox/BleedBox). The bundled profile
  `assets/icc/ISOcoated_v2_300_mth.icc` is **CC0** (see `assets/icc/README-amethyst.md`); a printer's own
  profile can be used instead. The app's check is **mechanical, not a certification** - run a real preflight
  (Adobe Acrobat, veraPDF) before sending files to a print shop.

## ICC profiles (bundled in `assets/icc`)

| Profile | Licence | Note |
|---|---|---|
| ISOcoated_v2_300_mth.icc (ECI) | free to use and redistribute; the ECI profiles are published for unrestricted use | default PDF/X-4 output intent |
| Amethyst CMYK profiles (experimental) | CC0 (`assets/icc/LICENSE-CC0-amethyst.txt`) | alternative gamuts; a hard proof is recommended |

## Video output caveat

The optional video export uses PyAV, which bundles FFmpeg built as LGPLv3+ **and** links libx264/libx265 (GPL).
The default container is WebM with VP9 + Opus - both codecs are royalty-free - so a default install produces
no GPL-covered output. MP4/H.264 is only produced after `confirm_gpl`/the UI confirmation (the API answers 402
otherwise); distributing such an MP4 is subject to the GPL terms of x264.

## Generated content

Story templates, illustrations drawn by the procedural renderer, and the UI were written for this project.
Machine-generated text/images from external models are subject to the respective provider/model terms; their
copyright status depends on jurisdiction, and Bookmaker makes no claim about ownership of generated output.
