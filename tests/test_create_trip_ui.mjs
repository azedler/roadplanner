/**
 * RP-410: the "Neue Reise" button, its dialog, and the submit path.
 *
 * Pinned here because every half of this pair has failed alone before: a
 * backend action nobody can reach, and a button whose handler quietly does
 * nothing. The submit tests pin §11 (UI truthfulness) in two layers: the
 * dialog-close half against a stubbed _runAction (a failed create leaves
 * the form open), and the wire half through the REAL _runActionNow - the
 * toast fires only after the send resolved, and create_trip is NOT in
 * tripScopedActions, so no expected_trip_id of some unrelated selected
 * trip is smuggled into the payload.
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
// _handleSubmit reads the form the way the browser does.
globalThis.FormData = class {
  constructor(form) { this._entries = form._entries || []; }
  entries() { return this._entries[Symbol.iterator](); }
};

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const Panel = registry.get("roadplanner-panel");
const panel = new Panel();

const trips = [
  { id: "new-trip", title: "Finnland", active: true, valid: true, day_count: 3, stop_count: 9, revision: 12 },
  { id: "alt", title: "Alte Reise", active: false, valid: true, day_count: 1, stop_count: 2, revision: 4 },
];
panel._data = {
  selected_trip_id: "new-trip",
  selected_is_active: true,
  capabilities: { can_edit: true, can_activate: true },
  summary: { revision: 12 },
  trips: { trips },
};
panel._selectedTripId = "new-trip";
panel._render = () => {};

// --- the button exists exactly when the account may edit ---------------
assert.match(panel._renderTrips(), /data-action="create-trip"/);

// Looking at a foreign trip must NOT hide the button: creating a trip is
// unrelated to which trip is selected.
panel._data.selected_is_active = false;
assert.match(panel._renderTrips(), /data-action="create-trip"/);
panel._data.selected_is_active = true;

panel._data.capabilities = { can_edit: false, can_activate: false };
assert.doesNotMatch(panel._renderTrips(), /data-action="create-trip"/);
panel._data.capabilities = { can_edit: true, can_activate: true };

// --- the dialog opens on click, gated the same way ----------------------
function click(action) {
  const target = {
    dataset: { action },
    closest() { return target; },
    classList: { contains: () => false },
  };
  panel._handleClick({ target });
}
click("create-trip");
assert.equal(panel._dialog?.type, "trip-create");
panel._dialog = null;
panel._data.capabilities = { can_edit: false, can_activate: false };
click("create-trip");
assert.equal(panel._dialog, null, "read-only must not open the dialog");
panel._data.capabilities = { can_edit: true, can_activate: true };

// --- the form ----------------------------------------------------------
const form = panel._renderTripCreateForm();
assert.match(form, /data-form="trip-create"/);
assert.match(form, /name="title"[^>]*required/, "the title is the one required field");
assert.match(form, /name="activate"/, "an approver may activate immediately");
assert.doesNotMatch(form, /data-revision/, "a create edits nothing and needs no revision");

panel._data.capabilities = { can_edit: true, can_activate: false };
assert.doesNotMatch(panel._renderTripCreateForm(), /name="activate"/, "without the approver role there is no activate checkbox");
panel._data.capabilities = { can_edit: true, can_activate: true };

// --- submitting --------------------------------------------------------
let actionCall = null;
let actionResult = { trip_id: "nordkap-2027", activated: false };
panel._runAction = async (action, data, successMessage) => {
  actionCall = { action, data, successMessage };
  return actionResult;
};
let closed = 0;
panel._closeDialog = () => { closed += 1; };
let toasts = [];
panel._showToast = (message, kind) => { toasts.push({ message, kind }); };

function submit(entries) {
  const form = { dataset: { form: "trip-create" }, _entries: entries, closest() { return form; } };
  return panel._handleSubmit({ target: form, preventDefault() {} });
}

// Empty title: refused locally, nothing sent, dialog stays open.
await submit([["title", "   "], ["status", "planning"]]);
assert.equal(actionCall, null, "an empty title must not reach the server");
assert.equal(closed, 0);
assert.equal(toasts[0]?.kind, "error");

// Full create with activation: the payload is exactly the contract.
toasts = [];
await submit([
  ["title", "Nordkap 2027"],
  ["status", "confirmed"],
  ["start_date", "2027-06-01"],
  ["end_date", ""],
  ["notes", "Endlich."],
  ["activate", "on"],
]);
assert.equal(actionCall.action, "create_trip");
assert.equal(actionCall.data.title, "Nordkap 2027");
assert.equal(actionCall.data.status, "confirmed");
assert.equal(actionCall.data.start_date, "2027-06-01");
assert.equal(actionCall.data.end_date, "");
assert.equal(actionCall.data.notes, "Endlich.");
assert.equal(actionCall.data.activate, true);
assert.equal(
  actionCall.data.expected_active_trip, "new-trip",
  "activating must guard against a concurrent pointer switch",
);
assert.equal(actionCall.successMessage, "Reise angelegt");
assert.equal(closed, 1, "the dialog closes after the server confirmed");

// Without activation the guard is not sent at all.
actionCall = null;
await submit([["title", "Nur anlegen"]]);
assert.equal(actionCall.data.activate, false);
assert.equal("expected_active_trip" in actionCall.data, false);

// §11: a failed create must NOT close the dialog.
actionResult = null;
closed = 0;
await submit([["title", "Scheitert"]]);
assert.equal(closed, 0, "a failed create closed the dialog anyway");

// --- activation adopts the new trip ------------------------------------
// Without this, the pointer switches server-side while the panel keeps
// showing the old trip: every edit button gone, the toast claiming
// success. The submit must follow the activate-trip pattern.
actionResult = { trip_id: "sofort-los", activated: true };
let loads = [];
panel._loadData = async (options) => { loads.push(options); };
let storyResets = 0;
panel._storyResetForTrip = () => { storyResets += 1; };
panel._selectedTripId = "new-trip";
await submit([["title", "Sofort los"], ["activate", "on"]]);
assert.equal(panel._selectedTripId, "sofort-los", "the panel must look at the trip it just activated");
assert.equal(storyResets, 1, "trip-scoped story state must reset on the switch");
assert.equal(loads.length, 1);
assert.equal(loads[0].force, true);

// A plain create refreshes too (the list must show the new trip), but
// keeps the selection.
actionResult = { trip_id: "nur-liste", activated: false };
loads = [];
storyResets = 0;
await submit([["title", "Nur Liste"]]);
assert.equal(panel._selectedTripId, "sofort-los", "creating without activating must not steal the selection");
assert.equal(storyResets, 0);
assert.equal(loads.length, 1, "the trips list must refresh to show the new trip");

// --- the wire itself, through the REAL _runActionNow --------------------
// The stub above proves what _handleSubmit sends; this proves what goes
// over the WebSocket: no expected_trip_id injection (create_trip must
// not be trip-scoped), and the toast only after the send resolved.
delete panel._runAction; // back to the prototype implementation
const events = [];
let sent = null;
panel._send = async (message) => { events.push("send"); sent = message; return { trip_id: "x", activated: false }; };
panel._showToast = (message, kind) => { events.push(`toast:${kind}`); };
panel._loadData = async () => { events.push("load"); };
panel._setBusy = () => {};
const wireResult = await panel._runActionNow("create_trip", { title: "Draht" }, "Reise angelegt");
assert.equal(sent.action, "create_trip");
assert.equal(
  "expected_trip_id" in sent.data, false,
  "create_trip landed in tripScopedActions - the new trip would be guarded against the WRONG trip",
);
assert.deepEqual(events, ["send", "toast:success", "load"], "the toast may only follow a confirmed send");
assert.equal(wireResult.trip_id, "x");

console.log("Create trip UI tests passed.");
