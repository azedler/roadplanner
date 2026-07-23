#!/usr/bin/env python3
"""Safe local development helpers for Roadplanner patches.

GitHub remains the source of truth. This tool can inspect a local clone, run the
canonical checks, apply a reviewed patch, and export staged changes. It never
commits, pushes, merges, tags, opens pull requests, or modifies Git remotes.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TOOL = ROOT / "tools" / "release.py"


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


def git(*arguments: str, capture: bool = True, check: bool = True) -> str:
    return run(
        ["git", *arguments],
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


def run_checks() -> None:
    require_repository()
    run(
        [sys.executable, str(RELEASE_TOOL), "check"],
        capture=False,
    )


def apply_patch(patch_path: Path, *, run_validation: bool = True) -> None:
    """Apply one reviewed patch to a clean worktree and optionally validate it."""
    require_clean_tree()
    patch = patch_path.expanduser().resolve()
    if not patch.is_file():
        raise DevError(f"Patch-Datei wurde nicht gefunden: {patch}")
    run(
        [
            "git",
            "apply",
            "--check",
            "--whitespace=error-all",
            str(patch),
        ],
        capture=True,
    )
    run(
        [
            "git",
            "apply",
            "--whitespace=error-all",
            str(patch),
        ],
        capture=True,
    )
    if run_validation:
        try:
            run_checks()
        except DevError as err:
            raise DevError(
                "Der Patch wurde angewendet, aber die Validierung ist fehlgeschlagen. "
                "Die Änderungen bleiben zur Prüfung im Arbeitsbaum.\n"
                f"{err}"
            ) from err


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


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Sichere lokale Patch- und Prüfautomation für Roadplanner."
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
        elif args.command == "export":
            output = export_patch(args.output, base=args.base)
            print(f"Patch exportiert: {output}")
        else:  # pragma: no cover - argparse enforces known commands.
            raise DevError(f"Unbekannter Befehl: {args.command}")
    except DevError as err:
        print(f"Fehler: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
