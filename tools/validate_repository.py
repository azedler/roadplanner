#!/usr/bin/env python3
"""Run lightweight repository validation without external dependencies."""

from __future__ import annotations

import argparse
import compileall
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"

REQUIRED_MANIFEST_KEYS = {
    "domain",
    "name",
    "version",
    "documentation",
    "issue_tracker",
    "codeowners",
}

SUSPICIOUS_PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OAuth bearer token": re.compile(r"\bBearer\s+[0-9A-Za-z._-]{20,}"),
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def validate_json_files() -> int:
    count = 0
    for path in ROOT.rglob("*.json"):
        if any(part.startswith(".") and part != ".devcontainer" for part in path.parts):
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:  # noqa: BLE001
            fail(f"Invalid JSON in {path.relative_to(ROOT)}: {err}")
        count += 1
    return count


def validate_manifest() -> str:
    path = INTEGRATION / "manifest.json"
    if not path.exists():
        fail("Missing integration manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    if missing:
        fail(f"manifest.json is missing: {', '.join(missing)}")
    if manifest.get("domain") != "roadplanner_mcp":
        fail("The compatibility domain must remain roadplanner_mcp")
    if not manifest.get("codeowners"):
        fail("manifest.json must have at least one code owner")
    return str(manifest["version"])


def validate_layout() -> None:
    custom_components = ROOT / "custom_components"
    integrations = [p for p in custom_components.iterdir() if p.is_dir()]
    if integrations != [INTEGRATION]:
        fail("HACS repository must contain exactly custom_components/roadplanner_mcp")
    for required in (ROOT / "README.md", ROOT / "hacs.json", INTEGRATION / "__init__.py"):
        if not required.exists():
            fail(f"Missing required file: {required.relative_to(ROOT)}")


def scan_secrets() -> int:
    checked = 0
    text_suffixes = {".py", ".js", ".json", ".yaml", ".yml", ".md", ".txt", ".toml"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SUSPICIOUS_PATTERNS.items():
            if pattern.search(text):
                fail(f"Potential {label} in {path.relative_to(ROOT)}")
        checked += 1
    return checked


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip full secret scan")
    args = parser.parse_args()

    validate_layout()
    version = validate_manifest()
    json_count = validate_json_files()
    if not compileall.compile_dir(INTEGRATION, quiet=1, force=True):
        fail("Python compilation failed")
    scanned = 0 if args.quick else scan_secrets()

    print(f"Roadplanner repository validation passed (version {version}).")
    print(f"JSON files validated: {json_count}")
    if not args.quick:
        print(f"Text files scanned for obvious secrets: {scanned}")


if __name__ == "__main__":
    main()
