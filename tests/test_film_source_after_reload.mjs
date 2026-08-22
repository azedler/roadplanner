/**
 * After a reload the panel still knows which job made the film on screen.
 *
 * Live finding (RP-420 F3): the source job id was only ever set during a
 * render session, and the renderer's recent-job list ages out within the
 * day. So after a reload "Musik auflegen" and "Review-Kopie" were simply
 * gone - no message, no explanation - while the finished film played in
 * the card right above them. The player's own record knew the job id the
 * whole time.
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

const JOB = "11111111-2222-4333-8444-555555555555";
const LIVE_JOB = "99999999-2222-4333-8444-555555555555";

function freshPanel(film) {
  const panel = new Panel();
  panel._render = () => {};
  panel._selectedTripId = "trip-a";
  panel._data = { capabilities: { can_edit: true }, selected_is_active: true };
  panel._storyFilmMusicOffer = async () => { panel._offerAsked = true; };
  panel._runAction = async (action) => {
    if (action !== "player_latest_film") return null;
    return { player_latest_film: film };
  };
  return panel;
}

async function verify_the_recorded_film_becomes_the_source() {
  const panel = freshPanel({ job_id: JOB, url: "/x.mp4", has_music: true, duration_seconds: 734 });
  await panel._storyLoadLatestFilm();
  assert.equal(panel._storyFilmSourceJobId, JOB, "the buttons have something to point at again");
  assert.equal(panel._storyFilmSourceIsExcerpt, false, "the recorded film is never an excerpt");
  assert.equal(panel._storyFilmSourceHasAudio, true, "measured on the file, not guessed");
}

async function verify_a_silent_film_asks_for_its_music_offer() {
  const panel = freshPanel({ job_id: JOB, url: "/x.mp4", has_music: false });
  await panel._storyLoadLatestFilm();
  assert.equal(panel._storyFilmSourceHasAudio, false);
  assert.equal(panel._offerAsked, true, "a silent film should be able to offer 'Musik auflegen'");
}

async function verify_a_record_without_the_field_leaves_the_answer_unknown() {
  const panel = freshPanel({ job_id: JOB, url: "/x.mp4" });
  await panel._storyLoadLatestFilm();
  assert.equal(panel._storyFilmSourceJobId, JOB);
  assert.equal(
    panel._storyFilmSourceHasAudio,
    undefined,
    "not measured must never be spoken as 'silent'",
  );
}

async function verify_a_live_session_is_not_overwritten_by_the_record() {
  const panel = freshPanel({ job_id: JOB, url: "/x.mp4", has_music: true });
  panel._storyFilmSetSource(LIVE_JOB, { isExcerpt: true });
  await panel._storyLoadLatestFilm();
  assert.equal(panel._storyFilmSourceJobId, LIVE_JOB, "what is rendering now knows better");
  assert.equal(panel._storyFilmSourceIsExcerpt, true);
}

async function verify_no_film_means_no_source() {
  const panel = freshPanel(null);
  await panel._storyLoadLatestFilm();
  assert.equal(panel._storyLatestFilm, null);
  assert.ok(!panel._storyFilmSourceJobId, "nothing to point at, so nothing is claimed");
}

async function verify_a_trip_switch_drops_the_adopted_source() {
  const panel = freshPanel({ job_id: JOB, url: "/x.mp4", has_music: true });
  await panel._storyLoadLatestFilm();
  panel._storyResetForTrip();
  assert.ok(!panel._storyFilmSourceJobId, "one trip's film is not the next trip's source");
  assert.equal(panel._storyLatestFilm, undefined);
}

const checks = Object.entries({
  verify_the_recorded_film_becomes_the_source,
  verify_a_silent_film_asks_for_its_music_offer,
  verify_a_record_without_the_field_leaves_the_answer_unknown,
  verify_a_live_session_is_not_overwritten_by_the_record,
  verify_no_film_means_no_source,
  verify_a_trip_switch_drops_the_adopted_source,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
