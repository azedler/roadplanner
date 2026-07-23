# Security and Privacy

## Never commit

- API keys, tokens or passwords,
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

## Reporting

Until a public security contact is selected, use a private GitHub Security Advisory in this repository. Do not disclose secrets or personal trip data in a public issue.

## Optional image analysis

- Roadplanner performs deterministic local filtering before any external image analysis.
- Existing installations default to local-only media curation. Hybrid Vision is opt-in.
- Hybrid mode sends only a bounded set of reduced thumbnails and opaque image IDs to the configured assistant provider.
- Original OneDrive files are never modified or deleted by curation.
- Roadplanner does not ask the model to identify people or infer sensitive personal traits.
- Candidate fingerprints, per-trip daily limits and local fallbacks prevent repeated or unbounded external calls.
