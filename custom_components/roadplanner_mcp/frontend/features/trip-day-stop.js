import { escapeHtml, cleanText, cloneObject } from "../lib/core-helpers.js";
import { stopIcons } from "../lib/constants.js";

export const tripDayStopMixin = {
  _isOvernightStop(stop) {
    return ["overnight", "campsite", "camping", "stellplatz", "wildcamp", "accommodation"]
      .includes(cleanText(stop?.type).toLowerCase());
  },

  _stopTimeMinutes(stop) {
    const value = cleanText(stop?.arrival_time || stop?.departure_time);
    const match = /^(\d{2}):(\d{2})(?::\d{2})?$/.exec(value);
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) return null;
    return hour * 60 + minute;
  },

  _canonicalStops(stops) {
    const values = Array.isArray(stops) ? stops.filter((stop) => stop && typeof stop === "object") : [];
    if (values.length < 2) return [...values];
    const positions = values.map((stop) => Number(stop.position));
    const completePositions = positions.every((position) => Number.isInteger(position) && position > 0)
      && new Set(positions).size === values.length;
    if (completePositions) {
      return values.map((stop, index) => ({ stop, index }))
        .sort((left, right) => Number(left.stop.position) - Number(right.stop.position) || left.index - right.index)
        .map((item) => item.stop);
    }
    // Schedule times are descriptive only. The stored Roadbook list order is
    // the canonical fallback whenever a legacy day has incomplete positions.
    return [...values];
  },

  _stopOrderState(day, stopId = "") {
    const stops = this._dayRoadbookStops(day);
    const normalizedStopId = cleanText(stopId);
    const index = normalizedStopId
      ? stops.findIndex((stop) => cleanText(stop?.id) === normalizedStopId)
      : -1;
    return {
      stops,
      index,
      position: index >= 0 ? index + 1 : null,
      canMoveEarlier: index > 0,
      canMoveLater: index >= 0 && index < stops.length - 1,
    };
  },

  _stopMovePosition(day, stopId, delta) {
    const state = this._stopOrderState(day, stopId);
    if (!Number.isInteger(delta) || ![-1, 1].includes(delta) || state.index < 0) return null;
    const position = state.index + 1 + delta;
    if (position < 1 || position > state.stops.length) return null;
    return position;
  },

  async _moveStop(dayId, stopId, position) {
    const day = this._findDay(dayId);
    const state = this._stopOrderState(day, stopId);
    const targetPosition = Number(position);
    if (!day || state.index < 0 || !Number.isInteger(targetPosition)
      || targetPosition < 1 || targetPosition > state.stops.length
      || targetPosition === state.position) return null;
    return this._runAction("update_stop", {
      day_id: dayId,
      stop_id: stopId,
      patch: {},
      position: targetPosition,
      expected_revision: this._currentRevision(),
    }, "Stopp verschoben");
  },

  _samePlace(first, second) {
    if (!first || !second) return false;
    if (first.id && first.id === second.id) return true;
    const firstName = cleanText(first.name).toLowerCase();
    const secondName = cleanText(second.name).toLowerCase();
    if (firstName && firstName === secondName) return true;
    const firstCoordinate = this._coordinate(first);
    const secondCoordinate = this._coordinate(second);
    if (!firstCoordinate || !secondCoordinate) return false;
    return Math.abs(firstCoordinate.lat - secondCoordinate.lat) < 0.00005
      && Math.abs(firstCoordinate.lon - secondCoordinate.lon) < 0.00005;
  },

  _canonicalDayModel(day) {
    return day?.canonical && typeof day.canonical === "object" && !Array.isArray(day.canonical)
      ? day.canonical
      : null;
  },

  _dayRoadbookStops(day) {
    const model = this._canonicalDayModel(day);
    if (Array.isArray(model?.stops)) return model.stops;
    return this._canonicalStops(day?.stops || []);
  },

  _effectiveDayStops(day) {
    const model = this._canonicalDayModel(day);
    if (Array.isArray(model?.route_nodes)) return model.route_nodes;
    const canonicalStops = this._canonicalStops(day?.stops || []);
    const days = this._data?.days?.days || [];
    const index = days.findIndex((item) => item.id === day?.id);
    if (index <= 0) return canonicalStops.map((stop, stopIndex) => ({ ...stop, display_sequence: stopIndex + 1, marker_label: String(stopIndex + 1) }));
    const previous = days[index - 1];
    const previousStops = this._canonicalStops(previous?.stops || []);
    const overnight = previousStops.at(-1);
    if (!this._isOvernightStop(overnight)) return canonicalStops.map((stop, stopIndex) => ({ ...stop, display_sequence: stopIndex + 1, marker_label: String(stopIndex + 1) }));
    if (canonicalStops.length && this._samePlace(overnight, canonicalStops[0])) {
      return canonicalStops.map((stop, stopIndex) => ({ ...stop, display_sequence: stopIndex + 1, marker_label: String(stopIndex + 1) }));
    }
    return [{
      ...cloneObject(overnight),
      display_sequence: null,
      marker_label: "S",
      _inherited: true,
      _sourceDayId: previous.id,
      _sourceDayTitle: previous.title,
    }, ...canonicalStops.map((stop, stopIndex) => ({ ...stop, display_sequence: stopIndex + 1, marker_label: String(stopIndex + 1) }))];
  },

  _displayStopSequence(stop, fallback) {
    if (stop?._inherited) return "S";
    const value = Number(stop?.display_sequence);
    return Number.isInteger(value) && value > 0 ? value : fallback;
  },

  _renderTripSelect() {
    const trips = (this._data?.trips?.trips || []).filter((trip) => trip.valid);
    if (!trips.length) return "";
    return `<label class="trip-select" title="Reise auswählen">
      <ha-icon icon="mdi:map-multiple-outline"></ha-icon>
      <select data-action="select-trip" aria-label="Reise auswählen">
        ${trips.map((trip) => `<option value="${escapeHtml(trip.id)}" ${trip.id === this._selectedTripId ? "selected" : ""}>${escapeHtml(trip.title)}${trip.active ? " · aktiv" : ""}</option>`).join("")}
      </select>
    </label>`;
  },

  _renderExperienceAlbum(media, { dayId = "", stopId = "", compact = false, title = "Reisefotos", totalCount = null } = {}) {
    if (!Array.isArray(media) || !media.length) return "";
    const highlights = media.slice(0, compact ? 3 : 5);
    const cover = media.find((item) => item.is_cover) || media[0];
    const count = Number.isInteger(Number(totalCount)) && Number(totalCount) > 0 ? Number(totalCount) : media.length;
    const selectionMode = cleanText(this._experiencePresentation().selection_mode_by_stop?.[stopId]);
    const modeLabel = selectionMode.includes("hybrid_vision")
      ? "Lokal vorgefiltert · KI kuratiert"
      : "Lokal nach Qualität, Dubletten und Serien ausgewählt";
    const canVisionCurate = Boolean(stopId && this._canEdit() && this._data?.settings?.media_vision_enabled && count > 1);
    return `<section class="experience-album ${compact ? "compact" : ""}">
      <div class="experience-album-heading"><div><span class="eyebrow">Unsere Fotos</span><strong>${escapeHtml(title)}</strong><small>${highlights.length} Highlights aus ${count} ${count === 1 ? "Foto" : "Fotos"} · ${escapeHtml(modeLabel)}</small></div><div class="experience-album-actions"><button class="text-button" type="button" data-action="media-open-album" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-media-id="${escapeHtml(cover.id)}">Alle ansehen</button>${canVisionCurate ? `<button class="text-button" type="button" data-action="media-curate-stop" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}"><ha-icon icon="mdi:creation-outline"></ha-icon>Neu bewerten</button>` : ""}</div></div>
      <div class="experience-album-strip">${highlights.map((item) => `<button class="experience-album-thumb ${item.is_cover ? "cover" : ""}" type="button" data-action="media-open-album" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-media-id="${escapeHtml(item.id)}"><img src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}" loading="lazy">${item.is_cover ? `<ha-icon icon="mdi:star"></ha-icon>` : ""}</button>`).join("")}</div>
    </section>`;
  },

  _renderOverview() {
    const summary = this._data.summary;
    const trip = summary.trip;
    const nextDay = summary.next_day || this._findDay(this._selectedDayId) || this._data?.days?.days?.[0];
    const handoffs = this._data.handoffs;
    const settings = this._data.settings;
    const days = this._data?.days?.days || [];
    const plannedDays = days.filter((day) => this._dayRoadbookStops(day).length > 0).length;
    const planningProgress = days.length ? Math.round((plannedDays / days.length) * 100) : 0;
    const openDecisions = Number(this._experienceData().stats?.open_decision_count || 0);
    const integrity = this._integrityData();
    const todoTiming = this._todoTimingSummary();
    const galleries = this._destinationGalleries();
    const ownByStop = this._experienceData().by_stop || {};
    const missingVisuals = days.flatMap((day) => this._dayRoadbookStops(day)).filter((stop) => {
      const own = ownByStop?.[stop.id];
      const gallery = galleries?.[stop.id];
      return !(Array.isArray(own) && own.length) && !this._destinationGalleryImages(gallery).length;
    }).length;
    const heroMedia = this._tripCoverImage();
    const now = new Date();
    const start = trip.start_date ? new Date(`${trip.start_date}T00:00:00`) : null;
    const end = trip.end_date ? new Date(`${trip.end_date}T23:59:59`) : null;
    const phase = start && now < start ? "Planen" : end && now > end ? "Erinnern" : start && end ? "Unterwegs" : "Planen";
    const distance = summary.total_distance_km != null ? `${summary.total_distance_km} km` : "≈ offen";
    return `
      ${this._renderReadOnlyNotice()}
      <section class="hero-card journey-hero ${heroMedia ? "with-image" : ""}">
        ${heroMedia ? `<div class="hero-image">${this._renderDestinationImage(heroMedia, { compact: false })}</div>` : ""}
        <div class="hero-copy">
          <span class="eyebrow">${escapeHtml(phase)} · ${this._data.selected_is_active ? "Aktive Reise" : "Ausgewählte Reise"}</span>
          <h2>${escapeHtml(trip.title)}</h2>
          <p>${escapeHtml(trip.notes || "Roadplanner begleitet euch von der Planung bis zu den Erinnerungen.")}</p>
          <div class="planning-progress" aria-label="Planungsfortschritt ${planningProgress} Prozent"><span style="width:${planningProgress}%"></span></div>
          <div class="hero-meta">
            <span><ha-icon icon="mdi:calendar-range"></ha-icon>${escapeHtml(trip.start_date || "offen")} – ${escapeHtml(trip.end_date || "offen")}</span>
            <span><ha-icon icon="mdi:progress-check"></ha-icon>${plannedDays}/${days.length || summary.day_count || 0} Tage mit Stopps</span>
          </div>
          <div class="button-row">
            <button class="primary-button" type="button" data-tab="day-route"><ha-icon icon="mdi:white-balance-sunny"></ha-icon> Heute öffnen</button>
            <button class="secondary-button" type="button" data-tab="assistant"><ha-icon icon="mdi:message-processing-outline"></ha-icon> Reisebegleiter</button>
            ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="edit-trip"><ha-icon icon="mdi:pencil-outline"></ha-icon> Reise bearbeiten</button>` : ""}
          </div>
        </div>
      </section>

      <section class="stat-grid planning-stats" aria-label="Reiseplanung">
        ${this._statCard("mdi:progress-check", `${planningProgress}%`, "Planungsstand")}
        ${this._statCard("mdi:road-variant", distance, summary.total_distance_km != null ? "berechnete Strecke" : "grobe Strecke offen")}
        ${this._statCard("mdi:cards-playing-outline", openDecisions, "offene Entscheidungen")}
        ${this._statCard("mdi:checkbox-marked-circle-auto-outline", todoTiming.urgent || 0, todoTiming.urgent ? "heute / überfällig" : "nichts Dringendes")}
      </section>

      ${this._renderIntegrityCard(integrity)}

      ${nextDay ? `<section class="panel-card next-journey-card">
        <div class="section-heading">
          <div><span class="eyebrow">${phase === "Unterwegs" ? "Heute" : "Als Nächstes"}</span><h2>${escapeHtml(nextDay.title)}</h2><p>${escapeHtml(this._formatDate(nextDay.date))}</p></div>
          <ha-icon icon="mdi:map-marker-distance"></ha-icon>
        </div>
        <div class="next-day-grid">
          <div><span>Route</span><strong>${escapeHtml(this._effectiveDayStart(nextDay))} → ${escapeHtml(this._effectiveDayEnd(nextDay))}</strong></div>
          <div><span>Stopps</span><strong>${this._dayRoadbookStops(nextDay).length}</strong></div>
          <div><span>Fahrzeit</span><strong>${escapeHtml(this._formatDriveMinutes(nextDay.drive_minutes) || "noch offen")}</strong></div>
        </div>
        <div class="button-row"><button class="primary-button" type="button" data-tab="day-route" data-day-id="${escapeHtml(nextDay.id)}"><ha-icon icon="mdi:map-clock-outline"></ha-icon> Tagesetappe</button>${openDecisions ? `<button class="secondary-button" type="button" data-tab="decisions"><ha-icon icon="mdi:cards-playing-outline"></ha-icon>${openDecisions} Entscheidungen</button>` : ""}</div>
      </section>` : `<section class="panel-card"><div class="empty-inline"><ha-icon icon="mdi:calendar-plus"></ha-icon><div><strong>Noch kein Reisetag geplant</strong><span>Lege einen Tag an oder plane ihn mit dem Reisebegleiter.</span></div></div></section>`}

      <section class="panel-card journey-readiness">
        <div class="section-heading compact"><div><span class="eyebrow">Reisebereitschaft</span><h2>Was noch Aufmerksamkeit braucht</h2></div></div>
        <div class="readiness-grid">
          <button type="button" data-tab="decisions"><ha-icon icon="mdi:cards-playing-outline"></ha-icon><span><strong>${openDecisions}</strong> Entscheidungen</span></button>
          <button type="button" data-tab="archive"><ha-icon icon="mdi:file-document-check-outline"></ha-icon><span><strong>${Number(this._archiveData().stats?.document_count || 0)}</strong> Dokumente</span></button>
          <button type="button" data-tab="media"><ha-icon icon="mdi:image-search-outline"></ha-icon><span><strong>${missingVisuals}</strong> Stopps ohne Bild</span></button>
          <button type="button" data-tab="handoffs"><ha-icon icon="mdi:inbox-arrow-down"></ha-icon><span><strong>${handoffs.total || 0}</strong> Übergaben</span></button>
        </div>
      </section>

      <details class="overview-technical panel-card">
        <summary><span><ha-icon icon="mdi:tools"></ha-icon>Werkzeuge & System</span><small>Import, Reisen, Routing, Rollen und Diagnose</small></summary>
        <div class="assistant-technical-content">
          <div class="button-row">
            <button class="secondary-button" type="button" data-tab="total-route"><ha-icon icon="mdi:map-marker-path"></ha-icon> Gesamtroute</button>
            <button class="secondary-button" type="button" data-tab="import"><ha-icon icon="mdi:file-import-outline"></ha-icon> Import</button>
            <button class="secondary-button" type="button" data-tab="trips"><ha-icon icon="mdi:map-multiple-outline"></ha-icon> Reisen</button>
            <button class="secondary-button" type="button" data-tab="archive"><ha-icon icon="mdi:file-document-multiple-outline"></ha-icon> Dokumente & Kosten</button>
          </div>
          <div class="settings-list">
            ${this._valueRow("Roadplanner", this._data.integration_version)}
            ${this._valueRow("Zugriff", this._statusLabel(this._data.capabilities?.role || "viewer"))}
            ${this._settingRow("Straßenrouting", settings.routing_configured)}
            ${this._settingRow("Externe Google-Drive-Bridge", settings.handoff_webhook_enabled)}
            ${this._settingRow("Automatische Planungsbilder", settings.destination_image_auto_fill)}
          </div>
          <div class="button-row">${this._canAdmin() ? `<button class="secondary-button" type="button" data-action="backup"><ha-icon icon="mdi:backup-restore"></ha-icon> Sicherung erstellen</button>` : ""}${this._data.capabilities?.can_approve ? `<button class="secondary-button" type="button" data-action="scan-handoffs"><ha-icon icon="mdi:folder-refresh-outline"></ha-icon> Übergaben prüfen</button>` : ""}</div>
        </div>
      </details>
    `;
  },

  _settingRow(label, enabled) {
    return `<div class="setting-row">
      <span>${escapeHtml(label)}</span>
      <span class="state-pill ${enabled ? "on" : "off"}">${enabled ? "Aktiv" : "Aus"}</span>
    </div>`;
  },

  _valueRow(label, value) {
    return `<div class="setting-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "—")}</strong>
    </div>`;
  },

  _renderDayRoute() {
    const days = this._data.days.days || [];
    const day = this._findDay(this._selectedDayId) || days[0];
    if (!day) {
      return `${this._renderReadOnlyNotice()}<div class="empty-state"><ha-icon icon="mdi:map-clock-outline"></ha-icon><h2>Noch keine Tagesroute</h2><p>Lege zuerst einen Reisetag an.</p>${this._canEdit() ? '<button class="primary-button" type="button" data-action="add-day">Reisetag anlegen</button>' : ""}</div>`;
    }
    const routeStops = this._effectiveDayStops(day);
    const roadbookStops = this._dayRoadbookStops(day);
    const points = this._dayRoutePoints(day);
    const routePaths = this._routingSegmentPaths(day);
    const canonicalModel = this._canonicalDayModel(day) || {};
    const missingLocationNodes = Array.isArray(canonicalModel.missing_location_nodes)
      ? canonicalModel.missing_location_nodes
      : routeStops.filter((stop) => !this._coordinate(stop, day)).map((stop, index) => ({
        id: stop?.id,
        name: cleanText(stop?.name) || `Stopp ${index + 1}`,
        marker_label: cleanText(stop?.marker_label) || String(this._displayStopSequence(stop, index + 1)),
        status: cleanText(stop?.location_status) || "missing",
      }));
    const locationAttentionNodes = Array.isArray(canonicalModel.location_attention_nodes)
      ? canonicalModel.location_attention_nodes
      : routeStops.filter((stop) => cleanText(stop?.location_status) && cleanText(stop?.location_status) !== "resolved");
    const coordinateCount = Number(canonicalModel.coordinate_count ?? points.length);
    const dayCover = this._dayCoverImage(day);
    const dayImages = [];
    const seenDayImages = new Set();
    const addDayImage = (image, context) => {
      const url = this._safeUrl(image?.image_url || image?.thumbnail_url);
      if (!url || seenDayImages.has(url)) return;
      seenDayImages.add(url);
      dayImages.push({ ...image, image_url: url, context });
    };
    addDayImage(dayCover, day.title);
    for (const stop of this._dayRoadbookStops(day)) {
      const own = this._experienceCoverForStop(stop.id);
      if (own) {
        addDayImage({ ...own, image_url: own.thumbnail_url, attribution: "Eigenes Reisefoto" }, stop.name);
        continue;
      }
      const gallery = this._destinationGalleryForStop(stop.id);
      addDayImage(this._destinationGalleryPrimary(gallery) || this._mediaFrom(stop), stop.name);
    }
    const drive = this._formatDriveMinutes(day.drive_minutes);
    const routeStatus = this._routeStatusLabel(day);
    const effectiveStart = this._effectiveDayStart(day);
    const navigationUrl = day?.navigation?.google_maps_directions_url;
    const omittedNavigationStops = Number(day?.navigation?.omitted_point_count || 0);
    const routingConfigured = Boolean(this._data?.settings?.routing_configured);
    const missingCount = Number(day?.routing?.missing_stop_count || 0);
    const gapCount = Number(day?.routing?.gap_count || 0);
    const ferryDistanceKm = Number(day?.routing?.ferry_distance_m || 0) / 1000;
    const routeWarnings = Array.isArray(day?.routing?.warnings) ? day.routing.warnings.filter(Boolean) : [];
    const archiveRecords = this._archiveRecordsForDay(day.id);
    const experienceDayMedia = this._experienceMediaForDay(day.id);
    const allExperienceDayMedia = this._experienceAllMediaForDay(day.id);
    const routingNotices = [];
    if (missingLocationNodes.length) {
      const names = missingLocationNodes.slice(0, 4).map((item) => cleanText(item?.name)).filter(Boolean);
      const suffix = missingLocationNodes.length > names.length ? ` und ${missingLocationNodes.length - names.length} weitere` : "";
      routingNotices.push(`Karte und Straßenroute sind unvollständig: GPS fehlt bei ${names.join(", ")}${suffix}.`);
    }
    const unverifiedLocationNodes = locationAttentionNodes.filter((item) => cleanText(item?.status) === "unverified");
    if (unverifiedLocationNodes.length) {
      const names = unverifiedLocationNodes.slice(0, 4).map((item) => cleanText(item?.name)).filter(Boolean);
      const suffix = unverifiedLocationNodes.length > names.length ? ` und ${unverifiedLocationNodes.length - names.length} weitere` : "";
      routingNotices.push(`GPS-Prüfung offen bei ${names.join(", ")}${suffix}. Die Punkte bleiben für die Route verwendbar.`);
    }
    if (day?.routing?.status === "stale") {
      routingNotices.push("Die gespeicherte Route ist nach einer Stoppänderung veraltet. Bitte neu berechnen.");
    }
    if (missingCount) {
      routingNotices.push(`Teilroute: ${missingCount} ${missingCount === 1 ? "Stopp besitzt" : "Stopps besitzen"} noch keine GPS-Koordinaten.`);
    }
    if (gapCount) {
      routingNotices.push(`${gapCount} ${gapCount === 1 ? "Routenabschnitt ist" : "Routenabschnitte sind"} bewusst unterbrochen. Für eine Fähre werden Abfahrts- und Ankunftsterminal als zwei GPS-Stopps benötigt.`);
    }
    for (const warning of routeWarnings.slice(0, 3)) {
      if (!routingNotices.includes(warning)) routingNotices.push(warning);
    }
    const routingNotice = routingNotices.map((text) => `<div class="notice warning">${escapeHtml(text)}</div>`).join("");
    return `
      ${this._renderReadOnlyNotice()}
      <section class="toolbar-card day-toolbar">
        <div>
          <span class="eyebrow">Tagesroute</span>
          <h2>${escapeHtml(day.title)}</h2>
          <p>${escapeHtml(this._formatDate(day.date))} · ${escapeHtml(effectiveStart)} → ${escapeHtml(this._effectiveDayEnd(day))}</p>
        </div>
        <label class="day-select"><span>Reisetag</span><select data-action="select-day">${days.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === day.id ? "selected" : ""}>${item.sequence}. ${escapeHtml(item.title)}</option>`).join("")}</select></label>
      </section>

      ${dayCover ? `<section class="day-cover-hero panel-card"><div class="day-cover-image">${this._renderDestinationImage(dayCover, { compact: false })}</div><div class="day-cover-copy"><span class="eyebrow">${dayCover.provider === "onedrive" ? "Unsere Erinnerung" : "Planungsvorschau"}</span><h2>${escapeHtml(day.title)}</h2><p>${dayCover.provider === "onedrive" ? "Roadplanner zeigt bevorzugt eure eigenen Fotos dieses Reisetags." : "Dieses Planungsbild vermittelt einen ersten visuellen Eindruck. Nach dem Besuch werden passende eigene OneDrive-Fotos bevorzugt."}</p></div></section>` : ""}

      <section class="route-layout">
        <div class="route-main">
          ${this._renderMap("day-route-map", points, day.title, routePaths, "", routeStops)}
          ${this._renderRouteFlow(day)}
        </div>
        <aside class="day-facts panel-card">
          <span class="eyebrow">Fahrdaten</span>
          <div class="facts-grid">
            <div><span>Autofahrt</span><strong>${day.distance_km != null ? `${escapeHtml(day.distance_km)} km` : "—"}</strong></div>
            <div><span>Fahrzeit</span><strong>${escapeHtml(drive || "—")}</strong></div>
            <div><span>Fähre</span><strong>${ferryDistanceKm > 0 ? `${escapeHtml(ferryDistanceKm.toFixed(1))} km` : "—"}</strong></div>
            <div><span>Stopps</span><strong>${routeStops.length}</strong></div>
            <div><span>Mit GPS</span><strong>${coordinateCount}</strong></div>
            <div><span>Routing</span><strong>${escapeHtml(routeStatus)}</strong></div>
          </div>
          ${routingNotice}
          ${omittedNavigationStops ? `<div class="notice neutral">Google Maps übernimmt auf Mobilgeräten nur die ersten drei Zwischenstopps. ${omittedNavigationStops} weitere ${omittedNavigationStops === 1 ? "Stopp wird" : "Stopps werden"} in diesem Link ausgelassen.</div>` : ""}
          ${!routingConfigured ? '<div class="notice neutral">Straßenrouting ist in den Roadplanner-Optionen noch nicht aktiviert.</div>' : ""}
          ${day.notes ? `<p class="notes-block">${escapeHtml(day.notes)}</p>` : ""}
          <div class="button-row">
            ${this._canEdit() && locationAttentionNodes.length ? `<button class="secondary-button" type="button" data-action="complete-day-locations" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:map-marker-question-outline"></ha-icon>Stopps anreichern (${locationAttentionNodes.length})</button>` : ""}
            ${this._canEdit() && routingConfigured ? `<button class="primary-button" type="button" data-action="calculate-day-route" data-day-id="${escapeHtml(day.id)}" data-force="${day.routing ? "true" : "false"}"><ha-icon icon="mdi:routes"></ha-icon>${day.routing ? "Neu berechnen" : "Route berechnen"}</button>` : ""}
            ${this._externalLink(navigationUrl, "Tagesroute in Google Maps", "mdi:google-maps")}
            ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="edit-day" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon> Tag bearbeiten</button><button class="secondary-button" type="button" data-action="add-stop" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon> Stopp</button>` : ""}
          </div>
        </aside>
      </section>

      ${this._renderDayArchivePanel(day, archiveRecords)}
      ${experienceDayMedia.length ? `<section class="panel-card day-experience-album"><div class="section-heading compact"><div><span class="eyebrow">Reiseerinnerungen</span><h2>Fotos dieses Tages</h2></div></div>${this._renderExperienceAlbum(experienceDayMedia, { dayId: day.id, title: day.title || "Tagesalbum", totalCount: allExperienceDayMedia.length })}</section>` : ""}

      <section class="panel-card image-section">
        <div class="section-heading compact">
          <div><span class="eyebrow">${allExperienceDayMedia.length ? "Unsere Fotos" : "Planungsbilder"}</span><h2>${allExperienceDayMedia.length ? "Highlights dieses Tages" : "Visuelle Vorschau"}</h2></div>
          ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="search-day-images" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:image-search-outline"></ha-icon> Titelbild suchen</button>` : ""}
        </div>
        ${dayImages.length ? this._renderImageGallery(dayImages) : `<div class="empty-inline"><ha-icon icon="mdi:image-outline"></ha-icon><div><strong>Noch keine Zielbilder</strong><span>Roadplanner sucht automatisch Planungsbilder. Nach dem Besuch werden passende eigene OneDrive-Fotos bevorzugt.</span></div></div>`}
      </section>

      <section class="stops-section">
        <div class="section-heading"><div><span class="eyebrow">Ablauf</span><h2>${routeStops.length} Stopps${missingLocationNodes.length ? ` · ${missingLocationNodes.length} ohne GPS` : ""}</h2></div>${this._canEdit() && roadbookStops.length > 1 ? `<button class="secondary-button" type="button" data-action="open-stop-order" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:sort-numeric-ascending"></ha-icon> Reihenfolge ändern</button>` : ""}</div>
        ${routeStops.length ? `<div class="stop-grid">${routeStops.map((stop, index) => this._renderStopCard(day, stop, index)).join("")}</div>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon><h2>Noch keine Stopps</h2><p>Füge Ziele, Fähren, Stellplätze oder Sehenswürdigkeiten hinzu.</p>${this._canEdit() ? `<button class="primary-button" type="button" data-action="add-stop" data-day-id="${escapeHtml(day.id)}">Ersten Stopp hinzufügen</button>` : ""}</div>`}
      </section>
    `;
  },

  _renderStopCard(day, stop, index) {
    const inherited = Boolean(stop._inherited);
    const media = this._mediaFrom(stop);
    const experienceMedia = this._experienceMediaForStop(stop.id);
    const allExperienceMedia = this._experienceAllMediaForStop(stop.id);
    const experienceCover = this._experienceCoverForStop(stop.id) || experienceMedia[0] || null;
    const destinationGallery = this._destinationGalleryForStop(stop.id);
    const destinationImages = this._destinationGalleryImages(destinationGallery);
    const location = stop.location || {};
    const coordinate = this._coordinate(stop);
    const time = [stop.arrival_time && `Ankunft ${stop.arrival_time}`, stop.departure_time && `Abfahrt ${stop.departure_time}`].filter(Boolean).join(" · ");
    const mapUrl = stop?.navigation?.google_maps_search_url;
    const navigationUrl = stop?.navigation?.google_maps_navigation_url;
    const externalActions = [
      this._externalLink(mapUrl, "Google Maps", "mdi:google-maps"),
      this._externalLink(navigationUrl, "Navigieren", "mdi:navigation-variant-outline", "primary-button"),
    ].filter(Boolean).join("");
    return `<article class="stop-card ${inherited ? "inherited-stop" : ""}">
      ${experienceCover ? `<button type="button" class="stop-experience-cover" data-action="media-open-album" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}" data-media-id="${escapeHtml(experienceCover.id)}"><img src="${escapeHtml(this._safeUrl(experienceCover.thumbnail_url))}" alt="${escapeHtml(experienceCover.caption || experienceCover.name || stop.name)}" loading="lazy"><span><ha-icon icon="mdi:image-multiple"></ha-icon>${allExperienceMedia.length} ${allExperienceMedia.length === 1 ? "Foto" : "Fotos"}</span></button>` : destinationImages.length ? this._renderDestinationGalleryPreview(destinationGallery, { dayId: day.id, stopId: stop.id, compact: true }) : media ? this._renderDestinationImage({ ...media, context: stop.name }, { compact: true }) : `<div class="stop-image-placeholder"><ha-icon icon="${stopIcons[stop.type] || stopIcons.waypoint}"></ha-icon><span>${escapeHtml(this._statusLabel(stop.type))}</span></div>`}
      <div class="stop-card-body">
        <div class="stop-card-heading"><span class="sequence-badge">${this._displayStopSequence(stop, index + 1)}</span><div><h3>${escapeHtml(stop.name)}</h3><span>${escapeHtml(this._statusLabel(stop.type))}${inherited ? " · Start vom Vortag" : ""}</span></div></div>
        ${inherited ? `<div class="inherited-badge"><ha-icon icon="mdi:link-variant"></ha-icon>Derselbe Übernachtungsstopp wie am Vortag</div>` : ""}
        <div class="stop-meta">
          ${time ? `<span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(time)}</span>` : ""}
          ${location.city ? `<span><ha-icon icon="mdi:map-marker-outline"></ha-icon>${escapeHtml(location.city)}${location.country_code ? `, ${escapeHtml(location.country_code)}` : ""}</span>` : ""}
          ${coordinate ? `<span><ha-icon icon="mdi:crosshairs-gps"></ha-icon>${coordinate.lat.toFixed(5)}, ${coordinate.lon.toFixed(5)}</span>` : ""}
        </div>
        ${!coordinate ? `<div class="location-status warning"><ha-icon icon="mdi:map-marker-question-outline"></ha-icon><div><strong>Ort fehlt</strong><span>${escapeHtml(stop.location_message || "Dieser Stopp benötigt noch einen bestätigten Kartenpunkt und ein Ortsprofil.")}</span></div></div>` : stop.location_status === "unverified" ? `<div class="location-status neutral"><ha-icon icon="mdi:map-marker-alert-outline"></ha-icon><div><strong>Ort noch prüfen</strong><span>${escapeHtml(stop.location_message || "Koordinaten sind vorhanden, aber der konkrete Ort ist noch nicht bestätigt.")}</span></div></div>` : !stop?.details?.place_profile?.confirmed_at ? `<div class="location-status neutral"><ha-icon icon="mdi:map-marker-check-outline"></ha-icon><div><strong>Ortsprofil vervollständigen</strong><span>GPS ist vorhanden. Bitte Name, Adresse, Kategorie, Quelle und verfügbare Kontaktdaten einmal bestätigen.</span></div></div>` : ""}
        ${stop?.navigation?.uses_access_point ? `<div class="location-status neutral"><ha-icon icon="mdi:car-arrow-right"></ha-icon><div><strong>Navigation bis zum erreichbaren Zugang</strong><span>Der Zielmarker bleibt am tatsächlichen Ort. Die Straßenroute endet ${Number(stop.navigation.access_point?.distance_m || 0) > 0 ? `etwa ${Math.round(Number(stop.navigation.access_point.distance_m))} m entfernt ` : ""}am nächstgelegenen befahrbaren Zugang.</span></div></div>` : ""}
        ${stop.notes ? `<p>${escapeHtml(stop.notes)}</p>` : ""}
        ${media?.attribution && !experienceCover && !destinationImages.length ? `<div class="attribution">${media.source_url ? `<a href="${escapeHtml(media.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(media.attribution)}</a>` : escapeHtml(media.attribution)}</div>` : ""}
        ${this._renderDestinationGalleryStatus(destinationGallery, day.id, stop.id, allExperienceMedia.length > 0)}
        ${this._renderExperienceAlbum(experienceMedia, { dayId: day.id, stopId: stop.id, compact: true, title: stop.name, totalCount: allExperienceMedia.length })}
        ${this._renderStopArchiveSummary(day, stop)}
        ${externalActions ? `<div class="button-row stop-actions">${externalActions}</div>` : ""}
        ${this._canEdit() && !inherited ? `<div class="button-row stop-actions"><button class="secondary-button" type="button" data-action="edit-stop" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon> Bearbeiten</button>${cleanText(stop?.location_status) !== "resolved" || !stop?.details?.place_profile?.confirmed_at ? `<button class="secondary-button" type="button" data-action="complete-stop-place" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:map-marker-check-outline"></ha-icon> Stopp anreichern</button>` : ""}<button class="secondary-button" type="button" data-action="search-stop-images" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:image-multiple-outline"></ha-icon> Bilder verwalten</button>${destinationImages.length ? `<button class="text-button danger-text" type="button" data-action="destination-gallery-delete" data-stop-id="${escapeHtml(stop.id)}">Galerie entfernen</button>` : media ? `<button class="text-button danger-text" type="button" data-action="remove-stop-image" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}">Bild entfernen</button>` : ""}<button class="text-button danger-text" type="button" data-action="delete-stop" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:trash-can-outline"></ha-icon> Stopp löschen</button></div>` : ""}
      </div>
    </article>`;
  },

  _renderTotalDay(day) {
    const orderedStops = this._dayRoadbookStops(day);
    const media = this._dayCoverImage(day);
    const drive = this._formatDriveMinutes(day.drive_minutes);
    const routeStatus = this._routeStatusLabel(day);
    return `<article class="total-day-card" data-action="select-day-card" data-day-id="${escapeHtml(day.id)}">
      <div class="total-day-sequence"><span>${day.sequence}</span></div>
      ${media ? `<div class="total-day-image">${this._renderDestinationImage({ ...media, context: day.title }, { compact: true })}</div>` : ""}
      <div class="total-day-copy"><span>${escapeHtml(this._formatDate(day.date))}</span><h3>${escapeHtml(day.title)}</h3><p>${escapeHtml(this._effectiveDayStart(day))} → ${escapeHtml(this._effectiveDayEnd(day))}</p><div>${[day.distance_km != null ? `${day.distance_km} km` : "", drive, `${day.stop_count || 0} Stopps`, routeStatus].filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div></div>
      <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon>
    </article>`;
  },

  _renderTrips() {
    const trips = this._data.trips?.trips || [];
    return `<section class="toolbar-card"><div><span class="eyebrow">Roadbook</span><h2>Alle Reisen</h2><p>Andere Reisen lassen sich ansehen, ohne die aktive Reise zu wechseln.</p></div></section>
      <section class="trip-grid">${trips.map((trip) => this._renderTripCard(trip)).join("")}</section>`;
  },

  _renderTripCard(trip) {
    if (!trip.valid) {
      return `<article class="trip-card invalid"><div class="trip-card-placeholder"><ha-icon icon="mdi:alert-circle-outline"></ha-icon></div><div class="trip-card-body"><span class="eyebrow">Ungültige Reise</span><h3>${escapeHtml(trip.id)}</h3><p>${escapeHtml(trip.error || "Die Reisedaten konnten nicht gelesen werden.")}</p></div></article>`;
    }
    const media = trip.cover_image;
    return `<article class="trip-card ${trip.active ? "active" : ""} ${trip.id === this._selectedTripId ? "selected" : ""}">
      ${media?.image_url ? this._renderDestinationImage({ ...media, context: trip.title }, { compact: true }) : `<div class="trip-card-placeholder"><ha-icon icon="mdi:map-outline"></ha-icon></div>`}
      <div class="trip-card-body"><div class="trip-title-row"><div><span class="eyebrow">${trip.active ? "Aktive Reise" : "Gespeicherte Reise"}</span><h3>${escapeHtml(trip.title)}</h3></div>${trip.active ? '<span class="status-badge success">Aktiv</span>' : ""}</div><p>${escapeHtml(trip.start_date || "offen")} – ${escapeHtml(trip.end_date || "offen")}</p><div class="trip-stats"><span>${trip.day_count} Tage</span><span>${trip.stop_count} Stopps</span><span>${trip.total_distance_km != null ? `${trip.total_distance_km} km` : "— km"}</span><span>Rev. ${trip.revision}</span></div><div class="button-row"><button class="secondary-button" type="button" data-action="view-trip" data-trip-id="${escapeHtml(trip.id)}"><ha-icon icon="mdi:eye-outline"></ha-icon> Ansehen</button>${!trip.active && this._canActivate() ? `<button class="primary-button" type="button" data-action="activate-trip" data-trip-id="${escapeHtml(trip.id)}"><ha-icon icon="mdi:check-circle-outline"></ha-icon> Aktivieren</button>` : ""}</div></div>
    </article>`;
  },

  _renderStopOrderDialog(dialog) {
    const day = this._findDay(dialog?.dayId);
    const stops = this._dayRoadbookStops(day);
    if (!day) {
      return `${this._renderModalHeader("Stopp-Reihenfolge")}<div class="empty-state compact-empty"><ha-icon icon="mdi:calendar-remove-outline"></ha-icon><h2>Reisetag nicht mehr vorhanden</h2><p>Bitte die Ansicht neu laden.</p></div>`;
    }
    const rows = stops.map((stop, index) => {
      const time = [
        stop?.arrival_time ? `Ankunft ${stop.arrival_time}` : "",
        stop?.departure_time ? `Abfahrt ${stop.departure_time}` : "",
      ].filter(Boolean).join(" · ");
      const options = stops.map((_item, optionIndex) => `<option value="${optionIndex + 1}" ${optionIndex === index ? "selected" : ""}>${optionIndex + 1}</option>`).join("");
      return `<li class="stop-order-row" data-stop-order-row="${escapeHtml(stop.id)}">
        <span class="sequence-badge">${index + 1}</span>
        <div class="stop-order-copy"><strong>${escapeHtml(stop.name || `Stopp ${index + 1}`)}</strong><span>${escapeHtml([this._statusLabel(stop.type), time].filter(Boolean).join(" · "))}</span></div>
        <div class="stop-order-controls">
          <label class="stop-order-position"><span>Position</span><select data-action="move-stop-position" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}">${options}</select></label>
          <div class="stop-order-buttons">
            <button class="icon-button" type="button" data-action="move-stop-up" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}" title="Früher einplanen" aria-label="${escapeHtml(stop.name || "Stopp")} früher einplanen" ${index === 0 ? "disabled" : ""}><ha-icon icon="mdi:arrow-up"></ha-icon></button>
            <button class="icon-button" type="button" data-action="move-stop-down" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}" title="Später einplanen" aria-label="${escapeHtml(stop.name || "Stopp")} später einplanen" ${index === stops.length - 1 ? "disabled" : ""}><ha-icon icon="mdi:arrow-down"></ha-icon></button>
          </div>
        </div>
      </li>`;
    }).join("");
    return `${this._renderModalHeader("Stopp-Reihenfolge", `${this._formatDate(day.date)} · ${day.title || "Reisetag"}`)}
      <div class="stop-order-body">
        <div class="notice info"><ha-icon icon="mdi:sort-numeric-ascending"></ha-icon><div><strong>Bestätigte Tagesfolge</strong><span>Verschiebe Stopps bewusst früher oder später. Uhrzeiten bleiben reine Planungsangaben und sortieren den Tag nicht automatisch. Ein geerbter Übernachtungsstart vom Vortag bleibt automatisch davor.</span></div></div>
        ${rows ? `<ol class="stop-order-list">${rows}</ol>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:map-marker-off-outline"></ha-icon><h2>Keine Stopps vorhanden</h2></div>`}
      </div>
      <div class="modal-actions"><button class="primary-button" type="button" data-action="close-dialog">Fertig</button></div>`;
  },

  _renderTripForm(dialog) {
    const trip = dialog.trip;
    return `${this._renderModalHeader("Reise bearbeiten", `Revision ${dialog.revision}`)}<form data-form="trip" data-revision="${dialog.revision}" class="form-grid">${this._field("title", "Titel", trip.title, "text", true, "full")}${this._selectField("status", "Status", trip.status, ["planned", "tentative", "confirmed", "completed", "cancelled"])}${this._field("start_date", "Startdatum", trip.start_date || "", "date")}${this._field("end_date", "Enddatum", trip.end_date || "", "date")}${this._textarea("notes", "Notizen", trip.notes || "", "full")}${this._renderTripCrewFields(trip)}${this._formActions("Reise speichern")}</form>`;
  },

  _renderDayForm(dialog) {
    const day = dialog.day || {};
    const media = this._mediaFrom(day) || {};
    const add = dialog.mode === "add";
    return `${this._renderModalHeader(add ? "Reisetag hinzufügen" : "Reisetag bearbeiten", add ? "Neuer Eintrag in der Route" : `Tag ${day.sequence}`)}<form data-form="day" data-mode="${dialog.mode}" data-day-id="${escapeHtml(day.id || "")}" data-revision="${dialog.revision}" class="form-grid">${this._field("title", "Titel", day.title || "", "text", true, "full")}${this._field("date", "Datum", day.date || "", "date")}${this._field("position", "Position", day.sequence || "", "number", false, "", "1")}${this._field("start", "Start", day.start || "", "text")}${this._field("end", "Ziel", day.end || "", "text")}${this._field("distance_km", "Entfernung (km)", day.distance_km ?? "", "number", false, "", "0", "0.1")}${this._field("drive_minutes", "Fahrzeit (Minuten)", day.drive_minutes ?? "", "number", false, "", "0")}${this._selectField("status", "Status", day.status || "planned", ["planned", "tentative", "confirmed", "completed", "cancelled"])}${this._textarea("notes", "Notizen", day.notes || "", "full")}<div class="form-section full"><h3>Bild</h3><p>Optionales Titelbild für den Reisetag.</p></div>${this._field("image_url", "Bild-URL", media.image_url || "", "text", false, "full")}${this._field("image_alt", "Alternativtext", media.alt || "", "text", false, "full")}${this._field("image_attribution", "Bildnachweis", media.attribution || "", "text", false, "full")}${this._field("image_source_url", "Quellseite", media.source_url || "", "text", false, "full")}${this._hiddenField("image_provider", media.provider || "manual")}${this._formActions(add ? "Reisetag hinzufügen" : "Änderungen speichern")}</form>`;
  },

  _renderStopForm(dialog) {
    const stop = dialog.stop || {};
    const location = stop.location || {};
    const media = this._mediaFrom(stop) || {};
    const transport = stop?.details?.transport && typeof stop.details.transport === "object" ? stop.details.transport : {};
    const add = dialog.mode === "add";
    return `${this._renderModalHeader(add ? "Stopp hinzufügen" : "Stopp bearbeiten", this._findDay(dialog.dayId)?.title || "Reisetag")}<form data-form="stop" data-mode="${dialog.mode}" data-day-id="${escapeHtml(dialog.dayId)}" data-stop-id="${escapeHtml(stop.id || "")}" data-revision="${dialog.revision}" class="form-grid">${this._field("name", "Name", stop.name || "", "text", true, "full")}${this._selectField("stop_type", "Typ", stop.type || "waypoint", ["waypoint", "start", "destination", "overnight", "campsite", "camping", "parking", "sightseeing", "attraction", "activity", "restaurant", "shopping", "ferry", "charging", "fuel", "service", "water", "waste", "laundry", "border", "break", "viewpoint", "fishing"])}${this._field("position", "Position", "", "number", false, "", "1")}${this._field("arrival_time", "Ankunft", stop.arrival_time || "", "time")}${this._field("departure_time", "Abfahrt", stop.departure_time || "", "time")}${this._archiveSelect("segment_mode_to_next", "Etappe zum nächsten Stopp", transport.mode_to_next || "auto", [{value:"auto",label:"Automatisch"},{value:"driving",label:"Straße / Auto"},{value:"ferry",label:"Fähre"},{value:"break",label:"Keine automatische Verbindung"}])}${this._archiveSelect("ferry_role", "Fährrolle", transport.ferry_role || "", [{value:"",label:"Keine / automatisch"},{value:"departure",label:"Abfahrtsterminal"},{value:"arrival",label:"Ankunftsterminal"}])}<div class="notice neutral full"><ha-icon icon="mdi:ferry"></ha-icon><div><strong>Fährstrecken</strong><span>Für eine korrekte Fährlinie sollten Abfahrts- und Ankunftsterminal als zwei Stopps mit GPS vorhanden sein. Die Etappe vom Abfahrtsterminal zum Ankunftsterminal wird als „Fähre“ markiert.</span></div></div>${this._field("address", "Adresse", location.address || "", "text", false, "full")}${this._field("city", "Ort", location.city || "", "text")}${this._field("country_code", "Land (ISO)", location.country_code || "", "text")}${this._field("latitude", "Breitengrad", location.latitude ?? location.lat ?? "", "number", false, "", "-90", "any")}${this._field("longitude", "Längengrad", location.longitude ?? location.lon ?? location.lng ?? "", "number", false, "", "-180", "any")}${this._textarea("notes", "Notizen", stop.notes || "", "full")}<div class="form-section full"><h3>Zielbild</h3><p>Du kannst eine Bild-URL hinterlegen oder nach dem Speichern über „Bild suchen“ Wikimedia Commons verwenden.</p></div>${this._field("image_url", "Bild-URL", media.image_url || "", "text", false, "full")}${this._field("image_alt", "Alternativtext", media.alt || "", "text", false, "full")}${this._field("image_attribution", "Bildnachweis", media.attribution || "", "text", false, "full")}${this._field("image_source_url", "Quellseite", media.source_url || "", "text", false, "full")}${this._hiddenField("image_provider", media.provider || "manual")}${this._formActions(add ? "Stopp hinzufügen" : "Änderungen speichern")}</form>`;
  },
};
