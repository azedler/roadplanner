#!/usr/bin/env python3
"""Run dependency-free Roadplanner repository validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

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

REQUIRED_ROOT_FILES = {
    "README.md",
    "ARCHITECTURE.md",
    "AI_DEVELOPMENT_CONTRACT.md",
    "ROADMAP.md",
    "BACKLOG.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "LICENSE",
    "NOTICE",
    "hacs.json",
}

REQUIRED_DEVELOPMENT_DOCS = {
    "docs/README.md",
    "docs/architecture/README.md",
    "docs/legacy/README.md",
    "docs/product/README.md",
    "docs/product/ROADPLANNER_3_0_VISION_UX_BLUEPRINT.md",
    "docs/development/README.md",
    "docs/development/REPOSITORY_STRUCTURE.md",
    "docs/development/DEVELOPMENT_WORKFLOW.md",
    "docs/development/PATCH_WORKFLOW.md",
    "docs/development/BRANCHING.md",
    "docs/development/COMMIT_CONVENTIONS.md",
    "docs/development/DEFINITION_OF_DONE.md",
    "docs/development/TEST_STRATEGY.md",
    "docs/development/RELEASE_PROCESS.md",
    "docs/development/RELEASE_CHECKLIST.md",
    "docs/development/PUBLICATION_CHECKLIST.md",
    "docs/development/TASK_TEMPLATE.md",
}

SUSPICIOUS_PATTERNS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    "Private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OAuth bearer token": re.compile(r"\bBearer\s+[0-9A-Za-z._-]{20,}"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[0-9A-Za-z_]{20,}"),
}

ROOT_ARTIFACT_PATTERNS = (
    "*.patch",
    "*_bundle.zip",
    "*_VALIDATION.log",
    "*_SHA256SUMS.txt",
    "Roadplanner_MCP_*.zip",
)

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
CONST_VERSION_RE = re.compile(r'^INTEGRATION_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
ADR_NAME_RE = re.compile(r"^ADR-(\d{3})-.+\.md$")


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def validate_layout() -> None:
    custom_components = ROOT / "custom_components"
    if not custom_components.exists():
        fail("Missing custom_components directory")
    integrations = sorted(p for p in custom_components.iterdir() if p.is_dir())
    if integrations != [INTEGRATION]:
        fail("HACS repository must contain exactly custom_components/roadplanner_mcp")

    for item in sorted(REQUIRED_ROOT_FILES | REQUIRED_DEVELOPMENT_DOCS):
        path = ROOT / item
        if not path.exists():
            fail(f"Missing required file: {item}")

    for required in (INTEGRATION / "__init__.py", INTEGRATION / "manifest.json"):
        if not required.exists():
            fail(f"Missing required file: {relative(required)}")

    if (ROOT / ".devcontainer").exists():
        fail("A repository-specific .devcontainer is not allowed without an approved task")

    for pattern in ROOT_ARTIFACT_PATTERNS:
        for path in ROOT.glob(pattern):
            fail(f"Temporary/generated artifact must not be committed at repository root: {path.name}")

    forbidden_runtime_dirs = {
        ".storage",
        ".roadplanner_archive",
        ".roadplanner_handoffs",
        "roadbook",
    }
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_dir() and path.name in forbidden_runtime_dirs:
            fail(f"Runtime/private directory must not be committed: {relative(path)}")
        if path.is_file() and (path.suffix == ".pyc" or path.name == "secrets.yaml"):
            fail(f"Runtime/private file must not be committed: {relative(path)}")
        if path.is_dir() and path.name == "__pycache__":
            fail(f"Python cache directory must not be committed: {relative(path)}")


def validate_json_files() -> int:
    count = 0
    for path in ROOT.rglob("*.json"):
        if ".git" in path.parts:
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:  # noqa: BLE001
            fail(f"Invalid JSON in {relative(path)}: {err}")
        count += 1
    return count


def validate_manifest_and_versions() -> str:
    manifest_path = INTEGRATION / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    if missing:
        fail(f"manifest.json is missing: {', '.join(missing)}")
    if manifest.get("domain") != "roadplanner_mcp":
        fail("The compatibility domain must remain roadplanner_mcp")
    if manifest.get("name") != "Roadplanner":
        fail("The visible integration name must remain Roadplanner")
    if not manifest.get("codeowners"):
        fail("manifest.json must have at least one code owner")

    version = str(manifest["version"])
    const_text = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    match = CONST_VERSION_RE.search(const_text)
    if not match:
        fail("const.py does not define INTEGRATION_VERSION")
    if match.group(1) != version:
        fail(
            "Version mismatch: manifest.json "
            f"has {version}, const.py has {match.group(1)}"
        )

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        fail(f"CHANGELOG.md has no section for current version {version}")

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if hacs.get("name") != "Roadplanner":
        fail("hacs.json name must be Roadplanner")
    if not hacs.get("homeassistant"):
        fail("hacs.json must declare the minimum Home Assistant version")

    return version


def validate_license() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8", errors="ignore")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        fail("LICENSE must contain the full Apache License 2.0 text")
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8", errors="ignore")
    if "Roadplanner" not in notice or "Copyright" not in notice:
        fail("NOTICE must contain Roadplanner attribution and copyright")


def validate_python_syntax() -> int:
    count = 0
    for path in ROOT.rglob("*.py"):
        if ".git" in path.parts:
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as err:
            fail(f"Python syntax error in {relative(path)}: {err}")
        count += 1
    return count


def validate_javascript_syntax() -> int:
    files = [p for p in ROOT.rglob("*.js") if ".git" not in p.parts]
    if not files:
        return 0
    node = shutil.which("node")
    if node is None:
        warn("Node.js is unavailable; JavaScript syntax validation was skipped")
        return 0
    for path in files:
        result = subprocess.run(
            [node, "--check", str(path)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            fail(f"JavaScript syntax error in {relative(path)}: {detail}")
    return len(files)


def validate_yaml_basics() -> int:
    count = 0
    for pattern in ("*.yaml", "*.yml"):
        for path in ROOT.rglob(pattern):
            if ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "\t" in text:
                fail(f"YAML file contains tab indentation: {relative(path)}")
            if not text.strip():
                fail(f"YAML file is empty: {relative(path)}")
            count += 1
    return count


def normalize_markdown_target(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    elif " " in target:
        target = target.split(" ", 1)[0]
    return unquote(target)


def validate_markdown_links() -> int:
    checked = 0
    for path in ROOT.rglob("*.md"):
        if ".git" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            target = normalize_markdown_target(match.group(1))
            if not target or target.startswith("#"):
                continue
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith(("mailto:", "tel:")):
                continue
            local = parsed.path
            if not local:
                continue
            resolved = (path.parent / local).resolve()
            try:
                resolved.relative_to(ROOT.resolve())
            except ValueError:
                fail(f"Markdown link escapes repository in {relative(path)}: {target}")
            if not resolved.exists():
                fail(f"Broken local Markdown link in {relative(path)}: {target}")
            checked += 1
    return checked


def validate_adrs() -> int:
    directory = ROOT / "docs" / "architecture" / "adr"
    files = sorted(path for path in directory.glob("ADR-*.md") if path.name != "README.md")
    if not files:
        fail("No Architecture Decision Records found")
    numbers: list[int] = []
    required_sections = ("## Context", "## Decision", "## Consequences", "## Rejected alternatives")
    for path in files:
        match = ADR_NAME_RE.match(path.name)
        if not match:
            fail(f"Invalid ADR filename: {relative(path)}")
        numbers.append(int(match.group(1)))
        text = path.read_text(encoding="utf-8")
        if "**Status:**" not in text:
            fail(f"ADR is missing status: {relative(path)}")
        for section in required_sections:
            if section not in text:
                fail(f"ADR is missing {section}: {relative(path)}")
    expected = list(range(min(numbers), max(numbers) + 1))
    if numbers != expected:
        fail(f"ADR numbering is not contiguous: {numbers}")
    return len(files)


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
                fail(f"Potential {label} in {relative(path)}")
        checked += 1
    return checked


def validate_git_release_state() -> None:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        warn("Git metadata unavailable; release working-tree check was skipped")
        return
    result = subprocess.run(
        [git, "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        fail(f"Unable to inspect Git working tree: {result.stderr.strip()}")
    if result.stdout.strip():
        fail("Release validation requires a clean Git working tree")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip link, JavaScript and secret scans")
    parser.add_argument("--release", action="store_true", help="Enable release-only checks")
    args = parser.parse_args()

    validate_layout()
    version = validate_manifest_and_versions()
    validate_license()
    json_count = validate_json_files()
    yaml_count = validate_yaml_basics()
    python_count = validate_python_syntax()
    adr_count = validate_adrs()

    javascript_count = 0
    link_count = 0
    scanned = 0
    if not args.quick:
        javascript_count = validate_javascript_syntax()
        link_count = validate_markdown_links()
        scanned = scan_secrets()

    if args.release:
        validate_git_release_state()

    print(f"Roadplanner repository validation passed (version {version}).")
    print(f"Python files validated: {python_count}")
    print(f"JavaScript files validated: {javascript_count}")
    print(f"JSON files validated: {json_count}")
    print(f"YAML files checked: {yaml_count}")
    print(f"ADRs validated: {adr_count}")
    print(f"Local Markdown links checked: {link_count}")
    if not args.quick:
        print("Repository text files scanned for obvious secrets.")


if __name__ == "__main__":
    main()
