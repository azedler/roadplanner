import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = {
  createElement() { return { setAttribute() {}, style: {}, remove() {} }; },
  body: { appendChild() {} },
};
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

const source = await readFile(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url), "utf8");
vm.runInThisContext(source, { filename: "roadplanner-panel.js" });
const Panel = registry.get("roadplanner-panel");
assert.ok(Panel);
const panel = new Panel();
panel._data = {
  selected_trip_id: "trip-1",
  selected_is_active: true,
  capabilities: { can_edit: true },
  integrity: {
    status: "attention",
    score: 78,
    dimensions: { sequence: 100, locations: 75, routes: 60, visuals: 50 },
    stats: {
      stop_count: 8,
      repairable_location_count: 2,
      route_issue_count: 1,
      visual_missing_count: 4,
    },
    issues: [
      {
        severity: "error",
        title: "GPS für Apotheke fehlt",
        message: "Der Stopp kann noch nicht vollständig geroutet werden.",
        day_id: "day-1",
        day_date: "2026-07-22",
        day_title: "Tallinn",
        stop_name: "Apotheke",
      },
    ],
  },
};

const card = panel._renderIntegrityCard();
assert.match(card, /Reisequalität/);
assert.match(card, />78</);
assert.match(card, /2 GPS offen/);
assert.match(card, /data-action="integrity-open"/);
assert.match(card, /data-action="integrity-prepare-locations"/);

const dialog = panel._renderTravelIntegrity();
assert.match(dialog, /GPS für Apotheke fehlt/);
assert.match(dialog, /data-action="integrity-open-day"/);
assert.match(dialog, /Planungsbilder ergänzen/);
assert.match(dialog, /Routen neu berechnen/);

console.log("Travel integrity UI tests passed.");
