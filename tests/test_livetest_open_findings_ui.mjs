/**
 * The panel half of the 2026-08-21 live findings.
 *
 * O-1  the application collided with ITSELF: the automatic route
 *      recalculation writes a revision a few seconds after every stop
 *      move, the panel queued that push while a dialog was open, and the
 *      second move in "Reihenfolge ändern" was rejected as if somebody
 *      else had edited the trip.
 * O-2  a button said "an Änderungsübersicht übergeben" and applied the
 *      change directly, into an overview that was then empty.
 * O-3  "Reisetag anlegen" existed only in the empty state, so a second
 *      day was unreachable from the Tage tab.
 * O-5  two counters for one set: the tile said 112, the filter 163.
 * O-6  "43 nahe Vorschläge übernehmen" confirmed 4, then 0, then 0.
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
panel._render = () => {};

// --- O-1: the pushed revision is adopted even behind a dialog ----------
panel._data = {
  selected_trip_id: "reise-a",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 15 },
  days: { days: [], total: 0 },
};
panel._dialog = { type: "stop-order", dayId: "day-1" };

panel._adoptPushedRevision(16);
assert.equal(panel._currentRevision(), 16, "the panel must learn the revision the auto-route wrote");

// It only ever moves forward - an event that arrives late must not undo
// a newer number the panel already has.
panel._adoptPushedRevision(14);
assert.equal(panel._currentRevision(), 16);
// And nothing that is not a revision gets in.
for (const bad of [undefined, null, "17", 1.5, NaN, -1, true]) {
  panel._adoptPushedRevision(bad);
  assert.equal(panel._currentRevision(), 16, `accepted ${String(bad)} as a revision`);
}

// Looking at a DIFFERENT trip: the event describes the active one, so
// adopting its number here would be the same fault mirrored.
panel._data.selected_is_active = false;
panel._adoptPushedRevision(99);
assert.equal(panel._currentRevision(), 16, "a foreign trip's revision was adopted");
panel._data.selected_is_active = true;

// The move then carries the current number, which is the whole point.
let sent = null;
panel._runAction = async (action, data) => { sent = { action, data }; return { changed: true }; };
panel._findDay = () => ({ id: "day-1", stops: [{ id: "s1" }, { id: "s2" }, { id: "s3" }] });
panel._canonicalDayModel = () => null;
panel._canonicalStops = (stops) => stops;
await panel._moveStop("day-1", "s1", 2);
assert.equal(sent.action, "update_stop");
assert.equal(sent.data.expected_revision, 16, "the second move still sends the stale revision");

// --- O-2: the button says what it does ---------------------------------
const enrichment = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/features/place-enrichment.js", import.meta.url),
  "utf-8",
);
assert.doesNotMatch(
  enrichment.split("_renderPlaceEnrichment")[1] || enrichment,
  /an Änderungsübersicht übergeben<\/button>/,
  "the submit button still promises a handover it does not perform",
);
assert.match(enrichment, /Ortsprofile"\} übernehmen<\/button>/, "the button must name the actual effect");
// The handover wording survives where it is TRUE: the fallback branch.
assert.match(enrichment, /an die Änderungsübersicht übergeben - dort bitte anwenden/);
assert.match(enrichment, /result\.applied/, "both outcomes must still be distinguished");

// --- O-3: a second day is reachable from the day view -------------------
const dayStop = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js", import.meta.url),
  "utf-8",
);
const toolbar = dayStop.split("_renderDayRoute()")[1].split("_renderStopCard")[0];
assert.match(toolbar, /data-action="add-day"/, "the day toolbar offers no way to add the next day");
assert.match(toolbar, /data-action="add-stop"/);

// --- O-5 / O-6: numbers that hold ---------------------------------------
const media = [
  // suggested, real distance, near  -> confirmable
  { id: "a", assignment_status: "suggested", linked_day_id: "d1", distance_m: 200 },
  { id: "b", assignment_status: "suggested", linked_day_id: "d1", distance_m: 1500 },
  // suggested, distance UNKNOWN -> the server refuses these, and Number(null) is 0
  { id: "c", assignment_status: "suggested", linked_day_id: "d1", distance_m: null },
  { id: "d", assignment_status: "suggested", linked_day_id: "d1" },
  // suggested but far away
  { id: "e", assignment_status: "suggested", linked_day_id: "d1", distance_m: 4000 },
  // suggested without a day at all
  { id: "f", assignment_status: "suggested", distance_m: 10 },
  { id: "g", assignment_status: "manual", linked_day_id: "d1", distance_m: 10 },
  { id: "h", assignment_status: "unassigned" },
];
panel._experienceData = () => ({ media });
panel._data.capabilities = { can_edit: true };
assert.equal(
  panel._confirmableSuggestions(), 2,
  "a photograph whose distance is unknown was counted as standing on its stop",
);

console.log("Live findings UI tests passed.");
