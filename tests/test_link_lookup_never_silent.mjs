// Pressing "Link lesen und übernehmen" must ALWAYS say something.
//
// Live report: "Es erscheint weiterhin keine Fehler" - and no filled field,
// no note, nothing at all. A press that hits a permission gate did nothing
// whatsoever, and a lookup answered by an older backend left no trace
// either, so there was no way to tell the two apart from a phone.
import assert from "node:assert/strict";
import fs from "node:fs";

const panelSource = fs.readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf8",
);
const pitchesSource = fs.readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/features/pitches.js", import.meta.url),
  "utf8",
);

// 1. The permission gate explains itself instead of swallowing the press.
assert.doesNotMatch(
  panelSource,
  /action === "pitch-option-link-lookup" && this\._canEdit\(\)/,
  "a press that fails the edit check must not vanish without a word",
);
assert.match(panelSource, /Bearbeitungsberechtigung/);
assert.match(panelSource, /nicht die aktive Planung/);

// 2. The form shows that the read started, before any answer arrives.
assert.match(pitchesSource, /Link wird gelesen …/);
// 3. ... and always ends with a verdict, including "the backend said nothing".
assert.match(pitchesSource, /keine Auskunft erhalten \(ältere Version\?\)/);
assert.match(pitchesSource, /Leseergebnis - \$\{diagnosis\}/);

// 4. Backend and interface version are visible side by side, so "function
//    missing" and "panel still old" stop looking identical.
assert.match(panelSource, /_versionSummary\(\)\s*\{/);
assert.match(panelSource, /Oberfläche \$\{LOADED_MODULE_VERSION\}/);
const overviewSource = fs.readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js", import.meta.url),
  "utf8",
);
assert.match(overviewSource, /this\._versionSummary\(\)/);

console.log("Link lookup never-silent tests passed.");
