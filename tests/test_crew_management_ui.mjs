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

const aron = { id: "person-aron", name: "Aron", kind: "person", note: "Fahrer", active: true };
const rufus = { id: "person-rufus", name: "Rufus", kind: "dog", note: "", active: false };
const nugget = { id: "vehicle-nugget", name: "Nugget", description: "Cremefarbener Kastenwagen", active: true };

panel._data = {
  capabilities: { can_edit: true },
  selected_is_active: true,
  crew: { people: [aron, rufus], vehicles: [nugget] },
};

// The management screen lists active entries directly and tucks retired
// ones behind a collapsed "stillgelegt" (retired) section instead of
// deleting them - a retired vehicle/person must stay findable.
const manageHtml = panel._renderCrewManage();
assert.match(manageHtml, /Aron/);
assert.match(manageHtml, /Stillgelegte Personen \(1\)/);
assert.match(manageHtml, /Rufus/);
assert.match(manageHtml, /Nugget/);
assert.match(manageHtml, /data-action="add-crew-person"/);
assert.match(manageHtml, /data-action="retire-crew-person"/);
assert.match(manageHtml, /data-action="reactivate-crew-person"/);

// Lookups by id back the edit/retire dialogs and the trip-form snapshotting.
assert.equal(panel._crewPersonById("person-aron").name, "Aron");
assert.equal(panel._crewVehicleById("vehicle-nugget").name, "Nugget");
assert.equal(panel._crewPersonById("missing"), null);

const personForm = panel._renderCrewPersonForm({ person: aron });
assert.match(personForm, /data-form="crew-person"/);
assert.match(personForm, /data-mode="edit"/);
assert.match(personForm, /data-person-id="person-aron"/);

const addPersonForm = panel._renderCrewPersonForm({ person: null });
assert.match(addPersonForm, /data-mode="add"/);

const vehicleForm = panel._renderCrewVehicleForm({ vehicle: nugget });
assert.match(vehicleForm, /data-form="crew-vehicle"/);
assert.match(vehicleForm, /data-vehicle-id="vehicle-nugget"/);

// The trip form embeds a crew selection: a checkbox per person (marking
// retired ones without hiding them) and a vehicle dropdown.
const trip = {
  title: "Finnland 2026",
  status: "planned",
  travelers: [{ person_id: "person-aron", name: "Aron", kind: "person", note: "Fahrer" }],
  vehicle: { vehicle_id: "vehicle-nugget", name: "Nugget", description: "" },
};
const crewFields = panel._renderTripCrewFields(trip);
assert.match(crewFields, /name="traveler_ids" value="person-aron" checked/);
assert.match(crewFields, /name="traveler_ids" value="person-rufus"/);
assert.doesNotMatch(crewFields, /value="person-rufus" checked/);
assert.match(crewFields, /\(stillgelegt\)/);
assert.match(crewFields, /<option value="vehicle-nugget" selected>/);

// An assigned trip photo IS the person's face - the list must show it
// instead of a generic silhouette (live request: "Wenn ein Bild zugeordnet
// ist sollten wir dieses anstatt des Symbols zeigen").
panel._experienceData = () => ({
  media: [
    { id: "media-1", media_type: "photo", thumbnail_url: "https://ha.example/api/roadplanner/media/1" },
  ],
});
assert.match(panel._crewPersonAvatar(aron), /<ha-icon/, "no assigned photo keeps the icon");

const withPhoto = panel._crewPersonAvatar({ ...aron, reference_media_id: "media-1" });
assert.match(withPhoto, /crew-row-avatar/);
assert.match(withPhoto, /<img/);
assert.doesNotMatch(withPhoto, /<ha-icon/, "the photo replaces the symbol, it does not join it");

// A crop means only that part of a group photo is the person: the avatar
// zooms into the crop's centre rather than showing everyone.
const cropped = panel._crewPersonAvatar({
  ...aron,
  reference_media_id: "media-1",
  reference_crop: { x: 0.5, y: 0.2, w: 0.25, h: 0.25 },
});
assert.match(cropped, /transform:scale\(4\.00\)/);
assert.match(cropped, /transform-origin:62\.5% 32\.5%/);

// A reference pointing at a photo that is no longer there falls back.
assert.match(panel._crewPersonAvatar({ ...aron, reference_media_id: "media-weg" }), /<ha-icon/);

// The row itself uses the avatar, not a hard-coded icon.
assert.match(
  panel._renderCrewPersonRow({ ...aron, reference_media_id: "media-1" }, true),
  /crew-row-avatar/,
);

print_ok();

function print_ok() {
  console.log("Crew management UI tests passed.");
}

// Live report: "Ich hatte das neue Release eingespielt. Keine Veränderung
// bei der Crew" - the browser kept running the pre-update panel module and
// nothing said so. The panel now compares the version it was loaded with
// against the version the backend reports.
const panelSource = await import("node:fs").then((fs) =>
  fs.readFileSync(
    new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
    "utf8",
  ),
);
assert.match(panelSource, /LOADED_MODULE_VERSION/, "the module knows its own ?v= version");
assert.match(panelSource, /_renderStaleModuleNotice\(\)/);
assert.match(panelSource, /Ältere Oberfläche geladen/);
assert.match(
  panelSource,
  /backendVersion === LOADED_MODULE_VERSION\) return ""/,
  "matching versions must not nag",
);
assert.match(
  panelSource,
  /target\.searchParams\.set\("rp"/,
  "the reload must bypass a service-worker cached document",
);

console.log("Panel stale-module notice tests passed.");
