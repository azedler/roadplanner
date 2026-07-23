# EPIC-003 — Complete Places & Smart Media

## Goal

Replace the GPS-only repair workflow with a complete, reviewable place profile and make media presentation automatically transition from representative internet images to a curated best-of selection of personal OneDrive photos after a visit.

## User outcome

- A user reviews the actual terminal, campsite, attraction, pharmacy or restaurant — not just a latitude/longitude pair.
- A selected candidate supplies concrete values to the normal Roadplanner review workflow.
- Before a visit, the stop is visually prepared with representative, attributed planning images.
- After a visit, the stop and day automatically prefer the strongest personal travel memories.

## Safety boundaries

- No place candidate is applied without explicit selection.
- Existing stop and day IDs are preserved.
- Server-controlled trip ID, revision and ChangeSet metadata remain authoritative.
- Provider metadata and public image references are stored; external image binaries are not copied into the Roadbook.
- Gemini does not reinterpret already selected place coordinates/details.
- Conflicting or missing candidates remain unresolved instead of being guessed.

## Acceptance criteria

1. An incomplete stop opens a candidate dialog with name, address, coordinates, category, source, confidence and available public contact/opening fields.
2. Each candidate can show up to three representative planning images with attribution metadata.
3. Only explicitly selected candidates are transferred to a review-only ChangeSet.
4. The ChangeSet contains concrete `location` and `details.place_profile` values.
5. Coordinate-only stops remain routable but are visible as unreviewed places.
6. Planning-image selection penalizes logos/maps/posters and avoids near-identical results.
7. Personal OneDrive photos are deduplicated, burst-reduced and selected from diverse moments.
8. Personal travel photos take precedence in stop/day presentation; planning images remain available.
9. Provider failures are isolated and never block the stop or another provider.
10. Mobile dialogs remain readable and touch-operable.
