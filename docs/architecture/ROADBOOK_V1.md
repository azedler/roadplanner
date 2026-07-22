# Roadbook schema v1 — contract draft

## Canonical objects

- Trip
- Day
- Stop

## Referenced sidecar objects

- Document
- Expense
- Todo
- Decision
- Media
- Handoff

## Invariants

1. IDs are stable within a trip.
2. Days are explicitly ordered.
3. Stops have one canonical day ownership and one canonical positive `position`.
4. A day with stops uses a complete gap-free one-based position sequence; schedule times never define order.
5. Overnight continuity uses references rather than duplicate physical stops.
6. Canonical writes are atomic and revisioned.
7. Sidecars reference canonical IDs and cannot silently create canonical objects.
8. A concrete stop may temporarily lack GPS, but its location completeness remains explicit and reviewable.
9. Unknown provider fields remain provider metadata and do not enter canonical objects without normalization.

This document will be completed and version-frozen during Roadplanner 3.0.
