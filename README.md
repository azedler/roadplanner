# Roadplanner

**AI-powered travel planning and travel journal for Home Assistant.**

Roadplanner supports the full travel lifecycle:

- **Plan** routes, days, stops, alternatives, bookings, and preferences.
- **Prepare** documents, tickets, tasks, expenses, and decisions.
- **Travel** with daily routes, navigation handoff, a conversational assistant, and photo assignment.
- **Remember** with day and stop albums, media highlights, and future travel-story exports.

## Project status

The repository contains the proven **Roadplanner 2.6.5** baseline and the architecture foundation for **Roadplanner 3.0**.

- Stable/releasable branch: `main`
- Active integration branch: `develop`
- Technical Home Assistant domain: `roadplanner_mcp`
- Visible product name: **Roadplanner**

Roadplanner 3.0 is an incremental product and architecture evolution, not a big-bang rewrite.

## Core principles

- Canonical Roadbook and domain sidecars have explicit ownership boundaries.
- AI systems and importers propose; the user reviews and applies.
- Existing IDs are never invented.
- Revisions and ChangeSet metadata are server-controlled.
- One canonical stop order drives map, timeline, routing, navigation, and assistant context.
- No destructive or schema-breaking change without migration and rollback.
- Mobile-first behavior is a release requirement.
- Secrets and personal travel data never belong in Git.

Read the mandatory rules in [AI_DEVELOPMENT_CONTRACT.md](AI_DEVELOPMENT_CONTRACT.md) and the accepted decisions in [docs/architecture/adr/](docs/architecture/adr/README.md).

## Repository layout

```text
.github/                              GitHub templates and ownership
custom_components/roadplanner_mcp/   HACS-compatible Home Assistant integration
docs/                                Architecture and development contracts
tests/                               Regression and contract tests
tools/                               Validation and release utilities
assets/                              Public repository documentation assets
```

See [Repository structure](docs/development/REPOSITORY_STRUCTURE.md).

## Installation

### HACS custom repository

HACS requires a public GitHub repository. After the public-source review and first stable release:

1. Add `https://github.com/azedler/roadplanner` to HACS as a custom repository of type **Integration**.
2. Download Roadplanner.
3. Restart Home Assistant.
4. Add or reload Roadplanner under **Settings → Devices & services**.

See [HACS setup](docs/development/HACS_SETUP.md).

### Manual installation

Copy:

```text
custom_components/roadplanner_mcp
```

into:

```text
/config/custom_components/roadplanner_mcp
```

and restart Home Assistant.

## Development

Roadplanner uses a specification-first, patch-based workflow designed for iPad and GitHub Codespaces.

Start here:

- [Documentation index](docs/README.md)
- [Architecture](ARCHITECTURE.md)
- [Roadmap](ROADMAP.md)
- [Backlog](BACKLOG.md)
- [Development workflow](docs/development/DEVELOPMENT_WORKFLOW.md)
- [AI patch workflow](docs/development/PATCH_WORKFLOW.md)
- [Definition of Done](docs/development/DEFINITION_OF_DONE.md)
- [Contributing](CONTRIBUTING.md)

Validate the repository with:

```bash
python tools/validate_repository.py
```

## Privacy

The repository must never contain:

- real trips or Roadbook JSON files,
- uploaded documents or receipts,
- photos or OneDrive metadata,
- Gemini API keys,
- Microsoft OAuth tokens,
- Home Assistant `.storage` data,
- handoff or archive directories.

See [SECURITY.md](SECURITY.md).

## License

Roadplanner is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) and the [license policy](docs/development/LICENSE_POLICY.md).
