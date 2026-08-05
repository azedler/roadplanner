/**
 * The size next to "Letztes PDF" / "Letztes Video" must not depend on luck.
 *
 * Those labels read their value from the export status, and nothing fetched
 * that status when the overview was merely opened - only running an export,
 * a running-build poll, or clicking the download itself did. After a reload
 * the labels stayed bare until something happened to trigger a fetch, which
 * is why the size seemed to appear at random (live report: "Außerdem zeigt
 * er nur manchmal die Größe vom letzten Video und pdf").
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
const panel = new (registry.get("roadplanner-panel"))();

const calls = [];
panel._render = () => {};
panel._runAction = async (action) => {
  calls.push(action);
  if (action === "trip_pdf_status") return { status: { last_pdf: { url: "/p.pdf", size_mb: 3.4 } } };
  if (action === "trip_video_status") return { status: { last_video: { url: "/v.mp4", size_mb: 21.7 } } };
  return {};
};

// --- opening the overview loads both statuses -------------------------
panel._selectedTripId = "trip-1";
panel._activeTab = "total-route";
panel._maybeLoadExportStatus();
await new Promise((resolve) => setTimeout(resolve, 0));
assert.deepEqual(calls.sort(), ["trip_pdf_status", "trip_video_status"]);
assert.equal(panel._tripPdfStatus.last_pdf.size_mb, 3.4);
assert.equal(panel._tripVideoStatus.last_video.size_mb, 21.7);

// --- and does not re-fetch on every render ----------------------------
calls.length = 0;
panel._maybeLoadExportStatus();
await new Promise((resolve) => setTimeout(resolve, 0));
assert.deepEqual(calls, [], "one load per trip, not one per render");

// --- another trip is a different library ------------------------------
panel._selectedTripId = "trip-2";
panel._maybeLoadExportStatus();
await new Promise((resolve) => setTimeout(resolve, 0));
assert.deepEqual(calls.sort(), ["trip_pdf_status", "trip_video_status"]);

// --- other tabs and trip-less states stay quiet -----------------------
calls.length = 0;
panel._activeTab = "assistant";
panel._selectedTripId = "trip-3";
panel._maybeLoadExportStatus();
panel._activeTab = "total-route";
panel._selectedTripId = null;
panel._maybeLoadExportStatus();
await new Promise((resolve) => setTimeout(resolve, 0));
assert.deepEqual(calls, []);

// --- a failing status must never surface as an error ------------------
panel._selectedTripId = "trip-4";
panel._runAction = async () => { throw new Error("offline"); };
await panel._maybeLoadExportStatus();
await new Promise((resolve) => setTimeout(resolve, 0));

// --- the wiring itself: both entry points must call it ----------------
const source = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf-8",
);
assert.equal(
  (source.match(/this\._maybeLoadExportStatus\(\)/g) || []).length,
  2,
  "must run after a data load AND on a tab switch - either alone leaves the gap",
);
const feature = await (await import("node:fs/promises")).readFile(
  new URL("../custom_components/roadplanner_mcp/frontend/features/route-map.js", import.meta.url),
  "utf-8",
);
assert.match(feature, /_maybeLoadExportStatus\(\) \{/, "defined next to the status fetchers");

console.log("Export status load-on-open tests passed.");
