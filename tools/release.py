#!/usr/bin/env python3
"""Roadplanner release automation for Codespaces and GitHub.

The workflow is deliberately split into safe stages:

* ``check`` validates the current repository without modifying Git history.
* ``prepare`` cuts the changelog, updates versions, validates, commits, and can
  push ``develop`` and open the release pull request.
* ``publish`` dispatches the protected GitHub release workflow on ``main``.
* ``sync`` fast-forwards ``develop`` to the released ``main`` history.
* ``notes`` exports one changelog section as GitHub release notes.

No command force-pushes, moves an existing tag, merges a pull request, or writes
Home Assistant runtime data.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
MANIFEST = INTEGRATION / "manifest.json"
CONSTANTS = INTEGRATION / "const.py"
CHANGELOG = ROOT / "CHANGELOG.md"
DIST = ROOT / "dist"
REPOSITORY = "azedler/roadplanner"
DEVELOP_BRANCH = "develop"
MAIN_BRANCH = "main"
RELEASE_WORKFLOW = "release.yml"
VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CONST_VERSION_RE = re.compile(
    r'^(INTEGRATION_VERSION\s*=\s*)["\']([^"\']+)["\']', re.MULTILINE
)
RELEASE_HEADING_RE = re.compile(
    r"^## \[(?P<version>[^\]]+)\](?:\s+-\s+(?P<date>[^\n]+))?\s*$", re.MULTILINE
)


class ReleaseError(RuntimeError):
    """Raised for a safe, user-correctable release automation failure."""


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = VERSION_RE.fullmatch(value.strip())
        if not match:
            raise ReleaseError(
                f"Version must use stable semantic versioning X.Y.Z, got {value!r}."
            )
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run(
    command: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
    cwd: Path = ROOT,
) -> CommandResult:
    if not command:
        raise ReleaseError("Internal error: empty command")
    result = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        capture_output=capture,
        check=False,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        rendered = " ".join(command)
        raise ReleaseError(f"Command failed ({rendered}): {detail or result.returncode}")
    return CommandResult(result.stdout or "", result.stderr or "", result.returncode)


def git(*arguments: str, capture: bool = True, check: bool = True) -> str:
    if not command_exists("git"):
        raise ReleaseError("Git is required but was not found.")
    return run(["git", *arguments], capture=capture, check=check).stdout.strip()


def gh(*arguments: str, capture: bool = True, check: bool = True) -> str:
    if not command_exists("gh"):
        raise ReleaseError(
            "GitHub CLI (gh) is required for remote automation. "
            "Codespaces normally includes it."
        )
    return run(["gh", *arguments], capture=capture, check=check).stdout.strip()


def current_branch() -> str:
    return git("branch", "--show-current")


def current_commit(reference: str = "HEAD") -> str:
    return git("rev-parse", reference)


def require_clean_tree() -> None:
    status = git("status", "--porcelain")
    if status:
        raise ReleaseError(
            "The working tree is not clean. Commit, stash, or remove the listed "
            f"changes before continuing:\n{status}"
        )


def require_branch(expected: str) -> None:
    actual = current_branch()
    if actual != expected:
        raise ReleaseError(
            f"This command must run on {expected!r}; current branch is {actual!r}."
        )


def fetch_origin() -> None:
    git("fetch", "origin", "--prune", "--tags", capture=False)


def ensure_local_matches_remote(branch: str) -> None:
    local = current_commit("HEAD")
    remote = current_commit(f"origin/{branch}")
    if local != remote:
        raise ReleaseError(
            f"Local {branch} does not match origin/{branch}. "
            f"Run: git pull --ff-only origin {branch}"
        )


def ensure_local_can_push(branch: str) -> None:
    """Allow a local branch that is equal to or ahead of its remote.

    Release preparation may include committed feature work that has not yet
    been pushed from the Codespace. Divergence or a local branch behind its
    remote remains a hard stop.
    """

    remote_ref = f"origin/{branch}"
    result = run(
        ["git", "merge-base", "--is-ancestor", remote_ref, "HEAD"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(
            f"Local {branch} is behind or diverged from {remote_ref}. "
            f"Run: git pull --rebase origin {branch}"
        )


def ensure_main_is_ancestor() -> None:
    result = run(
        ["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseError(
            "develop does not contain the current origin/main history. Run:\n"
            "  git fetch origin\n"
            "  git merge --ff-only origin/main\n"
            "If fast-forwarding is impossible, inspect the branch divergence before continuing."
        )


def existing_release_tags(version: Version) -> list[str]:
    conflicts: list[str] = []
    for candidate in (f"v{version}", f"V{version}"):
        remote = run(
            ["git", "ls-remote", "--tags", "origin", f"refs/tags/{candidate}"],
            capture=True,
            check=False,
        ).stdout.strip()
        if remote:
            conflicts.append(candidate)
    return conflicts


def manifest_data(root: Path = ROOT) -> dict[str, object]:
    try:
        value = json.loads((root / MANIFEST.relative_to(ROOT)).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as err:
        raise ReleaseError(f"Unable to read manifest.json: {err}") from err
    if not isinstance(value, dict):
        raise ReleaseError("manifest.json must contain a JSON object")
    return value


def current_version(root: Path = ROOT) -> Version:
    value = manifest_data(root).get("version")
    if not isinstance(value, str):
        raise ReleaseError("manifest.json has no string version")
    return Version.parse(value)


def cleanup_python_caches(root: Path = ROOT) -> tuple[int, int]:
    removed_directories = 0
    removed_files = 0
    for path in sorted(root.rglob("__pycache__"), reverse=True):
        if ".git" in path.parts or not path.is_dir():
            continue
        shutil.rmtree(path)
        removed_directories += 1
    for pattern in ("*.pyc", "*.pyo"):
        for path in root.rglob(pattern):
            if ".git" in path.parts or not path.is_file():
                continue
            path.unlink()
            removed_files += 1
    return removed_directories, removed_files


def python_tests() -> list[Path]:
    return sorted(path for path in (ROOT / "tests").glob("test_*.py") if path.is_file())


def javascript_tests() -> list[Path]:
    return sorted(path for path in (ROOT / "tests").glob("test_*.mjs") if path.is_file())


def run_repository_checks(
    *,
    expected_version: Version | None = None,
    release: bool = False,
    build: bool = False,
) -> None:
    version = current_version()
    if expected_version is not None and version != expected_version:
        raise ReleaseError(
            f"Manifest version is {version}, expected {expected_version}."
        )
    if release:
        require_clean_tree()

    removed_dirs, removed_files = cleanup_python_caches()
    if removed_dirs or removed_files:
        print(
            f"Removed Python caches before validation: "
            f"{removed_dirs} directories, {removed_files} files.",
            flush=True,
        )

    if (ROOT / ".git").exists():
        run(["git", "diff", "--check"], capture=False)

    for test in python_tests():
        print(f"[python] {test.relative_to(ROOT)}", flush=True)
        run([sys.executable, str(test)], capture=False)

    if javascript_tests() or list(ROOT.rglob("*.js")):
        if not command_exists("node"):
            raise ReleaseError("Node.js is required for JavaScript release checks.")
        for test in javascript_tests():
            print(f"[node] {test.relative_to(ROOT)}", flush=True)
            run(["node", str(test)], capture=False)
        panel = INTEGRATION / "frontend" / "roadplanner-panel.js"
        if panel.exists():
            run(["node", "--check", str(panel)], capture=False)

    cleanup_python_caches()

    validator = [sys.executable, str(ROOT / "tools" / "validate_repository.py")]
    if release:
        validator.append("--release")
    run(validator, capture=False)

    tag = f"v{version}"
    run(
        [
            sys.executable,
            str(ROOT / "tools" / "hacs_preflight.py"),
            "--tag",
            tag,
        ],
        capture=False,
    )

    if build:
        if not release:
            raise ReleaseError("--build requires --release and a clean working tree")
        run([sys.executable, str(ROOT / "tools" / "build_release.py")], capture=False)

    print(f"Release checks passed for Roadplanner {version}.")


def replace_version_files(version: Version, root: Path = ROOT) -> None:
    manifest_path = root / MANIFEST.relative_to(ROOT)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ReleaseError("manifest.json must contain an object")
    manifest["version"] = str(version)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    constants_path = root / CONSTANTS.relative_to(ROOT)
    constants = constants_path.read_text(encoding="utf-8")
    updated, count = CONST_VERSION_RE.subn(
        lambda match: f'{match.group(1)}"{version}"', constants, count=1
    )
    if count != 1:
        raise ReleaseError("const.py does not contain exactly one INTEGRATION_VERSION")
    constants_path.write_text(updated, encoding="utf-8")


def changelog_matches(text: str) -> list[re.Match[str]]:
    return list(RELEASE_HEADING_RE.finditer(text))


def release_section(text: str, version: str) -> str:
    matches = changelog_matches(text)
    for index, match in enumerate(matches):
        if match.group("version") != version:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            raise ReleaseError(f"CHANGELOG section {version} is empty")
        return body
    raise ReleaseError(f"CHANGELOG.md has no section for {version}")


def cut_changelog(version: Version, release_date: str, root: Path = ROOT) -> str:
    path = root / CHANGELOG.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    matches = changelog_matches(text)
    if not matches or matches[0].group("version") != "Unreleased":
        raise ReleaseError("CHANGELOG.md must begin with a ## [Unreleased] section")
    if any(match.group("version") == str(version) for match in matches):
        raise ReleaseError(f"CHANGELOG.md already contains {version}")

    unreleased = matches[0]
    next_heading = matches[1].start() if len(matches) > 1 else len(text)
    body = text[unreleased.end() : next_heading].strip()
    if not body:
        raise ReleaseError(
            "CHANGELOG [Unreleased] is empty. Add the user-visible changes before "
            "preparing a release."
        )

    prefix = text[: unreleased.end()].rstrip()
    suffix = text[next_heading:].lstrip()
    replacement = (
        f"{prefix}\n\n"
        f"## [{version}] - {release_date}\n\n"
        f"{body}\n\n"
        f"{suffix}"
    )
    path.write_text(replacement.rstrip() + "\n", encoding="utf-8")
    return body


def release_notes_text(version: Version, root: Path = ROOT) -> str:
    changelog = (root / CHANGELOG.relative_to(ROOT)).read_text(encoding="utf-8")
    body = release_section(changelog, str(version))
    # Changelog sections live below a level-two version heading. Promote their
    # child headings by one level when exporting standalone GitHub notes.
    body = re.sub(
        r"^(#{3,6}) ",
        lambda match: "#" * (len(match.group(1)) - 1) + " ",
        body,
        flags=re.MULTILINE,
    )
    return f"# Roadplanner {version}\n\n{body}\n"


def write_release_notes(version: Version, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(release_notes_text(version), encoding="utf-8")
    return output


def pull_request_body(version: Version) -> str:
    body = release_notes_text(version)
    return f"""## Release

Roadplanner {version}

## User-visible changes

{body}
## Validation

- [x] Python contract tests
- [x] JavaScript contract tests
- [x] Repository validator
- [x] HACS preflight for `v{version}`
- [x] Python caches removed before validation

## Publication

After this pull request is merged, run:

```bash
python tools/release.py publish {version} --watch --sync-develop
```
"""


def confirm(message: str, *, assume_yes: bool) -> None:
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise ReleaseError(f"Confirmation required: {message}. Re-run with --yes.")
    answer = input(f"{message} [y/N] ").strip().lower()
    if answer not in {"y", "yes", "j", "ja"}:
        raise ReleaseError("Cancelled by user")


def open_or_create_release_pr(version: Version) -> str:
    gh("auth", "status", capture=True)
    DIST.mkdir(parents=True, exist_ok=True)
    body_path = DIST / f"PR_v{version}.md"
    body_path.write_text(pull_request_body(version), encoding="utf-8")
    title = f"Release Roadplanner {version}"

    raw = gh(
        "pr",
        "list",
        "--head",
        DEVELOP_BRANCH,
        "--base",
        MAIN_BRANCH,
        "--state",
        "open",
        "--json",
        "number,url",
        "--limit",
        "1",
        "-R",
        REPOSITORY,
    )
    try:
        entries = json.loads(raw or "[]")
    except json.JSONDecodeError:
        entries = []
    if isinstance(entries, list) and entries and isinstance(entries[0], dict):
        number = str(entries[0].get("number"))
        url = str(entries[0].get("url"))
        gh(
            "pr",
            "edit",
            number,
            "--title",
            title,
            "--body-file",
            str(body_path),
            "-R",
            REPOSITORY,
            capture=False,
        )
        print(f"Updated existing release pull request: {url}")
        return url

    url = gh(
        "pr",
        "create",
        "--base",
        MAIN_BRANCH,
        "--head",
        DEVELOP_BRANCH,
        "--title",
        title,
        "--body-file",
        str(body_path),
        "-R",
        REPOSITORY,
    )
    print(f"Created release pull request: {url}")
    return url


def prepare_release(args: argparse.Namespace) -> None:
    target = Version.parse(args.version)
    require_branch(DEVELOP_BRANCH)
    require_clean_tree()
    fetch_origin()
    ensure_local_can_push(DEVELOP_BRANCH)
    ensure_main_is_ancestor()

    before = current_version()
    if target <= before:
        raise ReleaseError(
            f"Target version {target} must be greater than current version {before}."
        )
    conflicts = existing_release_tags(target)
    if conflicts:
        raise ReleaseError(
            f"Release tag already exists for {target}: {', '.join(conflicts)}."
        )

    release_date = args.date or date.today().isoformat()
    try:
        date.fromisoformat(release_date)
    except ValueError as err:
        raise ReleaseError(
            f"Release date must use YYYY-MM-DD, got {release_date!r}."
        ) from err
    cut_changelog(target, release_date)
    replace_version_files(target)

    run_repository_checks(expected_version=target)

    git(
        "add",
        str(MANIFEST.relative_to(ROOT)),
        str(CONSTANTS.relative_to(ROOT)),
        str(CHANGELOG.relative_to(ROOT)),
        capture=False,
    )
    git("commit", "-m", f"chore: prepare Roadplanner {target} release", capture=False)

    if args.remote:
        confirm(
            f"Push {DEVELOP_BRANCH} and create/update the Roadplanner {target} pull request?",
            assume_yes=args.yes,
        )
        git("push", "origin", DEVELOP_BRANCH, capture=False)
        open_or_create_release_pr(target)
    else:
        print("Release preparation committed locally.")
        print(f"Push when ready: git push origin {DEVELOP_BRANCH}")
        print(
            "Then create the release pull request with GitHub or run:\n"
            f"  gh pr create --base {MAIN_BRANCH} --head {DEVELOP_BRANCH} "
            f"--title 'Release Roadplanner {target}'"
        )


def latest_workflow_run(head_sha: str) -> tuple[str, str] | None:
    # GitHub can take a few seconds to register a workflow_dispatch run.
    for _attempt in range(8):
        raw = gh(
            "run",
            "list",
            "--workflow",
            RELEASE_WORKFLOW,
            "--branch",
            MAIN_BRANCH,
            "--event",
            "workflow_dispatch",
            "--limit",
            "10",
            "--json",
            "databaseId,headSha,url,createdAt",
            "-R",
            REPOSITORY,
        )
        try:
            entries = json.loads(raw or "[]")
        except json.JSONDecodeError:
            entries = []
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("headSha") == head_sha:
                    return str(entry.get("databaseId")), str(entry.get("url"))
        time.sleep(2)
    return None


def publish_release(args: argparse.Namespace) -> None:
    target = Version.parse(args.version)
    require_branch(MAIN_BRANCH)
    require_clean_tree()
    fetch_origin()
    git("pull", "--ff-only", "origin", MAIN_BRANCH, capture=False)
    ensure_local_matches_remote(MAIN_BRANCH)

    if current_version() != target:
        raise ReleaseError(
            f"main contains version {current_version()}, not requested {target}. "
            "Merge the release preparation pull request first."
        )
    release_section(CHANGELOG.read_text(encoding="utf-8"), str(target))

    tag = f"v{target}"
    conflicting_tags = existing_release_tags(target)
    if conflicting_tags:
        joined = ", ".join(conflicting_tags)
        raise ReleaseError(
            f"Remote release tag already exists: {joined}. "
            "The automation never moves release tags."
        )
    for candidate in (tag, f"V{target}"):
        release_exists = gh("release", "view", candidate, "-R", REPOSITORY, check=False)
        if release_exists:
            raise ReleaseError(f"GitHub release {candidate} already exists")

    gh("auth", "status", capture=True)
    confirm(
        f"Dispatch the protected GitHub release workflow for Roadplanner {target}?",
        assume_yes=args.yes,
    )
    gh(
        "workflow",
        "run",
        RELEASE_WORKFLOW,
        "--ref",
        MAIN_BRANCH,
        "-f",
        f"version={target}",
        "-R",
        REPOSITORY,
        capture=False,
    )
    head_sha = current_commit()
    found = latest_workflow_run(head_sha)
    if found is None:
        print("Release workflow dispatched. Open GitHub Actions to follow progress.")
        return
    run_id, url = found
    print(f"Release workflow: {url}")
    if args.watch:
        run(["gh", "run", "watch", run_id, "--exit-status", "-R", REPOSITORY], capture=False)
        print(f"Roadplanner {target} release workflow completed successfully.")
        if args.sync_develop:
            sync_develop(argparse.Namespace(yes=True))
    elif args.sync_develop:
        print("--sync-develop requires --watch so the release is known to have completed.")


def sync_develop(_args: argparse.Namespace) -> None:
    require_clean_tree()
    fetch_origin()
    starting_branch = current_branch()
    if starting_branch != DEVELOP_BRANCH:
        git("switch", DEVELOP_BRANCH, capture=False)
    git("pull", "--ff-only", "origin", DEVELOP_BRANCH, capture=False)
    git("merge", "--ff-only", "origin/main", capture=False)
    git("push", "origin", DEVELOP_BRANCH, capture=False)
    print("develop now contains the released main history.")


def notes_command(args: argparse.Namespace) -> None:
    version = Version.parse(args.version)
    output = Path(args.output).resolve() if args.output else None
    text = release_notes_text(version)
    if output is None:
        print(text, end="")
        return
    write_release_notes(version, output)
    print(output)


def check_command(args: argparse.Namespace) -> None:
    expected = Version.parse(args.version) if args.version else None
    run_repository_checks(
        expected_version=expected,
        release=args.release,
        build=args.build,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, prepare, publish, and synchronize Roadplanner releases."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Run all local release checks")
    check_parser.add_argument("--version", help="Expected X.Y.Z version")
    check_parser.add_argument(
        "--release", action="store_true", help="Require a clean release working tree"
    )
    check_parser.add_argument(
        "--build", action="store_true", help="Build the manual installation archive"
    )
    check_parser.set_defaults(handler=check_command)

    prepare_parser = subparsers.add_parser(
        "prepare", help="Cut changelog, update version, validate, and commit on develop"
    )
    prepare_parser.add_argument("version", help="Target stable version X.Y.Z")
    prepare_parser.add_argument(
        "--date", help="Release date YYYY-MM-DD (defaults to today)"
    )
    prepare_parser.add_argument(
        "--remote",
        action="store_true",
        help="Push develop and create or reuse the release pull request",
    )
    prepare_parser.add_argument(
        "--yes", action="store_true", help="Skip the remote-action confirmation"
    )
    prepare_parser.set_defaults(handler=prepare_release)

    publish_parser = subparsers.add_parser(
        "publish", help="Dispatch the protected GitHub release workflow from main"
    )
    publish_parser.add_argument("version", help="Released stable version X.Y.Z")
    publish_parser.add_argument(
        "--watch", action="store_true", help="Wait for the GitHub workflow result"
    )
    publish_parser.add_argument(
        "--sync-develop",
        action="store_true",
        help="After a successful watched release, fast-forward develop to main",
    )
    publish_parser.add_argument(
        "--yes", action="store_true", help="Skip the workflow-dispatch confirmation"
    )
    publish_parser.set_defaults(handler=publish_release)

    sync_parser = subparsers.add_parser(
        "sync", help="Fast-forward develop to the released origin/main history"
    )
    sync_parser.set_defaults(handler=sync_develop)

    notes_parser = subparsers.add_parser(
        "notes", help="Export one version section from CHANGELOG.md"
    )
    notes_parser.add_argument("version", help="Version X.Y.Z")
    notes_parser.add_argument("--output", help="Write Markdown to this file")
    notes_parser.set_defaults(handler=notes_command)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except ReleaseError as err:
        print(f"Release automation failed: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Release automation cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
