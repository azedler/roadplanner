# Roadplanner

**AI-powered travel planning and travel journal for Home Assistant.**

Roadplanner supports the full travel lifecycle:

- **Plan** routes, days, stops, alternatives, bookings, and preferences.
- **Prepare** documents, tickets, tasks, expenses, and family decisions.
- **Travel** with daily routes, navigation handoff, a conversational assistant, and photo assignment.
- **Remember** with day and stop albums, media highlights, and future travel-story exports.

## Project status

This repository starts from the proven **Roadplanner 2.6.5** Home Assistant integration and is the development foundation for **Roadplanner 3.0**.

The technical Home Assistant domain remains:

```text
roadplanner_mcp
```

The visible product name is simply **Roadplanner**.

## Core principles

- Home Assistant and the Roadbook are the single source of truth.
- AI systems propose; the user reviews and applies.
- Existing IDs are never invented.
- No destructive or schema-breaking change without migration and rollback.
- Mobile-first UX is a release requirement.
- Secrets and personal travel data never belong in Git.

Read the full rules in [AI_DEVELOPMENT_CONTRACT.md](AI_DEVELOPMENT_CONTRACT.md).

## Repository layout

```text
custom_components/roadplanner_mcp/   Home Assistant integration and panel
docs/                                Architecture, development and roadmap
tests/                               Regression and contract tests
tools/                               Local validation and release helpers
assets/                              Repository documentation assets
```

The integration stays at the repository root under `custom_components/` because that is the repository layout expected by HACS.

## Installation

### HACS custom repository

HACS only supports **public GitHub repositories**. After this repository has been reviewed for secrets and a license has been selected:

1. Make the repository public.
2. Add `https://github.com/azedler/roadplanner` to HACS as a custom repository of type **Integration**.
3. Download Roadplanner in HACS.
4. Restart Home Assistant.
5. Add or reload the Roadplanner integration under **Settings → Devices & services**.

### Manual installation

Copy:

```text
custom_components/roadplanner_mcp
```

to:

```text
/config/custom_components/roadplanner_mcp
```

and restart Home Assistant.

## Development

Development decisions and feature specifications are documented before implementation.

Start here:

- [Architecture](ARCHITECTURE.md)
- [Roadmap](ROADMAP.md)
- [AI development contract](AI_DEVELOPMENT_CONTRACT.md)
- [Contributing](CONTRIBUTING.md)
- [iPad and Codespaces workflow](docs/development/IPAD_CODESPACES_WORKFLOW.md)
- [Release process](docs/development/RELEASE_PROCESS.md)

Run the repository checks with:

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

A public distribution license has not yet been selected. Until that decision is made, all rights are reserved. Keep the repository private until the license decision and public-source review are complete.
