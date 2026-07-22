# Release automation

Roadplanner releases use one explicit human approval and otherwise run automatically from GitHub and HACS.

```text
develop
  ↓ prepare (version, changelog, tests, commit, push, PR)
release pull request
  ↓ human merge into main
GitHub Actions on main
  ↓ validate, build, tag, release, synchronize develop
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

The repository must allow GitHub Actions to write repository contents so the protected workflow can create the release tag, release assets, and a safe fast-forward of `develop`.

## 1. Record changes under Unreleased

Every user-visible feature or fix is documented below:

```markdown
## [Unreleased]
```

in `CHANGELOG.md`. `prepare` converts that section into the requested released version and creates a new empty Unreleased section.

## 2. Validate during development

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

## 3. Prepare a release on develop

Example:

```bash
python tools/release.py prepare 3.2.0 --remote
```

The command:

1. requires branch `develop` and a clean tree,
2. fetches `origin` and refuses branch divergence,
3. requires `develop` to contain current `origin/main`,
4. verifies that `3.2.0` is greater than the current version,
5. moves the Unreleased changelog content to `3.2.0`,
6. updates `manifest.json` and `const.py`,
7. runs all release checks,
8. commits the release-preparation files,
9. after confirmation, pushes `develop`,
10. creates or updates the release pull request to `main`.

For a non-interactive Codespaces command:

```bash
python tools/release.py prepare 3.2.0 --remote --yes
```

## 4. Review and merge the pull request

GitHub runs the Roadplanner validation workflow, official HACS validation and repository security checks.

Merge only when required checks are green. The merge is the single human publication approval.

## 5. Automatic publication after merge

The push of the merged release commit to `main` automatically starts:

```text
.github/workflows/release.yml
```

The workflow:

1. reads the exact version from `manifest.json`,
2. verifies a matching `CHANGELOG.md` section,
3. skips safely when the matching tag already exists,
4. runs all contract, repository and HACS checks against the exact merge commit,
5. creates the deterministic manual-install archive and checksum,
6. exports release notes from `CHANGELOG.md`,
7. creates a lower-case tag such as `v3.2.0`,
8. publishes the GitHub release and validated assets,
9. fast-forwards `develop` to the released `main` commit when this is safe.

No manual workflow dispatch is required. This avoids Codespaces token restrictions such as `Resource not accessible by integration`.

## 6. Observe or verify publication

Publication continues even if the Codespace is closed. To watch it from the iPad after the merge:

```bash
git switch main
git pull --ff-only origin main
python tools/release.py publish 3.2.0 --watch --sync-develop
```

`publish` no longer starts the workflow. It finds the automatic run for the current `main` commit, optionally watches it, verifies that the GitHub release exists, and can run a fallback synchronization of `develop`.

The same status is visible under:

```text
GitHub → Actions → Publish Roadplanner release
```

`workflow_dispatch` remains available only as a manual fallback when an automatic run was cancelled or GitHub was temporarily unavailable.

## 7. Synchronize develop separately

Normally the release workflow fast-forwards `develop` automatically. If it reports that `develop` already contains new commits, synchronize later after reviewing the branch:

```bash
python tools/release.py sync
```

The command only performs fast-forward operations. It fails rather than creating an unexpected merge or rewriting shared history.

## 8. Export release notes

```bash
python tools/release.py notes 3.2.0
```

or:

```bash
python tools/release.py notes 3.2.0 --output dist/RELEASE_NOTES_v3.2.0.md
```

Release notes are derived from the exact matching changelog section, not from GitHub's automatic pull-request summary.

## 9. Safety and recovery

### A release tag already exists

The workflow skips publication. Tags are immutable release evidence and are never moved automatically.

### The GitHub workflow fails

No release is published. Correct the cause on `develop`, prepare a patch release, merge it, and let the automatic workflow run again.

### prepare fails after version files were changed

To abandon the preparation before commit:

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
