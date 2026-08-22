/**
 * RP-415: when a film cannot start, the card says WHY - the server's why.
 *
 * The old text was a guess, and always the same guess: "Die Renderer-App
 * läuft nicht oder ist noch nicht bereit. Der genaue Grund steht im
 * Home-Assistant-Protokoll." On a live system every word of that was
 * wrong at once - the app was ready, its own test render worked, the log
 * held no line about it, and the real reason ("Bildposition liegt
 * außerhalb des erlaubten Bereichs") had been thrown away with the null
 * that `_runAction` returns for every failure. Three days were spent
 * checking a service that had nothing wrong with it.
 *
 * So: the reason travels, and the guess survives only where it was ever
 * true - a failure that carried no message at all.
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
const panel = new Panel();
panel._render = () => {};
panel._data = { capabilities: { can_edit: true }, selected_is_active: true };
panel._selectedTripId = "reise-a";

const GUESS = /Renderer-App läuft nicht/;
const REAL = "Bildposition liegt außerhalb des erlaubten Bereichs";

// --- the helper itself --------------------------------------------------
panel._lastActionError = { action: "story_film_render", message: REAL, code: "roadplanner_error" };
const explained = panel._storyFilmStartReason("story_film_render", "FALLBACK");
assert.match(explained, new RegExp(REAL), "the server's reason must reach the card");
assert.doesNotMatch(explained, /FALLBACK/);
assert.match(explained, /nicht von der Renderer-App/, "and must not read as a renderer fault");

// A failure from a DIFFERENT action is not this card's explanation. The
// music offer and the status probe fail on their own schedule; borrowing
// their message would explain the film with something unrelated.
panel._lastActionError = { action: "story_film_music_offer", message: "Musik nicht lesbar", code: "" };
assert.equal(panel._storyFilmStartReason("story_film_render", "FALLBACK"), "FALLBACK");

// No message at all: the old text, which was written for exactly this.
panel._lastActionError = null;
assert.equal(panel._storyFilmStartReason("story_film_render", "FALLBACK"), "FALLBACK");
panel._lastActionError = { action: "story_film_render", message: "   ", code: "" };
assert.equal(panel._storyFilmStartReason("story_film_render", "FALLBACK"), "FALLBACK");

// --- the whole submit path ---------------------------------------------
async function failingSubmit(kind) {
  panel._storyFilmStartError = "";
  panel._lastActionError = { action: kind, message: REAL, code: "roadplanner_error" };
  panel._runAction = async () => null;          // exactly what a failure returns
  panel._storyFilmChosen = () => "high_quality";
  panel._storyFilmProfileTable = () => [];
  if (kind === "story_film_render") await panel._storyFilmRenderSubmit();
  else await panel._storyFilmQaRender();
  return panel._storyFilmStartError;
}

for (const kind of ["story_film_render", "story_film_qa_render"]) {
  const shown = await failingSubmit(kind);
  assert.match(shown, new RegExp(REAL), `${kind}: the card still hides the real reason`);
  assert.doesNotMatch(shown, GUESS, `${kind}: the card still blames the renderer app`);
}

// And the fallback still appears when there is genuinely nothing to say.
panel._storyFilmStartError = "";
panel._lastActionError = null;
panel._runAction = async () => null;
await panel._storyFilmRenderSubmit();
assert.match(panel._storyFilmStartError, GUESS, "a silent failure loses its explanation");

// --- the error is captured where every action passes ---------------------
// Pinned in the shared runner, not only in the film card: the next card
// that has to explain a failure in place should find the reason already
// there rather than inventing another guess.
const source = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf-8",
);
assert.match(source, /this\._lastActionError = \{ action, message/, "_runActionNow must keep the reason");
assert.match(source, /this\._lastActionError = null;/, "and must forget it after a success");

// --- and the app's absence is explained by the thing that made it absent -
// RP-420: the card printed "Die Renderer-App ist nicht erreichbar (ready)"
// - the state a dead app last claimed for itself, beside the statement
// that contradicts it. What makes it unreachable is the heartbeat going
// quiet, so that is what the sentence has to say.
const reasons = new Panel();
assert.equal(
  reasons._rendererOfflineReason({ state: "ready", fresh: false, age_seconds: 1107 }),
  "letztes Lebenszeichen vor 18 Minuten",
  "the silence is the reason, not the last thing it said before it",
);
assert.equal(
  reasons._rendererOfflineReason({ state: "ready", fresh: false, age_seconds: 42 }),
  "letztes Lebenszeichen vor 42 Sekunden",
);
assert.equal(
  reasons._rendererOfflineReason({ state: "ready", fresh: false, age_seconds: 7200 }),
  "letztes Lebenszeichen vor 2 Stunden",
);
assert.equal(
  reasons._rendererOfflineReason({
    state: null,
    reason: "Kein Heartbeat gefunden - App vermutlich nicht installiert.",
  }),
  "Kein Heartbeat gefunden - App vermutlich nicht installiert.",
  "a real explanation is never overwritten by a derived one",
);
assert.equal(
  reasons._rendererOfflineReason({ state: "stopping", fresh: true }),
  "Zustand: stopping",
  "a beating app that says it is stopping HAS explained itself",
);
assert.equal(reasons._rendererOfflineReason({}), "kein Lebenszeichen");
assert.ok(
  !reasons._rendererOfflineReason({ state: "ready", fresh: false, age_seconds: 1107 }).includes("ready"),
  "the contradiction must not come back",
);

console.log("Film start failure explanation tests passed.");
