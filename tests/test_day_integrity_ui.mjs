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

const inherited = {
  id: "camp",
  name: "RMK Matsi Beach",
  type: "wildcamp",
  marker_label: "S",
  display_sequence: null,
  _inherited: true,
  location_status: "resolved",
  location: { latitude: 58.2, longitude: 24.1 },
};
const parking = {
  id: "parking",
  name: "Parkplatz Kaarli puiestee",
  type: "parking",
  position: 1,
  marker_label: "1",
  display_sequence: 1,
  location_status: "resolved",
  location: { latitude: 59.43254, longitude: 24.74150 },
};
const pharmacy = {
  id: "pharmacy",
  name: "Apotheke / Arztbesuch",
  type: "service",
  position: 2,
  marker_label: "2",
  display_sequence: 2,
  location_status: "missing",
  location_message: "GPS-Koordinaten fehlen",
  location: {},
};
const ferry = {
  id: "ferry",
  name: "Fährterminal Tallinn",
  type: "ferry",
  position: 3,
  marker_label: "3",
  display_sequence: 3,
  arrival_time: "19:30",
  departure_time: "19:30",
  location_status: "resolved",
  location: { latitude: 59.444, longitude: 24.768 },
};
const day = {
  id: "day-1",
  sequence: 1,
  title: "Tallinn und Fähre",
  date: "2026-07-22",
  start: "Pärnu",
  end: "Helsinki",
  stops: [parking, pharmacy, ferry],
  canonical: {
    version: 3,
    stops: [parking, pharmacy, ferry],
    route_nodes: [inherited, parking, pharmacy, ferry],
    map_nodes: [inherited, parking, ferry],
    coordinate_count: 3,
    missing_coordinate_count: 1,
    missing_location_nodes: [
      {
        id: "pharmacy",
        name: "Apotheke / Arztbesuch",
        display_sequence: 2,
        marker_label: "2",
        inherited: false,
        status: "missing",
        query: "Apotheke / Arztbesuch, Tallinn, EE",
      },
    ],
    start_label: "RMK Matsi Beach",
    end_label: "Fährterminal Tallinn",
  },
  navigation: {},
};

panel._data = {
  selected_trip_id: "trip-1",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 1, day_count: 1, stop_count: 3, trip: { title: "Testreise", status: "active" } },
  days: { days: [day] },
  settings: { routing_configured: false },
  handoffs: { total: 0, status_counts: {}, handoffs: [] },
  experience: {
    destination_galleries: {}, presentation: {}, media: [], by_day: {}, by_stop: {}, decisions: [], stats: {}, onedrive: {},
  },
  archive: { documents: [], expenses: [], todos: [], stats: {}, by_day: {}, by_stop: {} },
  trips: { trips: [] },
};
panel._selectedTripId = "trip-1";
panel._selectedDayId = day.id;
panel._activeTab = "day-route";

// Times must not reorder a day with incomplete positions.
const incomplete = [
  { id: "parking", type: "parking" },
  { id: "pharmacy", type: "service" },
  { id: "ferry", type: "ferry", arrival_time: "19:30" },
];
assert.deepEqual(panel._canonicalStops(incomplete).map((item) => item.id), ["parking", "pharmacy", "ferry"]);

const effective = panel._effectiveDayStops(day);
assert.deepEqual(effective.map((item) => item.id), ["camp", "parking", "pharmacy", "ferry"]);

const flow = panel._renderRouteFlow(day);
assert.ok(flow.indexOf("RMK Matsi Beach") < flow.indexOf("Parkplatz Kaarli puiestee"));
assert.ok(flow.indexOf("Parkplatz Kaarli puiestee") < flow.indexOf("Apotheke / Arztbesuch"));
assert.ok(flow.indexOf("Apotheke / Arztbesuch") < flow.indexOf("Fährterminal Tallinn"));
assert.doesNotMatch(flow, />Pärnu</);
assert.doesNotMatch(flow, />Helsinki</);

const points = panel._dayRoutePoints(day);
assert.deepEqual(points.map((item) => item.markerLabel), ["S", "1", "3"]);
const map = panel._renderMap("integrity-map", points, day.title, [], "", effective);
assert.match(map, /Apotheke \/ Arztbesuch · GPS fehlt/);
assert.match(map, />2</);
assert.match(map, /Fährterminal Tallinn/);

const page = panel._renderDayRoute();
assert.match(page, /4 Stopps · 1 ohne GPS/);
assert.match(page, /Stopps anreichern \(1\)/);
assert.match(page, /Karte und Straßenroute sind unvollständig/);
assert.match(page, /Apotheke \/ Arztbesuch/);
assert.match(page, /Mit GPS<\/span><strong>3/);

const stopCard = panel._renderStopCard(day, pharmacy, 2);
assert.match(stopCard, /Ort fehlt/);
assert.match(stopCard, /GPS-Koordinaten fehlen/);

const tripNodes = panel._allRouteNodes();
assert.deepEqual(tripNodes.map((item) => item.id), ["parking", "pharmacy", "ferry"]);
assert.deepEqual(tripNodes.map((item) => item.marker_label), ["1", "2", "3"]);
const tripPoints = panel._allRoutePoints(tripNodes);
assert.deepEqual(tripPoints.map((item) => item.markerLabel), ["1", "3"]);
const totalMap = panel._renderMap("total-integrity-map", tripPoints, "Testreise", [], "", tripNodes);
assert.match(totalMap, /Apotheke \/ Arztbesuch · GPS fehlt/);

console.log("Canonical day integrity UI tests passed.");
