/**
 * RP-417: the player shows the selected trip, and says so under its name.
 *
 * Live finding: after a reload the player stood there with a TEST trip's
 * title over the sentence "Für diese Reise wurde noch kein Reisefilm
 * erstellt", while the selected and active trip was the real journey -
 * whose film had just finished rendering. Read on a wall-mounted tablet,
 * that reads as "the film is gone", and that is exactly the wrong
 * conclusion somebody drew from it.
 *
 * Two things had come apart:
 *
 *  - The player's trip came out of `localStorage` and was honoured
 *    whenever that trip still existed, no matter which trip was selected.
 *  - A diagnostic asking the panel for `_playerMode` got `undefined`
 *    while the player was plainly on screen, because the mode was read
 *    straight out of storage on every render and no such field existed.
 *    Two answers to one question, one of them from a field nobody made.
 */
import assert from "node:assert/strict";

const storage = new Map();
class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = {
  location: { origin: "https://ha.example" },
  setTimeout,
  clearTimeout,
  localStorage: {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
  },
};
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

const REAL = "finnland-baltikum-2026";
const TEST_TRIP = "test-claude-oesterreich-2026-08-20";

function panelOn(selected, { films = {} } = {}) {
  const panel = new Panel();
  panel._render = () => {};
  panel._playerTryAutoplay = () => {};
  panel._selectedTripId = selected;
  panel._data = {
    capabilities: { can_edit: true },
    selected_is_active: true,
    summary: { trip: { title: "Finnland / Baltikum 2026" } },
    trips: {
      trips: [
        { id: REAL, title: "Finnland / Baltikum 2026" },
        { id: TEST_TRIP, title: "TEST — Claude Österreich 2026-08-20" },
      ],
    },
  };
  panel.asked = [];
  panel._runAction = async (action, payload) => {
    if (action !== "player_latest_film") return null;
    panel.asked.push(payload?.trip_id);
    return { player_latest_film: films[payload?.trip_id] || null };
  };
  return panel;
}

async function verify_a_remembered_trip_never_wins_over_the_selected_one() {
  storage.set("roadplanner.ui-mode", "player");
  storage.set("roadplanner.player-trip", TEST_TRIP);
  const panel = panelOn(REAL, { films: { [REAL]: { url: "/film.mp4", duration_seconds: 734 } } });
  assert.equal(panel._playerTripId(), REAL, "the player shows what is selected");
  await panel._playerLoadFilm();
  assert.deepEqual(panel.asked, [REAL], "and asks about that trip, not the remembered one");
  assert.ok(panel._renderPlayer().includes("Finnland / Baltikum 2026"));
  assert.ok(
    !panel._renderPlayer().includes("TEST — Claude Österreich"),
    "another trip's name must not appear over this trip's film",
  );
}

async function verify_the_leftover_key_is_cleared_rather_than_left_lying() {
  storage.set("roadplanner.ui-mode", "player");
  storage.set("roadplanner.player-trip", TEST_TRIP);
  const panel = panelOn(REAL);
  await panel._playerLoadFilm();
  assert.equal(
    storage.get("roadplanner.player-trip"),
    undefined,
    "a key the code no longer reads must not stay in the browser looking like state",
  );
  assert.equal(storage.get("roadplanner.ui-mode"), "player", "the MODE is still a device preference");
}

async function verify_the_mode_is_one_field_and_it_is_the_one_that_is_read() {
  storage.set("roadplanner.ui-mode", "player");
  const panel = panelOn(REAL);
  assert.equal(panel._playerModeActive(), true);
  assert.equal(panel._playerMode, true, "asking the panel for the mode gives the same answer");
  panel._playerSetMode("editor");
  assert.equal(panel._playerMode, false);
  assert.equal(panel._playerModeActive(), false);
  assert.equal(storage.get("roadplanner.ui-mode"), "editor");
}

async function verify_no_film_is_reported_under_the_trip_it_was_checked_for() {
  storage.set("roadplanner.ui-mode", "player");
  const panel = panelOn(TEST_TRIP);
  await panel._playerLoadFilm();
  const html = panel._renderPlayer();
  assert.ok(html.includes("noch kein Reisefilm erstellt"));
  assert.ok(html.includes("TEST — Claude Österreich"), "the sentence names the trip it is about");
  assert.equal(panel._playerFilmTripId, TEST_TRIP);
}

async function verify_a_trip_switch_does_not_leave_the_old_answer_standing() {
  storage.set("roadplanner.ui-mode", "player");
  const panel = panelOn(REAL, { films: { [REAL]: { url: "/film.mp4" } } });
  await panel._playerLoadFilm();
  assert.ok(panel._renderPlayer().includes("<video"), "the real trip has its film");
  panel._selectedTripId = TEST_TRIP;
  panel._storyResetForTrip();
  assert.equal(panel._playerFilm, null, "the previous trip's film is not the next trip's film");
  assert.ok(
    panel._renderPlayer().includes("wird gesucht"),
    "the player looks again instead of showing a stale answer",
  );
}

async function verify_an_answer_that_arrives_after_a_switch_is_dropped() {
  storage.set("roadplanner.ui-mode", "player");
  const panel = panelOn(REAL);
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  panel._runAction = async () => {
    await pending;
    return { player_latest_film: { url: "/old-trip.mp4" } };
  };
  const loading = panel._playerLoadFilm();
  panel._selectedTripId = TEST_TRIP;
  release();
  await loading;
  assert.equal(panel._playerFilm, undefined, "a late answer belongs to the trip that asked for it");
}

const checks = Object.entries({
  verify_a_remembered_trip_never_wins_over_the_selected_one,
  verify_the_leftover_key_is_cleared_rather_than_left_lying,
  verify_the_mode_is_one_field_and_it_is_the_one_that_is_read,
  verify_no_film_is_reported_under_the_trip_it_was_checked_for,
  verify_a_trip_switch_does_not_leave_the_old_answer_standing,
  verify_an_answer_that_arrives_after_a_switch_is_dropped,
});

for (const [name, check] of checks) {
  storage.clear();
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
