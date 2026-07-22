# ADR-010: Location completeness is explicit and repaired through review

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

A day may contain valid planned stops without GPS coordinates. Those stops must
remain visible in their confirmed order, yet maps and street routes can only
use coordinates. Previously a GPS-less stop could silently disappear from the
map, making marker gaps look like ordering errors and hiding incomplete data.

## Decision

Every canonical route node exposes one location state:

- `resolved` — validated coordinates are available;
- `unverified` — coordinates exist but provider confirmation is open;
- `ambiguous` — textual geocoding has multiple plausible candidates;
- `missing` — no usable coordinates exist.

The canonical day model publishes:

- all ordered route nodes,
- map-capable nodes,
- missing-coordinate nodes,
- all location-attention nodes,
- route and location completeness,
- a bounded data-quality summary.

A missing or ambiguous location never removes the stop from the stop cards,
map legend or schematic day flow. Only the physical map marker and calculated
route segment are omitted. The UI labels this as a partial route.

GPS completion is review-only:

1. Roadplanner creates update drafts with the existing stop ID, owning day ID
   and a bounded `place_query`.
2. The existing geocoding provider enriches those drafts.
3. Ambiguous results remain open questions.
4. The user reviews the resulting ChangeSet before any canonical write.
5. The server never invents coordinates.

Inherited overnight starts may be repaired through their owning previous day.

## Consequences

- Map gaps are understandable rather than silent.
- A concrete stop can be planned before exact GPS is known.
- Route calculation remains explicitly partial until all required coordinates
  exist.
- Provider failures do not discard valid stop planning.
- Location repair reuses the established change-basket and review pipeline.

## Rejected alternatives

- Hiding GPS-less stops entirely.
- Assigning guessed coordinates without review.
- Making every stop time mandatory to infer order.
- Storing geocoding candidates as confirmed locations.
