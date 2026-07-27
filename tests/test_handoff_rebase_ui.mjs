import assert from "node:assert/strict";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = { createElement() { return { setAttribute() {}, style: {}, remove() {} }; }, body: { appendChild() {} } };
globalThis.customElements = { define(name, constructor) { registry.set(name, constructor); }, get(name) { return registry.get(name); } };

await import(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url));
const Panel = registry.get("roadplanner-panel");
assert.ok(Panel);
const panel = new Panel();
panel._data = {
  capabilities: { can_approve: true },
  selected_is_active: true,
  summary: { revision: 20 },
};

const staleHandoff = {
  id: "handoff-1",
  title: "Übernachtung aktualisieren",
  source: "roadplanner_assistant",
  base_revision: 15,
  operation_count: 1,
  operation_counts: {},
  open_question_count: 0,
  status: "pending",
  received_at: "2026-07-27T12:00:00Z",
};

const currentHandoff = { ...staleHandoff, id: "handoff-2", base_revision: 20 };

const staleHtml = panel._renderHandoff(staleHandoff);
assert.match(staleHtml, /data-action="rebase-handoff"/);
assert.match(staleHtml, /data-handoff-id="handoff-1"/);
assert.match(staleHtml, /Neu aufsetzen/);
// The disabled "Übernehmen" reflects the conflict too - both must be present.
assert.match(staleHtml, /data-action="apply-handoff"[^>]*disabled/);

const currentHtml = panel._renderHandoff(currentHandoff);
assert.doesNotMatch(currentHtml, /data-action="rebase-handoff"/);
assert.doesNotMatch(currentHtml, /data-action="apply-handoff"[^>]*disabled/);

const conflictPreviewHtml = panel._renderHandoffPreview({
  handoff: staleHandoff,
  preview: { status: "revision_conflict", applicable: false, current_revision: 20, base_revision: 15 },
});
assert.match(conflictPreviewHtml, /data-action="rebase-handoff"/);

const readyPreviewHtml = panel._renderHandoffPreview({
  handoff: currentHandoff,
  preview: { status: "ready", applicable: true, current_revision: 20, base_revision: 20, operation_results: [] },
});
assert.doesNotMatch(readyPreviewHtml, /data-action="rebase-handoff"/);

console.log("Handoff rebase UI tests passed.");
