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
        { id: "opt-1", name: "Waldparkplatz am See", status: "backup", price: { amount: 0, currency: "EUR" }, notes: "kostenlos", features: { quiet: true }, pros: ["Ruhig"], cons: ["Kein Handyempfang"] },
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

// Pros/cons must stand out as their own chips, not buried in a plain text line.
assert.match(tab, /pitch-chip-pro/);
assert.match(tab, /pitch-chip-con/);
assert.match(tab, /Ruhig/);
assert.match(tab, /Kein Handyempfang/);

// A day-select dropdown lets the user jump directly to any day.
assert.match(tab, /data-action="pitch-select-day"/);
assert.match(tab, /<option value="day-1" selected>3\. Rostock<\/option>/);

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

// Multi-day scenario: the tab must default to the current/upcoming day, not
// day 1, and show route context (where we come from / where we go next).
const day1 = { id: "day-1", sequence: 1, date: "2026-08-01", title: "Start", details: {}, stops: [] };
const day2 = {
  id: "day-2",
  sequence: 2,
  date: "2026-08-02",
  title: "Rostock",
  details: { overnight_plan: { strategy: "route_optimal", options: [] } },
  stops: [
    { id: "s1", name: "Hafen Rostock", type: "overnight", location: { latitude: 54.09, longitude: 12.13 } },
  ],
};
const day3 = {
  id: "day-3",
  sequence: 3,
  date: "2026-08-03",
  title: "Stralsund",
  details: {},
  stops: [
    { id: "s2", name: "Altstadt Stralsund", type: "waypoint", location: { latitude: 54.31, longitude: 13.08 } },
  ],
};
panel._data.days = { days: [day1, day2, day3] };
panel._data.summary.next_day = { id: "day-2" };
panel._findDay = (dayId) => [day1, day2, day3].find((item) => item.id === dayId) || null;
panel._pitchSelectedDayId = null;

const multiDayTab = panel._renderPitches();
assert.match(multiDayTab, /Aktueller Reisetag/, "the current day must be labeled as such");
assert.match(multiDayTab, /2\. Rostock/);
assert.equal((multiDayTab.match(/panel-card pitch-day-card/g) || []).length, 1, "only the current day's card must render, not every day stacked");
assert.doesNotMatch(multiDayTab, />Start</, "day 1's card must not be shown when day 2 is current");
assert.match(multiDayTab, /Morgen: Altstadt Stralsund/, "the map/route context must show tomorrow's first stop");

// Selecting a day from the dropdown switches which day is shown.
panel._pitchSelectedDayId = "day-3";
const day3Tab = panel._renderPitches();
assert.match(day3Tab, /3\. Stralsund/);
assert.doesNotMatch(day3Tab, /Aktueller Reisetag/);

// Opening the tab from the Plan-B card must jump to that specific day.
panel._pitchSelectedDayId = null;
panel._activeTab = "day-route";
panel._render = () => {};
const planBButtonDayId = "day-3";
// Simulate the click handler's pitch-open-tab branch directly (dispatch
// wiring is covered by the contract test).
if (planBButtonDayId) panel._pitchSelectedDayId = planBButtonDayId;
panel._activeTab = "pitches";
assert.equal(panel._pitchCurrentDayId(), "day-3");

// The tool-tabs tray must never force itself open just because a tool tab
// (like Stellplätze) is active - it used to eat the whole screen above the
// actual content on mobile.
panel._activeTab = "pitches";
const tabsHtml = panel._renderTabs();
assert.match(tabsHtml, /<details class="tool-tabs">/, "the tool-tabs tray must start collapsed regardless of the active tab");
assert.doesNotMatch(tabsHtml, /<details class="tool-tabs" open>/);

// Planning images for backup options: a saved option gallery (keyed
// "option:<id>") must surface as the row's thumbnail and on the Plan-B
// card; without one, the search button still offers internet images.
day2.details.overnight_plan.options.push({
  id: "opt-cover",
  name: "Wiese am Fluss",
  status: "backup",
  place_query: "Stellplatz Wiese am Fluss Rostock",
  location: { latitude: 54.2, longitude: 12.4 },
});
panel._data.experience.destination_galleries = {
  "option:opt-cover": {
    stop_id: "option:opt-cover",
    status: "ready",
    primary_image_id: "img-1",
    images: [{ id: "img-1", thumbnail_url: "https://images.example/wiese.jpg", image_url: "https://images.example/wiese-full.jpg" }],
  },
};
panel._pitchSelectedDayId = "day-2";
const galleryTab = panel._renderPitches();
assert.match(galleryTab, /pitch-option-cover/, "an option with a saved gallery must show its thumbnail");
assert.match(galleryTab, /images\.example\/wiese\.jpg/);
assert.match(galleryTab, /data-action="pitch-option-images"/, "every backup option must offer the image search");

const planBWithCover = panel._renderPlanBCard(day2);
assert.match(planBWithCover, /pitch-plan-b-cover/, "the Plan-B card must show the option's planning image");

// The image search for an option reuses the shared dialog flow with the
// option key and the option's own coordinates and query.
let searchContext = null;
panel._searchImages = async (context) => { searchContext = context; };
panel._searchPitchOptionImages("day-2", "opt-cover");
assert.equal(searchContext.stopId, "option:opt-cover");
assert.equal(searchContext.dayId, "day-2");
assert.equal(searchContext.query, "Stellplatz Wiese am Fluss Rostock");
assert.equal(searchContext.location.latitude, 54.2);

console.log("Pitch options UI tests passed.");

// The option form offers "Link lesen und übernehmen": Park4Night pages are
// read directly (AI-free), Google-Maps links resolved, other pages via the
// Reisebegleiter - values only prefill the form, saving stays explicit.
const optionForm = panel._renderPitchOptionForm({ dayId: "day-2", option: null });
assert.match(optionForm, /name="url"/, "the option form must carry a link field");
assert.match(optionForm, /data-action="pitch-option-link-lookup"/);
assert.match(optionForm, /Link lesen und übernehmen/);

{
  const fields = {
    url: { value: "https://park4night.com/lieu/513700/" },
    name: { value: "" },
    latitude: { value: "" },
    longitude: { value: "" },
    place_query: { value: "" },
    notes: { value: "" },
  };
  const fakeForm = {
    dataset: { dayId: "day-2", sourceUrl: "" },
    querySelector: (selector) => {
      const match = /\[name='([^']+)'\]|input\[name='([^']+)'\]/.exec(selector);
      const key = match && (match[1] || match[2]);
      return key ? fields[key] || null : null;
    },
  };
  panel._runAction = async (action, data) => {
    assert.equal(action, "place_link_lookup");
    assert.equal(data.url, "https://park4night.com/lieu/513700/");
    return { result: { name: "Skogsglänta", latitude: 60.19, longitude: 17.62, rating_text: "4.5/5" } };
  };
  panel.toasts = [];
  panel._showToast = (message, type) => panel.toasts.push({ message, type });
  await panel._runPitchOptionLinkLookup(fakeForm);
  assert.equal(fields.name.value, "Skogsglänta");
  assert.equal(fields.latitude.value, "60.19");
  assert.equal(fields.longitude.value, "17.62");
  assert.equal(fields.place_query.value, "60.19,17.62");
  assert.match(fields.notes.value, /Bewertung 4.5\/5/);
  assert.equal(panel.toasts[0].type, "success");
}

console.log("Pitch option link lookup UI tests passed.");
