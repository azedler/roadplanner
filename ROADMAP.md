# Roadplanner Roadmap

## Current baseline

- Latest public feature release: [GitHub Releases](https://github.com/azedler/roadplanner/releases/latest)
- Active major development: **Roadplanner 4.0**
- Technical domain: `roadplanner_mcp`

## Roadplanner 4.0 — Destination & Media Intelligence

### Destination identity and providers

- [x] Provider-neutral `place_profile` schema version 2.
- [x] OpenStreetMap/Nominatim remains the open default and durable place source.
- [x] Optional backend-only Google Places (New) discovery with fallback and preferred modes.
- [x] Visible Google Maps attribution and review-only candidate selection.
- [x] Google Place IDs retained as source references while transient Google content is not copied into the durable Roadbook profile.
- [x] Park4Night, OpenStreetMap, Wikidata, Wikipedia and Google Maps IDs/links remain traceable source hints without unofficial scraping.
- [x] Actual destination point separated from a derived drivable parking/access point.
- [x] Manual stop deletion with explicit confirmation and retained media/documents.
- [ ] Live acceptance with the known destination regression set on Home Assistant.

### Media intelligence

- [x] Personal photos, planning images and external-provider failures have separate states.
- [x] Trip, day and stop covers use independent explicit selections.
- [x] Date-only photo suggestions cannot become automatic trip covers.
- [x] Existing personal photos prevent a misleading generic no-images error.
- [x] Concise destination-profile queries are available without requiring a complete provider profile.
- [x] Deterministic local selection remains authoritative when optional Vision fails or is disabled.
- [ ] Live acceptance for Weiße Düne, Restorāns Meke and trip-cover selection.

### Reproducible AI and patch delivery

- [x] Multi-patch preflight in an isolated temporary worktree.
- [x] Filtered repository context export with Git status, log, diff and optional check evidence.
- [x] Secret, Home Assistant storage, personal travel data, media and archive exclusions.
- [ ] Independent review of one exported package and documentation of findings.

## Roadplanner 3.0 — Product foundation

### Foundation completed

- [x] GitHub is the source of truth for code.
- [x] HACS-compatible repository layout.
- [x] `main` and `develop` branch model.
- [x] Apache-2.0 licensing and NOTICE.
- [x] AI Development Contract.
- [x] Architecture Decision Records.
- [x] Patch-based iPad/Codespaces workflow.
- [x] Repository governance, validation, release, and Definition-of-Done contracts.
- [x] RP-500: Codespaces/GitHub release preparation with protected tags and HACS publication.
- [x] Roadplanner 3.2: automatic publication on the merged `main` commit without Codespaces workflow-dispatch permissions.

### Domain consistency

- [x] Canonical stop ordering shared by map, timeline, routing, navigation, decisions, archives, imports, and assistant context.
- [x] User-confirmed legacy list order as fallback; schedule times never reorder stops.
- [x] Overnight continuity without duplicate canonical stops in derived views.
- [x] Day timeline built exclusively from effective ordered route elements.
- [x] Legacy day `start`/`end` fields no longer create conflicting route elements.
- [x] Every stop mutation persists a complete one-based `position` sequence.
- [x] Explicit location completeness states and partial-route UX for GPS-less stops.
- [x] Review-only GPS completion through the existing change-basket/geocoding pipeline.

### Planning metrics and overview

- Rough full-trip distance from confirmed planning anchors before detailed stops exist.
- Separate estimated planning distance from calculated driving distance.
- [x] Phase-aware overview for planning, preparation, travel, and memory.
- Planning completeness, open decisions, due tasks, document readiness, and route coverage.

### Expenses and EUR reference values

- Preserve original amount and currency.
- Add optional EUR reference amount.
- Support daily, trip-start, and manual fixed-rate policies.
- Store rate source, effective date, conversion mode, and timestamp.
- Aggregate EUR totals without hiding per-currency totals.

### Assistant lifecycle

- Session conversation per user and trip.
- Controlled summarization of long conversations.
- Attachment and image-context pruning.
- Persistent Roadbook remains memory after commit.
- Diagnostics remain separate from normal travel UX.

### Media Intelligence

- [x] Automatic stop galleries with Wikimedia Commons and Openverse.
- [x] Up to three planning images, source/license metadata, main image, reordering, and swipe gallery.
- [x] Existing OneDrive travel photos preferred in decisions.

#### Local analysis by default

- [x] exact/file-hash and metadata duplicate collapse,
- [x] local burst grouping and representative selection,
- blur, exposure, and resolution scoring,
- [x] time/GPS assignment confidence in local ranking,
- [x] best representative per exact duplicate group.

#### Optional AI curation

- operate only on the locally reduced set,
- select story-oriented highlights per stop/day,
- propose title images and captions,
- user approval remains authoritative.

### Architecture

- Provider APIs v1.
- Roadbook schema v1 freeze and migration contract.
- Incremental Core, Home Assistant adapter, and UI boundaries.
- Eliminate duplicate configuration and derived-logic sources.

## Roadplanner 3.1 — Canonical Day Experience

- [x] User-confirmed stop sequence independent of schedule times.
- [x] Complete positions after every stop mutation.
- [x] Shared order for map, flow, routing, navigation and assistant planning.
- [x] Explicit location completeness and partial-route UX.
- [x] Review-only GPS completion through the change basket.
- [ ] Live acceptance on the active journey.

## Roadplanner 3.2 — Intelligent Travel Foundation

- [x] Trip-wide travel-integrity score for sequence, GPS, routes and images.
- [x] Review-only bulk GPS completion for the active trip.
- [x] Automatic background planning-image enrichment for current and upcoming days.
- [x] Personal OneDrive photos suppress unnecessary stock-image searches.
- [x] Release publication starts automatically after the prepared pull request is merged into `main`.
- [x] GitHub publishes the release assets and fast-forwards `develop` when safe.

## Roadplanner 3.4 — Complete Places & Vision Smart Media

- [x] Reviewable full-place profiles instead of GPS-only drafts.
- [x] Concrete server-generated ChangeSets from explicitly selected place candidates.
- [x] Candidate previews with map, address, category, contact/opening information, confidence and images.
- [x] Smarter representative planning-image selection from Wikimedia Commons/Openverse.
- [x] Deterministic best-of selection for personal OneDrive photos.
- [x] Automatic planning-image-before-visit / personal-photo-after-visit presentation policy.
- [x] Local deterministic prefilter before any external Vision request.
- [x] Optional Gemini Vision selection for representative planning images and personal travel-photo highlights.
- [x] Manual cover selections always override semantic curation.
- [x] Fingerprint cache, per-trip daily limit and local fallback for every provider or quota failure.

## Roadplanner 3.5 — Reliable places and cleanup

- [x] Structured address parsing and bounded Nominatim variants.
- [x] Reviewable weak candidates and manual WGS84 confirmation.
- [x] Optional AI text cleanup without AI-generated coordinates.
- [x] Existing-day `day_ref` normalization.
- [x] Repository-local patch helper.

## Roadplanner 3.6 — Smart destination enrichment and stop order

- [x] Type-aware destination queries and provider/source hints.
- [x] Concise image queries derived from destination identity.
- [x] Safe gallery refresh after a stale day reference.
- [x] Touch-friendly manual stop ordering with one canonical persisted sequence.

## Later product themes

- Trip-wide travel-story chapters, movie and photo-book exports.
- Apple Photos, Google Photos, Immich, NAS and additional OneDrive providers.
- EVCC-aware charging, weather-aware suggestions, improved offline mode and mobile notifications.

## Planning policy

The canonical internal priority list is [BACKLOG.md](BACKLOG.md). GitHub Issues are used for concrete reproducible bugs and external feedback, not for every product idea.
