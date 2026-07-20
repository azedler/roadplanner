# ADR-002: Review-only mutation pipeline

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Roadplanner accepts changes from direct UI actions, assistants, importers and external providers. Applying these changes through separate code paths risks partial writes, stale revisions and assistant prose that claims a change happened when it did not.

## Decision

Every canonical Roadbook mutation follows one pipeline:

```text
intent
→ normalized operation
→ complete validation
→ review preview
→ explicit user approval
→ atomic apply
→ revision increment exactly once
```

Rules:

- Assistants and importers produce proposals only.
- The server supplies canonical trip ID, base revision, timestamps and change-set metadata.
- Existing object IDs come from the current Roadbook.
- A failed, rejected, stale or no-op proposal does not increment the revision.
- UI text may confirm a queued or applied change only after the server confirms that state.

Direct manual editing may use a streamlined preview, but it still uses the same domain validation and atomic write engine.

## Consequences

- One validation contract protects all write sources.
- Revision conflicts are explicit.
- Regression tests can target one mutation pipeline.
- New providers cannot bypass business rules.

## Rejected alternatives

- Direct writes from assistant or importer modules.
- Separate mutation engines for UI and AI.
- Best-effort partial application of multi-operation changes.
