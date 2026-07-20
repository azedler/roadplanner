# Roadplanner Roadmap

## Current baseline

- **Imported baseline:** Roadplanner 2.6.5
- **Next major:** Roadplanner 3.0
- **Technical domain:** `roadplanner_mcp`

## Roadplanner 3.0 — Product foundation

### Repository and delivery

- GitHub becomes the source of truth for code.
- HACS-compatible repository layout.
- Browser/Codespaces workflow for iPad-only development.
- Clear release, rollback and migration process.
- Public-repository and license decision before HACS rollout.

### Domain consistency

- One canonical stop-order function.
- Stop numbering follows planned/temporal day progression.
- Day timeline, map, routing and navigation use the same order.
- Overnight continuity without duplicate canonical stops.
- Legacy day `start`/`end` fields no longer create duplicate visual route elements.

### Planning metrics and overview

- Rough full-trip distance based on confirmed day anchors even before detailed stops exist.
- Distinguish estimated planning distance from calculated detailed driving distance.
- Replace technical dashboard counters with phase-aware information:
  - planning completion,
  - open decisions,
  - due tasks,
  - estimated/detailed distance,
  - document readiness.

### Assistant lifecycle

- Session conversation per user and trip.
- Controlled summarization of long conversations.
- Attachment/image context pruning.
- Persistent Roadbook remains the memory after a commit.
- Diagnosis stays separate from normal travel UX.

### Media Intelligence

#### Local analysis by default

- file and perceptual-hash deduplication,
- burst grouping,
- blur/sharpness score,
- exposure and resolution score,
- time/GPS consistency,
- best representative image per duplicate group.

#### Optional AI curation

- operate on the locally reduced set, per stop/day,
- select story-oriented highlights rather than near-identical images,
- propose title images and captions,
- user approval remains authoritative.

### Architecture

- Define provider APIs v1.
- Prepare incremental boundaries for Core, HA adapter and UI.
- Freeze and document Roadbook schema v1.
- Eliminate duplicate configuration sources.

## Roadplanner 3.1 — Travel Story

- Trip-wide highlight selection.
- Story chapters per day and stop.
- Travel Movie export contract.
- Photo-book/export metadata.

## Roadplanner 3.2 — Media providers

- Apple Photos bridge or native companion sync.
- Multi-account OneDrive support.
- Optional Google Photos, Immich and NAS providers.

## Roadplanner 3.3 — Automation and mobility

- EVCC-aware charging plans.
- weather-aware proactive suggestions,
- improved offline mode,
- background mobile notifications.

## Backlog policy

Until multiple external contributors are active, the canonical backlog is [BACKLOG.md](BACKLOG.md). GitHub Issues remain available for concrete bugs and externally reported problems, but are not required for every internal idea.
