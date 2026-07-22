# EPIC-001: Canonical Day Experience

- **Target release:** Roadplanner 3.1.0
- **Base release:** Roadplanner 3.0.0 plus RP-500 release automation
- **Status:** Implemented on `develop`; live acceptance pending

## User problem

A day can contain correctly planned stops but show a different order in the
map, schematic flow or navigation. A timed ferry may jump ahead of untimed
parking or pharmacy stops. Stops without GPS may disappear from the map without
explaining why the route is incomplete.

## Outcomes

- Stop order is the user-confirmed plan, not an inference from schedule times.
- Every mutation leaves complete one-based positions behind.
- The assistant inserts and moves stops against the complete day sequence.
- Maps, stop cards, route flow, routing and navigation consume the same order.
- GPS-less stops remain visible in map legends and route-quality notices.
- Location repair uses the normal change basket, geocoding enrichment and
  explicit user review.

## In scope

- canonical stop ordering and lazy legacy normalization;
- reindexing in direct store and ChangeSet mutation paths;
- assistant operation position management;
- explicit location states and day-quality metadata;
- partial-route and missing-GPS UI;
- review-only GPS completion for a day, including inherited overnight starts;
- regression tests and architecture decisions.

## Out of scope

- automatic route optimization;
- mandatory arrival/departure times;
- background geocoding without user review;
- automatic choice among ambiguous geocoding candidates;
- weather-aware schedule generation;
- changes to the Roadbook schema version.

## Acceptance criteria

1. A ferry at 19:30 remains after untimed stops when its canonical position is later.
2. Complete explicit positions are authoritative.
3. Legacy incomplete positions preserve stored list order and are reindexed lazily.
4. Add, update, move and remove leave positions `1..N` without gaps.
5. New assistant stop operations receive an explicit position.
6. Ordinary new stops default before an existing overnight stop; new overnight stops default to the end.
7. Map, day flow and stop cards show identical labels and order.
8. GPS-less stops remain in the map legend with `GPS fehlt`.
9. The day view shows total stops, GPS-capable stops and partial-route warnings.
10. `GPS prüfen/ergänzen` creates review-only drafts and never invents coordinates.
11. An inherited GPS-less overnight start is repaired through its owning previous day.
12. Existing trips load without a destructive standalone migration.

## Live acceptance

Use one day containing:

- inherited overnight start;
- untimed parking stop;
- untimed pharmacy/service stop;
- timed ferry stop;
- at least one GPS-less stop.

Confirm that stop cards, flow and map legend retain this exact order, map marker
numbers contain intentional gaps only where GPS is missing, and GPS completion
produces reviewable updates in the change basket.
