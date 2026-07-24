import fs from "node:fs";

const source = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/roadplanner-panel.js",
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
  'OpenStreetMap',
  'Öffnungszeiten',
  'Vertrauen',
  'use_ai_cleanup',
  'manual_entries',
  'cleanup_confirmations',
  'data-action="place-enrichment-ai-retry"',
  'data-action="place-manual-select"',
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
if (!source.includes('Ortsprofile an die Änderungsübersicht übergeben')) {
  throw new Error("Review-only handoff confirmation is missing");
}
if (!source.includes('place_profile?.confirmed_at')) {
  throw new Error("Stop cards do not distinguish reviewed place profiles");
}
if (!source.includes('result.gallery.day_id') || !source.includes('resolvedDayId')) {
  throw new Error("Gallery refresh does not adopt the backend-resolved stop reference");
}

console.log("Place enrichment UI tests passed.");
