# Release checklist

The canonical automated path is documented in [Release automation](RELEASE_AUTOMATION.md).

## Source readiness

- [ ] `develop` contains the reviewed feature and fix commits
- [ ] user-visible changes are documented under `CHANGELOG.md` → `[Unreleased]`
- [ ] migration and rollback notes are complete
- [ ] no temporary feature flag or debug output remains unintentionally enabled
- [ ] `python tools/release.py check` passes

## Prepare on develop

- [ ] run `python tools/release.py prepare X.Y.Z --remote`
- [ ] version is identical in `manifest.json` and `const.py`
- [ ] `CHANGELOG.md` contains `## [X.Y.Z] - YYYY-MM-DD`
- [ ] release-preparation commit is pushed to `develop`
- [ ] release pull request targets `main`

## Pull-request validation

- [ ] Roadplanner validation workflow is green
- [ ] CodeQL and configured security checks are green
- [ ] task-specific and regression tests are green
- [ ] relevant Home Assistant restart/setup/reload path was tested
- [ ] relevant iPhone/iPad workflow was tested when UI changed
- [ ] external provider limitations are recorded
- [ ] pull request is explicitly reviewed and merged

## Privacy and licensing

- [ ] repository and archive contain no personal Roadbook, documents, photos, tokens, or logs
- [ ] third-party licenses and NOTICE obligations were reviewed
- [ ] release includes `LICENSE` and `NOTICE`

## Automated publication

- [ ] merge the prepared release pull request into `main`
- [ ] automatic release workflow starts for the merge commit
- [ ] optionally run `python tools/release.py publish X.Y.Z --watch --sync-develop` to monitor
- [ ] protected workflow validates the exact `main` commit
- [ ] deterministic manual archive and checksum are created
- [ ] lower-case tag `vX.Y.Z` points to the exact release commit
- [ ] GitHub release exists and is marked correctly as latest or prerelease
- [ ] HACS discovers the published GitHub release

## Post-release

- [ ] Home Assistant reports the expected installed version
- [ ] core Roadplanner workflow opens after restart
- [ ] rollback to the prior GitHub release remains available
- [ ] `develop` contains the released `main` history
