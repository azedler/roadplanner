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
3. Stops have one canonical day ownership and one canonical position.
4. Overnight continuity uses references rather than duplicate physical stops.
5. Canonical writes are atomic and revisioned.
6. Sidecars reference canonical IDs and cannot silently create canonical objects.
7. Unknown provider fields remain provider metadata and do not enter canonical objects without normalization.

This document will be completed and version-frozen during Roadplanner 3.0.
