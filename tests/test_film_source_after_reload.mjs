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

// --- #377: an excerpt is not the film, and a scored film is not silent ---

function panelWithJobs(jobs) {
  const panel = new Panel();
  panel._render = () => {};
  panel._rendererAppRedraw = () => {};
  panel._selectedTripId = "trip-a";
  panel._data = { capabilities: { can_edit: true }, selected_is_active: true };
  panel._storyFilmMusicOffer = async () => { panel._offerAsked = true; };
  panel._runAction = async (action) => {
    if (action !== "renderer_app_recent_jobs") return null;
    return {
      renderer_app_recent_jobs: jobs,
      renderer_app_active_job: null,
      renderer_app_result: null,
    };
  };
  return panel;
}

const filmJob = (overrides = {}) => ({
  job_id: JOB,
  kind: "trip_film",
  state: "completed",
  trip_id: "trip-a",
  has_audio: false,
  excerpt: false,
  source_job_id: "",
  ...overrides,
});

async function verify_a_quality_excerpt_is_not_adopted_as_the_film() {
  const panel = panelWithJobs([filmJob({ excerpt: true })]);
  await panel._rendererAppAdoptRunningJob();
  assert.ok(
    !panel._storyFilmSourceJobId,
    "a 65-second excerpt must not become the film music is laid onto",
  );
}

async function verify_the_whole_film_beside_an_excerpt_is_the_one_adopted() {
  const whole = "22222222-2222-4333-8444-555555555555";
  const panel = panelWithJobs([
    filmJob({ job_id: JOB, excerpt: true }),
    filmJob({ job_id: whole, excerpt: false }),
  ]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyFilmSourceJobId, whole);
  assert.equal(panel._storyFilmSourceIsExcerpt, false, "set through the setter, not by assignment");
}

async function verify_a_film_that_was_already_scored_is_not_offered_music_again() {
  const mux = "33333333-2222-4333-8444-555555555555";
  const panel = panelWithJobs([
    { ...filmJob({ job_id: mux, kind: "film_music", source_job_id: JOB }), has_audio: true },
    filmJob({ job_id: JOB, has_audio: false }),
  ]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyFilmSourceJobId, JOB, "the silent render stays the mux source");
  assert.equal(
    panel._storyFilmSourceHasAudio,
    true,
    "a film with a finished mux behind it is not silent any more",
  );
  assert.ok(!panel._offerAsked, "and nothing asks for a second helping of the same music");
}

async function verify_a_mux_of_another_film_does_not_count() {
  const mux = "33333333-2222-4333-8444-555555555555";
  const other = "44444444-2222-4333-8444-555555555555";
  const panel = panelWithJobs([
    { ...filmJob({ job_id: mux, kind: "film_music", source_job_id: other }), has_audio: true },
    filmJob({ job_id: JOB, has_audio: false }),
  ]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyFilmSourceHasAudio, false, "that mux belongs to a different render");
  assert.equal(panel._offerAsked, true, "so this film still gets its offer");
}

const checks = Object.entries({
  verify_the_recorded_film_becomes_the_source,
  verify_a_silent_film_asks_for_its_music_offer,
  verify_a_record_without_the_field_leaves_the_answer_unknown,
  verify_a_live_session_is_not_overwritten_by_the_record,
  verify_no_film_means_no_source,
  verify_a_trip_switch_drops_the_adopted_source,
  verify_a_quality_excerpt_is_not_adopted_as_the_film,
  verify_the_whole_film_beside_an_excerpt_is_the_one_adopted,
  verify_a_film_that_was_already_scored_is_not_offered_music_again,
  verify_a_mux_of_another_film_does_not_count,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
