# Google Places setup

Google Places is optional but fully supported as a Roadplanner destination-search provider. OpenStreetMap/Nominatim remains available without Google. Google can be configured as a fallback or as the preferred first search provider.

## What Roadplanner uses

Roadplanner uses **Places API (New) – Text Search** for reviewed destination candidates such as named campsites, restaurants, shops, terminals and attractions.

The integration requests a deliberately small field mask containing:

- Place ID;
- display name and formatted address for the temporary review card;
- location;
- address components;
- primary type and types;
- Google Maps source link;
- business status.

Roadplanner does not request Google photos, ratings, reviews, opening hours or atmosphere fields in this release. Field masks control both returned data and billing class, so widening the mask requires a separate review.

## Google Cloud preparation

1. Create or select a Google Cloud project.
2. Attach a billing account.
3. Enable **Places API (New)**.
4. Create a dedicated API key for Roadplanner.
5. Restrict the key to **Places API (New)** only.
6. For a Home Assistant server with a stable public egress address, add an IP-address application restriction. If the address is dynamic, use the strongest practical network restriction and monitor usage closely.
7. Configure Google Cloud budgets, alerts and quota limits. The Roadplanner daily limit is only an additional in-process guard and resets when Home Assistant restarts.

Google's current guidance recommends both API restrictions and an application restriction. Unrestricted keys can be abused and the project owner remains responsible for the charges.

Official references:

- [Set up the Places API](https://developers.google.com/maps/documentation/places/web-service/cloud-setup)
- [Text Search (New)](https://developers.google.com/maps/documentation/places/web-service/text-search)
- [API security best practices](https://developers.google.com/maps/api-security-best-practices)
- [Pricing and billing](https://developers.google.com/maps/billing-and-pricing/billing-overview)

## Enable Google in Home Assistant

After installing the Roadplanner release:

1. Open **Settings → Devices & services**.
2. Open **Roadplanner**.
3. Choose **Configure**.
4. Enable **Google Places for destination search**.
5. Paste the API key into **Google Places API key**.
6. Select the provider priority:
   - `fallback`: OpenStreetMap first; Google is called only when no safe concrete OSM result exists.
   - `preferred`: Google first; OpenStreetMap remains the fallback and durable normalization source.
7. Set the Roadplanner daily request limit. `50` is a conservative starting point for a private installation; `0` disables Google calls.
8. Save the options and reload Roadplanner or restart Home Assistant.

The API key field is intentionally blank whenever the options form is reopened. Leaving it blank preserves the stored key; entering a new value replaces it.

## Runtime behavior

- The key is used only by the Home Assistant backend and is never included in panel data, diagnostics, context-export packages or logs.
- Identical searches may be reused for up to five minutes from an in-memory cache.
- A candidate card visibly shows `Google Maps` attribution.
- The review UI explains the ranking factors.
- After confirmation Roadplanner stores the Place ID as a Google discovery reference.
- The durable Roadbook profile is normalized through OpenStreetMap/Nominatim. If that is not possible, Roadplanner asks for manual confirmation instead of persisting transient Google content.
- No Google Place Photos are used in this release.

## Validation checklist

Use a place that is weak or absent in Nominatim, for example a named restaurant or campsite:

1. Open **Stopps anreichern**.
2. Confirm that the provider status says `Google Places als Fallback` or `Google Places bevorzugt`.
3. Confirm that a Google result carries visible `Google Maps` attribution and a Google Maps source link.
4. Select the candidate and create the ChangeSet.
5. In the change review, verify that the stop keeps its user-defined name.
6. After applying, verify that `place_profile.provider` is `nominatim` and `source_references` contains only the Google Place ID reference.
7. Check Google Cloud metrics and billing after the first request.

## Troubleshooting

### Not configured

No API key is stored. Reopen Roadplanner options and enter the key.

### Key or permission rejected

Verify that Places API (New) is enabled, billing is active, the key is allowed to call that API, and the application/IP restriction matches the Home Assistant server's observed public address.

### Daily limit reached

Increase the Roadplanner limit deliberately or wait until the next local calendar day. A Home Assistant restart resets this local counter, so use Google Cloud quotas and budgets as the authoritative cost protection.

### Google finds the place but selection fails

The transient Google coordinate could not be converted to a durable OpenStreetMap profile. Open the Google source to verify the intended place and use the manual coordinate confirmation if necessary.

## Terms and privacy

The operator must review the terms that apply to the Google Cloud billing address, including EEA-specific terms where applicable. Roadplanner sends the bounded destination query, optional location bias, language and country hint to Google. See [External services and privacy](../product/EXTERNAL_SERVICES_AND_PRIVACY.md).
