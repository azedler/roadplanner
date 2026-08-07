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
      ${retiredPeople.length ? `<details data-section="crew-1" class="crew-retired"><summary>Stillgelegte Personen (${retiredPeople.length})</summary><ul class="crew-list">${retiredPeople.map((person) => this._renderCrewPersonRow(person, canEdit)).join("")}</ul></details>` : ""}</section>
      <section class="crew-section"><h3>Fahrzeuge</h3>${activeVehicles.length ? `<ul class="crew-list">${activeVehicles.map((vehicle) => this._renderCrewVehicleRow(vehicle, canEdit)).join("")}</ul>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:rv-truck"></ha-icon><h2>Noch kein Fahrzeug angelegt</h2></div>`}
      ${retiredVehicles.length ? `<details data-section="crew-2" class="crew-retired"><summary>Stillgelegte Fahrzeuge (${retiredVehicles.length})</summary><ul class="crew-list">${retiredVehicles.map((vehicle) => this._renderCrewVehicleRow(vehicle, canEdit)).join("")}</ul></details>` : ""}</section>`;
  },

  _crewPersonAvatar(person) {
    // The assigned trip photo IS the person's face here - showing a generic
    // silhouette next to it was pointless (live request: "Wenn ein Bild
    // zugeordnet ist sollten wir dieses anstatt des Symbols zeigen"). The
    // chosen crop decides which part of a group photo is shown, using the
    // same numbers the picker stores.
    const icon = person.kind === "dog" ? "mdi:dog-side" : "mdi:account-outline";
    // A stored portrait is ALREADY cropped, so there is no crop maths left
    // to get wrong, and the face survives OneDrive being unreachable.
    const stored = this._safeUrl(person.portrait_url || "");
    if (stored) {
      return `<span class="crew-row-avatar"><img src="${escapeHtml(stored)}" alt="${escapeHtml(person.name)}" loading="lazy"></span>`;
    }
    const reference = String(person.reference_media_id || "");
    const item = reference
      ? this._crewPickerPhotos().find((photo) => photo.id === reference)
      : null;
    const url = item ? this._safeUrl(item.thumbnail_url || item.original_url) : "";
    if (!url) return `<ha-icon icon="${icon}"></ha-icon>`;
    const crop = person.reference_crop;
    let style = "";
    let cropped = "";
    if (crop && crop.w > 0 && crop.h > 0) {
      // Map the crop RECTANGLE exactly onto the avatar box (live report:
      // "Bildausschnitt und tatsächlicher Ausschnitt weichen etwas ab").
      // The old version was wrong twice over: `object-fit: cover` already
      // cropped the photo to a centred square before the transform ran, so
      // the stored coordinates no longer referred to what was on screen -
      // and `transform-origin` with `scale` magnifies AROUND a point, it
      // does not move that point to the middle.
      //
      // Percentages in `translate` are relative to the image's own size,
      // which is what makes this exact: the image is blown up so the crop
      // fills the box, then shifted by the crop's own offset. The picker
      // keeps the crop square in screen pixels, so nothing is distorted.
      const width = (100 / crop.w).toFixed(2);
      const height = (100 / crop.h).toFixed(2);
      const left = (-crop.x * 100).toFixed(2);
      const top = (-crop.y * 100).toFixed(2);
      style = `width:${width}%;height:${height}%;transform:translate(${left}%,${top}%)`;
      cropped = " cropped";
    }
    return `<span class="crew-row-avatar${cropped}"><img src="${escapeHtml(url)}" alt="${escapeHtml(person.name)}" loading="lazy" style="${style}"></span>`;
  },

  _renderCrewPersonRow(person, canEdit) {
    return `<li class="crew-row ${person.active ? "" : "inactive"}">
      ${this._crewPersonAvatar(person)}
      <div class="crew-row-body"><strong>${escapeHtml(person.name)}</strong>${person.note ? `<span>${escapeHtml(person.note)}</span>` : ""}</div>
      ${canEdit ? `<div class="button-row">
        <button class="icon-button" type="button" data-action="edit-crew-person" data-person-id="${escapeHtml(person.id)}" title="Bearbeiten" aria-label="${escapeHtml(person.name)} bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
        ${person.active
          ? `<button class="icon-button" type="button" data-action="retire-crew-person" data-person-id="${escapeHtml(person.id)}" title="Stilllegen" aria-label="${escapeHtml(person.name)} stilllegen"><ha-icon icon="mdi:archive-outline"></ha-icon></button>`
          : `<button class="icon-button" type="button" data-action="reactivate-crew-person" data-person-id="${escapeHtml(person.id)}" title="Reaktivieren" aria-label="${escapeHtml(person.name)} reaktivieren"><ha-icon icon="mdi:archive-arrow-up-outline"></ha-icon></button>`}
      </div>` : ""}
    </li>`;
  },

  _crewVehicleAvatar(vehicle) {
    const stored = this._safeUrl(vehicle.portrait_url || "");
    if (stored) {
      return `<span class="crew-row-avatar"><img src="${escapeHtml(stored)}" alt="${escapeHtml(vehicle.name)}" loading="lazy"></span>`;
    }
    return `<ha-icon icon="mdi:rv-truck"></ha-icon>`;
  },

  _renderCrewVehicleRow(vehicle, canEdit) {
    return `<li class="crew-row ${vehicle.active ? "" : "inactive"}">
      ${this._crewVehicleAvatar(vehicle)}
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

  CREW_PHOTO_PAGE_SIZE: 36,

  _crewPickerPhotos() {
    return (this._experienceData()?.media || []).filter(
      (item) => (item.media_type || "photo") === "photo" && this._safeUrl(item.thumbnail_url),
    );
  },

  _setCrewReferencePhoto(form, mediaId, thumbUrl, largeUrl) {
    if (!form) return;
    const referenceInput = form.querySelector('input[name="reference_media_id"]');
    if (referenceInput) referenceInput.value = mediaId;
    form.querySelectorAll(".crew-photo-choice.selected").forEach((button) => button.classList.remove("selected"));
    const current = form.querySelector("[data-crew-photo-current]");
    const currentImage = form.querySelector("[data-crew-photo-current-image]");
    if (current) current.hidden = !mediaId;
    if (currentImage && mediaId) currentImage.src = largeUrl || thumbUrl;
    // A new photo starts without a crop - the whole picture counts.
    this._setCrewReferenceCrop(form, null);
  },

  _setCrewReferenceCrop(form, crop) {
    if (!form) return;
    const field = form.querySelector('input[name="reference_crop"]');
    if (field) field.value = crop ? JSON.stringify(crop) : "";
    const box = form.querySelector("[data-crew-crop-box]");
    const hint = form.querySelector("[data-crew-crop-hint]");
    if (box) {
      box.hidden = !crop;
      if (crop) {
        box.style.left = `${crop.x * 100}%`;
        box.style.top = `${crop.y * 100}%`;
        box.style.width = `${crop.w * 100}%`;
        box.style.height = `${crop.h * 100}%`;
      }
    }
    if (hint) {
      hint.textContent = crop
        ? "Ausschnitt gesetzt - nur dieser Bereich zählt als Gesicht. Zum Verschieben im Bild ziehen."
        : "Ganzes Bild. Auf das Gesicht tippen oder ziehen, um einen Ausschnitt zu setzen (bei Gruppenfotos).";
    }
  },

  _applyCrewCropTap(form, frame, event) {
    // Tap centers a square crop; the slider sets its size. That works on
    // touch without drag handles (live request: pick one face out of a
    // group photo).
    const rect = frame.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const sizeInput = form.querySelector("[data-crew-crop-size]");
    const size = Math.min(Math.max(Number(sizeInput?.value || 40) / 100, 0.05), 1);
    const cx = Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1);
    const cy = Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1);
    // Keep the box square on screen by compensating the frame's aspect.
    const aspect = rect.width / rect.height;
    const w = size;
    const h = Math.min(size * aspect, 1);
    this._setCrewReferenceCrop(form, {
      x: Number(Math.min(Math.max(cx - w / 2, 0), 1 - w).toFixed(4)),
      y: Number(Math.min(Math.max(cy - h / 2, 0), 1 - h).toFixed(4)),
      w: Number(w.toFixed(4)),
      h: Number(h.toFixed(4)),
    });
  },

  _resizeCrewCrop(form) {
    const field = form?.querySelector('input[name="reference_crop"]');
    if (!field?.value) return;
    const frame = form.querySelector("[data-crew-crop-frame]");
    const box = form.querySelector("[data-crew-crop-box]");
    if (!frame || !box) return;
    const rect = frame.getBoundingClientRect();
    const current = JSON.parse(field.value);
    const sizeInput = form.querySelector("[data-crew-crop-size]");
    const size = Math.min(Math.max(Number(sizeInput?.value || 40) / 100, 0.05), 1);
    const aspect = rect.width / rect.height || 1;
    const w = size;
    const h = Math.min(size * aspect, 1);
    const cx = current.x + current.w / 2;
    const cy = current.y + current.h / 2;
    this._setCrewReferenceCrop(form, {
      x: Number(Math.min(Math.max(cx - w / 2, 0), 1 - w).toFixed(4)),
      y: Number(Math.min(Math.max(cy - h / 2, 0), 1 - h).toFixed(4)),
      w: Number(w.toFixed(4)),
      h: Number(h.toFixed(4)),
    });
  },

  _showCrewPhotoPage(form, page) {
    const photos = this._crewPickerPhotos();
    const pages = Math.max(1, Math.ceil(photos.length / this.CREW_PHOTO_PAGE_SIZE));
    const safePage = Math.min(Math.max(page, 0), pages - 1);
    const grid = form?.querySelector("[data-crew-photo-grid]");
    if (!grid) return;
    grid.dataset.page = String(safePage);
    const selected = form.querySelector('input[name="reference_media_id"]')?.value || "";
    grid.innerHTML = this._renderCrewPhotoTiles(photos, safePage, selected);
    const label = form.querySelector("[data-crew-photo-pager-label]");
    const from = safePage * this.CREW_PHOTO_PAGE_SIZE + 1;
    const to = Math.min((safePage + 1) * this.CREW_PHOTO_PAGE_SIZE, photos.length);
    if (label) label.textContent = `${from}–${to} von ${photos.length}`;
    form.querySelectorAll("[data-crew-photo-prev]").forEach((b) => { b.disabled = safePage === 0; });
    form.querySelectorAll("[data-crew-photo-next]").forEach((b) => { b.disabled = safePage >= pages - 1; });
  },

  _renderCrewPhotoTiles(photos, page, selectedId) {
    const start = page * this.CREW_PHOTO_PAGE_SIZE;
    return photos
      .slice(start, start + this.CREW_PHOTO_PAGE_SIZE)
      .map((item) => `<button type="button" class="crew-photo-choice ${item.id === selectedId ? "selected" : ""}" data-action="crew-pick-reference" data-media-id="${escapeHtml(item.id)}" data-thumb-url="${escapeHtml(this._safeUrl(item.thumbnail_url))}" data-large-url="${escapeHtml(this._safeUrl(item.original_url || item.thumbnail_url))}" aria-label="Dieses Foto zuordnen"><img loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt=""></button>`)
      .join("");
  },

  _renderCrewReferencePhotoPicker(person, options = {}) {
    // "Wer ist wer": one assigned trip photo is the person's portrait in
    // the PDF and the Vision reference for personal summaries - no photo
    // captions needed (live request). A vehicle uses the very same picker
    // ("Vielleicht auch vom Fahrzeug ein Bild?") - only the wording differs,
    // since a camper has no face to recognise.
    const media = this._crewPickerPhotos();
    const selected = String(person.reference_media_id || "");
    const selectedItem = media.find((item) => item.id === selected) || null;
    const crop = person.reference_crop || null;
    const selectedIndex = selectedItem ? media.indexOf(selectedItem) : 0;
    const page = Math.max(0, Math.floor(selectedIndex / this.CREW_PHOTO_PAGE_SIZE));
    const pages = Math.max(1, Math.ceil(media.length / this.CREW_PHOTO_PAGE_SIZE));
    const from = page * this.CREW_PHOTO_PAGE_SIZE + 1;
    const to = Math.min((page + 1) * this.CREW_PHOTO_PAGE_SIZE, media.length);
    const cropSize = crop ? Math.round(crop.w * 100) : 40;
    return `<details data-section="crew-3" class="form-field full crew-photo-picker" open>
      <summary><ha-icon icon="${options.icon || "mdi:face-recognition"}"></ha-icon> ${escapeHtml(options.title || "Reisefoto zuordnen (wer ist wer)")}</summary>
      <small class="hint">${escapeHtml(options.hint || "Ein Foto antippen, auf dem die Person gut zu erkennen ist. Es wird als Porträt im Reise-Rückblick genutzt und hilft dem Reisebegleiter, die Person auf den übrigen Fotos zu erkennen - ohne dass Bilder beschriftet werden müssen.")}</small>
      <input type="hidden" name="reference_media_id" value="${escapeHtml(selected)}">
      <input type="hidden" name="reference_crop" value="${crop ? escapeHtml(JSON.stringify(crop)) : ""}">
      <div class="crew-photo-current" data-crew-photo-current ${selectedItem ? "" : "hidden"}>
        <div class="crew-crop-frame" data-crew-crop-frame data-action="crew-crop-tap">
          <img data-crew-photo-current-image src="${escapeHtml(selectedItem ? this._safeUrl(selectedItem.original_url || selectedItem.thumbnail_url) : "")}" alt="Ausgewähltes Foto">
          <span class="crew-crop-box" data-crew-crop-box ${crop ? "" : "hidden"} style="${crop ? `left:${crop.x * 100}%;top:${crop.y * 100}%;width:${crop.w * 100}%;height:${crop.h * 100}%` : ""}"></span>
        </div>
        <div class="crew-crop-controls">
          <small class="hint" data-crew-crop-hint>${crop ? "Ausschnitt gesetzt - nur dieser Bereich zählt als Gesicht. Zum Verschieben im Bild ziehen." : "Ganzes Bild. Auf das Gesicht tippen oder ziehen, um einen Ausschnitt zu setzen (bei Gruppenfotos)."}</small>
          <label class="crew-crop-size"><span>Ausschnittgröße</span><input type="range" min="10" max="90" step="5" value="${cropSize}" data-crew-crop-size data-action="crew-crop-size"></label>
          <div class="button-row compact-row">
            <button class="text-button" type="button" data-action="crew-crop-reset">Ganzes Bild</button>
            <button class="text-button danger-text" type="button" data-action="crew-clear-reference">Zuordnung entfernen</button>
          </div>
        </div>
      </div>
      ${media.length ? `
        <div class="crew-photo-pager">
          <button class="text-button" type="button" data-action="crew-photo-prev" data-crew-photo-prev ${page === 0 ? "disabled" : ""}><ha-icon icon="mdi:chevron-left"></ha-icon> Zurück</button>
          <span data-crew-photo-pager-label>${from}–${to} von ${media.length}</span>
          <button class="text-button" type="button" data-action="crew-photo-next" data-crew-photo-next ${page >= pages - 1 ? "disabled" : ""}>Weiter <ha-icon icon="mdi:chevron-right"></ha-icon></button>
        </div>
        <div class="crew-photo-grid" data-crew-photo-grid data-page="${page}">${this._renderCrewPhotoTiles(media, page, selected)}</div>
      ` : `<p class="hint">Für die ausgewählte Reise sind noch keine Fotos synchronisiert.</p>`}
    </details>`;
  },

  _renderCrewVehicleForm(dialog) {
    const vehicle = dialog.vehicle || {};
    const add = !dialog.vehicle;
    return `${this._renderModalHeader(add ? "Fahrzeug hinzufügen" : "Fahrzeug bearbeiten")}<form data-form="crew-vehicle" data-mode="${add ? "add" : "edit"}" data-vehicle-id="${escapeHtml(vehicle.id || "")}" class="form-grid">${this._field("name", "Name", vehicle.name || "", "text", true, "full")}${this._textarea("description", "Beschreibung (für ein KI-Icon z.B. Farbe, Typ)", vehicle.description || "", "full")}${this._renderCrewReferencePhotoPicker(vehicle, {
      icon: "mdi:rv-truck",
      title: "Reisefoto zuordnen",
      hint: "Ein Foto antippen, auf dem das Fahrzeug gut zu sehen ist. Es wird als Bild im Reise-Rückblick genutzt. Der Ausschnitt lässt sich wie bei Personen setzen.",
    })}${this._formActions(add ? "Fahrzeug hinzufügen" : "Änderungen speichern")}</form>`;
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
