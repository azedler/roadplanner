# Roadplanner Roadmap

## Current baseline

- Latest public feature release: **Roadplanner 3.0.0**
- Active major development: **Roadplanner 3.x**
- Technical domain: `roadplanner_mcp`

## Roadplanner 3.0 — Product foundation

### Foundation completed

- [x] GitHub is the source of truth for code.
- [x] HACS-compatible repository layout.
- [x] `main` and `develop` branch model.
- [x] Apache-2.0 licensing and NOTICE.
- [x] AI Development Contract.
- [x] Architecture Decision Records.
- [x] Patch-based iPad/Codespaces workflow.
- [x] Repository governance, validation, release, and Definition-of-Done contracts.

### Domain consistency

- [x] Canonical stop ordering shared by map, timeline, routing, navigation, decisions, archives, imports, and assistant context.
- [x] Deterministic legacy fallback with explicit positions remaining authoritative.
- [x] Overnight continuity without duplicate canonical stops in derived views.
- [x] Day timeline built exclusively from effective ordered route elements.
- [x] Legacy day `start`/`end` fields no longer create conflicting route elements.

### Planning metrics and overview

- Rough full-trip distance from confirmed planning anchors before detailed stops exist.
- Separate estimated planning distance from calculated driving distance.
- [x] Phase-aware overview for planning, preparation, travel, and memory.
- Planning completeness, open decisions, due tasks, document readiness, and route coverage.

### Expenses and EUR reference values

- Preserve original amount and currency.
- Add optional EUR reference amount.
- Support daily, trip-start, and manual fixed-rate policies.
- Store rate source, effective date, conversion mode, and timestamp.
- Aggregate EUR totals without hiding per-currency totals.

### Assistant lifecycle

- Session conversation per user and trip.
- Controlled summarization of long conversations.
- Attachment and image-context pruning.
- Persistent Roadbook remains memory after commit.
- Diagnostics remain separate from normal travel UX.

### Media Intelligence

- [x] Automatic stop galleries with Wikimedia Commons and Openverse.
- [x] Up to three planning images, source/license metadata, main image, reordering, and swipe gallery.
- [x] Existing OneDrive travel photos preferred in decisions.

#### Local analysis by default

- [x] exact/file-hash and metadata duplicate collapse,
- [x] local burst grouping and representative selection,
- blur, exposure, and resolution scoring,
- [x] time/GPS assignment confidence in local ranking,
- [x] best representative per exact duplicate group.

#### Optional AI curation

- operate only on the locally reduced set,
- select story-oriented highlights per stop/day,
- propose title images and captions,
- user approval remains authoritative.

### Architecture

- Provider APIs v1.
- Roadbook schema v1 freeze and migration contract.
- Incremental Core, Home Assistant adapter, and UI boundaries.
- Eliminate duplicate configuration and derived-logic sources.

## Roadplanner 3.1 — Travel Story

- Trip-wide highlight selection.
- Story chapters per day and stop.
- Travel Movie export contract.
- Photo-book/export metadata.

## Roadplanner 3.2 — Media providers

- Apple Photos bridge or native companion sync.
- Multi-account OneDrive support.
- Optional Google Photos, Immich, and NAS providers.

## Roadplanner 3.3 — Automation and mobility

- EVCC-aware charging plans.
- Weather-aware proactive suggestions.
- Improved offline mode.
- Background mobile notifications.

## Planning policy

The canonical internal priority list is [BACKLOG.md](BACKLOG.md). GitHub Issues are used for concrete reproducible bugs and external feedback, not for every product idea.
