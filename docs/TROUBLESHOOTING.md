# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `externally-managed-environment` from pip | Use the virtualenv: `python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt` (or `./run.sh`). |
| UI shows a blank page / 404 on `/` | The UI is not built: `cd frontend && npm ci && npm run build`, then restart uvicorn. In dev use `npm run dev` (port 5173). |
| Every API call returns 401 | `BOOKMAKER_API_TOKEN` is set: enter the same token under *Models & providers → API token*. |
| "The offline story engine supports 6-18 pages" | The built-in engine has 18 beats per arc. Use 6–18 pages or connect an LLM provider (up to 40 pages). |
| The story ignores my exact topic | The built-in engine is template-based and maps the topic to the nearest of 4 arcs (stars/helping, friendship/kite, courage/night, nature/stream). Connect an LLM for free-form topics. |
| "first_person narration is not supported by the offline engine" in the plan engine note | Offline arcs are written in third person; LLM providers support first person. |
| Book status `needs_review` | A PDF was produced but QA found unresolved errors. Open *Quality report*: each issue shows the stage and page. Fix the page (edit text, regenerate image, approve image) and click *Rebuild PDF*. |
| Book status `failed` | No valid PDF. See the error under the title and the *Logs* tab (the failing stage and message). Typical: provider down with no fallback, disk quota reached. Resume with ▶ after fixing. |
| `text_overflow` in the layout section | The page text is too long for the page size/age font range even after the automatic shortening. Shorten the text, choose a larger page size, landscape orientation or a lower age group/text amount. |
| `text_in_image` on AI images | The model painted letters. Regenerate (new seed), strengthen the negative prompt, or use a model better at clean illustrations. Disable OCR only if it produces false positives: `BOOKMAKER_ENABLE_OCR=false`. |
| `ocr: unavailable` in image metrics | Optional OCR deps not installed: `.venv/bin/pip install -r backend/requirements-optional.txt`. On minimal Linux images, `opencv-python` (non-headless, pulled in by rapidocr) shadows the headless build and fails with `ImportError: libGL.so.1`. Fix: `.venv/bin/pip uninstall -y opencv-python && .venv/bin/pip install --force-reinstall --no-deps opencv-python-headless==5.0.0.93` (`run.sh` does this automatically). |
| `character_colours_weak` warnings with AI images | Heuristic colour check could not find the character's signature colours. Use reference-image workflows (IP-Adapter in ComfyUI, `images/edits`), keep outfit colours distinctive, or review manually and approve the image. |
| `center_cropped` warning | The image API only supports fixed sizes; the result was cropped to your aspect ratio. Use a provider that supports custom sizes (ComfyUI/A1111) to avoid cropping. |
| Provider test: `Cannot connect to …` | Wrong URL/port, service not running, or a firewall/container network issue (inside Docker use the host IP instead of `localhost`). |
| Provider test: `Authentication failed (401)` | Wrong/expired key. Keys are never retried; the fallback provider (if any) is used during generation. |
| `Rate limited (429)` in usage | Automatic retries honour `Retry-After`. Set *Max requests / minute* on the provider to avoid it. |
| HTTP 402 when creating a book | The run uses paid providers; tick *I accept the estimated cost* (API: `"confirm_cost": true`). |
| HTTP 409 "Another task … is already queued" | One task per book at a time. Wait for it (see *Logs*) or pause. |
| HTTP 507 | `BOOKMAKER_MAX_DISK_GB` reached. Delete books (moved to `data/trash/`, then remove that folder) or raise the limit. |
| Server was killed during generation | Just restart it: interrupted jobs are re-queued on startup and continue from the last finished stage/page. |
| Persian letters appear disconnected in a viewer | Make sure you opened the generated PDF (fonts are embedded and shaped). Copy-pasting Persian text out of the PDF yields presentation-form characters in visual order. See the known limitations in TEST_REPORT.md. |
| Recover a deleted book | Files are in `data/trash/<id>-<timestamp>/` with `project.json` (settings + plan). |
