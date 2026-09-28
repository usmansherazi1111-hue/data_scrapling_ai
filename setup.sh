#!/usr/bin/env bash
# One-time setup on macOS / Linux: virtual env, Python packages, browsers, .env
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,12), sys.version' \
  || { echo "Python 3.10-3.12 is required (set PYTHON=/path/to/python3.x to choose one)"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo "Installing browsers (about 500 MB)..."
.venv/bin/scrapling install
[ -f .env ] || cp .env.example .env
echo
echo "Setup complete. Start the app with:  .venv/bin/python main.py   (then open http://127.0.0.1:8000)"
