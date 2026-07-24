# ADR-012: Provider-neutral destination profiles and transient Google discovery

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

Roadplanner needs more reliable destination discovery for named places such as restaurants, shops, ferry terminals, campsites and attractions. OpenStreetMap/Nominatim remains the open default but does not contain every place that users can find in Google Maps. At the same time, Google Places content has attribution, storage and use restrictions that are different from OpenStreetMap data.

A destination may also have two meaningful coordinates: the actual place that should appear on the map and in media matching, and a nearby drivable access point that should be used for road routing.

## Decision

Roadplanner uses one provider-neutral `place_profile` schema version 2. The profile owns the durable destination identity used by maps, routing, media, navigation and assistant context.

Every place provider implements the same bounded search contract and returns reviewable candidates. OpenStreetMap/Nominatim remains the durable geodata source and default provider. Google Places (New) is an optional server-side discovery provider that can run as fallback or preferred search source.

Google candidates are transient:

- Roadplanner displays the candidate with visible `Google Maps` attribution.
- The user must explicitly select the candidate before a ChangeSet is created.
- Roadplanner stores only the Google Place ID and a generated Google Maps source link as a discovery reference.
- The Google display name, formatted address, category and coordinates are not copied into the persistent Roadbook profile.
- On selection, the candidate coordinate is reverse-resolved through Nominatim. The durable name remains the user's stop name; the durable address, map point and provider provenance come from OpenStreetMap.
- If no durable Nominatim result can be produced, Roadplanner refuses silent persistence and offers the existing manual confirmation path.
- Google photos, reviews, ratings and other atmosphere fields are not requested or stored.

Google result ranking is explained in the review UI. Google first orders its own search results; Roadplanner then applies provider-neutral scoring and safety checks for name, target type, address, country, location bias and concrete-POI specificity.

The destination profile may additionally contain a separate `navigation` access point. The original stop coordinate remains the actual target. A derived access point is used only for routing and is identified as derived and unconfirmed unless the user confirms it.

## Consequences

- Roadplanner can find places that are missing or weakly represented in Nominatim without making Google the canonical Roadbook database.
- Existing installations remain functional without a Google account or API key.
- API keys remain server-side and never enter the panel payload or repository.
- Provider attribution and source references stay visible and auditable.
- Google discovery may fail to become persistent when OpenStreetMap cannot provide a durable reverse result; the user can still confirm a manual map point.
- Media lookup continues to use a durable reviewed place profile rather than transient provider content.
- Target markers no longer disappear merely because a road router cannot drive to the exact coordinate.

## Rejected alternatives

- Persisting complete Google names, addresses, categories, photos or coordinates indefinitely in the Roadbook.
- Making Google Places mandatory for all installations.
- Sending the API key to the browser.
- Scraping Google Maps or Park4Night pages.
- Replacing the actual destination coordinate with an arbitrary road snap.
- Letting an external provider mutate a stop without the existing ChangeSet review boundary.

## References

- [Google Places Text Search (New)](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [Places API policies and attribution](https://developers.google.com/maps/documentation/places/web-service/policies)
- [Google Maps Platform API security guidance](https://developers.google.com/maps/api-security-best-practices)
- [Google Maps Platform EEA Terms](https://cloud.google.com/terms/maps-platform/eea)
- [EEA Places API permitted uses](https://cloud.google.com/terms/maps-platform/eea-places-api-permitted-uses)
