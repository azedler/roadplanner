import { escapeHtml, cleanText } from "../lib/core-helpers.js";

// Feature keys shown in the preferences editor and the option form. The
// backend accepts arbitrary feature names; this is only the curated UI set.
const PITCH_FEATURES = [
  ["power", "Strom"],
  ["water", "Wasser"],
  ["disposal", "Entsorgung"],
  ["quiet", "Ruhig"],
  ["dog_ok", "Hunde erlaubt"],
  ["lake_or_sea", "Am Wasser"],
  ["shelter", "Windgeschützt"],
  ["fireplace", "Feuerstelle"],
];

const PITCH_STRATEGIES = [
  ["route_optimal", "Routenoptimal (Tag + Folgetag)"],
  ["best_first", "Beste Plätze zuerst"],
  ["early_arrival", "Früh ankommen"],
];

const OVERNIGHT_TYPES = new Set(["overnight", "campsite", "camping", "stellplatz", "wildcamp", "accommodation"]);

export const pitchesMixin = {
  _pitchPlan(day) {
    const plan = day?.details?.overnight_plan;
    if (!plan || typeof plan !== "object") return { strategy: "route_optimal", options: [] };
    return {
      strategy: typeof plan.strategy === "string" ? plan.strategy : "route_optimal",
      options: Array.isArray(plan.options) ? plan.options.filter((item) => item && item.id && item.name) : [],
    };
  },

  _pitchActiveStop(day) {
    const stops = Array.isArray(day?.stops) ? day.stops : [];
    for (let index = stops.length - 1; index >= 0; index -= 1) {
      if (OVERNIGHT_TYPES.has(String(stops[index]?.type || "").toLowerCase())) return stops[index];
    }
    return null;
  },

  _pitchPreferences() {
    const preferences = this._data?.summary?.trip?.details?.pitch_preferences;
    return preferences && typeof preferences === "object" ? preferences : {};
  },

  _pitchBackups(day) {
    return this._pitchPlan(day).options.filter((item) => item.status !== "rejected");
  },

  _pitchLinkedCounts(stopId) {
    const media = (this._data?.experience?.by_stop?.[stopId] || []).length;
    const documents = (this._data?.travel_archive?.by_stop?.[stopId] || []).length;
    return { media, documents };
  },

  _pitchOptionGalleryKey(option) {
    return `option:${option?.id || ""}`;
  },

  _pitchOptionCover(option) {
    const gallery = this._destinationGalleryForStop(this._pitchOptionGalleryKey(option));
    return this._destinationGalleryPrimary(gallery);
  },

  _searchPitchOptionImages(dayId, optionId) {
    const day = this._findDay(dayId);
    const option = this._pitchPlan(day).options.find((item) => item.id === optionId);
    if (!option) return;
    void this._searchImages({
      targetType: "stop",
      dayId,
      stopId: this._pitchOptionGalleryKey(option),
      query: cleanText(option.place_query) || cleanText(option.name),
      location: option.location || {},
    });
  },

  _deletePitchOptionGallery(optionId) {
    const key = `option:${optionId}`;
    if (!this._destinationGalleryForStop(key)) return;
    void this._runAction("delete_destination_gallery", {
      trip_id: this._selectedTripId,
      stop_id: key,
    }, "", { refresh: false });
  },

  _pitchCurrentDayId() {
    const days = this._data?.days?.days || [];
    if (days.some((day) => day.id === this._pitchSelectedDayId)) return this._pitchSelectedDayId;
    const nextDayId = this._data?.summary?.next_day?.id;
    if (nextDayId && days.some((day) => day.id === nextDayId)) return nextDayId;
    return days[0]?.id || null;
  },

  async _loadPitchRoutes(dayId) {
    if (!dayId || this._pitchRoutesLoading) return;
    this._pitchRoutesLoading = dayId;
    this._render({ preserveScroll: true });
    const result = await this._runAction("pitch_route_overview", { day_id: dayId }, "", {
      refresh: false,
      errorTitle: "Routenübersicht fehlgeschlagen",
    });
    this._pitchRoutesLoading = null;
    if (result) {
      this._pitchRoutes = { ...(this._pitchRoutes || {}), [dayId]: result };
    }
    this._render({ preserveScroll: true });
  },

  // Fixed, theme-independent semantics for the pitch map: gray dots are the
  // day's own route, the BLUE STAR is the active overnight place, numbered
  // AMBER/VIOLET markers are the backup options (B1, B2 ...) and the GREEN
  // arrow is tomorrow's first stop. Route lines reuse the same colors.
  _pitchOptionColor(index) {
    const palette = ["#ef8f00", "#ab47bc", "#00897b", "#d81b60", "#5c6bc0"];
    return palette[index % palette.length];
  },

  _pitchRouteContext(day) {
    const days = this._data?.days?.days || [];
    const index = days.findIndex((item) => item.id === day?.id);
    const activeStop = this._pitchActiveStop(day);
    const points = this._dayRoutePoints(day).map((point) => {
      const isActive = activeStop && point.label === activeStop.name;
      return {
        ...point,
        markerLabel: isActive ? "★" : "•",
        color: isActive ? "#039be5" : "#90a4ae",
        label: isActive ? `Aktiver Platz: ${point.label}` : point.label,
      };
    });
    const fromLabel = points[0]?.label || "";
    let toLabel = "";
    const nextDay = index >= 0 ? days[index + 1] : null;
    if (nextDay) {
      const nextStops = this._canonicalStops(nextDay.stops || []);
      const nextFirst = nextStops[0];
      if (nextFirst) {
        const point = this._coordinate(nextFirst, nextDay, 0);
        toLabel = nextFirst.name || "";
        if (point) points.push({ ...point, label: `Morgen: ${toLabel}`, markerLabel: "→", color: "#43a047" });
      }
    }
    const plan = this._pitchPlan(day);
    let optionIndex = 0;
    for (const option of plan.options) {
      if (option.status === "rejected") continue;
      const color = this._pitchOptionColor(optionIndex);
      optionIndex += 1;
      if (option.location?.latitude == null || option.location?.longitude == null) continue;
      points.push({
        lat: option.location.latitude,
        lon: option.location.longitude,
        label: `Option B${optionIndex}: ${option.name}`,
        markerLabel: `B${optionIndex}`,
        color,
        timestamp: new Date(),
      });
    }
    return { points, fromLabel, toLabel, hasNextDay: Boolean(nextDay) };
  },

  _pitchOptionColorById(day) {
    const colors = new Map();
    let optionIndex = 0;
    for (const option of this._pitchPlan(day).options) {
      if (option.status === "rejected") continue;
      colors.set(option.id, this._pitchOptionColor(optionIndex));
      optionIndex += 1;
    }
    return colors;
  },

  async _runPitchAction(action, dayId, payload, successMessage) {
    return this._runAction(action, {
      day_id: dayId,
      payload,
      expected_revision: this._currentRevision(),
    }, successMessage);
  },

  _activatePitchOption(dayId, optionId) {
    const day = this._findDay(dayId);
    const plan = this._pitchPlan(day);
    const option = plan.options.find((item) => item.id === optionId);
    if (!option) return;
    const stop = this._pitchActiveStop(day);
    const lines = [`„${option.name}" wird der neue Übernachtungsstopp dieses Tages.`];
    if (stop) {
      lines.push(`Der bisherige Platz „${stop.name}" bleibt als Backup-Option erhalten.`);
      const { media, documents } = this._pitchLinkedCounts(stop.id);
      if (media || documents) {
        const parts = [];
        if (media) parts.push(`${media} ${media === 1 ? "Foto" : "Fotos"}`);
        if (documents) parts.push(`${documents} ${documents === 1 ? "Dokument" : "Dokumente"}`);
        lines.push(`Achtung: Am bisherigen Stopp ${media + documents === 1 ? "hängt" : "hängen"} ${parts.join(" und ")}. Diese bleiben am Stopp und gehören danach möglicherweise zum falschen Ort - bitte anschließend unter Erinnerungen bzw. Dokumente prüfen.`);
      }
    }
    this._confirm(
      "Plan B aktivieren?",
      lines.join(" "),
      "Jetzt wechseln",
      async () => {
        const result = await this._runPitchAction("pitch_option_activate", dayId, { option_id: optionId }, "");
        if (!result) return;
        this._showToast("Plan B aktiviert. Der Übernachtungsplatz wurde getauscht.", "success", 5000);
        // The option just became a real stop; its planning gallery lived
        // under the option key and would otherwise be orphaned - the stop
        // now runs the normal machinery (internet images before the visit,
        // personal OneDrive photos afterwards).
        this._deletePitchOptionGallery(optionId);
        if (day?.routing && this._data?.settings?.routing_configured) {
          void this._calculateDayRoute(dayId, true);
        }
      },
    );
  },

  _renderPitches() {
    const days = this._data?.days?.days || [];
    if (!days.length) {
      return `${this._renderReadOnlyNotice()}<div class="empty-state"><ha-icon icon="mdi:caravan"></ha-icon><h2>Noch keine Reisetage</h2><p>Lege zuerst Reisetage an - danach kannst du hier je Tag mehrere Übernachtungs-Optionen pflegen.</p></div>`;
    }
    const canEdit = this._canEdit();
    const currentDayId = this._pitchCurrentDayId();
    const day = days.find((item) => item.id === currentDayId) || days[0];
    return `${this._renderReadOnlyNotice()}
      <section class="toolbar-card day-toolbar">
        <div>
          <span class="eyebrow">Übernachtung</span>
          <h2>Stellplätze</h2>
          <p>Ist der aktuelle Platz voll oder laut, wird mit einem Tipp der nächste aktiviert - der bisherige bleibt als Backup erhalten.</p>
        </div>
        <label class="day-select"><span>Reisetag</span><select data-action="pitch-select-day">${days.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === day.id ? "selected" : ""}>${item.sequence}. ${escapeHtml(item.title)}</option>`).join("")}</select></label>
      </section>
      ${this._renderPitchDayCard(day, canEdit)}
      ${this._renderPitchPreferencesCard(canEdit)}`;
  },

  _renderPitchPreferencesCard(canEdit) {
    const preferences = this._pitchPreferences();
    const required = preferences.required || {};
    const preferred = preferences.preferred || {};
    const limits = preferences.limits || {};
    const vehicle = preferences.vehicle || {};
    const weightOptions = (key) => {
      const isRequired = required[key] === true;
      const weight = Number.isInteger(preferred[key]) ? preferred[key] : 0;
      return `<option value="0" ${!isRequired && !weight ? "selected" : ""}>Egal</option>
        ${[1, 2, 3, 4, 5].map((value) => `<option value="${value}" ${!isRequired && weight === value ? "selected" : ""}>Wichtig (${value})</option>`).join("")}
        <option value="required" ${isRequired ? "selected" : ""}>Muss</option>`;
    };
    return `<details class="panel-card pitch-preferences"><summary><ha-icon icon="mdi:tune-variant"></ha-icon> Stellplatz-Präferenzen (für Suche und spätere Bewertung)</summary>
      <form data-form="pitch-preferences" class="form-grid">
        ${PITCH_FEATURES.map(([key, label]) => `<label class="form-field"><span>${escapeHtml(label)}</span><select name="feature_${key}" ${canEdit ? "" : "disabled"}>${weightOptions(key)}</select></label>`).join("")}
        <label class="form-field"><span>Max. Preis/Nacht (€)</span><input type="number" name="max_price_per_night" min="0" step="1" value="${limits.max_price_per_night ?? ""}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field"><span>Max. Umweg (km)</span><input type="number" name="max_detour_km" min="0" step="1" value="${limits.max_detour_km ?? ""}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field"><span>Max. Umweg (Minuten)</span><input type="number" name="max_detour_minutes" min="0" step="1" value="${limits.max_detour_minutes ?? ""}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field"><span>Fahrzeughöhe (m)</span><input type="number" name="height_m" min="0" step="0.01" value="${vehicle.height_m ?? ""}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field"><span>Fahrzeuglänge (m)</span><input type="number" name="length_m" min="0" step="0.01" value="${vehicle.length_m ?? ""}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field full"><span>Vermeiden (kommagetrennt)</span><input type="text" name="avoid" value="${escapeHtml((preferences.avoid || []).join(", "))}" ${canEdit ? "" : "disabled"}></label>
        <label class="form-field full"><span>Freitext für den Reisebegleiter</span><textarea name="free_text" rows="2" ${canEdit ? "" : "disabled"}>${escapeHtml(preferences.free_text || "")}</textarea></label>
        ${canEdit ? `<div class="form-actions full"><button class="primary-button" type="submit">Präferenzen speichern</button></div>` : ""}
      </form>
    </details>`;
  },

  _renderPitchDayCard(day, canEdit) {
    const plan = this._pitchPlan(day);
    const stop = this._pitchActiveStop(day);
    let backups = plan.options.filter((item) => item.status !== "rejected");
    const rejected = plan.options.filter((item) => item.status === "rejected");
    const isToday = day.id === this._data?.summary?.next_day?.id;
    const context = this._pitchRouteContext(day);
    const routes = this._pitchRoutes?.[day.id] || null;
    const routeByOption = new Map((routes?.candidates || []).map((candidate) => [candidate.option_id, candidate]));
    if (routes && plan.strategy === "route_optimal") {
      // Strategy is rank-only: with real detours available, route_optimal
      // orders the list by extra travel time. Options without a computed
      // route keep their manual order at the end.
      backups = [...backups].sort((left, right) => {
        const a = routeByOption.get(left.id)?.extra_duration_minutes;
        const b = routeByOption.get(right.id)?.extra_duration_minutes;
        if (a == null && b == null) return 0;
        if (a == null) return 1;
        if (b == null) return -1;
        return a - b;
      });
    }
    const optionColors = this._pitchOptionColorById(day);
    const routePaths = (routes?.candidates || [])
      .filter((candidate) => Array.isArray(candidate.geometry) && candidate.geometry.length > 1)
      .map((candidate) => ({
        title: `${candidate.name}${candidate.extra_duration_minutes != null ? ` (+${candidate.extra_duration_minutes} min)` : ""}`,
        mode: "driving",
        // The active place's route stays blue; each option's route uses the
        // SAME color as its B-marker, so line and marker read as one unit.
        color: candidate.kind === "active"
          ? "#039be5"
          : optionColors.get(candidate.option_id) || "#ef8f00",
        points: candidate.geometry.map(([lon, lat]) => ({ lat, lon })),
      }));
    const optionsWithoutGps = backups.filter(
      (option) => option.location?.latitude == null || option.location?.longitude == null,
    );
    const routeSummary = routes
      ? `<div class="notice neutral pitch-route-summary"><ha-icon icon="mdi:routes"></ha-icon><div><strong>Umwege je Kandidat${routes.baseline ? ` · direkte Strecke ${routes.baseline.duration_minutes} min / ${routes.baseline.distance_km} km` : ""}</strong><span>${(routes.candidates || []).map((candidate) => `<span class="pitch-route-dot" style="background:${candidate.kind === "active" ? "#039be5" : (optionColors.get(candidate.option_id) || "#ef8f00")}"></span>${candidate.kind === "active" ? "★ " : ""}${escapeHtml(candidate.name)}: ${candidate.extra_duration_minutes != null ? `+${candidate.extra_duration_minutes} min · +${candidate.extra_distance_km} km` : `${candidate.duration_minutes} min · ${candidate.distance_km} km`}`).join(" · ")}</span>${routes.skipped?.length ? `<small>Ohne GPS übersprungen: ${routes.skipped.map((name) => escapeHtml(name)).join(", ")}</small>` : ""}${routes.errors?.length ? `<small>Fehler: ${routes.errors.map((text) => escapeHtml(text)).join(" · ")}</small>` : ""}</div></div>`
      : "";
    return `<section class="panel-card pitch-day-card">
      <div class="section-heading compact">
        <div>
          <span class="eyebrow">${isToday ? "Aktueller Reisetag" : escapeHtml(this._formatDate(day.date) || `Tag ${day.sequence}`)}</span>
          <h2>${day.sequence}. ${escapeHtml(day.title)}</h2>
        </div>
        <label class="day-select"><span>Strategie</span><select data-action="pitch-strategy" data-day-id="${escapeHtml(day.id)}" ${canEdit ? "" : "disabled"}>
          ${PITCH_STRATEGIES.map(([value, label]) => `<option value="${value}" ${plan.strategy === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("")}
        </select></label>
      </div>
      ${context.fromLabel || context.toLabel ? `<div class="pitch-route-flow"><span><ha-icon icon="mdi:map-marker-outline"></ha-icon> ${escapeHtml(context.fromLabel || "?")}</span><ha-icon icon="mdi:arrow-right-thin"></ha-icon><span><ha-icon icon="mdi:weather-night"></ha-icon> ${escapeHtml(stop?.name || "Übernachtung")}</span>${context.hasNextDay ? `<ha-icon icon="mdi:arrow-right-thin"></ha-icon><span><ha-icon icon="mdi:map-marker-outline"></ha-icon> ${escapeHtml(context.toLabel || "?")}</span>` : ""}</div>` : ""}
      ${this._renderMap(`pitch-map-${day.id}`, context.points, day.title, routePaths, "★ blau = aktiver Platz · B1/B2 farbig = Optionen (Routenlinie in derselben Farbe) · → grün = erster Stopp von morgen · • grau = übrige Tagesroute.")}
      <div class="button-row compact-row"><button class="secondary-button" type="button" data-action="pitch-routes" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:routes"></ha-icon> ${routes ? "Routen aktualisieren" : "Routen je Option berechnen"}</button>${this._pitchRoutesLoading === day.id ? `<span class="hint">Routen werden berechnet …</span>` : ""}</div>
      ${routeSummary}
      ${stop
        ? `<div class="setting-row pitch-active-row"><span><ha-icon icon="mdi:weather-night"></ha-icon> Aktiver Platz</span><strong>${escapeHtml(stop.name)}</strong></div>`
        : `<div class="notice neutral">Dieser Tag hat noch keinen Übernachtungsstopp. Beim Aktivieren einer Option wird er automatisch angelegt.</div>`}
      ${optionsWithoutGps.length
        ? `<div class="notice neutral"><ha-icon icon="mdi:map-marker-off-outline"></ha-icon><span>Ohne GPS nicht auf der Karte: ${optionsWithoutGps.map((option) => escapeHtml(option.name)).join(", ")} - über „Bearbeiten" → „Link lesen und übernehmen" die Koordinaten holen.</span></div>`
        : ""}
      ${backups.length
        ? `<h3 class="pitch-options-heading"><ha-icon icon="mdi:format-list-bulleted"></ha-icon> Backup-Optionen (${backups.length})</h3>
          <ul class="crew-list pitch-option-list">${backups.map((option) => this._renderPitchOptionRow(day, option, canEdit)).join("")}</ul>`
        : `<p class="hint">Noch keine Backup-Option für diesen Tag${stop ? " - wenn der Platz voll ist, gibt es keinen Plan B" : ""}.</p>`}
      ${rejected.length ? `<details class="crew-retired"><summary>Verworfene Optionen (${rejected.length})</summary><ul class="crew-list pitch-option-list">${rejected.map((option) => this._renderPitchOptionRow(day, option, canEdit)).join("")}</ul></details>` : ""}
      ${canEdit ? `<div class="button-row"><button class="secondary-button" type="button" data-action="pitch-add-option" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon> Option hinzufügen</button></div>` : ""}
    </section>`;
  },

  _renderPitchOptionRow(day, option, canEdit) {
    const rejectedOption = option.status === "rejected";
    const featureLabels = PITCH_FEATURES.filter(([key]) => option.features?.[key] === true).map(([, label]) => label);
    const meta = [];
    if (option.price?.amount != null) meta.push(`${option.price.amount} ${escapeHtml(option.price.currency || "EUR")}/Nacht`);
    if (featureLabels.length) meta.push(featureLabels.join(" · "));
    if (option.location?.latitude != null) meta.push("GPS vorhanden");
    const routeInfo = (this._pitchRoutes?.[day.id]?.candidates || []).find((candidate) => candidate.option_id === option.id) || null;
    const routeChip = routeInfo
      ? `<span class="pitch-chip pitch-chip-route"><ha-icon icon="mdi:routes"></ha-icon>${routeInfo.extra_duration_minutes != null ? `+${routeInfo.extra_duration_minutes} min · +${routeInfo.extra_distance_km} km Umweg` : `${routeInfo.duration_minutes} min · ${routeInfo.distance_km} km`}</span>`
      : "";
    const prosChips = (option.pros || []).map((text) => `<span class="pitch-chip pitch-chip-pro"><ha-icon icon="mdi:plus"></ha-icon>${escapeHtml(text)}</span>`).join("");
    const consChips = (option.cons || []).map((text) => `<span class="pitch-chip pitch-chip-con"><ha-icon icon="mdi:minus"></ha-icon>${escapeHtml(text)}</span>`).join("");
    const cover = this._pitchOptionCover(option);
    const coverUrl = cover ? this._safeUrl(cover.thumbnail_url || cover.image_url) : "";
    return `<li class="crew-row ${rejectedOption ? "inactive" : ""}">
      ${coverUrl
        ? `<img class="pitch-option-cover" src="${escapeHtml(coverUrl)}" alt="${escapeHtml(option.name)}" loading="lazy">`
        : `<ha-icon icon="mdi:caravan"></ha-icon>`}
      <div class="crew-row-body">
        <strong>${option.status !== "rejected" && this._pitchOptionColorById(day).has(option.id) ? `<span class="pitch-route-dot" style="background:${this._pitchOptionColorById(day).get(option.id)}"></span>` : ""}${escapeHtml(option.name)}</strong>
        ${meta.length ? `<span>${meta.map((item) => escapeHtml(item)).join(" · ")}</span>` : ""}
        ${routeChip || prosChips || consChips ? `<div class="pitch-chip-row">${routeChip}${prosChips}${consChips}</div>` : ""}
        ${option.notes ? `<span>${escapeHtml(option.notes)}</span>` : ""}
      </div>
      ${canEdit ? `<div class="button-row">
        ${rejectedOption
          ? `<button class="secondary-button" type="button" data-action="pitch-restore" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}" aria-label="${escapeHtml(option.name)} wiederherstellen"><ha-icon icon="mdi:archive-arrow-up-outline"></ha-icon> Wiederherstellen</button>`
          : `<button class="secondary-button" type="button" data-action="pitch-activate" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}"><ha-icon icon="mdi:swap-horizontal"></ha-icon> Aktivieren</button>
            <button class="secondary-button" type="button" data-action="pitch-option-images" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}" aria-label="Bilder für ${escapeHtml(option.name)}"><ha-icon icon="mdi:image-search-outline"></ha-icon> Bilder</button>
            <button class="secondary-button" type="button" data-action="pitch-edit-option" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}" aria-label="${escapeHtml(option.name)} bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon> Bearbeiten</button>
            <button class="secondary-button" type="button" data-action="pitch-reject" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}" aria-label="${escapeHtml(option.name)} verwerfen"><ha-icon icon="mdi:archive-outline"></ha-icon> Verwerfen</button>`}
        <button class="text-button danger-text" type="button" data-action="pitch-delete" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(option.id)}" aria-label="${escapeHtml(option.name)} löschen"><ha-icon icon="mdi:trash-can-outline"></ha-icon> Löschen</button>
      </div>` : ""}
    </li>`;
  },

  _renderPitchOptionForm(dialog) {
    const option = dialog.option || {};
    const add = !dialog.option;
    const location = option.location || {};
    return `${this._renderModalHeader(add ? "Stellplatz-Option hinzufügen" : "Stellplatz-Option bearbeiten")}
      <form data-form="pitch-option" data-day-id="${escapeHtml(dialog.dayId)}" data-option-id="${escapeHtml(option.id || "")}" data-source-url="${escapeHtml(option.source?.url || "")}" class="form-grid">
        ${this._field("url", "Link zum Platz (Park4Night, Google Maps, Website …)", option.source?.url || "", "url", false, "full")}
        <div class="form-field full"><button class="secondary-button" type="button" data-action="pitch-option-link-lookup"><ha-icon icon="mdi:link-variant"></ha-icon>Link lesen und übernehmen</button><small class="hint">Park4Night-Seiten werden direkt gelesen, Google-Maps-Links aufgelöst, andere Seiten liest der Reisebegleiter (KI). Die Werte füllen nur dieses Formular vor - gespeichert wird erst mit dem Speichern-Knopf.</small><small class="hint" data-link-diagnosis hidden></small></div>
        ${this._field("name", "Name des Platzes", option.name || "", "text", true, "full")}
        ${this._field("place_query", "Suchbegriff für Karte/Geocoding (optional)", option.place_query || "", "text", false, "full")}
        ${this._field("latitude", "Breitengrad (optional)", location.latitude ?? "", "number")}
        ${this._field("longitude", "Längengrad (optional)", location.longitude ?? "", "number")}
        ${this._field("price_amount", "Preis pro Nacht (€, optional)", option.price?.amount ?? "", "number")}
        <div class="form-field full crew-checkbox-group">${PITCH_FEATURES.map(([key, label]) => `<label class="checkbox-field"><input type="checkbox" name="feature_${key}" ${option.features?.[key] === true ? "checked" : ""}><span>${escapeHtml(label)}</span></label>`).join("")}</div>
        ${this._textarea("pros", "Vorteile (eine je Zeile, max. 4)", (option.pros || []).join("\n"), "full")}
        ${this._textarea("cons", "Nachteile (eine je Zeile, max. 4)", (option.cons || []).join("\n"), "full")}
        ${this._textarea("notes", "Notizen", option.notes || "", "full")}
        ${this._formActions(add ? "Option speichern" : "Änderungen speichern")}
      </form>`;
  },

  _renderPlanBCard(day) {
    const backups = this._pitchBackups(day);
    if (!backups.length || !this._canEdit()) return "";
    const next = backups[0];
    const meta = [];
    if (next.price?.amount != null) meta.push(`${next.price.amount} ${next.price.currency || "EUR"}/Nacht`);
    else if (next.price?.amount === 0) meta.push("kostenlos");
    if (next.notes) meta.push(next.notes);
    return `<section class="panel-card pitch-plan-b">
      <div class="section-heading compact">
        <div>
          <span class="eyebrow">Übernachtung</span>
          <h2>Plan B für diesen Tag</h2>
        </div>
      </div>
      ${(() => {
        const cover = this._pitchOptionCover(next);
        const coverUrl = cover ? this._safeUrl(cover.thumbnail_url || cover.image_url) : "";
        return coverUrl ? `<img class="pitch-plan-b-cover" src="${escapeHtml(coverUrl)}" alt="${escapeHtml(next.name)}" loading="lazy">` : "";
      })()}
      <div class="setting-row"><span><ha-icon icon="mdi:caravan"></ha-icon> ${escapeHtml(next.name)}</span><strong>${escapeHtml(meta.join(" · ") || `${backups.length} ${backups.length === 1 ? "Option" : "Optionen"} hinterlegt`)}</strong></div>
      <div class="button-row">
        <button class="primary-button" type="button" data-action="pitch-activate" data-day-id="${escapeHtml(day.id)}" data-option-id="${escapeHtml(next.id)}"><ha-icon icon="mdi:swap-horizontal"></ha-icon> Plan B aktivieren</button>
        ${backups.length > 1 ? `<button class="secondary-button" type="button" data-action="pitch-open-tab" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:format-list-bulleted"></ha-icon> Alle ${backups.length} Optionen</button>` : `<button class="secondary-button" type="button" data-action="pitch-open-tab" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:format-list-bulleted"></ha-icon> Stellplätze verwalten</button>`}
      </div>
    </section>`;
  },

  async _runPitchOptionLinkLookup(form) {
    if (!form) return;
    const input = form.querySelector("input[name='url']");
    const url = cleanText(input?.value);
    if (!url) {
      this._showToast("Bitte zuerst einen Link einfügen (https://…).", "error", 5000);
      return;
    }
    const diagnosisElement = form.querySelector("[data-link-diagnosis]");
    if (diagnosisElement) {
      // Visible the moment the button is pressed, so a lookup that never
      // reaches the backend is distinguishable from one that returns empty.
      diagnosisElement.textContent = "Link wird gelesen …";
      diagnosisElement.hidden = false;
    }
    const result = await this._runAction("place_link_lookup", { url }, "", {
      refresh: false,
      errorTitle: "Link konnte nicht gelesen werden",
    });
    const facts = result?.result;
    // What the read actually did stays visible in the form (not only in a
    // toast that fades): a lookup that "runs without an error but does
    // nothing" is otherwise impossible to diagnose from a phone.
    if (diagnosisElement) {
      const diagnosis = cleanText(facts?.diagnosis);
      diagnosisElement.textContent = diagnosis
        ? `Leseergebnis - ${diagnosis}`
        : "Leseergebnis - keine Auskunft erhalten (ältere Version?)";
    }
    if (!facts) return;
    // Prefill only - the option is saved exclusively via the submit button.
    const setValue = (name, value, overwrite = false) => {
      const element = form.querySelector(`[name='${name}']`);
      if (element && (overwrite || !cleanText(element.value))) element.value = value;
    };
    if (facts.name) setValue("name", facts.name);
    if (facts.latitude != null && facts.longitude != null) {
      setValue("latitude", String(facts.latitude), true);
      setValue("longitude", String(facts.longitude), true);
      setValue("place_query", `${facts.latitude},${facts.longitude}`);
    }
    const summary = [facts.price_text, facts.rating_text ? `Bewertung ${facts.rating_text}` : "", facts.summary]
      .filter(Boolean).join(" · ");
    if (summary) setValue("notes", summary);
    const gotSomething = facts.latitude != null || Boolean(cleanText(facts.name)) || Boolean(summary);
    this._showToast(
      facts.latitude != null
        ? "Vom Link übernommen - bitte prüfen und speichern."
        : gotSomething
          ? "Der Link nannte keine GPS-Position - Name/Notizen wurden vorbefüllt."
          : "Der Link gab nichts Übernehmbares her - siehe Leseergebnis unter dem Knopf.",
      facts.latitude != null ? "success" : "error",
      6000,
    );
  },

  async _submitPitchOptionForm(form, values) {
    const dayId = cleanText(form.dataset.dayId);
    const optionId = cleanText(form.dataset.optionId);
    const option = {
      name: cleanText(values.name),
      place_query: cleanText(values.place_query),
      notes: cleanText(values.notes),
      pros: String(values.pros || "").split("\n").map((item) => cleanText(item)).filter(Boolean).slice(0, 4),
      cons: String(values.cons || "").split("\n").map((item) => cleanText(item)).filter(Boolean).slice(0, 4),
      features: Object.fromEntries(
        PITCH_FEATURES
          .filter(([key]) => form.querySelector(`input[name='feature_${key}']`)?.checked)
          .map(([key]) => [key, true]),
      ),
    };
    if (optionId) option.id = optionId;
    const sourceUrl = cleanText(values.url);
    if (sourceUrl !== cleanText(form.dataset.sourceUrl)) {
      // Only when the link actually changed: an untouched assistant option
      // keeps its stored provenance (e.g. park4night) instead of being
      // rewritten to a plain user source on every edit.
      option.source = sourceUrl ? { type: "user", url: sourceUrl } : {};
    }
    const latitude = Number.parseFloat(values.latitude);
    const longitude = Number.parseFloat(values.longitude);
    if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
      option.location = { latitude, longitude };
    }
    const priceAmount = Number.parseFloat(values.price_amount);
    if (Number.isFinite(priceAmount)) option.price = { amount: priceAmount, currency: "EUR" };
    const result = await this._runPitchAction("pitch_option_save", dayId, { option }, optionId ? "Option aktualisiert" : "Option hinzugefügt");
    if (result) this._closeDialog({ flushRefresh: false });
  },

  async _submitPitchPreferencesForm(form, values) {
    const required = {};
    const preferred = {};
    for (const [key] of PITCH_FEATURES) {
      const value = cleanText(values[`feature_${key}`]);
      if (value === "required") required[key] = true;
      else if (Number.parseInt(value, 10) > 0) preferred[key] = Number.parseInt(value, 10);
    }
    const limits = {};
    for (const field of ["max_price_per_night", "max_detour_km", "max_detour_minutes"]) {
      const value = Number.parseFloat(values[field]);
      if (Number.isFinite(value)) limits[field] = value;
    }
    const vehicle = {};
    for (const field of ["height_m", "length_m"]) {
      const value = Number.parseFloat(values[field]);
      if (Number.isFinite(value)) vehicle[field] = value;
    }
    await this._runAction("pitch_update_preferences", {
      preferences: {
        required,
        preferred,
        limits,
        vehicle,
        avoid: String(values.avoid || "").split(",").map((item) => cleanText(item)).filter(Boolean),
        free_text: cleanText(values.free_text),
      },
      expected_revision: this._currentRevision(),
    }, "Stellplatz-Präferenzen gespeichert");
  },
};
