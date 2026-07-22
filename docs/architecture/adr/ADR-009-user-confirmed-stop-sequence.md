# ADR-009: User-confirmed stop sequence is authoritative

- **Status:** Accepted
- **Date:** 2026-07-22
- **Supersedes:** ADR-004

## Context

Legacy Roadbook days may contain incomplete `position` values while some stops
have arrival or departure times and others do not. Sorting such a day by time
can move a late ferry ahead of untimed parking, pharmacy, shopping or service
stops even though the user confirmed a different travel order. This made stop
cards, maps, route flows and navigation disagree.

## Decision

The travel plan order is independent from its schedule.

1. A complete set of unique positive `position` values is authoritative.
2. If legacy positions are incomplete, the stored Roadbook stop-list order is
   authoritative.
3. `arrival_time` and `departure_time` are descriptive schedule data only and
   never participate in canonical sorting.
4. Every add, update, move and remove operation reindexes the affected day to a
   complete one-based `position` sequence.
5. The assistant plans a day as an ordered sequence and emits an explicit
   operation-level `position` for every newly added stop.
6. The inherited overnight start remains a referenced route node with marker
   `S`; it does not renumber Roadbook-owned stops.

All derived consumers use the same canonical order:

- stop cards and numbering,
- map legend and markers,
- schematic day flow,
- routing and Google Maps handoff,
- assistant context and decision templates,
- imports, exports and ChangeSets.

## Consequences

- Missing times no longer destabilize a valid travel plan.
- Times can be estimated or edited without changing stop order.
- Legacy days acquire complete positions lazily when normalized or next
  changed; no destructive standalone migration is required.
- The assistant can insert, move or remove a stop while the server keeps the
  complete sequence gap-free.

## Rejected alternatives

- Sorting by arrival or departure time.
- Making times mandatory for all stops.
- Keeping a second persisted `sequence` field beside `position`.
- Allowing each UI component to sort independently.
