# ADR-011: Local-first Vision media curation

- **Status:** Accepted
- **Date:** 2026-07-23

## Context

Roadplanner can associate many external planning images and personal OneDrive photos with one stop. A semantic image model can choose more representative and varied covers than metadata alone, but sending complete albums externally would waste quota, increase latency and expose unnecessary personal image content.

## Decision

Every media selection begins with deterministic local processing:

- assignment confidence, time and GPS relevance;
- technical metadata and dimensions;
- exact duplicate collapse;
- short burst suppression;
- screenshot and low-value candidate penalties;
- stable local ranking.

Optional hybrid mode may then send only a bounded set of reduced thumbnails and opaque Roadplanner image IDs to the configured multimodal assistant provider. The provider may select and explain a cover and highlight order only from those IDs.

The default mode for existing installations is local-only. Hybrid mode requires explicit configuration, applies per-trip daily limits, caches selections by candidate fingerprint and falls back to the local result after any error, timeout, quota limit or invalid output.

Manual cover selections always override automated curation. Curation never deletes, modifies or identifies people in provider-hosted originals.

## Consequences

- Roadplanner remains useful without an AI provider or internet connectivity.
- External image analysis receives less data and consumes fewer requests.
- Unchanged albums are not repeatedly analysed.
- The same contract applies to planning images and personal travel photos.
- Users can inspect the complete album and replace any automated selection.

## Rejected alternatives

- Sending every image from a stop directly to an external model.
- Making Vision analysis mandatory for all users.
- Allowing the model to invent image IDs or delete provider files.
- Ignoring manual cover choices after automatic re-evaluation.
