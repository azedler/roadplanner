# ADR-001: Domain authority boundaries

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Roadplanner contains canonical travel planning data and several related domains such as documents, expenses, tasks, decisions and media. Earlier iterations occasionally treated caches, assistant output or UI state as if they were authoritative, which creates contradictions and duplicate data sources.

## Decision

Roadplanner uses one authoritative store per domain:

- **Canonical Roadbook:** trip, day and stop identity, ownership, order and planning state.
- **Document store:** uploaded originals and confirmed extracted booking metadata.
- **Expense store:** confirmed monetary transactions.
- **Todo store:** confirmed tasks and completion state.
- **Decision store:** decision drafts, options and selected outcomes until they become a reviewed Roadbook proposal.
- **Media index:** provider references, metadata, assignments and curation state.
- **Handoff store:** review artifacts and import/export lifecycle.

Assistant conversations, provider caches, thumbnails, routing responses and UI state are never authoritative.

Cross-domain objects reference stable canonical IDs. They do not silently create or mutate trip, day or stop entities.

## Consequences

- Every field has a clearly identified owner.
- Sync and provider failures cannot redefine the trip.
- Deleting a cache does not delete confirmed business data.
- Domain migrations can be versioned independently while preserving references.
- Services must avoid copying authoritative fields into unrelated stores unless the copy is explicitly marked as a snapshot.

## Rejected alternatives

- One monolithic JSON document for all domains.
- Assistant conversation history as long-term memory.
- Provider-specific records as canonical Roadbook entities.
