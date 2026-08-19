/**
 * The product shape: what is on the front page, and what the tablet does.
 *
 * Roadplanner spent a long time growing instruments, and every one of
 * them ended up on the same surface as the trip. This checks the tidy-up
 * held: four primary places, the technical drawers behind "Mehr", the
 * film block down to two decisions, and a player that shows the film and
 * nothing else.
 *
 * Rendered from the real panel with a real payload rather than asserted
 * against source text. A regex over the file would pass on a tab that is
 * defined and never drawn - which is exactly the bug it would be there
 * to catch.
 */

import assert from "node:assert/strict";

const storage = new Map();
class FakeShadowRoot {
  addEventListener() {}
  querySelector() { return null; }
}
class FakeHTMLElement {
  attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; }
}
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

await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);
const Panel = registry.get("roadplanner-panel");
const panel = new Panel();
panel._render = () => {};
panel._data = {
  selected_trip_id: "trip",
  selected_is_active: true,
  capabilities: { can_edit: true },
  summary: { revision: 1, trip: { title: "Nordkap" } },
  days: { days: [] },
  handoffs: { total: 0, handoffs: [] },
  experience: { stats: {}, media: [], by_day: {}, by_stop: {}, decisions: [] },
  archive: { documents: [], expenses: [], todos: [], stats: {}, by_day: {}, by_stop: {} },
  settings: {},
  trips: { trips: [{ id: "trip", title: "Nordkap" }] },
};
panel._selectedTripId = "trip";

// --- navigation ---------------------------------------------------------

function primaryTabs(markup) {
  const nav = markup.split('class="tabs primary-tabs"')[1].split("</nav>")[0];
  return [...nav.matchAll(/<span>([^<]+)<\/span>/g)].map((match) => match[1]);
}

function drawerLabels(markup) {
  const drawer = markup.split('class="tool-tab-drawer"')[1];
  return [...drawer.matchAll(/<span>([^<]+)<\/span>/g)].map((match) => match[1]);
}

const navigation = panel._renderTabs();
assert.deepEqual(
  primaryTabs(navigation),
  ["Reise", "Tage", "Reisegeschichte", "Reisebegleiter"],
  "die vier festen Plätze der Hauptnavigation stimmen nicht",
);
// The rename, checked as an absence too: "Heute" promised one day and the
// view always showed the trip day by day.
assert.ok(!primaryTabs(navigation).includes("Heute"), navigation);
// Memories still exist - they moved, they were not removed. Both halves
// matter: gone from the front, present in the drawer.
assert.ok(!primaryTabs(navigation).includes("Erinnerungen"), "Erinnerungen belegt wieder einen Hauptplatz");
assert.ok(drawerLabels(navigation).includes("Erinnerungen"), "Erinnerungen ist ganz verschwunden");
assert.ok(drawerLabels(navigation).includes("Diagnose"), "die Diagnose ist nicht erreichbar");
for (const heading of ["Reise verwalten", "Medien &amp; Personen", "Daten &amp; Verwaltung", "Technik"]) {
  assert.ok(navigation.includes(heading), `die Gruppe „${heading}“ fehlt`);
}
// Deep links keep working: the ids are unchanged, only the labels and
// the grouping moved.
assert.ok(navigation.includes('data-tab="day-route"'), navigation);
assert.ok(navigation.includes('data-tab="media"'), navigation);
assert.ok(navigation.includes('data-tab="story"'), navigation);

// --- the film block -----------------------------------------------------

panel._storyFilm = {
  chapter_count: 14,
  planned_photo_count: 197,
  photos_per_chapter: 14,
  chapters_without_photos: 2,
  mapped_chapters: 12,
  film_seconds: 912,
};
panel._storyFilmProfiles = [
  { id: "review_480", label: "Review schnell · 480p", description: "", width: 854, height: 480, fps: 30, default: false },
  { id: "high_quality", label: "Hohe Qualität · 1440p", description: "", width: 2560, height: 1440, fps: 30, default: true },
];
panel._rendererAppStatus = { online: true };

const withoutMusic = panel._renderStoryFilm();
assert.ok(withoutMusic.includes("Film erstellen"), "die Hauptaktion heisst nicht mehr „Film erstellen“");
assert.ok(withoutMusic.includes('data-action="story-film-music-choice"'), "die Musikentscheidung fehlt");
assert.ok(withoutMusic.includes('data-action="story-film-profile"'), "die Qualitätswahl fehlt");
// Exactly two decisions in the film card. Anything else that grew a
// picker here would be a third.
const selects = [...withoutMusic.matchAll(/<select data-action="([^"]+)"/g)].map((m) => m[1]);
assert.deepEqual(selects.sort(), ["story-film-music-choice", "story-film-profile"], selects.join(", "));
// And none of the instruments.
for (const gone of ["Prüfausschnitt", "Musikarchitektur", "Bildzuteilung simulieren", "Ganze Reise prüfen"]) {
  assert.ok(!withoutMusic.includes(gone), `„${gone}“ steht wieder im normalen Filmblock`);
}
assert.ok(withoutMusic.includes("15:12"), "die Dauer fehlt in der Zusammenfassung");
assert.ok(withoutMusic.includes("Hohe Qualität · 1440p"), "die gewählte Qualität fehlt");
assert.ok(withoutMusic.includes("197 Bilder ausgewählt"), "die Medienzeile fehlt");
assert.ok(withoutMusic.includes("Ohne Musik"), withoutMusic);
// No cost line at all while there is no music: a "0,00 USD" beside a
// silent film invites the question it answers.
assert.ok(!withoutMusic.includes("Musikkosten"), withoutMusic);

// Music on, nothing generated yet: the price, from the real offer.
panel._storyFilmTrack = "__generated__";
panel._storyFilmMusicOfferData = {
  model: "lyria-3-pro-preview",
  sections: 5,
  cached: 0,
  new_generations: 5,
  estimated_cost: 0.4,
  price_per_generation: 0.08,
  currency: "USD",
  available: true,
};
const fresh = panel._renderStoryFilm();
assert.ok(fresh.includes("Geplante Musikabschnitte: 5"), fresh);
assert.ok(fresh.includes("ca. 0.40 USD"), fresh);
assert.ok(fresh.includes("wiederverwendet"), "der Hinweis auf die Wiederverwendung fehlt");
// Nothing here orders anything: the only paid button is the explicit one.
assert.ok(!fresh.includes('data-action="story-film-music-generate"'), fresh);

// Music already there: free, and said so.
panel._storyFilmMusicOfferData = {
  ...panel._storyFilmMusicOfferData,
  cached: 5,
  new_generations: 0,
  estimated_cost: 0,
};
const cached = panel._renderStoryFilm();
assert.ok(cached.includes("vorhandener Soundtrack"), cached);
assert.ok(cached.includes("0,00 USD"), cached);
assert.ok(cached.includes("0.00 USD"), "die Zusammenfassung nennt die Zusatzkosten nicht");
// A new variant is possible, and only as its own deliberate action.
assert.ok(cached.includes('data-action="story-film-music-regenerate"'), cached);

// Changing the size must not be able to order music. The film block sends
// a profile; the paid action is a different one entirely.
assert.ok(!/story_film_music_generate/.test(cached), cached);

// --- the player ---------------------------------------------------------

assert.equal(panel._playerModeActive(), false, "ohne Voreinstellung ist der Editor an");
panel._playerSetMode("player");
assert.equal(panel._playerModeActive(), true, "die Moduswahl wird nicht lokal gemerkt");
assert.equal(storage.get("roadplanner.ui-mode"), "player");

panel._playerLoaded = true;
panel._playerFilm = {
  url: "/api/roadplanner/trip_video_library/abc.mp4",
  duration_seconds: 912,
  has_music: true,
  width: 2560,
  height: 1440,
};
const playing = panel._renderPlayer();
assert.ok(playing.includes("<video"), "der Player zeigt kein Video");
assert.ok(/<video[^>]*\sloop/.test(playing), "die Endlosschleife ist nicht am Videoelement");
assert.ok(/<video[^>]*playsinline/.test(playing), "ohne playsinline geht iOS in den Vollbildplayer");
assert.ok(playing.includes("/api/roadplanner/trip_video_library/abc.mp4"), playing);
// No private path ever reaches the markup.
assert.ok(!playing.includes("/share/"), playing);
assert.ok(!playing.includes("roadplanner-renderer"), playing);
// The player presents; it does not administer.
for (const editorish of [
  "Film erstellen",
  "story-film-render",
  "story-film-qa-render",
  "renderer-app-cancel",
  "USD",
  "Diagnose",
]) {
  assert.ok(!playing.includes(editorish), `der Player zeigt „${editorish}“`);
}
for (const control of ["player-play", "player-mute", "player-restart", "player-fullscreen", "player-leave"]) {
  assert.ok(playing.includes(`data-action="${control}"`), `dem Player fehlt ${control}`);
}

// Autoplay refused: one large button, and no attempt to get around it.
panel._playerAutoplayBlocked = true;
const blocked = panel._renderPlayer();
assert.ok(blocked.includes('data-action="player-start"'), blocked);
assert.ok(blocked.includes("Film starten"), blocked);
assert.ok(!/<video[^>]*\smuted/.test(blocked), "der Player schaltet den Ton ab, statt zu fragen");
panel._playerAutoplayBlocked = false;

// No film: a sentence, not a workflow.
panel._playerFilm = null;
const empty = panel._renderPlayer();
assert.ok(empty.includes("noch kein Reisefilm"), empty);
assert.ok(!empty.includes("<video"), empty);
assert.ok(!empty.includes('data-action="story-film-render"'), "der Player bietet einen Render an");

// Not looked yet is a third state, and it must not read as "no film".
panel._playerLoaded = false;
const looking = panel._renderPlayer();
assert.ok(!looking.includes("noch kein Reisefilm"), looking);

// The trip preference survives, and only for a trip that still exists.
storage.set("roadplanner.player-trip", "verschwunden");
assert.equal(panel._playerTripId(), "trip", "eine gelöschte Reise wird weiter gemerkt");
storage.set("roadplanner.player-trip", "trip");
assert.equal(panel._playerTripId(), "trip");

// Leaving puts the device back in the editor, on this device only.
panel._playerSetMode("editor");
assert.equal(panel._playerModeActive(), false);
assert.equal(storage.get("roadplanner.ui-mode"), "editor");

console.log("Navigation and player tests passed.");

// --- the diagnosis view -------------------------------------------------
//
// Rendered with nothing loaded, which is how somebody will first open it:
// straight from the drawer, before the story page has ever been visited.
// Every block in here reads state the story editor fills in, and a view
// that throws on an empty panel is a view nobody can reach.
storage.clear();
// The renderer card probes the app when it first draws. There is no
// websocket here, and its failure is caught and logged - stubbed so the
// test output says what the test found rather than what the fake did not
// have.
panel._runAction = async () => null;
panel._activeTab = "diagnostics";
const diagnosis = panel._renderDiagnostics();
assert.ok(diagnosis.includes("Diagnose"), diagnosis);
for (const group of ["Film", "Musik", "Video", "Renderer"]) {
  assert.ok(diagnosis.includes(`>${group}</span>`), `die Gruppe „${group}“ fehlt in der Diagnose`);
}
// The instruments are HERE, which is the other half of removing them from
// the film block: moved, not deleted.
assert.ok(diagnosis.includes("Musikarchitektur") || diagnosis.includes("story-music-prototype"), diagnosis);
assert.equal(typeof panel._renderActiveTab, "function");

console.log("Diagnosis view tests passed.");
