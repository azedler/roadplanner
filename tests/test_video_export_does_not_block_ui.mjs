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
globalThis.window = {
  location: { origin: "https://ha.example" },
  setTimeout,
  clearTimeout,
};
globalThis.document = {
  createElement() {
    return {
      setAttribute() {},
      style: {},
      select() {},
      remove() {},
    };
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
panel._selectedTripId = "trip-1";
panel._render = () => {};
panel._renderToastHost = () => {};
panel._showToast = () => {};
panel._loadData = async () => {};

// Simulate a slow, still-pending export_trip_video call (real ffmpeg encodes take minutes).
let releaseVideoCall;
const videoPending = new Promise((resolve) => { releaseVideoCall = resolve; });

panel._hass = {
  connection: {
    async sendMessagePromise(message) {
      if (message.action === "export_trip_video") {
        await videoPending;
        return { result: { download_url: "/api/roadplanner/trip_video_library/abc.mp4" } };
      }
      return { result: { ok: true } };
    },
  },
};

const videoCallPromise = panel._runAction(
  "export_trip_video",
  { trip_id: "trip-1", style: "highlight" },
  "",
  { refresh: false, errorTitle: "Video konnte nicht erstellt werden", blockUi: false },
);

// While the video export is still in flight, the panel must not report itself as busy...
assert.equal(panel._busy, false, "a non-blocking action must not set the global busy flag");

// ...and an unrelated action (assistant chat) must be able to run concurrently.
const chatResult = await panel._runAction(
  "assistant_chat",
  { trip_id: "trip-1", text: "Bist du da?" },
  "",
  { refresh: false },
);
assert.deepEqual(chatResult, { ok: true }, "assistant chat must not be blocked by an in-flight video export");

releaseVideoCall();
const videoResult = await videoCallPromise;
assert.equal(videoResult.download_url, "/api/roadplanner/trip_video_library/abc.mp4");
assert.equal(panel._busy, false);

// A failure notice sticks around until the next run. When it was produced
// by an older version, the notice has to say so - the same text can come
// from a bug that is already fixed (live report: a screenshot of an error
// message that the running release can no longer even produce).
panel._data = { ...(panel._data || {}), integration_version: "4.19.1" };
panel._tripVideoStatus = {
  state: "error",
  error: "OneDrive hat eine unlesbare Antwort geliefert",
  integration_version: "4.18.2",
};
const staleNotice = panel._renderTripVideoStatusLine();
assert.match(staleNotice, /aus Version 4\.18\.2/);
assert.match(staleNotice, /jetzt läuft 4\.19\.1/);

panel._tripVideoStatus = {
  state: "error",
  error: "Frisch fehlgeschlagen",
  integration_version: "4.19.1",
};
assert.doesNotMatch(
  panel._renderTripVideoStatusLine(),
  /aus Version/,
  "a failure from the running version must not be labelled stale",
);


console.log("Video export does not block other panel actions.");
