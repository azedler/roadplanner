import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

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

const source = await readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf8",
);
vm.runInThisContext(source, { filename: "roadplanner-panel.js" });

const Panel = registry.get("roadplanner-panel");
assert.ok(Panel, "Roadplanner panel must register its custom element");

const panel = new Panel();
panel._selectedTripId = "trip-1";
panel._setBusy = () => {};
panel._render = () => {};
panel._renderToastHost = () => {};
panel._showToast = () => {};
let loadCount = 0;
panel._loadData = async () => { loadCount += 1; };
panel._hass = {
  connection: {
    async sendMessagePromise() {
      return { result: { ok: true } };
    },
  },
};

const fastResult = await panel._runAction(
  "assistant_chat",
  { trip_id: "trip-1", text: "Hallo" },
  "",
  { refresh: false },
);
assert.deepEqual(fastResult, { ok: true });
assert.equal(loadCount, 0, "assistant fast path must not reload the full panel payload");

panel._hass.connection.sendMessagePromise = async () => {
  throw new Error("Gemini antwortete nicht rechtzeitig (Anfrage chat-abc123)");
};
const failed = await panel._runAction(
  "assistant_chat",
  {},
  "",
  {
    refresh: false,
    errorMode: "dialog",
    errorTitle: "Assistent konnte nicht antworten",
  },
);
assert.equal(failed, null);
assert.equal(panel._dialog.type, "action-error");
assert.equal(panel._dialog.requestId, "chat-abc123");
assert.match(panel._renderActionErrorDialog(panel._dialog), /Details kopieren/);
assert.match(panel._renderActionErrorDialog(panel._dialog), /Gemini antwortete nicht rechtzeitig/);

const pending = panel._renderAssistantPending({ text: "Plane morgen", created_at: "2026-07-20T12:00:00Z" });
assert.match(pending, /Roadplanner denkt/);
assert.match(pending, /Plane morgen/);

panel._decisionCreateInFlightMessageId = "msg-1";
const decisionButton = panel._renderAssistantMessage({
  id: "msg-1",
  role: "assistant",
  created_at: "2026-07-20T12:00:00Z",
  content: "Drei Optionen",
});
assert.match(decisionButton, /Vorlage wird erstellt/);
assert.match(decisionButton, /disabled/);

console.log("Assistant interaction and persistent error tests passed.");
