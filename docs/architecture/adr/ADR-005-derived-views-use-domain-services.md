# ADR-005: Derived views use shared domain services

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Maps, dashboards, timelines, assistant context and exports are derived representations. When each representation reimplements business logic, they drift and expose contradictory results.

## Decision

Derived views consume shared domain services and do not reconstruct domain rules independently.

Examples:

- day timeline consumes canonical ordered stops,
- trip distance consumes planning anchors or calculated route metrics,
- dashboard completion consumes a documented planning-completeness service,
- task badges consume due-state classification,
- media galleries consume deduplicated and curated media results.

Derived data must include provenance and state where relevant, for example:

- `estimated` versus `calculated` distance,
- provider and calculation timestamp,
- complete versus partial route coverage,
- automatic versus manually confirmed media assignment.

A derived result may be cached, but the cache is invalidated when its authoritative inputs change.

## Consequences

- UI components become simpler and more consistent.
- Backend services become testable contracts.
- Cached results can be safely rebuilt.
- New clients can reuse the same domain behavior.

## Rejected alternatives

- Business logic embedded in individual cards or panels.
- Persisting every UI representation as a separate source of truth.
- Showing derived values without provenance or completeness state.
