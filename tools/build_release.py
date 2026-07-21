#!/usr/bin/env python3
"""Build a deterministic manual Roadplanner installation archive."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text(encoding="utf-8"))
VERSION = str(MANIFEST["version"])
DIST = ROOT / "dist"
ARCHIVE = DIST / f"Roadplanner_MCP_v{VERSION}_HA_install.zip"
CHECKSUMS = DIST / f"Roadplanner_MCP_v{VERSION}_SHA256SUMS.txt"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def validate() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "validate_repository.py"), "--release"],
        cwd=ROOT,
        check=True,
    )


def iter_release_files() -> list[tuple[Path, Path]]:
    files: list[tuple[Path, Path]] = []
    for path in sorted(INTEGRATION.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append((path, path.relative_to(ROOT)))
    for name in ("LICENSE", "NOTICE", "README.md", "CHANGELOG.md"):
        path = ROOT / name
        files.append((path, Path(name)))
    return files


def write_file(archive: zipfile.ZipFile, source: Path, target: Path) -> None:
    info = zipfile.ZipInfo(str(target), FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, source.read_bytes())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    validate()
    DIST.mkdir(parents=True, exist_ok=True)
    ARCHIVE.unlink(missing_ok=True)
    CHECKSUMS.unlink(missing_ok=True)

    with zipfile.ZipFile(ARCHIVE, "w") as archive:
        for source, target in iter_release_files():
            write_file(archive, source, target)

    checksum = sha256(ARCHIVE)
    CHECKSUMS.write_text(f"{checksum}  {ARCHIVE.name}\n", encoding="utf-8")

    print(ARCHIVE)
    print(CHECKSUMS)


if __name__ == "__main__":
    main()
