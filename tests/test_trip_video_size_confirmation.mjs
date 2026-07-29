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
const createdLinks = [];
globalThis.document = {
  createElement(tag) {
    const el = { tag, setAttribute() {}, style: {}, select() {}, remove() {}, click() { el.clicked = true; } };
    if (tag === "a") createdLinks.push(el);
    return el;
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
let toasts = [];
panel._showToast = (message) => { toasts.push(message); };

panel._hass = {
  connection: {
    async sendMessagePromise() {
      return { result: { download_url: "/api/roadplanner/trip_video_library/abc.mp4", size_bytes: 42 * 1024 * 1024 } };
    },
  },
};

let confirmed = null;
panel._confirm = (title, message, label, callback) => { confirmed = { title, message, label, callback }; };

await panel._exportTripVideo();

assert.equal(createdLinks.length, 0, "the video must not auto-download before the user confirms the size");
assert.ok(confirmed, "a size confirmation dialog must be shown");
assert.match(confirmed.message, /42\.0 MB/);
assert.match(toasts.join(" "), /42\.0 MB/, "the toast should also mention the size");

confirmed.callback();
assert.equal(createdLinks.length, 1, "confirming must trigger exactly one download link click");
assert.equal(createdLinks[0].clicked, true);

console.log("Trip video size confirmation tests passed.");
