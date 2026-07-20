# First HACS release checklist

## Source and privacy

- [ ] `develop` is clean and validated.
- [ ] The release candidate is merged into `main`.
- [ ] No real Roadbook, documents, receipts, photos, media metadata, OAuth tokens, API keys, or `.storage` files are present.
- [ ] Apache-2.0 `LICENSE` and `NOTICE` are present.
- [ ] Third-party assets and dependencies have known compatible licensing.

## HACS metadata

- [ ] Exactly one directory exists under `custom_components/`.
- [ ] `manifest.json` includes domain, name, version, documentation, issue tracker, and codeowners.
- [ ] `hacs.json` is valid and hides the default branch.
- [ ] `brand/icon.png` is present and square.
- [ ] README explains HACS installation and configuration.
- [ ] Repository description, Issues, and topics are configured on GitHub.

## Validation

- [ ] `python tools/validate_repository.py`
- [ ] `python tools/hacs_preflight.py --tag v2.6.5`
- [ ] Manual **HACS preflight** workflow passes on `main`.
- [ ] Home Assistant manual-install baseline still starts.
- [ ] Roadplanner panel loads after restart.

## Publication

- [ ] Repository visibility changed to **Public**.
- [ ] Full GitHub Release `v2.6.5` created from `main`.
- [ ] Release is marked stable, not prerelease.
- [ ] Repository added to HACS as a custom **Integration**.
- [ ] HACS downloads `v2.6.5` successfully.
- [ ] Home Assistant restarts successfully.
- [ ] Existing Roadbook, config entry, documents, expenses, tasks, and media references remain available.

## After publication

- [ ] HACS update entity is visible.
- [ ] Rollback to the previous manual backup or Home Assistant backup was documented.
- [ ] The repository remains public while it is used by HACS.
