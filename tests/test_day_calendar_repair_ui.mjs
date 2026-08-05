/**
 * UI contract for the travel-calendar repair (live report: "Die Tage sind
 * verwurschtelt"). The chain button -> plan dialog -> proposal must stay
 * wired: a broken link here is invisible until somebody needs the repair.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

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
panel._data = {
  selected_trip_id: "trip-1",
  selected_is_active: true,
  capabilities: { can_edit: true },
  integrity: { status: "attention", score: 80, dimensions: {}, stats: {}, issues: [] },
};

// The entry point lives in the trip-quality dialog.
assert.match(panel._renderTravelIntegrity(), /data-action="integrity-repair-days"/);

const plan = {
  needs_repair: true,
  day_count_before: 28,
  day_count_after: 25,
  start_date: "2026-07-17",
  end_date: "2026-08-10",
  last_day_title: "Trelleborg → Rostock (TT-Line Fähre)",
  empty_days: [{ day_id: "d21", title: "Tag 2026-08-06", date: "2026-08-06" }],
  redated_days: [
    { day_id: "d24", title: "Gränna → Store Mosse", sequence: 21, old_date: "2026-08-09", new_date: "2026-08-06" },
  ],
};
const dialog = panel._renderDayCalendarRepair({ plan, busy: false });
assert.match(dialog, /28 → 25 Reisetage/);
assert.match(dialog, /Tag 2026-08-06/);
assert.match(dialog, /2026-08-09 → 2026-08-06/);
assert.match(dialog, /Trelleborg/);
// The promise this dialog makes must be on screen, not only in the backend.
assert.match(dialog, /Reihenfolge der Tage bleibt unverändert/);
assert.match(dialog, /Übergaben/);
assert.match(dialog, /data-action="day-calendar-repair-submit"/);

const busy = panel._renderDayCalendarRepair({ plan, busy: true });
assert.match(busy, /disabled/, "a second click must not create a second proposal");

const healthy = panel._renderDayCalendarRepair({ plan: { needs_repair: false, day_count_before: 23 } });
assert.match(healthy, /in Ordnung/);
assert.doesNotMatch(healthy, /day-calendar-repair-submit/);

// The dispatcher and the backend actions have to agree on the strings.
const panelJs = readFileSync(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url), "utf8");
assert.match(panelJs, /action === "integrity-repair-days"/);
assert.match(panelJs, /action === "day-calendar-repair-submit"/);
assert.match(panelJs, /this\._dialog\.type === "day-calendar-repair"/);

const panelPy = readFileSync(new URL("../custom_components/roadplanner_mcp/panel.py", import.meta.url), "utf8");
for (const action of ["plan_day_calendar_repair", "propose_day_calendar_repair"]) {
  assert.ok(panelPy.includes(`"${action}"`), `${action} must be a known panel action`);
  assert.ok(panelPy.includes(`async_${action}`), `${action} must reach the manager`);
}
// Creating the proposal writes; reading the plan does not.
const editBlock = panelPy.slice(panelPy.indexOf("_EDIT_ACTIONS = {"), panelPy.indexOf("_PROVIDER_CALL_ACTIONS"));
assert.ok(editBlock.includes('"propose_day_calendar_repair"'));
assert.ok(!editBlock.includes('"plan_day_calendar_repair"'));

const managerPy = readFileSync(new URL("../custom_components/roadplanner_mcp/manager.py", import.meta.url), "utf8");
assert.match(managerPy, /"apply_mode": "review"/);
assert.ok(
  managerPy.includes("roadplanner_day_calendar_repair"),
  "the proposal must be attributable to its source",
);

console.log("Travel calendar repair UI tests passed.");
