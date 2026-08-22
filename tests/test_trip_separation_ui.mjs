/**
 * Trips own their renderer jobs - the panel half of the audit fixes.
 *
 * The story tab used to adopt "the newest job, whoever made it": the job
 * line, the film source and the mix buttons then all operated on another
 * trip's film, and a trip switch carried all of it along. Every job now
 * says which trip submitted it, and the tab adopts only its own.
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

function freshPanel(selectedTrip, jobs, { active = null } = {}) {
  const panel = new Panel();
  panel._render = () => {};
  panel._rendererAppRedraw = () => {};
  panel._selectedTripId = selectedTrip;
  panel._data = { capabilities: { can_edit: true }, selected_is_active: true };
  panel._runAction = async (action) => {
    if (action !== "renderer_app_recent_jobs") return null;
    return {
      renderer_app_recent_jobs: jobs,
      renderer_app_active_job: active,
      renderer_app_result: null,
    };
  };
  panel._pollRendererAppJob = (jobId) => { panel._polled = jobId; };
  panel._storyFilmMusicOffer = async () => { panel._offerLoaded = true; };
  return panel;
}

const finnlandFilm = {
  job_id: "film-finnland", kind: "trip_film", state: "completed",
  terminal: true, trip_id: "finnland", has_audio: false,
};
const testFilm = {
  job_id: "film-test", kind: "trip_film", state: "completed",
  terminal: true, trip_id: "test-reise", has_audio: false,
};
const legacyFilm = {
  job_id: "film-legacy", kind: "trip_film", state: "completed",
  terminal: true, trip_id: "", has_audio: false,
};

// --- the film source is only ever the own trip's film -------------------
{
  // Newest first: the test trip's film is newer, exactly the live shape.
  const panel = freshPanel("finnland", [testFilm, legacyFilm, finnlandFilm]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyFilmSourceJobId, "film-finnland",
    "the film source must be the own trip's film, not the newest one");
  assert.equal(panel._rendererAppJob?.job_id, "film-finnland");
}

// A trip with no own film adopts NOTHING - not the stranger's, not the
// legacy one whose ownership is unprovable.
{
  const panel = freshPanel("neue-reise", [testFilm, legacyFilm, finnlandFilm]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyFilmSourceJobId ?? "", "",
    "a trip without a film adopted a stranger's film");
  assert.equal(panel._rendererAppJob ?? null, null);
  assert.notEqual(panel._offerLoaded, true,
    "the silent stranger's film triggered this trip's music offer");
}

// --- a running render of another trip is not this trip's job line ------
{
  const running = {
    job_id: "run-test", kind: "trip_film", state: "rendering",
    terminal: false, trip_id: "test-reise",
  };
  const panel = freshPanel("finnland", [running, finnlandFilm], { active: running });
  await panel._rendererAppAdoptRunningJob();
  assert.notEqual(panel._rendererAppJob?.job_id, "run-test",
    "another trip's running render was adopted as this trip's job");
  assert.notEqual(panel._polled, "run-test",
    "the panel polls a job that is not its own");
  // The own finished film is still found underneath it.
  assert.equal(panel._storyFilmSourceJobId, "film-finnland");
}

// --- mix jobs of another trip stay theirs -------------------------------
{
  const foreignMix = {
    job_id: "mix-test", kind: "film_music", state: "completed",
    terminal: true, trip_id: "test-reise", music_variant: "a",
  };
  const ownMix = {
    job_id: "mix-finnland", kind: "film_music", state: "completed",
    terminal: true, trip_id: "finnland", music_variant: "a",
  };
  const panel = freshPanel("finnland", [foreignMix, ownMix, finnlandFilm]);
  await panel._rendererAppAdoptRunningJob();
  assert.equal(panel._storyMusicPrototypeJobs?.a, "mix-finnland",
    "another trip's mix was adopted for the comparison buttons");
}

// --- the trip switch clears the renderer block --------------------------
{
  const panel = freshPanel("finnland", []);
  panel._rendererAppJob = { job_id: "film-finnland" };
  panel._rendererAppKind = "trip_film";
  panel._rendererAppRecent = [finnlandFilm];
  panel._rendererAppRecentAsked = true;
  panel._rendererAppAdoptTried = true;
  panel._storyFilmSourceJobId = "film-finnland";
  panel._storyFilmSourceHasAudio = false;
  panel._storyMusicPrototypeJobs = { a: "mix-finnland" };
  panel._storyFilmMuxAfterJobId = "job-armed";
  panel._storyFilmMuxAfterTripId = "finnland";

  panel._storyResetForTrip();

  assert.equal(panel._rendererAppJob, null, "the adopted job survived the switch");
  assert.equal(panel._storyFilmSourceJobId, "", "the film source survived the switch");
  assert.equal(panel._storyFilmSourceHasAudio, undefined);
  assert.deepEqual(panel._storyMusicPrototypeJobs, {});
  assert.deepEqual(panel._rendererAppRecent, []);
  assert.equal(panel._rendererAppAdoptTried, false,
    "the next story tab open would not re-adopt for the new trip");
  // The armed mux intent deliberately survives: it carries its own trip
  // and job, and a switch must not cancel a decision already paid for.
  assert.equal(panel._storyFilmMuxAfterJobId, "job-armed");
  assert.equal(panel._storyFilmMuxAfterTripId, "finnland");
}

console.log("Trip separation UI tests passed.");
