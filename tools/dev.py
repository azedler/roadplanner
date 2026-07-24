#!/usr/bin/env python3
"""Safe local development helpers for Roadplanner changes.

GitHub remains the source of truth. This tool can inspect a local clone, run the
canonical checks, apply one or more reviewed patches, export staged changes and
create a bounded AI review context. It never commits, pushes, merges, tags,
opens pull requests, or modifies Git remotes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable, Sequence
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOOL = ROOT / "tools" / "release.py"

_TEXT_SUFFIXES = {
    "",
    ".css",
    ".csv",
    ".gitignore",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".roadplanner_archive",
    ".roadplanner_backups",
    ".roadplanner_handoffs",
    ".storage",
    ".vscode",
    "__pycache__",
    "archives",
    "backups",
    "dist",
    "documents",
    "handoffs",
    "node_modules",
    "patch-files",
    "photos",
    "receipts",
    "roadbook-data",
    "roadbooks",
    "travel-data",
    "trip-data",
    "uploads",
    "venv",
}
_SECRET_NAMES = {
    ".env",
    ".env.local",
    "configuration.yaml",
    "secrets.yaml",
    "secrets.yml",
}
_SECRET_SUFFIXES = {".key", ".p12", ".pem", ".token"}
_MAX_CONTEXT_FILE_BYTES = 2 * 1024 * 1024
_MAX_CONTEXT_TOTAL_BYTES = 40 * 1024 * 1024


class DevError(RuntimeError):
    """Raised for a safe, user-correctable development workflow failure."""


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd or ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        rendered = " ".join(command)
        raise DevError(
            f"Befehl fehlgeschlagen ({rendered}): {detail or result.returncode}"
        )
    return result


def git(*arguments: str, capture: bool = True, check: bool = True, cwd: Path | None = None) -> str:
    return run(
        ["git", *arguments],
        cwd=cwd,
        capture=capture,
        check=check,
    ).stdout.strip()


def require_repository() -> None:
    result = run(
        ["git", "rev-parse", "--show-toplevel"],
        capture=True,
        check=False,
    )
    if result.returncode:
        raise DevError("Dieser Befehl muss in einem Git-Klon ausgeführt werden.")
    repository_root = Path(result.stdout.strip()).resolve()
    if repository_root != ROOT.resolve():
        raise DevError(
            f"Git-Arbeitsverzeichnis {repository_root} passt nicht zu {ROOT.resolve()}."
        )


def status_lines() -> list[str]:
    require_repository()
    branch = git("branch", "--show-current") or "(detached HEAD)"
    commit = git("rev-parse", "--short=12", "HEAD")
    status = git("status", "--short")
    lines = [f"Branch: {branch}", f"Commit: {commit}"]
    if status:
        lines.append("Arbeitsbaum:")
        lines.extend(f"  {line}" for line in status.splitlines())
    else:
        lines.append("Arbeitsbaum: sauber")
    return lines


def require_clean_tree() -> None:
    require_repository()
    status = git("status", "--porcelain")
    if status:
        raise DevError(
            "Der Arbeitsbaum ist nicht sauber. Änderungen zuerst committen, "
            f"stashen oder entfernen:\n{status}"
        )


def run_checks_at(root: Path, *, capture: bool = False) -> str:
    result = run(
        [sys.executable, str(root / "tools" / "release.py"), "check"],
        cwd=root,
        capture=capture,
    )
    return result.stdout


def run_checks() -> None:
    require_repository()
    run_checks_at(ROOT, capture=False)


def _resolved_patches(patch_paths: Iterable[Path]) -> list[Path]:
    patches: list[Path] = []
    for raw in patch_paths:
        patch = raw.expanduser().resolve()
        if not patch.is_file():
            raise DevError(f"Patch-Datei wurde nicht gefunden: {patch}")
        if patch in patches:
            raise DevError(f"Patch wurde doppelt angegeben: {patch}")
        patches.append(patch)
    if not patches:
        raise DevError("Es wurde keine Patch-Datei angegeben.")
    return patches


def _apply_patch_at(root: Path, patch: Path) -> None:
    run(
        ["git", "apply", "--check", "--whitespace=error-all", str(patch)],
        cwd=root,
    )
    run(
        ["git", "apply", "--whitespace=error-all", str(patch)],
        cwd=root,
    )


def _apply_patch_series_at(root: Path, patches: Sequence[Path]) -> None:
    """Check and apply a complete immutable patch series atomically."""
    arguments = [str(patch) for patch in patches]
    run(
        ["git", "apply", "--check", "--whitespace=error-all", *arguments],
        cwd=root,
    )
    run(
        ["git", "apply", "--whitespace=error-all", *arguments],
        cwd=root,
    )


def apply_patch(patch_path: Path, *, run_validation: bool = True) -> None:
    """Apply one reviewed patch to a clean worktree and optionally validate it."""
    require_clean_tree()
    patch = _resolved_patches([patch_path])[0]
    _apply_patch_at(ROOT, patch)
    if run_validation:
        try:
            run_checks()
        except DevError as err:
            raise DevError(
                "Der Patch wurde angewendet, aber die Validierung ist fehlgeschlagen. "
                "Die Änderungen bleiben zur Prüfung im Arbeitsbaum.\n"
                f"{err}"
            ) from err


def apply_patch_series(
    patch_paths: Iterable[Path],
    *,
    run_validation: bool = True,
) -> None:
    """Validate a patch series in an isolated worktree before applying it."""
    require_clean_tree()
    patches = _resolved_patches(patch_paths)
    temporary_root = Path(tempfile.mkdtemp(prefix="roadplanner-patch-series-"))
    worktree = temporary_root / "worktree"
    added = False
    try:
        git("worktree", "add", "--detach", str(worktree), "HEAD")
        added = True
        _apply_patch_series_at(worktree, patches)
        if run_validation:
            run_checks_at(worktree, capture=False)
        # The isolated check proved that the complete series applies and works.
        # Git applies the immutable series as one operation to the real clean
        # worktree, so an unexpected late failure cannot leave half a series.
        _apply_patch_series_at(ROOT, patches)
    finally:
        if added:
            run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                check=False,
            )
        shutil.rmtree(temporary_root, ignore_errors=True)


def _ensure_export_is_complete(base: str) -> None:
    unstaged = run(
        ["git", "diff", "--quiet", "--"],
        capture=True,
        check=False,
    )
    if unstaged.returncode not in {0, 1}:
        raise DevError("Der ungestagte Git-Status konnte nicht geprüft werden.")
    status = git("status", "--porcelain")
    untracked = [line for line in status.splitlines() if line.startswith("??")]
    if unstaged.returncode == 1 or untracked:
        raise DevError(
            "Der Export enthält ausschließlich gestagte Änderungen. "
            "Bitte zuerst alle beabsichtigten Dateien mit `git add -A` stagen."
        )
    staged = run(
        ["git", "diff", "--cached", "--quiet", base, "--"],
        capture=True,
        check=False,
    )
    if staged.returncode == 0:
        raise DevError("Es gibt keine gestagten Änderungen für den Patch-Export.")
    if staged.returncode != 1:
        raise DevError(f"Die Basisreferenz ist ungültig oder nicht lesbar: {base}")


def export_patch(output_path: Path, *, base: str = "HEAD") -> Path:
    """Export all staged changes relative to ``base`` as a binary-safe patch."""
    require_repository()
    _ensure_export_is_complete(base)
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--cached",
                "--binary",
                "--full-index",
                "--no-ext-diff",
                base,
                "--",
            ],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode:
        output.unlink(missing_ok=True)
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise DevError(f"Patch-Export ist fehlgeschlagen: {detail or result.returncode}")
    if not output.stat().st_size:
        output.unlink(missing_ok=True)
        raise DevError("Der erzeugte Patch ist leer.")
    return output


def _safe_context_path(raw: str) -> PurePosixPath | None:
    try:
        path = PurePosixPath(raw)
    except ValueError:
        return None
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    lower_parts = {part.casefold() for part in path.parts}
    if lower_parts & {part.casefold() for part in _EXCLUDED_PARTS}:
        return None
    name = path.name.casefold()
    if name in _SECRET_NAMES or path.suffix.casefold() in _SECRET_SUFFIXES:
        return None
    if path.suffix.casefold() not in _TEXT_SUFFIXES:
        return None
    return path


def _context_files() -> list[Path]:
    result = run(
        [
            "git",
            "ls-files",
            "--cached",
            "--modified",
            "--others",
            "--exclude-standard",
            "-z",
        ]
    ).stdout
    paths: list[Path] = []
    seen: set[PurePosixPath] = set()
    total = 0
    for raw in result.split("\0"):
        safe = _safe_context_path(raw)
        if safe is None or safe in seen:
            continue
        seen.add(safe)
        source = ROOT / Path(*safe.parts)
        if not source.is_file():
            continue
        size = source.stat().st_size
        if size > _MAX_CONTEXT_FILE_BYTES:
            continue
        total += size
        if total > _MAX_CONTEXT_TOTAL_BYTES:
            raise DevError(
                "Der sichere Kontextabzug überschreitet 40 MiB. "
                "Bitte große generierte Dateien aus dem Repository entfernen."
            )
        paths.append(source)
    return sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_ai_context(
    output_path: Path,
    *,
    include_check: bool = True,
) -> Path:
    """Create a bounded source/context archive without secrets or travel data."""
    require_repository()
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    branch = git("branch", "--show-current") or "(detached HEAD)"
    commit = git("rev-parse", "HEAD")
    status = git("status", "--short")
    log = git("log", "--oneline", "--decorate", "-20")
    diff = git("diff", "--binary", "--full-index", "HEAD", "--")
    staged_diff = git("diff", "--cached", "--binary", "--full-index", "HEAD", "--")
    release_check = "Nicht ausgeführt."
    check_passed: bool | None = None
    if include_check:
        try:
            release_check = run_checks_at(ROOT, capture=True)
            check_passed = True
        except DevError as err:
            release_check = str(err)
            check_passed = False
    files = _context_files()
    metadata = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "branch": branch,
        "commit": commit,
        "working_tree_clean": not bool(status),
        "release_check_passed": check_passed,
        "file_count": len(files),
        "exclusions": {
            "secret_names": sorted(_SECRET_NAMES),
            "secret_suffixes": sorted(_SECRET_SUFFIXES),
            "binary_files": True,
            "ignored_files": True,
            "patch_files": True,
            "travel_and_media_data": True,
        },
    }
    context_md = f"""# Roadplanner AI context\n\n- Branch: `{branch}`\n- Commit: `{commit}`\n- Working tree clean: `{not bool(status)}`\n- Release check passed: `{check_passed}`\n- Source files: `{len(files)}`\n\nThe checked-out GitHub repository is authoritative. Never claim that a test\npassed unless it is listed in `RELEASE_CHECK.txt`. Do not invent coordinates,\nprovider IDs, addresses, commits, tags or release results.\n"""
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source in files:
            relative = source.relative_to(ROOT).as_posix()
            archive.write(source, f"repository/{relative}")
        archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False) + "\n")
        archive.writestr("CONTEXT.md", context_md)
        archive.writestr("GIT_STATUS.txt", status + ("\n" if status else ""))
        archive.writestr("GIT_LOG.txt", log + "\n")
        archive.writestr("GIT_DIFF.patch", diff + ("\n" if diff and staged_diff else "") + staged_diff)
        archive.writestr("RELEASE_CHECK.txt", release_check + ("\n" if release_check and not release_check.endswith("\n") else ""))
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{_sha256(output)}  {output.name}\n",
        encoding="utf-8",
    )
    return output


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Sichere lokale Patch-, Prüf- und Kontextautomation für Roadplanner."
    )
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Branch, Commit und Arbeitsbaum anzeigen")
    commands.add_parser("check", help="Vollständigen Roadplanner-Check ausführen")

    apply_command = commands.add_parser(
        "apply",
        help="Einen geprüften Patch auf einen sauberen Arbeitsbaum anwenden",
    )
    apply_command.add_argument("patch", type=Path)
    apply_command.add_argument(
        "--skip-check",
        action="store_true",
        help="Release-Check nach dem Anwenden ausnahmsweise überspringen",
    )

    series_command = commands.add_parser(
        "apply-series",
        help="Mehrere Patches isoliert prüfen und danach gemeinsam anwenden",
    )
    series_command.add_argument("patches", type=Path, nargs="+")
    series_command.add_argument(
        "--skip-check",
        action="store_true",
        help="Release-Check im temporären Worktree ausnahmsweise überspringen",
    )

    export_command = commands.add_parser(
        "export",
        help="Alle gestagten Änderungen als binärsicheren Patch exportieren",
    )
    export_command.add_argument("output", type=Path)
    export_command.add_argument(
        "--base",
        default="HEAD",
        help="Git-Basisreferenz für den Patch (Standard: HEAD)",
    )

    context_command = commands.add_parser(
        "context-export",
        help="Sicheren Repository-Abzug für KI-Entwicklung oder Review erzeugen",
    )
    context_command.add_argument("output", type=Path)
    context_command.add_argument(
        "--skip-check",
        action="store_true",
        help="Release-Check nicht in den Kontextabzug aufnehmen",
    )
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "status":
            print("\n".join(status_lines()))
        elif args.command == "check":
            run_checks()
        elif args.command == "apply":
            apply_patch(args.patch, run_validation=not args.skip_check)
            print(f"Patch angewendet: {args.patch.expanduser().resolve()}")
            if not args.skip_check:
                print("Vollständiger Roadplanner-Check erfolgreich.")
        elif args.command == "apply-series":
            apply_patch_series(args.patches, run_validation=not args.skip_check)
            print(f"Patchserie angewendet: {len(args.patches)} Patches")
            if not args.skip_check:
                print("Vollständiger Roadplanner-Check im isolierten Worktree erfolgreich.")
        elif args.command == "export":
            output = export_patch(args.output, base=args.base)
            print(f"Patch exportiert: {output}")
        elif args.command == "context-export":
            output = export_ai_context(args.output, include_check=not args.skip_check)
            print(f"KI-Kontext exportiert: {output}")
            print(f"SHA-256: {output.with_suffix(output.suffix + '.sha256')}")
        else:  # pragma: no cover - argparse enforces known commands.
            raise DevError(f"Unbekannter Befehl: {args.command}")
    except DevError as err:
        print(f"Fehler: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
