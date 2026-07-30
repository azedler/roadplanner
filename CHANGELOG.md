# Changelog

All notable changes to Roadplanner will be documented here.

The project follows Semantic Versioning for public releases.

## [Unreleased]

### Fixed

- "Park4Night-Daten lesen (KI)" in the stop form worked exactly ONCE per stop and then failed forever with "Kein Park4Night-Verweis gefunden". The first successful lookup overwrote the stop name - which carried the only copy of the p4n reference - with the clean place name, preserving the reference nowhere. The lookup now writes the reference into the notes ("Park4Night: https://park4night.com/lieu/<id>/") before the name is replaced, so repeat lookups keep working after saving.
- A real park4night.com page link was not recognized as a Park4Night reference at all - the shared regex (frontend and backend alike) only matched shorthand forms like "p4n 506374", although the stop form's hint and the panel's error text both promise that pasting the link works. Links like https://park4night.com/lieu/506374/ and /de/place/506374 are now recognized everywhere the shorthand is.

## [4.11.4] - 2026-07-30

### Fixed

- Pasting a tour or booking link into the Reisebegleiter chat (Komoot, AllTrails, Booking, Park4Night, ...) could get the answer "Da ich den genauen Inhalt des Links nicht direkt live auslesen kann..." even though the assistant is fully capable of reading such pages. The chat only enabled its web tools (Google-Suche + url_context page fetch) when the message sounded like a discovery question ("suche", "empfiehl", "in der Nähe", ...) - a pasted URL matched none of those phrases, so a message like "Prüfe den Link" ran without the very tool that opens links. A pasted link now always enables the web tools; Google-Maps links remain excluded, since they are resolved deterministically from their own URL structure and never need a fetch.
- The assistant filed a mountain hike (Skuleberget) as "Stadtbesichtigung" (sightseeing). The compile prompt listed the allowed stop types as a bare enumeration with no semantics - only the sleep-place types had an explicit meaning rule, so the model guessed for everything else. The prompt now carries a short meaning rule for the experience types: `activity` for anything actively undertaken (hike, summit, kayak/bike/boat tour, swim, via ferrata, guided tour), `sightseeing` exclusively for visiting a town (stroll, old town, harbour walk), `attraction` for a single visited sight (museum, castle, church, waterfall), `viewpoint` for a short photo/scenic halt - with the explicit anchor that a mountain or hiking trail is activity, never sightseeing.

## [4.11.3] - 2026-07-30

### Fixed

- Preparing an assistant change review could fail outright with "Änderungsentwurf konnte nicht erstellt werden / Bestehende Stopp-ID ist nicht im aktuellen Reisetag vorhanden", even though the stop existed - just under a different day than the operation referenced, either because the stop moved days after the draft was written (a handoff applied in between) or because the model paired a correctly-referenced stop with the wrong day. Stop IDs are globally unique, so a single match under another day identifies the real day deterministically; the day reference is now corrected automatically (the same stale-day-ID fallback the panel has always had) instead of the whole pending change being rejected. A stop that exists on no day at all remains a hard error - it was deleted or invented, and silently guessing would write to the wrong place.
- Confirming a stop's place profile twice left two pending "Ortsprofile vervollständigen" handoffs for the same stop under Übergaben - easy to do, because the "Ortsprofil vervollständigen" badge on the stop card stays visible while the first handoff sits unapplied. The older handoff then went stale as the trip revision advanced, showing a conflict warning that was pure noise. Submitting a new place-enrichment now automatically archives older pending enrichment handoffs that touch the same stop (marked "superseded"); handoffs from other sources or for other stops are never touched.

### Changed

- The "Ortsprofil vervollständigen" notice on a stop card now says where to actually do that: via the "Stopp anreichern" button below, and that the confirmed change then lands under "Übergaben" for the final apply - previously it asked for a confirmation without naming the place to give it, and didn't mention the second step at all.

## [4.11.2] - 2026-07-29

### Fixed

- Image search failed outright with "Bildsuche fehlgeschlagen" / "Can't find variable: WS_ACTION" - for a normal stop's "Bilder verwalten" just as much as for a pitch option's "Bilder" button. `media.js` referenced the shared `WS_ACTION` WebSocket-message-type constant without importing it; each panel feature file is its own ES module, so importing it in `roadplanner-panel.js` doesn't make it available inside `media.js`. The background auto-populate call hit the same bug silently (swallowed by its own error handling), which is why this went unnoticed until a user actually triggered image search directly.
- Icon-only buttons on a Stellplatz-Option row (Bilder, Bearbeiten, Verwerfen, Löschen, Wiederherstellen) were hard to make sense of without a visible label - a tooltip doesn't help on a touchscreen. Every action now shows real text next to its icon.
- Preparing an assistant change review could fail outright with "Änderungsentwurf konnte nicht erstellt werden / Nicht erlaubte Felder für stop: category", because Gemini sometimes classifies a stop (e.g. "Camping") under a `category` field - a natural word choice for "what kind of place is this" - even though that field only ever belongs to a preference change. The strict per-entity field check rejected the whole pending change outright instead of just that one misplaced field. A stray `category` on anything other than a preference is now salvaged into the notes ("Kategorie: ...") instead, the same approach already used for a stray `text` field.

## [4.11.1] - 2026-07-29

### Fixed

- After a Roadplanner update, some panel tabs (observed on Stellplätze) could keep showing the PREVIOUS release's text and behavior indefinitely, with no error. Root cause: only the panel's entry file (`roadplanner-panel.js`) was ever cache-busted, via the `?v=<version>` query parameter Home Assistant's `panel_custom` appends to its module URL - but that entry file's own static imports (`./features/pitches.js`, `./lib/core-helpers.js`, ...) carry no such parameter and were served with no explicit cache header, so a browser (especially a mobile Companion app WebView) could keep a stale, heuristically-cached copy of a submodule around even after the entry file itself was freshly fetched. Every panel file is now served through a dedicated view that always sends `Cache-Control: no-cache`, forcing revalidation on every load regardless of the URL - a version upgrade can no longer leave part of the panel running old code silently.

### Added

- A dedicated "App aktualisieren" button next to the existing data-refresh icon (kept visible on narrow/mobile widths). A pull-to-refresh gesture doesn't reach the app shell from inside the panel's own scrollable content, so there was previously no way to force a real page/module reload from within the app - only "Neu laden", which just re-fetches data, not code. The new button reloads the page outright (with a confirmation first if a form or dialog is currently open, so in-progress typed input isn't silently discarded); combined with the cache fix above, this is now the reliable way to pick up a fresh release without leaving the app.

## [4.11.0] - 2026-07-29

### Added

- Park4Night pages are now read directly via Gemini (the same url_context fetch the assistant chat already uses during plan handover) - in three places. Park4Night has no public API, so stops like "Parkplatz am Angelteich" stayed at "Ort fehlt" forever; geocoding a generic name is hopeless. (1) "Stopp anreichern": when a stop carries a p4n reference, the review dialog shows a clearly labeled card "Von der Park4Night-Seite gelesen (KI)" with the page's stated GPS position, price and rating; one tap copies it into the existing manual-confirmation form. (2+3) Stop add and edit: the stop form has a "Park4Night-Daten lesen (KI)" button that detects a p4n ID or link in the name/notes, reads the page, and prefills GPS, city and country for review before saving. In every path, AI-read coordinates are never written to the roadbook on their own - reviewing and saving/confirming remains the user's explicit step, stored as manually confirmed rather than provider-verified.
- Planning images for pitch backup options, following the same rule as everywhere else in Roadplanner: before a place is visited its images come from internet sources, afterwards from your own OneDrive photos. The active overnight place is a normal stop and already had both behaviors; backup options now plug into the same internet-image machinery - each option row gets a "Bilder" button that opens the familiar image search (Wikimedia/Openverse, biased by the option's coordinates), the chosen image appears as the option's thumbnail and on the "Plan B" card, and when an option is activated into a real stop its planning gallery is cleaned up so the stop's normal image flow (and later your personal photos) takes over.

### Fixed

- Park4Night stops are handled properly now. Previously the internal ID stayed glued to the stop name ("Parkplatz am Angelteich (p4n #506374)") and polluted every card, map legend and export, while the reference itself did nothing useful on the stop card. Two changes: (1) when the assistant hands over a stop whose name carries a p4n ID, the sanitizer strips it from the name at ingestion and guarantees the reference survives as a real Park4Night URL in the notes (the enrichment flow's source-hint detection scans name and notes, so classification and linking keep working); (2) the stop card now displays a cleaned name for existing roadbook entries too - without mutating the roadbook - and shows a "Park4Night #506374" button that opens the place's page directly, next to Google Maps.
- The tool-tabs tray (Entscheidungen, Dokumente & Kosten, Stellplätze, Gesamtroute, ...) forced itself fully open whenever any of those tabs was active, pushing the actual tab content far down the screen - a real problem on mobile once a tool tab like Stellplätze saw daily use. It now behaves like a normal collapsible menu: closed by default, opened with a tap, and closes again once you pick a tab.

### Changed

- Reworked the Stellplätze tab based on live usage feedback: it now defaults to the current/upcoming travel day (with a dropdown to jump to any other day) instead of stacking every day's card one after another; each day shows a route-context line (where today starts, tonight's stop, tomorrow's first stop) and a small map plotting that context plus every backup option's location; and an option's pros/cons now stand out as their own green/red chips instead of being buried in one plain-text line.

## [4.10.0] - 2026-07-29

### Added

- Stellplatz-Optionen: every travel day can now hold up to six persistent overnight options in the roadbook itself (stored day-anchored in `day.details.overnight_plan` - no migration, older roadbooks are untouched). A new "Stellplätze" tool tab manages them: per-day cards with the active place and its backups, add/edit/reject/restore/delete, a per-day strategy selector (route-optimal, best-first, early-arrival - stored now, ranking logic follows in a later phase), and an editable trip-level pitch-preferences card (must-have features, weighted nice-to-haves, price/detour limits, vehicle size, free text for the assistant). Activating an option ("Plan B") is one atomic commit: the chosen option becomes the day's overnight stop (materialized if the day had none), the previous place is automatically demoted into the options list as a backup - so "Platz voll → Plan B → doch wieder Plan A" never loses a candidate - and the confirmation dialog warns when photos or documents are linked to the old stop, since they may belong to the previous physical place. The "Heute" tab shows a one-tap "Plan B aktivieren" card with the best backup, and the day route is recalculated automatically after a switch.

## [4.9.1] - 2026-07-29

### Fixed

- Starting a trip video export blocked every other panel action - including the assistant chat - for as long as the render ran (up to several minutes), because the panel's single global "busy" flag was held for the whole in-flight WebSocket call and every other action refused to start while it was set. The video export now runs without holding that flag, so the assistant (and everything else in the panel) stays usable while a video renders in the background; the export button still shows its own "Erstelle Video..." progress state so it's clear the render is underway.

## [4.9.0] - 2026-07-28

### Changed

- A finished trip video is no longer served through a short-lived, in-memory download ticket - it's written to a small durable library on disk (new "Trip video library folder" option, `.roadplanner_trip_videos` by default, oldest files pruned beyond 10 kept) and announced with a Home Assistant persistent notification containing the download link. A render can take minutes, and the app may well be closed by the time it's ready; the previous ticket was tied to that one WebSocket response and would be silently lost if the connection was gone when the export finished - the video is now safe on disk and the link keeps working (with normal Home Assistant login) whenever you come back to check.

## [4.8.1] - 2026-07-28

### Fixed

- Roadplanner could fail to load entirely right after updating to 4.8.0 ("Setup failed for custom integration 'roadplanner_mcp': Requirements for roadplanner_mcp not found: ['Pillow>=10,<12']"), because the new trip-video feature declared its own narrow `Pillow` version range in `manifest.json` - which conflicts on any host where Home Assistant Core itself already has a newer Pillow installed (as most current Core versions do), since Home Assistant won't downgrade a package shared with Core to satisfy one integration's separate pin. `reportlab` (already a Roadplanner dependency) pulls in a working Pillow on its own, so the trip-video feature now simply relies on that instead of redeclaring its own version constraint.

## [4.8.0] - 2026-07-28

### Added

- A new "Reise als Video" button next to the PDF export renders the trip as a downloadable MP4 slideshow: real personal-photo-first, stock-photo-fallback chapters (one per day), an optional map-snapshot chapter opener showing where that day's stops were (OpenStreetMap, tile-stitched with attribution, or Google Static Maps - configurable in Roadplanner's options, reusing the existing Google Places API key), crossfade transitions, and a short Gemini-written narrative per day, grounded strictly in that day's real stops/date/distance (the model is explicitly instructed never to invent details). Choose between a short highlight reel (top days only, ~2-3 min) or a full day-by-day recap at export time. Background music is supported but ships without any tracks yet - `assets/music/README.md` documents adding real royalty-free files as a manual follow-up; a missing/empty music folder simply produces a silent video. Rendering runs `ffmpeg` as an isolated subprocess (never blocking Home Assistant's shared executor pool) and needs an `ffmpeg` binary on the host - the button is disabled with an explanation if none is found. Requires the new `Pillow` dependency.

### Fixed

- The trip-summary PDF's cover title always fell back to the generic "Roadplanner-Reise", and the crew/vehicle page never rendered at all, because `trip_pdf_export.py` read the trip dict from a top-level `payload["trip"]` key that does not exist in the assistant payload - the real trip data (title, dates, travelers, vehicle) lives at `payload["summary"]["trip"]`. The export now reads from the correct location, and the previously untested `async_generate` data-gathering path (title/crew/vehicle) now has a real regression test.

## [4.7.0] - 2026-07-28

### Fixed

- Exporting the trip-summary PDF could fail outright with "Die Roadplanner-Aktion ist unerwartet fehlgeschlagen" if any single day photo had been downloaded incompletely (e.g. an interrupted OneDrive fetch). Reportlab only decodes a photo's actual pixel data inside `drawImage()`, well after the existing corrupt-photo guard's `ImageReader.getSize()` call already succeeded from just the file header - so a photo whose header was intact but body was truncated slipped past that guard and crashed the whole export instead of just that one day's one photo.
- A trip's automatically-picked cover photo (shown at the top of the "Reise" tab, and used as a Vision-curation candidate) could land on a photo taken right after leaving home, at a fuel stop, or at a border crossing, instead of anything actually representative of the trip - because both the automatic personal-photo candidate pool and the destination-gallery planning fallback picked whichever confirmed stop came first chronologically, with no regard for whether that stop was an actual destination or just logistics. Photos linked to a purely logistical stop type (waypoint, start/origin, parking, charging, fuel, service, water, waste, laundry, border, break) no longer compete for the automatic trip cover; day covers and an explicit personal trip-cover choice are unaffected.

### Changed

- A day page in the trip-summary PDF with no real, usable photo (none linked, or the only one available turned out to be corrupt/truncated) no longer shows a generic drawn camera-icon filler in its place - a personal trip retrospective shouldn't look assembled with placeholders. The photo area is simply left out for that day, and a day with one real photo now gets one full-width tile instead of one real photo plus one icon filler alongside it.

## [4.6.0] - 2026-07-28

### Added

- A "Reisezusammenfassung als PDF" button on the Gesamtroute ("Reise") tab now exports the whole trip as a downloadable PDF: a cover page, a crew page (the trip's confirmed travelers/vehicle snapshot, with a person/dog/camper icon per member), a schematic route overview, one page per day with its stops and up to two real photos, and a closing stats page (days/km/countries/stops). A stop's own personal photo (OneDrive-synced or manually uploaded, its "Erinnerungen" cover image if set) is always used first; the stock destination-gallery image (Wikimedia Commons/Openverse) is only a fallback for a stop with no personal photo of its own, and a drawn placeholder is the last resort. Google Places photos (which resolve to a browser-session-signed redirect, not a plain fetchable URL) are deliberately skipped for this server-side export either way. Rendering uses the new `reportlab` dependency; the generated PDF is served through a short-lived, few-use download ticket, never stored on disk.

## [4.5.7] - 2026-07-28

### Fixed

- The assistant chat, its review preparation, the connection test, and the daily briefing could all fail outright with "Assistent konnte nicht antworten - Connection lost" whenever the app was backgrounded (or the network briefly dropped) while one of these AI-provider calls - which can take a minute or more - was still in flight. Home Assistant cancels whichever task is awaiting a WebSocket connection that just closed; previously that was the very task running the provider call, so backgrounding the app didn't just lose the reply, it aborted the request entirely and discarded whatever the model had already produced. These four actions now run in a detached, shielded task that keeps going to completion regardless of the connection's fate - the chat's existing "war die letzte Nachricht doch beantwortet?" self-heal check then finds the real answer on the next reload instead of the conversation staying stuck forever.
- A stop with its own specific business name (e.g. "Minimani Rovaniemi") could still get resolved to a completely different, unrelated business at the same shared street address (e.g. a retail park with several tenants), because destination classification treated any parsed street/house number as a pure address lookup and searched only the bare address text - never the name - as soon as one was available. An address-only search at a shared address resolves ambiguously to whichever business a provider associates most strongly with that raw address, silently picking the wrong one even though the stop's own name would have found the right business unambiguously. A specific name is now always preferred for the search unless it's just the address written out with no distinguishing word of its own (e.g. "Krumhermsdorf Neuhäuser 40").

## [4.5.6] - 2026-07-28

### Fixed

- A new stop compiled from a link the user gave (Google Maps or otherwise) could end up with the link only inside `changes.notes`, never in the dedicated `place_query` field the prompt asks for - even though the model correctly extracted it. Since automatic geocoding enrichment only ever inspects `place_query`, that stop's enrichment never even attempted to run, silently leaving it on "Ort fehlt" until a manual "Stopp anreichern" - instead of getting resolved right away as part of the same change, the way a new stop is supposed to. A stop operation with no `place_query` now has its notes and reason text scanned for a link, lifting the first one found into `place_query` (kept in notes too, as human-readable context) so the normal geocoding path still gets a chance to run automatically.

## [4.5.5] - 2026-07-28

### Fixed

- Preparing a pending change could fail outright with "Nicht erlaubte Felder für stop: text" when Gemini's JSON-mode fallback put descriptive content (e.g. facts extracted from a resolved booking link) under `changes.text` on a stop/day/trip update - a field that only ever belongs to `entity_type=preference`. The whole ChangeSet was rejected instead of just the misplaced field, discarding genuinely useful content the model had correctly extracted. A stray `changes.text` on any non-preference entity is now salvaged into `changes.notes` (appended if notes already has content) before the strict per-entity field check runs, the same salvage-not-crash approach used for the earlier `changes.location` fix.

## [4.5.4] - 2026-07-27

### Fixed

- The assistant's compile/basket schema could get rejected by Gemini with a generic HTTP 400 ("Request contains an invalid argument.", no further detail) across several current models (confirmed live for `gemini-3.6-flash`, `gemini-3.5-flash`, and `gemini-3.5-flash-lite`), forcing every call all the way back to the unconstrained plain-JSON fallback - which can never include the `google_search`/`url_context` tools, so a booking-link stop update could never actually get fetched. Google's own guidance for this exact generic error attributes it to schema "complexity" (many properties combined with numeric/length constraints), which a bare 400 doesn't otherwise identify. `GeminiClient` already stripped `maxLength`/`minLength`/`pattern` for this reason; it now also strips `minimum`/`maximum`/`minItems`/`maxItems` from the schema sent to Gemini. None of these are needed for correctness - every value is still fully re-validated server-side regardless of what schema Gemini used during generation.

## [4.5.3] - 2026-07-27

### Fixed

- When Gemini rejects a request shape with HTTP 400 (an "invalid argument" response) and `GeminiClient` falls back to a more compatible shape, the actual provider error text was never logged anywhere - only aggregate counters (`compatibility_fallback_count`) were visible in diagnostics, even once the call ultimately succeeded via a less capable fallback (e.g. losing `google_search`/`url_context` tool access). This made a live report of persistent schema rejections across several current models impossible to diagnose without adding a log line first. Every HTTP 400 compatibility fallback now emits a debug-level log with the request mode, model, and Gemini's own error detail.

## [4.5.2] - 2026-07-27

### Fixed

- A booking-link stop update could still silently skip Gemini's `google_search`/`url_context` tools even with research correctly requested, if the same model had already answered an earlier, ordinary (non-search) call by falling back to its schema-less plain-JSON request shape. `GeminiClient` memoizes the last request shape that worked per model to skip straight to it next time, but that cache was keyed only by model name - so once a model's plain-JSON fallback got cached (very likely, since it happens whenever the primary model times out or errors and the client retries with a fallback model), every later call to that model reused it first, including ones that explicitly needed the search/`url_context` tools to fetch a booking link. The cache is now keyed by `(model, whether search was requested)`, so a plain call's cached shape can never keep a search-requiring call from actually attempting to search.

## [4.5.1] - 2026-07-27

### Fixed

- Pasting a booking link for an *existing* stop's overnight update (for example "Hier schlafen wir heute und morgen: https://booking.com/...") could get accepted into the change basket but then fail when preparing the review ("Als Entscheidungsvorlage"), with a cryptic `stops[N].location muss ein JSON-Objekt sein` error. Root cause: the assistant compile system prompt incorrectly listed `location` as a settable `changes` field for stops, even though only the server-side geocoding plugin may ever populate it (from a confirmed `place_query`); Gemini's plain-JSON fallback (more likely whenever the `url_context`/search tools are used) ignores the response schema and, following that prompt text, could put a raw place name or hand-built object straight into `changes.location`, which then reached the ChangeSet untouched and failed deep inside validation. `changes.location` is now always stripped before the operation reaches the ChangeSet - any text content it held is salvaged into `place_query` (unless one is already set) so the normal geocoding path still gets a chance to resolve it - and the prompt no longer tells the model `location` is an allowed field.

## [4.5.0] - 2026-07-27

### Added

- A pending change ("Übergabe") whose base revision has gone stale - because another change was applied first - can now be "neu aufgesetzt" (rebased): re-validated against the trip's current state and, if it still applies cleanly, re-stamped onto the current revision so it can be reviewed and applied normally. Previously the only option for a stale change was to reject it and redo the underlying request from scratch. If a referenced day or stop no longer exists (or anything else about the change is no longer applicable), rebasing fails with a clear error and the pending change is left completely untouched - there is no partial/best-effort rebase.

### Changed

- Every panel load fetched the trip/day payload, then travel-archive data, then experience data, then crew data, strictly one after another - four sequential round trips on every single click (add/update/remove a stop, apply a change, anything that triggers a refresh), even though most of them don't depend on each other. Independent subsystems (crew alongside the main payload; travel-archive alongside experience once the selected trip is known) now fetch concurrently instead.

### Fixed

- Updating an *existing* stop (as opposed to adding a new one) never enabled Gemini's search/`url_context` tools, even without a resolved `place_query` - only `add` did. A pasted booking link on a stop the chat step had already matched to a prior placeholder therefore got no fetch at all: the model had to guess a name from conversation context alone, and no location was ever resolved. Any stop `add` or `update` without a `place_query` yet, or that mentions a non-Google-Maps link anywhere in its basket text, now enables research the same way.
- Updating a stop, day, or trip's `details` (nested planning metadata - geocoding results, transport/ferry info, source attributions from a resolved booking link, etc.) silently discarded whatever wasn't part of that particular update's patch, since it was a wholesale dict replacement rather than a merge. An update meant only to change e.g. an arrival time, but that happened to also touch `details` for an unrelated reason, would wipe out previously stored `details` sub-keys with no error or warning. `details` is now merged one level deep on update; every other field still overwrites as before.
- A compiled stop `add` could get silently misattributed as last night's overnight and converted into an `update` of a completely unrelated, already-existing overnight stop - overwriting its name/notes - whenever the change basket happened to hold exactly one differently-themed stop item mentioning a past-overnight phrase ("gestern Nacht hier übernachtet") and the new operation itself had no `place_query`/name to match against. The lone-basket-item fallback that caused this is still used (as before) for the lower-stakes task of inferring which *day* an operation with a missing `day_id` belongs to, but no longer feeds the decision to silently rewrite an existing stop - that now requires the operation's own text, or an actual basket match by `place_query`/name.
- The Übergabe-Vorschau (handoff preview) dialog now actually shows what a pending change will do before you click "Übernehmen". `execute_changeset` previously only recorded bare metadata (index/op/day_id/stop_id/position) per operation result, never the requested patch or new entity content, even though the preview dialog already renders each result verbatim - so an `update_stop`/`update_day`/`update_trip`/`update_preference` preview showed no patch at all, and an `add_stop`/`add_day`/`add_preference` preview showed no content for the new entity. `remove_*` operations are unchanged, since there is nothing beyond the already-shown ID to preview.

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
