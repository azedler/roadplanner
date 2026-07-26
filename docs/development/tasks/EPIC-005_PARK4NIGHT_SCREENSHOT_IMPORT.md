# EPIC-005 — Park4Night screenshot import

## Goal

Let the operator hand Roadplanner a screenshot (or several) of a Park4Night stop and get a pre-filled durable place profile back, without Roadplanner ever fetching Park4Night itself.

## User outcome

- The operator opens the stop in their own Park4Night app/browser, takes one or more screenshots, and uploads them to Roadplanner.
- Roadplanner extracts name, GPS coordinates, description, amenities and any visible photos from the screenshot content and proposes a review card, the same way Google Places discovery already does.
- The operator confirms or edits before anything becomes a durable place profile. Nothing is written automatically.
- Park4Night remains labeled as the source (place ID/link) for provenance, consistent with existing source-hint handling.

## Why screenshots, not a live fetch

Automated requests to `park4night.com` are blocked (confirmed via a direct `robots.txt`/terms fetch returning HTTP 403), and Roadplanner's stated privacy policy is that it does not scrape Park4Night or other third-party pages. Repository visibility has no bearing on this. Screenshot import keeps data acquisition in the operator's hands — same as manually copying data today — while automating the tedious part (typing GPS/name/description into Roadplanner).

## Processing contract

```text
Operator screenshot(s)
→ upload to Roadplanner
→ Vision extraction (name, GPS, description, amenities, embedded photo regions)
→ strict field validation (coordinate bounds, no free-form code execution on extracted text)
→ review card (same UX as Google Places discovery)
→ operator confirms/edits
→ durable place profile with Park4Night source reference
```

## Safety boundaries

- Roadplanner never requests Park4Night URLs itself; the only Park4Night byte range it processes is what the operator uploaded.
- Extraction failures fall back to an empty/partial review card the operator fills in manually — never a hard error that blocks stop creation.
- Uploaded screenshots are treated like other transient discovery images: not retained beyond what's needed to build the review card, unless the operator explicitly keeps one as a place photo.
- No OCR/Vision output is trusted for anything beyond populating editable review-card fields; the operator's confirmation remains the write gate.

## Open questions

- Multi-screenshot stitching: does one stop ever need more than one screenshot (e.g. overview + photo gallery), and if so how are they correlated in one upload flow?
- Where does the upload entry point live in the panel (new stop creation vs. existing source-hint flow)?
- Which Vision provider handles extraction — same Gemini path as EPIC-004, or a dedicated prompt/schema?

## Status

Planned, not yet scoped into an RP-XXX implementation task.
