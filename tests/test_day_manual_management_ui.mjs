/**
 * A handler nobody can reach is not a feature.
 *
 * "delete-day", "move-day-up" and "move-day-down" were all handled in the
 * panel's dispatcher, the backend action existed, the confirm dialog was
 * written - and no template anywhere rendered a button for any of them.
 * Deleting a day was only possible through a Home Assistant service call or
 * the assistant (live question: "Kann ich denn Tage händisch löschen?").
 *
 * This test pins BOTH halves together: the buttons must be rendered, and
 * clicking them must reach the right action with the right payload.
 */
import assert from "node:assert/strict";

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

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const Panel = registry.get("roadplanner-panel");
const panel = new Panel();

const days = [
  { id: "day-1", sequence: 1, title: "Anreise", date: "2026-07-17", stops: [], stop_count: 0 },
  { id: "day-2", sequence: 2, title: "Masuren", date: "2026-07-18", stops: [], stop_count: 4 },
  { id: "day-3", sequence: 3, title: "Fähre zurück", date: "2026-07-19", stops: [], stop_count: 2 },
];
panel._data = {
  selected_trip_id: "trip-1",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 58 },
  days: { days, total: 3 },
};
panel._selectedTripId = "trip-1";

// --- the buttons exist at all -----------------------------------------
const middle = panel._renderDayOrderButtons(days[1]);
assert.match(middle, /data-action="move-day-up"/);
assert.match(middle, /data-action="move-day-down"/);

// The ends offer only the direction that exists.
const first = panel._renderDayOrderButtons(days[0]);
assert.doesNotMatch(first, /move-day-up/);
assert.match(first, /data-action="move-day-down"/);
const last = panel._renderDayOrderButtons(days[2]);
assert.match(last, /data-action="move-day-up"/);
assert.doesNotMatch(last, /move-day-down/);

// A single-day trip has nothing to reorder.
panel._data.days = { days: [days[0]], total: 1 };
assert.equal(panel._renderDayOrderButtons(days[0]), "");
panel._data.days = { days, total: 3 };

// The day view itself must render the delete button - the missing piece.
const source = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js", import.meta.url),
  "utf-8",
);
assert.match(source, /data-action="delete-day"/, "the day view must offer a delete button");
assert.match(source, /_renderDayOrderButtons\(day\)/, "the day view must offer the reorder buttons");

// --- clicking them does the right thing -------------------------------
// The dispatcher is reached the way the browser reaches it: a click on an
// element carrying data-action / data-day-id.
function click(action, dayId) {
  const target = {
    dataset: { action, dayId },
    closest() { return target; },
    classList: { contains: () => false },
  };
  panel._handleClick({ target });
}

let actionCall = null;
panel._runAction = async (action, data, successMessage) => {
  actionCall = { action, data, successMessage };
  return { ok: true };
};
let confirmed = null;
panel._confirm = (title, message, label, callback, destructive) => {
  confirmed = { title, message, label, destructive, callback };
};
panel._render = () => {};

click("move-day-down", "day-1");
assert.equal(actionCall.action, "update_day");
assert.equal(actionCall.data.position, 2, "moving day 1 down targets position 2");
assert.deepEqual(actionCall.data.patch, {});
assert.equal(actionCall.data.expected_revision, 58);

actionCall = null;
click("move-day-up", "day-3");
assert.equal(actionCall.data.position, 2, "moving day 3 up targets position 2");

// Deleting warns about the stops that go WITH the day - they are not
// re-homed anywhere, and that must be said before the click.
click("delete-day", "day-2");
assert.equal(confirmed.title, "Reisetag löschen?");
assert.equal(confirmed.destructive, true);
assert.match(confirmed.message, /4 Stopps/);
assert.match(confirmed.message, /nicht rückgängig/);

click("delete-day", "day-1");
assert.match(confirmed.message, /keine Stopps/, "an empty day must not threaten with stops");

actionCall = null;
await confirmed.callback();
assert.equal(actionCall.action, "remove_day");
assert.equal(actionCall.data.day_id, "day-1");
assert.equal(actionCall.data.remove_stops, true);
assert.equal(actionCall.data.expected_revision, 58);

// --- read-only must not offer any of it -------------------------------
panel._data.capabilities = { can_edit: false };
actionCall = null;
confirmed = null;
click("delete-day", "day-2");
click("move-day-up", "day-3");
assert.equal(confirmed, null);
assert.equal(actionCall, null);

console.log("Manual day management UI tests passed.");
