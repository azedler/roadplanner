import assert from "node:assert/strict";

// Regression test: media.js referenced the module-level WS_ACTION constant
// without importing it. Each mixin file is its own ES module merged onto a
// shared prototype via Object.assign - importing it in roadplanner-panel.js
// does NOT make it available inside media.js. This never crashed visibly
// for the background auto-populate call (silently swallowed by its own
// try/catch), but surfaced as "Bildsuche fehlgeschlagen / Can't find
// variable: WS_ACTION" the moment a user actually triggered image search -
// for a normal stop's "Bilder verwalten" just as much as for a pitch
// option's new "Bilder" button.

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

const { WS_ACTION } = await import(
  new URL("../custom_components/roadplanner_mcp/frontend/lib/constants.js", import.meta.url)
);
await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);

const Panel = registry.get("roadplanner-panel");
const panel = new Panel();
panel._render = () => {};
panel._busy = false;
panel._findStop = () => null;
panel._destinationGalleryForStop = () => null;
panel._destinationGalleryImages = () => [];

let sentMessage = null;
panel._send = async (message) => {
  sentMessage = message;
  return { query: "Test", results: [], provider_errors: {} };
};

await panel._searchImages({ targetType: "stop", dayId: "day-1", stopId: "stop-1", query: "Test" });

assert.ok(sentMessage, "the WebSocket call must actually be sent, not throw before reaching it");
assert.equal(sentMessage.type, WS_ACTION, "media.js must use the real imported WS_ACTION constant");
assert.equal(sentMessage.action, "search_destination_images");

console.log("Media search WS_ACTION regression test passed.");
