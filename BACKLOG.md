# Backlog

## Completed foundation

- GitHub/HACS-compatible repository baseline.
- `main` and `develop` branch model.
- Apache-2.0 licensing.
- AI-to-Git patch workflow.
- Architecture Decision Records.
- Repository governance, Definition of Done, test strategy, and release process.

## Next — correctness

### RP-303: Canonical stop ordering

- One backend ordering service using canonical `position`.
- Deterministic legacy fallback.
- Numbering, map, timeline, routing, navigation, and assistant use the same order.
- Regression coverage for inherited overnight start plus explicit stops.

### RP-304: Derived day route consistency

- Build the schematic day flow from effective ordered route elements only.
- Remove legacy start/end duplication from derived views.
- Share route elements between panel, routing, and exports.

## Next — planning UX

### RP-305: Phase-aware overview and planning distance

- Estimated full-trip distance before detailed GPS stops exist.
- Separate estimated and calculated distance.
- Planning completeness, open decisions, due tasks, and document readiness.

### RP-306: Assistant conversation compaction

- Summarize old text context.
- Prune obsolete attachments and image context.
- Preserve current Roadbook facts and recent conversation.

## Next — expenses

### RP-307: EUR reference conversion

- Preserve original amount and currency.
- Store EUR reference amount with rate source, date, and policy.
- Show original and EUR totals without silent currency mixing.

## Next — Media Intelligence

### RP-308: Local media deduplication and quality selection

- File/perceptual-hash duplicate groups.
- Burst grouping.
- Technical quality scoring.
- Representative image and manual override.

### RP-309: Optional AI highlight curation

- Analyze only locally reduced candidates.
- Top images per stop/day.
- Story-oriented title-image and caption proposals.

## Later — architecture and delivery

- Public-source and third-party license audit.
- First stable GitHub release and HACS installation test.
- Provider interfaces v1.
- Roadbook schema v1 and migration harness.
- Incremental separation of domain, Home Assistant adapter, and UI.
