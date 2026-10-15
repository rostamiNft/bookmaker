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
| opencv-python-headless *(optional)* | 5.0.0.93 | Apache-2.0 | OCR pre-processing |
| numpy *(optional)* | 2.4.6 | BSD-3-Clause and others | OCR arrays |
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

## Fonts (bundled in `assets/fonts`, embedded as subsets in generated PDFs)

| Font | Licence | Source |
|---|---|---|
| Vazirmatn Regular/Bold | SIL Open Font License 1.1 (`assets/fonts/OFL-Vazirmatn.txt`) | github.com/rastikerdar/vazirmatn |
| Andika Regular/Bold (Latin subset, converted from the @fontsource WOFF files) | SIL Open Font License 1.1 (`assets/fonts/OFL-Andika.txt`) | SIL International via npm `@fontsource/andika` |
| DejaVu Sans (system fallback, not bundled) | Bitstream Vera / DejaVu licence | OS package |

OFL allows embedding in documents; the fonts may not be sold on their own.

## External services (not bundled, integrated over HTTP)

Ollama (MIT), llama.cpp (MIT), vLLM (Apache-2.0), ComfyUI (GPL-3.0), AUTOMATIC1111/Forge (AGPL-3.0), and hosted APIs
(OpenAI, Anthropic, OpenRouter …, governed by their terms of service). Bookmaker talks to them as separate processes
over their public HTTP APIs and does not include or link their code. **Model weights carry their own licences**
(e.g. FLUX.1-dev is non-commercial, SDXL uses OpenRAIL++-M, Llama uses the Llama Community Licence; InsightFace
models used by InstantID/PuLID are non-commercial). Check the model card before commercial use. See docs/RESEARCH.md.

## Generated content

Story templates, illustrations drawn by the procedural renderer, and the UI were written for this project.
Machine-generated text/images from external models are subject to the respective provider/model terms; their
copyright status depends on jurisdiction, and Bookmaker makes no claim about ownership of generated output.
