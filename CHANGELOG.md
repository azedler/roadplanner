# Changelog

All notable changes to Roadplanner will be documented here.

The project follows Semantic Versioning for public releases.

## [Unreleased]

### Fixed

- Updating an *existing* stop (as opposed to adding a new one) never enabled Gemini's search/`url_context` tools, even without a resolved `place_query` - only `add` did. A pasted booking link on a stop the chat step had already matched to a prior placeholder therefore got no fetch at all: the model had to guess a name from conversation context alone, and no location was ever resolved. Any stop `add` or `update` without a `place_query` yet, or that mentions a non-Google-Maps link anywhere in its basket text, now enables research the same way.
- Updating a stop, day, or trip's `details` (nested planning metadata - geocoding results, transport/ferry info, source attributions from a resolved booking link, etc.) silently discarded whatever wasn't part of that particular update's patch, since it was a wholesale dict replacement rather than a merge. An update meant only to change e.g. an arrival time, but that happened to also touch `details` for an unrelated reason, would wipe out previously stored `details` sub-keys with no error or warning. `details` is now merged one level deep on update; every other field still overwrites as before.

## [4.4.0] - 2026-07-27

### Added

- Crew &amp; Fahrzeuge: a new cross-trip master-data registry for people and vehicles, managed once under the new "Crew & Fahrzeuge" panel tab instead of being retyped per trip. Retiring a person or vehicle (e.g. selling a camper) only hides it from selection for new trips - it is never deleted, so trips that already selected it keep working. The trip-edit dialog now lets you pick which people and which vehicle are along for that specific trip; the selection is stored as a point-in-time snapshot on the trip (`travelers`/`vehicle`), so later edits to a person's or vehicle's master data don't retroactively change already-planned trips.
- The assistant can now resolve a non-Google-Maps link given for a new or updated stop (Booking.com, Hotels.com, Airbnb, Park4Night, or any other booking/site link) by having Gemini fetch and read the page itself via its `url_context` tool, instead of needing a bespoke parser per booking provider. The model may only extract a place name/address for `place_query` (still verified server-side through the normal geocoding check, exactly like any other place text) plus a few concrete, attributed facts (amenities, price, rating) into notes - it never reports coordinates straight from the page, and an unresolvable page stays an open question rather than a guess. Google Maps keeps its existing, cheaper no-fetch link resolution.

## [4.3.0] - 2026-07-27

### Added

- New `GeminiClient.async_generate_image()` provider capability (dedicated image model, never falls back to the configured text/vision model) plus `vehicle_icon_service.async_generate_vehicle_icon()`, which turns a short free-text vehicle description into a flat, line-art icon image matching Roadplanner's existing icon style. Not yet wired into any user-facing flow (trip-summary PDF/video work is still prototype-only); this lands the tested building block first.

### Changed

- Google Places photos saved into a destination gallery no longer go stale. Instead of persisting Google's short-lived photo URL, Roadplanner now stores only the durable Google photo reference and resolves a fresh URL on demand through a signed redirect (`/api/roadplanner/google_photo/...`), mirroring the existing OneDrive personal-media redirect. Google's photo bytes/URL are still never written to disk. Each view spends one entry of the Google photo daily quota, since it re-resolves live - keep that in mind for the daily limit if a gallery with Google photos is opened often.

### Fixed

- External ChangeSets submitted through the handoff webhook (voice assistants, automations, other tools calling the Roadplanner LLM API) now get the same Google Maps link resolution and geocoding the in-panel assistant chat already applies to its own compiled operations. Previously only the in-panel chat's compile step resolved a pasted Google Maps link (including short `maps.app.goo.gl` links) into coordinates/a place name before geocoding it into `changes.location`; a stop added through any other ChangeSet-submitting path had no such resolution at all, so it kept only whatever bare location the external caller supplied itself.

## [4.2.0] - 2026-07-26

### Added

- Optional, off-by-default Google Places photos as a third destination-image source alongside Wikimedia Commons and Openverse. Requires an explicit new "Google-Fotos aktivieren" toggle in options (separate from the existing Google Places search toggle) plus a Google Places API key; has its own daily request quota, tracked separately from Google Places text search. Returned images show a "Foto von Google" attribution and are not cached in the normal 12-hour destination-image cache, since Google does not guarantee how long the returned image URL stays valid - treat this as a test/preview source for now. See `docs/product/EXTERNAL_SERVICES_AND_PRIVACY.md` for the full data-flow and licensing caveat.

### Changed

- Internal: completed the `frontend/roadplanner-panel.js` decomposition (6749 → 2092 lines), the last of EPIC-006's four planned module decompositions. Split into 3 `frontend/lib/*.js` infrastructure modules (styles, constants, core helpers) and 8 `frontend/features/*.js` mixins (universal import, place enrichment, archive, media, decisions/integrity, assistant, route/map, trip/day/stop), applied to the panel's prototype at load time. Also landed the panel.py/test-harness infrastructure this needed: `panel.py` now serves the whole `frontend/` directory instead of one specific file, and all 8 `import()`-capable frontend tests switched from a classic-script `vm.runInThisContext` harness to real ES-module `import()`. No functional or behavior changes; each step was validated against the full test suite.

## [4.1.0] - 2026-07-26

### Changed

- Internal: completed the `experience_manager.py` decomposition (3143 → 419 lines), splitting OneDrive photo-curation, the AI-assisted planning-photo gallery system, and the aggregated "experience" panel payload assembly into their own collaborators (`media_curation_manager.py`, `destination_gallery_manager.py`, `panel_payload_builder.py`).
- Internal: completed the `roadplanner.py` decomposition (3605 → 403 lines), splitting the store's exception hierarchy, file I/O, ID/JSON validation, routing metrics, document normalization, trip state, on-disk repository, queries, mutations, ChangeSet handling, and handoff-context export into twelve focused modules. `RoadplannerStore` is now a thin facade; the public API is unchanged. Also resolved the one circular import between `roadplanner.py` and `changeset.py`. No functional or behavior changes in either decomposition; each step was validated against the full test suite.

### Fixed

- Failed planning-image searches (Wikimedia Commons/Openverse unreachable, timed out, etc.) now write a debug-level log entry (`Destination image provider %s failed: ...`) instead of failing completely silently. Enable debug logging for `custom_components.roadplanner_mcp` to see the actual cause behind a stop's "Bilder konnten noch nicht geladen werden".

### Added

- The assistant now recognizes a Google Maps link (including `goo.gl`/`maps.app.goo.gl` short links) given for a new or updated stop and resolves it into a `place_query` deterministically from the link's own URL structure - never by fetching the Google Maps page. A coordinate pair encoded in the link takes priority and is verified through the normal GPS-Prüfung reverse-geocoding; otherwise the place name in the link is used as a search query. The result is still reviewed and confirmed like any other place before it becomes a durable stop.

## [4.0.3] - 2026-07-26

### Changed

- Internal: continued decomposing large integration modules into smaller, single-responsibility files (`assistant.py` fully split into four modules; `experience_manager.py` partially split, through the OneDrive media sync engine). No functional or behavior changes; included so the OneDrive sync path can be exercised on a live installation after the refactor.

## [4.0.2] - 2026-07-26

### Added

- Universal Import now recognizes Park4Night-style overnight-stay screenshots: visible GPS coordinates are carried into the existing GPS-Prüfung as a reverse-geocoding query (never trusted directly), and visible name/amenities/portal ID are copied into the stop notes for the existing Park4Night source-hint recognition.

### Fixed

- Pasting an image (Ctrl+V) directly into the "Reisebegleiter" chat message field now attaches it as a document/receipt, matching the existing paperclip attach flow. Previously, paste only worked inside the dedicated archive drop/paste zones.

## [4.0.1] - 2026-07-26

### Fixed

- Park4Night place IDs written without a URL – for example `Park4Night 448383`, `P4N-448383` or `Park4Night-ID: 448383` – are now recognized as source hints, classify the stop as an overnight place, are stripped from the destination name used for provider searches, and stay out of image queries.
- A recognized Park4Night place ID now outranks the AI text classification when both disagree, so such stops are always searched as camping/overnight places.
- Source hints no longer lose their provider and place ID when a confirmed Google Places discovery result is converted into the durable place profile; the Park4Night link stays labeled after confirmation.

## [4.0.0] - 2026-07-24

### Added

- Optional backend-only Google Places (New) destination discovery with fallback/preferred modes, visible Google Maps attribution, provider diagnostics, a conservative in-process daily limit, and setup documentation.
- Provider-neutral place-profile schema version 2 with structured address, durable provenance, source references, and a separate derived drivable access point for road routing.
- Explicit stop deletion from the day editor while retaining linked documents and personal media.
- Independent trip, day, and stop cover selection with manual overrides and deterministic personal/planning-image fallbacks.
- `tools/dev.py apply-series` for isolated multi-patch preflight and `context-export` for filtered AI/reviewer snapshots with Git metadata.

### Changed

- Google content is used only as transient reviewed discovery: Roadplanner keeps the Place ID reference and normalizes persistent coordinates and address data through OpenStreetMap/Nominatim or manual confirmation.
- Place search can pass bounded location and target-type hints to provider implementations while keeping the API key and provider calls server-side.
- Road routing keeps the real destination marker and can route a vehicle to a nearby derived access point instead of silently dropping an unreachable stop.
- Image status distinguishes existing personal photos from external-provider failures, and concise destination-profile queries are used even when a complete provider profile is unavailable.
- Automatic trip covers reject photos assigned only by date, preventing unrelated but technically strong images from becoming the journey hero.

### Fixed

- Gallery cards no longer report that no images are available when personal photos already exist.
- A stale day reference no longer prevents a uniquely identifiable stop gallery from being refreshed.
- Candidate provider host names are matched only as exact domains or real subdomains instead of unsafe arbitrary substrings.
- Non-drivable nature and beach destinations remain visible in the day route while navigation uses a separately explained access point.

### Security

- Google Maps Platform keys are excluded from panel data, logs, diagnostics, patches, and exported AI context packages.
- Google search responses use a short-lived in-memory cache and do not request or persist photos, reviews, ratings, or atmosphere fields.

## [3.6.0] - 2026-07-24

### Added

- Geodata-first destination intelligence that classifies addresses, ferry and transport terminals, hikes, nature centres, attractions, retail, gastronomy, camping and other stop types before provider search.
- Bounded type-aware geocoding query plans and persisted provider identifiers, destination kinds, source hints and concise image queries in confirmed place profiles.
- Recognition of Park4Night, OpenStreetMap, Wikidata, Wikipedia and Google Maps links as reviewable source hints without treating them as verified coordinates.
- Touch-friendly manual stop ordering with earlier/later controls and direct numbered positions for each Roadbook day.

### Changed

- Place enrichment now rejects surrounding locality results as automatic matches for specific POIs and falls back from reverse geocoding to bounded type-aware forward searches near existing coordinates.
- Destination image search uses the confirmed place identity, city, country and category while excluding notes and day titles; coordinates remain a separate ranking signal.
- The place-review UI is presented as “Stopps anreichern” and explains the geodata-first workflow while leaving times and confirmed stop order unchanged.

### Fixed

- Address parsing retains `Neuhäuser 40`, `01844 Neustadt in Sachsen` and `Krumhermsdorf` instead of turning aggregate category text into a city.
- German destination terms such as `Fährterminal` and hyphenated `-Wanderung` are translated into provider-friendly POI searches without losing the proper name.
- Overlong internal image queries are shortened safely instead of failing at the 400-character provider boundary.
- Gallery refreshes recover a uniquely identifiable stop after a stale day reference and continue with the canonical day and stop IDs returned by the backend.
- Manual move controls calculate their target from the canonical explicit position sequence instead of a potentially stale payload array order.

## [3.5.0] - 2026-07-23

### Added

- Structured address parsing and controlled multi-variant Nominatim searches with explicit house, street, locality and mismatch quality levels.
- Reviewable weak place candidates instead of an immediate dead end when only a street, locality or partial address can be resolved.
- Optional AI place-text cleanup that can normalize names and address fields without receiving, producing or verifying coordinates.
- Manual WGS84 place confirmation with explicit non-provider-verified provenance and separate confirmation for AI-suggested stop renames.
- Safe local `tools/dev.py` commands for repository status, full checks, reviewed patch application and binary-safe staged patch export.

### Changed

- Place completion now separates text normalization, provider geocoding and user confirmation so AI suggestions can never silently become map coordinates.
- Place-review dialogs expose match quality, search provenance, manual fallback and optional AI cleanup while preserving the existing ChangeSet review boundary.
- Technical `assistant_prepare` diagnostics remain available, while the visible dialog explains day-assignment failures in user-facing language.

### Fixed

- Existing Roadbook day IDs returned by the assistant in `day_ref`, including `day-e6c19b335d42`, are losslessly normalized to `day_id`; true new-day references remain strict.
- Place completion no longer requires an exact provider result before showing useful review candidates or allowing an intentional manual map point.

## [3.4.0] - 2026-07-23

### Added

- Reviewable full-place enrichment for incomplete stops, including candidate name, address, coordinates, category, website, phone, e-mail, opening hours, source, map link, confidence and up to three planning images.
- Direct review-only ChangeSet creation from explicitly selected place candidates, without routing the selected values through Gemini again.
- Smart local best-of selection for personal OneDrive photos with duplicate collapse, burst suppression, screenshot penalties and time-diverse highlights.
- Optional hybrid Gemini Vision curation after deterministic local preselection, with bounded candidates, structured image-ID selection and manual-cover priority.
- Semantic selection for representative planning-image covers and personal OneDrive travel-photo highlights.
- Persistent media-curation fingerprints and per-trip daily Vision limits to avoid repeated external analysis of unchanged candidate sets.

### Changed

- Travel integrity evaluates confirmed place profiles instead of treating coordinates alone as a fully complete stop.
- The former GPS-only repair flow is replaced by “Orte vervollständigen”, so users confirm the actual place rather than isolated coordinates.
- Planning-image ranking now separates relevance and technical quality, penalizes logos/maps/posters and prefers diverse representative photos.
- Stop and day presentation explicitly prefers personal `travel_images` after a visit and falls back to attributed `planning_images` before it.
- Media curation defaults to local-only for existing installations; hybrid Vision must be enabled explicitly in Roadplanner options.
- Planning-image and travel-photo galleries label whether selection is local or Vision-curated.

### Fixed

- Place-completion drafts now contain the selected coordinates and place details instead of empty update operations.
- A rejected or unavailable image provider no longer prevents another provider or the place profile from being reviewed.
- Coordinate-only stops remain routable but are visibly flagged until their place identity has been confirmed.
- Any Gemini Vision timeout, invalid output, unavailable thumbnail or exhausted daily limit now keeps the deterministic local best-of selection instead of blocking the stop or album.

## [3.2.1] - 2026-07-22

### Changed

- Assistant operation payloads now normalize lossless structured-output variants before strict Roadbook validation.
- The compile prompt explicitly requires `changes` to be one JSON object and `{}` for move/remove operations.

### Fixed

- `assistant_prepare` no longer fails when Gemini returns `changes` as a one-item object list, a list of disjoint field fragments, field/value records, simple JSON-Patch records, or a JSON-encoded object.
- Move operations with omitted, empty, or explanatory `changes` values are normalized to an empty object instead of raising `changes muss ein JSON-Objekt sein`.
- Conflicting change fragments and accidentally nested multiple operations remain rejected instead of being guessed or merged.

## [3.2.0] - 2026-07-22

### Added

- Trip-wide travel-integrity report with scores for stop order, GPS completeness, routes and visual readiness.
- Review-only bulk GPS completion for all incomplete stops in the active trip.
- Automatic bounded planning-image enrichment for the active trip, including background scheduling and provider-status diagnostics.
- Travel-quality dashboard card and a mobile-friendly detail view with direct repair actions.
- Automatic GitHub publication after a prepared release pull request is merged into `main`.

### Changed

- Planning-image enrichment prioritizes the current and upcoming travel days and skips stops that already have personal OneDrive photos.
- The panel starts only one small best-effort image batch; the backend continues enrichment without blocking the UI.
- Release preparation now documents that merging the release pull request is the publication trigger.
- `tools/release.py publish` observes or verifies the automatic workflow instead of attempting an API dispatch that Codespaces may reject.
- Missing schedule times remain informational and never change the confirmed stop order or lower the trip-integrity score.

### Fixed

- Trips with missing GPS no longer require manual day-by-day diagnosis before repair drafts can be prepared.
- Release publication no longer depends on a Codespaces token having permission to call `workflow_dispatch`.
- Existing personal travel photos are no longer displaced by unnecessary stock-image searches.

## [3.1.0] - 2026-07-22

### Added

- Canonical location states for every day-route node, including explicit missing, ambiguous and unverified GPS data.
- Review-only “GPS prüfen/ergänzen” workflow that prepares geocoding drafts for incomplete stops without inventing coordinates.
- Complete map legends and partial-route notices that keep GPS-less stops visible in their confirmed sequence.
- Two-stage release automation for Codespaces: prepare, validate, push, pull request, publish, and branch synchronization.
- Protected GitHub release workflow that validates the exact `main` commit, creates a lower-case version tag, publishes release notes from the changelog, and attaches validated manual-install artifacts.
- Canonical Roadplanner validation workflow for pull requests to `main`, with an on-demand manual trigger.

### Changed

- Stop order is independent from schedule times: complete explicit positions win; legacy days preserve their stored user-confirmed list order.
- Every stop mutation and ChangeSet operation leaves a complete gap-free one-based `position` sequence behind.
- The assistant plans stop additions and moves against the complete canonical day sequence and emits explicit positions.
- Local and GitHub release checks now use the same `tools/release.py check` entry point.
- Release preparation cuts the `[Unreleased]` changelog section and keeps `manifest.json` and `const.py` versions synchronized.
- Python caches are removed by release automation before and after tests instead of requiring repetitive manual cleanup.

### Fixed

- A timed ferry can no longer jump ahead of untimed parking, pharmacy, shopping or service stops.
- GPS-less stops no longer disappear silently from the day map; the route remains visibly partial until reviewed coordinates exist.
- GPS repair for an inherited overnight start targets the owning previous Roadbook day instead of creating a duplicate stop.

## [3.0.0] - 2026-07-22

### Added

- Canonical day view-model shared by maps, stop cards, schematic day flow, navigation, decisions and assistant context.
- Phase-oriented Roadplanner navigation: Reise, Heute, Erinnerungen and Reisebegleiter.
- Roadplanner 3.0 dashboard with planning progress, open decisions, urgent tasks, visual readiness and the next travel day.
- Deterministic local media curation with duplicate collapse, burst suppression and per-stop/per-day highlights.
- Automatic day covers that prefer personal OneDrive travel photos and fall back to attributed planning images.
- Roadplanner 3.0 Vision & UX Blueprint as the product contract for subsequent work.

### Changed

- Inherited overnight stops are displayed as a shared start marker without renumbering Roadbook-owned stops.
- Legacy `day.start` and `day.end` values remain contextual metadata but no longer appear as pseudo-stops when real stops exist.
- Decision cards prefer locally curated personal travel-photo highlights before external planning images.
- Stop cards show a curated highlight strip while the full OneDrive album remains accessible.
- Technical tools move into a secondary menu so the primary navigation follows the travel lifecycle.

### Fixed

- Map markers, route flow, stop cards, Google Maps handoff and assistant context no longer use divergent day sequences.
- Legacy targets such as a stale `Riga` day-end label no longer appear in the graphical route unless a real Roadbook stop exists.
- Personal-photo duplicates and short bursts no longer dominate stop and day covers.

## [2.8.0] - 2026-07-21

### Added

- Canonical stop ordering shared by Roadbook payloads, maps, day cards, routing, navigation, decisions, assistant context, archives, and imports.
- Automatic destination galleries with up to three planning images per stop.
- Wikimedia Commons coordinate-aware image search and Openverse fallback with source and license metadata.
- Main image selection, reordering, removal, full-screen swipe gallery, lazy loading, and inline retry states.
- Decision slides with up to three images and preference for the stop's own OneDrive travel photos.
- Tolerant structured-output parsing and one bounded Gemini repair attempt for malformed JSON responses.

### Changed

- Stop numbering and derived day routes now use one deterministic ordering contract.
- Existing explicit `position` values remain authoritative; legacy trips fall back to times, start/overnight roles, and stable storage order.
- Destination image providers run concurrently and fail independently.
- Image searches use stop name, category, place, country, coordinates, description, and day context.
- External destination images remain provider-hosted; Roadplanner stores only URLs, attribution, licensing, and selection metadata.

### Fixed

- Maps, stop cards, day flows, routing, navigation, and assistant context no longer disagree about stop order.
- A failed Wikimedia request no longer blocks a stop card, decision template, or alternative image provider.
- Assistant prepare requests can recover from JSON wrapped in Markdown, surrounding prose, a bare list, or a nested JSON string.
- OneDrive image references in persisted decisions are resolved to fresh signed URLs when the panel payload is loaded.

## [2.7.2] - 2026-07-21

### Added

- Decision templates can include the currently planned Roadbook stop as a verified baseline option.
- Current-plan decision slides are visibly labelled and require no change-basket transfer.

### Changed

- Markdown links from the assistant tolerate safe line wrapping inside long HTTPS URLs.
- The assistant review button shows a dedicated progress state and opens the handoff overview after preparation.
- Keep-or-replace decisions may contain the current plan plus up to three alternatives.

### Fixed

- Google Maps Markdown links with Unicode query values are rendered as clickable links.
- The "Änderungen prüfen" button no longer fails silently on touch devices.
- A stale "last message unanswered" banner is cleared when a later assistant reply exists.
- Decision questions that mention keeping the existing plan can no longer omit that plan from the options.

## [2.7.1] - 2026-07-21

### Added

- Assistant responses can contain safely clickable HTTPS and Google Maps links.
- Persistent, mobile-friendly error dialogs with retry and copy-details actions.
- Visible assistant loading state while requests are processed.

### Changed

- Assistant responses are rendered directly without a blocking full panel reload.
- Decision option enrichment runs concurrently with bounded timeouts.
- Missing images, routes, or geocoding results no longer invalidate an entire decision draft.
- Gemini timeout handling reserves time for a configured fallback model.

### Fixed

- Assistant and decision errors are no longer hidden or clipped at the bottom of mobile screens.

## [2.6.5] — Imported baseline

### Added and stabilized

- Native conversational assistant and change basket.
- Roadbook, routes, stops and inherited overnight starts.
- Routing and Google Maps handoff.
- Documents, expenses and daily tasks.
- Decisions and image-based option cards.
- OneDrive Personal photo synchronization and albums.
- Universal importer.
- Mobile layout and numerous assistant-normalization fixes.

This entry records the first Git-managed baseline. Detailed historical notes are preserved in `docs/legacy/2.6.5/`.
