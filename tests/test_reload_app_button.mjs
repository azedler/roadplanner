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
let reloadCalls = 0;
globalThis.window = {
  location: { origin: "https://ha.example", reload: () => { reloadCalls += 1; } },
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

await import(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url)
);

const Panel = registry.get("roadplanner-panel");
const panel = new Panel();
panel._render = () => {};

const fakeTarget = { dataset: { action: "reload-app" }, classList: { contains: () => false } };
const fakeEvent = { target: { closest: (selector) => (selector.includes("data-action") ? fakeTarget : null) } };

// No dialog open: reload happens immediately, no confirmation needed.
reloadCalls = 0;
panel._dialog = null;
panel._handleClick(fakeEvent);
assert.equal(reloadCalls, 1, "with no open dialog, the app reload must happen immediately");

// A dialog/form is open: reload must be gated behind a confirmation so
// in-progress typed input isn't silently discarded.
reloadCalls = 0;
panel._dialog = { type: "stop", mode: "add", stop: {}, dayId: "day-1", revision: 1 };
let confirmed = null;
panel._confirm = (title, message, label, callback) => { confirmed = { title, message, callback }; };
panel._handleClick(fakeEvent);
assert.equal(reloadCalls, 0, "reload must not happen before the user confirms");
assert.ok(confirmed, "an open dialog must trigger a confirmation before reloading");
confirmed.callback();
assert.equal(reloadCalls, 1, "confirming must trigger exactly one reload");

// The button itself must be in the topbar, distinct from the data-only
// "Neu laden" refresh button, and visible on narrow (mobile) widths where
// pull-to-refresh doesn't reach the app shell.
const fs = await import("node:fs");
const source = fs.readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf8",
);
assert.match(source, /data-action="reload-app"/);
assert.match(source, /data-action="refresh"[\s\S]{0,40}aria-label="Daten neu laden"/);
const styles = fs.readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/lib/styles.js", import.meta.url),
  "utf8",
);
assert.doesNotMatch(
  styles,
  /\[data-action="reload-app"\]\s*\{\s*display:\s*none/,
  "the app-reload button must stay visible at the mobile breakpoints where the bug actually shows up",
);

console.log("Reload-app button tests passed.");
