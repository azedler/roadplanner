import assert from "node:assert/strict";

// Regression test: briefly leaving the app while the assistant works must
// not surface "Änderungsentwurf konnte nicht erstellt werden / Connection
// lost". Backgrounding the mobile app kills the WebSocket, but
// assistant_prepare (and the other provider calls) are shielded
// server-side and run to completion - the draft/handoff arrives anyway.
// The old error dialog made users retry and produced duplicate handoffs.
// A connection-lost failure on a server-continuing action now shows a calm
// "läuft auf dem Server weiter" toast and schedules a re-check; every
// other action and every other error keeps the loud error path.

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
const timeouts = [];
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = {
  location: { origin: "https://ha.example" },
  setTimeout: (fn, ms) => { timeouts.push({ fn, ms }); return timeouts.length; },
  clearTimeout: () => {},
};
globalThis.document = {
  createElement() { return { setAttribute() {}, style: {}, select() {}, remove() {} }; },
  body: { appendChild() {} },
  execCommand() { return true; },
};
Object.defineProperty(globalThis, "navigator", { value: { clipboard: { async writeText() {} } }, configurable: true });
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));

const Panel = registry.get("roadplanner-panel");

const makePanel = () => {
  const panel = new Panel();
  panel._render = () => {};
  panel._loadDataCalls = 0;
  panel._loadData = async () => { panel._loadDataCalls += 1; };
  panel.toasts = [];
  panel._showToast = (message, type) => panel.toasts.push({ message, type });
  panel.errorDialogs = [];
  panel._showActionError = (message, meta) => panel.errorDialogs.push({ message, meta });
  return panel;
};

const connectionLost = () => {
  const error = new Error("Connection lost");
  error.code = 3;
  return error;
};

// --- assistant_prepare + connection lost => calm toast, no dialog -------

{
  const panel = makePanel();
  panel._send = async () => { throw connectionLost(); };
  const result = await panel._runAction("assistant_prepare", {}, "", {
    refresh: false, errorMode: "dialog", errorTitle: "Änderungsentwurf konnte nicht erstellt werden",
  });
  assert.equal(result, null);
  assert.equal(panel.errorDialogs.length, 0, "no scary error dialog for a shielded action");
  assert.equal(panel.toasts.length, 1);
  assert.ok(panel.toasts[0].message.includes("arbeitet auf dem Server weiter"), panel.toasts[0].message);
  assert.ok(panel.toasts[0].message.includes("nicht erneut starten"), "the toast must warn against retrying (duplicates)");
  assert.equal(timeouts.length >= 1, true, "a delayed re-check is scheduled");
  timeouts.at(-1).fn();
  await Promise.resolve();
  assert.equal(panel._loadDataCalls, 1, "the re-check reloads so the arrived handoff shows up");
}

// --- ordinary actions keep the loud error path --------------------------

{
  const panel = makePanel();
  panel._send = async () => { throw connectionLost(); };
  await panel._runAction("update_stop", {}, "", { refresh: false });
  assert.equal(panel.toasts.length, 1);
  assert.equal(panel.toasts[0].type, "error", "a normal action still errors loudly on connection loss");
}

// --- other errors on assistant_prepare keep the dialog ------------------

{
  const panel = makePanel();
  panel._send = async () => { throw new Error("Der Änderungskorb ist leer"); };
  await panel._runAction("assistant_prepare", {}, "", {
    refresh: false, errorMode: "dialog", errorTitle: "Änderungsentwurf konnte nicht erstellt werden",
  });
  assert.equal(panel.errorDialogs.length, 1, "real failures still show the dialog");
}

console.log("Connection-lost handling tests passed.");
