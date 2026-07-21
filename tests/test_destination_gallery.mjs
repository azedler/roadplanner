import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

class FakeShadowRoot { addEventListener() {} querySelector() { return null; } }
class FakeHTMLElement { attachShadow() { this.shadowRoot = new FakeShadowRoot(); return this.shadowRoot; } }
const registry = new Map();
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { location: { origin: "https://ha.example" }, setTimeout, clearTimeout };
globalThis.document = { createElement() { return { setAttribute() {}, style: {}, remove() {} }; }, body: { appendChild() {} } };
globalThis.customElements = { define(name, constructor) { registry.set(name, constructor); }, get(name) { return registry.get(name); } };

const source = await readFile(new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url), "utf8");
vm.runInThisContext(source, { filename: "roadplanner-panel.js" });
const Panel = registry.get("roadplanner-panel");
assert.ok(Panel);
const panel = new Panel();
panel._data = {
  capabilities: { can_edit: true },
  selected_is_active: true,
  experience: {
    destination_galleries: {
      "stop-1": {
        stop_id: "stop-1",
        day_id: "day-1",
        status: "ready",
        primary_image_id: "img-2",
        images: [
          { id: "img-1", provider: "openverse", image_url: "https://example.org/1.jpg", thumbnail_url: "https://example.org/1-thumb.jpg", source_url: "https://example.org/source-1", alt: "One", license: "CC BY 4.0" },
          { id: "img-2", provider: "wikimedia_commons", image_url: "https://example.org/2.jpg", thumbnail_url: "https://example.org/2-thumb.jpg", source_url: "https://example.org/source-2", alt: "Two", license: "CC BY-SA 4.0" },
          { id: "img-3", provider: "openverse", image_url: "https://example.org/3.jpg", thumbnail_url: "https://example.org/3-thumb.jpg", source_url: "https://example.org/source-3", alt: "Three", license: "CC0" },
        ],
      },
    },
    media: [], by_day: {}, by_stop: {}, decisions: [], stats: {}, onedrive: {},
  },
};
const gallery = panel._destinationGalleryForStop("stop-1");
assert.equal(panel._destinationGalleryPrimary(gallery).id, "img-2");
const preview = panel._renderDestinationGalleryPreview(gallery, { dayId: "day-1", stopId: "stop-1", compact: true });
assert.match(preview, /3 Bilder/);
assert.match(preview, /2-thumb\.jpg/);
assert.match(preview, /destination-gallery-thumbs/);

const dialogHtml = panel._renderDestinationGallery({ type: "destination-gallery", dayId: "day-1", stopId: "stop-1", images: gallery.images, index: 1, primaryImageId: "img-2" });
assert.match(dialogHtml, /CC BY-SA 4\.0/);
assert.match(dialogHtml, /Hauptbild/);
assert.match(dialogHtml, /data-action="destination-gallery-prev"/);

const decisionHtml = panel._renderDecisionOptionGallery(
  { id: "decision-1" },
  { id: "option-1", title: "Option", image: gallery.images[0], images: gallery.images },
);
assert.match(decisionHtml, /decision-option-gallery/);
assert.match(decisionHtml, /decision-gallery-open/);
assert.equal((decisionHtml.match(/<img /g) || []).length, 3);

const empty = panel._renderDestinationGalleryStatus({ status: "error", images: [], provider_errors: { openverse: "offline" } }, "day-1", "stop-2");
assert.match(empty, /Bilder konnten noch nicht geladen werden/);
assert.match(empty, /Erneut versuchen/);

console.log("Destination gallery panel tests passed.");
