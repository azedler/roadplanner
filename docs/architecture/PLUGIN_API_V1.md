# Provider API v1 — architecture draft

Roadplanner providers are adapters behind stable internal contracts.

## Provider types

- Assistant
- Routing
- Geocoding
- Media
- Weather (future)
- Document analysis (future)

## Shared rules

- Providers do not write canonical Roadbook files.
- Network calls have explicit timeouts.
- Retries are bounded and idempotent.
- Provider output is validated and normalized.
- Provider configuration has one source of truth.
- Provider-specific metadata is namespaced.
- Errors are understandable to users and useful in redacted diagnostics.
