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

const day = {
  id: "day-1",
  title: "Masuren und Kaunas",
  date: "2026-07-18",
  stops: [
    { id: "overnight", name: "RMK Matsi Beach", type: "wildcamp", position: 3 },
    { id: "zoo", name: "Dino Zoo Akropolis Alfa", type: "shopping", position: 1, arrival_time: "15:00" },
    { id: "restaurant", name: "Restorans Meke", type: "restaurant", position: 2, arrival_time: "18:00" },
  ],
};
panel._data = {
  selected_trip_id: "trip-1",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 58 },
  days: { days: [day] },
};
panel._selectedTripId = "trip-1";

// Move targets must follow the canonical explicit positions, not the stale
// payload array order. Times remain descriptive only.
const state = panel._stopOrderState(day, "restaurant");
assert.deepEqual(state.stops.map((stop) => stop.id), ["zoo", "restaurant", "overnight"]);
assert.equal(state.position, 2);
assert.equal(state.canMoveEarlier, true);
assert.equal(state.canMoveLater, true);
assert.equal(panel._stopMovePosition(day, "restaurant", -1), 1);
assert.equal(panel._stopMovePosition(day, "restaurant", 1), 3);
assert.equal(panel._stopMovePosition(day, "zoo", -1), null);
assert.equal(panel._stopMovePosition(day, "overnight", 1), null);

const dialog = panel._renderStopOrderDialog({ dayId: day.id });
assert.ok(dialog.indexOf("Dino Zoo Akropolis Alfa") < dialog.indexOf("Restorans Meke"));
assert.ok(dialog.indexOf("Restorans Meke") < dialog.indexOf("RMK Matsi Beach"));
assert.match(dialog, /data-action="move-stop-position"/);
assert.match(dialog, /data-action="move-stop-up"/);
assert.match(dialog, /data-action="move-stop-down"/);
assert.match(dialog, /Uhrzeiten bleiben reine Planungsangaben/);
assert.match(dialog, /Ein geerbter Übernachtungsstart vom Vortag bleibt automatisch davor/);

let actionCall = null;
panel._runAction = async (action, data, successMessage) => {
  actionCall = { action, data, successMessage };
  return { ok: true };
};
const moved = await panel._moveStop(day.id, "restaurant", 1);
assert.deepEqual(moved, { ok: true });
assert.deepEqual(actionCall, {
  action: "update_stop",
  data: {
    day_id: "day-1",
    stop_id: "restaurant",
    patch: {},
    position: 1,
    expected_revision: 58,
  },
  successMessage: "Stopp verschoben",
});

actionCall = null;
assert.equal(await panel._moveStop(day.id, "restaurant", 2), null);
assert.equal(actionCall, null);

console.log("Manual stop reordering UI tests passed.");
