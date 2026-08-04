// The stop card's shared-link buttons must be distinguishable.
//
// Live report: a wildcamp stop carrying Plan A/B/C Park4Night links showed
// three identical "park4night.com" buttons - impossible to tell apart, and
// the same place listed twice under two URL shapes (/place/<id> and
// /lieu/<id>/) counted as two entries.
import assert from "node:assert/strict";

class FakeShadowRoot {
  addEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
}

class FakeHTMLElement {
  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { matchMedia: () => ({ matches: false, addEventListener() {} }), setTimeout, clearTimeout };
globalThis.document = { createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, click() {} }) };
Object.defineProperty(globalThis, "navigator", { value: { clipboard: { async writeText() {} } }, configurable: true });
globalThis.customElements = { define(name, ctor) { registry.set(name, ctor); }, get: (name) => registry.get(name) };

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const panel = new (registry.get("roadplanner-panel"))();

const stop = {
  notes: [
    "Plan A https://park4night.com/de/place/603309",
    "Plan B https://park4night.com/lieu/110490/",
    "Info https://www.naturkartan.se/de/tiveden",
  ].join(" · "),
  details: {
    // The SAME place again under the other URL shape, plus noise the card
    // must never offer as a lookup link.
    sources: ["https://park4night.com/lieu/603309/?utm=share", "https://cdn.example/foto.jpg"],
    maps: "https://maps.app.goo.gl/abc123",
  },
};

const links = panel._stopSharedLinks(stop);
assert.deepEqual(
  links.map((link) => link.label),
  ["Park4Night #603309", "Park4Night #110490", "naturkartan.se"],
  "each link is labelled by its place id, unlabelled hosts fall back to the domain",
);
assert.equal(new Set(links.map((l) => l.label)).size, links.length, "no two buttons look alike");
assert.ok(
  !links.some((link) => /goo\.gl|google\./.test(link.url)),
  "maps links have their own button and are never duplicated here",
);
assert.ok(!links.some((link) => /\.jpg/.test(link.url)), "gallery images are not lookup pages");

// Excluding the dedicated Park4Night button drops exactly those entries.
const withoutP4n = panel._stopSharedLinks(stop, { excludeP4n: true });
assert.deepEqual(withoutP4n.map((link) => link.label), ["naturkartan.se"]);

// A stop without any links stays quiet.
assert.deepEqual(panel._stopSharedLinks({ notes: "keine Links", details: {} }), []);

// Historical provenance is NOT a reference to the current place.
//
// Live report: "Wir stehen an einem unbedeutenden Platz nicht mit p4n
// verlinkt sondern mit Google. Er scheint noch historische p4n links zu
// haben. In den Daten des Stopps finde ich nichts von p4n." The stop's
// place_profile still carried the source hints of an earlier enrichment
// run, and scanning the whole details blob turned them into three
// Park4Night buttons the user could neither explain nor remove.
const movedStop = {
  name: "Übernachtungsplatz",
  notes: "Übernachtungsplatz laut Google Maps Link: https://maps.app.goo.gl/kbHKXdHxaekk39aQA",
  details: {
    place_profile: {
      name: "Übernachtungsplatz",
      source_hints: [
        { provider: "park4night", id: "129424", url: "https://park4night.com/lieu/129424/" },
        { provider: "park4night", id: "595143", url: "https://park4night.com/lieu/595143/" },
        { provider: "park4night", id: "630381", url: "https://park4night.com/lieu/630381/" },
      ],
    },
    geocoding: { provider: "nominatim", raw_response: "https://park4night.com/lieu/129424/" },
  },
};
assert.deepEqual(
  panel._stopSharedLinks(movedStop),
  [],
  "a stop whose only current reference is a Google-Maps link offers no Park4Night buttons",
);
// A reference the user can actually see and change still counts.
assert.deepEqual(
  panel._stopSharedLinks({
    ...movedStop,
    details: { ...movedStop.details, source: "https://park4night.com/lieu/129424/" },
  }).map((link) => link.label),
  ["Park4Night #129424"],
);

console.log("Stop shared link tests passed.");
