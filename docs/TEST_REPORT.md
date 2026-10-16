# Test report and feature status

Date: 2026-10-16 · Environment: 2 vCPU, 3.8 GB RAM, no GPU, Python 3.11.2, Node 22, outbound network limited to
GitHub/PyPI/npm (no model downloads, no model APIs, no API keys).

## Automated test results (real runs)

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration + end-to-end | `cd backend && ../.venv/bin/python -m pytest -q` | **256 passed**, 0 failed (486 s): [log](test-results/backend-pytest.txt) |
| Frontend UI (Vitest + Testing Library, jsdom) | `cd frontend && npm test` | **18 passed**, 0 failed: [log](test-results/frontend-vitest.txt) |
| **Browser end-to-end** (real Chromium 153 headless + real backend in `accounts` mode, no mocks) | `frontend/e2e/browser-e2e.mjs` | **14/14 checks passed**: [results + screenshots](test-results/browser-e2e/) |

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

* **New features** (`test_new_features.py`, `test_media_providers.py`): outline gate (`awaiting_review`, no images before
  approval, edits kept); print PDF (bleed boxes, crop marks, DeviceCMYK images, full-bleed pages, layout fallback);
  Persian `/ActualText`; revisions + restore (undo of undo); region repair with OpenCV (pixels outside the mask unchanged)
  and with AI providers over HTTP (OpenAI `/images/edits`, A1111/sd.cpp img2img+mask, ComfyUI inpaint workflow, IOPaint);
  FastSD CPU adapter; offline eSpeak NG + OpenAI-compatible TTS (MP3 per page, re-record only changed pages); EPUB passes
  EPUBCheck 5.3, HTML reader, images ZIP; accounts (setup, login, throttling, roles, isolation 404, disabled user, password
  change, `?token=` media); character scale/cast sheet; paid-TTS cost estimate and 402 confirmation for narration.

UI tests: dashboard, create-book form (preset + topic → correct request, navigation), blocked start on estimate
warnings, page editing + image regeneration, progress + QA report, book-like preview navigation (spread/single), PDF
download link, provider add/test, Persian RTL switch, API error display; login + first-run setup, outline approval,
revision restore, narration player + repair dialog, EPUB export status, character scale slider, preview read-along.
Backend responses are mocked in these Vitest tests.

**Browser E2E (real, 2026-10-15)** with headless Chromium against a fresh server (`BOOKMAKER_AUTH_MODE=accounts`):
first-run setup → admin login → Persian 8-page book with outline review + narration → status `awaiting_review` with no
images → approve → `completed`, 0 errors/0 warnings → MP3 on every page (10–23 s), Chromium plays it → paint a mask in
the repair dialog → OpenCV repair, `changed_outside_mask = 0`, old image kept as a revision → build EPUB in the UI →
EPUBCheck ✓ 5.3.0 → HTML reader is RTL with 11 sheets and 8 audio elements → cast line-up visible → Preview read-along
plays page 1, then page 2 automatically → second user "reader" gets 404 for the admin's book and sees an empty library →
no unexpected console errors (only the expected 401 before login and the deliberate 404).
Screenshots: `docs/test-results/browser-e2e/e2e-*.png`.

**Not re-run on 2026-10-16:** the sandbox no longer has Chromium's system libraries (`libnspr4`, `libnss3`, …) and the
Debian mirrors are unreachable, so `frontend/e2e/browser-e2e.mjs` cannot start a browser here today (it fails with
`error while loading shared libraries`, not with a test failure). The UI changes made since that run - typography,
print-standard and bubble controls in the create form, video/LoRA entries in the export panel, new i18n strings - are
covered by the 18 Vitest tests (`npm test`, green) and by `npm run build`; the browser E2E must be re-run on a machine
with Chromium before a release.

## Acceptance books (generated through the live web API, then inspected page by page)

| Book | Settings | Result |
|---|---|---|
| `samples/sample-fa-lost-star.pdf`: *سامان و ستاره‌ی کوچولوی گمشده* | fa, 6-8, 14 story pages, watercolor, A4 | 19 PDF pages (cover, title, copyright, 14 story, end, back cover), 1.3 MB, QA: 0 errors / 0 warnings, fonts Vazirmatn embedded, `/Lang fa` |
| `samples/sample-en-silent-stream.pdf`: *Mia and the Silent Stream* | en, 6-8, 14 story pages, gouache, A4 | 19 PDF pages, 1.6 MB, QA: 0 errors / 0 warnings, Andika embedded, `/Lang en`, text extractable |
| `samples/sample-fa-lost-star.epub` | fa, narration on | 3.2 MB, **EPUBCheck 5.3.0: 0 errors**, 14 story pages, **14 media overlays** (read-along, 268 s of narration) |
| `samples/sample-fa-lost-star.webm` | fa, narration on | 7.6 MB, **279 s**, 1280x720 @ 24 fps, VP9 + Opus, word highlighting from `engine_word_events`, probe: 2 streams, 6697 frames |
| `samples/sample-fa-print-pdfx4.pdf` | fa, PDF/X-4, A5 | 5.1 MB, 19 pages, QA 0/0, self-check `ok` (0 issues): `%PDF-1.6`, output intent FOGRA39 with a 4-component ICC profile, XMP, TrimBox on all 19 pages, 0 RGB operators |
| `samples/sample-fa-speech-bubbles.pdf` | fa, bubbles, full-bleed | 1.7 MB, 19 pages, QA 0 errors / 1 warning (unrelated: the offline story names the hero on 4/14 pages); every story page reports `layout=speech_bubbles balloons=1..3` |

All samples are regenerated by `scripts/make_samples.py` (real API + worker, built-in engines, 2026-10-16).
| `samples/sample-fa-lost-star.epub` | same Persian book + narration | EPUB 3, 3.2 MB, 14 pages with audio, **EPUBCheck 5.3.0: 0 errors, 0 warnings** |
| `samples/sample-fa-print-fullbleed-cmyk.pdf` | Persian, aspect 3:4, full-bleed layout, 3 mm bleed, crop marks, CMYK | 19 pages, 5.0 MB, MediaBox 648×895 pt, TrimBox = A4 (595×841), BleedBox +8.5 pt, all images DeviceCMYK, 89 ActualText spans, QA 0/0 |

Contact sheets: `samples/sample-*-pages.jpg`; full per-page data (text, prompts, attempts, QA): `samples/sample-*-report.json`.
Each PDF was re-opened with pypdf and every page rasterised with PDFium; the renders were viewed manually.
All samples were regenerated on 2026-10-15 with the current code through the live web API.

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
15. Full-bleed text panel bounding box was computed in the wrong coordinate system (verify_layout flagged overlap). Fixed.
16. RTL extraction check rewritten: PDFium ignores `/ActualText`, so the check reads the content stream with pypdf.
17. Rapidocr installs GUI OpenCV which shadows the headless build (`libGL.so.1` ImportError). `run.sh`/Dockerfile now keep only the headless build.
18. UI: the Users page requested the admin-only user list for normal users (403 in the console). Fixed (found by the browser E2E).
19. Mock ComfyUI server crashed on non-image uploads after the inpaint change (found by the full suite). Fixed.

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
* **Outline review gate** before illustration (API + UI + browser E2E).
* **Region repair (inpainting)**: mask painter in the UI, built-in OpenCV engine (CPU), AI engines via IOPaint, A1111/Forge/sd.cpp, ComfyUI, OpenAI edits (mock-protocol tests); pixels outside the mask are proven unchanged.
* **Revisions / undo** for every page change, restore from the UI.
* **User accounts** (`accounts` mode): first-run admin setup, login/logout, sessions, roles, per-user isolation, login throttling, user management UI.
* **Print PDF**: bleed, crop marks, TrimBox/BleedBox, CMYK images, full-bleed layout with typeset text panel (with automatic, reported fallback to classic when the crop would be too large).
* **Narration**: offline eSpeak NG (Persian + English) and OpenAI-compatible TTS; MP3 per page; player, Preview read-along, embedded in EPUB/HTML; paid-TTS cost estimate + confirmation.
* **Exports**: EPUB 3 validated by EPUBCheck, single-file HTML reader, images + text ZIP.
* **Character scale & cast line-up** (relative sizes enforced; used as an extra reference for AI images when `cast_reference` is on).
* **CPU diffusion adapters**: FastSD CPU (`fastsdcpu`), stable-diffusion.cpp via the A1111-compatible API.
* Persian `/ActualText` for correct copy/paste and screen readers.
* **Typography**: HarfBuzz shaping (uharfbuzz, Apache-2.0) with automatic fallback to arabic-reshaper; Pyphen
  hyphenation for Latin scripts; justified lines with **kashida stretching** first for Persian/Arabic (Benatia rules)
  and word spaces afterwards (`test_new_pipeline_features.py`: engine selection, kashida capacity, justification
  measured at the margin, hyphenation split points).
* **PDF/X-4 + ICC**: forced CMYK through a real ICC profile (bundled ISOcoated v2 300 %, CC0, or a user profile),
  `/OutputIntent` with `GTS_PDFX` + `DestOutputProfile`, XMP packet, `/ID`, `Trapped`, TrimBox/BleedBox on every page,
  plus a mechanical self-check that is proven to *reject* a plain screen PDF.
* **Speech bubbles inside the art**: balloons are placed over the picture from a saliency/face analysis, each with a
  visible tail (>= 10 pt) pointing at its speaker; verified by rasterising the composed PDF and checking the exact
  balloon rectangles (opaque light fill, differs from the same page composed without bubbles, no layout issues).
* **Word-level narration timings** (eSpeak word events, de-duplicated, off-by-one corrected, first word anchored at 0)
  -> karaoke highlighting in the HTML reader, EPUB 3 media overlays (SMIL + `media:duration`), and video captions.
* **Video export** (PyAV/FFmpeg): WebM VP9 + Opus by default (royalty-free); MP4/H.264 only after an explicit
  `confirm_gpl` (the bundled FFmpeg links x264, GPL-2+) - without it the API answers 402.
* **Per-character LoRA dataset export**: ZIP with `img/<repeats>_<trigger> <class>/` images, caption files, ready-to-run
  `kohya_config.toml` and `ai_toolkit.yaml`; the trained LoRA's `<lora:name:weight> trigger` is prepended to every
  page/reference/cast/cover prompt once it is configured.
* **Single sign-on (OIDC + PKCE)** and **password reset by e-mail**: tested against a real local OIDC provider
  (RSA-signed ID tokens, nonce/audience/issuer verified, state replay refused) and a real SMTP server (aiosmtpd).
  With no mail server configured the reset link is handed to an administrator instead of failing silently.
* **PostgreSQL / Redis deployment**: `BOOKMAKER_DATABASE_URL` switches the whole schema to PostgreSQL (pg8000,
  pure Python, BSD-3) with a real connection pool; the worker claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED` so
  several processes share one queue; `BOOKMAKER_REDIS_URL` upgrades the worker locks to Redis (falls back to
  in-process, never deadlocks). Verified against a real PostgreSQL 16 started locally by `pgserver`; `docker-compose.yml`
  ships postgres + redis + app + scalable workers.

### ⚠️ Implemented, partially verified
* **Real AI models were not exercised** (no network access to model hosts/APIs here). Adapters are tested against mock servers implementing the same HTTP protocols; behaviour with real services (latency, odd outputs) still needs validation by the user.
* Anthropic LLM/vision adapter has **no automated test**. AUTOMATIC1111 / sd.cpp / FastSD CPU / IOPaint / Kokoro-style TTS adapters are tested only against mock servers built from their documented APIs (no real server could be downloaded here).
* AI region repair, `cast_reference` with a real image model, and the OpenAI-compatible TTS were never run against real models.
* EPUBCheck needs the optional `epubcheck` + `jdk4py` wheels (~70 MB); without them EPUBs are reported "not checked".
* Offline narration quality: eSpeak NG is intelligible but robotic; it is GPL-3.0 and therefore an optional install.
* Vision-model review is implemented (adapter tested), but not exercised inside a full end-to-end run.
* Character consistency for AI images relies on references + fixed seeds + a colour heuristic (warning-level) + optional vision review. Quality depends on the model and workflow.
* "Actual" cost = provider-reported token/image usage × user-entered prices (APIs do not return prices).
* LLM path: Persian quality depends strongly on the model; small local models may need more revision attempts.

### ❌ Not implemented (future work)
* **Certified preflight** (veraPDF / Adobe Acrobat preflight). The PDF/X-4 output passes the app's own mechanical
  check; it was not run through an external, certified checker (none is installable here).
* **Celery**: the job queue is PostgreSQL-backed with `SKIP LOCKED` claiming and runs in worker threads/processes;
  there is no Celery integration and none is planned unless a need appears.
* **LoRA training itself** (GPU, kohya/ai-toolkit) is out of scope: the app exports a ready-to-train dataset and then
  uses the trained weights, it does not train.
* A **certified PDF/A** export (PDF/A-1b/-2b) - veraPDF would be GPL-3.0, so only PDF/X-4 was implemented.
* Real AI models: still unexercised (see below).

## Known limitations

* The offline engine is a template system, **not AI**: it maps any topic to the closest of 4 arcs, writes third-person
  stories with 2 main characters + 1 helper, English or Persian, 6–18 pages.
* The procedural illustrations are simple flat vector art, consistent and child-friendly but far from painterly AI or human illustration.
* PDF text extraction: Persian text now carries `/ActualText` (logical order), which Acrobat-style readers and pypdf honour; PDFium/pdftotext-style extractors ignore ActualText and still return visual-order presentation forms.
* Full-bleed layout: with 4:3 pictures on portrait pages the crop would be too large, so pages fall back to the classic layout (noted in the layout report); use aspect 3:4. The text panel can cover the characters' feet/legs on some scenes (the illustrator does not reserve the panel area yet).
* A failed job (e.g. a crash in a repair) marks the whole book `failed` until the next successful job; finished work is kept.
* SQLite + in-process worker = single server instance. For several workers/containers set `BOOKMAKER_DATABASE_URL`
  (PostgreSQL) and optionally `BOOKMAKER_REDIS_URL`; see `docs/ARCHITECTURE.md` and `docker-compose.yml`.
  No hard CPU/RAM caps inside the app: use container limits.
* The Dockerfile and `docker-compose.yml` were not built or tested in this environment (no Docker here); the
  PostgreSQL and Redis code paths they rely on are tested against real local servers.
