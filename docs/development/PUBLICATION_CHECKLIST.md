# Public repository and HACS checklist

Use this checklist before changing the repository from private to public.

## Source audit

- [ ] no API keys, OAuth tokens, passwords, or private endpoints
- [ ] no real Roadbook JSON, booking data, receipts, documents, photos, or OneDrive identifiers
- [ ] no Home Assistant `.storage` content
- [ ] no private handoff/archive directories
- [ ] screenshots and examples contain no personal data
- [ ] legacy documentation has been reviewed for private values

## Legal and project metadata

- [ ] Apache-2.0 `LICENSE` is present
- [ ] `NOTICE` is present and current
- [ ] third-party assets and dependencies have compatible licenses
- [ ] README, security policy, issue tracker, and code owners are current

## HACS readiness

- [ ] exactly one integration exists under `custom_components/roadplanner_mcp/`
- [ ] `manifest.json` contains version, documentation, issue tracker, and code owners
- [ ] `hacs.json` is valid
- [ ] `main` is installable
- [ ] automatic publication after the `main` merge completed successfully
- [ ] a stable GitHub release and lower-case `vX.Y.Z` tag exist on the exact validated `main` commit
- [ ] installation and update were tested through HACS

## Repository settings

- [ ] default branch is `main`
- [ ] branch protection or review policy is configured as desired
- [ ] security advisories are available
- [ ] GitHub Issues are used only for concrete bugs/external feedback unless policy changes
