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
globalThis.window = { location: { origin: "https://ha.example" } };
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
panel._hass = { locale: { language: "de-DE" } };

const html = panel._renderAssistantMessage({
  id: "msg-test",
  role: "assistant",
  created_at: "2026-07-20T12:00:00Z",
  content: [
    "Karte: https://www.google.com/maps/search/?api=1&query=Tallinn.",
    "Mehr unter [offizielle Website](https://example.com/info).",
    "Nicht öffnen: [gefährlich](javascript:alert(1)).",
    "<img src=x onerror=alert(1)>",
  ].join("\n"),
});

assert.match(html, /class="assistant-inline-link google-maps"/);
assert.match(html, /href="https:\/\/www\.google\.com\/maps\/search\/\?api=1&amp;query=Tallinn"/);
assert.match(html, />Google Maps öffnen<\/span>/);
assert.match(html, /href="https:\/\/example\.com\/info"/);
assert.match(html, />offizielle Website<\/span>/);
assert.doesNotMatch(html, /href="javascript:/);
assert.match(html, /\[gefährlich\]\(javascript:alert\(1\)\)/);
assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
assert.doesNotMatch(html, /query=Tallinn\./);

const internalHttp = panel._renderAssistantMessage({
  role: "assistant",
  created_at: "2026-07-20T12:00:00Z",
  content: "Unsicher: http://example.com/test",
});
assert.doesNotMatch(internalHttp, /href="http:\/\/example\.com/);

console.log("Assistant link rendering tests passed.");
