#!/usr/bin/env bash
# One-time setup for a Linux cloud session (Claude Code on the web, Codex, CI).
# Installs system libraries, the locked Python packages and the two verified
# models, then runs the test suite. Safe to re-run; never touches Git state.
set -euo pipefail
cd "$(dirname "$0")/.."

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq
    # FFmpeg for export/verification; EGL/GL/xkb/fontconfig/dbus for offscreen Qt (PySide6).
    $SUDO apt-get install -y -qq ffmpeg libegl1 libgl1 libopengl0 libglib2.0-0 libxkbcommon0 \
        libfontconfig1 libdbus-1-3 >/dev/null
else
    echo "apt-get not found: install FFmpeg and Qt runtime libraries manually." >&2
fi

PYTHON=""
for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
done
if [ -z "$PYTHON" ]; then echo "Python 3.11-3.13 is required." >&2; exit 1; fi
"$PYTHON" -c 'import sys; assert (3, 11) <= sys.version_info[:2] <= (3, 13), sys.version' \
    || { echo "Unsupported $($PYTHON --version); use Python 3.11-3.13 (3.12 preferred)." >&2; exit 1; }

if [ ! -x .venv/bin/python ]; then "$PYTHON" -m venv .venv; fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-lock.txt
.venv/bin/python -m pip install -q --no-deps -e .
.venv/bin/python scripts/download_model.py

export QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software
ffmpeg -hide_banner -version | head -1
echo "Running tests (failures are reported, setup itself is complete)..."
.venv/bin/python -m pytest -q || echo "Some tests failed; see output above. Rotation tests need FFmpeg 8+ (-display_rotation)."
