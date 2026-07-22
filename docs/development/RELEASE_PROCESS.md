# Release process

Roadplanner uses Semantic Versioning and an explicit two-stage release automation. See [Release automation](RELEASE_AUTOMATION.md) for the complete Codespaces workflow.

## Versioning

- major: user-visible or architectural breaking change with migration,
- minor: backward-compatible feature release,
- patch: backward-compatible defect correction.

The version must match in:

```text
custom_components/roadplanner_mcp/manifest.json
custom_components/roadplanner_mcp/const.py
CHANGELOG.md
```

The automation enforces a lower-case release tag:

```text
vX.Y.Z
```

Existing tags are never moved automatically.

## Branch contract

- `develop` is the active integration branch.
- `main` is always releasable and is the source for HACS and GitHub releases.
- A release is created only from the exact current `main` commit.
- Pull requests are still reviewed and merged explicitly by the maintainer.

## Normal release

### On develop

1. Complete the task and Definition of Done.
2. Add user-visible changes to `CHANGELOG.md` under `[Unreleased]`.
3. Run:

```bash
python tools/release.py prepare X.Y.Z --remote
```

4. Review and merge the generated pull request after all checks pass.

### On main

```bash
git switch main
git pull --ff-only origin main
python tools/release.py publish X.Y.Z --watch --sync-develop
```

The protected GitHub workflow performs final validation, builds optional manual-install artifacts, creates the exact tag, and publishes the release used by HACS.

## Local validation only

```bash
python tools/release.py check
```

For a clean release workspace including manual artifacts:

```bash
python tools/release.py check --version X.Y.Z --release --build
```

## Manual build fallback

```bash
python tools/validate_repository.py --release
python tools/build_release.py
```

The manual archive and checksum are created in `dist/`. GitHub source archives remain the normal HACS distribution source.

## GitHub-only publication fallback

After the release preparation pull request has been merged:

```text
Actions → Publish Roadplanner release → Run workflow
```

Enter version `X.Y.Z` without a tag prefix. The workflow creates `vX.Y.Z` on the exact validated `main` commit.

## HACS

HACS identifies published versions from GitHub releases. A pushed tag without a published GitHub release is not considered a complete Roadplanner publication.

## CI policy

Roadplanner now has one canonical validation entry point:

```bash
python tools/release.py check
```

Local Codespaces checks and GitHub Actions call the same command. This avoids separate test definitions drifting apart while still protecting pull requests and releases.
