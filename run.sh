#!/usr/bin/env bash
# One-command setup + start (Linux/macOS). Serves UI + API on http://0.0.0.0:8000
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -q -r backend/requirements.txt
if [ "${BOOKMAKER_NO_OCR:-0}" != "1" ]; then
  .venv/bin/pip install -q -r backend/requirements-optional.txt || echo "OCR extras not installed (optional)"
fi
if [ "${BOOKMAKER_NO_MEDIA:-0}" != "1" ]; then
  # EPUBCheck + Java runtime, offline narration (eSpeak NG is GPL-3.0; set BOOKMAKER_NO_MEDIA=1 to skip)
  .venv/bin/pip install -q -r backend/requirements-media.txt || echo "Media extras not installed (optional)"
fi
# rapidocr pulls the GUI build of OpenCV, which shadows the headless one and needs libGL: keep only headless
if .venv/bin/pip show -q opencv-python >/dev/null 2>&1; then
  .venv/bin/pip uninstall -q -y opencv-python
  .venv/bin/pip install -q --force-reinstall --no-deps opencv-python-headless==5.0.0.93
fi
if [ ! -d frontend/dist ]; then (cd frontend && npm ci && npm run build); fi
cd backend && exec ../.venv/bin/uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
