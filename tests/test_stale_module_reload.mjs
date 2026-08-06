/**
 * The "update the app" button has to actually beat the cache.
 *
 * Live report: "Dein Mechanismus zur Cache Aktualisierung funktioniert
 * also nicht. Ich hatte schon auf deinen Button gedrückt als der mir das
 * veraltete frontend zeigte. Nach 'von oben nach unten ziehen' ist jetzt
 * der neue Check da."
 *
 * Reloading the document was never the problem: Home Assistant fetches the
 * panel module from a URL the backend supplies, so that URL is already
 * correct after an update. What was stale is the FETCH - the frontend's
 * service worker answers it from cache, and several of its routes match
 * while ignoring the query string, so a fresh "?v=4.32.0" is served from
 * the entry stored for the previous version. A cache-busting parameter on
 * the document cannot reach that.
 */
import assert from "node:assert/strict";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;

// --- a service worker cache holding the PREVIOUS panel module ----------
const deleted = [];
const cacheEntries = {
  "roadplanner-static": [
    { url: "https://ha.example/api/roadplanner/panel/roadplanner-panel.js?v=4.31.1" },
    { url: "https://ha.example/api/roadplanner/panel/features/crew.js?v=4.31.1" },
    { url: "https://ha.example/static/frontend/other-app-asset.js" },
  ],
};
let updated = 0;
let replacedWith = "";

globalThis.window = {
  location: {
    href: "https://ha.example/roadplanner-panel/dashboard",
    replace(value) { replacedWith = value; },
    reload() { replacedWith = "RELOAD"; },
  },
  setTimeout,
  clearTimeout,
  caches: {
    async keys() { return Object.keys(cacheEntries); },
    async open(name) {
      return {
        async keys() { return cacheEntries[name] || []; },
        async delete(request, options) {
          deleted.push({ url: request.url, ignoreSearch: options?.ignoreSearch });
          return true;
        },
      };
    },
  },
};
// Node 22 exposes a read-only `navigator`, so it has to be redefined.
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    serviceWorker: {
      async getRegistrations() {
        return [{ async update() { updated += 1; } }];
      },
    },
  },
});
globalThis.document = {
  createElement() { return { setAttribute() {}, style: {}, remove() {} }; },
  body: { appendChild() {} },
};
globalThis.customElements = {
  define(name, constructor) { registry.set(name, constructor); },
  get(name) { return registry.get(name); },
};

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const panel = new (registry.get("roadplanner-panel"))();

panel._data = { capabilities: { can_edit: true }, settings: {} };
panel._dialog = null;
panel._render = () => {};

const click = (action) => {
  const target = {
    dataset: { action },
    closest() { return target; },
    classList: { contains: () => false },
  };
  panel._handleClick({ target });
};

click("reload-app");
// The cache work is asynchronous; let it settle.
await new Promise((resolve) => setTimeout(resolve, 20));

// Every Roadplanner entry must be dropped - and with ignoreSearch, because
// the stale entry is stored under the OLD ?v= and would otherwise survive.
const dropped = deleted.map((entry) => entry.url);
assert.ok(
  dropped.some((url) => url.includes("roadplanner-panel.js")),
  `the panel module must be evicted, dropped: ${JSON.stringify(dropped)}`,
);
assert.ok(
  dropped.some((url) => url.includes("features/crew.js")),
  "its submodules too - they are fetched under their own ?v=",
);
assert.ok(
  deleted.every((entry) => entry.ignoreSearch === true),
  "ignoreSearch is the whole point: the stale copy sits under a different query",
);

// Targeted, not scorched earth: another app's asset stays cached, and the
// service worker is asked to update rather than unregistered.
assert.ok(
  !dropped.some((url) => url.includes("other-app-asset")),
  "clearing all of Home Assistant's caches would degrade the whole app",
);
assert.equal(updated, 1, "the worker is asked to re-check, not removed");

// And only THEN is the document replaced, with a busting parameter.
assert.match(replacedWith, /[?&]rp=\d+/, `document not replaced: ${replacedWith}`);

// The notice names the fallback that demonstrably works on the phone.
const source = await import("node:fs").then((fs) =>
  fs.readFileSync(
    new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
    "utf8",
  ),
);
assert.match(source, /von oben nach unten ziehen/, "the notice offers the manual fallback");

console.log("Stale module cache-busting tests passed.");
