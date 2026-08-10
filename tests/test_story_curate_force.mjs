/**
 * "Bildauswahl erneuern" never actually forced anything.
 *
 * The dispatcher for `media-curate-days` reads `force` off the clicked
 * button's own `data-force="1"` attribute (roadplanner-panel.js), the same
 * pattern `calculate-day-route` and `calculate-trip-routes` use. But the
 * curate button (story-editor.js `_renderStoryCuration`) never set that
 * attribute on any render - so `target.dataset.force` was always
 * `undefined`, `undefined === "1"` is always `false`, and every press of
 * "Bildauswahl erneuern" ran exactly like the very first, uncurated press.
 *
 * `DayCurationService` is faithful to that flag once it arrives (see
 * `verify_an_unchanged_day_is_never_paid_for_twice` in
 * test_day_curation_service.py): without force, a day whose pool has not
 * changed since its last look is served from cache with `analysed_count`
 * 0. So a real trip whose days 2-5 had already been looked at once, and
 * had not gained or lost a single photograph since, stayed at "analysiert
 * 0" through any number of presses - the button could never re-derive
 * them, no matter how many times "erneuern" was clicked.
 */

import assert from "node:assert/strict";

class FakeShadowRoot {
  addEventListener() {}
  querySelector() { return null; }
}

class FakeHTMLElement {
  attachShadow() {
    this.shadowRoot = new FakeShadowRoot();
    return this.shadowRoot;
  }
}

const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = { createElement() { return { setAttribute() {}, style: {} }; }, body: { appendChild() {} } };
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);

const Panel = registry.get("roadplanner-panel");

const makePanel = ({ curated }) => {
  const panel = new Panel();
  panel._data = {
    capabilities: { can_edit: true },
    selected_is_active: true,
    action_costs: {
      media_curate_days: { cost: "model", effect: "Lässt Gemini die Fotos ansehen.", note: "Verbraucht Kontingent." },
    },
    experience: {
      day_curations: curated ? { "day-1": { day_id: "day-1", media_ids: ["m1"] } } : {},
    },
  };
  return panel;
};

const CHAPTER = { chapter_id: "day-1" };

// --- a day already curated must ask the backend to actually re-derive it -

{
  const panel = makePanel({ curated: true });
  const html = panel._renderStoryCuration(CHAPTER);
  assert.match(html, /Bildauswahl erneuern/, "the renew label is shown for an already-curated day");
  const button = html.match(/<button[^>]*data-action="media-curate-days"[^>]*>/)[0];
  assert.match(
    button,
    /data-force="1"/,
    "the renew button must carry data-force=\"1\" - the dispatcher reads exactly that " +
      "attribute off the clicked element, and without it every press ran unforced and a " +
      "day with an unchanged pool could never be re-looked at",
  );
}

// --- a day with nothing curated yet needs no force - there is no cache to bypass -

{
  const panel = makePanel({ curated: false });
  const html = panel._renderStoryCuration(CHAPTER);
  assert.match(html, /Bilder auswählen lassen/);
  const button = html.match(/<button[^>]*data-action="media-curate-days"[^>]*>/)[0];
  assert.doesNotMatch(button, /data-force="1"/);
}

// --- the loop forces every round, but marks its own finished work ---------
//
// Force alone re-paid days 1-4 in every round and never reached day 5: the
// first days with photographs consumed the batch budget again and again
// until the daily limit was gone. The backend answers round 0 with its own
// start time (`run_marker`); every later round must send it back as
// `fresh_after` so this run's finished days are not forced twice.

{
  const panel = makePanel({ curated: true });
  panel._selectedTripId = "trip-1";
  panel._showToast = () => {};
  panel._loadData = async () => {};
  panel._storyLoad = async () => {};
  const payloads = [];
  let remaining = 2;
  panel._runAction = async (_action, data) => {
    payloads.push({ ...data });
    const result = {
      remaining,
      run_marker: "2026-08-10T09:00:00Z",
      selected_count: 3,
      pool_count: 5,
      unmet: {},
    };
    remaining = Math.max(0, remaining - 1);
    return result;
  };

  await panel._storyCurate({ force: true });

  assert.equal(payloads.length, 3, "remaining 2 -> 1 -> 0 takes three rounds");
  assert.ok(payloads.every((entry) => entry.force === true), "force goes out on EVERY round");
  assert.equal(payloads[0].fresh_after, undefined, "round 0 has no marker yet");
  assert.equal(payloads[1].fresh_after, "2026-08-10T09:00:00Z");
  assert.equal(payloads[2].fresh_after, "2026-08-10T09:00:00Z");
}

console.log("Story curate force tests passed.");
