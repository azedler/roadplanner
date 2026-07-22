# ADR-007: Planning and travel image precedence

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

Before a stop is visited, external images help travellers understand a destination. After the visit, Roadplanner may have personal OneDrive photos for the same stop. Mixing both sources without a clear rule creates unstable covers and weakens the distinction between expectation and memory.

## Decision

Roadplanner keeps two media classes for a stop or day:

- **planning images:** attributed external previews from approved providers such as Wikimedia Commons and Openverse;
- **travel images:** personal provider references assigned to the stop or day after capture.

The default presentation order is:

1. manually selected personal cover,
2. locally curated personal travel highlight,
3. manually selected planning cover,
4. highest-ranked attributed planning image,
5. neutral placeholder.

Personal travel photos replace planning images only in the default presentation. Planning images remain stored and available as the historical planning preview.

Original personal media remains with the media provider. Roadplanner stores references and curation metadata according to ADR-003.

Local deterministic curation may collapse exact duplicates and short bursts without user review. Optional semantic or emotional AI curation remains opt-in and never deletes originals.

## Consequences

- Planned destinations are visual before the journey.
- The same stop becomes personal after it is visited.
- Planning provenance and licensing remain available.
- OneDrive albums remain complete even when the main UI shows only curated highlights.
- Provider failures do not erase previously stored images.

## Rejected alternatives

- Permanently replacing or deleting planning images after a visit.
- Copying all provider-hosted originals into Home Assistant.
- Showing external stock images ahead of suitable personal travel photos.
- Sending every photo to a vision model by default.
