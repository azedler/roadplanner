#!/usr/bin/env python3
"""Local HACS release preflight for the Roadplanner repository."""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REPOSITORY = "azedler/roadplanner"
EXPECTED_DOMAIN = "roadplanner_mcp"
REQUIRED_MANIFEST_KEYS = {
    "domain",
    "documentation",
    "issue_tracker",
    "codeowners",
    "name",
    "version",
}
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class PreflightError(RuntimeError):
    """Raised for a failed HACS preflight check."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise PreflightError(f"Missing required file: {path.relative_to(ROOT)}") from err
    except json.JSONDecodeError as err:
        raise PreflightError(
            f"Invalid JSON in {path.relative_to(ROOT)}: {err.msg} at line {err.lineno}"
        ) from err
    if not isinstance(value, dict):
        raise PreflightError(f"Expected a JSON object in {path.relative_to(ROOT)}")
    return value


def png_size(path: Path) -> tuple[int, int]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as err:
        raise PreflightError(f"Missing HACS brand asset: {path.relative_to(ROOT)}") from err
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise PreflightError(f"Brand asset is not a valid PNG: {path.relative_to(ROOT)}")
    return struct.unpack(">II", raw[16:24])


def check_required_root_files() -> None:
    for name in ("README.md", "LICENSE", "NOTICE", "hacs.json"):
        if not (ROOT / name).is_file():
            raise PreflightError(f"Missing required repository file: {name}")


def check_integration_layout() -> tuple[Path, dict[str, Any]]:
    root = ROOT / "custom_components"
    if not root.is_dir():
        raise PreflightError("Missing custom_components directory")
    integrations = sorted(path for path in root.iterdir() if path.is_dir())
    if len(integrations) != 1:
        names = ", ".join(path.name for path in integrations) or "none"
        raise PreflightError(
            "HACS integration repositories must contain exactly one directory under "
            f"custom_components; found: {names}"
        )
    integration = integrations[0]
    if integration.name != EXPECTED_DOMAIN:
        raise PreflightError(
            f"Expected integration directory {EXPECTED_DOMAIN}, found {integration.name}"
        )
    manifest = load_json(integration / "manifest.json")
    missing = sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    if missing:
        raise PreflightError(f"manifest.json is missing: {', '.join(missing)}")
    if manifest.get("domain") != EXPECTED_DOMAIN:
        raise PreflightError("manifest domain does not match the integration directory")
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise PreflightError(f"Unsupported manifest version: {version!r}")
    codeowners = manifest.get("codeowners")
    if not isinstance(codeowners, list) or "@azedler" not in codeowners:
        raise PreflightError("manifest codeowners must include @azedler")
    expected_base = f"https://github.com/{EXPECTED_REPOSITORY}"
    documentation = manifest.get("documentation")
    issue_tracker = manifest.get("issue_tracker")
    if not isinstance(documentation, str) or not documentation.startswith(expected_base):
        raise PreflightError("manifest documentation must point to the Roadplanner repository")
    if issue_tracker != f"{expected_base}/issues":
        raise PreflightError("manifest issue_tracker must point to the Roadplanner issue tracker")
    return integration, manifest


def check_hacs_manifest(version: str) -> None:
    hacs = load_json(ROOT / "hacs.json")
    if hacs.get("name") != "Roadplanner":
        raise PreflightError("hacs.json name must be Roadplanner")
    minimum = hacs.get("homeassistant")
    if not isinstance(minimum, str) or not VERSION_RE.fullmatch(minimum):
        raise PreflightError("hacs.json homeassistant must be a semantic version")
    if hacs.get("content_in_root") is True:
        raise PreflightError("content_in_root must not be enabled for this integration layout")
    if hacs.get("zip_release") is True:
        filename = hacs.get("filename")
        if not isinstance(filename, str) or not filename:
            raise PreflightError("zip_release requires a filename")
    if hacs.get("hide_default_branch") is not True:
        raise PreflightError(
            "hide_default_branch must be true so HACS users install published releases only"
        )
    if not isinstance(version, str):
        raise PreflightError("Internal error: manifest version missing")


def check_brand_assets() -> None:
    icon = ROOT / "brand" / "icon.png"
    width, height = png_size(icon)
    if width != height:
        raise PreflightError(f"HACS icon must be square, got {width}x{height}")
    if width < 256:
        raise PreflightError(f"HACS icon must be at least 256x256, got {width}x{height}")


def check_readme() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_fragments = (
        "HACS custom repository",
        "https://github.com/azedler/roadplanner",
        "custom_components/roadplanner_mcp",
    )
    missing = [fragment for fragment in required_fragments if fragment not in readme]
    if missing:
        raise PreflightError(
            "README is missing HACS/install information: " + ", ".join(missing)
        )


def check_tag(tag: str | None, version: str) -> None:
    if tag is None:
        return
    normalized = tag[1:] if tag.startswith("v") else tag
    if normalized != version:
        raise PreflightError(
            f"Release tag {tag!r} does not match manifest version {version!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate Roadplanner before publishing a GitHub release for HACS."
    )
    parser.add_argument(
        "--tag",
        help="Optional GitHub release tag to compare with manifest version, e.g. v2.6.5",
    )
    args = parser.parse_args()

    try:
        check_required_root_files()
        integration, manifest = check_integration_layout()
        version = str(manifest["version"])
        check_hacs_manifest(version)
        check_brand_assets()
        check_readme()
        check_tag(args.tag, version)
    except PreflightError as err:
        print(f"HACS preflight failed: {err}", file=sys.stderr)
        return 1

    print("Roadplanner HACS preflight passed.")
    print(f"Repository: {EXPECTED_REPOSITORY}")
    print(f"Integration: {integration.relative_to(ROOT)}")
    print(f"Version: {version}")
    print("Distribution mode: GitHub release source archive (no custom ZIP asset required)")
    if args.tag:
        print(f"Release tag: {args.tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
