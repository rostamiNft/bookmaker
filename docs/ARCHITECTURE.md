# Architecture

```
┌──────────── Browser (React SPA, en/fa, RTL) ─────────────┐
│ Dashboard · New book · Workspace · Preview · Providers   │
└───────────────┬──────────────────────────────────────────┘
                │ relative /api calls (same origin)
┌───────────────▼──────────────── FastAPI (backend/app) ───────────────────────────┐
│ api/routes.py    auth · rate limit · validation · safe file serving · downloads   │
│ pipeline/worker  durable DB job queue · 1 job per book · restart recovery         │
│ pipeline/orchestrator  plan → characters → images → pdf → qa (resumable)           │
│   story/         offline_engine (templates) · llm_engine (prompts/JSON/repair)     │
│                  validator (independent story QA)                                  │
│   illustration/  scene (procedural renderer) · prompts (AI prompts) · image_qa     │
│   layout/        text (fonts, shaping, bidi, wrap/fit) · engine (layouts+verify)   │
│                  book (page sequence) · pdf (ReportLab) · pdf_qa (pypdf+pdfium)    │
│   providers/     base interfaces · registry/router (retry, fallback, usage, cost)  │
│                  offline · llm_http (Ollama/OpenAI-compat/Anthropic)               │
│                  image_http (OpenAI images/edits, ComfyUI, AUTOMATIC1111)          │
│ SQLite (WAL) · data/projects/<id>/{images,refs,pdf,render,uploads}                │
└──────────────────────────────────────────────────────────────────────────────────┘
```

## Design principles

* **Separation of concerns.** The UI only talks to the REST API. Story, illustration and layout engines do not know
  which provider produced their inputs: they exchange the same structured contracts (`schemas.py`: `BookSettings`,
  `StoryPlan`, `CharacterSpec`, `Scene`, `PageSpec`). Layout never imports providers.
* **Structured facts, not free text.** Characters (appearance, outfit colours, palette, distinctive items), locations
  and per-page scenes are stored as data. Later stages read those facts instead of re-inventing them, so a character's
  look cannot drift between pages unless the Character Bible is edited.
* **Never trust a successful API call.** Independent QA checks the story, every image, every layout and the final PDF.
* **Local repair, bounded loops.** An issue is attributed to a stage (`story`, `image`, `layout`, `pdf`) and only that
  part is redone; attempts, time and budget are limited, and unresolved issues are reported (`needs_review`), never hidden.

## Pipeline (pipeline/orchestrator.py)

| Stage | What happens | Repair / bounds |
|---|---|---|
| `plan` | Offline engine or LLM produces a `StoryPlan` (summary, moral, characters, locations, arc, pages with text + English illustration brief + structured scene). `story/validator.py` checks it. | LLM: invalid JSON → re-ask; validation issues → *targeted revision* prompt listing only the problems (max `max_story_attempts`). LLM failure → offline engine fallback (if page count supported). Offline: alternative seeds. |
| `characters` | Character Bible reference sheet for every character (procedural, or AI with a reference-sheet prompt). | AI sheets are checked; users can upload/approve references. |
| `images` | One task per page (bounded thread pool). Prompt from bible + scene; reference images and fixed seeds are passed to the provider; output is centre-cropped (never stretched) to the exact aspect ratio; `image_qa` checks it; optional vision-model review. Then near-duplicate detection (perceptual hash) across pages and the cover. | Per page up to `max_image_attempts` new variants/seeds. Duplicates: the later page is regenerated once. Locked/approved images are never touched. |
| `pdf` | `layout/book.py` builds cover, title, copyright, story pages, end page, back cover; `layout/engine.py` fits text by measurement and verifies geometry; `layout/pdf.py` renders; `layout/pdf_qa.py` re-opens and renders every page. | Text overflow → the story stage shortens that page (offline: shorter variant; LLM: "make it shorter") and layout is retried (max 2 rounds). PDF is written atomically (`.tmp` → rename). |
| `audio` | Only when `narration_audio` is on: one MP3 per page from the TTS provider (offline eSpeak NG or OpenAI-compatible `/v1/audio/speech`). Audio is keyed by a hash of text+voice+speed, so edits re-record only the changed page. | TTS failure → warning on that page (book still completes); paid TTS needs cost confirmation. |
| `qa` | Re-validates the *current* story (after user edits), collects image/layout/PDF issues → `completed`, `completed_with_warnings`, `needs_review` or `failed`. | – |

**Outline gate** (`review_outline`): after `plan` the job ends with status `awaiting_review`; no image is generated.
`POST /approve-outline` (after optional text edits) queues the remaining stages.

**Cast sheet**: `characters` also renders a line-up of all characters at their relative heights (`appearance.scale` overrides
species defaults; a cat is never drawn as tall as a child). With an AI image provider and `cast_reference`, the line-up is
passed as an extra reference image to every page, the same idea as ZFRNodes' contact-sheet conditioning.

Partial operations reuse the same building blocks: *regenerate image* (one page → pdf → qa), *rewrite text*
(one page → its image only if the brief changed → pdf → qa), *rebuild* (pending images → pdf → qa),
*regenerate cover*, *inpaint* (masked region of one page → pdf → qa), *narrate* (audio only), *exports* (EPUB/HTML/ZIP
from the current version; EPUBCheck when available).

**Revisions**: before every text edit, rewrite, regeneration, repair or restore, `snapshot_page()` stores the page's text,
brief, image file and QA in `page_revisions` (max 50 per page; image files are never deleted, so every kept revision stays restorable).
Restoring is itself snapshotted, so it can be undone too.

**Region repair** (`illustration/inpaint.py`): the mask (painted in the browser) is normalised, capped at 60% coverage,
dilated/feathered; the engine (OpenCV Telea/NS, IOPaint, A1111/sd.cpp img2img+mask, ComfyUI inpaint, OpenAI edits) produces a
candidate, which is composited back through the mask. `changed_outside_mask` is measured and must be 0, then image QA runs again.

### Resume, pause, recovery

* `project.stage_state` stores each stage's status; pages store `image_status` (`pending/generated/failed/approved`).
  A resumed job skips finished stages and already generated pages.
* Jobs live in the `jobs` table. On startup `recover_interrupted()` re-queues jobs left `running` by a crash.
  A job that raises is retried (`max_attempts`) and then marked failed with the error visible in the UI.
* Pause sets `pause_requested`; the pipeline stops at the next checkpoint (between pages/attempts), cancels queued
  page tasks, keeps finished work and marks the book `paused`.
* Only one active job per book (`JobConflict` → HTTP 409); identical re-submissions are idempotent.

## Story engines

* **Offline engine** (`story/themes.py`, `story/offline_engine.py`): four hand-written bilingual arcs (lost star,
  kite festival, brave night, silent stream) with 18 beats each. A beat has text variants, optional detail and humour
  sentences, an English illustration brief and a structured scene. Beats have priorities: the N most essential are
  kept, so 6–18 page books stay coherent (episodes are self-contained). Topic keywords and fantasy level choose the
  arc; age group and text amount choose detail; ages 3-5 get sentence splitting. It is *not* AI and cannot follow an
  arbitrary topic. It maps the topic to the closest arc (documented limitation).
* **LLM engine** (`story/llm_engine.py`): one schema-constrained JSON prompt; robust JSON extraction; normalisation
  sanitises unknown species/locations/colours/character ids so downstream stages stay safe; validation-driven revision.
* **Validator** (`story/validator.py`): page count/order, setup/inciting/climax/ending structure, early climax, words
  per page by age and language, sentence length, word length (EN), duplicates and near-duplicates (shingle Jaccard),
  repeated sentences, placeholder/nonsense text, preachy moral phrases (EN+FA), unknown characters, text↔scene
  character mismatch (whole-word, Persian-safe), protagonist presence, unfinished ending.

## Illustration

* **Procedural illustrator** (`illustration/scene.py`, `figures.py`): Pillow vector drawing at 2× supersampling.
  Nine location types × day/sunset/night, weather, ~70 props, children/elders/7 animal species with 19 poses and 12
  expressions, art-style finishing (watercolour, crayon, paper cut-out, gouache, flat). Characters are drawn from the
  bible, which makes consistency deterministic. It returns character bounding boxes and palettes for QA. No text is ever drawn.
* **AI image providers**: prompts from `illustration/prompts.py` (style + scene + fixed character descriptions +
  negative prompt against text, extra characters and deformities). Consistency beyond prompting: approved reference
  sheets are sent as reference images (OpenAI `images/edits`, ComfyUI `{{ref_image_N}}` placeholders for
  IP-Adapter/PuLID workflows, A1111 ControlNet/IP-Adapter unit), and fixed seeds per page.
* **Image QA** (`illustration/image_qa.py`): decodable/non-empty, resolution, exact aspect, blank/flat, exposure,
  perceptual hash duplicates, OCR stray-text detection (RapidOCR, optional), character presence/extra characters and
  signature-colour consistency inside the character's box (renderer metadata), or a weaker whole-image colour
  heuristic for AI images (reported as a warning), plus an optional vision-LLM review (relevance, text, anatomy).

## Layout & PDF

* Fonts: **Andika** (literacy font for Latin) and **Vazirmatn** (Persian, also Latin), both OFL; DejaVu fallback.
  Per-character font fallback runs; missing glyphs raise a layout error instead of printing tofu.
* RTL: Arabic-script shaping with `arabic-reshaper`, bidi via `python-bidi`. Wrapping is done on logical text and bidi is
  applied per line (keeps line order). Latin runs inside RTL lines are wrapped in LRE/PDF embeddings so brackets and
  punctuation in English phrases stay correct.
* Layout candidates per page (side-by-side for landscape, stacked image/text otherwise, with decreasing image share);
  the first one whose text fits at the largest font size in the age range wins. Images are placed at their exact aspect
  ratio. `verify_layout` checks page bounds, safe margins, line widths, vertical overflow, text/image overlap and
  minimum font size.
* ReportLab output: embedded TrueType subsets, `/Lang`, title/author/subject/keywords/creator, images resampled to the
  output DPI (150 screen / 300 print) as JPEG.
* **Print options**: bleed (0–5 mm; the page box grows, `TrimBox`/`BleedBox` are set, full-bleed images extend into the
  bleed), crop marks (drawn outside the trim in the slug), CMYK (images converted with a naive profile-less RGB→CMYK and
  embedded as DeviceCMYK; this is **not** PDF/X). Full-bleed layout: the illustration covers the page and the text sits on
  a translucent panel; `verify_layout` checks the panel stays inside the safe area. Known limitation: on some scenes the
  bottom text panel can cover the characters' feet/legs (the illustrator does not yet reserve that area).
* Persian text carries `/ActualText` spans (logical order) so copy/paste and screen readers get correct text from
  visually ordered glyphs. `pdf_qa` verifies them with pypdf; note that PDFium/pdftotext-style extractors ignore
  ActualText, so their output may still look reversed.
* `pdf_qa`: header, strict parse, page count and sizes, metadata, language, fonts embedded, illustrations present on
  expected pages, text extractable (EN), every page rasterised with PDFium (blank and identical-page detection), file
  size bounds; renders are reused for the in-app preview.

## Providers (providers/)

`Router.run()` wraps every call: pacing (`rpm`), retries with exponential backoff, `Retry-After` on 429, no retries
for auth/config errors, fallback chain (`fallback_id`, cycle-safe), budget check before paid calls, usage record
(tokens, images, duration, cost from configured pricing, redacted error). Adding a provider = implementing
`LLMProvider`, `ImageProvider` (optionally `inpaint()`), `VisionProvider` or `TTSProvider` and registering it in `registry.build()`.
Kinds: `llm`, `image`, `vision`, `inpaint`, `tts`; each has a default in *Models & providers*.

## Data model (models.py)

`Project` (settings, story_plan metadata, stage_state, qa_report, version, pdf_file, owner_id), `Page`, `Character`
(stable `char_id` + bible `spec` + reference), `HistoryEntry` (user/system/qa actions with before/after),
`StageLog` (input/output/errors per step), `Job`, `ProviderConfig` (encrypted key), `UsageRecord`, `AppSetting`,
`PageRevision` (undo snapshots), `User` (scrypt password hash, role admin/user, active flag), `AuthSession` (SHA-256 of the
session token, expiry).

## Resource management

* No heavy model is loaded in-process; GPU work happens in separate services (Ollama/ComfyUI/A1111) or APIs, so the app
  runs on a 2-CPU / 4 GB machine without a GPU.
* Bounded concurrency: books (`MAX_CONCURRENT_BOOKS`), offline renders per book (`MAX_CONCURRENT_IMAGES`), external image
  APIs sequential per book, plus per-provider `rpm` pacing.
* Images are opened only when needed; the PDF step resamples to the target DPI; OCR model is loaded lazily once.
* Disk quota check before new books; deleted books go to `data/trash/`; backup zip export per book.
* Cost/time estimate before every run; paid runs require confirmation; per-book budget cap.

## Security

Three auth modes (`BOOKMAKER_AUTH_MODE`): `none` (local single user), `token` (shared bearer token), `accounts` (users with
login; first visitor creates the admin via `/api/auth/setup`, existing local books are transferred to that admin; scrypt
password hashes; random session tokens stored only as SHA-256; 5 failed logins → 15-minute lock per username; admin-only
user and provider management; every project query is filtered by `owner_id`, other users' books answer 404). Media links
opened outside `fetch` (img/audio/download) carry `?token=`. Per-IP rate limiting,
request-size limit, Pydantic validation of all input, `safe_join` against path traversal, file serving restricted to
image/JSON types inside the project dir, uploads validated with Pillow (type, size, pixel count) and re-encoded,
encrypted API keys, masked keys in the UI, log redaction, no stack traces in production, security headers, delete
confirmation and trash instead of hard delete, atomic PDF writes.
