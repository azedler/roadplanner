"""Contract tests for the safe local Roadplanner development helper."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location("roadplanner_dev_tool", ROOT / "tools" / "dev.py")
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def command(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        list(arguments),
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


with tempfile.TemporaryDirectory() as directory:
    temporary = Path(directory)
    repository = temporary / "repo"
    repository.mkdir()
    command(repository, "git", "init", "-q")
    command(repository, "git", "config", "user.name", "Roadplanner Test")
    command(repository, "git", "config", "user.email", "test@example.invalid")
    target = repository / "example.txt"
    target.write_text("baseline\n", encoding="utf-8")
    command(repository, "git", "add", "example.txt")
    command(repository, "git", "commit", "-q", "-m", "baseline")

    target.write_text("patched\n", encoding="utf-8")
    patch = temporary / "change.patch"
    patch.write_text(
        command(
            repository,
            "git",
            "diff",
            "--binary",
            "--full-index",
            "HEAD",
            "--",
        ),
        encoding="utf-8",
    )
    command(repository, "git", "restore", "example.txt")

    MODULE.ROOT = repository
    MODULE.RELEASE_TOOL = repository / "tools" / "release.py"
    assert MODULE.status_lines()[-1] == "Arbeitsbaum: sauber"
    MODULE.apply_patch(patch, run_validation=False)
    assert target.read_text(encoding="utf-8") == "patched\n"

    command(repository, "git", "add", "example.txt")
    exported = temporary / "exported.patch"
    assert MODULE.export_patch(exported, base="HEAD") == exported.resolve()
    exported_text = exported.read_text(encoding="utf-8")
    assert "diff --git a/example.txt b/example.txt" in exported_text
    assert "-baseline" in exported_text
    assert "+patched" in exported_text

source = (ROOT / "tools" / "dev.py").read_text(encoding="utf-8")
for forbidden_call in (
    'git("commit"',
    'git("push"',
    'git("merge"',
    'git("tag"',
    'git("remote"',
    'gh ',
):
    assert forbidden_call not in source
assert "--whitespace=error-all" in source
assert "release.py" in source
print("Development helper tests passed.")
