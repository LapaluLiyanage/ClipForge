#!/usr/bin/env bash
# ClipForge helper - macOS / Linux launcher
set -euo pipefail
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python || true)
if [ -z "$PY" ]; then
  echo "[ClipForge] Python 3.9+ is required but was not found."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[ClipForge] First run - creating a virtual environment..."
  "$PY" -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "[ClipForge] Starting helper..."
exec python -m clipforge.server "$@"
