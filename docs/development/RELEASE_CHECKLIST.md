# Release checklist

## Source readiness

- [ ] `main` contains only reviewed, releasable changes
- [ ] version is identical in `manifest.json` and `const.py`
- [ ] `CHANGELOG.md` contains the release entry
- [ ] migration and rollback notes are complete
- [ ] no temporary feature flag or debug output remains unintentionally enabled

## Validation

- [ ] `python tools/validate_repository.py --release`
- [ ] task-specific and regression tests
- [ ] Home Assistant restart/setup/reload test
- [ ] relevant iPhone/iPad checks
- [ ] external provider limitations recorded

## Privacy and licensing

- [ ] repository and archive contain no personal Roadbook, documents, photos, tokens, or logs
- [ ] third-party licenses and NOTICE obligations reviewed
- [ ] release includes `LICENSE` and `NOTICE`

## Packaging and publication

- [ ] `python tools/build_release.py`
- [ ] generated archive inspected
- [ ] checksum generated and verified
- [ ] Git tag `vX.Y.Z` created from `main`
- [ ] GitHub release notes published
- [ ] HACS install/update path verified

## Post-release

- [ ] installed version reports the expected number
- [ ] rollback package or prior tag remains available
- [ ] `develop` is synchronized with the released `main`
