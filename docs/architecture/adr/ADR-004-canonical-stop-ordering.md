# ADR-004: Canonical stop ordering

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Stop numbering, map markers, day timeline and route calculation can diverge when they use insertion order or independent sorting rules. This was visible when cards and map markers did not match the temporal day progression.

## Decision

Every day has one canonical ordered-stop service.

The service determines order using:

1. explicit canonical stop position,
2. confirmed arrival/departure chronology when legacy position is missing,
3. deterministic legacy fallback only for incomplete data.

The explicit schema field remains `position` in Roadbook v1 for compatibility. UI labels may call it order or sequence, but they must not create a second persisted ordering field.

The inherited overnight start is a referenced effective route element. It is included once in derived day order and is not duplicated as a canonical stop.

The following consumers must use the shared ordered-stop service:

- stop numbering,
- day cards and schematic timeline,
- map marker numbering,
- routing and distance calculation,
- Google Maps export,
- assistant context,
- day summaries and exports.

## Consequences

- Insertion order no longer controls user-visible planning order.
- Reordering one stop updates every derived view consistently.
- Legacy trips require deterministic migration or fallback behavior.
- Independent sorting logic in UI and provider modules must be removed incrementally.

## Rejected alternatives

- A new `sequence` field alongside `position`.
- Sorting separately in each UI view.
- Using stop IDs or file order as route order.
