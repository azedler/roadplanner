# ADR-003: Media originals and references

- **Status:** Accepted
- **Date:** 2026-07-20

## Context

Travel photos can be numerous and already live in systems such as OneDrive or Apple Photos. Copying all originals into Home Assistant would duplicate storage, increase backup size and create unclear ownership.

## Decision

Roadplanner stores media references and curation metadata, not provider-hosted originals.

The media index may contain:

- provider and provider item ID,
- capture time and location,
- stable assignment to trip/day/stop,
- duplicate-group and quality metadata,
- highlight and title-image state,
- attribution and source metadata.

Provider access URLs and thumbnails are short-lived derived data and must be refreshed as needed.

Exceptions:

- A user may explicitly upload an original to the private Roadplanner archive.
- A device may keep a best-effort offline cache, but that cache is not authoritative.

## Consequences

- OneDrive or another provider remains the owner of its originals.
- Roadplanner backups stay bounded.
- Provider disconnection may temporarily prevent full-size viewing, while assignments and metadata remain.
- Media providers must expose a stable reference contract.

## Rejected alternatives

- Copy every synchronized photo into Home Assistant.
- Store expiring provider URLs as permanent media identifiers.
- Treat browser cache as durable storage.
