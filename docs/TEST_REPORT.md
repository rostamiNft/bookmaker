# Test report and feature status

Date: 2026-10-15 · Environment: 2 vCPU, 3.8 GB RAM, no GPU, Python 3.11.2, Node 22, outbound network limited to
GitHub/PyPI/npm (no model downloads, no model APIs, no API keys).

## Automated test results (real runs)

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration + end-to-end | `cd backend && ../.venv/bin/python -m pytest -q` | **216 passed**, 0 failed (250 s): [log](test-results/backend-pytest.txt) |
| Frontend UI (Vitest + Testing Library, jsdom) | `cd frontend && npm test` | **10 passed**, 0 failed: [log](test-results/frontend-vitest.txt) |

What the backend suite covers:

* **Story** (`test_story.py`, 104 tests incl. 96 parametrised plans): EN/FA × 3 age groups × 4 lengths × fantasy levels ×
  endings all produce valid plans; fantasy 0 never uses talking-animal arcs; topic→arc mapping; ZWNJ and Persian
  names; page-limit errors are explicit; determinism; text regeneration gives new wording with the same facts;
  validator catches duplicates, missing climax/ending, unknown characters, preachy morals, unfinished endings; Persian-safe name matching.
* **LLM engine** (`test_llm_engine.py`): JSON extraction from noisy output, sanitising unknown species/locations/colours/ids,
  repair after unparseable output, targeted revision when the page count is wrong, single-page rewrite.
* **Illustration** (`test_illustration.py`): every page of a 14-page book renders all expected characters/props and passes
  QA; no near-duplicates; wrong colours → `character_inconsistent`; missing/extra characters detected; empty, corrupt,
  blank, low-resolution, wrong-aspect images rejected; OCR finds painted text; crop-to-aspect never stretches; reference sheets.
* **Layout/PDF** (`test_layout_pdf.py`, 72 tests): 64 combinations (EN/FA × 4 page sizes × 2 orientations × 2 ages × 2 text
  amounts) fit with zero geometric issues and exact image aspect; overflow raises instead of clipping; overlap/out-of-page
  detection; Persian wrapping keeps logical line order; mixed Persian/English bidi; full PDFs in EN and FA are valid, have the
  expected page count, embedded fonts, metadata, `/Lang`, all pages rasterise; validator detects wrong page count,
  missing images and garbage files.
* **Providers** (`test_providers.py`): encryption/masking/redaction, path traversal, config validation, retry on 429 with
  `Retry-After`, no retry on 401 + fallback chain, bounded retries, budget cap; **real HTTP** against mock servers for the
  OpenAI-compatible chat + vision, Ollama, OpenAI images (generations and edits with references, 429/500 recovery) and
  ComfyUI (workflow placeholders, upload, polling).
* **End-to-end API** (`test_api_e2e.py`): full book + PDF download + history; regenerate one image (only that page
  changes, new PDF version); edit, reorder, add and delete pages + rebuild; character colour edit invalidates and
  redraws affected pages; Persian book; pause in the middle of illustration and resume without redoing finished pages;
  crash recovery (job left `running` is re-queued and finished); duplicate task rejection (409); broken image API →
  automatic fallback to the built-in illustrator, failures recorded in usage without leaking the key; complete book via an
  **LLM + image API over HTTP** (paid-run confirmation 402, cost accounting, references sent, LLM page rewrite); upload
  validation (script disguised as PNG rejected), auth token, input validation; delete → trash; backup zip; system stats and estimates.

UI tests: dashboard, create-book form (preset + topic → correct request, navigation), blocked start on estimate
warnings, page editing + image regeneration, progress + QA report, book-like preview navigation (spread/single), PDF
download link, provider add/test, Persian RTL switch, API error display. Backend responses are mocked in these tests.
**Not done:** browser-based visual UI testing (no headless browser could be downloaded in this environment).

## Acceptance books (generated through the live web API, then inspected page by page)

| Book | Settings | Result |
|---|---|---|
| `samples/sample-fa-lost-star.pdf`: *سامان و ستاره‌ی کوچولوی گمشده* | fa, 6-8, 14 story pages, watercolor, A4 | 19 PDF pages (cover, title, copyright, 14 story, end, back cover), 1.3 MB, QA: 0 errors / 0 warnings, fonts Vazirmatn embedded, `/Lang fa` |
| `samples/sample-en-silent-stream.pdf`: *Mia and the Silent Stream* | en, 6-8, 14 story pages, gouache, A4 | 19 PDF pages, 1.6 MB, QA: 0 errors / 0 warnings, Andika embedded, `/Lang en`, text extractable |

Contact sheets: `samples/sample-*-pages.jpg`; full per-page data (text, prompts, attempts, QA): `samples/sample-*-report.json`.
Each PDF was re-opened with pypdf and every page rasterised with PDFium; the renders were viewed manually.

## Defects found by testing/inspection and fixed at the root

1. Mouth arcs inverted (smiles drawn as frowns): PIL angle convention; fixed in `figures.py`.
2. Night tint made colours muddy; forest trees too tall and dark; basket drawn on the head in the "lift" pose; sleeping hero drawn standing. Fixed in the renderer.
3. OCR false positive: owl glasses read as "0.0". Stray text now requires ≥2 letters.
4. **PDF QA caught a non-embedded Helvetica** referenced on every page (ReportLab default font). Fixed with `initialFontName`.
5. Keyword collision crashed the final QA stage; the worker correctly marked the book `failed` instead of reporting success. Fixed.
6. Project briefly reported `completed` after a partial task was queued (stale state for polling clients). Now `queued` immediately.
7. Offline "rewrite text" could return identical text. Now enumerates all wordings and reports honestly when none exists.
8. Validator false positive: «پارسا» matched inside «پارسال». Now whole-word matching.
9. Sentence splitter ignored closing quotes (`.»`), giving false long-sentence warnings. Fixed.
10. Fallback from a failing AI image API to the procedural illustrator crashed during the character-reference stage. Fixed.
11. Pause race: queued page tasks kept running after a pause. They are now cancelled.
12. **Bidi defect**: brackets of English phrases inside Persian lines were misplaced (`(… CPU :label`). Fixed with LTR embeddings + regression test.
13. Wording problems found while reading the sample books (repeated "garden", lowercase sentence start, doubled "And"). Templates fixed, plus automatic sentence capitalisation.
14. (Test bug) A test deleted the last page, which removes the story's ending; QA correctly reported `no_ending`. The test was fixed, not the check.

## Feature status

### ✅ Implemented and tested
* Book projects: create, list/filter/search, open, edit, archive, delete-to-trash, backup zip, version history of PDFs, change history, stage logs.
* Settings form with all requested options, presets, live cost/time estimate, paid-run confirmation, budget cap.
* Built-in story engine (EN/FA, 6–18 pages, 4 arcs, age/length/humour/ending adaptation) + independent story validator.
* LLM story engine logic (plan, repair, targeted revision, page rewrite, shortening) against protocol-faithful mocks.
* Character Bible (structured, stable ids, palettes, reference sheets, user edits, uploaded references).
* Procedural illustrator (no GPU), cover generation, per-page tasks, bounded retries, duplicate detection, locked/approved images.
* Image QA (file, resolution, aspect, blank, exposure, duplicates, OCR text, character presence/extra/colour consistency).
* Layout engine (content-driven fitting, RTL/LTR, mixed scripts, page sizes/orientations, verification) and PDF (embedded fonts, metadata, language, DPI profiles) with structural + rendered validation.
* Provider layer: Ollama, OpenAI-compatible (chat, vision, images, edits), ComfyUI; retries, Retry-After, fallback, pacing, usage, cost, encrypted/masked keys, connection checks.
* Durable queue: restart recovery, pause/resume, one task per book, bounded concurrency, partial regeneration (image, text, cover), rebuild.
* Security controls: optional token auth, rate limiting, size limits, validation, path-traversal protection, safe uploads, redacted logs.
* Bilingual responsive UI (dashboard, creation, workspace, preview, QA, history, logs, settings, library, providers).
* Headless CLI (`backend/cli.py`).

### ⚠️ Implemented, partially verified
* **Real AI models were not exercised** (no network access to model hosts/APIs here). Adapters are tested against mock servers implementing the same HTTP protocols; behaviour with real services (latency, odd outputs) still needs validation by the user.
* Anthropic LLM/vision adapter and AUTOMATIC1111 image adapter are implemented but have **no automated test**.
* Vision-model review is implemented (adapter tested), but not exercised inside a full end-to-end run.
* Character consistency for AI images relies on references + fixed seeds + a colour heuristic (warning-level) + optional vision review. Quality depends on the model and workflow.
* "Actual" cost = provider-reported token/image usage × user-entered prices (APIs do not return prices).
* LLM path: Persian quality depends strongly on the model; small local models may need more revision attempts.

### ❌ Not implemented (future work)
* Inpainting/region repair of images (a defective image is regenerated as a whole).
* CPU diffusion adapter (stable-diffusion.cpp / FastSD CPU), so AI images without GPU or API.
* User accounts/roles (only a shared token; `owner_id` prepared), PostgreSQL/Redis/Celery deployment profile.
* Print-shop features: bleed, crop marks, CMYK/PDF-X. Hyphenation and justified text.
* Undo/revert from history (history is an audit log).
* Typeset text inside illustrations (the `text_in_image` option only relaxes the "no text" prompt rules).

## Known limitations

* The offline engine is a template system, **not AI**: it maps any topic to the closest of 4 arcs, writes third-person
  stories with 2 main characters + 1 helper, English or Persian, 6–18 pages.
* The procedural illustrations are simple flat vector art, consistent and child-friendly but far from painterly AI or human illustration.
* PDF text extraction: English extracts correctly; Persian extracts as presentation-form glyphs in visual order (reading is fine, copy/paste/search is degraded). Fix would need ActualText spans or a HarfBuzz-based renderer.
* Layout chooses from stacked and side-by-side templates (no full-bleed story pages yet); a 4:3 image on a portrait page leaves generous whitespace (choose 3:4 for portrait books).
* SQLite + in-process worker = single server instance. No hard CPU/RAM caps inside the app: use container limits.
* The Dockerfile was not built or tested in this environment.
