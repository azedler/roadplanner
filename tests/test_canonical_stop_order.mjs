import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = { createElement() { return { setAttribute() {}, style: {}, remove() {} }; }, body: { appendChild() {} } };
globalThis.customElements = { define(name, constructor) { registry.set(name, constructor); }, get(name) { return registry.get(name); } };

const source = await readFile(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url), "utf8");
vm.runInThisContext(source, { filename: "roadplanner-panel.js" });
const Panel = registry.get("roadplanner-panel");
assert.ok(Panel);
const panel = new Panel();

const explicit = [
  { id: "third", position: 3 },
  { id: "first", position: 1 },
  { id: "second", position: 2 },
];
assert.deepEqual(panel._canonicalStops(explicit).map((item) => item.id), ["first", "second", "third"]);

const chronological = [
  { id: "overnight", type: "wildcamp" },
  { id: "late", type: "attraction", arrival_time: "16:30" },
  { id: "start", type: "start" },
  { id: "early", type: "sightseeing", arrival_time: "09:45" },
];
assert.deepEqual(panel._canonicalStops(chronological).map((item) => item.id), ["start", "early", "late", "overnight"]);

panel._data = {
  days: {
    days: [
      { id: "day-1", stops: [{ id: "camp", type: "wildcamp", name: "Camp", position: 1 }] },
      { id: "day-2", stops: [{ id: "visit", type: "sightseeing", name: "Visit", position: 1 }] },
    ],
  },
};
const effective = panel._effectiveDayStops(panel._data.days.days[1]);
assert.deepEqual(effective.map((item) => item.id), ["camp", "visit"]);
assert.equal(effective[0]._inherited, true);

const dayWithLegacyLabels = {
  id: "day-3",
  start: "Legacy start",
  end: "Legacy end",
  stops: [
    { id: "visit-2", name: "Second", type: "sightseeing", position: 2 },
    { id: "visit-1", name: "First", type: "sightseeing", position: 1 },
  ],
};
panel._data.days.days.push(dayWithLegacyLabels);
const flow = panel._renderRouteFlow(dayWithLegacyLabels);
assert.match(flow, /First/);
assert.match(flow, /Second/);
assert.doesNotMatch(flow, /Legacy start/);
assert.doesNotMatch(flow, /Legacy end/);
assert.equal(panel._effectiveDayStart(dayWithLegacyLabels), "First");
const journey = panel._renderTripRouteGraphic([dayWithLegacyLabels]);
assert.match(journey, /First/);
assert.match(journey, /Second/);
assert.doesNotMatch(journey, /Legacy start/);
assert.doesNotMatch(journey, /Legacy end/);

console.log("Canonical stop order panel tests passed.");
