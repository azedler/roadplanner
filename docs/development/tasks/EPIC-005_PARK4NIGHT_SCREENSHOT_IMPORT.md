# EPIC-005 — Park4Night screenshot import

## Goal

Let the operator hand Roadplanner a screenshot of a Park4Night (or similar overnight-stay portal) stop and get a pre-filled, verified stop back, without Roadplanner ever fetching Park4Night itself.

## Superseded by reusing Universal Import

The original plan below assumed a dedicated screenshot-import pipeline (its own preview cache, review card, confirm path). Investigation found that `UniversalImportManager` (`universal_import_manager.py`) already provides exactly that pipeline for any uploaded file, including images: upload → Gemini Vision/text analysis → editable preview (`preview_items`/`basket_delta`) → operator confirms via `universal_import_transfer` → change lands in the assistant basket or a review changeset. The paperclip attach button and clipboard paste (Ctrl+V) in the "Reisebegleiter" chat already route into this same flow via the "Als Reiseplan oder Übergabe" attachment purpose.

So the actual implementation was a **prompt change only**, in `_IMPORT_SYSTEM_PROMPT`:

- Recognize a Park4Night-style overnight-stay listing screenshot.
- If decimal GPS coordinates are visible on screen, copy them verbatim into `place_query` as `"latitude,longitude"`. This still goes through the existing, mandatory GPS-Prüfung (`GeocodingProvider.async_resolve` in `geocoding.py` already reverse-geocodes a coordinate-pair query instead of text-searching it) — coordinates are never trusted directly, only used as a reverse-geocoding input that still produces a confirmable candidate.
- If no coordinates are visible, fall back to name/address as `place_query` (existing behavior).
- Copy the visible name, amenities/description, and any visible portal ID/URL verbatim into the stop's `notes`, so the existing Park4Night ID recognition (`destination_intelligence.py`, since 4.0.1) can tag provenance downstream.
- Never invent coordinates, IDs, or addresses that aren't visible.

No new upload path, preview cache, service, or frontend component was needed.

## Why screenshots, not a live fetch

Automated requests to `park4night.com` are blocked (confirmed via a direct `robots.txt`/terms fetch returning HTTP 403), and Roadplanner's stated privacy policy is that it does not scrape Park4Night or other third-party pages. Repository visibility has no bearing on this. Screenshot import keeps data acquisition in the operator's hands — same as manually copying data today — while automating the tedious part.

## Safety boundaries

- Roadplanner never requests Park4Night URLs itself; the only Park4Night byte range it processes is what the operator uploaded.
- Coordinates are read verbatim from the screenshot only as a *reverse-geocoding query*, never written directly as a durable location; the normal GPS-Prüfung and operator review/confirm step are unchanged.
- The operator's confirmation via `universal_import_transfer` remains the write gate, same as every other import.

## Open questions

- Multi-screenshot stitching for one stop (overview + photo gallery) is not handled specially; each upload is analyzed independently.
- Amenity/photo extraction quality depends entirely on prompt tuning and hasn't been evaluated against real Park4Night screenshots yet.

## Status

Implemented (prompt-tuning only) — extraction quality against real screenshots not yet field-verified.
