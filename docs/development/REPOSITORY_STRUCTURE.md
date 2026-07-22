# Repository structure

Roadplanner keeps a HACS-compatible layout while preparing gradual architectural separation.

```text
.github/                              GitHub templates and ownership
custom_components/roadplanner_mcp/   Installable Home Assistant integration
  frontend/                           Native Roadplanner panel
  translations/                       User-facing translations
  brand/                              Integration branding assets
docs/
  architecture/                       Stable architecture contracts and ADRs
  development/                        Development and release procedures
  legacy/                             Imported historical release records
  product/                            Product vision and UX blueprints
tests/                                Repository and domain regression tests
tools/                                Validation and release utilities
assets/                               Public repository documentation assets
```

## Root files

Root-level files are entry points and project-wide contracts:

- `README.md`: product and installation overview
- `ARCHITECTURE.md`: current architecture overview
- `AI_DEVELOPMENT_CONTRACT.md`: mandatory rules for AI and human contributors
- `ROADMAP.md`: product direction and release themes
- `BACKLOG.md`: prioritized work items
- `CHANGELOG.md`: released and unreleased user-visible changes
- `CONTRIBUTING.md`: contribution entry point
- `SECURITY.md`: security and privacy reporting
- `LICENSE` and `NOTICE`: distribution terms and attribution
- `hacs.json`: HACS repository metadata

## HACS constraint

The installable integration remains directly under:

```text
custom_components/roadplanner_mcp/
```

Do not move it under `src/` unless a later ADR and release packaging change deliberately replace the HACS layout.

## Runtime boundaries

The repository must never include runtime or user data such as:

```text
/config/.storage/
/config/www/roadbook/
/config/.roadplanner_archive/
/config/.roadplanner_handoffs/
API keys or OAuth tokens
personal documents, receipts, photos, or provider metadata
```

## Future architecture

Roadplanner 3.x may extract domain, provider, and UI boundaries incrementally, but no big-bang directory rewrite is permitted. Every move must preserve installation compatibility, migrations, and rollback.
