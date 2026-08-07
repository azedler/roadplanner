/**
 * Expandable sections have to survive a re-render.
 *
 * Live report: "nach Drücken auf einen der Tests schließt sich der Test
 * immer und man muss ihn neu öffnen und runterscrollen."
 *
 * Rendering replaces the whole shadow DOM, so every <details> comes back
 * in its default state - closed. Preserving the scroll offset, which the
 * panel already did, cannot compensate: a collapsed page is shorter than
 * the offset that was measured against the expanded one, so the browser
 * clamps it and the user lands at the top of a closed section.
 *
 * Every button inside such a section triggers a re-render, which made the
 * renderer-app and Remotion cards effectively unusable: press a button,
 * lose the card.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (relative) =>
  readFileSync(new URL(`../custom_components/roadplanner_mcp/${relative}`, import.meta.url), "utf8");

const panel = read("frontend/roadplanner-panel.js");

// --- the mechanism ------------------------------------------------------
assert.match(panel, /_openSections\(\)/, "der offene Zustand muss erfasst werden");
assert.match(panel, /_restoreOpenSections\(/, "und wiederhergestellt werden");
assert.match(
  panel,
  /details\[data-section\]/,
  "die Sektionen brauchen eine stabile Kennung, kein Positionsraten",
);

// Order matters: the page has to be its full height again before the
// scroll offset means anything.
const restoreAt = panel.indexOf("this._restoreOpenSections(openSections)");
const scrollAt = panel.indexOf("nextContent.scrollTop = scrollTop");
assert.ok(restoreAt > 0 && scrollAt > 0, "beide Schritte müssen existieren");
assert.ok(
  restoreAt < scrollAt,
  "erst aufklappen, dann scrollen - sonst wird der Offset auf eine zu kurze Seite angewendet",
);

// --- every section is addressable ---------------------------------------
const files = [
  "frontend/roadplanner-panel.js",
  "frontend/features/trip-day-stop.js",
  "frontend/features/assistant.js",
  "frontend/features/crew.js",
  "frontend/features/pitches.js",
  "frontend/features/place-enrichment.js",
];
/**
 * Comments are prose. This very file's explanation of the bug contains the
 * word `<details>`, and counting it as markup would demand a data-section
 * on a sentence. Count what renders, not what explains it.
 */
const stripComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const ids = new Set();
for (const file of files) {
  const source = stripComments(read(file));
  const all = source.match(/<details\b/g) || [];
  const marked = source.match(/<details data-section="([^"]+)"/g) || [];
  assert.equal(
    all.length,
    marked.length,
    `${file}: ${all.length - marked.length} <details> ohne data-section - die klappen weiterhin zu`,
  );
  for (const hit of marked) {
    const id = hit.match(/data-section="([^"]+)"/)[1];
    assert.ok(!ids.has(id), `doppelte Sektionskennung: ${id}`);
    ids.add(id);
  }
}
assert.ok(ids.size >= 8, `zu wenige Sektionen gefunden: ${ids.size}`);

// --- and the card must fit on a phone -----------------------------------
const styles = read("frontend/lib/styles.js");
assert.match(
  styles,
  /\.facts-grid strong.*overflow-wrap: anywhere/,
  "lange Werte ohne Leerzeichen müssen umbrechen, sonst schiebt sich die Karte aus dem Bild",
);
assert.match(
  styles,
  /\.renderer-app-artifact \{[^}]*max-width: 100%/,
  "ein Artefakt bringt seine Eigenbreite mit und muss begrenzt werden",
);

console.log("Panel section state tests passed.");
