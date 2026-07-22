"""Contract tests for Roadplanner release automation."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("roadplanner_release_tool", ROOT / "tools" / "release.py")
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
import sys
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

Version = MODULE.Version
ReleaseError = MODULE.ReleaseError

assert str(Version.parse("3.1.0")) == "3.1.0"
assert Version.parse("3.1.1") > Version.parse("3.1.0")

# A command failure with capture disabled must surface as ReleaseError rather
# than masking the original failure with a NoneType traceback.
try:
    MODULE.run(
        [sys.executable, "-c", "raise SystemExit(7)"],
        capture=False,
    )
except ReleaseError as err:
    assert "Command failed" in str(err)
else:
    raise AssertionError("Non-captured command failures must raise ReleaseError")

try:
    Version.parse("v3.1.0")
except ReleaseError:
    pass
else:
    raise AssertionError("Version parser must reject a tag prefix")

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    integration = root / "custom_components" / "roadplanner_mcp"
    integration.mkdir(parents=True)
    (integration / "manifest.json").write_text(
        json.dumps({"version": "3.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (integration / "const.py").write_text(
        'INTEGRATION_VERSION = "3.0.0"\n', encoding="utf-8"
    )
    (root / "CHANGELOG.md").write_text(
        """# Changelog

## [Unreleased]

### Added

- Automated release preparation.

### Fixed

- Release tags always use a lower-case v prefix.

## [3.0.0] - 2026-07-22

### Added

- Baseline.
""",
        encoding="utf-8",
    )

    body = MODULE.cut_changelog(Version.parse("3.1.0"), "2026-07-23", root=root)
    assert "Automated release preparation" in body
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n## [3.1.0] - 2026-07-23" in changelog
    assert "## [3.0.0] - 2026-07-22" in changelog
    assert "lower-case v prefix" in MODULE.release_section(changelog, "3.1.0")
    notes = MODULE.release_notes_text(Version.parse("3.1.0"), root=root)
    assert notes.startswith("# Roadplanner 3.1.0\n\n## Added")
    assert "### Added" not in notes

    MODULE.replace_version_files(Version.parse("3.1.0"), root=root)
    manifest = json.loads((integration / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "3.1.0"
    assert 'INTEGRATION_VERSION = "3.1.0"' in (
        integration / "const.py"
    ).read_text(encoding="utf-8")

    cache = integration / "__pycache__"
    cache.mkdir()
    (cache / "example.cpython-312.pyc").write_bytes(b"cache")
    loose = integration / "other.pyc"
    loose.write_bytes(b"cache")
    removed_dirs, removed_files = MODULE.cleanup_python_caches(root=root)
    assert removed_dirs == 1
    assert removed_files == 1
    assert not cache.exists()
    assert not loose.exists()

print("Release automation contract tests passed.")

release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
    encoding="utf-8"
)
assert "workflow_dispatch:" in release_workflow
assert 'tag="v${VERSION}"' in release_workflow
assert '"V${VERSION}"' in release_workflow
assert 'gh release create "${args[@]}"' in release_workflow
assert '--target "$GITHUB_SHA"' in release_workflow
assert "contents: write" in release_workflow

validation_workflow = (
    ROOT / ".github" / "workflows" / "roadplanner-validation.yml"
).read_text(encoding="utf-8")
assert "python tools/release.py check" in validation_workflow
assert "pull_request:" in validation_workflow
assert "push:" not in validation_workflow
assert "uses: hacs/action@main" in validation_workflow

print("Release workflow contract tests passed.")
