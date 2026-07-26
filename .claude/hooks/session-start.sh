#!/bin/bash
set -euo pipefail

# Roadplanner's CI and tools/release.py target Python 3.12 (navigation.py uses
# PEP 701 f-string syntax that only parses on 3.12+), but the base container
# image points python/python3 at 3.11. Re-point them to 3.12 when available so
# `python tools/release.py ...` works without specifying python3.12 explicitly.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PY312="$(command -v python3.12 || true)"
if [ -n "$PY312" ]; then
  ln -sf "$PY312" /usr/local/bin/python3
  ln -sf "$PY312" /usr/local/bin/python
fi
