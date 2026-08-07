"""Contract tests for the safe local Roadplanner development helper."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

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
    # Same reason as the identity above: this throwaway repository must not
    # depend on the developer's setup. A machine that signs commits through
    # an external helper made this test fail for a reason that has nothing
    # to do with what it checks.
    command(repository, "git", "config", "commit.gpgsign", "false")
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

    # The series command validates the full sequence in a temporary worktree
    # before touching the real clean repository.
    command(repository, "git", "reset", "--hard", "-q", "HEAD")
    first = repository / "first.txt"
    second = repository / "second.txt"
    first.write_text("first baseline\n", encoding="utf-8")
    second.write_text("second baseline\n", encoding="utf-8")
    command(repository, "git", "add", "first.txt", "second.txt")
    command(repository, "git", "commit", "-q", "-m", "series baseline")

    first.write_text("first patched\n", encoding="utf-8")
    patch_one = temporary / "one.patch"
    patch_one.write_text(command(repository, "git", "diff", "--binary", "--full-index", "HEAD", "--", "first.txt"), encoding="utf-8")
    command(repository, "git", "restore", "first.txt")
    second.write_text("second patched\n", encoding="utf-8")
    patch_two = temporary / "two.patch"
    patch_two.write_text(command(repository, "git", "diff", "--binary", "--full-index", "HEAD", "--", "second.txt"), encoding="utf-8")
    command(repository, "git", "restore", "second.txt")

    MODULE.apply_patch_series([patch_one, patch_two], run_validation=False)
    assert first.read_text(encoding="utf-8") == "first patched\n"
    assert second.read_text(encoding="utf-8") == "second patched\n"

    # Context exports include source and Git metadata, but explicitly omit
    # secrets, patch transport files and binary data. Duplicate ls-files
    # categories must not create duplicate ZIP entries.
    (repository / "secrets.yaml").write_text("google_places_api_key: secret\n", encoding="utf-8")
    patch_dir = repository / "patch-files"
    patch_dir.mkdir()
    (patch_dir / "do-not-export.patch").write_text("secret transport\n", encoding="utf-8")
    (repository / "binary.jpg").write_bytes(b"not-an-image")
    context_zip = MODULE.export_ai_context(temporary / "context.zip", include_check=False)
    with zipfile.ZipFile(context_zip) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert "repository/example.txt" in names
        assert "repository/first.txt" in names
        assert "repository/second.txt" in names
        assert "repository/secrets.yaml" not in names
        assert not any("patch-files" in name for name in names)
        assert "repository/binary.jpg" not in names
        metadata = archive.read("metadata.json").decode("utf-8")
        assert '"release_check_passed": null' in metadata
    assert context_zip.with_suffix(".zip.sha256").is_file()

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
assert "apply-series" in source
assert "context-export" in source
print("Development helper tests passed.")
