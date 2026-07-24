# Roadplanner

**AI-powered travel planning and travel journal for Home Assistant.**

Roadplanner supports the full travel lifecycle:

- **Plan** routes, days, stops, alternatives, bookings, and preferences.
- **Prepare** documents, tickets, tasks, expenses, and decisions.
- **Travel** with daily routes, navigation handoff, a conversational assistant, and photo assignment.
- **Remember** with day and stop albums, media highlights, and future travel-story exports.

## Project status

The repository contains the current **Roadplanner 3.x** source. The published stable version is shown in [GitHub Releases](https://github.com/azedler/roadplanner/releases/latest) and in the integration manifest.

- Stable/releasable branch: `main`
- Active integration branch: `develop`
- Technical Home Assistant domain: `roadplanner_mcp`
- Visible product name: **Roadplanner**

Roadplanner 3.0 is an incremental product and architecture evolution, not a big-bang rewrite.

## Roadplanner 3.0 highlights

- One canonical day model drives maps, graphical day flow, stop cards, routing, navigation, decisions and assistant context.
- Legacy start/end labels remain context only and no longer create phantom stops.
- Planned stops use attributed Wikimedia Commons/Openverse images until suitable personal OneDrive photos are available.
- Local media curation reduces exact duplicates and short bursts, then proposes stop and day highlights without token cost.
- The main navigation follows the travel lifecycle: Reise, Heute, Erinnerungen and Reisebegleiter.
- The dashboard emphasizes planning progress, next-day readiness and unresolved travel work instead of technical counters.

See the [Roadplanner 3.0 Vision & UX Blueprint](docs/product/ROADPLANNER_3_0_VISION_UX_BLUEPRINT.md).

## Roadplanner 3.2 intelligent foundation

- Trip-wide quality report for sequence, GPS completeness, routes and visual readiness.
- Review-only bulk GPS completion for incomplete stops.
- Bounded automatic planning-image enrichment for the active trip.
- Personal OneDrive photos take precedence over external planning images.
- Merging a prepared release pull request automatically validates, tags and publishes the HACS release.

## Roadplanner 3.4 complete places and Vision smart media

- “Orte vervollständigen” resolves incomplete stops to reviewable place profiles instead of proposing GPS-only updates.
- Candidate previews include map position, address, category, available contact/opening information, confidence and representative planning images.
- Confirmed candidates become concrete review-only ChangeSets; selected values are not sent through Gemini a second time.
- Before a visit, attributed Wikimedia Commons/Openverse planning images are shown. After a visit, a deterministic best-of selection from personal OneDrive photos takes precedence.
- Planning and travel images remain separate, so original previews are retained while personal memories become the normal presentation.
- Image curation is local-first: deterministic filtering removes duplicates, bursts and weak candidates before an optional Gemini Vision step selects the most representative cover and highlights.
- Existing installations default to local-only curation; hybrid Vision must be enabled explicitly and is protected by candidate and daily-call limits.

## Smart destination enrichment

- “Stopps anreichern” classifies addresses, POIs, ferry terminals, hikes, nature centres, shops, restaurants and overnight places before provider search.
- Geodata and provider identity are reviewed first; the confirmed name, city, country, category and coordinates then drive concise planning-image queries.
- Surrounding city or district matches remain visible for review but are not accepted automatically when a specific POI is expected.
- Notes and day titles are excluded from image-provider queries, while source links such as Park4Night, OpenStreetMap, Wikidata and Wikipedia remain traceable hints.

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
- [Release automation](docs/development/RELEASE_AUTOMATION.md)
- [Contributing](CONTRIBUTING.md)

Run the canonical local and CI checks with:

```bash
python tools/release.py check
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
