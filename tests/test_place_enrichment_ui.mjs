import fs from "node:fs";

const source =
  fs.readFileSync(
    "custom_components/roadplanner_mcp/frontend/roadplanner-panel.js",
    "utf8",
  ) +
  fs.readFileSync(
    "custom_components/roadplanner_mcp/frontend/lib/styles.js",
    "utf8",
  ) +
  fs.readFileSync(
    "custom_components/roadplanner_mcp/frontend/features/place-enrichment.js",
    "utf8",
  ) +
  fs.readFileSync(
    "custom_components/roadplanner_mcp/frontend/features/media.js",
    "utf8",
  ) +
  fs.readFileSync(
    "custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js",
    "utf8",
  );

for (const required of [
  '"prepare_place_enrichment"',
  '"submit_place_enrichment"',
  'data-action="complete-stop-place"',
  'data-action="place-enrichment-select"',
  'data-action="place-enrichment-submit"',
  'Ortsprofil vervollständigen',
  'Geodaten zuerst, Bilder danach',
  'Stopps anreichern',
  'Zieltyp:',
  'Google Maps',
  'Google dient als Suchquelle.',
  'Wie werden die Treffer sortiert?',
  'Google wird je nach Einrichtung bevorzugt oder nur als Fallback aufgerufen.',
  'OpenStreetMap',
  'Öffnungszeiten',
  'Vertrauen',
  'use_ai_cleanup',
  'manual_entries',
  'cleanup_confirmations',
  'data-action="place-enrichment-ai-retry"',
  'data-action="place-manual-select"',
  'data-action="place-manual-check-map"',
  'In Google Maps prüfen',
  'data-action="place-cleanup-toggle"',
  '__manual__',
  'Die Zuordnung eines Reisetags war nicht eindeutig.',
  'technicalMessage',
]) {
  if (!source.includes(required)) {
    throw new Error(`Missing place-enrichment UI contract: ${required}`);
  }
}

if (!source.includes('type: "place-enrichment"')) {
  throw new Error("Place-enrichment dialog is not opened by the panel");
}
if (!source.includes('this._dialog?.type !== "place-enrichment"')) {
  throw new Error("Place-enrichment selection is not scoped to its dialog");
}
if (!source.includes('class="google-maps-label" translate="no">Google Maps</span>')) {
  throw new Error("Google candidate attribution must be visible and excluded from translation");
}
if (!source.includes('font-family: Roboto, Sans-Serif') || !source.includes('font-weight: 400')) {
  throw new Error("Google text attribution styling contract is missing");
}
// The submit button names what it DOES. It used to be pinned here as
// "an Änderungsübersicht übergeben" - which is what it said and not what
// it did: the profiles were written straight into the trip, and the
// overview it sent people to was empty. A test that writes the same
// wrong assumption down again is why it survived that long.
if (!source.includes('Ortsprofile"} übernehmen</button>')) {
  throw new Error("The submit button must name the effect it actually has");
}
if (source.includes('an Änderungsübersicht übergeben</button>')) {
  throw new Error("The submit button promises a handover it does not perform");
}
if (!source.includes('übernommen und angewendet')) {
  throw new Error("Direct apply of confirmed enrichments is missing");
}
if (!source.includes('dort bitte anwenden')) {
  throw new Error("The review fallback no longer tells the user to apply the handoff");
}
if (!source.includes('Math.abs(latitude) > 90 || Math.abs(longitude) > 180')) {
  throw new Error("The manual map check must validate typed coordinates (incl. empty fields) before opening Maps");
}
if (!source.includes('.replace(",", ".")')) {
  throw new Error("The manual map check must accept German decimal commas");
}
if (!source.includes('place_profile?.confirmed_at')) {
  throw new Error("Stop cards do not distinguish reviewed place profiles");
}
if (!source.includes('result.gallery.day_id') || !source.includes('resolvedDayId')) {
  throw new Error("Gallery refresh does not adopt the backend-resolved stop reference");
}

console.log("Place enrichment UI tests passed.");
