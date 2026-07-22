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

const day = {
  id: "day-1",
  title: "Küste",
  date: "2026-07-21",
  start: "Legacy Start",
  end: "Riga",
  canonical: {
    start_label: "Berg der Kreuze",
    end_label: "RMK Matsi Beach",
    stops: [
      { id: "a", name: "Berg der Kreuze", display_sequence: 1, marker_label: "1", type: "sightseeing" },
      { id: "b", name: "RMK Matsi Beach", display_sequence: 2, marker_label: "2", type: "overnight" },
    ],
    route_nodes: [
      { id: "a", name: "Berg der Kreuze", display_sequence: 1, marker_label: "1", type: "sightseeing" },
      { id: "b", name: "RMK Matsi Beach", display_sequence: 2, marker_label: "2", type: "overnight" },
    ],
  },
};
panel._data = {
  selected_trip_id: "trip",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 1, day_count: 1, stop_count: 2, trip: { title: "Testreise", status: "planning" } },
  days: { days: [day] },
  handoffs: { total: 0, status_counts: {}, handoffs: [] },
  experience: {
    destination_galleries: {},
    presentation: {},
    media: [],
    by_day: {},
    by_stop: {},
    decisions: [],
    stats: {},
    onedrive: {},
  },
  archive: { documents: [], expenses: [], todos: [], stats: {}, by_day: {}, by_stop: {} },
  settings: {},
  trips: { trips: [] },
};
panel._selectedDayId = day.id;

const navigation = panel._renderTabs();
assert.match(navigation, />Reise</);
assert.match(navigation, />Heute</);
assert.match(navigation, />Erinnerungen</);
assert.match(navigation, />Reisebegleiter</);
assert.match(navigation, />Mehr</);

const flow = panel._renderRouteFlow(day);
assert.match(flow, /Berg der Kreuze/);
assert.match(flow, /RMK Matsi Beach/);
assert.doesNotMatch(flow, /Legacy Start/);
assert.doesNotMatch(flow, /Riga/);
assert.equal(panel._effectiveDayStart(day), "Berg der Kreuze");
assert.equal(panel._effectiveDayEnd(day), "RMK Matsi Beach");

panel._data.experience.media = [
  { id: "all-1", linked_stop_id: "a", linked_day_id: "day-1", thumbnail_url: "https://example/1.jpg" },
  { id: "all-2", linked_stop_id: "a", linked_day_id: "day-1", thumbnail_url: "https://example/2.jpg" },
  { id: "all-3", linked_stop_id: "a", linked_day_id: "day-1", thumbnail_url: "https://example/3.jpg" },
  { id: "all-4", linked_stop_id: "a", linked_day_id: "day-1", thumbnail_url: "https://example/4.jpg" },
];
panel._data.experience.by_stop = { a: ["all-1", "all-2", "all-3", "all-4"] };
panel._data.experience.by_day = { "day-1": ["all-1", "all-2", "all-3", "all-4"] };
panel._data.experience.presentation = {
  stop_highlights: { a: ["all-3", "all-1"] },
  stop_covers: { a: "all-3" },
  day_highlights: { "day-1": ["all-3", "all-2"] },
  day_covers: { "day-1": "all-3" },
};
assert.deepEqual(panel._experienceMediaForStop("a").map((item) => item.id), ["all-3", "all-1"]);
assert.equal(panel._experienceAllMediaForStop("a").length, 4);
assert.equal(panel._experienceCoverForStop("a").id, "all-3");
assert.equal(panel._dayCoverImage(day).id, "all-3");

console.log("Roadplanner 3.0 UX contract tests passed.");
