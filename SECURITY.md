# Security and Privacy

## Never commit

- API keys, tokens or passwords, including Google Maps Platform and Gemini credentials,
- Home Assistant `.storage` files,
- Roadbook data from real trips,
- booking documents and references,
- receipts and expense records,
- OneDrive identifiers or photo metadata,
- handoff/archive directories,
- diagnostic logs containing personal data.

## Runtime principles

- Documents and provider tokens stay in private Home Assistant storage.
- External providers receive only the minimum data needed for the requested operation.
- AI output is untrusted until validated.
- Changes remain review-only until explicitly applied.
- Download and media links should be short-lived and unguessable.

## Optional Google Places

- Google Places is disabled until the Home Assistant operator explicitly enables it and stores an API key in the Roadplanner config entry.
- The API key is backend-only and must never be exposed in the panel payload, logs, diagnostics, patches, or context exports.
- Keys should be restricted to Places API (New) and to the strongest practical application or egress-IP restriction.
- Google candidates remain review-only, carry visible attribution, and are treated as transient discovery content. Roadplanner persists only the Google Place ID as a source reference and derives durable place data from OpenStreetMap/Nominatim or explicit manual confirmation.
- Google photos, reviews, ratings, and atmosphere fields are not requested in this release.
- Roadplanner's local request counter is a soft guard only; Google Cloud quotas, budgets, and alerts remain the authoritative cost protection.

See [Google Places setup](docs/development/GOOGLE_PLACES_SETUP.md) and [External services and privacy](docs/product/EXTERNAL_SERVICES_AND_PRIVACY.md).

## Reporting

Until a public security contact is selected, use a private GitHub Security Advisory in this repository. Do not disclose secrets or personal trip data in a public issue.

## Optional image analysis

- Roadplanner performs deterministic local filtering before any external image analysis.
- Existing installations default to local-only media curation. Hybrid Vision is opt-in.
- Hybrid mode sends only a bounded set of reduced thumbnails and opaque image IDs to the configured assistant provider.
- Original OneDrive files are never modified or deleted by curation.
- Roadplanner does not ask the model to identify people or infer sensitive personal traits.
- Candidate fingerprints, per-trip daily limits and local fallbacks prevent repeated or unbounded external calls.
