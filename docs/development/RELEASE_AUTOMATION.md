# Release automation

Roadplanner releases use a two-stage, explicit workflow that is convenient from an iPad Codespace and safe for the permanent `main` branch.

```text
develop
  ↓ prepare (version, changelog, tests, commit, push, PR)
main
  ↓ publish (protected GitHub workflow)
tag + GitHub release
  ↓
HACS update
```

The automation never force-pushes, moves an existing tag, merges a pull request, or edits Home Assistant runtime data.

## Prerequisites

Codespaces normally provides all required tools:

```bash
git --version
gh --version
python --version
node --version
gh auth status
```

The repository must be clean before `prepare`, `publish`, or `sync` is used.

## 1. Record changes under Unreleased

Every user-visible feature or fix must first be documented below:

```markdown
## [Unreleased]
```

in `CHANGELOG.md`. `prepare` converts that section into the requested released version and creates a new empty Unreleased section.

## 2. Validate during development

Run the same canonical checks used by GitHub:

```bash
python tools/release.py check
```

This command:

- removes generated Python caches before and after tests,
- runs all Python contract tests,
- runs all JavaScript contract tests,
- checks the panel JavaScript syntax,
- runs the repository validator,
- runs the local HACS preflight for the manifest version.

The validator itself remains non-mutating and will continue to reject caches if another process leaves them behind.

## 3. Prepare a release on develop

Example:

```bash
python tools/release.py prepare 3.1.0 --remote
```

The command:

1. requires branch `develop` and a clean tree,
2. fetches `origin` and refuses branch divergence,
3. requires `develop` to contain current `origin/main`,
4. verifies that `3.1.0` is greater than the current version,
5. moves the Unreleased changelog content to `3.1.0`,
6. updates `manifest.json` and `const.py`,
7. runs all release checks,
8. commits the three release-preparation files,
9. after confirmation, pushes `develop`,
10. creates or reuses the release pull request to `main`.

For a non-interactive Codespaces command:

```bash
python tools/release.py prepare 3.1.0 --remote --yes
```

Without `--remote`, the release commit is created locally but nothing is pushed.

## 4. Review and merge the pull request

GitHub runs `Roadplanner validation`, CodeQL, and the configured repository checks. Merge only when required checks are green.

The automation deliberately does not merge the pull request. Human approval remains the publication boundary.

## 5. Publish after merge

Switch to current `main` and dispatch the protected release workflow:

```bash
git switch main
git pull --ff-only origin main
python tools/release.py publish 3.1.0 --watch --sync-develop
```

`publish` verifies that `main` contains the requested version and then dispatches `.github/workflows/release.yml`.

The GitHub workflow:

1. checks out the exact `main` commit,
2. runs all contract, repository and HACS checks,
3. creates the deterministic manual-install archive and checksum,
4. exports release notes from `CHANGELOG.md`,
5. refuses any existing `v3.1.0` tag,
6. creates a lower-case `v3.1.0` tag on the exact validated commit,
7. publishes the GitHub release and validated assets.

With `--watch`, Codespaces waits for the workflow result. With `--sync-develop`, a successful release is followed by a safe fast-forward of `develop` to `origin/main`.

The equivalent GitHub-only path is:

```text
Actions → Publish Roadplanner release → Run workflow
```

Enter the stable version without the `v` prefix.

## 6. Synchronize develop separately

If the release was created from the GitHub interface:

```bash
python tools/release.py sync
```

The command only performs fast-forward operations. It fails rather than creating an unexpected merge or rewriting shared history.

## 7. Export release notes

```bash
python tools/release.py notes 3.1.0
```

or:

```bash
python tools/release.py notes 3.1.0 --output dist/RELEASE_NOTES_v3.1.0.md
```

Release notes are derived from the exact matching changelog section, not from GitHub's automatic pull-request summary.

## 8. Safety and recovery

### A release tag already exists

The workflow stops. Tags are immutable release evidence and are never moved automatically. Investigate before deleting or replacing anything.

### The GitHub workflow fails

No GitHub release is published. Open the failed workflow, correct the cause on `develop`, prepare another patch release, and repeat the normal process.

### prepare fails after version files were changed

Inspect the error, correct the repository, and rerun the checks. To abandon the preparation before commit:

```bash
git restore CHANGELOG.md \
  custom_components/roadplanner_mcp/manifest.json \
  custom_components/roadplanner_mcp/const.py
```

### develop is behind main

```bash
git fetch origin
git switch develop
git merge --ff-only origin/main
git push origin develop
```

If fast-forwarding is impossible, stop and inspect the divergence. Never use a force push as a routine release fix.
