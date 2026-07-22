# ADR-008: Phase-oriented primary navigation

- **Status:** Accepted
- **Date:** 2026-07-22

## Context

The original Roadplanner panel exposed every technical module as an equal top-level tab. As documents, imports, media, decisions, handoffs and assistant tooling grew, the mobile navigation became crowded and reflected implementation details rather than traveller intent.

## Decision

Roadplanner's primary navigation follows the travel lifecycle:

- **Reise** — planning overview and readiness;
- **Heute** — the selected/current day, route and execution context;
- **Erinnerungen** — personal media and travel history;
- **Reisebegleiter** — conversational planning and assistance.

Technical or occasional modules such as import, total route, documents and costs, trips, decisions and handoffs live in a secondary tools menu or contextual entry points.

The underlying internal tab identifiers may remain stable for compatibility, but the visible hierarchy must follow the traveller's task.

## Consequences

- The mobile interface remains bounded as new providers are added.
- Frequently used travel actions require fewer taps.
- Diagnostic and administrative functions no longer dominate the product surface.
- Contextual links may still open a secondary tool directly.

## Rejected alternatives

- Keeping every module as a permanent top-level tab.
- Hiding all advanced functions inside Home Assistant settings.
- Building separate applications for planning, travel and memories.
