import { escapeHtml, cleanText } from "../lib/core-helpers.js";

export const decisionsIntegrityMixin = {
  async _createDecisionFromMessage(messageId) {
    const id = cleanText(messageId);
    if (!id || this._decisionCreateInFlightMessageId) return null;
    this._decisionCreateInFlightMessageId = id;
    this._render({ preserveScroll: true });
    const retry = () => this._createDecisionFromMessage(id);
    try {
      const result = await this._runAction("decision_create_from_message", {
        trip_id: this._selectedTripId,
        message_id: id,
      }, "Entscheidungsvorlage erstellt", {
        refresh: false,
        errorMode: "dialog",
        errorTitle: "Entscheidungsvorlage konnte nicht erstellt werden",
        retry,
      });
      if (!result) return null;
      if (result.experience && this._data) {
        this._data = { ...this._data, experience: result.experience };
        this._signature = "";
      }
      this._activeTab = "decisions";
      this._render({ preserveScroll: false });
      return result;
    } finally {
      this._decisionCreateInFlightMessageId = "";
      if (this._activeTab === "assistant") this._render({ preserveScroll: true });
    }
  },

  _integrityData() {
    const value = this._data?.integrity;
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : { score: 0, status: "incomplete", dimensions: {}, stats: {}, days: [], issues: [] };
  },

  _renderDecisions() {
    const experience = this._experienceData();
    const decisions = (experience.decisions || []).filter((item) => item.status !== "archived");
    return `
      ${this._renderReadOnlyNotice()}
      <section class="panel-card decision-intro">
        <div><span class="eyebrow">Gemeinsam entscheiden</span><h2>Entscheidungs-Slides</h2><p>Vergleiche den aktuellen Plan mit bis zu drei Alternativen, wische gemeinsam durch die Optionen und übernimm nur eine echte Planänderung in den Änderungskorb.</p></div>
      </section>
      ${decisions.length ? `<div class="decision-list">${decisions.map((decision) => this._renderDecisionCard(decision)).join("")}</div>` : `<div class="empty-state"><ha-icon icon="mdi:cards-playing-outline"></ha-icon><h2>Noch keine Entscheidungsvorlage</h2><p>Öffne eine Assistentenantwort mit mehreren konkreten Optionen und tippe dort auf „Als Entscheidungsvorlage“.</p></div>`}
    `;
  },

  _renderDecisionOptionGallery(decision, option) {
    const images = Array.isArray(option?.images) && option.images.length
      ? option.images.slice(0, 3)
      : (option?.image?.image_url ? [option.image] : []);
    if (!images.length) {
      return `<div class="decision-image empty"><ha-icon icon="mdi:image-area"></ha-icon><span>Kein sicher zugeordnetes Bild gefunden</span></div>`;
    }
    const primary = images[0];
    return `<div class="decision-option-gallery">
      <button type="button" class="decision-image" data-action="decision-gallery-open" data-decision-id="${escapeHtml(decision.id)}" data-option-id="${escapeHtml(option.id)}" data-image-index="0"><img src="${escapeHtml(this._safeUrl(primary.thumbnail_url || primary.image_url))}" alt="${escapeHtml(primary.alt || option.title)}" loading="lazy">${primary.attribution ? `<small>${escapeHtml(primary.attribution)}</small>` : ""}</button>
      ${images.length > 1 ? `<div class="decision-image-thumbs">${images.slice(1).map((image, index) => `<button type="button" data-action="decision-gallery-open" data-decision-id="${escapeHtml(decision.id)}" data-option-id="${escapeHtml(option.id)}" data-image-index="${index + 1}"><img src="${escapeHtml(this._safeUrl(image.thumbnail_url || image.image_url))}" alt="${escapeHtml(image.alt || option.title)}" loading="lazy"></button>`).join("")}</div>` : ""}
    </div>`;
  },

  _renderDecisionCard(decision) {
    const options = Array.isArray(decision.options) ? decision.options : [];
    if (!options.length) return "";
    let index = Number(this._decisionSlideIndexes.get(decision.id));
    if (!Number.isInteger(index) || index < 0 || index >= options.length) {
      const selected = options.findIndex((item) => item.id === decision.selected_option_id);
      index = selected >= 0 ? selected : 0;
    }
    const option = options[index];
    const location = option?.location || {};
    const lat = Number(location.latitude ?? location.lat);
    const lon = Number(location.longitude ?? location.lon ?? location.lng);
    const hasCoordinate = Number.isFinite(lat) && Number.isFinite(lon);
    const mapsQuery = hasCoordinate ? `${lat},${lon}` : option.place_query || option.title;
    const route = option.route_metrics || {};
    const selected = decision.selected_option_id === option.id;
    const transferred = decision.status === "transferred";
    const currentPlan = Boolean(option.is_current_plan || option.change_type === "keep_existing");
    const cost = option.estimated_cost || {};
    const costText = Number.isFinite(Number(cost.amount))
      ? `${Number(cost.amount).toLocaleString("de-DE", { maximumFractionDigits: 2 })} ${escapeHtml(cost.currency || "EUR")}`
      : cleanText(cost.note);
    return `<article class="decision-card panel-card" data-decision-card="${escapeHtml(decision.id)}">
      <header class="decision-heading"><div><span class="eyebrow">${escapeHtml(this._archiveDayLabel(decision.linked_day_id))}</span><h2>${escapeHtml(decision.title || "Entscheidung")}</h2><p>${escapeHtml(decision.question || "Welche Option passt am besten?")}</p></div><span class="decision-counter">${index + 1} / ${options.length}</span></header>
      <div class="decision-slide">
        ${this._renderDecisionOptionGallery(decision, option)}
        <div class="decision-copy">
          <div class="decision-title-row"><div><span class="eyebrow">${currentPlan ? "Aktuell geplant" : `Option ${index + 1}`}</span><h3>${escapeHtml(option.title)}</h3></div><div class="decision-title-badges">${currentPlan ? `<span class="status-badge neutral">Bestehender Plan</span>` : ""}${selected ? `<span class="status-badge status-success">Ausgewählt</span>` : ""}</div></div>
          <p>${escapeHtml(option.summary || "")}</p>
          <div class="decision-metrics">
            ${Number.isFinite(Number(route.distance_km)) ? `<span><ha-icon icon="mdi:map-marker-distance"></ha-icon>${Number(route.distance_km).toLocaleString("de-DE", { maximumFractionDigits: 1 })} km</span>` : ""}
            ${Number.isFinite(Number(route.drive_minutes)) ? `<span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(this._formatDriveMinutes(Number(route.drive_minutes)))}</span>` : ""}
            ${costText ? `<span><ha-icon icon="mdi:cash"></ha-icon>${escapeHtml(costText)}</span>` : ""}
          </div>
          <div class="decision-procon"><div><strong>Vorteile</strong><ul>${(option.pros || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>Keine verifizierten Vorteile hinterlegt</li>"}</ul></div><div><strong>Nachteile</strong><ul>${(option.cons || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>Keine verifizierten Nachteile hinterlegt</li>"}</ul></div></div>
          <div class="button-row decision-actions">
            ${this._externalLink(this._googleMapsQueryUrl(mapsQuery), "Karte", "mdi:google-maps", "secondary-button")}
            <button class="${selected ? "secondary-button" : "primary-button"}" type="button" data-action="decision-select" data-decision-id="${escapeHtml(decision.id)}" data-option-id="${escapeHtml(option.id)}" ${transferred ? "disabled" : ""}><ha-icon icon="mdi:check-circle-outline"></ha-icon>${selected ? "Ausgewählt" : (currentPlan ? "Bei diesem Plan bleiben" : "Diese Option auswählen")}</button>
            ${selected && currentPlan ? `<span class="status-badge status-success">Keine Änderung nötig</span>` : ""}
            ${selected && !currentPlan && !transferred ? `<button class="primary-button" type="button" data-action="decision-transfer" data-decision-id="${escapeHtml(decision.id)}"><ha-icon icon="mdi:playlist-plus"></ha-icon>In Änderungskorb</button>` : ""}
            ${transferred ? `<span class="status-badge status-success">Im Änderungskorb</span>` : ""}
          </div>
        </div>
      </div>
      <footer class="decision-footer"><button class="icon-button" type="button" data-action="decision-prev" data-decision-id="${escapeHtml(decision.id)}" aria-label="Vorherige Option"><ha-icon icon="mdi:chevron-left"></ha-icon></button><div class="decision-dots">${options.map((item, optionIndex) => `<button type="button" class="decision-dot ${optionIndex === index ? "active" : ""}" data-action="decision-go" data-decision-id="${escapeHtml(decision.id)}" data-option-index="${optionIndex}" aria-label="Option ${optionIndex + 1}"></button>`).join("")}</div><button class="icon-button" type="button" data-action="decision-next" data-decision-id="${escapeHtml(decision.id)}" aria-label="Nächste Option"><ha-icon icon="mdi:chevron-right"></ha-icon></button><button class="text-button" type="button" data-action="decision-archive" data-decision-id="${escapeHtml(decision.id)}">Archivieren</button></footer>
    </article>`;
  },

  _integrityStatusLabel(status) {
    return {
      ready: "Bereit",
      attention: "Prüfen",
      incomplete: "Unvollständig",
    }[cleanText(status)] || "Prüfen";
  },

  _integrityDimensionLabel(key) {
    return {
      sequence: "Reihenfolge",
      locations: "Ortsprofile",
      routes: "Routen",
      visuals: "Bilder",
    }[key] || key;
  },

  _renderIntegrityCard(integrity = this._integrityData()) {
    const score = Math.max(0, Math.min(100, Number(integrity?.score || 0)));
    const stats = integrity?.stats || {};
    const dimensions = integrity?.dimensions || {};
    const status = cleanText(integrity?.status) || "incomplete";
    const repairable = Number(stats.repairable_location_count || 0);
    const routeIssues = Number(stats.route_issue_count || 0);
    const visualMissing = Number(stats.visual_missing_count || 0);
    return `<section class="panel-card integrity-card status-${escapeHtml(status)}">
      <div class="integrity-card-main">
        <div class="integrity-score" aria-label="Reisequalität ${score} Prozent"><strong>${score}</strong><span>%</span></div>
        <div class="integrity-copy">
          <span class="eyebrow">Datenqualität</span>
          <h2>Reisequalität · ${escapeHtml(this._integrityStatusLabel(status))}</h2>
          <p>${repairable ? `${repairable} Ortsprofile benötigen Aufmerksamkeit.` : "Die Standortdaten sind vollständig."} ${routeIssues ? `${routeIssues} Routen sind unvollständig oder veraltet.` : ""}</p>
        </div>
      </div>
      <div class="integrity-dimensions">
        ${Object.entries(dimensions).map(([key, value]) => `<div><span>${escapeHtml(this._integrityDimensionLabel(key))}</span><strong>${Math.round(Number(value || 0))}%</strong><i><b style="width:${Math.max(0, Math.min(100, Number(value || 0)))}%"></b></i></div>`).join("")}
      </div>
      <div class="integrity-summary">
        <span><ha-icon icon="mdi:map-marker-alert-outline"></ha-icon>${repairable} Orte offen</span>
        <span><ha-icon icon="mdi:map-marker-path"></ha-icon>${routeIssues} Routen offen</span>
        <span><ha-icon icon="mdi:image-search-outline"></ha-icon>${visualMissing} Bilder offen</span>
      </div>
      <div class="button-row">
        <button class="primary-button" type="button" data-action="integrity-open"><ha-icon icon="mdi:shield-check-outline"></ha-icon> Details prüfen</button>
        ${repairable && this._canEdit() ? `<button class="secondary-button" type="button" data-action="integrity-prepare-locations"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon> Stopps anreichern</button>` : ""}
      </div>
    </section>`;
  },

  _renderTravelIntegrity() {
    const integrity = this._integrityData();
    const stats = integrity?.stats || {};
    const issues = Array.isArray(integrity?.issues) ? integrity.issues : [];
    const severityIcon = {
      error: "mdi:alert-circle-outline",
      warning: "mdi:alert-outline",
      info: "mdi:information-outline",
    };
    const rows = issues.length
      ? issues.slice(0, 100).map((issue) => `<article class="integrity-issue severity-${escapeHtml(issue.severity || "info")}">
          <ha-icon icon="${severityIcon[issue.severity] || severityIcon.info}"></ha-icon>
          <div><strong>${escapeHtml(issue.title || "Prüfhinweis")}</strong><span>${escapeHtml(issue.message || "")}</span>${issue.day_date || issue.day_title ? `<small>${escapeHtml([issue.day_date, issue.day_title, issue.stop_name].filter(Boolean).join(" · "))}</small>` : ""}</div>
          ${issue.day_id ? `<button class="text-button" type="button" data-action="integrity-open-day" data-day-id="${escapeHtml(issue.day_id)}">Tag öffnen</button>` : ""}
        </article>`).join("")
      : `<div class="empty-inline"><ha-icon icon="mdi:check-decagram-outline"></ha-icon><div><strong>Keine offenen Qualitätsprobleme</strong><span>Die Reise ist für die vorhandenen Daten vollständig vorbereitet.</span></div></div>`;
    return `${this._renderModalHeader("Reisequalität", `${Number(integrity.score || 0)} % · ${this._integrityStatusLabel(integrity.status)}`)}
      <div class="integrity-dialog-body">
        <div class="integrity-dialog-stats">
          <span><strong>${Number(stats.stop_count || 0)}</strong> Stopps</span>
          <span><strong>${Number(stats.repairable_location_count || 0)}</strong> Orte offen</span>
          <span><strong>${Number(stats.route_issue_count || 0)}</strong> Routen offen</span>
          <span><strong>${Number(stats.visual_missing_count || 0)}</strong> Bilder offen</span>
        </div>
        <div class="integrity-issue-list">${rows}</div>
      </div>
      <div class="modal-actions integrity-dialog-actions">
        ${Number(stats.repairable_location_count || 0) && this._canEdit() ? `<button class="secondary-button" type="button" data-action="integrity-prepare-locations"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon>Stopps anreichern</button>` : ""}
        ${Number(stats.route_issue_count || 0) && this._canEdit() ? `<button class="secondary-button" type="button" data-action="integrity-recalculate-routes"><ha-icon icon="mdi:map-marker-path"></ha-icon>Routen neu berechnen</button>` : ""}
        ${Number(stats.visual_missing_count || 0) && this._canEdit() ? `<button class="secondary-button" type="button" data-action="integrity-auto-images"><ha-icon icon="mdi:image-sync-outline"></ha-icon>Planungsbilder ergänzen</button>` : ""}
        ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="integrity-repair-days"><ha-icon icon="mdi:calendar-check-outline"></ha-icon>Reisetage aufräumen</button>` : ""}
        <button class="primary-button" type="button" data-action="close-dialog">Schließen</button>
      </div>`;
  },

  async _planDayCalendarRepair() {
    // Read-only first: the traveller sees the resulting day count and end
    // date BEFORE a single day is proposed for removal (live report:
    // "Die Tage sind verwurschtelt").
    const retry = () => this._planDayCalendarRepair();
    const plan = await this._runAction("plan_day_calendar_repair", {
      trip_id: this._selectedTripId,
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "Die Reisetage konnten nicht geprüft werden",
      retry,
    });
    if (!plan) return null;
    this._dialog = { type: "day-calendar-repair", plan, busy: false };
    this._render({ preserveScroll: true });
    return plan;
  },

  async _proposeDayCalendarRepair() {
    if (this._dialog?.type !== "day-calendar-repair" || this._dialog.busy) return null;
    this._dialog.busy = true;
    this._render({ preserveScroll: true });
    const result = await this._runAction("propose_day_calendar_repair", {
      trip_id: this._selectedTripId,
    }, "", {
      refresh: true,
      errorMode: "dialog",
      errorTitle: "Der Reparaturvorschlag konnte nicht erstellt werden",
    });
    if (this._dialog?.type === "day-calendar-repair") this._dialog.busy = false;
    if (!result) {
      this._render({ preserveScroll: true });
      return null;
    }
    this._closeDialog();
    this._showToast(
      result.duplicate
        ? "Dieser Vorschlag liegt bereits unter „Übergaben“ bereit"
        : "Vorschlag liegt unter „Übergaben“ zur Prüfung bereit",
      "success",
      6000,
    );
    return result;
  },

  _renderDayCalendarRepair(dialog) {
    const plan = dialog?.plan || {};
    const empties = Array.isArray(plan.empty_days) ? plan.empty_days : [];
    const redated = Array.isArray(plan.redated_days) ? plan.redated_days : [];
    if (!plan.needs_repair) {
      return `${this._renderModalHeader("Reisetage prüfen", `${Number(plan.day_count_before || 0)} Reisetage`)}
        <div class="integrity-dialog-body">
          <div class="empty-inline"><ha-icon icon="mdi:check-decagram-outline"></ha-icon><div><strong>Der Reisekalender ist in Ordnung</strong><span>Keine leeren Platzhalter-Tage, und die Daten laufen der Reihenfolge nach.</span></div></div>
        </div>
        <div class="modal-actions"><button class="primary-button" type="button" data-action="close-dialog">Schließen</button></div>`;
    }
    const emptyRows = empties.map((entry) => `<article class="integrity-issue severity-warning">
        <ha-icon icon="mdi:calendar-remove-outline"></ha-icon>
        <div><strong>${escapeHtml(entry.title || "Reisetag")}</strong><span>Kein Stopp, keine Notiz, kein eigener Titel – wird entfernt.</span>${entry.date ? `<small>${escapeHtml(entry.date)}</small>` : ""}</div>
      </article>`).join("");
    const dateRows = redated.slice(0, 60).map((entry) => `<article class="integrity-issue severity-info">
        <ha-icon icon="mdi:calendar-edit-outline"></ha-icon>
        <div><strong>${escapeHtml(entry.title || "Reisetag")}</strong><span>${escapeHtml(entry.old_date || "ohne Datum")} → ${escapeHtml(entry.new_date || "")}</span><small>Tag ${Number(entry.sequence || 0)} der Reihenfolge</small></div>
      </article>`).join("");
    const more = redated.length > 60
      ? `<p class="muted-note">… und ${redated.length - 60} weitere Tage.</p>`
      : "";
    return `${this._renderModalHeader("Reisetage aufräumen", `${Number(plan.day_count_before || 0)} → ${Number(plan.day_count_after || 0)} Reisetage`)}
      <div class="integrity-dialog-body">
        <div class="integrity-dialog-stats">
          <span><strong>${empties.length}</strong> Platzhalter entfernen</span>
          <span><strong>${redated.length}</strong> Tage neu datieren</span>
          <span><strong>${Number(plan.day_count_after || 0)}</strong> Tage danach</span>
        </div>
        <p class="muted-note">Danach läuft die Reise vom ${escapeHtml(plan.start_date || "?")} bis zum ${escapeHtml(plan.end_date || "?")}${plan.last_day_title ? ` und endet mit „${escapeHtml(plan.last_day_title)}“` : ""}. Die Reihenfolge der Tage bleibt unverändert – nur die Daten folgen ihr wieder.</p>
        <div class="integrity-issue-list">${emptyRows}${dateRows}</div>
        ${more}
        <p class="muted-note">Nichts davon wird sofort geändert: Der Vorschlag landet unter „Übergaben“ und wird erst dort übernommen.</p>
      </div>
      <div class="modal-actions">
        <button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button>
        <button class="primary-button" type="button" data-action="day-calendar-repair-submit" ${dialog.busy ? "disabled" : ""}><ha-icon icon="mdi:calendar-check-outline"></ha-icon>${dialog.busy ? "Wird vorbereitet…" : "Vorschlag erstellen"}</button>
      </div>`;
  },
};
