// Regression tests for the reported Park4Night lookup breakage.
//
// Live report: "Stopp editieren" -> "Park4Night-Daten lesen (KI)" delivered
// great data ONCE - afterwards every attempt failed with "er kann den Ort
// nicht finden". Root cause: the first successful lookup overwrote the stop
// name (which carried the only copy of the p4n reference) with the clean
// place name, preserving the reference NOWHERE. After saving, no field
// contained a p4n hint anymore, so park4nightReference() found nothing.
//
// Second gap fixed alongside: the reference regex only matched shorthand
// forms ("p4n 506374") - a real park4night.com page link was not recognized
// at all, although every hint text promises it works.

import { strict as assert } from "node:assert";
import { park4nightReference, cleanPlaceName, withPark4nightReference } from "../custom_components/roadplanner_mcp/frontend/lib/place-links.js";

// --- URL recognition ---------------------------------------------------

for (const url of [
  "https://park4night.com/lieu/506374/",
  "https://www.park4night.com/lieu/506374",
  "https://park4night.com/de/place/506374",
  "park4night.com/lieu/506374",
]) {
  const ref = park4nightReference(url);
  assert.ok(ref, `URL must be recognized: ${url}`);
  assert.equal(ref.id, "506374", `full ID must survive (no digit split) for ${url}`);
}

assert.equal(park4nightReference("https://park4night.com/"), null, "a bare host without ID stays unrecognized");
assert.ok(park4nightReference("Stellplatz (p4n #506374)"), "shorthand forms keep working");

// --- reference survival in notes ---------------------------------------

const ref = park4nightReference("Parkplatz am Angelteich p4n 506374");
assert.equal(
  withPark4nightReference("", ref),
  "Park4Night: https://park4night.com/lieu/506374/",
  "empty notes gain the reference line",
);
assert.equal(
  withPark4nightReference("Schöner Platz am See.\n", ref),
  "Schöner Platz am See.\nPark4Night: https://park4night.com/lieu/506374/",
  "existing notes keep their text and gain the reference line",
);
const withExisting = "Laut p4n: ruhig. Park4Night: https://park4night.com/lieu/506374/";
assert.equal(
  withPark4nightReference(withExisting, ref),
  withExisting,
  "notes already carrying the ID stay untouched (no duplicate lines)",
);
assert.equal(withPark4nightReference("Notiz", null), "Notiz", "no reference, no change");

// After the lookup flow (notes updated, name overwritten with the clean
// place name), a SECOND lookup must still find the reference - this is the
// exact reported failure.
const notesAfterFirstLookup = withPark4nightReference("", ref);
const nameAfterFirstLookup = "Parkplatz am Angelteich";
assert.ok(
  park4nightReference(nameAfterFirstLookup, notesAfterFirstLookup),
  "a repeat lookup after the first successful one must still find the reference",
);

// --- display cleaning stays intact for URLs -----------------------------

assert.equal(
  cleanPlaceName("Stellplatz Hafen park4night.com/lieu/506374"),
  "Stellplatz Hafen",
  "a pasted page link is stripped from the displayed name",
);

console.log("Park4Night reference survival tests passed.");
