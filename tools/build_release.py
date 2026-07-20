#!/usr/bin/env python3
"""Build a manual Home Assistant installation ZIP from the repository."""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
VERSION = MANIFEST["version"]
OUT = ROOT / "dist" / f"Roadplanner_MCP_v{VERSION}_HA_install.zip"
OUT.parent.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(INTEGRATION.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        archive.write(path, path.relative_to(ROOT))

print(OUT)
