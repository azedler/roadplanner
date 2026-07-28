import { escapeHtml, cleanText } from "../lib/core-helpers.js";
import { stopIcons } from "../lib/constants.js";

export const routeMapMixin = {
  async _calculateDayRoute(dayId, force = false) {
    const result = await this._runAction("calculate_day_route", {
      day_id: dayId,
      expected_revision: this._currentRevision(),
      force: Boolean(force),
    }, "");
    if (!result) return;
    const calculated = Array.isArray(result.calculated) ? result.calculated : [];
    const skipped = Array.isArray(result.skipped) ? result.skipped : [];
    const failures = Array.isArray(result.failures) ? result.failures : [];
    if (calculated.length) {
      const route = calculated[0] || {};
      const distance = Number(route.distance_km);
      const minutes = Number(route.drive_minutes);
      const metric = [
        Number.isFinite(distance) ? `${distance.toFixed(1).replace(".0", "")} km` : "",
        Number.isFinite(minutes) ? this._formatDriveMinutes(minutes) : "",
      ].filter(Boolean).join(" · ");
      this._showToast(`Tagesroute berechnet${metric ? `: ${metric}` : ""}`, "success", 5000);
      return;
    }
    if (failures.length) {
      this._showToast(failures[0]?.error || "Die Tagesroute konnte nicht berechnet werden", "error", 7500);
      return;
    }
    const reason = skipped[0]?.reason || "Für diesen Tag ist noch keine berechenbare Route vorhanden.";
    this._showToast(reason, "error", 6500);
  },

  async _calculateTripRoutes(force = false) {
    const result = await this._runAction("calculate_trip_routes", {
      expected_revision: this._currentRevision(),
      force: Boolean(force),
    }, "");
    if (!result) return;
    const calculated = Array.isArray(result.calculated) ? result.calculated : [];
    const skipped = Array.isArray(result.skipped) ? result.skipped : [];
    const failures = Array.isArray(result.failures) ? result.failures : [];
    const parts = [];
    if (calculated.length) parts.push(`${calculated.length} ${calculated.length === 1 ? "Tag" : "Tage"} berechnet`);
    if (skipped.length) parts.push(`${skipped.length} übersprungen`);
    if (failures.length) parts.push(`${failures.length} fehlgeschlagen`);
    const message = parts.join(" · ") || "Es war keine neue Routenberechnung erforderlich.";
    this._showToast(message, failures.length ? "error" : "success", failures.length ? 7500 : 5000);
  },

  async _exportTripPdf() {
    if (this._exportingTripPdf || !this._selectedTripId) return;
    this._exportingTripPdf = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction("export_trip_pdf", {
        trip_id: this._selectedTripId,
      }, "", { refresh: false, errorTitle: "PDF konnte nicht erstellt werden" });
      if (!result?.download_url) return;
      const link = document.createElement("a");
      link.href = result.download_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.download = "";
      link.click();
      this._showToast("Reisezusammenfassung als PDF erstellt", "success", 4500);
    } finally {
      this._exportingTripPdf = false;
      this._render({ preserveScroll: true });
    }
  },

  async _exportTripVideo() {
    if (this._exportingTripVideo || !this._selectedTripId) return;
    this._exportingTripVideo = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction("export_trip_video", {
        trip_id: this._selectedTripId,
        style: this._videoStyle || "highlight",
      }, "", {
        refresh: false,
        errorTitle: "Video konnte nicht erstellt werden",
      });
      if (!result?.download_url) return;
      const link = document.createElement("a");
      link.href = result.download_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.download = "";
      link.click();
      this._showToast("Reise als Video erstellt", "success", 4500);
    } finally {
      this._exportingTripVideo = false;
      this._render({ preserveScroll: true });
    }
  },

  _coordinate(stop, day = null, index = 0) {
    const location = stop?.location || {};
    const latitude = Number(location.latitude ?? location.lat);
    const longitude = Number(location.longitude ?? location.lon ?? location.lng);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
    if (latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) return null;
    const time = cleanText(stop?.arrival_time || stop?.departure_time) || "12:00";
    const normalizedTime = /^\d{2}:\d{2}(:\d{2})?$/.test(time)
      ? time
      : "12:00";
    const timestampText = normalizedTime.length === 5
      ? `${normalizedTime}:00`
      : normalizedTime;
    const candidate = day?.date
      ? new Date(`${day.date}T${timestampText}`)
      : null;
    const timestamp = candidate && !Number.isNaN(candidate.getTime())
      ? candidate
      : new Date(Date.now() + index * 60000);
    return {
      lat: latitude,
      lon: longitude,
      label: cleanText(stop?.name) || `Stopp ${index + 1}`,
      stopId: stop?.id,
      stopType: cleanText(stop?.type) || "waypoint",
      sequence: this._displayStopSequence(stop, index + 1),
      markerLabel: cleanText(stop?.marker_label) || String(this._displayStopSequence(stop, index + 1)),
      inherited: Boolean(stop?._inherited),
      timestamp,
    };
  },

  _dayRoutePoints(day) {
    return this._effectiveDayStops(day)
      .map((stop, index) => this._coordinate(stop, day, index))
      .filter(Boolean);
  },

  _allRouteNodes() {
    const nodes = [];
    let sequence = 0;
    for (const day of this._data?.days?.days || []) {
      for (const stop of this._dayRoadbookStops(day)) {
        sequence += 1;
        nodes.push({
          ...stop,
          display_sequence: sequence,
          route_sequence: sequence,
          marker_label: String(sequence),
          _trip_day_id: day.id,
          _trip_day_title: day.title,
          _trip_day_date: day.date,
        });
      }
    }
    return nodes;
  },

  _allRoutePoints(routeNodes = null) {
    const points = [];
    const nodes = Array.isArray(routeNodes) ? routeNodes : this._allRouteNodes();
    for (const [index, stop] of nodes.entries()) {
      const day = {
        id: stop?._trip_day_id,
        title: stop?._trip_day_title,
        date: stop?._trip_day_date,
      };
      const point = this._coordinate(stop, day, index);
      if (point) {
        points.push({
          ...point,
          markerLabel: cleanText(stop?.marker_label) || String(index + 1),
          sequence: Number(stop?.display_sequence) || index + 1,
          dayId: day.id,
          dayTitle: day.title,
          date: day.date,
        });
      }
    }
    return points;
  },

  _geometryCoordinatesToPoints(coordinates, day, label, offset = 0) {
    if (!Array.isArray(coordinates)) return [];
    const base = day?.date ? new Date(`${day.date}T12:00:00`) : new Date();
    return coordinates.map((coordinate, index) => {
      if (!Array.isArray(coordinate) || coordinate.length < 2) return null;
      const lon = Number(coordinate[0]);
      const lat = Number(coordinate[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
      return {
        lat,
        lon,
        label: label || day?.title || "Route",
        timestamp: new Date(base.getTime() + (offset + index) * 1000),
      };
    }).filter(Boolean);
  },

  _routeGeometryPoints(day) {
    const routing = day?.routing;
    if (!routing || !["calculated", "partial"].includes(routing.status)) return [];
    if (routing.geometry_stale) return [];
    return this._geometryCoordinatesToPoints(routing?.geometry?.coordinates, day, day?.title || "Straßenroute");
  },

  _routingSegmentPaths(day) {
    const routing = day?.routing;
    if (!routing || !["calculated", "partial"].includes(routing.status) || routing.geometry_stale) return [];
    if (Array.isArray(routing.segments)) {
      let offset = 0;
      const paths = [];
      for (const segment of routing.segments) {
        if (!segment || !segment.geometry || segment.mode === "break") continue;
        const mode = cleanText(segment.mode) || "driving";
        const points = this._geometryCoordinatesToPoints(
          segment.geometry.coordinates,
          day,
          mode === "ferry" ? "Fährstrecke" : day?.title || "Straßenroute",
          offset,
        );
        offset += points.length + 1;
        if (points.length > 1) paths.push({
          title: mode === "ferry" ? `${day?.title || "Etappe"} · Fähre` : day?.title || "Straßenroute",
          points,
          mode,
        });
      }
      if (paths.length) return paths;
    }
    const points = this._routeGeometryPoints(day);
    return points.length > 1 ? [{ title: day?.title || "Straßenroute", points, mode: "driving" }] : [];
  },

  _tripRoutePaths(days) {
    return (days || []).flatMap((day) => this._routingSegmentPaths(day));
  },

  _effectiveDayStart(day) {
    const model = this._canonicalDayModel(day);
    if (cleanText(model?.start_label)) return cleanText(model.start_label);
    const stops = this._effectiveDayStops(day);
    return stops[0]?.name || day?.start || "?";
  },

  _effectiveDayEnd(day) {
    const model = this._canonicalDayModel(day);
    if (cleanText(model?.end_label)) return cleanText(model.end_label);
    const stops = this._effectiveDayStops(day);
    return stops.at(-1)?.name || day?.end || "?";
  },

  _routeStatusLabel(day) {
    const status = cleanText(day?.routing?.status);
    const labels = {
      calculated: "Straßenroute berechnet",
      partial: "Teilroute berechnet",
      stale: "Route veraltet",
      manual_override: "Fahrdaten manuell",
    };
    return labels[status] || "Noch nicht berechnet";
  },

  _routeCoverageText(metrics = this._data?.summary?.route_metrics) {
    if (!metrics) return "Noch keine Fahrdaten";
    const candidate = Number(metrics.route_candidate_day_count || 0);
    const calculated = Number(metrics.calculated_day_count || 0)
      + Number(metrics.partial_day_count || 0)
      + Number(metrics.manual_day_count || 0);
    if (!candidate) return "Keine berechenbaren Tagesetappen";
    if (metrics.status === "complete") return `${calculated}/${candidate} Etappen berechnet`;
    return `${calculated}/${candidate} Etappen mit Fahrdaten`;
  },

  _externalLink(url, label, icon = "mdi:google-maps", className = "secondary-button") {
    const safe = this._safeUrl(url);
    if (!safe) return "";
    return `<a class="${className}" href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="${icon}"></ha-icon>${escapeHtml(label)}</a>`;
  },

  _googleMapsQueryUrl(value) {
    const query = cleanText(value);
    if (!query) return "";
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
  },

  _renderMap(id, points, title, paths = [], caption = "", routeNodes = []) {
    const validPoints = points.filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon));
    const validPaths = (paths || []).map((path) => ({
      title: cleanText(path?.title) || title,
      mode: cleanText(path?.mode) || "driving",
      points: (path?.points || []).filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon)),
    })).filter((path) => path.points.length > 1);
    const routeNodeValues = Array.isArray(routeNodes) ? routeNodes : [];
    const legendTotal = routeNodeValues.length || validPoints.length;
    const buildLegendItems = () => routeNodeValues.length
      ? routeNodeValues.slice(0, 30).map((stop, index) => {
        const point = this._coordinate(stop, null, index);
        return {
          markerLabel: cleanText(stop?.marker_label) || String(this._displayStopSequence(stop, index + 1)),
          label: cleanText(stop?.name) || `Stopp ${index + 1}`,
          inherited: Boolean(stop?._inherited),
          missing: !point,
          status: cleanText(stop?.location_status) || (!point ? "missing" : "resolved"),
        };
      })
      : validPoints.slice(0, 30).map((point, index) => ({
        markerLabel: cleanText(point.markerLabel) || String(Number.isInteger(Number(point.sequence)) && Number(point.sequence) > 0 ? Number(point.sequence) : index + 1),
        label: point.label,
        inherited: point.inherited,
        missing: false,
        status: "resolved",
      }));
    const legendSource = buildLegendItems();
    const legend = legendSource.map((item) => `
      <span class="map-key-item ${item.inherited ? "inherited" : ""} ${item.missing ? "missing" : ""}"><b>${escapeHtml(item.markerLabel)}</b><span>${escapeHtml(item.label || `Punkt ${item.markerLabel}`)}${item.missing ? " · GPS fehlt" : ""}</span></span>
    `).join("");
    if (!validPoints.length && !validPaths.length) {
      return `<section class="map-card map-unavailable"><div class="map-placeholder"><ha-icon icon="mdi:map-marker-off-outline"></ha-icon><strong>Noch keine Koordinaten</strong><span>Die schematische Route und die bestätigte Reihenfolge bleiben verfügbar. Ergänze GPS-Daten für die Kartenansicht.</span></div>${legend ? `<div class="map-key">${legend}${legendTotal > 30 ? `<span class="map-key-more">+${legendTotal - 30} weitere</span>` : ""}</div>` : ""}</section>`;
    }
    this._mapModels.set(id, { points: validPoints, paths: validPaths, title });
    /* legendSource is based on all canonical route nodes, not only mappable points. */
    const hasFerry = validPaths.some((path) => path.mode === "ferry");
    const defaultCaption = validPaths.length
      ? (hasFerry
        ? "Durchgezogene Linien zeigen Straßenetappen, gestrichelte Linien Fährstrecken. Nur nummerierte Marker sind echte Roadplanner-Stopps."
        : "Die Linie folgt der berechneten Straßenroute. Nur nummerierte Marker sind echte Roadplanner-Stopps; technische Geometriepunkte werden nicht dargestellt.")
      : "Die Linie verbindet die gespeicherten Koordinaten in Planungsreihenfolge; nur nummerierte Marker sind echte Stopps.";
    return `<section class="map-card" data-map-shell="${escapeHtml(id)}">
      <div class="map-stage">
        <ha-map data-map-id="${escapeHtml(id)}" auto-fit theme-mode="auto"></ha-map>
        <div class="map-overlay"><div class="spinner small"></div><span>Karte wird geladen</span></div>
      </div>
      ${legend ? `<div class="map-key">${legend}${legendTotal > 30 ? `<span class="map-key-more">+${legendTotal - 30} weitere</span>` : ""}</div>` : ""}
      <div class="map-caption"><ha-icon icon="mdi:information-outline"></ha-icon><span>${escapeHtml(caption || defaultCaption)}</span></div>
    </section>`;
  },

  _renderRouteFlow(day) {
    const model = this._canonicalDayModel(day);
    const routeNodes = this._effectiveDayStops(day);
    const legacyNodes = !routeNodes.length && Array.isArray(model?.legacy_route_nodes)
      ? model.legacy_route_nodes
      : [];
    const source = routeNodes.length ? routeNodes : legacyNodes;
    if (!source.length) return "";
    const nodes = source.map((stop, index) => ({
      label: cleanText(stop?.name) || `Stopp ${index + 1}`,
      type: cleanText(stop?.type) || "waypoint",
      icon: stopIcons[stop?.type] || stopIcons.waypoint,
      markerLabel: stop?._inherited ? "S" : (stop?._legacy_context ? "" : String(this._displayStopSequence(stop, index + 1))),
      inherited: Boolean(stop?._inherited),
      legacy: Boolean(stop?._legacy_context),
      time: stop?._inherited
        ? `Start vom Vortag${stop?.departure_time ? ` · Abfahrt ${stop.departure_time}` : ""}`
        : (stop?.arrival_time || stop?.departure_time || this._statusLabel(stop?.type)),
    }));
    return `<section class="route-flow-card"><span class="eyebrow">Schematischer Tagesablauf</span><div class="route-flow">${nodes.map((node, index) => `<div class="flow-item ${node.inherited ? "inherited" : ""} ${node.legacy ? "legacy" : ""}"><div class="flow-node">${node.markerLabel ? `<span>${escapeHtml(node.markerLabel)}</span>` : `<ha-icon icon="${node.icon}"></ha-icon>`}</div><div class="flow-copy"><strong>${escapeHtml(node.label)}</strong><span>${escapeHtml(node.time || this._statusLabel(node.type))}</span></div>${index < nodes.length - 1 ? '<div class="flow-line"></div>' : ""}</div>`).join("")}</div></section>`;
  },

  _renderTotalRoute() {
    const days = this._data.days.days || [];
    const routeNodes = this._allRouteNodes();
    const points = this._allRoutePoints(routeNodes);
    const paths = this._tripRoutePaths(days);
    const images = this._tripImages(12);
    const metrics = this._data?.summary?.route_metrics || {};
    const distance = this._data?.summary?.total_distance_km;
    const ferryDistance = metrics.total_ferry_distance_km;
    const movementDistance = metrics.total_movement_km;
    const drive = this._formatDriveMinutes(this._data?.summary?.total_drive_minutes);
    const coverage = this._routeCoverageText(metrics);
    const routingConfigured = Boolean(this._data?.settings?.routing_configured);
    return `
      ${this._renderReadOnlyNotice()}
      <section class="toolbar-card">
        <div><span class="eyebrow">Gesamtroute</span><h2>${escapeHtml(this._data.summary.trip.title)}</h2><p>${days.length} Tage · ${this._data.summary.stop_count} Stopps · ${distance != null ? `${escapeHtml(distance)} km Auto` : "Autostrecke noch offen"}${ferryDistance != null ? ` · ${escapeHtml(ferryDistance)} km Fähre` : ""}${movementDistance != null ? ` · ${escapeHtml(movementDistance)} km Bewegung gesamt` : ""}${drive ? ` · ${escapeHtml(drive)} Fahrzeit` : ""}</p><p>${escapeHtml(coverage)}</p></div>
        <div class="toolbar-actions">
          ${this._canEdit() && routingConfigured ? `<button class="primary-button" type="button" data-action="calculate-trip-routes" data-force="${paths.length ? "true" : "false"}"><ha-icon icon="mdi:routes"></ha-icon>${paths.length ? "Alle neu berechnen" : "Alle Routen berechnen"}</button>` : ""}
          ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="add-day"><ha-icon icon="mdi:calendar-plus"></ha-icon> Tag</button>` : ""}
          <button class="secondary-button" type="button" data-action="export-trip-pdf"${this._exportingTripPdf ? " disabled" : ""}><ha-icon icon="mdi:file-pdf-box"></ha-icon> ${this._exportingTripPdf ? "Erstelle PDF…" : "Reisezusammenfassung als PDF"}</button>
          ${this._data?.settings?.video_export_available ? `
            <select data-action="select-video-style" aria-label="Videolänge" ${this._exportingTripVideo ? "disabled" : ""}>
              <option value="highlight" ${(this._videoStyle || "highlight") === "highlight" ? "selected" : ""}>Kurzer Highlight-Reel</option>
              <option value="full" ${this._videoStyle === "full" ? "selected" : ""}>Ausführlicher Rückblick</option>
            </select>
            <button class="secondary-button" type="button" data-action="export-trip-video"${this._exportingTripVideo ? " disabled" : ""}><ha-icon icon="mdi:movie-open-outline"></ha-icon> ${this._exportingTripVideo ? "Erstelle Video… (kann einige Minuten dauern)" : "Reise als Video"}</button>
          ` : `
            <button class="secondary-button" type="button" disabled title="ffmpeg wurde auf diesem Home-Assistant-Host nicht gefunden"><ha-icon icon="mdi:movie-open-outline"></ha-icon> Reise als Video</button>
          `}
        </div>
      </section>
      ${!routingConfigured ? '<div class="notice neutral">Aktiviere Straßenrouting in den Roadplanner-Optionen, um Kilometer und Fahrzeiten zu berechnen.</div>' : ""}
      ${metrics.stale_day_count ? `<div class="notice warning">${metrics.stale_day_count} gespeicherte ${metrics.stale_day_count === 1 ? "Route ist" : "Routen sind"} nach Änderungen veraltet.</div>` : ""}
      ${metrics.routing_gap_count ? `<div class="notice warning">${metrics.routing_gap_count} ${metrics.routing_gap_count === 1 ? "Routenabschnitt ist" : "Routenabschnitte sind"} noch unvollständig modelliert. Eine Fähre benötigt Abfahrts- und Ankunftsterminal als getrennte Stopps.</div>` : ""}
      ${this._renderMap("total-route-map", points, this._data.summary.trip.title, paths, "", routeNodes)}
      ${this._renderTripRouteGraphic(days)}
      ${images.length ? `<section class="panel-card image-section"><div class="section-heading compact"><div><span class="eyebrow">Reiseeindrücke</span><h2>Geplante Ziele</h2></div></div>${this._renderImageGallery(images)}</section>` : ""}
      ${days.length ? `<section class="total-route-list"><div class="section-heading"><div><span class="eyebrow">Etappen</span><h2>Reiseverlauf</h2></div></div>${days.map((day) => this._renderTotalDay(day)).join("")}</section>` : `<div class="empty-state"><ha-icon icon="mdi:map-marker-path"></ha-icon><h2>Die Gesamtroute ist noch leer</h2></div>`}
      ${this._data.days.has_more ? `<div class="notice warning">Im Panel werden maximal 60 Reisetage angezeigt. Weitere Tage bleiben im Roadbook erhalten.</div>` : ""}
    `;
  },

  _renderTripRouteGraphic(days) {
    if (!days.length) return "";
    return `<section class="panel-card trip-route-graphic">
      <div class="section-heading compact">
        <div><span class="eyebrow">Reiseband</span><h2>Alle Etappen auf einen Blick</h2></div>
        <ha-icon icon="mdi:route"></ha-icon>
      </div>
      <div class="journey-track" role="list">
        ${days.map((day, index) => {
          const start = this._effectiveDayStart(day);
          const end = this._effectiveDayEnd(day);
          return `
          <button type="button" class="journey-node" role="listitem" data-action="select-day-card" data-day-id="${escapeHtml(day.id)}">
            <span class="journey-dot">${day.sequence}</span>
            <span class="journey-copy">
              <small>${escapeHtml(this._formatDate(day.date))}</small>
              <strong>${escapeHtml(day.title)}</strong>
              <span>${escapeHtml(start)} → ${escapeHtml(end)}</span>
            </span>
          </button>
          ${index < days.length - 1 ? '<span class="journey-line" aria-hidden="true"></span>' : ""}
        `;
        }).join("")}
      </div>
    </section>`;
  },

  async _ensureHaMap() {
    if (customElements.get("ha-map")) return true;
    if (this._mapHelpersPromise) return this._mapHelpersPromise;
    this._mapHelpersPromise = (async () => {
      try {
        if (typeof window.loadCardHelpers !== "function") return false;
        const helpers = await window.loadCardHelpers();
        if (!helpers?.createCardElement) return false;
        const loader = helpers.createCardElement({
          type: "map",
          entities: ["zone.home"],
        });
        loader.hass = this._hass;
        loader.setAttribute("aria-hidden", "true");
        loader.style.display = "none";
        this.shadowRoot.append(loader);
        await Promise.race([
          customElements.whenDefined("ha-map"),
          new Promise((resolve) => window.setTimeout(resolve, 4000)),
        ]);
        loader.remove();
      } catch (error) {
        console.warn("Roadplanner map component could not be loaded", error);
      }
      return Boolean(customElements.get("ha-map"));
    })();
    return this._mapHelpersPromise;
  },

  _displayPathPoints(points, maximum = 14) {
    const source = Array.isArray(points) ? points : [];
    if (source.length <= maximum) return source;
    const result = [source[0]];
    const interior = maximum - 2;
    for (let index = 1; index <= interior; index += 1) {
      const sourceIndex = Math.round((index * (source.length - 1)) / (interior + 1));
      result.push(source[sourceIndex]);
    }
    result.push(source[source.length - 1]);
    return result;
  },

  _mapColors() {
    const styles = getComputedStyle(this);
    const primary = cleanText(styles.getPropertyValue("--primary-color")) || "#039be5";
    const ferry = cleanText(styles.getPropertyValue("--accent-color")) || "#7e57c2";
    const muted = cleanText(styles.getPropertyValue("--secondary-text-color")) || "#78909c";
    return { primary, ferry, muted };
  },

  _buildLeafletLayers(map, model) {
    const Leaflet = map?.Leaflet;
    if (!Leaflet?.polyline || !Leaflet?.marker || !Leaflet?.divIcon) return null;
    const colors = this._mapColors();
    const layers = [];
    const pathModels = model.paths?.length
      ? model.paths
      : (model.points?.length > 1 ? [{ title: model.title, mode: "plan", points: model.points }] : []);
    for (const path of pathModels) {
      const latLngs = (path.points || []).map((point) => [point.lat, point.lon]);
      if (latLngs.length < 2) continue;
      const ferry = path.mode === "ferry";
      const plan = path.mode === "plan";
      layers.push(Leaflet.polyline(latLngs, {
        color: ferry ? colors.ferry : (plan ? colors.muted : colors.primary),
        weight: ferry ? 4 : 5,
        opacity: plan ? 0.65 : 0.88,
        dashArray: ferry ? "12 10" : (plan ? "5 8" : undefined),
        lineCap: "round",
        lineJoin: "round",
        interactive: false,
      }));
    }
    for (const [index, point] of (model.points || []).entries()) {
      const numericSequence = Number.isInteger(Number(point.sequence)) && Number(point.sequence) > 0 ? Number(point.sequence) : index + 1;
      const markerLabel = cleanText(point.markerLabel) || String(numericSequence);
      const ferryStop = point.stopType === "ferry";
      const markerColor = point.inherited ? colors.muted : (ferryStop ? colors.ferry : colors.primary);
      const icon = Leaflet.divIcon({
        className: `roadplanner-map-marker ${point.inherited ? "inherited" : ""}`,
        html: `<span style="display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:${escapeHtml(markerColor)};color:#fff;border:2px solid #fff;box-shadow:0 2px 7px rgba(0,0,0,.35);font:700 12px system-ui,sans-serif">${escapeHtml(markerLabel)}</span>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
        tooltipAnchor: [0, -14],
      });
      const marker = Leaflet.marker([point.lat, point.lon], { icon, interactive: true });
      const prefix = point.inherited ? "Start" : markerLabel;
      marker.bindTooltip(`${prefix}. ${point.label || `Stopp ${markerLabel}`}`, { direction: "top" });
      layers.push(marker);
    }
    return layers;
  },

  async _hydrateMaps() {
    const token = ++this._mapHydrationToken;
    if (!this._mapModels.size) return;
    const available = await this._ensureHaMap();
    if (token !== this._mapHydrationToken || !this.isConnected) return;
    for (const [id, model] of this._mapModels.entries()) {
      const shell = this.shadowRoot.querySelector(`[data-map-shell="${CSS.escape(id)}"]`);
      const map = shell?.querySelector("ha-map");
      if (!shell || !map || !available) {
        shell?.classList.add("map-failed");
        continue;
      }
      const base = Date.now();
      // The temporary ha-map fallback contains only canonical Roadplanner
      // stops. Routing geometry is never passed through paths because ha-map
      // renders every path coordinate as a marker. The preferred Leaflet layer
      // renderer below draws the full geometry as marker-free polylines.
      map.paths = model.points?.length ? [{
        name: model.title,
        fullDatetime: false,
        points: model.points.map((point, index) => ({
          point: [point.lat, point.lon],
          timestamp: point.timestamp instanceof Date
            ? point.timestamp
            : new Date(point.timestamp || base + index * 1000),
        })),
      }] : [];
      map.autoFit = true;
      map.clusterMarkers = false;
      map.renderPassive = false;
      map.themeMode = "auto";
      try {
        await map.updateComplete;
        for (let attempt = 0; attempt < 20 && (!map.Leaflet || !map.leafletMap); attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 75));
        }
        const layers = this._buildLeafletLayers(map, model);
        if (layers) {
          map.paths = [];
          map.layers = layers;
          map.requestUpdate?.();
        }
      } catch (error) {
        console.warn("Roadplanner marker-free route rendering unavailable; using simplified fallback", error);
      }
      shell.classList.add("map-ready");
      window.setTimeout(() => {
        try {
          const boundsPoints = [
            ...model.points,
            ...(model.paths || []).flatMap((path) => path.points || []),
          ];
          map.fitBounds(boundsPoints.map((point) => [point.lat, point.lon]), {
            pad: 0.25,
            zoom: boundsPoints.length === 1 ? 14 : 13,
          });
        } catch (_error) {
          // ha-map also performs its own auto fit.
        }
      }, 450);
    }
  },
};
