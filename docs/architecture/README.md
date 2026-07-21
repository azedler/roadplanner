# Architecture documentation

Roadplanner architecture is governed at three levels:

1. [`ARCHITECTURE.md`](../../ARCHITECTURE.md) — product-level overview and target boundaries.
2. Stable contracts in this directory — schemas and provider interfaces.
3. [Architecture Decision Records](adr/README.md) — accepted decisions and their rationale.

## Current contracts

- [Roadbook v1](ROADBOOK_V1.md)
- [Plugin API v1](PLUGIN_API_V1.md)

## Decision policy

A runtime change that conflicts with an accepted ADR must introduce a new ADR that explicitly supersedes the old decision. Implementation details and transient tasks do not belong in ADRs.
