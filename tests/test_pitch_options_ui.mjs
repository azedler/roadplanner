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
globalThis.window = {
  location: { origin: "https://ha.example" },
  setTimeout,
  clearTimeout,
};
globalThis.document = {
  createElement() {
    return { setAttribute() {}, style: {}, select() {}, remove() {} };
  },
  body: { appendChild() {} },
  execCommand() { return true; },
};
Object.defineProperty(globalThis, "navigator", { value: { clipboard: { async writeText() {} } }, configurable: true });
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);

const Panel = registry.get("roadplanner-panel");
const panel = new Panel();

const day = {
  id: "day-1",
  sequence: 3,
  date: "2026-08-02",
  title: "Rostock",
  details: {
    overnight_plan: {
      schema_version: 1,
      strategy: "best_first",
      options: [
        { id: "opt-1", name: "Waldparkplatz am See", status: "backup", price: { amount: 0, currency: "EUR" }, notes: "kostenlos", features: { quiet: true } },
        { id: "opt-2", name: "Laut an der Straße", status: "rejected" },
      ],
    },
  },
  stops: [
    { id: "stop-0", name: "Bäcker", type: "waypoint" },
    { id: "stop-1", name: "Stellplatz am Hafen", type: "stellplatz" },
  ],
};

panel._data = {
  days: { days: [day] },
  summary: { trip: { details: { pitch_preferences: { preferred: { quiet: 5 }, required: { dog_ok: true }, limits: { max_price_per_night: 25 }, avoid: ["busy_road"], free_text: "Ruhig." } } } },
  experience: { by_stop: { "stop-1": ["m1", "m2"] } },
  travel_archive: { by_stop: { "stop-1": ["doc1"] } },
  settings: { routing_configured: true },
  capabilities: { can_edit: true },
};
panel._selectedTripId = "trip-1";
panel._canEdit = () => true;
panel._renderReadOnlyNotice = () => "";
panel._findDay = (dayId) => (dayId === "day-1" ? day : null);

// The plan helpers must read the day-anchored overnight plan.
const plan = panel._pitchPlan(day);
assert.equal(plan.strategy, "best_first");
assert.equal(plan.options.length, 2);
assert.equal(panel._pitchActiveStop(day).id, "stop-1", "the overnight stop is the last overnight-typed stop");
assert.equal(panel._pitchBackups(day).length, 1, "rejected options are not backups");

// Tab rendering: active place, backup with activate button, rejected collapsed.
const tab = panel._renderPitches();
assert.match(tab, /Stellplätze/);
assert.match(tab, /Stellplatz am Hafen/);
assert.match(tab, /Waldparkparkplatz am See|Waldparkplatz am See/);
assert.match(tab, /data-action="pitch-activate"/);
assert.match(tab, /Verworfene Optionen \(1\)/);
assert.match(tab, /data-action="pitch-strategy"/);
assert.match(tab, /pitch-add-option/);

// Preferences card shows stored values.
assert.match(tab, /Stellplatz-Präferenzen/);
assert.match(tab, /value="25"/);
assert.match(tab, /busy_road/);

// Plan-B card for the Heute tab: first backup, one-tap activation.
const planB = panel._renderPlanBCard(day);
assert.match(planB, /Plan B für diesen Tag/);
assert.match(planB, /Waldparkplatz am See/);
assert.match(planB, /data-action="pitch-activate"/);
assert.doesNotMatch(planB, /Laut an der Straße/, "rejected options must never be offered as Plan B");

// A day without backups renders no Plan-B card at all.
assert.equal(panel._renderPlanBCard({ id: "day-2", details: {}, stops: [] }), "");

// Activation asks for confirmation and mentions linked media/documents.
let confirmed = null;
panel._confirm = (title, message, label, callback) => { confirmed = { title, message, label, callback }; };
panel._activatePitchOption("day-1", "opt-1");
assert.ok(confirmed, "activation must go through a confirmation dialog");
assert.match(confirmed.message, /2 Fotos/);
assert.match(confirmed.message, /1 Dokument/);
assert.match(confirmed.message, /Backup-Option erhalten/);

// Confirming sends the atomic activate action with the day revision.
const calls = [];
panel._currentRevision = () => 41;
panel._runAction = async (action, data) => { calls.push({ action, data }); return { ok: true }; };
panel._showToast = () => {};
panel._calculateDayRoute = async () => { calls.push({ action: "calculate_day_route_helper" }); };
await confirmed.callback();
assert.equal(calls[0].action, "pitch_option_activate");
assert.deepEqual(calls[0].data.payload, { option_id: "opt-1" });
assert.equal(calls[0].data.day_id, "day-1");
assert.equal(calls[0].data.expected_revision, 41);

console.log("Pitch options UI tests passed.");
