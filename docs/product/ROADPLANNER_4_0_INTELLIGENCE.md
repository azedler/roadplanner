# Roadplanner 4.0 – Destination and Media Intelligence

Roadplanner 4.0 turns a loosely described stop into a reviewed destination profile and uses that profile consistently for maps, road routing, navigation and media.

## Destination intelligence

`Stopps anreichern` first classifies the stop as an address, place, ferry or transport terminal, hike, nature centre, attraction, shop, restaurant, campsite, accommodation, parking, fuel or charging location. It then creates a bounded query plan instead of sending notes or an entire day description to one generic geocoder.

OpenStreetMap remains the default and durable source. Google Places can be enabled during Roadplanner setup and used as fallback or preferred discovery provider. Every candidate stays review-only.

Recognized source hints include Park4Night IDs and links, OpenStreetMap, Wikidata, Wikipedia and Google Maps. These hints improve discovery and provide direct source links; they are never treated as verified coordinates by themselves.

## Target and road access

A remote beach, dune, trail or nature destination can have:

- an actual target point for the map, destination identity and media;
- a derived drivable access point for road routing.

The route no longer drops an unreachable target. The UI explains that the road route ends at the nearest access and the original marker remains visible.

## Manual control

- Stops can be reordered without changing schedule times.
- Editable stops can be deleted after an explicit confirmation.
- Deleting a stop does not delete linked photos or documents.
- Manual coordinates remain available when no provider supplies a trustworthy persistent profile.
- Every write still passes through Roadplanner's ChangeSet review boundary.

## Media intelligence

Roadplanner distinguishes personal travel photos, planning images and external-provider failures.

- Existing personal photos suppress the misleading generic error “no images”.
- External-image failures are reported separately.
- Image queries use destination name, city, country and type; long notes and day descriptions are excluded.
- Trip, day and stop covers are independent.
- A manual cover always wins.
- A personal photo assigned only by date is not eligible as an automatic trip cover.
- A confirmed planning image is the safe automatic fallback when no suitable personal trip cover exists.
- Optional Gemini Vision receives only a locally prefiltered, bounded candidate set and may select only existing Roadplanner image IDs.

The result is a predictable policy: the travel story can become more personal after a visit without letting an unrelated but technically large photo become the trip hero.

## Provider transparency

Candidate cards show their source. Google content carries visible `Google Maps` attribution. An expandable explanation describes how provider ordering, name match, destination type, address, country, location bias and POI specificity influence Roadplanner's final candidate order.

Google is used as a transient discovery source. The durable Roadbook profile is normalized through OpenStreetMap and retains only the Google Place ID as a source reference.

## Developer workflow

The repository helper supports two additional reproducible workflows:

```bash
python tools/dev.py apply-series patch-files/RP-A.patch patch-files/RP-B.patch
python tools/dev.py context-export dist/roadplanner-ai-context.zip
```

`apply-series` validates all patches in an isolated worktree before changing the real worktree. `context-export` creates a secret- and personal-data-filtered repository snapshot with Git metadata and optional check evidence for an AI implementation or independent review.
