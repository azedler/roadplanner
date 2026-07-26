# External services and privacy

Roadplanner is self-hosted in Home Assistant, but optional features can send bounded data to external services. The Home Assistant operator decides which providers are enabled and is responsible for the applicable provider terms, privacy notices, budgets and credentials.

## OpenStreetMap and Nominatim

Destination enrichment can send a short place or address query and optional coordinates to the configured Nominatim endpoint. Returned OpenStreetMap provenance and attribution remain attached to the durable place profile.

## Google Places

Google Places is disabled until a Google Maps Platform API key is entered in Roadplanner options. When enabled, Roadplanner can send:

- the bounded destination search text;
- language and country hint;
- an optional approximate location bias from an existing stop coordinate;
- a target-type hint such as campground, restaurant or ferry terminal.

Roadplanner does not send trip notes, documents, personal photographs, user names or the Google API key to the panel. The API key is used only by the Home Assistant backend.

The review card temporarily displays Google name, address, type and coordinates with visible `Google Maps` attribution. On confirmation, Roadplanner keeps the Google Place ID as a source reference and creates the durable profile from OpenStreetMap/Nominatim. Google photos, reviews and ratings are not requested in this release.

Google states that search terms, IP addresses and coordinates may be collected according to its privacy policy. Applications using Google Maps features must make the applicable Google terms and privacy information available to their users. Operators with an EEA billing address must review the EEA-specific terms and permitted uses.

Official references:

- [Google Maps End User Additional Terms](https://maps.google.com/help/terms_maps/)
- [Google Privacy Policy](https://policies.google.com/privacy)
- [Places API policies and attribution](https://developers.google.com/maps/documentation/places/web-service/policies)
- [Google Maps Platform EEA Terms](https://cloud.google.com/terms/maps-platform/eea)
- [EEA Places API permitted uses](https://cloud.google.com/terms/maps-platform/eea-places-api-permitted-uses)

## Gemini and optional Vision curation

Local deterministic media filtering runs first. Hybrid Vision sends only a bounded set of reduced thumbnails and opaque image IDs to the configured assistant provider. Original provider files are not modified or deleted, and Vision is not asked to identify people or infer sensitive traits.

## Wikimedia Commons and Openverse

Planning-image searches send a short destination query and optional coordinates. Selected media keeps source, author, license and attribution metadata.

## Park4Night and other source links

Roadplanner recognizes source IDs and links and can open them for manual review. It does not scrape Park4Night, Google Maps or other third-party pages.

When the assistant compiles a new or updated stop and the operator's message contains a Google Maps link, Roadplanner reads only that link's own URL structure (a coordinate pair or a place name already encoded in the address) - it never fetches or parses the Google Maps page itself. If the link is a short link (`goo.gl`/`maps.app.goo.gl`), Roadplanner follows the HTTP redirect to read the resulting Google URL, again without downloading any page content. The resulting coordinate or name is then resolved exactly like any other place through the normal GPS-Prüfung/Google Places search and operator confirmation - never trusted directly.

## Context exports

`tools/dev.py context-export` excludes known secret files, Home Assistant storage, Roadbooks, documents, travel media, backups, patches and binary archives. The operator must still inspect the ZIP before sharing it with any external AI or reviewer.
