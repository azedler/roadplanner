import assert from "node:assert/strict";

// Live feedback: "Er zeigt bei ‚Erinnerungen' nur einen Teil der Bilder an.
// Ich würde gern ältere Bilder zuordnen" - the memories tab rendered a hard
// media.slice(0, 120): with 200+ trip photos every older photo was simply
// unreachable, with no hint that anything was cut off. The tab now has
// assignment filters and pages of 60, and every card carries its ABSOLUTE
// index into experience.media so the detail dialog opens the right photo on
// any page. Plus: with own photos present, the "Eigene Bilder vorhanden"
// notice about missing EXTERNAL planning images is suppressed entirely.

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
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
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
const panel = new Panel();
panel._canEdit = () => true;
panel._findDay = () => null;
panel._findStop = () => null;
panel._formatTimestamp = (value) => String(value);
panel._safeUrl = (value) => value || "";

const media = Array.from({ length: 209 }, (_, index) => ({
  id: `m-${index}`,
  name: `Foto ${index}`,
  assignment_status: index % 3 === 0 ? "unassigned" : index % 3 === 1 ? "automatic" : "suggested",
  thumbnail_url: "https://example.org/t.jpg",
  original_url: "https://example.org/o.jpg",
  taken_at: "",
}));
panel._experienceData = () => ({ media, onedrive: {}, vision: {}, stats: {} });
panel._experiencePresentation = () => ({});
panel._renderReadOnlyNotice = () => "";
panel._data = { selected_is_active: true, settings: {} };

// --- all photos are reachable via pages -------------------------------

panel._mediaFilter = "all";
panel._mediaPage = 0;
let html = panel._renderMedia();
assert.ok(html.includes("Bilder 1–60 von 209"), "page 1 label shows the real total");
assert.ok(html.includes('data-action="media-page"'), "page navigation exists");

panel._mediaPage = 3;
html = panel._renderMedia();
assert.ok(html.includes("Bilder 181–209 von 209"), "the LAST page reaches photo 209 - older photos are no longer cut off");
assert.ok(html.includes('data-media-index="208"'), "cards carry the absolute media index, so the detail dialog opens the right photo on any page");

// --- filters narrow the set -------------------------------------------

panel._mediaFilter = "unassigned";
panel._mediaPage = 0;
html = panel._renderMedia();
const expectedUnassigned = media.filter((item) => item.assignment_status === "unassigned").length;
assert.ok(html.includes(`von ${expectedUnassigned}`) || expectedUnassigned <= 60, "filtering paginates within the filtered set");
assert.ok(!html.includes('data-media-index="1"'), "an assigned photo is not rendered under the unassigned filter");

// --- out-of-range page clamps -----------------------------------------

panel._mediaFilter = "all";
panel._mediaPage = 99;
html = panel._renderMedia();
assert.ok(html.includes("Bilder 181–209 von 209"), "an out-of-range page clamps to the last page");

// --- own-photos notice is suppressed ----------------------------------

const withOwn = panel._renderDestinationGalleryStatus({ status: "error", provider_errors: { a: "kaputt" } }, "day-1", "stop-1", true);
assert.equal(withOwn, "", "with own photos present, no external-image notice is shown at all");
const withoutOwn = panel._renderDestinationGalleryStatus({ status: "error", provider_errors: { a: "kaputt" } }, "day-1", "stop-1", false);
assert.ok(withoutOwn.includes("Bilder konnten noch nicht geladen werden"), "without own photos the failure notice stays");

console.log("Memories paging/filter tests passed.");
