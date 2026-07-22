# EPIC-002 — Intelligent Travel Foundation

**Target release:** Roadplanner 3.2.0
**Status:** Implemented for release preparation

## Goal

Roadplanner should detect incomplete travel data, offer safe repairs, enrich planned stops visually in the background, and publish releases without the repetitive manual GitHub workflow-dispatch step.

## Product outcomes

- The overview exposes one trip-wide quality score.
- Sequence, GPS, route and image completeness remain separate dimensions.
- Missing schedule times are visible only as optional planning hints; they never reorder stops or lower data integrity.
- All incomplete GPS assignments can be prepared in one review-only batch.
- The existing change basket, geocoder and handoff review remain the only write path.
- Planning images are added in bounded background batches for the active trip.
- Existing personal OneDrive photos are always preferred and prevent unnecessary stock-image searches.
- A release pull-request merge into `main` automatically validates, tags, publishes and makes the version available to HACS.

## Safety constraints

- No coordinates are invented.
- No assistant or integrity action writes directly to the Roadbook.
- A failed image provider does not block the trip or another provider.
- Existing release tags are never moved.
- `develop` is fast-forwarded automatically only when it contains no unpublished commits.
- The human merge of the release pull request remains the publication approval.

## Acceptance criteria

1. The panel shows a quality score and dimension scores for sequence, GPS, routes and images.
2. A detail view lists affected days and stops without hiding GPS-less stops.
3. “GPS-Vervollständigung vorbereiten” creates review-only drafts for all incomplete stops in the active trip.
4. Times remain optional and never determine ordering.
5. Background image enrichment starts after integration initialization and repeats in bounded intervals.
6. Current and upcoming days are enriched before historical days.
7. Stops with personal OneDrive media are skipped by the stock-image worker.
8. The normal panel load no longer scans the whole trip in repeated image batches.
9. Merging a prepared release PR to `main` automatically starts publication.
10. The workflow validates the exact merge commit, creates `vX.Y.Z`, publishes assets and fast-forwards `develop` when safe.
11. Manual `workflow_dispatch` remains available as a fallback.
12. All repository, HACS, Python and JavaScript checks pass.
