# Backlog

## Completed foundation

- GitHub/HACS-compatible repository baseline.
- `main` and `develop` branch model.
- Apache-2.0 licensing.
- AI-to-Git patch workflow.
- Architecture Decision Records.
- Repository governance, Definition of Done, test strategy, and release process.
- RP-500: Automated, explicitly approved GitHub release pipeline.

## Completed in Roadplanner 2.8.0

- Canonical stop ordering across Roadbook, panel, routing, navigation, assistant, decisions, archives, and imports.
- Automatic stop galleries with Wikimedia Commons and Openverse.
- Multi-image decision slides and OneDrive-photo preference.
- Structured Gemini output normalization and bounded repair.


## Completed in Roadplanner 3.0.0

- RP-401: Canonical Day Model shared by all travel-day consumers.
- RP-402: Visual planning day covers and automatic planning-image presentation.
- RP-403: Personal-memory prioritization with local duplicate and burst reduction.
- RP-404: Phase-oriented navigation, dashboard and Roadplanner 3.0 mobile UX foundation.

## Prepared for Roadplanner 3.1.0 — Canonical Day Experience

- Stop order is authoritative through complete positions or stored legacy list order; times are descriptive only.
- Add, update, move and remove paths reindex positions consistently.
- Assistant operations maintain the complete day sequence and insert ordinary stops before the overnight destination by default.
- Canonical location states expose missing, ambiguous and unverified GPS data.
- Map legends retain all stop numbers and visibly mark GPS gaps.
- “GPS prüfen/ergänzen” creates review-only geocoding drafts, including inherited overnight starts.

## Next — correctness

## Next — planning UX

### RP-405: Planning distance and readiness refinement

- Estimated full-trip distance before detailed GPS stops exist.
- Separate estimated and calculated distance.
- Planning completeness, open decisions, due tasks, and document readiness.

### RP-406: Assistant conversation compaction

- Summarize old text context.
- Prune obsolete attachments and image context.
- Preserve current Roadbook facts and recent conversation.

## Next — expenses

### RP-407: EUR reference conversion

- Preserve original amount and currency.
- Store EUR reference amount with rate source, date, and policy.
- Show original and EUR totals without silent currency mixing.

## Next — Media Intelligence

### RP-408: Perceptual media quality refinement

- Add perceptual hashes for visually identical files with different metadata.
- Add blur and exposure measurements from image bytes.
- Preserve manual cover and assignment overrides.

### RP-409: Optional AI highlight curation

- Analyze only locally reduced candidates.
- Top images per stop/day.
- Story-oriented title-image and caption proposals.

## Later — architecture and delivery

- Public-source and third-party license audit.
- Provider interfaces v1.
- Roadbook schema v1 and migration harness.
- Incremental separation of domain, Home Assistant adapter, and UI.
