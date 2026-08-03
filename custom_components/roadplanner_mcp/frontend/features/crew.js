import { escapeHtml } from "../lib/core-helpers.js";

export const crewMixin = {
  _crewData() {
    return this._data?.crew || { people: [], vehicles: [] };
  },

  _crewPersonById(personId) {
    return (this._crewData().people || []).find((item) => item.id === personId) || null;
  },

  _crewVehicleById(vehicleId) {
    return (this._crewData().vehicles || []).find((item) => item.id === vehicleId) || null;
  },

  _renderCrewManage() {
    const { people, vehicles } = this._crewData();
    const activePeople = people.filter((item) => item.active);
    const retiredPeople = people.filter((item) => !item.active);
    const activeVehicles = vehicles.filter((item) => item.active);
    const retiredVehicles = vehicles.filter((item) => !item.active);
    const canEdit = this._canEdit();
    return `<section class="toolbar-card"><div><span class="eyebrow">Stammdaten</span><h2>Crew &amp; Fahrzeuge</h2><p>Personen und Fahrzeuge werden einmal gepflegt und dann pro Reise nur noch ausgewählt. Stillgelegte Einträge bleiben für vergangene Reisen erhalten, tauchen aber bei neuen Reisen nicht mehr zur Auswahl auf.</p></div>${canEdit ? `<div class="button-row"><button class="secondary-button" type="button" data-action="add-crew-person"><ha-icon icon="mdi:account-plus-outline"></ha-icon> Person hinzufügen</button><button class="secondary-button" type="button" data-action="add-crew-vehicle"><ha-icon icon="mdi:rv-truck"></ha-icon> Fahrzeug hinzufügen</button></div>` : ""}</section>
      <section class="crew-section"><h3>Personen</h3>${activePeople.length ? `<ul class="crew-list">${activePeople.map((person) => this._renderCrewPersonRow(person, canEdit)).join("")}</ul>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:account-outline"></ha-icon><h2>Noch keine Personen angelegt</h2></div>`}
      ${retiredPeople.length ? `<details class="crew-retired"><summary>Stillgelegte Personen (${retiredPeople.length})</summary><ul class="crew-list">${retiredPeople.map((person) => this._renderCrewPersonRow(person, canEdit)).join("")}</ul></details>` : ""}</section>
      <section class="crew-section"><h3>Fahrzeuge</h3>${activeVehicles.length ? `<ul class="crew-list">${activeVehicles.map((vehicle) => this._renderCrewVehicleRow(vehicle, canEdit)).join("")}</ul>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:rv-truck"></ha-icon><h2>Noch kein Fahrzeug angelegt</h2></div>`}
      ${retiredVehicles.length ? `<details class="crew-retired"><summary>Stillgelegte Fahrzeuge (${retiredVehicles.length})</summary><ul class="crew-list">${retiredVehicles.map((vehicle) => this._renderCrewVehicleRow(vehicle, canEdit)).join("")}</ul></details>` : ""}</section>`;
  },

  _renderCrewPersonRow(person, canEdit) {
    const icon = person.kind === "dog" ? "mdi:dog-side" : "mdi:account-outline";
    return `<li class="crew-row ${person.active ? "" : "inactive"}">
      <ha-icon icon="${icon}"></ha-icon>
      <div class="crew-row-body"><strong>${escapeHtml(person.name)}</strong>${person.note ? `<span>${escapeHtml(person.note)}</span>` : ""}</div>
      ${canEdit ? `<div class="button-row">
        <button class="icon-button" type="button" data-action="edit-crew-person" data-person-id="${escapeHtml(person.id)}" title="Bearbeiten" aria-label="${escapeHtml(person.name)} bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
        ${person.active
          ? `<button class="icon-button" type="button" data-action="retire-crew-person" data-person-id="${escapeHtml(person.id)}" title="Stilllegen" aria-label="${escapeHtml(person.name)} stilllegen"><ha-icon icon="mdi:archive-outline"></ha-icon></button>`
          : `<button class="icon-button" type="button" data-action="reactivate-crew-person" data-person-id="${escapeHtml(person.id)}" title="Reaktivieren" aria-label="${escapeHtml(person.name)} reaktivieren"><ha-icon icon="mdi:archive-arrow-up-outline"></ha-icon></button>`}
      </div>` : ""}
    </li>`;
  },

  _renderCrewVehicleRow(vehicle, canEdit) {
    return `<li class="crew-row ${vehicle.active ? "" : "inactive"}">
      <ha-icon icon="mdi:rv-truck"></ha-icon>
      <div class="crew-row-body"><strong>${escapeHtml(vehicle.name)}</strong>${vehicle.description ? `<span>${escapeHtml(vehicle.description)}</span>` : ""}</div>
      ${canEdit ? `<div class="button-row">
        <button class="icon-button" type="button" data-action="edit-crew-vehicle" data-vehicle-id="${escapeHtml(vehicle.id)}" title="Bearbeiten" aria-label="${escapeHtml(vehicle.name)} bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
        ${vehicle.active
          ? `<button class="icon-button" type="button" data-action="retire-crew-vehicle" data-vehicle-id="${escapeHtml(vehicle.id)}" title="Stilllegen" aria-label="${escapeHtml(vehicle.name)} stilllegen"><ha-icon icon="mdi:archive-outline"></ha-icon></button>`
          : `<button class="icon-button" type="button" data-action="reactivate-crew-vehicle" data-vehicle-id="${escapeHtml(vehicle.id)}" title="Reaktivieren" aria-label="${escapeHtml(vehicle.name)} reaktivieren"><ha-icon icon="mdi:archive-arrow-up-outline"></ha-icon></button>`}
      </div>` : ""}
    </li>`;
  },

  _renderCrewPersonForm(dialog) {
    const person = dialog.person || {};
    const add = !dialog.person;
    return `${this._renderModalHeader(add ? "Person hinzufügen" : "Person bearbeiten")}<form data-form="crew-person" data-mode="${add ? "add" : "edit"}" data-person-id="${escapeHtml(person.id || "")}" class="form-grid">${this._field("name", "Name", person.name || "", "text", true, "full")}${this._selectField("kind", "Art", person.kind || "person", ["person", "dog"])}${this._textarea("note", "Rolle / Besonderheit", person.note || "", "full")}${this._renderCrewReferencePhotoPicker(person)}${this._formActions(add ? "Person hinzufügen" : "Änderungen speichern")}</form>`;
  },

  _setCrewReferencePhoto(form, mediaId, thumbUrl) {
    if (!form) return;
    const referenceInput = form.querySelector('input[name="reference_media_id"]');
    if (referenceInput) referenceInput.value = mediaId;
    form.querySelectorAll(".crew-photo-choice.selected").forEach((button) => button.classList.remove("selected"));
    const current = form.querySelector("[data-crew-photo-current]");
    const currentImage = form.querySelector("[data-crew-photo-current-image]");
    if (current) current.hidden = !mediaId;
    if (currentImage && mediaId) currentImage.src = thumbUrl;
  },

  _renderCrewReferencePhotoPicker(person) {
    // "Wer ist wer": one assigned trip photo is the person's portrait in
    // the PDF and the Vision reference for personal summaries - no photo
    // captions needed (live request).
    const media = (this._experienceData()?.media || []).filter(
      (item) => (item.media_type || "photo") === "photo" && this._safeUrl(item.thumbnail_url),
    );
    const selected = String(person.reference_media_id || "");
    const recent = media.slice(0, 48);
    if (selected && !recent.some((item) => item.id === selected)) {
      const keep = media.find((item) => item.id === selected);
      if (keep) recent.unshift(keep);
    }
    const selectedItem = media.find((item) => item.id === selected) || null;
    return `<details class="form-field full crew-photo-picker" open>
      <summary><ha-icon icon="mdi:face-recognition"></ha-icon> Reisefoto zuordnen (wer ist wer)</summary>
      <small class="hint">Ein Foto antippen, auf dem die Person gut zu erkennen ist. Es wird als Porträt im Reise-Rückblick genutzt und hilft dem Reisebegleiter, die Person auf den übrigen Fotos zu erkennen - ohne dass Bilder beschriftet werden müssen.</small>
      <input type="hidden" name="reference_media_id" value="${escapeHtml(selected)}">
      <div class="crew-photo-current" data-crew-photo-current ${selectedItem ? "" : "hidden"}>
        <img data-crew-photo-current-image src="${escapeHtml(selectedItem ? this._safeUrl(selectedItem.thumbnail_url) : "")}" alt="Ausgewähltes Foto">
        <div><strong>Zugeordnet</strong><br><button class="text-button" type="button" data-action="crew-clear-reference">Zuordnung entfernen</button></div>
      </div>
      ${recent.length ? `<div class="crew-photo-grid">${recent.map((item) => `<button type="button" class="crew-photo-choice ${item.id === selected ? "selected" : ""}" data-action="crew-pick-reference" data-media-id="${escapeHtml(item.id)}" data-thumb-url="${escapeHtml(this._safeUrl(item.thumbnail_url))}" aria-label="Dieses Foto zuordnen"><img loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt=""></button>`).join("")}</div>` : `<p class="hint">Für die ausgewählte Reise sind noch keine Fotos synchronisiert.</p>`}
    </details>`;
  },

  _renderCrewVehicleForm(dialog) {
    const vehicle = dialog.vehicle || {};
    const add = !dialog.vehicle;
    return `${this._renderModalHeader(add ? "Fahrzeug hinzufügen" : "Fahrzeug bearbeiten")}<form data-form="crew-vehicle" data-mode="${add ? "add" : "edit"}" data-vehicle-id="${escapeHtml(vehicle.id || "")}" class="form-grid">${this._field("name", "Name", vehicle.name || "", "text", true, "full")}${this._textarea("description", "Beschreibung (für ein KI-Icon z.B. Farbe, Typ)", vehicle.description || "", "full")}${this._formActions(add ? "Fahrzeug hinzufügen" : "Änderungen speichern")}</form>`;
  },

  _renderTripCrewFields(trip) {
    const { people, vehicles } = this._crewData();
    const selectedPersonIds = new Set((trip.travelers || []).map((item) => item.person_id).filter(Boolean));
    const selectedVehicleId = trip.vehicle?.vehicle_id || "";
    const personOptions = people.length
      ? people.map((person) => `<label class="checkbox-field"><input type="checkbox" name="traveler_ids" value="${escapeHtml(person.id)}" ${selectedPersonIds.has(person.id) ? "checked" : ""}><span><strong>${escapeHtml(person.name)}</strong>${person.active ? "" : "<small>(stillgelegt)</small>"}</span></label>`).join("")
      : `<p class="hint">Noch keine Personen angelegt. Unter „Crew &amp; Fahrzeuge“ anlegen.</p>`;
    const vehicleOptions = [`<option value="">Kein Fahrzeug ausgewählt</option>`]
      .concat(vehicles.map((vehicle) => `<option value="${escapeHtml(vehicle.id)}" ${vehicle.id === selectedVehicleId ? "selected" : ""}>${escapeHtml(vehicle.name)}${vehicle.active ? "" : " (stillgelegt)"}</option>`))
      .join("");
    return `<div class="form-section full"><h3>Crew</h3><p>Wer ist bei dieser Reise dabei?</p></div>
      <div class="form-field full crew-checkbox-group">${personOptions}</div>
      <label class="form-field full"><span>Fahrzeug</span><select name="vehicle_id">${vehicleOptions}</select></label>`;
  },
};
