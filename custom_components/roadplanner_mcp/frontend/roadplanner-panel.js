const WS_GET_DATA = "roadplanner_mcp/panel/get_data";
const WS_ACTION = "roadplanner_mcp/panel/action";

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const cleanText = (value) => String(value ?? "").trim();

const newClientRequestId = () => {
  try {
    if (globalThis.crypto?.randomUUID) return `assistant-${globalThis.crypto.randomUUID()}`;
  } catch (_error) {
    // Fall back to a timestamp plus random material below.
  }
  return `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
};

const nullableNumber = (value, integer = false) => {
  const text = cleanText(value);
  if (!text) return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return null;
  return integer && !Number.isInteger(parsed) ? null : parsed;
};

const cloneObject = (value) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  try {
    return structuredClone(value);
  } catch (_error) {
    return JSON.parse(JSON.stringify(value));
  }
};

const operationLabels = {
  update_trip: "Reise ändern",
  add_day: "Tag hinzufügen",
  update_day: "Tag ändern",
  move_day: "Tag verschieben",
  remove_day: "Tag löschen",
  add_stop: "Stopp hinzufügen",
  update_stop: "Stopp ändern",
  move_stop: "Stopp verschieben",
  remove_stop: "Stopp löschen",
  add_preference: "Präferenz hinzufügen",
  update_preference: "Präferenz ändern",
  remove_preference: "Präferenz löschen",
};

const statusLabels = {
  planned: "Geplant",
  tentative: "Vorläufig",
  confirmed: "Bestätigt",
  completed: "Erledigt",
  cancelled: "Entfällt",
  pending: "Offen",
  review_required: "Prüfung nötig",
  conflict: "Konflikt",
  failed: "Fehler",
  applied: "Übernommen",
  archived: "Archiviert",
  waypoint: "Wegpunkt",
  start: "Start",
  origin: "Start",
  destination: "Tagesziel",
  overnight: "Übernachtung",
  ferry: "Fähre",
  campsite: "Campingplatz",
  camping: "Stellplatz",
  parking: "Parkplatz",
  sightseeing: "Stadtbesichtigung",
  attraction: "Sehenswürdigkeit",
  activity: "Aktivität",
  restaurant: "Restaurant",
  shopping: "Einkauf",
  charging: "Ladepunkt",
  fuel: "Tankstelle",
  service: "Service",
  water: "Wasser",
  waste: "Entsorgung",
  laundry: "Wäsche",
  border: "Grenze",
  break: "Pause",
  viewpoint: "Aussichtspunkt",
  fishing: "Angelplatz",
  viewer: "Leser",
  editor: "Bearbeiter",
  approver: "Freigeber",
  admin: "Administrator",
};

const archiveDocumentTypeLabels = {
  ferry_booking: "Fährbuchung",
  camping_booking: "Campingplatzbuchung",
  accommodation_booking: "Unterkunft",
  restaurant_reservation: "Restaurantreservierung",
  event_ticket: "Veranstaltungsticket",
  admission_ticket: "Eintrittsticket",
  transport_ticket: "Transportticket",
  invoice: "Rechnung",
  receipt: "Beleg",
  insurance: "Versicherung",
  vehicle_document: "Fahrzeugdokument",
  fishing_license: "Angellizenz",
  travel_document: "Reisedokument",
  other: "Sonstiges",
};

const archiveExpenseCategoryLabels = {
  fuel: "Tanken",
  charging: "Laden",
  campsite: "Campingplatz",
  motorhome_site: "Stellplatz",
  parking: "Parken",
  restaurant: "Restaurant",
  snack: "Imbiss",
  groceries: "Lebensmittel",
  ferry: "Fähre",
  transport: "Transportmittel",
  other: "Sonstiges",
};

const archiveStatusLabels = {
  draft: "Neu", analysis_pending: "Analyse läuft", analyzed: "Analysiert",
  confirmed: "Bestätigt", cancelled: "Storniert", expired: "Abgelaufen",
  file_removed: "Original gelöscht", open: "Offen", done: "Erledigt",
  dismissed: "Verworfen", planned: "Geplant", paid: "Bezahlt",
  refundable: "Erstattbar", refunded: "Erstattet", unknown: "Unklar",
};

const stopIcons = {
  waypoint: "mdi:map-marker-outline",
  start: "mdi:flag-outline",
  origin: "mdi:flag-outline",
  destination: "mdi:flag-checkered",
  overnight: "mdi:weather-night",
  ferry: "mdi:ferry",
  campsite: "mdi:tent",
  camping: "mdi:van-utility",
  parking: "mdi:parking",
  sightseeing: "mdi:city-variant-outline",
  attraction: "mdi:camera-marker-outline",
  activity: "mdi:hiking",
  restaurant: "mdi:silverware-fork-knife",
  shopping: "mdi:cart-outline",
  charging: "mdi:ev-station",
  fuel: "mdi:gas-station-outline",
  service: "mdi:tools",
  water: "mdi:water-outline",
  waste: "mdi:delete-outline",
  laundry: "mdi:washing-machine",
  border: "mdi:passport",
  break: "mdi:coffee-outline",
  viewpoint: "mdi:binoculars",
  fishing: "mdi:fish",
};

class RoadplannerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._panel = null;
    this._started = false;
    this._connected = false;
    this._data = null;
    this._signature = "";
    this._activeTab = "overview";
    this._selectedTripId = null;
    this._selectedDayId = null;
    this._expandedDays = new Set();
    this._dialog = null;
    this._busy = false;
    this._initialLoading = true;
    this._error = "";
    this._toast = null;
    this._toastTimer = null;
    this._eventUnsubscribe = null;
    this._eventRefreshTimer = null;
    this._refreshQueued = false;
    this._narrow = false;
    this._mapModels = new Map();
    this._mapHelpersPromise = null;
    this._mapHydrationToken = 0;
    this._assistantLastFailedText = "";
    this._assistantLastFailedRequestId = "";
    this._assistantDiagnostics = null;
    this._assistantAutoBriefingRequested = new Set();
    this._assistantSubmitInFlight = false;
    this._assistantPending = null;
    this._decisionCreateInFlightMessageId = "";
    this._actionErrorRetry = null;
    this._archiveUploadContext = null;
    this._offlineDocumentIds = new Set();
    this._archiveDbPromise = null;
    this._decisionSlideIndexes = new Map();
    this._decisionSwipe = null;
    this._onedriveAuth = null;

    this.shadowRoot.addEventListener("pointerdown", (event) => {
      const button = event.target?.closest?.("[data-action='assistant-send']");
      if (button) {
        event.preventDefault();
        event.stopPropagation();
        void this._submitAssistantComposer(button.closest("form"));
        return;
      }
      const slide = event.target?.closest?.(".decision-slide");
      if (!slide || event.target?.closest?.("button, a, input, select, textarea")) return;
      const card = slide.closest("[data-decision-card]");
      if (!card) return;
      this._decisionSwipe = {
        decisionId: card.dataset.decisionCard,
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
      };
    });
    this.shadowRoot.addEventListener("pointerup", (event) => {
      const swipe = this._decisionSwipe;
      this._decisionSwipe = null;
      if (!swipe || swipe.pointerId !== event.pointerId) return;
      const dx = event.clientX - swipe.x;
      const dy = event.clientY - swipe.y;
      if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      const decision = (this._experienceData().decisions || []).find((item) => item.id === swipe.decisionId);
      const options = decision?.options || [];
      if (options.length < 2) return;
      let index = Number(this._decisionSlideIndexes.get(decision.id) || 0);
      index = (index + (dx < 0 ? 1 : -1) + options.length) % options.length;
      this._decisionSlideIndexes.set(decision.id, index);
      this._render({ preserveScroll: true });
    });
    this.shadowRoot.addEventListener("pointercancel", () => { this._decisionSwipe = null; });
    this.shadowRoot.addEventListener("click", (event) => this._handleClick(event));
    this.shadowRoot.addEventListener("change", (event) => this._handleChange(event));
    this.shadowRoot.addEventListener("submit", (event) => this._handleSubmit(event));
    this.shadowRoot.addEventListener("error", (event) => {
      const image = event.target?.closest?.("img[data-destination-image]");
      if (image) image.closest(".destination-image")?.classList.add("image-error");
    }, true);
    this.shadowRoot.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._dialog) this._closeDialog();
      const textarea = event.target?.closest?.("textarea[name='message']");
      if (textarea && event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        const form = textarea.closest("form[data-form='assistant-chat']");
        void this._submitAssistantComposer(form);
      }
    });
    this.shadowRoot.addEventListener("paste", (event) => {
      const zone = event.target?.closest?.("[data-archive-paste-zone]");
      if (!zone && this._dialog?.type !== "archive-paste-text") return;
      const file = this._clipboardFileFromData(event.clipboardData);
      if (!file) return;
      event.preventDefault();
      this._closeDialog({ flushRefresh: false });
      void this._uploadArchiveFile(file, {
        source: "clipboard_paste",
        keepOriginal: true,
        links: this._archiveLinks(),
      });
    });
    this.shadowRoot.addEventListener("dragover", (event) => {
      const zone = event.target?.closest?.("[data-archive-drop-zone]");
      if (!zone) return;
      event.preventDefault();
      zone.classList.add("drag-active");
    });
    this.shadowRoot.addEventListener("dragleave", (event) => {
      event.target?.closest?.("[data-archive-drop-zone]")?.classList.remove("drag-active");
    });
    this.shadowRoot.addEventListener("drop", (event) => {
      const zone = event.target?.closest?.("[data-archive-drop-zone]");
      if (!zone) return;
      event.preventDefault();
      zone.classList.remove("drag-active");
      const file = this._supportedArchiveFile(Array.from(event.dataTransfer?.files || []));
      if (!file) {
        this._showToast("Keine unterstützte PDF-, Bild- oder Textdatei gefunden.", "error");
        return;
      }
      this._closeDialog({ flushRefresh: false });
      void this._uploadArchiveFile(file, {
        source: this._activeTab === "import" ? "universal_import" : "drag_drop",
        keepOriginal: true,
        links: this._archiveLinks(),
      });
    });
  }

  set hass(value) {
    this._hass = value;
    this._startWhenReady();
  }

  get hass() {
    return this._hass;
  }

  set panel(value) {
    this._panel = value;
  }

  get panel() {
    return this._panel;
  }

  set narrow(value) {
    this._narrow = Boolean(value);
    this.toggleAttribute("narrow", this._narrow);
  }

  set route(_value) {}

  connectedCallback() {
    this._connected = true;
    this._startWhenReady();
  }

  disconnectedCallback() {
    this._connected = false;
    this._started = false;
    this._mapHydrationToken += 1;
    if (this._eventUnsubscribe) {
      this._eventUnsubscribe();
      this._eventUnsubscribe = null;
    }
    if (this._eventRefreshTimer) {
      window.clearTimeout(this._eventRefreshTimer);
      this._eventRefreshTimer = null;
    }
    if (this._toastTimer) {
      window.clearTimeout(this._toastTimer);
      this._toastTimer = null;
    }
  }

  async _startWhenReady() {
    if (!this._connected || !this._hass || this._started) return;
    this._started = true;
    this._render();
    await this._subscribeToUpdates();
    await this._loadData();
  }

  async _subscribeToUpdates() {
    const eventType = this._panel?.config?.event_type || "roadplanner_mcp_updated";
    const entryId = this._panel?.config?.entry_id;
    const connection = this._hass?.connection;
    if (!connection?.subscribeEvents) return;
    try {
      this._eventUnsubscribe = await connection.subscribeEvents((event) => {
        if (entryId && event?.data?.entry_id !== entryId) return;
        if (this._busy || this._dialog) {
          this._refreshQueued = true;
          return;
        }
        if (this._eventRefreshTimer) window.clearTimeout(this._eventRefreshTimer);
        this._eventRefreshTimer = window.setTimeout(() => {
          this._eventRefreshTimer = null;
          this._loadData({ silent: true });
        }, 250);
      }, eventType);
    } catch (error) {
      console.warn("Roadplanner update subscription failed", error);
    }
  }

  async _send(message) {
    const response = await this._hass.connection.sendMessagePromise(message);
    return response?.result ?? response;
  }

  async _loadData({ silent = false, force = false } = {}) {
    if (!this._hass || (this._busy && !force)) return;
    if (!silent) {
      this._initialLoading = !this._data;
      this._error = "";
      this._render();
    }
    try {
      const request = { type: WS_GET_DATA };
      if (this._selectedTripId) request.trip_id = this._selectedTripId;
      const payload = await this._send(request);
      const signature = JSON.stringify(payload);
      if (force || signature !== this._signature) {
        this._data = payload;
        this._signature = signature;
        this._selectedTripId = payload.selected_trip_id;
        const availableDayIds = new Set((payload.days?.days || []).map((day) => day.id));
        if (!this._selectedDayId || !availableDayIds.has(this._selectedDayId)) {
          this._selectedDayId = payload.summary?.next_day?.id
            || payload.days?.days?.[0]?.id
            || null;
        }
        this._error = "";
        this._initialLoading = false;
        this._render({ preserveScroll: true });
        void this._refreshOfflineDocumentIds();
        if (this._activeTab === "assistant") this._maybeStartAutoBriefing();
      } else if (this._initialLoading) {
        this._initialLoading = false;
        this._render();
      }
    } catch (error) {
      this._initialLoading = false;
      this._error = this._errorMessage(error);
      this._render({ preserveScroll: true });
    }
  }

  _setBusy(value) {
    this._busy = Boolean(value);
    const app = this.shadowRoot.querySelector(".app");
    if (app) app.classList.toggle("busy", this._busy);
    const progress = this.shadowRoot.querySelector(".progress");
    if (progress) progress.toggleAttribute("hidden", !this._busy);
  }

  async _runAction(action, data = {}, successMessage = "Änderung gespeichert", options = {}) {
    if (this._busy) return null;
    const {
      refresh = true,
      errorMode = "toast",
      errorTitle = "Roadplanner-Aktion fehlgeschlagen",
      retry = null,
    } = options || {};
    const tripScopedActions = new Set([
      "update_trip",
      "add_day",
      "update_day",
      "remove_day",
      "add_stop",
      "update_stop",
      "remove_stop",
      "calculate_day_route",
      "calculate_trip_routes",
      "preview_handoff",
      "apply_handoff",
      "archive_handoff",
    ]);
    const payload = { ...data };
    if (tripScopedActions.has(action) && this._selectedTripId) {
      payload.expected_trip_id = this._selectedTripId;
    }
    this._setBusy(true);
    try {
      const result = await this._send({ type: WS_ACTION, action, data: payload });
      if (successMessage) this._showToast(successMessage, "success");
      if (refresh) await this._loadData({ silent: true, force: true });
      return result;
    } catch (error) {
      const message = this._errorMessage(error);
      if (errorMode === "dialog") {
        this._showActionError(message, {
          title: errorTitle,
          action,
          retry,
        });
      } else {
        this._showToast(message, "error", 6500);
      }
      if (String(error?.code || "").includes("revision")) {
        await this._loadData({ silent: true, force: true });
      }
      return null;
    } finally {
      this._setBusy(false);
      if (this._refreshQueued) {
        this._refreshQueued = false;
        await this._loadData({ silent: true, force: true });
      }
    }
  }

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
  }

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
  }

  _errorMessage(error) {
    if (typeof error === "string") return error;
    return error?.message || error?.error?.message || "Unbekannter Roadplanner-Fehler";
  }

  _requestIdFromMessage(message) {
    const match = String(message || "").match(/\(Anfrage\s+([^)]+)\)/i);
    return match ? cleanText(match[1]) : "";
  }

  _showActionError(message, { title = "Roadplanner-Aktion fehlgeschlagen", action = "", retry = null } = {}) {
    const text = cleanText(message) || "Unbekannter Roadplanner-Fehler";
    this._actionErrorRetry = typeof retry === "function" ? retry : null;
    this._dialog = {
      type: "action-error",
      title: cleanText(title) || "Roadplanner-Aktion fehlgeschlagen",
      message: text,
      requestId: this._requestIdFromMessage(text),
      action: cleanText(action),
    };
    this._render({ preserveScroll: true });
  }

  async _copyActionError() {
    const dialog = this._dialog?.type === "action-error" ? this._dialog : null;
    if (!dialog) return;
    const text = [
      dialog.title,
      dialog.message,
      dialog.requestId ? `Anfrage: ${dialog.requestId}` : "",
      dialog.action ? `Aktion: ${dialog.action}` : "",
    ].filter(Boolean).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      this._showToast("Fehlerdetails kopiert", "success", 3000);
    } catch (_error) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand?.("copy");
      textarea.remove();
      this._showToast("Fehlerdetails kopiert", "success", 3000);
    }
  }

  _showToast(message, type = "success", duration = 3500) {
    this._toast = { message, type };
    if (this._toastTimer) window.clearTimeout(this._toastTimer);
    this._toastTimer = window.setTimeout(() => {
      this._toast = null;
      this._renderToastHost();
    }, duration);
    this._renderToastHost();
  }

  _renderToastHost() {
    const host = this.shadowRoot.querySelector(".toast-host");
    if (!host) return;
    host.innerHTML = this._toast ? this._renderToast() : "";
  }

  _closeDialog({ flushRefresh = true } = {}) {
    if (this._dialog?.type === "action-error") this._actionErrorRetry = null;
    this._dialog = null;
    this._render({ preserveScroll: true });
    if (flushRefresh && this._refreshQueued && !this._busy) {
      this._refreshQueued = false;
      void this._loadData({ silent: true, force: true });
    }
  }

  _confirm(title, message, confirmLabel, callback, destructive = false) {
    this._dialog = {
      type: "confirm",
      title,
      message,
      confirmLabel,
      destructive,
      callback,
    };
    this._render({ preserveScroll: true });
  }

  _findDay(dayId) {
    return this._data?.days?.days?.find((day) => day.id === dayId) || null;
  }

  _findStop(dayId, stopId) {
    return this._findDay(dayId)?.stops?.find((stop) => stop.id === stopId) || null;
  }

  _isOvernightStop(stop) {
    return ["overnight", "campsite", "camping", "stellplatz", "wildcamp", "accommodation"]
      .includes(cleanText(stop?.type).toLowerCase());
  }

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
  }

  _effectiveDayStops(day) {
    const canonicalStops = [...(day?.stops || [])];
    const days = this._data?.days?.days || [];
    const index = days.findIndex((item) => item.id === day?.id);
    if (index <= 0) return canonicalStops;
    const previous = days[index - 1];
    const previousStops = previous?.stops || [];
    const overnight = previousStops.at(-1);
    if (!this._isOvernightStop(overnight)) return canonicalStops;
    if (canonicalStops.length && this._samePlace(overnight, canonicalStops[0])) {
      return canonicalStops;
    }
    return [{
      ...cloneObject(overnight),
      _inherited: true,
      _sourceDayId: previous.id,
      _sourceDayTitle: previous.title,
    }, ...canonicalStops];
  }

  _currentRevision() {
    return this._data?.summary?.revision ?? 0;
  }

  _canEdit() {
    return Boolean(this._data?.capabilities?.can_edit && this._data?.selected_is_active);
  }

  _canActivate() {
    return Boolean(this._data?.capabilities?.can_activate);
  }

  _canApprove() {
    return Boolean(this._data?.capabilities?.can_approve && this._data?.selected_is_active);
  }

  _canAdmin() {
    return Boolean(this._data?.capabilities?.can_admin);
  }

  _formatDate(value) {
    if (!value) return "ohne Datum";
    try {
      const locale = this._hass?.locale?.language || this._hass?.language || "de-DE";
      return new Intl.DateTimeFormat(locale, {
        weekday: "short",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(new Date(`${value}T12:00:00`));
    } catch (_error) {
      return value;
    }
  }

  _formatTimestamp(value) {
    if (!value) return "—";
    try {
      const locale = this._hass?.locale?.language || this._hass?.language || "de-DE";
      return new Intl.DateTimeFormat(locale, {
        day: "2-digit",
        month: "2-digit",
        year: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }).format(new Date(value));
    } catch (_error) {
      return value;
    }
  }

  _formatDriveMinutes(value) {
    if (!Number.isFinite(value)) return "";
    const hours = Math.floor(value / 60);
    const minutes = value % 60;
    if (!hours) return `${minutes} min`;
    return `${hours} h${minutes ? ` ${minutes} min` : ""}`;
  }

  _statusLabel(value) {
    return statusLabels[value] || value || "—";
  }

  _statusClass(value) {
    if (["confirmed", "completed", "applied"].includes(value)) return "success";
    if (["conflict", "failed", "cancelled"].includes(value)) return "danger";
    if (["review_required", "tentative"].includes(value)) return "warning";
    return "neutral";
  }

  _safeUrl(value) {
    const text = cleanText(value);
    if (!text) return "";
    if (text.startsWith("/local/") || text.startsWith("/api/") || text.startsWith("/media/")) {
      return text;
    }
    try {
      const parsed = new URL(text, window.location.origin);
      if (parsed.protocol === "https:") return parsed.href;
      if (parsed.protocol === "http:" && parsed.origin === window.location.origin) {
        return parsed.href;
      }
    } catch (_error) {
      return "";
    }
    return "";
  }

  _assistantLinkDetails(value) {
    const safe = this._safeUrl(value);
    if (!safe) return null;
    try {
      const parsed = new URL(safe, window.location.origin);
      const hostname = parsed.hostname.toLowerCase();
      const googleMaps = hostname === "maps.google.com"
        || hostname === "maps.app.goo.gl"
        || (hostname === "goo.gl" && parsed.pathname.startsWith("/maps"))
        || (hostname.endsWith(".google.com") && parsed.pathname.startsWith("/maps"));
      return {
        url: safe,
        icon: googleMaps ? "mdi:google-maps" : "mdi:open-in-new",
        className: googleMaps ? "google-maps" : "external",
        googleMaps,
      };
    } catch (_error) {
      return null;
    }
  }

  _assistantLinkLabel(url, fallback = "") {
    const details = this._assistantLinkDetails(url);
    if (!details) return cleanText(fallback);
    if (details.googleMaps) return cleanText(fallback) || "Google Maps öffnen";
    if (cleanText(fallback)) return cleanText(fallback);
    try {
      const parsed = new URL(details.url, window.location.origin);
      const hostname = parsed.hostname.replace(/^www\./i, "");
      let path = parsed.pathname && parsed.pathname !== "/" ? parsed.pathname : "";
      try {
        path = decodeURI(path);
      } catch (_error) {
        // Keep the encoded path when it cannot be decoded safely.
      }
      const display = `${hostname}${path}` || details.url;
      return display.length > 84 ? `${display.slice(0, 81)}…` : display;
    } catch (_error) {
      return details.url;
    }
  }

  _trimAssistantUrlCandidate(value) {
    let url = String(value || "");
    let suffix = "";
    while (url && /[.,;:!?]$/.test(url)) {
      suffix = url.slice(-1) + suffix;
      url = url.slice(0, -1);
    }
    for (const [open, close] of [["(", ")"], ["[", "]"], ["{", "}"]]) {
      const count = (input, character) => [...input].filter((item) => item === character).length;
      while (url.endsWith(close) && count(url, close) > count(url, open)) {
        suffix = close + suffix;
        url = url.slice(0, -1);
      }
    }
    return { url, suffix };
  }

  _renderAssistantLink(url, label = "") {
    const details = this._assistantLinkDetails(url);
    if (!details) return "";
    const display = this._assistantLinkLabel(details.url, label);
    return `<a class="assistant-inline-link ${details.className}" href="${escapeHtml(details.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(details.url)}"><ha-icon icon="${details.icon}"></ha-icon><span>${escapeHtml(display)}</span></a>`;
  }

  _linkifyAssistantPlainText(value) {
    const text = String(value ?? "");
    const pattern = /https?:\/\/[^\s<>"']+/gi;
    let cursor = 0;
    let output = "";
    for (const match of text.matchAll(pattern)) {
      const index = match.index ?? 0;
      output += escapeHtml(text.slice(cursor, index));
      const candidate = this._trimAssistantUrlCandidate(match[0]);
      const link = this._renderAssistantLink(candidate.url);
      output += link || escapeHtml(candidate.url);
      output += escapeHtml(candidate.suffix);
      cursor = index + match[0].length;
    }
    output += escapeHtml(text.slice(cursor));
    return output;
  }

  _renderAssistantContent(value) {
    const text = String(value ?? "");
    const markdownLink = /\[([^\]\n]{1,240})\]\((https?:\/\/[^\s<]+)\)/gi;
    let cursor = 0;
    let output = "";
    for (const match of text.matchAll(markdownLink)) {
      const index = match.index ?? 0;
      output += this._linkifyAssistantPlainText(text.slice(cursor, index));
      const candidate = this._trimAssistantUrlCandidate(match[2]);
      const link = this._renderAssistantLink(candidate.url, match[1]);
      output += link || this._linkifyAssistantPlainText(match[0]);
      output += escapeHtml(candidate.suffix);
      cursor = index + match[0].length;
    }
    output += this._linkifyAssistantPlainText(text.slice(cursor));
    return output;
  }

  _mediaFrom(entity) {
    const details = entity?.details;
    if (!details || typeof details !== "object" || Array.isArray(details)) return null;
    const media = details.media && typeof details.media === "object"
      ? details.media
      : details;
    const imageUrl = this._safeUrl(media.image_url || media.url);
    if (!imageUrl) return null;
    return {
      image_url: imageUrl,
      alt: cleanText(media.alt) || cleanText(entity?.name) || cleanText(entity?.title) || "Reiseziel",
      attribution: cleanText(media.attribution),
      source_url: this._safeUrl(media.source_url),
      provider: cleanText(media.provider),
    };
  }

  _detailsWithMedia(entity, media) {
    const details = cloneObject(entity?.details);
    if (!media?.image_url) {
      delete details.media;
      return details;
    }
    details.media = {
      image_url: cleanText(media.image_url),
      alt: cleanText(media.alt),
      attribution: cleanText(media.attribution),
      source_url: cleanText(media.source_url),
      provider: cleanText(media.provider),
    };
    return details;
  }

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
      timestamp,
    };
  }

  _dayRoutePoints(day) {
    return this._effectiveDayStops(day)
      .map((stop, index) => this._coordinate(stop, day, index))
      .filter(Boolean);
  }

  _allRoutePoints() {
    const points = [];
    let sequence = 0;
    for (const day of this._data?.days?.days || []) {
      for (const stop of day.stops || []) {
        const point = this._coordinate(stop, day, sequence);
        sequence += 1;
        if (point) {
          points.push({
            ...point,
            dayId: day.id,
            dayTitle: day.title,
            date: day.date,
          });
        }
      }
    }
    return points;
  }

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
  }

  _routeGeometryPoints(day) {
    const routing = day?.routing;
    if (!routing || !["calculated", "partial"].includes(routing.status)) return [];
    if (routing.geometry_stale) return [];
    return this._geometryCoordinatesToPoints(routing?.geometry?.coordinates, day, day?.title || "Straßenroute");
  }

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
  }

  _tripRoutePaths(days) {
    return (days || []).flatMap((day) => this._routingSegmentPaths(day));
  }

  _effectiveDayStart(day) {
    const stops = this._effectiveDayStops(day);
    const inherited = stops.find((stop) => stop?._inherited);
    return inherited?.name || day?.start || stops[0]?.name || "?";
  }

  _routeStatusLabel(day) {
    const status = cleanText(day?.routing?.status);
    const labels = {
      calculated: "Straßenroute berechnet",
      partial: "Teilroute berechnet",
      stale: "Route veraltet",
      manual_override: "Fahrdaten manuell",
    };
    return labels[status] || "Noch nicht berechnet";
  }

  _routeCoverageText(metrics = this._data?.summary?.route_metrics) {
    if (!metrics) return "Noch keine Fahrdaten";
    const candidate = Number(metrics.route_candidate_day_count || 0);
    const calculated = Number(metrics.calculated_day_count || 0)
      + Number(metrics.partial_day_count || 0)
      + Number(metrics.manual_day_count || 0);
    if (!candidate) return "Keine berechenbaren Tagesetappen";
    if (metrics.status === "complete") return `${calculated}/${candidate} Etappen berechnet`;
    return `${calculated}/${candidate} Etappen mit Fahrdaten`;
  }

  _externalLink(url, label, icon = "mdi:google-maps", className = "secondary-button") {
    const safe = this._safeUrl(url);
    if (!safe) return "";
    return `<a class="${className}" href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="${icon}"></ha-icon>${escapeHtml(label)}</a>`;
  }

  _googleMapsQueryUrl(value) {
    const query = cleanText(value);
    if (!query) return "";
    return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
  }

  _tripImages(limit = 12) {
    const result = [];
    const seen = new Set();
    const add = (entity, context) => {
      const media = this._mediaFrom(entity);
      if (!media || seen.has(media.image_url)) return;
      seen.add(media.image_url);
      result.push({ ...media, context });
    };
    add(this._data?.summary?.trip, this._data?.summary?.trip?.title);
    for (const day of this._data?.days?.days || []) {
      add(day, day.title);
      for (const stop of day.stops || []) add(stop, `${day.title} · ${stop.name}`);
      if (result.length >= limit) break;
    }
    return result.slice(0, limit);
  }

  _archiveData() {
    return this._data?.travel_archive || { documents: [], expenses: [], todos: [], stats: {}, by_day: {}, by_stop: {} };
  }

  _archiveDocument(documentId) {
    return (this._archiveData().documents || []).find((item) => item.id === documentId) || null;
  }

  _importDocuments() {
    return (this._archiveData().documents || []).filter((item) => item?.analysis?.universal_import);
  }

  _universalImport(documentId) {
    const documentItem = this._archiveDocument(documentId);
    return documentItem?.analysis?.universal_import || null;
  }

  async _analyzeUniversalImport(documentId) {
    const result = await this._runAction("universal_import_analyze", {
      trip_id: this._selectedTripId,
      document_id: documentId,
    }, "Übergabe analysiert");
    if (!result?.document) return;
    await this._loadData({ silent: true, force: true });
    this._dialog = {
      type: "universal-import-review",
      document: result.document,
      importResult: result.import || result.document?.analysis?.universal_import || {},
    };
    this._render({ preserveScroll: true });
  }

  _openUniversalImport(documentId) {
    const documentItem = this._archiveDocument(documentId);
    const importResult = documentItem?.analysis?.universal_import;
    if (!documentItem || !importResult) return;
    this._dialog = { type: "universal-import-review", document: documentItem, importResult };
    this._render({ preserveScroll: true });
  }

  _archiveExpense(expenseId) {
    return (this._archiveData().expenses || []).find((item) => item.id === expenseId) || null;
  }

  _archiveTodo(todoId) {
    return (this._archiveData().todos || []).find((item) => item.id === todoId) || null;
  }

  _parseTodoDue(value) {
    const text = cleanText(value);
    if (!text) return null;
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
    const candidate = dateOnly ? new Date(`${text}T23:59:59`) : new Date(text);
    return Number.isNaN(candidate.getTime()) ? null : candidate;
  }

  _todoDueState(todo, now = new Date()) {
    if (!todo || todo.status !== "open") return "closed";
    const due = this._parseTodoDue(todo.due_at);
    if (!due) return "unscheduled";
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const endToday = new Date(startToday.getTime() + 86400000 - 1);
    if (due < startToday) return "overdue";
    if (due <= endToday) return "today";
    if (due.getTime() <= now.getTime() + 86400000) return "upcoming";
    return "future";
  }

  _todoTimingSummary() {
    const summary = { open: 0, overdue: 0, today: 0, upcoming: 0, future: 0, unscheduled: 0 };
    const now = new Date();
    for (const todo of this._archiveData().todos || []) {
      const state = this._todoDueState(todo, now);
      if (state === "closed") continue;
      summary.open += 1;
      summary[state] = (summary[state] || 0) + 1;
    }
    summary.urgent = summary.overdue + summary.today;
    return summary;
  }

  _todoDueLabel(todo) {
    const state = this._todoDueState(todo);
    if (state === "overdue") return "Überfällig";
    if (state === "today") return "Heute fällig";
    if (state === "upcoming") return "In den nächsten 24 Stunden";
    return "";
  }

  _archiveLinks(dayId = "", stopId = "") {
    const day = cleanText(dayId);
    const stop = cleanText(stopId);
    return {
      day_ids: day ? [day] : [],
      stop_links: day && stop ? [{ day_id: day, stop_id: stop }] : [],
      people: [],
    };
  }

  _startArchiveFileSelection({ source = "panel_upload", dayId = "", stopId = "", camera = false, keepOriginal = true } = {}) {
    if (!this._canEdit()) return;
    this._archiveUploadContext = {
      source,
      keepOriginal: Boolean(keepOriginal),
      links: this._archiveLinks(dayId, stopId),
    };
    const input = this.shadowRoot.querySelector(camera ? "#roadplanner-camera-input" : "#roadplanner-document-input");
    if (!input) return;
    input.value = "";
    input.click();
  }

  async _handleArchiveFileInput(input) {
    const file = input?.files?.[0];
    if (!file) return;
    const context = this._archiveUploadContext || {
      source: "panel_upload",
      keepOriginal: true,
      links: this._archiveLinks(),
    };
    this._archiveUploadContext = null;
    await this._uploadArchiveFile(file, context);
  }

  async _uploadArchiveFile(file, context = {}) {
    if (!file || !this._selectedTripId) return;
    const maxBytes = Number(this._data?.settings?.document_max_upload_bytes || 0);
    if (maxBytes && file.size > maxBytes) {
      this._showToast(`Die Datei ist größer als ${Math.round(maxBytes / 1024 / 1024)} MB.`, "error", 6500);
      return;
    }
    const ticket = await this._runAction("archive_create_upload_ticket", {
      trip_id: this._selectedTripId,
      source: context.source || "panel_upload",
      keep_original: context.keepOriginal !== false,
      links: context.links || this._archiveLinks(),
    }, "");
    if (!ticket?.upload_url) return;
    this._setBusy(true);
    let document = null;
    try {
      const body = new FormData();
      body.append("file", file, file.name || "document");
      const response = await fetch(ticket.upload_url, {
        method: "POST",
        body,
        credentials: "same-origin",
        cache: "no-store",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || payload?.ok === false) {
        throw new Error(payload?.error || `Upload fehlgeschlagen (HTTP ${response.status})`);
      }
      document = payload?.document || null;
      this._showToast("Dokument sicher hochgeladen", "success", 3500);
    } catch (error) {
      this._showToast(this._errorMessage(error), "error", 7500);
      return;
    } finally {
      this._setBusy(false);
    }
    await this._loadData({ silent: true, force: true });
    if (!document?.id) return;
    if (context.source === "universal_import") {
      await this._analyzeUniversalImport(document.id);
      return;
    }
    if (context.source === "assistant") {
      this._dialog = { type: "attachment-purpose", document };
      this._render({ preserveScroll: true });
      return;
    }
    const analysisEnabled = Boolean(this._data?.settings?.document_analysis_enabled);
    const analysisConfigured = Boolean(this._data?.settings?.document_analysis_configured);
    if (analysisEnabled && analysisConfigured) {
      const result = await this._runAction("archive_analyze_document", {
        trip_id: this._selectedTripId,
        document_id: document.id,
      }, "Dokument analysiert");
      if (result?.document) {
        this._dialog = {
          type: "archive-document-review",
          document: result.document,
          analysis: result.analysis || result.document.analysis || {},
        };
        this._render({ preserveScroll: true });
        return;
      }
    }
    const latest = this._archiveDocument(document.id) || document;
    this._dialog = {
      type: "archive-document-review",
      document: latest,
      analysis: latest.analysis || {},
    };
    this._render({ preserveScroll: true });
  }

  _archiveExtensionForMime(type) {
    const normalized = cleanText(type).toLowerCase();
    const mapping = {
      "application/pdf": "pdf",
      "image/png": "png",
      "image/jpeg": "jpg",
      "image/webp": "webp",
      "image/heic": "heic",
      "image/heif": "heif",
      "text/plain": "txt",
      "text/markdown": "md",
      "text/csv": "csv",
      "text/calendar": "ics",
      "application/gpx+xml": "gpx",
      "application/xml": "xml",
      "text/xml": "xml",
      "application/zip": "zip",
      "application/x-zip-compressed": "zip",
      "application/json": "json",
    };
    return mapping[normalized] || "bin";
  }

  _isSupportedArchiveMime(type, filename = "") {
    const normalized = cleanText(type).toLowerCase();
    const extension = cleanText(filename).toLowerCase().match(/\.[a-z0-9]+$/)?.[0] || "";
    return normalized === "application/pdf"
      || normalized === "application/json"
      || normalized === "application/zip"
      || normalized === "application/x-zip-compressed"
      || normalized === "application/gpx+xml"
      || normalized === "application/xml"
      || normalized === "text/xml"
      || normalized === "text/calendar"
      || normalized.startsWith("image/")
      || normalized.startsWith("text/")
      || [".md", ".markdown", ".txt", ".json", ".csv", ".gpx", ".ics", ".ical", ".zip"].includes(extension);
  }

  _supportedArchiveFile(files) {
    return (files || []).find((file) => file instanceof File && this._isSupportedArchiveMime(file.type || "", file.name || "")) || null;
  }

  _clipboardFileFromData(data) {
    if (!data) return null;
    const direct = this._supportedArchiveFile(Array.from(data.files || []));
    if (direct) return direct;
    for (const item of Array.from(data.items || [])) {
      if (item.kind !== "file" || !this._isSupportedArchiveMime(item.type)) continue;
      const file = item.getAsFile?.();
      if (file) return file;
    }
    return null;
  }

  _friendlyClipboardError(error) {
    const text = this._errorMessage(error);
    const lower = text.toLowerCase();
    if (lower.includes("not allowed") || lower.includes("denied") || lower.includes("permission")) {
      return "Der Browser erlaubt in dieser Ansicht keinen direkten Zwischenablagezugriff. Nutze Strg+V bzw. ⌘V im Feld oder wähle die PDF über die Dateiauswahl aus.";
    }
    if (lower.includes("not supported") || lower.includes("clipboard")) {
      return "Die Zwischenablage stellt die Datei hier nicht direkt bereit. Du kannst sie stattdessen über die Dateiauswahl hochladen.";
    }
    return text;
  }

  async _pasteArchiveFromClipboard() {
    if (!this._canEdit()) return;
    try {
      if (navigator.clipboard?.read) {
        const items = await navigator.clipboard.read();
        for (const item of items) {
          const documentType = item.types.find((type) => this._isSupportedArchiveMime(type) && type !== "text/plain");
          if (documentType) {
            const blob = await item.getType(documentType);
            const extension = this._archiveExtensionForMime(documentType);
            const file = new File([blob], `Zwischenablage-${Date.now()}.${extension}`, { type: documentType });
            await this._uploadArchiveFile(file, {
              source: "clipboard",
              keepOriginal: true,
              links: this._archiveLinks(),
            });
            return;
          }
          if (item.types.includes("text/plain")) {
            const blob = await item.getType("text/plain");
            const text = await blob.text();
            if (cleanText(text)) {
              const file = new File([text], `Zwischenablage-${Date.now()}.txt`, { type: "text/plain" });
              await this._uploadArchiveFile(file, {
                source: "clipboard",
                keepOriginal: true,
                links: this._archiveLinks(),
              });
              return;
            }
          }
        }
      }
      if (navigator.clipboard?.readText) {
        const text = await navigator.clipboard.readText();
        if (cleanText(text)) {
          const file = new File([text], `Zwischenablage-${Date.now()}.txt`, { type: "text/plain" });
          await this._uploadArchiveFile(file, {
            source: "clipboard",
            keepOriginal: true,
            links: this._archiveLinks(),
          });
          return;
        }
      }
      throw new Error("Keine unterstützte PDF-, Bild- oder Textdatei in der Zwischenablage gefunden.");
    } catch (error) {
      this._dialog = { type: "archive-paste-text", error: this._friendlyClipboardError(error) };
      this._render({ preserveScroll: true });
    }
  }

  async _analyzeArchiveDocument(documentId) {
    const result = await this._runAction("archive_analyze_document", {
      trip_id: this._selectedTripId,
      document_id: documentId,
    }, "Dokument analysiert");
    if (!result?.document) return;
    this._dialog = {
      type: "archive-document-review",
      document: result.document,
      analysis: result.analysis || result.document.analysis || {},
    };
    this._render({ preserveScroll: true });
  }

  async _openArchiveDocument(documentId, { download = false } = {}) {
    const cached = await this._archiveCacheGet(documentId).catch(() => null);
    if (cached?.blob && !download) {
      const url = URL.createObjectURL(cached.blob);
      window.open(url, "_blank", "noopener,noreferrer");
      window.setTimeout(() => URL.revokeObjectURL(url), 120000);
      return;
    }
    const ticket = await this._runAction("archive_create_download_ticket", {
      trip_id: this._selectedTripId,
      document_id: documentId,
    }, "");
    if (!ticket?.download_url) return;
    const link = document.createElement("a");
    link.href = ticket.download_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    if (download) link.download = "";
    link.click();
  }

  async _cacheArchiveDocument(documentId) {
    const ticket = await this._runAction("archive_create_download_ticket", {
      trip_id: this._selectedTripId,
      document_id: documentId,
    }, "");
    if (!ticket?.download_url) return;
    this._setBusy(true);
    try {
      const response = await fetch(ticket.download_url, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error(`Dokument konnte nicht geladen werden (HTTP ${response.status})`);
      const blob = await response.blob();
      const item = this._archiveDocument(documentId) || {};
      await this._archiveCachePut(documentId, blob, item.original_filename || item.title || "document");
      this._offlineDocumentIds.add(documentId);
      this._showToast("Dokument auf diesem Gerät gespeichert", "success", 4500);
      this._render({ preserveScroll: true });
    } catch (error) {
      this._showToast(this._errorMessage(error), "error", 7500);
    } finally {
      this._setBusy(false);
    }
  }

  async _removeCachedDocument(documentId) {
    await this._archiveCacheDelete(documentId).catch(() => undefined);
    this._offlineDocumentIds.delete(documentId);
    this._showToast("Lokale Gerätekopie entfernt", "success", 3500);
    this._render({ preserveScroll: true });
  }

  _archiveDb() {
    if (this._archiveDbPromise) return this._archiveDbPromise;
    this._archiveDbPromise = new Promise((resolve, reject) => {
      if (!globalThis.indexedDB) {
        reject(new Error("Dieser Browser unterstützt keinen lokalen Dokumentcache."));
        return;
      }
      const request = indexedDB.open("roadplanner-documents-v1", 1);
      request.onerror = () => reject(request.error || new Error("Lokaler Dokumentcache konnte nicht geöffnet werden."));
      request.onupgradeneeded = () => {
        const db = request.result;
        if (!db.objectStoreNames.contains("files")) db.createObjectStore("files", { keyPath: "key" });
      };
      request.onsuccess = () => resolve(request.result);
    });
    return this._archiveDbPromise;
  }

  _archiveCacheKey(documentId) {
    return `${this._selectedTripId || "trip"}:${documentId}`;
  }

  async _archiveCachePut(documentId, blob, filename) {
    const db = await this._archiveDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("files", "readwrite");
      tx.objectStore("files").put({
        key: this._archiveCacheKey(documentId),
        documentId,
        tripId: this._selectedTripId,
        filename,
        blob,
        storedAt: new Date().toISOString(),
      });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Dokument konnte nicht lokal gespeichert werden."));
    });
  }

  async _archiveCacheGet(documentId) {
    const db = await this._archiveDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("files", "readonly");
      const request = tx.objectStore("files").get(this._archiveCacheKey(documentId));
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error || new Error("Lokale Dokumentkopie konnte nicht gelesen werden."));
    });
  }

  async _archiveCacheDelete(documentId) {
    const db = await this._archiveDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("files", "readwrite");
      tx.objectStore("files").delete(this._archiveCacheKey(documentId));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Lokale Dokumentkopie konnte nicht gelöscht werden."));
    });
  }

  async _refreshOfflineDocumentIds() {
    const documents = this._archiveData().documents || [];
    const ids = new Set();
    for (const documentItem of documents) {
      try {
        if (await this._archiveCacheGet(documentItem.id)) ids.add(documentItem.id);
      } catch (_error) {
        break;
      }
    }
    const changed = ids.size !== this._offlineDocumentIds.size || [...ids].some((id) => !this._offlineDocumentIds.has(id));
    this._offlineDocumentIds = ids;
    if (changed && this._activeTab === "archive") this._render({ preserveScroll: true });
  }

  _formatMoney(amount, currency = "EUR") {
    const numeric = Number(amount);
    if (!Number.isFinite(numeric)) return "—";
    try {
      return new Intl.NumberFormat(this._hass?.locale?.language || "de-DE", {
        style: "currency",
        currency: cleanText(currency).toUpperCase() || "EUR",
      }).format(numeric);
    } catch (_error) {
      return `${numeric.toFixed(2)} ${cleanText(currency).toUpperCase() || "EUR"}`;
    }
  }

  _handleChange(event) {
    const fileInput = event.target.closest("input[data-archive-file-input]");
    if (fileInput) {
      void this._handleArchiveFileInput(fileInput);
      return;
    }
    const select = event.target.closest("select[data-action]");
    if (!select) return;
    if (select.dataset.action === "select-trip") {
      this._selectedTripId = select.value;
      this._selectedDayId = null;
      this._loadData({ force: true });
    } else if (select.dataset.action === "select-day") {
      this._selectedDayId = select.value;
      this._render({ preserveScroll: true });
    }
  }

  _handleClick(event) {
    const target = event.target.closest("[data-action], [data-tab]");
    if (!target) {
      if (event.target.classList?.contains("modal-backdrop")) this._closeDialog();
      return;
    }

    if (target.dataset.tab) {
      this._activeTab = target.dataset.tab;
      this._render();
      if (this._activeTab === "assistant") this._maybeStartAutoBriefing();
      return;
    }

    const action = target.dataset.action;
    const dayId = target.dataset.dayId;
    const stopId = target.dataset.stopId;
    const tripId = target.dataset.tripId;
    const handoffId = target.dataset.handoffId;

    if (action === "assistant-send") {
      event.preventDefault();
      void this._submitAssistantComposer(target.closest("form"));
    } else if (action === "decision-from-message") {
      void this._createDecisionFromMessage(target.dataset.messageId);
    } else if (action === "copy-action-error") {
      void this._copyActionError();
    } else if (action === "retry-action-error") {
      const retry = this._actionErrorRetry;
      this._closeDialog({ flushRefresh: false });
      if (retry) void retry();
    } else if (["decision-prev", "decision-next", "decision-go"].includes(action)) {
      const decision = (this._experienceData().decisions || []).find((item) => item.id === target.dataset.decisionId);
      const options = decision?.options || [];
      if (!options.length) return;
      let index = Number(this._decisionSlideIndexes.get(decision.id) || 0);
      if (action === "decision-prev") index = (index - 1 + options.length) % options.length;
      else if (action === "decision-next") index = (index + 1) % options.length;
      else index = Math.max(0, Math.min(options.length - 1, Number(target.dataset.optionIndex || 0)));
      this._decisionSlideIndexes.set(decision.id, index);
      this._render({ preserveScroll: true });
    } else if (action === "decision-select") {
      void this._runAction("decision_select_option", {
        trip_id: this._selectedTripId,
        decision_id: target.dataset.decisionId,
        option_id: target.dataset.optionId,
      }, "Option ausgewählt");
    } else if (action === "decision-transfer") {
      void (async () => {
        const result = await this._runAction("decision_transfer", {
          trip_id: this._selectedTripId,
          decision_id: target.dataset.decisionId,
        }, "Auswahl in den Änderungskorb übernommen");
        if (result) this._activeTab = "assistant";
      })();
    } else if (action === "decision-archive") {
      this._confirm("Entscheidung archivieren?", "Die Vorlage bleibt gespeichert, wird aber aus der offenen Übersicht entfernt.", "Archivieren", () => this._runAction("decision_archive", { trip_id: this._selectedTripId, decision_id: target.dataset.decisionId }, "Entscheidung archiviert"));
    } else if (action === "universal-import-upload") {
      this._startArchiveFileSelection({ source: "universal_import" });
    } else if (action === "universal-import-open") {
      this._openUniversalImport(target.dataset.documentId);
    } else if (action === "universal-import-analyze") {
      void this._analyzeUniversalImport(target.dataset.documentId);
    } else if (action === "universal-import-transfer") {
      void (async () => {
        const result = await this._runAction("universal_import_transfer", {
          trip_id: this._selectedTripId,
          document_id: target.dataset.documentId,
        }, "Import zur Prüfung übergeben");
        if (!result) return;
        this._closeDialog({ flushRefresh: false });
        this._activeTab = result.mode === "review" ? "handoffs" : "assistant";
        await this._loadData({ silent: true, force: true });
      })();
    } else if (action === "universal-import-discuss") {
      void (async () => {
        const result = await this._runAction("universal_import_discuss", {
          trip_id: this._selectedTripId,
          document_id: target.dataset.documentId,
        }, "Übergabe dem Reisegespräch hinzugefügt");
        if (!result) return;
        this._closeDialog({ flushRefresh: false });
        this._activeTab = "assistant";
        await this._loadData({ silent: true, force: true });
      })();
    } else if (action === "universal-import-discard") {
      this._confirm("Import verwerfen?", "Die Datei bleibt im privaten Dokumentenarchiv. Nur die Importvorschau wird als verworfen markiert.", "Verwerfen", async () => {
        await this._runAction("universal_import_discard", { trip_id: this._selectedTripId, document_id: target.dataset.documentId }, "Import verworfen");
        this._closeDialog({ flushRefresh: false });
      }, true);
    } else if (action === "attachment-import") {
      const documentId = target.dataset.documentId;
      this._closeDialog({ flushRefresh: false });
      void this._analyzeUniversalImport(documentId);
    } else if (action === "attachment-document") {
      const documentId = target.dataset.documentId;
      this._closeDialog({ flushRefresh: false });
      void this._analyzeArchiveDocument(documentId);
    } else if (action === "onedrive-setup") {
      this._dialog = { type: "onedrive-setup" };
      this._render({ preserveScroll: true });
    } else if (action === "onedrive-connect") {
      if (!this._experienceData().onedrive?.configured) {
        this._dialog = { type: "onedrive-setup" };
        this._render({ preserveScroll: true });
        return;
      }
      void (async () => {
        const result = await this._runAction("onedrive_start_auth", {}, "");
        if (!result) return;
        this._onedriveAuth = result;
        this._dialog = { type: "onedrive-auth", auth: result };
        this._render({ preserveScroll: true });
      })();
    } else if (action === "onedrive-poll") {
      void (async () => {
        const result = await this._runAction("onedrive_poll_auth", {}, "");
        if (!result) return;
        if (result.status === "connected" || result.connected) {
          this._closeDialog({ flushRefresh: false });
          this._showToast("OneDrive Personal verbunden", "success", 5000);
          await this._loadData({ silent: true, force: true });
        } else {
          this._onedriveAuth = { ...(this._onedriveAuth || {}), ...result };
          this._dialog = { type: "onedrive-auth", auth: this._onedriveAuth };
          this._render({ preserveScroll: true });
        }
      })();
    } else if (action === "onedrive-disconnect") {
      this._confirm("OneDrive trennen?", "Die lokale Fotozuordnung bleibt erhalten, neue Bilder werden aber nicht mehr synchronisiert.", "Trennen", () => this._runAction("onedrive_disconnect", {}, "OneDrive getrennt"), true);
    } else if (action === "onedrive-sync" || action === "onedrive-full-sync") {
      void this._runAction("onedrive_sync", {
        trip_id: this._selectedTripId,
        full_rescan: action === "onedrive-full-sync",
      }, action === "onedrive-full-sync" ? "OneDrive-Fotos werden ab Reisebeginn neu eingelesen" : "OneDrive-Fotos synchronisiert");
    } else if (action === "media-open-album") {
      const dayId = target.dataset.dayId || "";
      const stopId = target.dataset.stopId || "";
      const media = stopId ? this._experienceMediaForStop(stopId) : this._experienceMediaForDay(dayId);
      const requestedId = target.dataset.mediaId || "";
      const index = Math.max(0, media.findIndex((item) => item.id === requestedId));
      if (media.length) {
        this._dialog = { type: "media-gallery", media, index };
        this._render({ preserveScroll: true });
      }
    } else if (action === "media-open") {
      const media = this._experienceData().media || [];
      const index = Math.max(0, Math.min(media.length - 1, Number(target.dataset.mediaIndex || 0)));
      if (media.length) {
        this._dialog = { type: "media-gallery", media, index };
        this._render({ preserveScroll: true });
      }
    } else if (action === "media-gallery-prev" || action === "media-gallery-next") {
      const media = this._dialog?.media || [];
      if (!media.length) return;
      const delta = action.endsWith("prev") ? -1 : 1;
      this._dialog.index = (Number(this._dialog.index || 0) + delta + media.length) % media.length;
      this._render({ preserveScroll: true });
    } else if (action === "media-edit") {
      const item = (this._experienceData().media || []).find((entry) => entry.id === target.dataset.mediaId);
      if (item) {
        this._dialog = { type: "media-edit", media: item };
        this._render({ preserveScroll: true });
      }
    } else if (action === "media-cover") {
      void this._runAction("media_update_assignment", {
        trip_id: this._selectedTripId,
        media_id: target.dataset.mediaId,
        patch: { is_cover: true },
      }, "Titelbild gesetzt");
    } else if (action === "media-delete") {
      this._confirm("Foto aus Roadplanner entfernen?", "Das Original in OneDrive wird nicht gelöscht. Nur die Roadplanner-Zuordnung wird entfernt.", "Entfernen", () => this._runAction("media_delete", { trip_id: this._selectedTripId, media_id: target.dataset.mediaId }, "Fotozuordnung entfernt"), true);
    } else if (action === "archive-upload") {
      this._startArchiveFileSelection({ source: "panel_upload" });
    } else if (action === "archive-camera") {
      this._startArchiveFileSelection({ source: "camera", camera: true });
    } else if (action === "archive-clipboard") {
      void this._pasteArchiveFromClipboard();
    } else if (action === "archive-paste-file") {
      this._closeDialog({ flushRefresh: false });
      this._startArchiveFileSelection({ source: "clipboard_fallback" });
    } else if (action === "archive-assistant-attach") {
      this._startArchiveFileSelection({ source: "assistant" });
    } else if (action === "archive-day-attach") {
      this._startArchiveFileSelection({ source: "day", dayId });
    } else if (action === "archive-stop-attach") {
      this._startArchiveFileSelection({ source: "stop", dayId, stopId });
    } else if (action === "archive-analyze") {
      void this._analyzeArchiveDocument(target.dataset.documentId);
    } else if (action === "archive-review") {
      const documentItem = this._archiveDocument(target.dataset.documentId);
      if (documentItem) {
        this._dialog = { type: "archive-document-review", document: documentItem, analysis: documentItem.analysis || {} };
        this._render({ preserveScroll: true });
      }
    } else if (action === "archive-open") {
      void this._openArchiveDocument(target.dataset.documentId);
    } else if (action === "archive-download") {
      void this._openArchiveDocument(target.dataset.documentId, { download: true });
    } else if (action === "archive-cache") {
      void this._cacheArchiveDocument(target.dataset.documentId);
    } else if (action === "archive-uncache") {
      void this._removeCachedDocument(target.dataset.documentId);
    } else if (action === "archive-edit-document") {
      const documentItem = this._archiveDocument(target.dataset.documentId);
      if (documentItem) {
        this._dialog = { type: "archive-document-edit", document: documentItem };
        this._render({ preserveScroll: true });
      }
    } else if (action === "archive-delete-document") {
      const documentItem = this._archiveDocument(target.dataset.documentId);
      this._confirm(
        "Dokument löschen?",
        `${documentItem?.title || "Dieses Dokument"} wird aus dem privaten Roadplanner-Archiv entfernt.`,
        "Dokument löschen",
        async () => {
          await this._runAction("archive_delete_document", {
            trip_id: this._selectedTripId,
            document_id: target.dataset.documentId,
            delete_linked_records: false,
          }, "Dokument gelöscht");
          await this._removeCachedDocument(target.dataset.documentId);
        },
        true,
      );
    } else if (action === "archive-add-expense") {
      this._dialog = { type: "archive-expense", mode: "add", expense: null, dayId: dayId || "", stopId: stopId || "" };
      this._render({ preserveScroll: true });
    } else if (action === "archive-edit-expense") {
      const expense = this._archiveExpense(target.dataset.expenseId);
      if (expense) {
        this._dialog = { type: "archive-expense", mode: "edit", expense };
        this._render({ preserveScroll: true });
      }
    } else if (action === "archive-delete-expense") {
      const expense = this._archiveExpense(target.dataset.expenseId);
      this._confirm(
        "Ausgabe löschen?",
        `${expense ? this._formatMoney(expense.amount, expense.currency) : "Diese Ausgabe"} wird aus dem Kostenbuch entfernt.`,
        "Ausgabe löschen",
        () => this._runAction("archive_delete_expense", { trip_id: this._selectedTripId, expense_id: target.dataset.expenseId }, "Ausgabe gelöscht"),
        true,
      );
    } else if (action === "archive-add-todo") {
      this._dialog = { type: "archive-todo", mode: "add", todo: null, dayId: dayId || "", stopId: stopId || "" };
      this._render({ preserveScroll: true });
    } else if (action === "archive-edit-todo") {
      const todo = this._archiveTodo(target.dataset.todoId);
      if (todo) {
        this._dialog = { type: "archive-todo", mode: "edit", todo };
        this._render({ preserveScroll: true });
      }
    } else if (action === "archive-toggle-todo") {
      const todo = this._archiveTodo(target.dataset.todoId);
      if (todo) void this._runAction("archive_update_todo", {
        trip_id: this._selectedTripId,
        todo_id: todo.id,
        patch: { status: todo.status === "done" ? "open" : "done" },
      }, todo.status === "done" ? "Aufgabe wieder geöffnet" : "Aufgabe erledigt");
    } else if (action === "archive-delete-todo") {
      const todo = this._archiveTodo(target.dataset.todoId);
      this._confirm(
        "Aufgabe löschen?",
        todo?.title || "Diese Aufgabe wird gelöscht.",
        "Aufgabe löschen",
        () => this._runAction("archive_delete_todo", { trip_id: this._selectedTripId, todo_id: target.dataset.todoId }, "Aufgabe gelöscht"),
        true,
      );
    } else if (action === "open-menu") {
      this.dispatchEvent(new Event("hass-toggle-menu", {
        bubbles: true,
        composed: true,
      }));
    } else if (action === "refresh") {
      this._runAction("refresh", {}, "Roadplanner neu geladen");
    } else if (action === "close-dialog") {
      this._closeDialog();
    } else if (action === "confirm-dialog") {
      const callback = this._dialog?.callback;
      this._closeDialog({ flushRefresh: false });
      if (callback) callback();
    } else if (action === "view-trip") {
      this._selectedTripId = tripId;
      this._selectedDayId = null;
      this._activeTab = "overview";
      this._loadData({ force: true });
    } else if (action === "activate-trip" && this._canActivate()) {
      const trip = this._data?.trips?.trips?.find((item) => item.id === tripId);
      const expectedActiveTrip = this._data?.active_trip_id;
      this._confirm(
        "Aktive Reise wechseln?",
        `${trip?.title || tripId} wird zur aktiven Reise. Sensoren, Gemini-Werkzeuge und neue Übergaben beziehen sich danach auf diese Reise.`,
        "Aktivieren",
        async () => {
          const result = await this._runAction("set_active_trip", {
            trip_id: tripId,
            expected_active_trip: expectedActiveTrip,
          }, "Aktive Reise gewechselt");
          if (result) {
            this._selectedTripId = tripId;
            await this._loadData({ force: true });
          }
        },
      );
    } else if (action === "select-day-card") {
      this._selectedDayId = dayId;
      this._activeTab = "day-route";
      this._render();
    } else if (action === "edit-trip" && this._canEdit()) {
      this._dialog = {
        type: "trip",
        trip: this._data.summary.trip,
        revision: this._currentRevision(),
      };
      this._render({ preserveScroll: true });
    } else if (action === "add-day" && this._canEdit()) {
      this._dialog = {
        type: "day",
        mode: "add",
        day: null,
        revision: this._currentRevision(),
      };
      this._render({ preserveScroll: true });
    } else if (action === "toggle-day") {
      if (this._expandedDays.has(dayId)) this._expandedDays.delete(dayId);
      else this._expandedDays.add(dayId);
      this._render({ preserveScroll: true });
    } else if (action === "edit-day" && this._canEdit()) {
      this._dialog = {
        type: "day",
        mode: "edit",
        day: this._findDay(dayId),
        revision: this._currentRevision(),
      };
      this._render({ preserveScroll: true });
    } else if (action === "delete-day" && this._canEdit()) {
      const day = this._findDay(dayId);
      const expectedRevision = this._currentRevision();
      this._confirm(
        "Reisetag löschen?",
        `${day?.title || "Dieser Reisetag"} und ${day?.stop_count || 0} Stopps werden entfernt.`,
        "Tag löschen",
        async () => {
          await this._runAction("remove_day", {
            day_id: dayId,
            expected_revision: expectedRevision,
            remove_stops: true,
          }, "Reisetag gelöscht");
        },
        true,
      );
    } else if ((action === "move-day-up" || action === "move-day-down") && this._canEdit()) {
      const day = this._findDay(dayId);
      const delta = action.endsWith("up") ? -1 : 1;
      const position = Math.max(1, Math.min(this._data.days.total, day.sequence + delta));
      this._runAction("update_day", {
        day_id: dayId,
        patch: {},
        position,
        expected_revision: this._currentRevision(),
      }, "Reihenfolge geändert");
    } else if (action === "add-stop" && this._canEdit()) {
      this._dialog = {
        type: "stop",
        mode: "add",
        dayId,
        stop: null,
        revision: this._currentRevision(),
      };
      this._render({ preserveScroll: true });
    } else if (action === "edit-stop" && this._canEdit()) {
      this._dialog = {
        type: "stop",
        mode: "edit",
        dayId,
        stop: this._findStop(dayId, stopId),
        revision: this._currentRevision(),
      };
      this._render({ preserveScroll: true });
    } else if (action === "delete-stop" && this._canEdit()) {
      const stop = this._findStop(dayId, stopId);
      const expectedRevision = this._currentRevision();
      this._confirm(
        "Stopp löschen?",
        `${stop?.name || "Dieser Stopp"} wird dauerhaft entfernt.`,
        "Stopp löschen",
        async () => {
          await this._runAction("remove_stop", {
            day_id: dayId,
            stop_id: stopId,
            expected_revision: expectedRevision,
          }, "Stopp gelöscht");
        },
        true,
      );
    } else if ((action === "move-stop-up" || action === "move-stop-down") && this._canEdit()) {
      const day = this._findDay(dayId);
      const index = day?.stops?.findIndex((stop) => stop.id === stopId) ?? -1;
      if (index < 0) return;
      const delta = action.endsWith("up") ? -1 : 1;
      const position = Math.max(1, Math.min(day.stops.length, index + 1 + delta));
      this._runAction("update_stop", {
        day_id: dayId,
        stop_id: stopId,
        patch: {},
        position,
        expected_revision: this._currentRevision(),
      }, "Stopp verschoben");
    } else if (action === "calculate-day-route" && this._canEdit()) {
      void this._calculateDayRoute(dayId, target.dataset.force === "true");
    } else if (action === "calculate-trip-routes" && this._canEdit()) {
      void this._calculateTripRoutes(target.dataset.force === "true");
    } else if (action === "search-stop-images" && this._canEdit()) {
      const stop = this._findStop(dayId, stopId);
      const day = this._findDay(dayId);
      const city = cleanText(stop?.location?.city);
      this._searchImages({
        targetType: "stop",
        dayId,
        stopId,
        query: [stop?.name, city, day?.end].filter(Boolean).join(" "),
      });
    } else if (action === "search-day-images" && this._canEdit()) {
      const day = this._findDay(dayId);
      this._searchImages({
        targetType: "day",
        dayId,
        query: [day?.title, day?.end].filter(Boolean).join(" "),
      });
    } else if (action === "choose-image") {
      this._chooseImage(Number(target.dataset.imageIndex));
    } else if (action === "remove-stop-image" && this._canEdit()) {
      this._removeImage({ targetType: "stop", dayId, stopId });
    } else if (action === "remove-day-image" && this._canEdit()) {
      this._removeImage({ targetType: "day", dayId });
    } else if (action === "scan-handoffs" && this._data?.capabilities?.can_approve) {
      this._runAction("scan_handoffs", {}, "Übergabeordner geprüft");
    } else if (action === "preview-handoff") {
      this._previewHandoff(handoffId);
    } else if (
      action === "apply-handoff"
      && this._canApprove()
      && this._data?.selected_is_active
    ) {
      const handoff = this._data.handoffs.handoffs.find((item) => item.id === handoffId);
      const expectedRevision = this._currentRevision();
      const warning = handoff?.destructive
        ? "Die Übergabe enthält Löschungen. Bitte prüfe die Vorschau besonders sorgfältig."
        : "Alle enthaltenen Änderungen werden als eine neue Revision übernommen.";
      this._confirm(
        "Übergabe übernehmen?",
        warning,
        "Übernehmen",
        async () => {
          await this._runAction("apply_handoff", {
            handoff_id: handoffId,
            expected_revision: expectedRevision,
            confirm_destructive: Boolean(handoff?.destructive),
          }, "Übergabe übernommen");
        },
        Boolean(handoff?.destructive),
      );
    } else if (action === "archive-handoff" && this._canApprove()) {
      this._confirm(
        "Übergabe ablehnen?",
        "Die Übergabe wird archiviert und verändert die Reise nicht.",
        "Ablehnen",
        async () => {
          await this._runAction("archive_handoff", {
            handoff_id: handoffId,
            resolution: "rejected",
            note: "Über das Roadplanner-Panel abgelehnt",
          }, "Übergabe archiviert");
        },
        true,
      );
    } else if (action === "assistant-quick" && this._data?.capabilities?.can_assistant) {
      this._sendAssistantMessage(target.dataset.prompt || "");
    } else if (action === "assistant-clear" && this._data?.capabilities?.can_assistant) {
      this._confirm(
        "Unterhaltung neu beginnen?",
        "Chat und vorgemerkte Änderungen dieser Reise werden aus dem flüchtigen Sitzungsspeicher entfernt. Das Roadbook bleibt unverändert.",
        "Neu beginnen",
        async () => {
          await this._runAction("assistant_clear", {
            trip_id: this._selectedTripId,
          }, "Neue Unterhaltung gestartet");
        },
        true,
      );
    } else if (action === "assistant-edit-draft" && this._data?.capabilities?.can_assistant) {
      const draft = (this._data?.assistant?.basket || []).find((item) => item.id === target.dataset.draftId);
      if (draft) {
        this._dialog = { type: "assistant-draft", draft };
        this._render({ preserveScroll: true });
      }
    } else if (action === "assistant-remove-draft" && this._data?.capabilities?.can_assistant) {
      this._runAction("assistant_remove_draft", {
        trip_id: this._selectedTripId,
        draft_id: target.dataset.draftId,
      }, "Vormerkung entfernt");
    } else if (action === "assistant-prepare" && this._data?.capabilities?.can_assistant) {
      this._prepareAssistantChanges();
    } else if (action === "assistant-test" && this._data?.capabilities?.can_assistant) {
      this._testAssistantConnection();
    } else if (action === "assistant-briefing" && this._data?.capabilities?.can_assistant) {
      this._requestAssistantBriefing();
    } else if (action === "assistant-retry" && this._data?.capabilities?.can_assistant) {
      if (this._assistantLastFailedText) {
        this._sendAssistantMessage(this._assistantLastFailedText, {
          requestId: this._assistantLastFailedRequestId || newClientRequestId(),
        });
      }
    } else if (action === "assistant-debug" && this._canAdmin()) {
      this._loadAssistantDiagnostics();
    } else if (action === "backup" && this._canAdmin()) {
      this._runAction("create_backup", { reason: "panel-manual" }, "Sicherung erstellt");
    }
  }

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
  }

  _setAssistantSubmitState(sending) {
    this._assistantSubmitInFlight = Boolean(sending);
    const form = this.shadowRoot.querySelector("form[data-form='assistant-chat']");
    const button = form?.querySelector("[data-action='assistant-send']");
    const label = button?.querySelector("span");
    const textarea = form?.querySelector("textarea[name='message']");
    if (button) {
      button.disabled = this._assistantSubmitInFlight;
      button.setAttribute("aria-busy", this._assistantSubmitInFlight ? "true" : "false");
    }
    if (label) label.textContent = this._assistantSubmitInFlight ? "Wird gesendet …" : "Senden";
    if (textarea) textarea.readOnly = this._assistantSubmitInFlight;
  }

  async _waitForAssistantIdle(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (this._busy && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return !this._busy;
  }

  async _submitAssistantComposer(form) {
    if (this._assistantSubmitInFlight) return false;
    const activeForm = form || this.shadowRoot.querySelector("form[data-form='assistant-chat']");
    const textarea = activeForm?.querySelector("textarea[name='message']");
    const text = cleanText(textarea?.value || "");
    if (!text) {
      this._showToast("Bitte eine Nachricht eingeben", "error");
      textarea?.focus();
      return false;
    }

    const requestId = cleanText(textarea?.dataset?.requestId) || newClientRequestId();
    if (textarea) textarea.dataset.requestId = requestId;
    this._setAssistantSubmitState(true);
    try {
      const idle = await this._waitForAssistantIdle();
      if (!idle) {
        this._showToast("Roadplanner verarbeitet noch eine andere Aktion. Bitte kurz erneut versuchen.", "error", 5000);
        return false;
      }
      const success = await this._sendAssistantMessage(text, { requestId });
      if (success) {
        const current = this.shadowRoot.querySelector("form[data-form='assistant-chat'] textarea[name='message']");
        if (current) {
          current.value = "";
          delete current.dataset.requestId;
        }
      }
      return success;
    } finally {
      this._setAssistantSubmitState(false);
      const current = this.shadowRoot.querySelector("form[data-form='assistant-chat'] textarea[name='message']");
      current?.focus();
    }
  }

  async _sendAssistantMessage(text, { requestId = "" } = {}) {
    const message = cleanText(text);
    if (!message || !this._selectedTripId) return false;
    const clientRequestId = cleanText(requestId) || newClientRequestId();
    this._assistantPending = {
      id: clientRequestId,
      text: message,
      created_at: new Date().toISOString(),
    };
    this._render({ preserveScroll: true });
    const retry = () => this._sendAssistantMessage(message, { requestId: clientRequestId });
    const result = await this._runAction("assistant_chat", {
      trip_id: this._selectedTripId,
      text: message,
      client_request_id: clientRequestId,
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "Assistent konnte nicht antworten",
      retry,
    });
    this._assistantPending = null;
    if (!result) {
      this._assistantLastFailedText = message;
      this._assistantLastFailedRequestId = clientRequestId;
      this._render({ preserveScroll: true });
      return false;
    }

    this._assistantLastFailedText = "";
    this._assistantLastFailedRequestId = "";
    if (result.assistant && this._data) {
      this._data = { ...this._data, assistant: result.assistant };
      this._signature = "";
      this._render({ preserveScroll: true });
    }

    const outcome = result?.basket_outcome || {};
    const changed = Number(outcome.actual_change_count || 0);
    if (result?.deduplicated) {
      this._showToast("Die bereits verarbeitete Antwort wurde wiederhergestellt", "success", 4500);
    } else if (result?.basket_warning) {
      this._showToast(result.basket_warning, "error", 7500);
    } else if (changed > 0) {
      this._showToast(`${changed} ${changed === 1 ? "Änderung" : "Änderungen"} vorgemerkt`, "success", 4500);
    } else {
      this._showToast("Antwort geladen · keine Änderung vorgemerkt", "success", 3500);
    }
    return true;
  }

  async _testAssistantConnection() {
    const result = await this._runAction("assistant_test", {
      trip_id: this._selectedTripId,
    }, "Gemini-Verbindung geprüft", { refresh: false, errorMode: "dialog", errorTitle: "Gemini-Verbindungstest fehlgeschlagen" });
    if (result) {
      this._showToast(result.ok ? "Gemini antwortet zuverlässig" : "Unerwartete Testantwort", result.ok ? "success" : "error", 5000);
    }
  }

  async _requestAssistantBriefing({ automatic = false } = {}) {
    if (!this._selectedTripId) return null;
    const result = await this._runAction("assistant_briefing", {
      trip_id: this._selectedTripId,
    }, automatic ? "Tagesbriefing geladen" : "Copilot-Briefing geladen", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "Tagesbriefing konnte nicht erstellt werden",
    });
    if (result?.assistant && this._data) {
      this._data = { ...this._data, assistant: result.assistant };
      this._signature = "";
      this._render({ preserveScroll: true });
    }
    return result;
  }

  _maybeStartAutoBriefing() {
    const assistant = this._data?.assistant || {};
    if (!assistant.briefing_due || !assistant.copilot_auto_briefing || !this._selectedTripId || this._busy) return;
    const key = `${this._selectedTripId}:${new Date().toISOString().slice(0, 10)}`;
    if (this._assistantAutoBriefingRequested.has(key)) return;
    this._assistantAutoBriefingRequested.add(key);
    window.setTimeout(async () => {
      const result = await this._requestAssistantBriefing({ automatic: true });
      if (!result) this._assistantAutoBriefingRequested.delete(key);
    }, 50);
  }

  async _loadAssistantDiagnostics() {
    const result = await this._runAction("assistant_diagnostics", {
      trip_id: this._selectedTripId,
    }, "Assistenten-Diagnose geladen", { refresh: false, errorMode: "dialog", errorTitle: "Assistenten-Diagnose konnte nicht geladen werden" });
    if (!result) return;
    this._assistantDiagnostics = result;
    this._dialog = { type: "assistant-diagnostics", diagnostics: result };
    this._render({ preserveScroll: true });
  }

  async _prepareAssistantChanges() {
    const assistant = this._data?.assistant || {};
    if (!assistant.basket_count) {
      this._showToast("Es sind noch keine Änderungen vorgemerkt", "error");
      return;
    }
    if (!this._data?.selected_is_active) {
      this._showToast("Bitte diese Reise zuerst als aktiv setzen", "error");
      return;
    }
    const result = await this._runAction("assistant_prepare", {
      trip_id: this._selectedTripId,
    }, "Änderungsentwurf erstellt");
    if (result?.handoff) {
      this._activeTab = "handoffs";
      this._render();
    }
  }

  async _previewHandoff(handoffId) {
    if (this._busy) return;
    this._setBusy(true);
    try {
      const result = await this._send({
        type: WS_ACTION,
        action: "preview_handoff",
        data: {
          handoff_id: handoffId,
          expected_trip_id: this._selectedTripId,
        },
      });
      const handoff = this._data.handoffs.handoffs.find((item) => item.id === handoffId);
      this._dialog = { type: "handoff-preview", handoff, preview: result.preview };
      this._render({ preserveScroll: true });
    } catch (error) {
      this._showToast(this._errorMessage(error), "error", 6500);
    } finally {
      this._setBusy(false);
    }
  }

  async _searchImages(context) {
    if (this._busy) return;
    this._setBusy(true);
    try {
      const result = await this._send({
        type: WS_ACTION,
        action: "search_destination_images",
        data: { query: context.query, limit: 8 },
      });
      this._dialog = {
        type: "image-search",
        context,
        query: result.query,
        results: result.results || [],
      };
      this._render({ preserveScroll: true });
    } catch (error) {
      this._showToast(this._errorMessage(error), "error", 6500);
    } finally {
      this._setBusy(false);
    }
  }

  async _chooseImage(index) {
    const dialog = this._dialog;
    const image = dialog?.results?.[index];
    const context = dialog?.context;
    if (!image || !context) return;
    this._closeDialog({ flushRefresh: false });
    const media = {
      image_url: image.image_url,
      alt: image.alt || image.title,
      attribution: image.attribution,
      source_url: image.source_url,
      provider: image.provider,
    };
    if (context.targetType === "day") {
      const day = this._findDay(context.dayId);
      await this._runAction("update_day", {
        day_id: context.dayId,
        patch: { details: this._detailsWithMedia(day, media) },
        expected_revision: this._currentRevision(),
      }, "Titelbild gespeichert");
    } else {
      const stop = this._findStop(context.dayId, context.stopId);
      await this._runAction("update_stop", {
        day_id: context.dayId,
        stop_id: context.stopId,
        patch: { details: this._detailsWithMedia(stop, media) },
        expected_revision: this._currentRevision(),
      }, "Zielbild gespeichert");
    }
  }

  async _removeImage(context) {
    if (context.targetType === "day") {
      const day = this._findDay(context.dayId);
      await this._runAction("update_day", {
        day_id: context.dayId,
        patch: { details: this._detailsWithMedia(day, null) },
        expected_revision: this._currentRevision(),
      }, "Titelbild entfernt");
    } else {
      const stop = this._findStop(context.dayId, context.stopId);
      await this._runAction("update_stop", {
        day_id: context.dayId,
        stop_id: context.stopId,
        patch: { details: this._detailsWithMedia(stop, null) },
        expected_revision: this._currentRevision(),
      }, "Zielbild entfernt");
    }
  }

  async _handleSubmit(event) {
    const form = event.target.closest("form[data-form]");
    if (!form) return;
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form).entries());
    const formType = form.dataset.form;

    if (formType === "assistant-chat") {
      await this._submitAssistantComposer(form);
      return;
    }

    if (formType === "assistant-draft") {
      const valueKeys = [
        "title", "status", "start_date", "end_date", "date", "start", "end",
        "distance_km", "drive_minutes", "notes", "name", "type",
        "arrival_time", "departure_time", "category", "text",
      ];
      const draftValues = {};
      for (const key of valueKeys) {
        const raw = values[`value_${key}`];
        if (raw === undefined || cleanText(raw) === "") continue;
        if (key === "distance_km") {
          const parsed = nullableNumber(raw);
          if (parsed !== null) draftValues[key] = parsed;
        } else if (key === "drive_minutes") {
          const parsed = nullableNumber(raw, true);
          if (parsed !== null) draftValues[key] = parsed;
        } else {
          draftValues[key] = String(raw);
        }
      }
      const rawPosition = cleanText(values.position);
      const patch = {
        summary: cleanText(values.summary),
        reason: cleanText(values.reason),
        target_id: cleanText(values.target_id),
        day_id: cleanText(values.day_id),
        day_date: cleanText(values.day_date),
        place_query: cleanText(values.place_query),
        position: rawPosition ? nullableNumber(rawPosition, true) : null,
        values: draftValues,
      };
      const result = await this._runAction("assistant_update_draft", {
        trip_id: this._selectedTripId,
        draft_id: form.dataset.draftId,
        patch,
      }, "Vormerkung aktualisiert");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    if (formType === "onedrive-setup") {
      const clientId = cleanText(values.client_id);
      if (!clientId && !this._experienceData().onedrive?.configured) {
        this._showToast("Bitte die Microsoft-Anwendungs-ID eintragen.", "error");
        return;
      }
      const result = await this._runAction("onedrive_configure", {
        client_id: clientId,
        folder_path: cleanText(values.folder_path) || "Pictures/Camera Roll",
        sync_interval_minutes: Number.parseInt(values.sync_interval_minutes || "15", 10) || 15,
        auto_sync: Boolean(form.querySelector("input[name='auto_sync']")?.checked),
        auto_assign: Boolean(form.querySelector("input[name='auto_assign']")?.checked),
        recursive_subfolders: Boolean(form.querySelector("input[name='recursive_subfolders']")?.checked),
        date_buffer_days: Number.parseInt(values.date_buffer_days || "3", 10),
        max_items_per_run: Number.parseInt(values.max_items_per_run || "2000", 10) || 2000,
        max_scan_seconds: Number.parseInt(values.max_scan_seconds || "12", 10) || 12,
      }, "OneDrive-Einstellungen gespeichert");
      if (!result) return;
      this._closeDialog({ flushRefresh: false });
      const auth = await this._runAction("onedrive_start_auth", {}, "");
      if (!auth) return;
      this._onedriveAuth = auth;
      this._dialog = { type: "onedrive-auth", auth };
      this._render({ preserveScroll: true });
      return;
    }

    if (formType === "media-edit") {
      const stopParts = cleanText(values.linked_stop_ref).split("::");
      const stopDayId = stopParts.length === 2 ? stopParts[0] : "";
      const stopId = stopParts.length === 2 ? stopParts[1] : "";
      const dayId = stopDayId || cleanText(values.linked_day_id);
      const result = await this._runAction("media_update_assignment", {
        trip_id: this._selectedTripId,
        media_id: form.dataset.mediaId,
        patch: {
          linked_day_id: dayId || null,
          linked_stop_id: stopId || null,
          assignment_status: dayId ? "manual" : "unassigned",
          caption: String(values.caption || ""),
          is_cover: Boolean(form.querySelector("input[name='is_cover']")?.checked),
        },
      }, "Fotozuordnung gespeichert");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    if (formType === "archive-paste-text") {
      const content = String(values.content || "");
      if (!cleanText(content)) {
        this._showToast("Bitte Text aus der Zwischenablage einfügen.", "error");
        return;
      }
      const filename = cleanText(values.filename) || `Zwischenablage-${Date.now()}.txt`;
      const file = new File([content], filename.endsWith(".txt") ? filename : `${filename}.txt`, { type: "text/plain" });
      this._closeDialog({ flushRefresh: false });
      await this._uploadArchiveFile(file, { source: "clipboard", keepOriginal: true, links: this._archiveLinks() });
      return;
    }

    if (formType === "archive-document-review") {
      const stopParts = cleanText(values.link_stop_ref).split("::");
      const stopDayId = stopParts.length === 2 ? stopParts[0] : "";
      const stopId = stopParts.length === 2 ? stopParts[1] : "";
      const dayId = stopDayId || cleanText(values.link_day_id);
      const links = this._archiveLinks(dayId, stopId);
      const lines = (value) => String(value || "").split(/\r?\n/).map((item) => cleanText(item)).filter(Boolean).slice(0, 100);
      const expenseEnabled = Boolean(form.querySelector("input[name='expense_enabled']")?.checked);
      const amount = nullableNumber(values.expense_amount);
      if (expenseEnabled && amount === null) {
        this._showToast("Für die Ausgabe wird ein gültiger Betrag benötigt.", "error");
        return;
      }
      const todos = [];
      const todoCount = Number.parseInt(form.dataset.todoCount || "0", 10) || 0;
      for (let index = 0; index < todoCount; index += 1) {
        if (!form.querySelector(`input[name='todo_${index}_enabled']`)?.checked) continue;
        const title = cleanText(values[`todo_${index}_title`]);
        if (!title) continue;
        todos.push({
          enabled: true,
          title,
          due_at: cleanText(values[`todo_${index}_due_at`]) || null,
          priority: cleanText(values[`todo_${index}_priority`]) || "normal",
          notes: String(values[`todo_${index}_notes`] || ""),
          day_id: dayId || null,
          stop_id: stopId || null,
        });
      }
      const patch = {
        classification: cleanText(values.classification) || "document",
        document_type: cleanText(values.document_type) || "other",
        title: cleanText(values.title),
        provider: cleanText(values.provider),
        summary: String(values.summary || ""),
        links,
        keep_original: Boolean(form.querySelector("input[name='keep_original']")?.checked),
        offline_priority: Boolean(form.querySelector("input[name='offline_priority']")?.checked),
        sensitive: Boolean(form.querySelector("input[name='sensitive']")?.checked),
        extracted: {
          booking_reference: cleanText(values.booking_reference),
          status: cleanText(values.extracted_status),
          start_at: cleanText(values.start_at),
          end_at: cleanText(values.end_at),
          check_in: cleanText(values.check_in),
          check_out: cleanText(values.check_out),
          address: cleanText(values.address),
          required_items: lines(values.required_items),
          important_notes: lines(values.important_notes),
        },
        expense: {
          enabled: expenseEnabled,
          amount: amount ?? 0,
          currency: cleanText(values.expense_currency).toUpperCase() || this._data?.settings?.default_currency || "EUR",
          merchant: cleanText(values.expense_merchant),
          category: cleanText(values.expense_category) || "other",
          date: cleanText(values.expense_date) || null,
          status: cleanText(values.expense_status) || "paid",
          payment_method: cleanText(values.expense_payment_method),
          notes: String(values.expense_notes || ""),
          day_id: dayId || null,
          stop_id: stopId || null,
        },
        todos,
      };
      const shouldCache = patch.offline_priority && patch.keep_original;
      const result = await this._runAction("archive_confirm_document", {
        trip_id: this._selectedTripId,
        document_id: form.dataset.documentId,
        patch,
      }, "Dokument, Kosten und Aufgaben gespeichert");
      if (result) {
        this._closeDialog({ flushRefresh: false });
        if (shouldCache && result.document?.file_retained) await this._cacheArchiveDocument(result.document.id);
      }
      return;
    }

    if (formType === "archive-document-edit") {
      const stopParts = cleanText(values.link_stop_ref).split("::");
      const stopDayId = stopParts.length === 2 ? stopParts[0] : "";
      const stopId = stopParts.length === 2 ? stopParts[1] : "";
      const dayId = stopDayId || cleanText(values.link_day_id);
      const result = await this._runAction("archive_update_document", {
        trip_id: this._selectedTripId,
        document_id: form.dataset.documentId,
        patch: {
          title: cleanText(values.title),
          document_type: cleanText(values.document_type) || "other",
          provider: cleanText(values.provider),
          summary: String(values.summary || ""),
          links: this._archiveLinks(dayId, stopId),
          offline_priority: Boolean(form.querySelector("input[name='offline_priority']")?.checked),
          sensitive: Boolean(form.querySelector("input[name='sensitive']")?.checked),
        },
      }, "Dokument gespeichert");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    if (formType === "archive-expense") {
      const amount = nullableNumber(values.amount);
      if (amount === null || amount < 0) {
        this._showToast("Bitte einen gültigen Ausgabenbetrag eingeben.", "error");
        return;
      }
      const stopParts = cleanText(values.stop_ref).split("::");
      const stopDayId = stopParts.length === 2 ? stopParts[0] : "";
      const stopId = stopParts.length === 2 ? stopParts[1] : "";
      const value = {
        merchant: cleanText(values.merchant),
        amount,
        currency: cleanText(values.currency).toUpperCase() || this._data?.settings?.default_currency || "EUR",
        category: cleanText(values.category) || "other",
        date: cleanText(values.date) || null,
        status: cleanText(values.status) || "paid",
        payment_method: cleanText(values.payment_method),
        day_id: stopDayId || cleanText(values.day_id) || null,
        stop_id: stopId || null,
        notes: String(values.notes || ""),
        source: "manual",
      };
      const mode = form.dataset.mode || "add";
      const result = mode === "edit"
        ? await this._runAction("archive_update_expense", { trip_id: this._selectedTripId, expense_id: form.dataset.expenseId, patch: value }, "Ausgabe gespeichert")
        : await this._runAction("archive_create_expense", { trip_id: this._selectedTripId, value }, "Ausgabe hinzugefügt");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    if (formType === "archive-todo") {
      const stopParts = cleanText(values.stop_ref).split("::");
      const stopDayId = stopParts.length === 2 ? stopParts[0] : "";
      const stopId = stopParts.length === 2 ? stopParts[1] : "";
      const value = {
        title: cleanText(values.title),
        due_at: cleanText(values.due_at) || null,
        priority: cleanText(values.priority) || "normal",
        status: cleanText(values.status) || "open",
        day_id: stopDayId || cleanText(values.day_id) || null,
        stop_id: stopId || null,
        notes: String(values.notes || ""),
        source: "manual",
      };
      if (!value.title) {
        this._showToast("Bitte einen Aufgabentitel eingeben.", "error");
        return;
      }
      const mode = form.dataset.mode || "add";
      const result = mode === "edit"
        ? await this._runAction("archive_update_todo", { trip_id: this._selectedTripId, todo_id: form.dataset.todoId, patch: value }, "Aufgabe gespeichert")
        : await this._runAction("archive_create_todo", { trip_id: this._selectedTripId, value }, "Aufgabe hinzugefügt");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    const expectedRevision = Number.parseInt(form.dataset.revision || "", 10);
    if (!Number.isInteger(expectedRevision)) {
      this._showToast("Die Bearbeitungsrevision fehlt. Bitte Dialog neu öffnen.", "error");
      return;
    }

    if (formType === "trip") {
      this._closeDialog({ flushRefresh: false });
      await this._runAction("update_trip", {
        expected_revision: expectedRevision,
        patch: {
          title: cleanText(values.title),
          status: cleanText(values.status) || "planned",
          start_date: cleanText(values.start_date) || null,
          end_date: cleanText(values.end_date) || null,
          notes: String(values.notes || ""),
        },
      }, "Reise gespeichert");
      return;
    }

    if (formType === "day") {
      const mode = form.dataset.mode;
      const existing = mode === "edit" ? this._findDay(form.dataset.dayId) : null;
      const details = this._detailsWithMedia(existing, {
        image_url: cleanText(values.image_url),
        alt: cleanText(values.image_alt),
        attribution: cleanText(values.image_attribution),
        source_url: cleanText(values.image_source_url),
        provider: cleanText(values.image_provider) || "manual",
      });
      const common = {
        title: cleanText(values.title),
        day_date: cleanText(values.date) || null,
        start: String(values.start || ""),
        end: String(values.end || ""),
        distance_km: nullableNumber(values.distance_km),
        drive_minutes: nullableNumber(values.drive_minutes, true),
        status: cleanText(values.status) || "planned",
        notes: String(values.notes || ""),
        details,
        position: nullableNumber(values.position, true),
        expected_revision: expectedRevision,
      };
      this._closeDialog({ flushRefresh: false });
      if (mode === "add") {
        const result = await this._runAction("add_day", common, "Reisetag hinzugefügt");
        if (result?.day?.id) {
          this._selectedDayId = result.day.id;
          this._expandedDays.add(result.day.id);
        }
      } else {
        await this._runAction("update_day", {
          day_id: form.dataset.dayId,
          expected_revision: common.expected_revision,
          position: common.position,
          patch: {
            title: common.title,
            date: common.day_date,
            start: common.start,
            end: common.end,
            distance_km: common.distance_km,
            drive_minutes: common.drive_minutes,
            status: common.status,
            notes: common.notes,
            details: common.details,
          },
        }, "Reisetag gespeichert");
      }
      return;
    }

    if (formType === "stop") {
      const mode = form.dataset.mode;
      const latitude = nullableNumber(values.latitude);
      const longitude = nullableNumber(values.longitude);
      const location = {
        address: cleanText(values.address),
        city: cleanText(values.city),
        country_code: cleanText(values.country_code).toUpperCase(),
        latitude,
        longitude,
      };
      const existing = mode === "edit"
        ? this._findStop(form.dataset.dayId, form.dataset.stopId)
        : null;
      const details = this._detailsWithMedia(existing, {
        image_url: cleanText(values.image_url),
        alt: cleanText(values.image_alt),
        attribution: cleanText(values.image_attribution),
        source_url: cleanText(values.image_source_url),
        provider: cleanText(values.image_provider) || "manual",
      });
      const existingTransport = cloneObject(existing?.details?.transport);
      const modeToNext = cleanText(values.segment_mode_to_next) || "auto";
      const ferryRole = cleanText(values.ferry_role);
      if (modeToNext && modeToNext !== "auto") existingTransport.mode_to_next = modeToNext;
      else delete existingTransport.mode_to_next;
      if (ferryRole) existingTransport.ferry_role = ferryRole;
      else delete existingTransport.ferry_role;
      if (Object.keys(existingTransport).length) details.transport = existingTransport;
      else delete details.transport;
      const common = {
        day_id: form.dataset.dayId,
        name: cleanText(values.name),
        stop_type: cleanText(values.stop_type) || "waypoint",
        arrival_time: cleanText(values.arrival_time) || null,
        departure_time: cleanText(values.departure_time) || null,
        location,
        notes: String(values.notes || ""),
        details,
        position: nullableNumber(values.position, true),
        expected_revision: expectedRevision,
      };
      this._closeDialog({ flushRefresh: false });
      this._expandedDays.add(common.day_id);
      if (mode === "add") {
        await this._runAction("add_stop", common, "Stopp hinzugefügt");
      } else {
        await this._runAction("update_stop", {
          day_id: common.day_id,
          stop_id: form.dataset.stopId,
          expected_revision: common.expected_revision,
          position: common.position,
          patch: {
            name: common.name,
            type: common.stop_type,
            arrival_time: common.arrival_time,
            departure_time: common.departure_time,
            location: common.location,
            notes: common.notes,
            details: common.details,
          },
        }, "Stopp gespeichert");
      }
    }
  }

  _render({ preserveScroll = false } = {}) {
    if (!this.shadowRoot) return;
    const content = this.shadowRoot.querySelector(".content");
    const scrollTop = preserveScroll ? content?.scrollTop || 0 : 0;
    this._mapModels = new Map();
    this.shadowRoot.innerHTML = `${this._styles()}${this._renderApp()}`;
    const nextContent = this.shadowRoot.querySelector(".content");
    if (preserveScroll && nextContent) nextContent.scrollTop = scrollTop;
    this._renderToastHost();
    this._setBusy(this._busy);
    queueMicrotask(() => {
      this._hydrateMaps();
      if (this._activeTab === "assistant") {
        const thread = this.shadowRoot.querySelector(".assistant-thread");
        if (thread) thread.scrollTop = 0;
      }
    });
  }

  _renderApp() {
    const title = this._data?.summary?.trip?.title || "Roadplanner";
    const revision = this._data?.summary?.revision;
    const activeBadge = this._data && !this._data.selected_is_active
      ? '<span class="view-badge">Nur Ansicht</span>'
      : "";
    return `
      <div class="app ${this._busy ? "busy" : ""}">
        <header class="topbar">
          <div class="topbar-start">
            <button class="icon-button menu-button" type="button" data-action="open-menu" aria-label="Menü öffnen" title="Menü öffnen">
              <ha-icon icon="mdi:menu"></ha-icon>
            </button>
            <div class="app-icon"><ha-icon icon="mdi:map-marker-path"></ha-icon></div>
            <div class="title-group">
              <div class="title-line"><h1>${escapeHtml(title)}</h1>${activeBadge}</div>
              <div class="subtitle">${revision === undefined ? "Home Assistant" : `Revision ${revision}`}</div>
            </div>
          </div>
          <div class="topbar-actions">
            ${this._renderTripSelect()}
            <button class="icon-button" type="button" data-action="refresh" aria-label="Neu laden" title="Neu laden">
              <ha-icon icon="mdi:refresh"></ha-icon>
            </button>
          </div>
        </header>
        ${this._renderTabs()}
        <main class="content">
          ${this._initialLoading ? this._renderLoading() : ""}
          ${this._error ? this._renderError() : ""}
          ${!this._initialLoading && !this._error && this._data ? this._renderActiveTab() : ""}
        </main>
        <div class="progress" aria-label="Aktion läuft" ${this._busy ? "" : "hidden"}></div>
        <div class="toast-host"></div>
        <input id="roadplanner-document-input" data-archive-file-input type="file" accept="application/pdf,image/jpeg,image/png,image/webp,image/heic,image/heif,text/plain,text/markdown,text/csv,text/calendar,application/json,application/gpx+xml,application/xml,text/xml,application/zip,.md,.markdown,.txt,.json,.csv,.gpx,.ics,.ical,.zip" hidden>
        <input id="roadplanner-camera-input" data-archive-file-input type="file" accept="image/*" capture="environment" hidden>
        ${this._dialog ? this._renderDialog() : ""}
      </div>
    `;
  }

  _renderTripSelect() {
    const trips = (this._data?.trips?.trips || []).filter((trip) => trip.valid);
    if (!trips.length) return "";
    return `<label class="trip-select" title="Reise auswählen">
      <ha-icon icon="mdi:map-multiple-outline"></ha-icon>
      <select data-action="select-trip" aria-label="Reise auswählen">
        ${trips.map((trip) => `<option value="${escapeHtml(trip.id)}" ${trip.id === this._selectedTripId ? "selected" : ""}>${escapeHtml(trip.title)}${trip.active ? " · aktiv" : ""}</option>`).join("")}
      </select>
    </label>`;
  }

  _renderTabs() {
    const pending = this._data?.handoffs?.total || 0;
    const drafts = this._data?.assistant?.basket_count || 0;
    const todoTiming = this._todoTimingSummary();
    const decisionCount = Number(this._data?.experience?.stats?.open_decision_count || 0);
    const mediaReviewCount = Number(this._data?.experience?.stats?.suggested_count || 0) + Number(this._data?.experience?.stats?.unassigned_count || 0);
    const importReadyCount = this._importDocuments().filter((item) => item?.analysis?.universal_import?.status === "ready").length;
    const tabs = [
      ["overview", "mdi:view-dashboard-outline", "Übersicht"],
      ["assistant", "mdi:message-text-outline", "Assistent"],
      ["import", "mdi:file-import-outline", "Import"],
      ["decisions", "mdi:cards-playing-outline", "Entscheidungen"],
      ["media", "mdi:image-multiple-outline", "Fotos"],
      ["archive", "mdi:file-document-multiple-outline", "Dokumente & Kosten"],
      ["day-route", "mdi:map-clock-outline", "Tagesroute"],
      ["total-route", "mdi:map-marker-path", "Gesamtroute"],
      ["trips", "mdi:map-multiple-outline", "Reisen"],
      ["handoffs", "mdi:inbox-arrow-down", "Übergaben"],
    ];
    return `<nav class="tabs" aria-label="Roadplanner Bereiche">
      ${tabs.map(([id, icon, label]) => `
        <button type="button" class="tab ${this._activeTab === id ? "active" : ""}" data-tab="${id}">
          <ha-icon icon="${icon}"></ha-icon>
          <span>${label}</span>
          ${id === "assistant" && drafts ? `<span class="count-badge">${drafts}</span>` : ""}
          ${id === "import" && importReadyCount ? `<span class="count-badge info">${importReadyCount}</span>` : ""}
          ${id === "decisions" && decisionCount ? `<span class="count-badge info">${decisionCount}</span>` : ""}
          ${id === "media" && mediaReviewCount ? `<span class="count-badge warning">${mediaReviewCount}</span>` : ""}
          ${id === "archive" && todoTiming.urgent ? `<span class="count-badge" title="Heute fällig oder überfällig">${todoTiming.urgent}</span>` : ""}
          ${id === "archive" && !todoTiming.urgent && todoTiming.upcoming ? `<span class="count-badge warning" title="In den nächsten 24 Stunden fällig">${todoTiming.upcoming}</span>` : ""}
          ${id === "handoffs" && pending ? `<span class="count-badge">${pending}</span>` : ""}
        </button>
      `).join("")}
    </nav>`;
  }

  _renderActiveTab() {
    if (this._activeTab === "assistant") return this._renderAssistant();
    if (this._activeTab === "import") return this._renderUniversalImport();
    if (this._activeTab === "decisions") return this._renderDecisions();
    if (this._activeTab === "media") return this._renderMedia();
    if (this._activeTab === "archive") return this._renderArchive();
    if (this._activeTab === "day-route") return this._renderDayRoute();
    if (this._activeTab === "total-route") return this._renderTotalRoute();
    if (this._activeTab === "trips") return this._renderTrips();
    if (this._activeTab === "handoffs") return this._renderHandoffs();
    return this._renderOverview();
  }

  _renderLoading() {
    return `<div class="loading-state">
      <div class="spinner"></div>
      <strong>Roadplanner wird geladen</strong>
      <span>Reisen, Routen und Übergaben werden abgerufen.</span>
    </div>`;
  }

  _renderError() {
    return `<div class="empty-state error-state">
      <ha-icon icon="mdi:alert-circle-outline"></ha-icon>
      <h2>Roadplanner konnte nicht geladen werden</h2>
      <p>${escapeHtml(this._error)}</p>
      <button class="primary-button" type="button" data-action="refresh">Erneut versuchen</button>
    </div>`;
  }

  _renderReadOnlyNotice() {
    if (this._data.selected_is_active) return "";
    const canActivate = this._canActivate();
    return `<div class="notice info view-notice">
      <ha-icon icon="mdi:eye-outline"></ha-icon>
      <div><strong>Historische oder alternative Reise geöffnet</strong><span>Du siehst diese Reise, ohne die aktive Planung umzuschalten.</span></div>
      ${canActivate ? `<button class="secondary-button compact-button" type="button" data-action="activate-trip" data-trip-id="${escapeHtml(this._selectedTripId)}">Als aktiv setzen</button>` : ""}
    </div>`;
  }

  _assistantAutonomyLabel(level) {
    return {
      answers: "Nur Antworten",
      suggestions: "Antworten & Vorschläge",
      change_basket: "Gespräch & Änderungskorb",
    }[level] || "Gespräch & Änderungskorb";
  }

  _assistantHealthPresentation(health) {
    if (!health || !health.configured) {
      return { label: "Nicht eingerichtet", className: "muted", icon: "mdi:connection" };
    }
    const cooldown = Number(health.cooldown_remaining_seconds || 0);
    if (cooldown > 0) {
      return { label: `Schutzpause ${Math.ceil(cooldown)} s`, className: "warning", icon: "mdi:timer-sand" };
    }
    if (Number(health.queue_depth || 0) > 0 || Number(health.active_requests || 0) > 0) {
      return { label: `Warteschlange ${Number(health.queue_depth || 0)}`, className: "muted", icon: "mdi:tray-full" };
    }
    if (health.last_error_code) {
      return { label: "Zuletzt mit Fehler", className: "warning", icon: "mdi:alert-circle-outline" };
    }
    if (health.last_success_at) {
      return { label: "Bereit", className: "success", icon: "mdi:check-network-outline" };
    }
    return { label: "Noch nicht getestet", className: "muted", icon: "mdi:connection" };
  }

  _renderAssistant() {
    const assistant = this._data?.assistant || {};
    const settings = this._data?.settings || {};
    const canUse = Boolean(this._data?.capabilities?.can_assistant);
    const configured = Boolean(assistant.configured);
    const messages = assistant.messages || [];
    const orderedMessages = messages.slice().reverse();
    const basket = assistant.basket || [];
    const memory = assistant.memory || {};
    const health = assistant.provider_health || {};
    const usage = assistant.usage || {};
    const healthView = this._assistantHealthPresentation(health);
    const basketEnabled = assistant.change_basket_enabled !== false;
    const autonomyLabel = this._assistantAutonomyLabel(assistant.autonomy_level || settings.assistant_autonomy_level);
    const plugins = Array.isArray(assistant.plugins) ? assistant.plugins : [];
    const pluginLabel = plugins.length
      ? plugins.filter((item) => item?.enabled !== false).map((item) => item?.title || item?.name || item?.id).filter(Boolean).join(" · ")
      : "Keine aktiven Plugins";

    if (!canUse) {
      return `<div class="empty-state"><ha-icon icon="mdi:account-lock-outline"></ha-icon><h2>Assistent nicht freigegeben</h2><p>Für den Roadplanner-Assistenten sind Bearbeitungsrechte erforderlich.</p></div>`;
    }

    if (!configured) {
      return `
        <section class="assistant-setup panel-card">
          <div class="assistant-setup-icon"><ha-icon icon="mdi:robot-confused-outline"></ha-icon></div>
          <div>
            <span class="eyebrow">Einrichtung</span>
            <h2>Gemini API-Schlüssel fehlt</h2>
            <p>Öffne <strong>Einstellungen → Geräte & Dienste → Roadplanner → Konfigurieren</strong> und hinterlege den Gemini API-Schlüssel. Der Schlüssel bleibt serverseitig in Home Assistant und wird nicht an das Panel ausgegeben.</p>
            <div class="settings-list">
              ${this._valueRow("Provider", settings.assistant_provider || "gemini")}
              ${this._valueRow("Primärmodell", settings.assistant_model || "gemini-3.5-flash")}
              ${this._settingRow("Webrecherche", settings.assistant_research_enabled)}
              ${this._settingRow("GPS-Auflösung", settings.assistant_geocoding_enabled)}
            </div>
          </div>
        </section>`;
    }

    const composer = `<form class="assistant-composer assistant-composer-top" data-form="assistant-chat">
      <div class="assistant-composer-heading">
        <label for="roadplanner-assistant-message">Nachricht an den Reiseplaner</label>
        <span><ha-icon icon="mdi:sort-clock-descending-outline"></ha-icon>Neueste Nachrichten oben</span>
      </div>
      <div class="assistant-input-row">
        <textarea id="roadplanner-assistant-message" name="message" rows="2" maxlength="12000" placeholder="Zum Beispiel: Wo wollten wir heute Abend essen oder übernachten?" required></textarea>
        <div class="assistant-input-actions">
          <button class="icon-button assistant-attach" type="button" data-action="archive-assistant-attach" title="Reisedokument oder Beleg anhängen" aria-label="Dokument anhängen" ${this._canEdit() ? "" : "disabled"}><ha-icon icon="mdi:paperclip"></ha-icon></button>
          <button class="primary-button assistant-send" type="button" data-action="assistant-send" title="Nachricht senden" ${this._assistantSubmitInFlight ? "disabled aria-busy=\"true\"" : "aria-busy=\"false\""}><ha-icon icon="mdi:send"></ha-icon><span>${this._assistantSubmitInFlight ? "Wird gesendet …" : "Senden"}</span></button>
        </div>
      </div>
      <div class="assistant-hint"><ha-icon icon="mdi:shield-check-outline"></ha-icon>Im Gespräch wird nichts automatisch gespeichert.${basketEnabled ? " Eindeutige Entscheidungen können vorgemerkt werden." : " Der Änderungskorb ist in diesem Autonomiemodus deaktiviert."}</div>
    </form>`;

    return `
      ${this._renderReadOnlyNotice()}
      <section class="assistant-toolbar panel-card assistant-toolbar-primary">
        <div>
          <span class="eyebrow">Reisegespräch · ${escapeHtml(autonomyLabel)}</span>
          <h2>Plane ganz normal im Gespräch</h2>
          <p>Der aktuelle Roadbook-Stand wird bei jeder Nachricht neu geladen. Die neuesten Antworten stehen direkt oben.</p>
        </div>
        <div class="assistant-toolbar-actions assistant-main-actions">
          ${assistant.copilot_enabled ? `<button class="primary-button compact-button assistant-briefing-button" type="button" data-action="assistant-briefing"><ha-icon icon="mdi:weather-sunset-up"></ha-icon> Tagesbriefing</button>` : ""}
          <button class="secondary-button compact-button" type="button" data-action="assistant-clear" ${messages.length || basket.length ? "" : "disabled"}><ha-icon icon="mdi:message-refresh-outline"></ha-icon> Neue Unterhaltung</button>
        </div>
      </section>

      ${!this._data.selected_is_active ? `<div class="notice warning"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Planung im Lesemodus</strong><span>Du kannst diese Reise besprechen. Für die Änderungsübersicht muss sie zuerst als aktive Reise gesetzt werden.</span></div></div>` : ""}

      ${this._assistantLastFailedText ? `<div class="notice warning assistant-retry-notice"><ha-icon icon="mdi:reload-alert"></ha-icon><div><strong>Die letzte Nachricht wurde nicht beantwortet</strong><span>Der Text bleibt erhalten. Roadplanner kann ihn mit aktuellem Reisekontext erneut senden.</span></div><button class="secondary-button compact-button" type="button" data-action="assistant-retry"><ha-icon icon="mdi:reload"></ha-icon> Erneut senden</button></div>` : ""}

      <section class="assistant-layout">
        <div class="assistant-chat panel-card newest-first">
          ${composer}
          <div class="assistant-thread" aria-live="polite" aria-label="Reisegespräch, neueste Nachrichten zuerst">
            ${this._assistantPending ? this._renderAssistantPending(this._assistantPending) : ""}
            ${orderedMessages.length ? orderedMessages.map((message) => this._renderAssistantMessage(message)).join("") : (this._assistantPending ? "" : this._renderAssistantWelcome())}
          </div>
        </div>

        <aside class="assistant-basket panel-card">
          <div class="section-heading compact">
            <div><span class="eyebrow">${basketEnabled ? "Änderungskorb" : "Autonomiemodus"}</span><h2>${basketEnabled ? `${basket.length} vorgemerkt` : escapeHtml(autonomyLabel)}</h2></div>
            <span class="basket-counter">${basketEnabled ? basket.length : "—"}</span>
          </div>
          ${basketEnabled
            ? (basket.length ? `<div class="basket-list">${basket.map((item) => this._renderDraftItem(item)).join("")}</div>` : `<div class="basket-empty"><ha-icon icon="mdi:playlist-edit"></ha-icon><strong>Noch keine Änderung</strong><span>Fragen und Vorschläge bleiben unverbindlich. Klare Entscheidungen oder Planungsaufträge erscheinen hier.</span></div>`)
            : `<div class="basket-empty"><ha-icon icon="mdi:message-processing-outline"></ha-icon><strong>Keine Vormerkungen</strong><span>In diesem Modus beantwortet der Assistent Fragen${assistant.autonomy_level === "suggestions" ? " und macht Vorschläge" : ""}, sammelt aber keine Änderungen. Das kannst du in den Integrationsoptionen umstellen.</span></div>`}
          <button class="primary-button full-width" type="button" data-action="assistant-prepare" ${basketEnabled && basket.length && this._data.selected_is_active ? "" : "disabled"}><ha-icon icon="mdi:clipboard-text-search-outline"></ha-icon> Änderungen prüfen</button>
          <p class="basket-footnote">Der Button erzeugt nur einen prüfbaren Entwurf. Das Reisegespräch läuft danach weiter; übernommen wird weiterhin separat in der Änderungsübersicht.</p>
        </aside>
      </section>

      <details class="assistant-technical panel-card">
        <summary><span><ha-icon icon="mdi:tools"></ha-icon>Technik & Diagnose</span><small>Providerstatus, Nutzung, Plugins und Fehlerdetails</small></summary>
        <div class="assistant-technical-content">
          <div class="assistant-technical-actions">
            <span class="assistant-model"><ha-icon icon="mdi:creation-outline"></ha-icon>${escapeHtml(assistant.model || settings.assistant_model || "Gemini")}</span>
            <span class="assistant-health ${healthView.className}"><ha-icon icon="${healthView.icon}"></ha-icon>${escapeHtml(healthView.label)}</span>
            <button class="secondary-button compact-button" type="button" data-action="assistant-test"><ha-icon icon="mdi:connection"></ha-icon> Verbindung testen</button>
            ${assistant.debug_enabled && this._canAdmin() ? `<button class="secondary-button compact-button" type="button" data-action="assistant-debug"><ha-icon icon="mdi:bug-outline"></ha-icon> Diagnose öffnen</button>` : ""}
          </div>
          <section class="assistant-status-grid">
            <article class="assistant-status-card"><ha-icon icon="mdi:message-text-clock-outline"></ha-icon><div><span>Gespräch</span><strong>${Number(memory.total_message_count || messages.length)} Nachrichten</strong><small>${memory.compacted_message_count ? `${Number(memory.compacted_message_count)} ältere Nachrichten lokal zusammengefasst` : "Noch keine Komprimierung nötig"}</small></div></article>
            <article class="assistant-status-card"><ha-icon icon="mdi:leaf-circle-outline"></ha-icon><div><span>API-Nutzung</span><strong>1 Aufruf pro Nachricht</strong><small>${Number(usage.logical_calls || 0)} Sitzungsaufrufe · ${Number(usage.total_tokens || 0).toLocaleString("de-DE")} Tokens</small></div></article>
            <article class="assistant-status-card"><ha-icon icon="mdi:backup-restore"></ha-icon><div><span>Ausfallschutz</span><strong>${Number(health.retry_attempts || 0)} Wiederholungen${health.fallback_model ? " + Fallback" : ""}</strong><small>${Number(health.queue_depth || 0)} wartend · Mindestabstand ${Number(health.min_request_interval || 0)} s${health.cooldown_remaining_seconds ? ` · Schutzpause ${Math.ceil(Number(health.cooldown_remaining_seconds))} s` : ""}</small></div></article>
            <article class="assistant-status-card"><ha-icon icon="mdi:puzzle-outline"></ha-icon><div><span>Plugins</span><strong>${plugins.filter((item) => item?.enabled !== false).length} aktiv</strong><small>${escapeHtml(pluginLabel)}</small></div></article>
          </section>
        </div>
      </details>`;
  }
  _renderAssistantWelcome() {
    const prompts = [
      ["mdi:calendar-today-outline", "Was ist heute geplant?"],
      ["mdi:food-fork-drink", "Wo wollten wir heute Abend essen oder übernachten?"],
      ["mdi:map-marker-star-outline", "Welche drei Stopps empfiehlst du für morgen?"],
    ];
    return `<div class="assistant-welcome">
      <div class="assistant-avatar"><ha-icon icon="mdi:map-marker-path"></ha-icon></div>
      <h3>Wobei soll ich euch helfen?</h3>
      <p>Ich kenne den gespeicherten Reiseplan, kann aktuelle Informationen recherchieren und Vorschläge vergleichen.</p>
      <div class="quick-prompt-grid">${prompts.map(([icon, prompt]) => `<button type="button" data-action="assistant-quick" data-prompt="${escapeHtml(prompt)}"><ha-icon icon="${icon}"></ha-icon><span>${escapeHtml(prompt)}</span></button>`).join("")}</div>
    </div>`;
  }

  _renderAssistantPending(pending) {
    return `<div class="assistant-pending-group">
      <article class="assistant-message user pending">
        <div class="message-avatar"><ha-icon icon="mdi:account-outline"></ha-icon></div>
        <div class="message-body"><div class="message-meta"><strong>Du</strong><span>Wird gesendet</span></div><div class="message-text">${escapeHtml(pending.text || "")}</div></div>
      </article>
      <article class="assistant-message assistant pending thinking" aria-busy="true">
        <div class="message-avatar"><ha-icon icon="mdi:robot-outline"></ha-icon></div>
        <div class="message-body"><div class="message-meta"><strong>Roadplanner</strong><span>arbeitet</span></div><div class="assistant-thinking"><span></span><span></span><span></span><strong>Roadplanner denkt und lädt den aktuellen Reisekontext …</strong></div></div>
      </article>
    </div>`;
  }

  _renderAssistantMessage(message) {
    const assistant = message.role === "assistant";
    const sources = (message.sources || [])
      .map((source) => ({ title: cleanText(source.title) || "Quelle", url: this._safeUrl(source.url) }))
      .filter((source) => source.url);
    const status = message.kind === "status";
    const basketOutcome = message?.metadata?.basket_outcome || {};
    const basketWarning = cleanText(message?.metadata?.basket_warning || "");
    const basketChanged = Number(basketOutcome.actual_change_count || 0);
    const basketMeta = assistant && (basketChanged > 0 || basketWarning)
      ? `<div class="message-basket-status ${basketChanged > 0 ? "success" : "warning"}"><ha-icon icon="${basketChanged > 0 ? "mdi:playlist-check" : "mdi:playlist-remove"}"></ha-icon><span>${basketChanged > 0 ? `${basketChanged} ${basketChanged === 1 ? "Änderung" : "Änderungen"} tatsächlich vorgemerkt · Korb jetzt ${Number(basketOutcome.after_count || 0)}` : escapeHtml(basketWarning)}</span></div>`
      : "";
    return `<article class="assistant-message ${assistant ? "assistant" : "user"} ${status ? "status" : ""}">
      <div class="message-avatar"><ha-icon icon="${assistant ? "mdi:robot-outline" : "mdi:account-outline"}"></ha-icon></div>
      <div class="message-body">
        <div class="message-meta"><strong>${assistant ? "Roadplanner" : "Du"}</strong><span>${escapeHtml(this._formatTimestamp(message.created_at))}</span></div>
        <div class="message-text">${this._renderAssistantContent(message.content || "")}</div>
        ${basketMeta}
        ${sources.length ? `<div class="message-sources"><span>Quellen</span>${sources.map((source) => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>${escapeHtml(source.title)}</a>`).join("")}</div>` : ""}
        ${assistant && !status && message.id ? `<div class="message-actions"><button class="text-button" type="button" data-action="decision-from-message" data-message-id="${escapeHtml(message.id)}" ${this._decisionCreateInFlightMessageId ? "disabled" : ""}><ha-icon icon="${this._decisionCreateInFlightMessageId === message.id ? "mdi:loading mdi-spin" : "mdi:cards-playing-outline"}"></ha-icon>${this._decisionCreateInFlightMessageId === message.id ? "Vorlage wird erstellt …" : "Als Entscheidungsvorlage"}</button></div>` : ""}
      </div>
    </article>`;
  }

  _renderDraftItem(item) {
    const action = {
      add: "Hinzufügen",
      update: "Ändern",
      remove: "Entfernen",
      plan: "Planen",
    }[item.action] || item.action || "Änderung";
    const type = {
      trip: "Reise",
      day: "Tag",
      stop: "Stopp",
      preference: "Präferenz",
    }[item.entity_type] || item.entity_type || "Plan";
    const mapsSearch = item.entity_type === "stop" && item.place_query
      ? this._externalLink(this._googleMapsQueryUrl(item.place_query), "In Google Maps suchen", "mdi:google-maps", "text-link")
      : "";
    return `<article class="basket-item">
      <div class="basket-item-icon"><ha-icon icon="${item.entity_type === "stop" ? "mdi:map-marker-plus-outline" : item.entity_type === "day" ? "mdi:calendar-edit" : item.entity_type === "preference" ? "mdi:tune-variant" : "mdi:map-edit-outline"}"></ha-icon></div>
      <div class="basket-item-copy"><div class="basket-item-label"><span>${escapeHtml(type)}</span><b>${escapeHtml(action)}</b></div><strong>${escapeHtml(item.summary || "Vorgemerkte Änderung")}</strong>${item.reason ? `<p>${escapeHtml(item.reason)}</p>` : ""}${mapsSearch ? `<div class="basket-map-link">${mapsSearch}</div>` : ""}</div>
      <div class="basket-item-actions">
        <button class="icon-button" type="button" data-action="assistant-edit-draft" data-draft-id="${escapeHtml(item.id)}" aria-label="Vormerkung bearbeiten" title="Vormerkung bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>
        <button class="icon-button basket-remove" type="button" data-action="assistant-remove-draft" data-draft-id="${escapeHtml(item.id)}" aria-label="Vormerkung entfernen" title="Vormerkung entfernen"><ha-icon icon="mdi:close"></ha-icon></button>
      </div>
    </article>`;
  }

  _archiveDayLabel(dayId) {
    const day = this._findDay(dayId);
    if (!day) return cleanText(dayId) || "Reise";
    return `${this._formatDate(day.date)} · ${day.title || day.id}`;
  }

  _archiveStopLabel(dayId, stopId) {
    const stop = this._findStop(dayId, stopId);
    if (!stop) return cleanText(stopId) || "Stopp";
    return stop.name || stop.id;
  }

  _formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let current = bytes;
    let index = 0;
    while (current >= 1024 && index < units.length - 1) {
      current /= 1024;
      index += 1;
    }
    const decimals = index === 0 ? 0 : current >= 10 ? 1 : 2;
    return `${current.toFixed(decimals).replace(".", ",")} ${units[index]}`;
  }

  _archiveLinkLabel(item) {
    const dayId = item?.day_id || item?.links?.day_ids?.[0] || item?.links?.stop_links?.[0]?.day_id || "";
    const stopId = item?.stop_id || item?.links?.stop_links?.[0]?.stop_id || "";
    if (dayId && stopId) return `${this._archiveDayLabel(dayId)} · ${this._archiveStopLabel(dayId, stopId)}`;
    if (dayId) return this._archiveDayLabel(dayId);
    return "Gesamte Reise";
  }

  _archiveRecordsForDay(dayId) {
    const archive = this._archiveData();
    const bucket = archive.by_day?.[dayId] || { documents: [], expenses: [], todos: [] };
    const byIds = (items, ids) => ids.map((id) => items.find((item) => item.id === id)).filter(Boolean);
    return {
      documents: byIds(archive.documents || [], bucket.documents || []),
      expenses: byIds(archive.expenses || [], bucket.expenses || []),
      todos: byIds(archive.todos || [], bucket.todos || []),
    };
  }

  _archiveRecordsForStop(dayId, stopId) {
    const archive = this._archiveData();
    const bucket = archive.by_stop?.[`${dayId}/${stopId}`] || { documents: [], expenses: [], todos: [] };
    const byIds = (items, ids) => ids.map((id) => items.find((item) => item.id === id)).filter(Boolean);
    return {
      documents: byIds(archive.documents || [], bucket.documents || []),
      expenses: byIds(archive.expenses || [], bucket.expenses || []),
      todos: byIds(archive.todos || [], bucket.todos || []),
    };
  }

  _archiveTotalText() {
    const totals = this._archiveData().stats?.totals_by_currency || {};
    const entries = Object.entries(totals);
    if (!entries.length) return "Noch keine Ausgaben";
    return entries.map(([currency, amount]) => this._formatMoney(amount, currency)).join(" · ");
  }

  _experienceData() {
    return this._data?.experience || { decisions: [], media: [], stats: {}, by_day: {}, by_stop: {}, onedrive: {} };
  }

  _experienceMediaByIds(ids) {
    const media = this._experienceData().media || [];
    const wanted = new Set(Array.isArray(ids) ? ids : []);
    return media.filter((item) => wanted.has(item.id));
  }

  _experienceMediaForDay(dayId) {
    if (!dayId) return [];
    const ids = this._experienceData().by_day?.[dayId] || [];
    return this._experienceMediaByIds(ids);
  }

  _experienceMediaForStop(stopId) {
    if (!stopId) return [];
    const ids = this._experienceData().by_stop?.[stopId] || [];
    return this._experienceMediaByIds(ids);
  }

  _renderExperienceAlbum(media, { dayId = "", stopId = "", compact = false, title = "Reisefotos" } = {}) {
    if (!Array.isArray(media) || !media.length) return "";
    const latest = media.slice(0, compact ? 5 : 8);
    const cover = media.find((item) => item.is_cover) || media[0];
    return `<section class="experience-album ${compact ? "compact" : ""}">
      <div class="experience-album-heading"><div><span class="eyebrow">Album</span><strong>${escapeHtml(title)}</strong><small>${media.length} ${media.length === 1 ? "Foto" : "Fotos"}</small></div><button class="text-button" type="button" data-action="media-open-album" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-media-id="${escapeHtml(cover.id)}">Alle ansehen</button></div>
      <div class="experience-album-strip">${latest.map((item) => `<button class="experience-album-thumb ${item.is_cover ? "cover" : ""}" type="button" data-action="media-open-album" data-day-id="${escapeHtml(dayId)}" data-stop-id="${escapeHtml(stopId)}" data-media-id="${escapeHtml(item.id)}"><img src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}" loading="lazy">${item.is_cover ? `<ha-icon icon="mdi:star"></ha-icon>` : ""}</button>`).join("")}</div>
    </section>`;
  }

  _renderDecisions() {
    const experience = this._experienceData();
    const decisions = (experience.decisions || []).filter((item) => item.status !== "archived");
    return `
      ${this._renderReadOnlyNotice()}
      <section class="panel-card decision-intro">
        <div><span class="eyebrow">Gemeinsam entscheiden</span><h2>Entscheidungs-Slides</h2><p>Speichere zwei oder drei Assistentenvorschläge als bildbasierte Vorlage, wische gemeinsam durch die Optionen und übernimm erst eure Auswahl in den Änderungskorb.</p></div>
      </section>
      ${decisions.length ? `<div class="decision-list">${decisions.map((decision) => this._renderDecisionCard(decision)).join("")}</div>` : `<div class="empty-state"><ha-icon icon="mdi:cards-playing-outline"></ha-icon><h2>Noch keine Entscheidungsvorlage</h2><p>Öffne eine Assistentenantwort mit mehreren konkreten Optionen und tippe dort auf „Als Entscheidungsvorlage“.</p></div>`}
    `;
  }

  _renderDecisionCard(decision) {
    const options = Array.isArray(decision.options) ? decision.options : [];
    if (!options.length) return "";
    let index = Number(this._decisionSlideIndexes.get(decision.id));
    if (!Number.isInteger(index) || index < 0 || index >= options.length) {
      const selected = options.findIndex((item) => item.id === decision.selected_option_id);
      index = selected >= 0 ? selected : 0;
    }
    const option = options[index];
    const imageUrl = this._safeUrl(option?.image?.image_url);
    const location = option?.location || {};
    const lat = Number(location.latitude ?? location.lat);
    const lon = Number(location.longitude ?? location.lon ?? location.lng);
    const hasCoordinate = Number.isFinite(lat) && Number.isFinite(lon);
    const mapsQuery = hasCoordinate ? `${lat},${lon}` : option.place_query || option.title;
    const route = option.route_metrics || {};
    const selected = decision.selected_option_id === option.id;
    const transferred = decision.status === "transferred";
    const cost = option.estimated_cost || {};
    const costText = Number.isFinite(Number(cost.amount))
      ? `${Number(cost.amount).toLocaleString("de-DE", { maximumFractionDigits: 2 })} ${escapeHtml(cost.currency || "EUR")}`
      : cleanText(cost.note);
    return `<article class="decision-card panel-card" data-decision-card="${escapeHtml(decision.id)}">
      <header class="decision-heading"><div><span class="eyebrow">${escapeHtml(this._archiveDayLabel(decision.linked_day_id))}</span><h2>${escapeHtml(decision.title || "Entscheidung")}</h2><p>${escapeHtml(decision.question || "Welche Option passt am besten?")}</p></div><span class="decision-counter">${index + 1} / ${options.length}</span></header>
      <div class="decision-slide">
        <div class="decision-image ${imageUrl ? "" : "empty"}">${imageUrl ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(option.image?.alt || option.title)}" loading="lazy">${option.image?.attribution ? `<small>${escapeHtml(option.image.attribution)}</small>` : ""}` : `<ha-icon icon="mdi:image-area"></ha-icon><span>Kein sicher zugeordnetes Bild gefunden</span>`}</div>
        <div class="decision-copy">
          <div class="decision-title-row"><div><span class="eyebrow">Option ${index + 1}</span><h3>${escapeHtml(option.title)}</h3></div>${selected ? `<span class="status-badge status-success">Ausgewählt</span>` : ""}</div>
          <p>${escapeHtml(option.summary || "")}</p>
          <div class="decision-metrics">
            ${Number.isFinite(Number(route.distance_km)) ? `<span><ha-icon icon="mdi:map-marker-distance"></ha-icon>${Number(route.distance_km).toLocaleString("de-DE", { maximumFractionDigits: 1 })} km</span>` : ""}
            ${Number.isFinite(Number(route.drive_minutes)) ? `<span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(this._formatDriveMinutes(Number(route.drive_minutes)))}</span>` : ""}
            ${costText ? `<span><ha-icon icon="mdi:cash"></ha-icon>${escapeHtml(costText)}</span>` : ""}
          </div>
          <div class="decision-procon"><div><strong>Vorteile</strong><ul>${(option.pros || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>Keine verifizierten Vorteile hinterlegt</li>"}</ul></div><div><strong>Nachteile</strong><ul>${(option.cons || []).map((item) => `<li>${escapeHtml(item)}</li>`).join("") || "<li>Keine verifizierten Nachteile hinterlegt</li>"}</ul></div></div>
          <div class="button-row decision-actions">
            ${this._externalLink(this._googleMapsQueryUrl(mapsQuery), "Karte", "mdi:google-maps", "secondary-button")}
            <button class="${selected ? "secondary-button" : "primary-button"}" type="button" data-action="decision-select" data-decision-id="${escapeHtml(decision.id)}" data-option-id="${escapeHtml(option.id)}" ${transferred ? "disabled" : ""}><ha-icon icon="mdi:check-circle-outline"></ha-icon>${selected ? "Ausgewählt" : "Diese Option auswählen"}</button>
            ${selected && !transferred ? `<button class="primary-button" type="button" data-action="decision-transfer" data-decision-id="${escapeHtml(decision.id)}"><ha-icon icon="mdi:playlist-plus"></ha-icon>In Änderungskorb</button>` : ""}
            ${transferred ? `<span class="status-badge status-success">Im Änderungskorb</span>` : ""}
          </div>
        </div>
      </div>
      <footer class="decision-footer"><button class="icon-button" type="button" data-action="decision-prev" data-decision-id="${escapeHtml(decision.id)}" aria-label="Vorherige Option"><ha-icon icon="mdi:chevron-left"></ha-icon></button><div class="decision-dots">${options.map((item, optionIndex) => `<button type="button" class="decision-dot ${optionIndex === index ? "active" : ""}" data-action="decision-go" data-decision-id="${escapeHtml(decision.id)}" data-option-index="${optionIndex}" aria-label="Option ${optionIndex + 1}"></button>`).join("")}</div><button class="icon-button" type="button" data-action="decision-next" data-decision-id="${escapeHtml(decision.id)}" aria-label="Nächste Option"><ha-icon icon="mdi:chevron-right"></ha-icon></button><button class="text-button" type="button" data-action="decision-archive" data-decision-id="${escapeHtml(decision.id)}">Archivieren</button></footer>
    </article>`;
  }

  _renderUniversalImport() {
    const imports = this._importDocuments().slice().sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
    const ready = imports.filter((item) => item?.analysis?.universal_import?.status === "ready").length;
    const transferred = imports.filter((item) => item?.analysis?.universal_import?.status === "transferred").length;
    return `
      ${this._renderReadOnlyNotice()}
      <section class="toolbar-card import-toolbar" data-archive-drop-zone>
        <div><span class="eyebrow">Universal Import</span><h2>Reisepläne und Übergaben einlesen</h2><p>Hänge Markdown, Text, JSON, CSV, GPX, ICS, PDF, Bilder oder ein begrenztes ZIP-Paket an. Roadplanner vergleicht den Inhalt mit dem aktuellen Roadbook und erzeugt erst nach deiner Freigabe Vormerkungen oder eine Review-Übergabe.</p></div>
        <div class="button-row"><button class="primary-button" type="button" data-action="universal-import-upload" ${this._canEdit() ? "" : "disabled"}><ha-icon icon="mdi:file-import-outline"></ha-icon>Datei importieren</button></div>
      </section>
      <section class="stat-grid import-stats">
        ${this._statCard("mdi:file-search-outline", imports.length, "analysiert")}
        ${this._statCard("mdi:clipboard-text-clock-outline", ready, "bereit")}
        ${this._statCard("mdi:check-decagram-outline", transferred, "übergeben")}
        ${this._statCard("mdi:shield-check-outline", "Review", "vor Speicherung")}
      </section>
      <section class="panel-card import-explainer"><div class="section-heading compact"><div><span class="eyebrow">Sicherer Ablauf</span><h2>Datei verstehen, Unterschiede prüfen, bewusst übernehmen</h2></div><ha-icon icon="mdi:shield-sync-outline"></ha-icon></div><div class="import-flow"><span>Datei</span><ha-icon icon="mdi:chevron-right"></ha-icon><span>Analyse</span><ha-icon icon="mdi:chevron-right"></ha-icon><span>Änderungskorb / Review</span><ha-icon icon="mdi:chevron-right"></ha-icon><span>Übernehmen</span></div><p class="muted">Eine importierte Datei verändert das Roadbook niemals direkt. Bestehende präzisere Daten und echte GPS-Punkte haben Vorrang.</p></section>
      ${imports.length ? `<section class="import-card-grid">${imports.map((item) => this._renderUniversalImportCard(item)).join("")}</section>` : `<div class="empty-state"><ha-icon icon="mdi:file-import-outline"></ha-icon><h2>Noch keine Übergabe importiert</h2><p>Markdown eignet sich besonders gut für Projektübergaben aus ChatGPT oder Gemini. GPX, ICS und CSV werden zusätzlich strukturell erkannt.</p><button class="primary-button" type="button" data-action="universal-import-upload" ${this._canEdit() ? "" : "disabled"}><ha-icon icon="mdi:paperclip"></ha-icon>Erste Datei auswählen</button></div>`}
    `;
  }

  _renderUniversalImportCard(documentItem) {
    const item = documentItem?.analysis?.universal_import || {};
    const status = item.status || "ready";
    const statusLabel = { ready: "Bereit", transferred: "Übergeben", discarded: "Verworfen" }[status] || status;
    const modeLabel = item.mode === "changeset" ? "Direktes ChangeSet" : "Änderungsvorschläge";
    const count = Number(item.counts?.operations ?? item.counts?.drafts ?? 0);
    const warningCount = (item.warnings || []).length + (item.open_questions || []).length;
    return `<article class="panel-card import-card">
      <div class="import-card-icon"><ha-icon icon="${item.mode === "changeset" ? "mdi:file-code-outline" : "mdi:file-document-edit-outline"}"></ha-icon></div>
      <div class="import-card-copy"><div class="import-card-title"><div><span class="eyebrow">${escapeHtml(item.format || "Datei")} · ${escapeHtml(modeLabel)}</span><h3>${escapeHtml(item.title || documentItem.title || documentItem.original_filename || "Import")}</h3></div><span class="status-badge ${status === "ready" ? "status-warning" : status === "transferred" ? "status-success" : "muted"}">${escapeHtml(statusLabel)}</span></div><p>${escapeHtml(item.summary || "Keine Zusammenfassung verfügbar.")}</p><div class="handoff-meta"><span><ha-icon icon="mdi:format-list-bulleted"></ha-icon>${count} ${item.mode === "changeset" ? "Operationen" : "Vormerkungen"}</span><span><ha-icon icon="mdi:alert-circle-outline"></ha-icon>${warningCount} Hinweise</span><span><ha-icon icon="mdi:file-outline"></ha-icon>${escapeHtml(documentItem.original_filename || documentItem.title || "Datei")}</span></div><div class="button-row"><button class="secondary-button" type="button" data-action="universal-import-open" data-document-id="${escapeHtml(documentItem.id)}"><ha-icon icon="mdi:eye-outline"></ha-icon>Vorschau</button>${status === "ready" && this._canEdit() ? `<button class="primary-button" type="button" data-action="universal-import-transfer" data-document-id="${escapeHtml(documentItem.id)}"><ha-icon icon="mdi:playlist-plus"></ha-icon>${item.mode === "changeset" ? "Zur Review-Übergabe" : "In Änderungskorb"}</button>` : ""}</div></div>
    </article>`;
  }

  _renderMedia() {
    const experience = this._experienceData();
    const media = experience.media || [];
    const oneDrive = experience.onedrive || {};
    const syncState = oneDrive.sync_state || {};
    const scanStats = syncState.scan_stats || {};
    const statusText = oneDrive.connected ? `${oneDrive.account_name || "OneDrive"} verbunden` : oneDrive.configured ? "Bereit zur Microsoft-Anmeldung" : "Noch nicht eingerichtet";
    const latest = media.slice(0, 120);
    const phase = syncState.mode || "";
    const phaseLabel = { initial_scan: "Selektiver Erstscan", delta_catchup: "Änderungen seit Scan nachziehen", delta: "Nur Änderungen" }[phase] || "Synchronisierung";
    const currentFolderValue = String(scanStats.current_folder || "");
    const currentFolder = currentFolderValue
      ? `<span class="onedrive-current-folder" title="${escapeHtml(currentFolderValue)}"><b>Aktueller Ordner:</b> ${escapeHtml(currentFolderValue)}</span>`
      : "";
    const relevantFolders = Number(scanStats.folders_discovered || 0);
    const completedFolders = Number(scanStats.folders_completed || 0);
    const skippedFolders = Number(scanStats.folders_skipped || 0);
    const progressSummary = `${Number(scanStats.entries_examined || 0).toLocaleString("de-DE")} Einträge geprüft · ${completedFolders.toLocaleString("de-DE")}/${relevantFolders.toLocaleString("de-DE")} relevante Ordner abgeschlossen · ${skippedFolders.toLocaleString("de-DE")} historische/technische Ordner übersprungen · ${Number(scanStats.relevant_photos || 0).toLocaleString("de-DE")} Reisefotos gefunden`;
    const syncNotice = syncState.truncated
      ? `<div class="notice neutral onedrive-sync-notice"><ha-icon icon="mdi:folder-sync-outline"></ha-icon><div><strong>${escapeHtml(phaseLabel)} läuft</strong><span>${progressSummary}.</span>${currentFolder}<small>Pro Lauf werden bis zu ${Number(oneDrive.max_items_per_run || 2000).toLocaleString("de-DE")} Metadateneinträge beziehungsweise höchstens ${Number(oneDrive.max_scan_seconds || 12).toLocaleString("de-DE")} Sekunden verarbeitet. Tippe erneut auf „Jetzt synchronisieren“ oder warte auf den nächsten automatischen Lauf.</small></div></div>`
      : syncState.last_sync_at
        ? `<div class="notice neutral"><ha-icon icon="mdi:clock-check-outline"></ha-icon><div><strong>OneDrive-Delta aktiv</strong><span>Zuletzt synchronisiert: ${escapeHtml(this._formatTimestamp(syncState.last_sync_at))} · Reisezeitraum ${escapeHtml(syncState.trip_date_range || "")} · historische Bilder außerhalb des Zeitfensters werden nicht übernommen.</span></div></div>`
        : "";
    return `
      ${this._renderReadOnlyNotice()}
      <section class="panel-card media-toolbar">
        <div><span class="eyebrow">OneDrive Personal</span><h2>Reisefotos automatisch zuordnen</h2><p>Roadplanner liest den Ordner <strong>${escapeHtml(oneDrive.folder_path || "Pictures/Camera Roll")}</strong>${oneDrive.recursive_subfolders ? " einschließlich Unterordnern" : ""}, berücksichtigt nur den Zeitraum der ausgewählten Reise mit ${Number(oneDrive.date_buffer_days || 0)} Tagen Puffer und kopiert keine Originale nach Home Assistant.</p></div>
        <div class="media-toolbar-actions"><span class="assistant-health ${oneDrive.connected ? "success" : "muted"}"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>${escapeHtml(statusText)}</span>${oneDrive.connected ? `<button class="secondary-button" type="button" data-action="onedrive-sync"><ha-icon icon="mdi:sync"></ha-icon>Jetzt synchronisieren</button><button class="text-button" type="button" data-action="onedrive-full-sync"><ha-icon icon="mdi:calendar-refresh-outline"></ha-icon>Neu ab Reisebeginn einlesen</button><button class="text-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:cog-outline"></ha-icon>Einrichtung</button><button class="text-button danger-text" type="button" data-action="onedrive-disconnect">Trennen</button>` : oneDrive.configured ? `<button class="primary-button" type="button" data-action="onedrive-connect"><ha-icon icon="mdi:login-variant"></ha-icon>Mit Microsoft anmelden</button><button class="text-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:cog-outline"></ha-icon>Einrichtung ändern</button>` : `<button class="primary-button" type="button" data-action="onedrive-setup"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>OneDrive einrichten</button>`}</div>
      </section>
      ${syncNotice}
      <section class="media-stat-grid">
        ${this._mediaStat("mdi:image-multiple-outline", "Fotos", experience.stats?.media_count || 0)}
        ${this._mediaStat("mdi:check-decagram-outline", "Automatisch", experience.stats?.automatic_count || 0)}
        ${this._mediaStat("mdi:help-circle-outline", "Zu prüfen", experience.stats?.suggested_count || 0)}
        ${this._mediaStat("mdi:image-off-outline", "Ohne Tag", experience.stats?.unassigned_count || 0)}
      </section>
      ${latest.length ? `<section class="media-grid">${latest.map((item, index) => this._renderMediaCard(item, index)).join("")}</section>` : `<div class="empty-state"><ha-icon icon="mdi:image-multiple-outline"></ha-icon><h2>Noch keine OneDrive-Fotos</h2><p>Verbinde OneDrive Personal und starte anschließend eine Synchronisierung. Bereits vorhandene Fotos im gewählten Kameraordner werden anhand von Datum und GPS zugeordnet.</p></div>`}
    `;
  }

  _mediaStat(icon, label, value) {
    return `<article class="panel-card media-stat"><ha-icon icon="${icon}"></ha-icon><div><strong>${Number(value).toLocaleString("de-DE")}</strong><span>${escapeHtml(label)}</span></div></article>`;
  }

  _renderMediaCard(item, index) {
    const day = item.linked_day_id ? this._findDay(item.linked_day_id) : null;
    const stop = item.linked_stop_id && item.linked_day_id ? this._findStop(item.linked_day_id, item.linked_stop_id) : null;
    const assignment = { automatic: "Automatisch", suggested: "Zu prüfen", manual: "Manuell", unassigned: "Nicht zugeordnet" }[item.assignment_status] || "Nicht zugeordnet";
    const statusClass = item.assignment_status === "automatic" || item.assignment_status === "manual" ? "status-success" : "status-warning";
    return `<article class="media-card ${item.is_cover ? "cover" : ""}">
      <button type="button" class="media-thumb" data-action="media-open" data-media-index="${index}"><img src="${escapeHtml(this._safeUrl(item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}" loading="lazy">${item.is_cover ? `<span class="cover-badge"><ha-icon icon="mdi:star"></ha-icon>Titelbild</span>` : ""}</button>
      <div class="media-card-copy"><div class="media-card-title"><strong>${escapeHtml(item.caption || item.name || "Foto")}</strong><span class="status-badge ${statusClass}">${escapeHtml(assignment)}</span></div><span>${escapeHtml(item.taken_at ? this._formatTimestamp(item.taken_at) : "Aufnahmezeit unbekannt")}</span><small>${escapeHtml(stop?.name || day?.title || "Noch keinem Reisetag zugeordnet")}${Number.isFinite(Number(item.distance_m)) ? ` · ${Math.round(Number(item.distance_m))} m` : ""}</small></div>
      <div class="media-card-actions"><button class="icon-button" type="button" data-action="media-edit" data-media-id="${escapeHtml(item.id)}" title="Zuordnung bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>${item.linked_stop_id && !item.is_cover ? `<button class="icon-button" type="button" data-action="media-cover" data-media-id="${escapeHtml(item.id)}" title="Als Titelbild"><ha-icon icon="mdi:star-outline"></ha-icon></button>` : ""}<a class="icon-button" href="${escapeHtml(this._safeUrl(item.original_url))}" target="_blank" rel="noopener noreferrer" title="Original öffnen"><ha-icon icon="mdi:open-in-new"></ha-icon></a></div>
    </article>`;
  }

  _renderArchive() {
    const archive = this._archiveData();
    const stats = archive.stats || {};
    const documents = archive.documents || [];
    const expenses = archive.expenses || [];
    const todos = archive.todos || [];
    const todoTiming = this._todoTimingSummary();
    return `${this._renderReadOnlyNotice()}
      <section class="toolbar-card archive-toolbar">
        <div>
          <span class="eyebrow">Reiseunterlagen</span>
          <h2>Dokumente, Ausgaben & Tagesaufgaben</h2>
          <p>Tickets und Buchungen bleiben privat im Roadplanner. Belege können nur zur Kostenerfassung analysiert und anschließend automatisch gelöscht werden.</p>
        </div>
        <div class="button-row archive-toolbar-actions">
          ${this._canEdit() ? `<button class="primary-button" type="button" data-action="archive-upload"><ha-icon icon="mdi:file-upload-outline"></ha-icon> Datei auswählen</button><button class="secondary-button" type="button" data-action="archive-camera"><ha-icon icon="mdi:camera-outline"></ha-icon> Kamera</button><button class="secondary-button" type="button" data-action="archive-clipboard"><ha-icon icon="mdi:content-paste"></ha-icon> Zwischenablage</button>` : ""}
        </div>
      </section>

      <section class="stat-grid archive-stats" aria-label="Dokumenten- und Kostenübersicht">
        ${this._statCard("mdi:file-document-multiple-outline", Number(stats.document_count || 0), "Dokumente")}
        ${this._statCard("mdi:cash-multiple", Number(stats.expense_count || 0), "Ausgaben")}
        ${this._statCard("mdi:checkbox-marked-circle-auto-outline", Number(stats.todo_open_count || 0), todoTiming.urgent ? `${todoTiming.urgent} heute / überfällig` : "geplante Aufgaben")}
        ${this._statCard("mdi:database-outline", this._formatBytes(stats.storage_bytes || 0), "privat gespeichert")}
      </section>

      <section class="panel-card archive-summary-card">
        <div class="section-heading compact"><div><span class="eyebrow">Reisekosten</span><h2>${escapeHtml(this._archiveTotalText())}</h2></div>${this._canEdit() ? `<button class="secondary-button compact-button" type="button" data-action="archive-add-expense"><ha-icon icon="mdi:cash-plus"></ha-icon> Ausgabe</button>` : ""}</div>
        <p class="muted">Beträge werden je Währung getrennt summiert. Umrechnungskurse werden nicht geraten.</p>
      </section>

      <section class="panel-card archive-section">
        <div class="section-heading"><div><span class="eyebrow">Originale & Buchungen</span><h2>Reisedokumente</h2></div><span class="section-count">${documents.length}</span></div>
        ${documents.length ? `<div class="archive-card-grid">${documents.map((item) => this._renderArchiveDocumentCard(item)).join("")}</div>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:file-document-plus-outline"></ha-icon><h2>Noch keine Reisedokumente</h2><p>Lade PDFs, Tickets, Buchungsbestätigungen oder Bilder hoch. Der Assistent kann sie auswerten und Reisehinweise vorschlagen.</p></div>`}
      </section>

      <section class="panel-card archive-section">
        <div class="section-heading"><div><span class="eyebrow">Kostenbuch</span><h2>Ausgaben</h2></div>${this._canEdit() ? `<button class="secondary-button compact-button" type="button" data-action="archive-add-expense"><ha-icon icon="mdi:plus"></ha-icon> Manuell</button>` : ""}</div>
        ${expenses.length ? `<div class="archive-list">${expenses.map((item) => this._renderArchiveExpenseCard(item)).join("")}</div>` : `<p class="muted">Noch keine Ausgaben erfasst.</p>`}
      </section>

      <section class="panel-card archive-section">
        <div class="section-heading"><div><span class="eyebrow">Durchführung</span><h2>Tagesaufgaben</h2></div>${this._canEdit() ? `<button class="secondary-button compact-button" type="button" data-action="archive-add-todo"><ha-icon icon="mdi:plus"></ha-icon> Aufgabe</button>` : ""}</div>
        ${todos.length ? `<div class="archive-list">${todos.map((item) => this._renderArchiveTodoCard(item)).join("")}</div>` : `<p class="muted">Noch keine Aufgaben aus Buchungen oder manuell erfasst.</p>`}
      </section>`;
  }

  _renderArchiveDocumentCard(item) {
    const offline = this._offlineDocumentIds.has(item.id);
    const canAnalyze = Boolean(this._data?.settings?.document_analysis_enabled && this._data?.settings?.document_analysis_configured && item.file_retained);
    const status = archiveStatusLabels[item.status] || item.status || "Neu";
    const type = archiveDocumentTypeLabels[item.document_type] || item.document_type || "Dokument";
    const warningCount = Array.isArray(item.warnings) ? item.warnings.length : 0;
    return `<article class="archive-document-card">
      <div class="archive-card-icon"><ha-icon icon="${item.mime_type === "application/pdf" ? "mdi:file-pdf-box" : item.mime_type?.startsWith("image/") ? "mdi:file-image-outline" : "mdi:file-document-outline"}"></ha-icon></div>
      <div class="archive-card-main">
        <div class="archive-card-heading"><div><span>${escapeHtml(type)}</span><h3>${escapeHtml(item.title || item.original_filename || "Reisedokument")}</h3></div><span class="status-badge ${item.status === "confirmed" || item.status === "file_removed" ? "status-success" : item.status === "draft" ? "status-warning" : "status-info"}">${escapeHtml(status)}</span></div>
        <p>${escapeHtml(item.summary || "Noch keine bestätigte Zusammenfassung.")}</p>
        <div class="archive-card-meta"><span><ha-icon icon="mdi:map-marker-outline"></ha-icon>${escapeHtml(this._archiveLinkLabel(item))}</span><span><ha-icon icon="mdi:file-outline"></ha-icon>${escapeHtml(this._formatBytes(item.size_bytes))}</span>${item.provider ? `<span><ha-icon icon="mdi:office-building-outline"></ha-icon>${escapeHtml(item.provider)}</span>` : ""}${offline ? `<span><ha-icon icon="mdi:cellphone-check"></ha-icon>Auf diesem Gerät</span>` : ""}${warningCount ? `<span class="warning-text"><ha-icon icon="mdi:alert-outline"></ha-icon>${warningCount} Hinweise</span>` : ""}</div>
        <div class="button-row archive-card-actions">
          ${item.file_retained ? `<button class="secondary-button compact-button" type="button" data-action="archive-open" data-document-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:open-in-new"></ha-icon> Öffnen</button>` : ""}
          ${this._canEdit() && canAnalyze ? `<button class="secondary-button compact-button" type="button" data-action="archive-analyze" data-document-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:text-recognition"></ha-icon> Analysieren</button>` : ""}
          ${this._canEdit() ? `${!["confirmed", "file_removed"].includes(item.status) ? `<button class="secondary-button compact-button" type="button" data-action="archive-review" data-document-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:clipboard-check-outline"></ha-icon> Prüfen</button>` : ""}<button class="icon-button" type="button" data-action="archive-edit-document" data-document-id="${escapeHtml(item.id)}" title="Metadaten bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button>` : ""}
          ${item.file_retained ? (offline ? `<button class="icon-button" type="button" data-action="archive-uncache" data-document-id="${escapeHtml(item.id)}" title="Lokale Kopie entfernen"><ha-icon icon="mdi:cellphone-remove"></ha-icon></button>` : `<button class="icon-button" type="button" data-action="archive-cache" data-document-id="${escapeHtml(item.id)}" title="Auf diesem Gerät speichern"><ha-icon icon="mdi:cellphone-arrow-down"></ha-icon></button>`) : ""}
          ${this._canEdit() ? `<button class="icon-button danger-text" type="button" data-action="archive-delete-document" data-document-id="${escapeHtml(item.id)}" title="Dokument löschen"><ha-icon icon="mdi:delete-outline"></ha-icon></button>` : ""}
        </div>
      </div>
    </article>`;
  }

  _renderArchiveExpenseCard(item) {
    const category = archiveExpenseCategoryLabels[item.category] || item.category || "Sonstiges";
    return `<article class="archive-row">
      <div class="archive-row-icon"><ha-icon icon="mdi:cash"></ha-icon></div>
      <div class="archive-row-copy"><strong>${escapeHtml(item.merchant || category)}</strong><span>${escapeHtml(category)} · ${escapeHtml(item.date ? this._formatDate(item.date) : "Datum offen")} · ${escapeHtml(this._archiveLinkLabel(item))}</span>${item.notes ? `<small>${escapeHtml(item.notes)}</small>` : ""}</div>
      <div class="archive-row-value"><strong>${escapeHtml(this._formatMoney(item.amount, item.currency))}</strong><span>${escapeHtml(archiveStatusLabels[item.status] || item.status || "")}</span></div>
      ${this._canEdit() ? `<div class="archive-row-actions"><button class="icon-button" type="button" data-action="archive-edit-expense" data-expense-id="${escapeHtml(item.id)}" title="Ausgabe bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button><button class="icon-button danger-text" type="button" data-action="archive-delete-expense" data-expense-id="${escapeHtml(item.id)}" title="Ausgabe löschen"><ha-icon icon="mdi:delete-outline"></ha-icon></button></div>` : ""}
    </article>`;
  }

  _renderArchiveTodoCard(item) {
    const done = item.status === "done";
    const dueState = this._todoDueState(item);
    const dueLabel = this._todoDueLabel(item);
    const due = item.due_at ? this._formatTimestamp(item.due_at) : "Ohne Frist";
    return `<article class="archive-row archive-todo-row ${done ? "done" : ""} due-${escapeHtml(dueState)}">
      <button class="todo-check" type="button" data-action="archive-toggle-todo" data-todo-id="${escapeHtml(item.id)}" ${this._canEdit() ? "" : "disabled"} aria-label="${done ? "Aufgabe wieder öffnen" : "Aufgabe erledigen"}"><ha-icon icon="${done ? "mdi:checkbox-marked-circle" : "mdi:checkbox-blank-circle-outline"}"></ha-icon></button>
      <div class="archive-row-copy"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(due)} · ${escapeHtml(this._archiveLinkLabel(item))}</span>${item.notes ? `<small>${escapeHtml(item.notes)}</small>` : ""}</div>
      <div class="todo-badges">${dueLabel ? `<span class="due-badge due-${escapeHtml(dueState)}">${escapeHtml(dueLabel)}</span>` : ""}<span class="priority-badge priority-${escapeHtml(item.priority || "normal")}">${escapeHtml(item.priority === "high" ? "Wichtig" : item.priority === "low" ? "Niedrig" : "Normal")}</span></div>
      ${this._canEdit() ? `<div class="archive-row-actions"><button class="icon-button" type="button" data-action="archive-edit-todo" data-todo-id="${escapeHtml(item.id)}" title="Aufgabe bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button><button class="icon-button danger-text" type="button" data-action="archive-delete-todo" data-todo-id="${escapeHtml(item.id)}" title="Aufgabe löschen"><ha-icon icon="mdi:delete-outline"></ha-icon></button></div>` : ""}
    </article>`;
  }


  _renderDayArchivePanel(day, records = this._archiveRecordsForDay(day.id)) {
    const documents = records.documents || [];
    const expenses = records.expenses || [];
    const todos = records.todos || [];
    const openTodos = todos.filter((item) => item.status === "open");
    const totals = {};
    for (const item of expenses) {
      if (item.status === "cancelled") continue;
      const currency = item.currency || "EUR";
      totals[currency] = (totals[currency] || 0) + Number(item.amount || 0);
    }
    const totalText = Object.entries(totals).map(([currency, amount]) => this._formatMoney(amount, currency)).join(" · ");
    return `<section class="panel-card day-archive-panel">
      <div class="section-heading compact"><div><span class="eyebrow">Heute benötigt</span><h2>Dokumente & Aufgaben</h2></div><div class="button-row">${this._canEdit() ? `<button class="secondary-button compact-button" type="button" data-action="archive-day-attach" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:paperclip"></ha-icon> Dokument</button><button class="secondary-button compact-button" type="button" data-action="archive-add-todo" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:checkbox-marked-circle-plus-outline"></ha-icon> Aufgabe</button><button class="secondary-button compact-button" type="button" data-action="archive-add-expense" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:cash-plus"></ha-icon> Ausgabe</button>` : ""}</div></div>
      ${documents.length || openTodos.length || expenses.length ? `<div class="day-archive-grid">
        <div><span class="archive-mini-heading">Dokumente</span>${documents.length ? documents.map((item) => `<button class="archive-mini-item" type="button" data-action="${item.file_retained ? "archive-open" : "archive-edit-document"}" data-document-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:file-document-outline"></ha-icon><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(archiveDocumentTypeLabels[item.document_type] || "Reisedokument")}</small></span></button>`).join("") : `<p class="muted">Keine Dokumente.</p>`}</div>
        <div><span class="archive-mini-heading">Offene Aufgaben</span>${openTodos.length ? openTodos.map((item) => `<button class="archive-mini-item todo" type="button" data-action="archive-toggle-todo" data-todo-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:checkbox-blank-circle-outline"></ha-icon><span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.due_at ? this._formatTimestamp(item.due_at) : "Ohne Frist")}</small></span></button>`).join("") : `<p class="muted">Keine offenen Aufgaben.</p>`}</div>
        <div><span class="archive-mini-heading">Tageskosten</span><strong class="archive-day-total">${escapeHtml(totalText || "Noch keine")}</strong><small>${expenses.length} ${expenses.length === 1 ? "Eintrag" : "Einträge"}</small></div>
      </div>` : `<div class="empty-inline"><ha-icon icon="mdi:file-check-outline"></ha-icon><div><strong>Für diesen Tag ist noch nichts hinterlegt</strong><span>Buchungen, Tickets, Ausgaben und Aufgaben können direkt dem Tag zugeordnet werden.</span></div></div>`}
    </section>`;
  }

  _renderStopArchiveSummary(day, stop) {
    const sourceDayId = stop._inherited ? stop._sourceDayId : day.id;
    const records = this._archiveRecordsForStop(sourceDayId, stop.id);
    const openTodos = records.todos.filter((item) => item.status === "open");
    const count = records.documents.length + records.expenses.length + openTodos.length;
    return `<div class="stop-archive-summary">
      ${count ? `<div class="stop-archive-counts">${records.documents.length ? `<span><ha-icon icon="mdi:file-document-outline"></ha-icon>${records.documents.length}</span>` : ""}${records.expenses.length ? `<span><ha-icon icon="mdi:cash"></ha-icon>${records.expenses.length}</span>` : ""}${openTodos.length ? `<span><ha-icon icon="mdi:checkbox-blank-circle-outline"></ha-icon>${openTodos.length}</span>` : ""}</div>` : ""}
      ${this._canEdit() && !stop._inherited ? `<div class="button-row stop-archive-actions"><button class="text-button" type="button" data-action="archive-stop-attach" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:paperclip"></ha-icon> Dokument</button><button class="text-button" type="button" data-action="archive-add-expense" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:cash-plus"></ha-icon> Ausgabe</button><button class="text-button" type="button" data-action="archive-add-todo" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:checkbox-marked-circle-plus-outline"></ha-icon> Aufgabe</button></div>` : ""}
    </div>`;
  }


  _renderOverview() {
    const summary = this._data.summary;
    const trip = summary.trip;
    const nextDay = summary.next_day;
    const handoffs = this._data.handoffs;
    const settings = this._data.settings;
    const heroMedia = this._tripImages(1)[0];
    return `
      ${this._renderReadOnlyNotice()}
      <section class="hero-card ${heroMedia ? "with-image" : ""}">
        ${heroMedia ? `<div class="hero-image">${this._renderDestinationImage(heroMedia, { compact: false })}</div>` : ""}
        <div class="hero-copy">
          <span class="eyebrow">${this._data.selected_is_active ? "Aktive Reise" : "Ausgewählte Reise"}</span>
          <h2>${escapeHtml(trip.title)}</h2>
          <p>${escapeHtml(trip.notes || "Noch keine Reisenotiz hinterlegt.")}</p>
          <div class="hero-meta">
            <span><ha-icon icon="mdi:calendar-range"></ha-icon>${escapeHtml(trip.start_date || "offen")} – ${escapeHtml(trip.end_date || "offen")}</span>
            <span><ha-icon icon="mdi:flag-outline"></ha-icon>${escapeHtml(this._statusLabel(trip.status))}</span>
          </div>
          ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="edit-trip"><ha-icon icon="mdi:pencil-outline"></ha-icon> Reise bearbeiten</button>` : ""}
        </div>
      </section>

      <section class="stat-grid" aria-label="Reiseübersicht">
        ${this._statCard("mdi:calendar-range", summary.day_count, "Reisetage")}
        ${this._statCard("mdi:map-marker-multiple", summary.stop_count, "Stopps")}
        ${this._statCard("mdi:road-variant", summary.total_distance_km != null ? `${summary.total_distance_km} km` : "— km", "geplant")}
        ${this._statCard("mdi:inbox-arrow-down", handoffs.total, "Übergaben")}
      </section>

      <section class="panel-card">
        <div class="section-heading">
          <div>
            <span class="eyebrow">Als Nächstes</span>
            <h2>${nextDay ? escapeHtml(nextDay.title) : "Noch kein Reisetag geplant"}</h2>
          </div>
          <ha-icon icon="mdi:map-marker-distance"></ha-icon>
        </div>
        ${nextDay ? `
          <div class="next-day-grid">
            <div><span>Datum</span><strong>${escapeHtml(this._formatDate(nextDay.date))}</strong></div>
            <div><span>Route</span><strong>${escapeHtml(nextDay.start || "?")} → ${escapeHtml(nextDay.end || "?")}</strong></div>
            <div><span>Stopps</span><strong>${nextDay.stop_count || 0}</strong></div>
          </div>
        ` : `<p class="muted">Lege den ersten Reisetag direkt im Panel an oder plane ihn später über Gemini.</p>`}
        <div class="button-row">
          ${this._canEdit() ? `<button class="primary-button" type="button" data-action="add-day"><ha-icon icon="mdi:calendar-plus"></ha-icon> Reisetag hinzufügen</button>` : ""}
          <button class="secondary-button" type="button" data-tab="day-route"><ha-icon icon="mdi:map-clock-outline"></ha-icon> Tagesroute</button>
          <button class="secondary-button" type="button" data-tab="total-route"><ha-icon icon="mdi:map-marker-path"></ha-icon> Gesamtroute</button>
        </div>
      </section>

      <section class="panel-card">
        <div class="section-heading compact">
          <div><span class="eyebrow">Zugriff</span><h2>${escapeHtml(this._statusLabel(this._data.capabilities?.role || "viewer"))}</h2></div>
          <span class="status-dot success"></span>
        </div>
        <div class="settings-list">
          ${this._settingRow("Routen bearbeiten", this._data.capabilities?.can_edit)}
          ${this._settingRow("Übergaben verarbeiten", this._data.capabilities?.can_approve)}
          ${this._valueRow("Standardrolle Nicht-Admins", this._statusLabel(settings.non_admin_role || "viewer"))}
        </div>
      </section>

      <section class="panel-card">
        <div class="section-heading compact">
          <div><span class="eyebrow">System</span><h2>Roadplanner ${escapeHtml(this._data.integration_version)}</h2></div>
          <span class="status-dot success"></span>
        </div>
        <div class="settings-list">
          ${this._settingRow("Übergabeordner automatisch prüfen", settings.auto_scan_handoffs)}
          ${this._settingRow("ChangeSets automatisch anwenden", settings.auto_apply_changesets)}
          ${this._settingRow("Externe Google-Drive-Bridge", settings.handoff_webhook_enabled)}
          ${this._settingRow("Straßenrouting", settings.routing_configured)}
          ${settings.routing_configured ? this._valueRow("Routing", `${settings.routing_provider || "osrm"} · ${settings.routing_profile || "driving"}`) : ""}
        </div>
        <div class="button-row">
          ${this._canAdmin() ? `<button class="secondary-button" type="button" data-action="backup"><ha-icon icon="mdi:backup-restore"></ha-icon> Sicherung erstellen</button>` : ""}
          ${this._data.capabilities?.can_approve ? `<button class="secondary-button" type="button" data-action="scan-handoffs"><ha-icon icon="mdi:folder-refresh-outline"></ha-icon> Übergaben prüfen</button>` : ""}
        </div>
      </section>
    `;
  }

  _statCard(icon, value, label) {
    return `<article class="stat-card">
      <ha-icon icon="${icon}"></ha-icon>
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
    </article>`;
  }

  _settingRow(label, enabled) {
    return `<div class="setting-row">
      <span>${escapeHtml(label)}</span>
      <span class="state-pill ${enabled ? "on" : "off"}">${enabled ? "Aktiv" : "Aus"}</span>
    </div>`;
  }

  _valueRow(label, value) {
    return `<div class="setting-row">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "—")}</strong>
    </div>`;
  }

  _renderDayRoute() {
    const days = this._data.days.days || [];
    const day = this._findDay(this._selectedDayId) || days[0];
    if (!day) {
      return `${this._renderReadOnlyNotice()}<div class="empty-state"><ha-icon icon="mdi:map-clock-outline"></ha-icon><h2>Noch keine Tagesroute</h2><p>Lege zuerst einen Reisetag an.</p>${this._canEdit() ? '<button class="primary-button" type="button" data-action="add-day">Reisetag anlegen</button>' : ""}</div>`;
    }
    const routeStops = this._effectiveDayStops(day);
    const points = this._dayRoutePoints(day);
    const routePaths = this._routingSegmentPaths(day);
    const dayImages = [];
    const dayMedia = this._mediaFrom(day);
    if (dayMedia) dayImages.push({ ...dayMedia, context: day.title });
    for (const stop of day.stops || []) {
      const media = this._mediaFrom(stop);
      if (media) dayImages.push({ ...media, context: stop.name });
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
    const routingNotices = [];
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
          <p>${escapeHtml(this._formatDate(day.date))} · ${escapeHtml(effectiveStart)} → ${escapeHtml(day.end || routeStops.at(-1)?.name || "?")}</p>
        </div>
        <label class="day-select"><span>Reisetag</span><select data-action="select-day">${days.map((item) => `<option value="${escapeHtml(item.id)}" ${item.id === day.id ? "selected" : ""}>${item.sequence}. ${escapeHtml(item.title)}</option>`).join("")}</select></label>
      </section>

      <section class="route-layout">
        <div class="route-main">
          ${this._renderMap("day-route-map", points, day.title, routePaths)}
          ${this._renderRouteFlow(day)}
        </div>
        <aside class="day-facts panel-card">
          <span class="eyebrow">Fahrdaten</span>
          <div class="facts-grid">
            <div><span>Autofahrt</span><strong>${day.distance_km != null ? `${escapeHtml(day.distance_km)} km` : "—"}</strong></div>
            <div><span>Fahrzeit</span><strong>${escapeHtml(drive || "—")}</strong></div>
            <div><span>Fähre</span><strong>${ferryDistanceKm > 0 ? `${escapeHtml(ferryDistanceKm.toFixed(1))} km` : "—"}</strong></div>
            <div><span>Routenpunkte</span><strong>${routeStops.length}</strong></div>
            <div><span>Routing</span><strong>${escapeHtml(routeStatus)}</strong></div>
          </div>
          ${routingNotice}
          ${omittedNavigationStops ? `<div class="notice neutral">Google Maps übernimmt auf Mobilgeräten nur die ersten drei Zwischenstopps. ${omittedNavigationStops} weitere ${omittedNavigationStops === 1 ? "Stopp wird" : "Stopps werden"} in diesem Link ausgelassen.</div>` : ""}
          ${!routingConfigured ? '<div class="notice neutral">Straßenrouting ist in den Roadplanner-Optionen noch nicht aktiviert.</div>' : ""}
          ${day.notes ? `<p class="notes-block">${escapeHtml(day.notes)}</p>` : ""}
          <div class="button-row">
            ${this._canEdit() && routingConfigured ? `<button class="primary-button" type="button" data-action="calculate-day-route" data-day-id="${escapeHtml(day.id)}" data-force="${day.routing ? "true" : "false"}"><ha-icon icon="mdi:routes"></ha-icon>${day.routing ? "Neu berechnen" : "Route berechnen"}</button>` : ""}
            ${this._externalLink(navigationUrl, "Tagesroute in Google Maps", "mdi:google-maps")}
            ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="edit-day" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon> Tag bearbeiten</button><button class="secondary-button" type="button" data-action="add-stop" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon> Stopp</button>` : ""}
          </div>
        </aside>
      </section>

      ${this._renderDayArchivePanel(day, archiveRecords)}
      ${experienceDayMedia.length ? `<section class="panel-card day-experience-album"><div class="section-heading compact"><div><span class="eyebrow">Reiseerinnerungen</span><h2>Fotos dieses Tages</h2></div></div>${this._renderExperienceAlbum(experienceDayMedia, { dayId: day.id, title: day.title || "Tagesalbum" })}</section>` : ""}

      <section class="panel-card image-section">
        <div class="section-heading compact">
          <div><span class="eyebrow">Inspiration</span><h2>Bilder der Tagesziele</h2></div>
          ${this._canEdit() ? `<button class="secondary-button" type="button" data-action="search-day-images" data-day-id="${escapeHtml(day.id)}"><ha-icon icon="mdi:image-search-outline"></ha-icon> Titelbild suchen</button>` : ""}
        </div>
        ${dayImages.length ? this._renderImageGallery(dayImages) : `<div class="empty-inline"><ha-icon icon="mdi:image-outline"></ha-icon><div><strong>Noch keine Zielbilder</strong><span>Bei jedem Stopp kannst du ein Bild aus Wikimedia Commons auswählen oder eine Bild-URL hinterlegen.</span></div></div>`}
      </section>

      <section class="stops-section">
        <div class="section-heading"><div><span class="eyebrow">Ablauf</span><h2>${routeStops.length} Routenpunkte</h2></div></div>
        ${routeStops.length ? `<div class="stop-grid">${routeStops.map((stop, index) => this._renderStopCard(day, stop, index)).join("")}</div>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:map-marker-plus-outline"></ha-icon><h2>Noch keine Stopps</h2><p>Füge Ziele, Fähren, Stellplätze oder Sehenswürdigkeiten hinzu.</p>${this._canEdit() ? `<button class="primary-button" type="button" data-action="add-stop" data-day-id="${escapeHtml(day.id)}">Ersten Stopp hinzufügen</button>` : ""}</div>`}
      </section>
    `;
  }

  _renderMap(id, points, title, paths = [], caption = "") {
    const validPoints = points.filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon));
    const validPaths = (paths || []).map((path) => ({
      title: cleanText(path?.title) || title,
      mode: cleanText(path?.mode) || "driving",
      points: (path?.points || []).filter((point) => Number.isFinite(point.lat) && Number.isFinite(point.lon)),
    })).filter((path) => path.points.length > 1);
    if (!validPoints.length && !validPaths.length) {
      return `<section class="map-card map-unavailable"><div class="map-placeholder"><ha-icon icon="mdi:map-marker-off-outline"></ha-icon><strong>Noch keine Koordinaten</strong><span>Trage bei den Stopps Breiten- und Längengrad ein. Die schematische Route darunter funktioniert auch ohne Koordinaten.</span></div></section>`;
    }
    this._mapModels.set(id, { points: validPoints, paths: validPaths, title });
    const legend = validPoints.slice(0, 30).map((point, index) => `
      <span class="map-key-item"><b>${index + 1}</b>${escapeHtml(point.label || `Punkt ${index + 1}`)}</span>
    `).join("");
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
      ${legend ? `<div class="map-key">${legend}${validPoints.length > 30 ? `<span class="map-key-more">+${validPoints.length - 30} weitere</span>` : ""}</div>` : ""}
      <div class="map-caption"><ha-icon icon="mdi:information-outline"></ha-icon><span>${escapeHtml(caption || defaultCaption)}</span></div>
    </section>`;
  }

  _renderRouteFlow(day) {
    const stops = this._effectiveDayStops(day);
    const nodes = [];
    if (day.start && !stops.some((stop) => stop?._inherited)) {
      nodes.push({ label: day.start, type: "start", icon: "mdi:flag-outline" });
    }
    for (const stop of stops) {
      nodes.push({
        label: stop.name,
        type: stop.type,
        icon: stopIcons[stop.type] || stopIcons.waypoint,
        time: stop._inherited
          ? `Start vom Vortag${stop.departure_time ? ` · Abfahrt ${stop.departure_time}` : ""}`
          : (stop.arrival_time || stop.departure_time),
      });
    }
    if (day.end && (!nodes.length || nodes[nodes.length - 1].label !== day.end)) {
      nodes.push({ label: day.end, type: "end", icon: "mdi:flag-checkered" });
    }
    if (!nodes.length) return "";
    return `<section class="route-flow-card"><span class="eyebrow">Schematischer Tagesablauf</span><div class="route-flow">${nodes.map((node, index) => `<div class="flow-item"><div class="flow-node"><ha-icon icon="${node.icon}"></ha-icon></div><div class="flow-copy"><strong>${escapeHtml(node.label)}</strong><span>${escapeHtml(node.time || this._statusLabel(node.type))}</span></div>${index < nodes.length - 1 ? '<div class="flow-line"></div>' : ""}</div>`).join("")}</div></section>`;
  }

  _renderStopCard(day, stop, index) {
    const inherited = Boolean(stop._inherited);
    const media = this._mediaFrom(stop);
    const experienceMedia = this._experienceMediaForStop(stop.id);
    const experienceCover = experienceMedia.find((item) => item.is_cover) || experienceMedia[0] || null;
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
      ${experienceCover ? `<button type="button" class="stop-experience-cover" data-action="media-open-album" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}" data-media-id="${escapeHtml(experienceCover.id)}"><img src="${escapeHtml(this._safeUrl(experienceCover.thumbnail_url))}" alt="${escapeHtml(experienceCover.caption || experienceCover.name || stop.name)}" loading="lazy"><span><ha-icon icon="mdi:image-multiple"></ha-icon>${experienceMedia.length} ${experienceMedia.length === 1 ? "Foto" : "Fotos"}</span></button>` : media ? this._renderDestinationImage({ ...media, context: stop.name }, { compact: true }) : `<div class="stop-image-placeholder"><ha-icon icon="${stopIcons[stop.type] || stopIcons.waypoint}"></ha-icon><span>${escapeHtml(this._statusLabel(stop.type))}</span></div>`}
      <div class="stop-card-body">
        <div class="stop-card-heading"><span class="sequence-badge">${index + 1}</span><div><h3>${escapeHtml(stop.name)}</h3><span>${escapeHtml(this._statusLabel(stop.type))}${inherited ? " · Start vom Vortag" : ""}</span></div></div>
        ${inherited ? `<div class="inherited-badge"><ha-icon icon="mdi:link-variant"></ha-icon>Derselbe Übernachtungsstopp wie am Vortag</div>` : ""}
        <div class="stop-meta">
          ${time ? `<span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(time)}</span>` : ""}
          ${location.city ? `<span><ha-icon icon="mdi:map-marker-outline"></ha-icon>${escapeHtml(location.city)}${location.country_code ? `, ${escapeHtml(location.country_code)}` : ""}</span>` : ""}
          ${coordinate ? `<span><ha-icon icon="mdi:crosshairs-gps"></ha-icon>${coordinate.lat.toFixed(5)}, ${coordinate.lon.toFixed(5)}</span>` : ""}
        </div>
        ${stop.notes ? `<p>${escapeHtml(stop.notes)}</p>` : ""}
        ${media?.attribution && !experienceCover ? `<div class="attribution">${media.source_url ? `<a href="${escapeHtml(media.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(media.attribution)}</a>` : escapeHtml(media.attribution)}</div>` : ""}
        ${this._renderExperienceAlbum(experienceMedia, { dayId: day.id, stopId: stop.id, compact: true, title: stop.name })}
        ${this._renderStopArchiveSummary(day, stop)}
        ${externalActions ? `<div class="button-row stop-actions">${externalActions}</div>` : ""}
        ${this._canEdit() && !inherited ? `<div class="button-row stop-actions"><button class="secondary-button" type="button" data-action="edit-stop" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon> Bearbeiten</button><button class="secondary-button" type="button" data-action="search-stop-images" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:image-search-outline"></ha-icon> Bild suchen</button>${media ? `<button class="text-button danger-text" type="button" data-action="remove-stop-image" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}">Bild entfernen</button>` : ""}</div>` : ""}
      </div>
    </article>`;
  }

  _renderTotalRoute() {
    const days = this._data.days.days || [];
    const points = this._allRoutePoints();
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
        </div>
      </section>
      ${!routingConfigured ? '<div class="notice neutral">Aktiviere Straßenrouting in den Roadplanner-Optionen, um Kilometer und Fahrzeiten zu berechnen.</div>' : ""}
      ${metrics.stale_day_count ? `<div class="notice warning">${metrics.stale_day_count} gespeicherte ${metrics.stale_day_count === 1 ? "Route ist" : "Routen sind"} nach Änderungen veraltet.</div>` : ""}
      ${metrics.routing_gap_count ? `<div class="notice warning">${metrics.routing_gap_count} ${metrics.routing_gap_count === 1 ? "Routenabschnitt ist" : "Routenabschnitte sind"} noch unvollständig modelliert. Eine Fähre benötigt Abfahrts- und Ankunftsterminal als getrennte Stopps.</div>` : ""}
      ${this._renderMap("total-route-map", points, this._data.summary.trip.title, paths)}
      ${this._renderTripRouteGraphic(days)}
      ${images.length ? `<section class="panel-card image-section"><div class="section-heading compact"><div><span class="eyebrow">Reiseeindrücke</span><h2>Geplante Ziele</h2></div></div>${this._renderImageGallery(images)}</section>` : ""}
      ${days.length ? `<section class="total-route-list"><div class="section-heading"><div><span class="eyebrow">Etappen</span><h2>Reiseverlauf</h2></div></div>${days.map((day) => this._renderTotalDay(day)).join("")}</section>` : `<div class="empty-state"><ha-icon icon="mdi:map-marker-path"></ha-icon><h2>Die Gesamtroute ist noch leer</h2></div>`}
      ${this._data.days.has_more ? `<div class="notice warning">Im Panel werden maximal 60 Reisetage angezeigt. Weitere Tage bleiben im Roadbook erhalten.</div>` : ""}
    `;
  }

  _renderTripRouteGraphic(days) {
    if (!days.length) return "";
    return `<section class="panel-card trip-route-graphic">
      <div class="section-heading compact">
        <div><span class="eyebrow">Reiseband</span><h2>Alle Etappen auf einen Blick</h2></div>
        <ha-icon icon="mdi:route"></ha-icon>
      </div>
      <div class="journey-track" role="list">
        ${days.map((day, index) => `
          <button type="button" class="journey-node" role="listitem" data-action="select-day-card" data-day-id="${escapeHtml(day.id)}">
            <span class="journey-dot">${day.sequence}</span>
            <span class="journey-copy">
              <small>${escapeHtml(this._formatDate(day.date))}</small>
              <strong>${escapeHtml(day.title)}</strong>
              <span>${escapeHtml(day.start || "?")} → ${escapeHtml(day.end || "?")}</span>
            </span>
          </button>
          ${index < days.length - 1 ? '<span class="journey-line" aria-hidden="true"></span>' : ""}
        `).join("")}
      </div>
    </section>`;
  }

  _renderTotalDay(day) {
    const media = this._mediaFrom(day) || (day.stops || []).map((stop) => this._mediaFrom(stop)).find(Boolean);
    const drive = this._formatDriveMinutes(day.drive_minutes);
    const routeStatus = this._routeStatusLabel(day);
    return `<article class="total-day-card" data-action="select-day-card" data-day-id="${escapeHtml(day.id)}">
      <div class="total-day-sequence"><span>${day.sequence}</span></div>
      ${media ? `<div class="total-day-image">${this._renderDestinationImage({ ...media, context: day.title }, { compact: true })}</div>` : ""}
      <div class="total-day-copy"><span>${escapeHtml(this._formatDate(day.date))}</span><h3>${escapeHtml(day.title)}</h3><p>${escapeHtml(this._effectiveDayStart(day))} → ${escapeHtml(day.end || day.stops?.at(-1)?.name || "?")}</p><div>${[day.distance_km != null ? `${day.distance_km} km` : "", drive, `${day.stop_count || 0} Stopps`, routeStatus].filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div></div>
      <ha-icon class="chevron" icon="mdi:chevron-right"></ha-icon>
    </article>`;
  }

  _renderImageGallery(images) {
    return `<div class="image-gallery">${images.map((image) => `<figure class="gallery-item">${this._renderDestinationImage(image, { compact: false })}<figcaption><strong>${escapeHtml(image.context || image.alt)}</strong>${image.attribution ? `<span>${image.source_url ? `<a href="${escapeHtml(image.source_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(image.attribution)}</a>` : escapeHtml(image.attribution)}</span>` : ""}</figcaption></figure>`).join("")}</div>`;
  }

  _renderDestinationImage(image, { compact = false } = {}) {
    const url = this._safeUrl(image?.image_url);
    if (!url) return "";
    return `<div class="destination-image ${compact ? "compact" : ""}"><img data-destination-image loading="lazy" decoding="async" referrerpolicy="no-referrer" src="${escapeHtml(url)}" alt="${escapeHtml(image.alt || image.context || "Reiseziel")}"><div class="image-fallback"><ha-icon icon="mdi:image-off-outline"></ha-icon><span>Bild nicht verfügbar</span></div></div>`;
  }

  _renderTrips() {
    const trips = this._data.trips?.trips || [];
    return `<section class="toolbar-card"><div><span class="eyebrow">Roadbook</span><h2>Alle Reisen</h2><p>Andere Reisen lassen sich ansehen, ohne die aktive Reise zu wechseln.</p></div></section>
      <section class="trip-grid">${trips.map((trip) => this._renderTripCard(trip)).join("")}</section>`;
  }

  _renderTripCard(trip) {
    if (!trip.valid) {
      return `<article class="trip-card invalid"><div class="trip-card-placeholder"><ha-icon icon="mdi:alert-circle-outline"></ha-icon></div><div class="trip-card-body"><span class="eyebrow">Ungültige Reise</span><h3>${escapeHtml(trip.id)}</h3><p>${escapeHtml(trip.error || "Die Reisedaten konnten nicht gelesen werden.")}</p></div></article>`;
    }
    const media = trip.cover_image;
    return `<article class="trip-card ${trip.active ? "active" : ""} ${trip.id === this._selectedTripId ? "selected" : ""}">
      ${media?.image_url ? this._renderDestinationImage({ ...media, context: trip.title }, { compact: true }) : `<div class="trip-card-placeholder"><ha-icon icon="mdi:map-outline"></ha-icon></div>`}
      <div class="trip-card-body"><div class="trip-title-row"><div><span class="eyebrow">${trip.active ? "Aktive Reise" : "Gespeicherte Reise"}</span><h3>${escapeHtml(trip.title)}</h3></div>${trip.active ? '<span class="status-badge success">Aktiv</span>' : ""}</div><p>${escapeHtml(trip.start_date || "offen")} – ${escapeHtml(trip.end_date || "offen")}</p><div class="trip-stats"><span>${trip.day_count} Tage</span><span>${trip.stop_count} Stopps</span><span>${trip.total_distance_km != null ? `${trip.total_distance_km} km` : "— km"}</span><span>Rev. ${trip.revision}</span></div><div class="button-row"><button class="secondary-button" type="button" data-action="view-trip" data-trip-id="${escapeHtml(trip.id)}"><ha-icon icon="mdi:eye-outline"></ha-icon> Ansehen</button>${!trip.active && this._canActivate() ? `<button class="primary-button" type="button" data-action="activate-trip" data-trip-id="${escapeHtml(trip.id)}"><ha-icon icon="mdi:check-circle-outline"></ha-icon> Aktivieren</button>` : ""}</div></div>
    </article>`;
  }

  _renderHandoffs() {
    const handoffs = this._data.handoffs?.handoffs || [];
    const inactive = !this._data.selected_is_active;
    return `${this._renderReadOnlyNotice()}<section class="toolbar-card"><div><span class="eyebrow">ChangeSets</span><h2>Übergabepostfach</h2><p>${inactive ? "Übergaben werden für die ausgewählte Reise angezeigt; zum Anwenden muss sie aktiv sein." : "Vorschläge aus Gemini, Google Drive und anderen Assistenten."}</p></div>${this._data.capabilities?.can_approve ? `<button class="secondary-button" type="button" data-action="scan-handoffs"><ha-icon icon="mdi:folder-refresh-outline"></ha-icon> Ordner prüfen</button>` : ""}</section>${handoffs.length ? `<section class="handoff-list">${handoffs.map((handoff) => this._renderHandoff(handoff)).join("")}</section>` : `<div class="empty-state"><ha-icon icon="mdi:inbox-outline"></ha-icon><h2>Keine offenen Übergaben</h2><p>Neue ChangeSets erscheinen nach dem Ordnerscan oder über die Google-Drive-Bridge.</p></div>`}`;
  }

  _renderHandoff(handoff) {
    const conflict = handoff.base_revision !== this._currentRevision();
    const operations = Object.entries(handoff.operation_counts || {})
      .map(([name, count]) => `${count}× ${operationLabels[name] || name}`)
      .join(" · ");
    const canApply = this._canApprove()
      && this._data?.selected_is_active
      && !conflict;
    return `<article class="handoff-card"><div class="handoff-heading"><div><span class="eyebrow">${escapeHtml(handoff.source || "extern")}</span><h3>${escapeHtml(handoff.title || handoff.id)}</h3><p>${escapeHtml(handoff.preview || "")}</p></div><span class="status-badge ${this._statusClass(handoff.status)}">${escapeHtml(this._statusLabel(handoff.status))}</span></div><div class="handoff-meta"><span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(this._formatTimestamp(handoff.received_at))}</span><span><ha-icon icon="mdi:format-list-bulleted"></ha-icon>${handoff.operation_count} Operationen</span><span><ha-icon icon="mdi:file-document-refresh"></ha-icon>Basis ${handoff.base_revision}</span><span><ha-icon icon="mdi:help-circle-outline"></ha-icon>${handoff.open_question_count} offene Fragen</span></div>${operations ? `<div class="operation-summary">${escapeHtml(operations)}</div>` : ""}${handoff.last_error ? `<div class="notice danger">${escapeHtml(handoff.last_error)}</div>` : ""}${conflict ? `<div class="notice warning">Die ausgewählte Reise steht auf Revision ${this._currentRevision()}. Die Vorschau zeigt den Konflikt.</div>` : ""}<div class="button-row"><button class="secondary-button" type="button" data-action="preview-handoff" data-handoff-id="${escapeHtml(handoff.id)}"><ha-icon icon="mdi:eye-outline"></ha-icon> Vorschau</button>${this._canApprove() ? `<button class="primary-button" type="button" data-action="apply-handoff" data-handoff-id="${escapeHtml(handoff.id)}" ${canApply ? "" : "disabled"}><ha-icon icon="mdi:check-bold"></ha-icon> Übernehmen</button><button class="text-button danger-text" type="button" data-action="archive-handoff" data-handoff-id="${escapeHtml(handoff.id)}">Ablehnen</button>` : ""}</div></article>`;
  }

  _renderToast() {
    return `<div class="toast ${this._toast.type}" role="status"><ha-icon icon="${this._toast.type === "error" ? "mdi:alert-circle" : "mdi:check-circle"}"></ha-icon><span>${escapeHtml(this._toast.message)}</span></div>`;
  }

  _renderDialog() {
    let body = "";
    if (this._dialog.type === "trip") body = this._renderTripForm(this._dialog);
    else if (this._dialog.type === "day") body = this._renderDayForm(this._dialog);
    else if (this._dialog.type === "stop") body = this._renderStopForm(this._dialog);
    else if (this._dialog.type === "confirm") body = this._renderConfirmDialog(this._dialog);
    else if (this._dialog.type === "handoff-preview") body = this._renderHandoffPreview(this._dialog);
    else if (this._dialog.type === "image-search") body = this._renderImageSearch(this._dialog);
    else if (this._dialog.type === "assistant-draft") body = this._renderAssistantDraftDialog(this._dialog);
    else if (this._dialog.type === "assistant-diagnostics") body = this._renderAssistantDiagnostics(this._dialog);
    else if (this._dialog.type === "action-error") body = this._renderActionErrorDialog(this._dialog);
    else if (this._dialog.type === "archive-document-review") body = this._renderArchiveDocumentReview(this._dialog);
    else if (this._dialog.type === "archive-document-edit") body = this._renderArchiveDocumentEdit(this._dialog);
    else if (this._dialog.type === "archive-expense") body = this._renderArchiveExpenseDialog(this._dialog);
    else if (this._dialog.type === "archive-todo") body = this._renderArchiveTodoDialog(this._dialog);
    else if (this._dialog.type === "archive-paste-text") body = this._renderArchivePasteText(this._dialog);
    else if (this._dialog.type === "attachment-purpose") body = this._renderAttachmentPurpose(this._dialog);
    else if (this._dialog.type === "universal-import-review") body = this._renderUniversalImportReview(this._dialog);
    else if (this._dialog.type === "onedrive-setup") body = this._renderOneDriveSetup(this._dialog);
    else if (this._dialog.type === "onedrive-auth") body = this._renderOneDriveAuth(this._dialog);
    else if (this._dialog.type === "media-edit") body = this._renderMediaEdit(this._dialog);
    else if (this._dialog.type === "media-gallery") body = this._renderMediaGallery(this._dialog);
    return `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-label="Roadplanner Dialog">${body}</section></div>`;
  }

  _renderActionErrorDialog(dialog) {
    const requestLine = dialog.requestId
      ? `<div class="action-error-request"><span>Anfrage</span><code>${escapeHtml(dialog.requestId)}</code></div>`
      : "";
    return `${this._renderModalHeader(dialog.title || "Roadplanner-Aktion fehlgeschlagen", "Die Meldung bleibt geöffnet, bis du sie schließt.")}
      <div class="action-error-body">
        <div class="action-error-icon"><ha-icon icon="mdi:alert-circle-outline"></ha-icon></div>
        <div><p>${escapeHtml(dialog.message || "Unbekannter Roadplanner-Fehler")}</p>${requestLine}</div>
      </div>
      <div class="modal-actions action-error-actions">
        <button class="secondary-button" type="button" data-action="copy-action-error"><ha-icon icon="mdi:content-copy"></ha-icon>Details kopieren</button>
        ${this._actionErrorRetry ? `<button class="secondary-button" type="button" data-action="retry-action-error"><ha-icon icon="mdi:reload"></ha-icon>Erneut versuchen</button>` : ""}
        <button class="primary-button" type="button" data-action="close-dialog">Schließen</button>
      </div>`;
  }

  _renderModalHeader(title, subtitle = "") {
    return `<header class="modal-header"><div><h2>${escapeHtml(title)}</h2>${subtitle ? `<p>${escapeHtml(subtitle)}</p>` : ""}</div><button class="icon-button" type="button" data-action="close-dialog" aria-label="Schließen"><ha-icon icon="mdi:close"></ha-icon></button></header>`;
  }

  _archiveSelect(name, label, value, options, className = "") {
    return `<label class="form-field ${className}"><span>${escapeHtml(label)}</span><select name="${escapeHtml(name)}">${options.map((option) => {
      const raw = typeof option === "string" ? option : option.value;
      const text = typeof option === "string" ? option : option.label;
      return `<option value="${escapeHtml(raw)}" ${String(raw) === String(value ?? "") ? "selected" : ""}>${escapeHtml(text)}</option>`;
    }).join("")}</select></label>`;
  }

  _archiveCheckbox(name, label, checked, hint = "", className = "") {
    return `<label class="checkbox-field ${className}"><input type="checkbox" name="${escapeHtml(name)}" ${checked ? "checked" : ""}><span><strong>${escapeHtml(label)}</strong>${hint ? `<small>${escapeHtml(hint)}</small>` : ""}</span></label>`;
  }

  _archiveDayOptions(selected = "") {
    return [{ value: "", label: "Gesamte Reise / nicht zugeordnet" }, ...(this._data?.days?.days || []).map((day) => ({
      value: day.id,
      label: `${this._formatDate(day.date)} · ${day.title || day.id}`,
    }))];
  }

  _archiveStopOptions(selected = "") {
    const result = [{ value: "", label: "Kein konkreter Stopp" }];
    for (const day of this._data?.days?.days || []) {
      for (const stop of day.stops || []) {
        result.push({ value: `${day.id}::${stop.id}`, label: `${this._formatDate(day.date)} · ${stop.name || stop.id}` });
      }
    }
    return result;
  }

  _archiveSelectedLink(documentItem = {}, analysis = {}) {
    const resolved = analysis.resolved_links && typeof analysis.resolved_links === "object" ? analysis.resolved_links : {};
    const links = resolved.day_ids?.length || resolved.stop_links?.length ? resolved : (documentItem.links || {});
    const stopLink = links.stop_links?.[0] || null;
    return {
      dayId: stopLink?.day_id || links.day_ids?.[0] || "",
      stopRef: stopLink ? `${stopLink.day_id}::${stopLink.stop_id}` : "",
    };
  }

  _renderArchiveDocumentReview(dialog) {
    const documentItem = dialog.document || {};
    const analysis = dialog.analysis && typeof dialog.analysis === "object" ? dialog.analysis : (documentItem.analysis || {});
    const link = this._archiveSelectedLink(documentItem, analysis);
    const extracted = documentItem.extracted && Object.keys(documentItem.extracted).length ? documentItem.extracted : analysis;
    const expense = analysis.expense && typeof analysis.expense === "object" ? analysis.expense : {};
    const todos = Array.isArray(analysis.todos) ? analysis.todos.slice(0, 20) : [];
    const classification = analysis.classification || documentItem.classification || "document";
    const keepOriginal = classification === "expense" ? false : documentItem.keep_original !== false;
    const warningList = [...(analysis.warnings || []), ...(documentItem.warnings || [])].filter(Boolean).slice(0, 20);
    const expenseEnabled = Boolean(expense.present || classification === "expense" || classification === "document_expense");
    const todoFields = todos.length ? todos.map((todo, index) => `<article class="archive-analysis-todo">
      ${this._archiveCheckbox(`todo_${index}_enabled`, "Als Tagesaufgabe speichern", true)}
      ${this._field(`todo_${index}_title`, "Aufgabe", todo.title || "", "text", false, "full")}
      ${this._field(`todo_${index}_due_at`, "Fällig am / Zeitpunkt", todo.due_at || "", "text")}
      ${this._archiveSelect(`todo_${index}_priority`, "Priorität", todo.priority || "normal", [{value:"low",label:"Niedrig"},{value:"normal",label:"Normal"},{value:"high",label:"Wichtig"}])}
      ${this._textarea(`todo_${index}_notes`, "Hinweise", todo.notes || "", "full")}
    </article>`).join("") : `<p class="muted">Aus dem Dokument wurden keine eindeutigen Aufgaben abgeleitet.</p>`;
    return `${this._renderModalHeader("Dokument prüfen", documentItem.original_filename || "Analyse bestätigen")}
      <form data-form="archive-document-review" data-document-id="${escapeHtml(documentItem.id || "")}" data-todo-count="${todos.length}" class="form-grid archive-review-form">
        ${warningList.length ? `<div class="notice warning full"><ha-icon icon="mdi:alert-outline"></ha-icon><div><strong>Bitte prüfen</strong><span>${warningList.map((item) => escapeHtml(item)).join(" · ")}</span></div></div>` : ""}
        <div class="form-section full"><h3>Einordnung</h3><p>Die Originaldatei bleibt maßgeblich. Bestätige nur Angaben, die du im Dokument nachvollziehen kannst.</p></div>
        ${this._archiveSelect("classification", "Erfassung als", classification, [{value:"document",label:"Reisedokument"},{value:"expense",label:"Nur Ausgabe"},{value:"document_expense",label:"Dokument und Ausgabe"}])}
        ${this._archiveSelect("document_type", "Dokumenttyp", analysis.document_type || documentItem.document_type || "other", Object.entries(archiveDocumentTypeLabels).map(([value,label]) => ({value,label})))}
        ${this._field("title", "Titel", analysis.title || documentItem.title || "", "text", true, "full")}
        ${this._field("provider", "Anbieter", analysis.provider || documentItem.provider || "", "text")}
        ${this._textarea("summary", "Kurzbeschreibung", analysis.summary || documentItem.summary || "", "full")}
        <div class="form-section full"><h3>Zuordnung</h3><p>Das Dokument kann der ganzen Reise, einem Tag oder direkt einem Stopp zugeordnet werden.</p></div>
        ${this._archiveSelect("link_day_id", "Reisetag", link.dayId, this._archiveDayOptions(link.dayId))}
        ${this._archiveSelect("link_stop_ref", "Stopp", link.stopRef, this._archiveStopOptions(link.stopRef))}
        <div class="form-section full"><h3>Erkannte Angaben</h3></div>
        ${this._field("booking_reference", "Buchungs-/Ticketnummer", extracted.booking_reference || "", "text")}
        ${this._field("extracted_status", "Buchungsstatus", extracted.status || "", "text")}
        ${this._field("start_at", "Beginn / Abfahrt", extracted.start_at || "", "text")}
        ${this._field("end_at", "Ende / Ankunft", extracted.end_at || "", "text")}
        ${this._field("check_in", "Check-in", extracted.check_in || "", "text")}
        ${this._field("check_out", "Check-out", extracted.check_out || "", "text")}
        ${this._field("address", "Adresse", extracted.address || "", "text", false, "full")}
        ${this._textarea("required_items", "Benötigte Unterlagen / Dinge (eine Zeile je Eintrag)", (extracted.required_items || []).join("\n"), "full")}
        ${this._textarea("important_notes", "Wichtige Hinweise (eine Zeile je Eintrag)", (extracted.important_notes || []).join("\n"), "full")}
        <div class="form-section full"><h3>Originaldatei</h3></div>
        ${this._archiveCheckbox("keep_original", "Originaldatei behalten", keepOriginal, "Für Tickets, Buchungen und QR-Codes aktivieren. Bei einfachen Kassenbons kann die Datei nach der Erfassung gelöscht werden.", "full")}
        ${this._archiveCheckbox("offline_priority", "Für die Reise auf diesem Gerät vorhalten", documentItem.offline_priority, "Die lokale Kopie wird anschließend über die Dokumentkarte gespeichert.", "full")}
        ${this._archiveCheckbox("sensitive", "Enthält sensible personenbezogene Daten", documentItem.sensitive, "Der Assistent erhält später nur die bestätigten relevanten Felder.", "full")}
        <div class="form-section full"><h3>Ausgabe</h3><p>Eine Ausgabe kann auch gespeichert werden, wenn das Foto oder PDF anschließend gelöscht wird.</p></div>
        ${this._archiveCheckbox("expense_enabled", "Ausgabe im Kostenbuch speichern", expenseEnabled, "Betrag und Kategorie vor dem Speichern prüfen.", "full")}
        ${this._field("expense_amount", "Betrag", expense.amount || "", "number", false, "", "0", "0.01")}
        ${this._field("expense_currency", "Währung", expense.currency || this._data?.settings?.default_currency || "EUR", "text")}
        ${this._field("expense_merchant", "Händler / Anbieter", expense.merchant || analysis.provider || "", "text")}
        ${this._archiveSelect("expense_category", "Kategorie", expense.category || "other", Object.entries(archiveExpenseCategoryLabels).map(([value,label]) => ({value,label})))}
        ${this._field("expense_date", "Datum", expense.date || "", "date")}
        ${this._archiveSelect("expense_status", "Zahlungsstatus", expense.payment_status || "paid", [{value:"planned",label:"Geplant"},{value:"paid",label:"Bezahlt"},{value:"refundable",label:"Erstattbar"},{value:"refunded",label:"Erstattet"},{value:"unknown",label:"Unklar"}])}
        ${this._field("expense_payment_method", "Zahlungsart", expense.payment_method || "", "text")}
        ${this._textarea("expense_notes", "Kostennotiz", expense.notes || "", "full")}
        <div class="form-section full"><h3>Vorgeschlagene Tagesaufgaben</h3><p>Nur aktivierte Aufgaben werden übernommen.</p></div>
        <div class="archive-analysis-todos full">${todoFields}</div>
        ${this._formActions("Bestätigen und speichern")}
      </form>`;
  }

  _renderArchiveDocumentEdit(dialog) {
    const item = dialog.document || {};
    const link = this._archiveSelectedLink(item, {});
    return `${this._renderModalHeader("Dokument bearbeiten", item.original_filename || "Reisedokument")}
      <form data-form="archive-document-edit" data-document-id="${escapeHtml(item.id || "")}" class="form-grid">
        ${this._field("title", "Titel", item.title || "", "text", true, "full")}
        ${this._archiveSelect("document_type", "Dokumenttyp", item.document_type || "other", Object.entries(archiveDocumentTypeLabels).map(([value,label]) => ({value,label})))}
        ${this._field("provider", "Anbieter", item.provider || "", "text")}
        ${this._textarea("summary", "Zusammenfassung", item.summary || "", "full")}
        ${this._archiveSelect("link_day_id", "Reisetag", link.dayId, this._archiveDayOptions(link.dayId))}
        ${this._archiveSelect("link_stop_ref", "Stopp", link.stopRef, this._archiveStopOptions(link.stopRef))}
        ${this._archiveCheckbox("offline_priority", "Wichtig für unterwegs", item.offline_priority, "Markiert das Dokument für eine lokale Gerätekopie.", "full")}
        ${this._archiveCheckbox("sensitive", "Sensible Inhalte", item.sensitive, "Begrenzt die spätere Kontextnutzung.", "full")}
        ${this._formActions("Dokument speichern")}
      </form>`;
  }

  _renderArchiveExpenseDialog(dialog) {
    const item = dialog.expense || {};
    const stopRef = item.day_id && item.stop_id ? `${item.day_id}::${item.stop_id}` : "";
    return `${this._renderModalHeader(dialog.mode === "edit" ? "Ausgabe bearbeiten" : "Ausgabe erfassen", "Kostenbuch der ausgewählten Reise")}
      <form data-form="archive-expense" data-mode="${escapeHtml(dialog.mode || "add")}" data-expense-id="${escapeHtml(item.id || "")}" class="form-grid">
        ${this._field("merchant", "Händler / Anbieter", item.merchant || "", "text", true, "full")}
        ${this._field("amount", "Betrag", item.amount ?? "", "number", true, "", "0", "0.01")}
        ${this._field("currency", "Währung", item.currency || this._data?.settings?.default_currency || "EUR", "text", true)}
        ${this._archiveSelect("category", "Kategorie", item.category || "other", Object.entries(archiveExpenseCategoryLabels).map(([value,label]) => ({value,label})))}
        ${this._field("date", "Datum", item.date || "", "date")}
        ${this._archiveSelect("status", "Status", item.status || "paid", [{value:"planned",label:"Geplant"},{value:"paid",label:"Bezahlt"},{value:"refundable",label:"Erstattbar"},{value:"refunded",label:"Erstattet"},{value:"cancelled",label:"Storniert"},{value:"unknown",label:"Unklar"}])}
        ${this._field("payment_method", "Zahlungsart", item.payment_method || "", "text")}
        ${this._archiveSelect("day_id", "Reisetag", item.day_id || dialog.dayId || "", this._archiveDayOptions(item.day_id || dialog.dayId || ""))}
        ${this._archiveSelect("stop_ref", "Stopp", stopRef || (dialog.dayId && dialog.stopId ? `${dialog.dayId}::${dialog.stopId}` : ""), this._archiveStopOptions(stopRef))}
        ${this._textarea("notes", "Notizen", item.notes || "", "full")}
        ${this._formActions(dialog.mode === "edit" ? "Ausgabe speichern" : "Ausgabe hinzufügen")}
      </form>`;
  }

  _renderArchiveTodoDialog(dialog) {
    const item = dialog.todo || {};
    const stopRef = item.day_id && item.stop_id ? `${item.day_id}::${item.stop_id}` : "";
    return `${this._renderModalHeader(dialog.mode === "edit" ? "Aufgabe bearbeiten" : "Aufgabe hinzufügen", "Tagesaufgaben und Dokumenthinweise")}
      <form data-form="archive-todo" data-mode="${escapeHtml(dialog.mode || "add")}" data-todo-id="${escapeHtml(item.id || "")}" class="form-grid">
        ${this._field("title", "Aufgabe", item.title || "", "text", true, "full")}
        ${this._field("due_at", "Fällig am / Zeitpunkt", item.due_at || "", "text")}
        ${this._archiveSelect("priority", "Priorität", item.priority || "normal", [{value:"low",label:"Niedrig"},{value:"normal",label:"Normal"},{value:"high",label:"Wichtig"}])}
        ${this._archiveSelect("status", "Status", item.status || "open", [{value:"open",label:"Offen"},{value:"done",label:"Erledigt"},{value:"dismissed",label:"Verworfen"}])}
        ${this._archiveSelect("day_id", "Reisetag", item.day_id || dialog.dayId || "", this._archiveDayOptions(item.day_id || dialog.dayId || ""))}
        ${this._archiveSelect("stop_ref", "Stopp", stopRef || (dialog.dayId && dialog.stopId ? `${dialog.dayId}::${dialog.stopId}` : ""), this._archiveStopOptions(stopRef))}
        ${this._textarea("notes", "Hinweise", item.notes || "", "full")}
        ${this._formActions(dialog.mode === "edit" ? "Aufgabe speichern" : "Aufgabe hinzufügen")}
      </form>`;
  }

  _renderArchivePasteText(dialog) {
    return `${this._renderModalHeader("Aus Zwischenablage oder Datei", "PDF, Bild oder Text als Reisedokument beziehungsweise Ausgabe prüfen")}
      <div class="archive-paste-zone full" tabindex="0" data-archive-paste-zone data-archive-drop-zone>
        <ha-icon icon="mdi:content-paste"></ha-icon>
        <div><strong>Hier einfügen</strong><span>Tippe in dieses Feld und nutze Strg+V bzw. ⌘V. Auf Mobilgeräten kannst du die systemeigene Einfügefunktion verwenden.</span></div>
      </div>
      ${dialog.error ? `<div class="notice info full"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Direkter Zugriff nicht möglich</strong><span>${escapeHtml(dialog.error)}</span></div></div>` : ""}
      <div class="button-row full"><button class="secondary-button" type="button" data-action="archive-paste-file"><ha-icon icon="mdi:file-upload-outline"></ha-icon> PDF oder Datei auswählen</button></div>
      <form data-form="archive-paste-text" class="form-grid">
        ${this._textarea("content", "Alternativ Text einfügen", "", "full")}
        ${this._field("filename", "Bezeichnung", `Zwischenablage-${new Date().toISOString().slice(0, 10)}.txt`, "text", true, "full")}
        ${this._formActions("Text hochladen und prüfen")}
      </form>`;
  }


  _renderAttachmentPurpose(dialog) {
    const documentItem = dialog.document || {};
    const filename = documentItem.original_filename || documentItem.title || "Anhang";
    return `${this._renderModalHeader("Anhang verwenden", filename)}
      <div class="attachment-purpose-body">
        <div class="attachment-summary"><ha-icon icon="mdi:file-outline"></ha-icon><div><strong>${escapeHtml(filename)}</strong><span>${escapeHtml(documentItem.mime_type || "Datei")} · ${Number(documentItem.size_bytes || 0).toLocaleString("de-DE")} Bytes</span></div></div>
        <p>Wähle, was Roadplanner mit dem Anhang tun soll. Die Originaldatei liegt bereits sicher im privaten Roadplanner-Archiv.</p>
        <div class="attachment-purpose-grid">
          <button class="attachment-purpose-card" type="button" data-action="attachment-import" data-document-id="${escapeHtml(documentItem.id || "")}"><ha-icon icon="mdi:file-import-outline"></ha-icon><span><strong>Als Reiseplan oder Übergabe</strong><small>Markdown, JSON, GPX, ICS, CSV, PDF oder Bild mit dem aktuellen Roadbook vergleichen.</small></span></button>
          <button class="attachment-purpose-card" type="button" data-action="attachment-document" data-document-id="${escapeHtml(documentItem.id || "")}"><ha-icon icon="mdi:file-document-check-outline"></ha-icon><span><strong>Als Reisedokument</strong><small>Ticket, Buchung, Rechnung oder Beleg analysieren und Tag beziehungsweise Stopp zuordnen.</small></span></button>
        </div>
      </div>
      <div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Später entscheiden</button></div>`;
  }

  _renderUniversalImportReview(dialog) {
    const documentItem = dialog.document || {};
    const item = dialog.importResult || documentItem?.analysis?.universal_import || {};
    const previews = Array.isArray(item.preview_items) ? item.preview_items.slice(0, 100) : [];
    const warnings = Array.isArray(item.warnings) ? item.warnings : [];
    const questions = Array.isArray(item.open_questions) ? item.open_questions : [];
    const status = item.status || "ready";
    const count = Number(item.counts?.operations ?? item.counts?.drafts ?? 0);
    const modeLabel = item.mode === "changeset" ? "Roadplanner-ChangeSet" : "Änderungsvorschläge";
    return `${this._renderModalHeader(item.title || "Import prüfen", documentItem.original_filename || modeLabel)}
      <div class="universal-import-review-body">
        <div class="preview-grid"><div><span>Format</span><strong>${escapeHtml(item.format || "unbekannt")}</strong></div><div><span>Ergebnis</span><strong>${escapeHtml(modeLabel)}</strong></div><div><span>Umfang</span><strong>${count}</strong></div><div><span>Status</span><strong>${escapeHtml({ready:"Bereit",transferred:"Übergeben",discarded:"Verworfen"}[status] || status)}</strong></div></div>
        <section class="import-review-section"><h3>Zusammenfassung</h3><p>${escapeHtml(item.summary || "Keine Zusammenfassung verfügbar.")}</p></section>
        ${warnings.length ? `<div class="notice warning"><ha-icon icon="mdi:alert-outline"></ha-icon><div><strong>Hinweise</strong><span>${warnings.map((entry) => escapeHtml(entry)).join(" · ")}</span></div></div>` : ""}
        ${questions.length ? `<section class="import-review-section"><h3>Offene Fragen</h3><ul>${questions.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("")}</ul></section>` : ""}
        <section class="import-review-section"><h3>Erkannte Inhalte</h3>${previews.length ? `<div class="import-preview-list">${previews.map((entry) => `<div class="import-preview-item"><ha-icon icon="${entry.kind === "stop" ? "mdi:map-marker-outline" : entry.kind === "event" ? "mdi:calendar-outline" : "mdi:format-list-bulleted"}"></ha-icon><div><strong>${escapeHtml(entry.title || "Eintrag")}</strong>${entry.subtitle ? `<span>${escapeHtml(entry.subtitle)}</span>` : ""}</div></div>`).join("")}</div>` : `<p class="muted">Keine Einzelvorschau verfügbar.</p>`}</section>
        <div class="notice neutral"><ha-icon icon="mdi:shield-check-outline"></ha-icon><div><strong>Keine direkte Speicherung</strong><span>Der Import landet zuerst im Änderungskorb oder in der bekannten Übergabeübersicht. Home Assistant setzt Revision und Zielreise serverseitig.</span></div></div>
      </div>
      <div class="modal-actions universal-import-actions">
        <button class="secondary-button" type="button" data-action="archive-open" data-document-id="${escapeHtml(documentItem.id || "")}"><ha-icon icon="mdi:file-eye-outline"></ha-icon>Original öffnen</button>
        ${status === "ready" ? `<button class="secondary-button" type="button" data-action="universal-import-discuss" data-document-id="${escapeHtml(documentItem.id || "")}"><ha-icon icon="mdi:message-text-outline"></ha-icon>Im Assistenten besprechen</button><button class="primary-button" type="button" data-action="universal-import-transfer" data-document-id="${escapeHtml(documentItem.id || "")}"><ha-icon icon="mdi:playlist-plus"></ha-icon>${item.mode === "changeset" ? "Zur Übergabeübersicht" : "In Änderungskorb"}</button><button class="text-button danger-text" type="button" data-action="universal-import-discard" data-document-id="${escapeHtml(documentItem.id || "")}">Verwerfen</button>` : `<button class="secondary-button" type="button" data-action="close-dialog">Schließen</button>`}
      </div>`;
  }

  _renderOneDriveSetup(_dialog) {
    const oneDrive = this._experienceData().onedrive || {};
    const setup = oneDrive.setup_settings || {};
    const folderPath = setup.folder_path || oneDrive.folder_path || "Pictures/Camera Roll";
    const interval = Number(setup.sync_interval_minutes || oneDrive.sync_interval_minutes || 15);
    const recursive = setup.recursive_subfolders ?? oneDrive.recursive_subfolders ?? true;
    const dateBufferDays = Number(setup.date_buffer_days ?? oneDrive.date_buffer_days ?? 3);
    const maxItemsPerRun = Number(setup.max_items_per_run ?? oneDrive.max_items_per_run ?? 2000);
    const maxScanSeconds = Number(setup.max_scan_seconds ?? oneDrive.max_scan_seconds ?? 12);
    const configuredHint = oneDrive.client_id_hint || "";
    return `${this._renderModalHeader("OneDrive Personal einrichten", "Einmalige Microsoft-Appregistrierung, danach Anmeldung per Gerätecode")}
      <form data-form="onedrive-setup" class="form-grid onedrive-setup-form">
        <div class="notice info full"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Kein API-Schlüssel und kein Client-Secret</strong><span>Microsoft verlangt für den Gerätecodefluss lediglich eine öffentliche Application (client) ID. Roadplanner speichert kein Microsoft-Passwort und benötigt nur lesenden Dateizugriff.</span></div></div>
        <div class="form-section full"><h3>1. Microsoft-App einmalig anlegen</h3><ol class="setup-steps"><li>Öffne Microsoft Entra und erstelle eine neue App-Registrierung.</li><li>Kontotyp: persönliche Microsoft-Konten oder Organisations- und persönliche Konten.</li><li>Unter „Authentifizierung“ öffentliche Clientflows erlauben.</li><li>Delegierte Graph-Berechtigungen: <code>Files.Read</code> und <code>User.Read</code>. <code>offline_access</code> wird bei der Anmeldung angefordert.</li><li>Kopiere die Application (client) ID hierher. Kein Geheimnis erstellen.</li></ol><a class="secondary-button inline-link-button" href="https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Microsoft App-Registrierungen öffnen</a></div>
        ${this._field("client_id", `Application (client) ID${configuredHint ? ` · bestehend ${configuredHint} · leer lassen zum Behalten` : ""}`, "", "text", !oneDrive.configured, "full")}
        <div class="form-section full"><h3>2. Fotoordner und Automatik</h3><p>Diese Einrichtung ist die einzige maßgebliche OneDrive-Konfiguration. Der Pfad ist relativ zum OneDrive-Stamm, zum Beispiel „Bilder/Eigene Aufnahmen“.</p></div>
        ${this._field("folder_path", "Kameraordner", folderPath, "text", true, "full")}
        ${this._archiveCheckbox("recursive_subfolders", "Unterordner rekursiv durchsuchen", recursive, "Jahres- und Monatsordner werden berücksichtigt. Ordner außerhalb des Reisezeitraums sowie versteckte Vorschaubildordner werden übersprungen.", "full")}
        ${this._field("date_buffer_days", "Puffer vor und nach der Reise (Tage)", dateBufferDays, "number", true, "", "0", "1")}
        ${this._field("max_items_per_run", "Maximale OneDrive-Einträge je Lauf", maxItemsPerRun, "number", true, "", "100", "100")}
        ${this._field("max_scan_seconds", "Maximale Laufzeit je Scan (Sekunden)", maxScanSeconds, "number", true, "", "3", "1")}
        ${this._field("sync_interval_minutes", "Synchronisierungsintervall (Minuten)", interval, "number", true, "", "5", "1")}
        ${this._archiveCheckbox("auto_sync", "Automatisch synchronisieren", setup.auto_sync ?? oneDrive.auto_sync ?? true, "Im Hintergrund wird nur die aktive Reise fortgesetzt. Große Erstscans laufen in begrenzten Paketen.", "full")}
        ${this._archiveCheckbox("auto_assign", "Automatisch nach Datum und GPS zuordnen", setup.auto_assign ?? oneDrive.auto_assign ?? true, "Eindeutige Treffer werden einem Tag oder Stopp zugeordnet; unsichere Treffer bleiben zur Prüfung.", "full")}
        <div class="notice neutral full"><ha-icon icon="mdi:shield-search-outline"></ha-icon><div><strong>Keine historischen Bilder werden heruntergeladen</strong><span>Der Erstscan liest nur OneDrive-Metadaten. Fotos außerhalb des Reisezeitraums werden nicht als Roadplanner-Medien übernommen. Nach Abschluss werden nur noch Änderungen über den Microsoft-Delta-Cursor gelesen.</span></div></div>
        <div class="modal-actions full"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="primary-button" type="submit"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon>Speichern und verbinden</button></div>
      </form>`;
  }

  _renderOneDriveAuth(dialog) {
    const auth = dialog.auth || {};
    const verification = this._safeUrl(auth.verification_uri);
    return `${this._renderModalHeader("OneDrive Personal verbinden", "Microsoft Geräteanmeldung")}
      <div class="onedrive-auth-body"><ha-icon icon="mdi:microsoft-onedrive"></ha-icon><p>Öffne die Microsoft-Anmeldeseite, gib den Code ein und erlaube Roadplanner ausschließlich lesenden Zugriff auf deine OneDrive-Dateien.</p><div class="device-code">${escapeHtml(auth.user_code || "—")}</div>${verification ? `<a class="primary-button" href="${escapeHtml(verification)}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Microsoft-Anmeldung öffnen</a>` : ""}<small>${escapeHtml(auth.message || "Der Code ist zeitlich begrenzt.")}</small></div>
      <div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Später</button><button class="primary-button" type="button" data-action="onedrive-poll"><ha-icon icon="mdi:check-network-outline"></ha-icon>Verbindung prüfen</button></div>`;
  }

  _renderMediaEdit(dialog) {
    const item = dialog.media || {};
    const days = this._data?.days?.days || [];
    const dayOptions = [{ value: "", label: "Nicht zugeordnet" }, ...days.map((day) => ({ value: day.id, label: `${this._formatDate(day.date)} · ${day.title || day.id}` }))];
    const stopOptions = [{ value: "", label: "Kein konkreter Stopp" }];
    for (const day of days) {
      for (const stop of day.stops || []) stopOptions.push({ value: `${day.id}::${stop.id}`, label: `${this._formatDate(day.date)} · ${stop.name}` });
    }
    const stopRef = item.linked_day_id && item.linked_stop_id ? `${item.linked_day_id}::${item.linked_stop_id}` : "";
    return `${this._renderModalHeader("Foto zuordnen", item.name || "OneDrive-Foto")}<form data-form="media-edit" data-media-id="${escapeHtml(item.id || "")}" class="form-grid">${this._archiveSelect("linked_day_id", "Reisetag", item.linked_day_id || "", dayOptions, "full")}${this._archiveSelect("linked_stop_ref", "Stopp", stopRef, stopOptions, "full")}${this._textarea("caption", "Bildunterschrift", item.caption || "", "full")}<label class="checkbox-field full"><input type="checkbox" name="is_cover" ${item.is_cover ? "checked" : ""}><span><strong>Als Titelbild dieses Stopps verwenden</strong><small>Roadplanner zeigt pro Stopp nur ein Titelbild.</small></span></label>${this._formActions("Zuordnung speichern")}</form><div class="modal-actions"><button class="text-button danger-text" type="button" data-action="media-delete" data-media-id="${escapeHtml(item.id || "")}">Aus Roadplanner entfernen</button></div>`;
  }

  _renderMediaGallery(dialog) {
    const media = dialog.media || [];
    if (!media.length) return `${this._renderModalHeader("Fotoalbum")}<div class="empty-state">Keine Bilder</div>`;
    const index = Math.max(0, Math.min(media.length - 1, Number(dialog.index || 0)));
    const item = media[index];
    const day = item.linked_day_id ? this._findDay(item.linked_day_id) : null;
    const stop = item.linked_day_id && item.linked_stop_id ? this._findStop(item.linked_day_id, item.linked_stop_id) : null;
    return `${this._renderModalHeader("Fotoalbum", `${index + 1} von ${media.length}`)}<div class="media-gallery"><div class="media-gallery-stage"><img src="${escapeHtml(this._safeUrl(item.original_url || item.thumbnail_url))}" alt="${escapeHtml(item.caption || item.name || "Reisefoto")}"></div><div class="media-gallery-caption"><strong>${escapeHtml(item.caption || item.name || "Foto")}</strong><span>${escapeHtml(stop?.name || day?.title || "Nicht zugeordnet")}</span><small>${escapeHtml(item.taken_at ? this._formatTimestamp(item.taken_at) : "Aufnahmezeit unbekannt")}</small></div></div><div class="modal-actions media-gallery-actions"><button class="icon-button" type="button" data-action="media-gallery-prev"><ha-icon icon="mdi:chevron-left"></ha-icon></button><button class="secondary-button" type="button" data-action="media-edit" data-media-id="${escapeHtml(item.id)}"><ha-icon icon="mdi:pencil-outline"></ha-icon>Zuordnen</button><a class="secondary-button" href="${escapeHtml(this._safeUrl(item.original_url))}" target="_blank" rel="noopener noreferrer"><ha-icon icon="mdi:open-in-new"></ha-icon>Original</a><button class="icon-button" type="button" data-action="media-gallery-next"><ha-icon icon="mdi:chevron-right"></ha-icon></button></div>`;
  }

  _renderAssistantDraftDialog(dialog) {
    const draft = dialog.draft || {};
    const values = draft.values && typeof draft.values === "object" ? draft.values : {};
    const actionLabel = { add: "Hinzufügen", update: "Ändern", remove: "Entfernen", plan: "Planen" }[draft.action] || draft.action || "Änderung";
    const entityLabel = { trip: "Reise", day: "Tag", stop: "Stopp", preference: "Präferenz" }[draft.entity_type] || draft.entity_type || "Plan";
    const common = `${this._textarea("summary", "Kurzbeschreibung", draft.summary || "", "full")}${this._textarea("reason", "Begründung", draft.reason || "", "full")}${this._field("day_date", "Datum", draft.day_date || "", "date")}${this._field("day_id", "Tages-ID (optional)", draft.day_id || "", "text")}${this._field("target_id", "Ziel-ID (optional)", draft.target_id || "", "text")}${this._field("position", "Position (optional)", draft.position ?? "", "number", false, "", "1")}${this._field("place_query", "Ortssuche für GPS (optional)", draft.place_query || "", "text", false, "full")}`;
    let valueFields = "";
    if (draft.entity_type === "stop") {
      valueFields = `${this._field("value_name", "Name", values.name || "", "text", false, "full")}${this._field("value_type", "Stopptyp", values.type || "", "text")}${this._field("value_arrival_time", "Ankunft", values.arrival_time || "", "time")}${this._field("value_departure_time", "Abfahrt", values.departure_time || "", "time")}${this._textarea("value_notes", "Notizen", values.notes || "", "full")}`;
    } else if (draft.entity_type === "day") {
      valueFields = `${this._field("value_title", "Tagestitel", values.title || "", "text", false, "full")}${this._field("value_date", "Datum", values.date || "", "date")}${this._field("value_status", "Status", values.status || "", "text")}${this._field("value_start", "Start", values.start || "", "text")}${this._field("value_end", "Ziel", values.end || "", "text")}${this._field("value_distance_km", "Entfernung (km)", values.distance_km ?? "", "number", false, "", "0", "0.1")}${this._field("value_drive_minutes", "Fahrzeit (Minuten)", values.drive_minutes ?? "", "number", false, "", "0")}${this._textarea("value_notes", "Notizen", values.notes || "", "full")}`;
    } else if (draft.entity_type === "preference") {
      valueFields = `${this._field("value_category", "Kategorie", values.category || "", "text")}${this._field("value_status", "Status", values.status || "", "text")}${this._textarea("value_text", "Präferenz", values.text || "", "full")}${this._textarea("value_notes", "Notizen", values.notes || "", "full")}`;
    } else {
      valueFields = `${this._field("value_title", "Titel", values.title || "", "text", false, "full")}${this._field("value_status", "Status", values.status || "", "text")}${this._field("value_start_date", "Startdatum", values.start_date || "", "date")}${this._field("value_end_date", "Enddatum", values.end_date || "", "date")}${this._textarea("value_notes", "Notizen", values.notes || "", "full")}`;
    }
    return `${this._renderModalHeader("Vormerkung bearbeiten", "Noch keine Änderung am Roadbook")}` +
      `<div class="preview-grid draft-summary-grid"><div><span>Aktion</span><strong>${escapeHtml(actionLabel)}</strong></div><div><span>Bereich</span><strong>${escapeHtml(entityLabel)}</strong></div></div>` +
      `<form data-form="assistant-draft" data-draft-id="${escapeHtml(draft.id || "")}" class="form-grid">${common}<div class="form-section full"><h3>Geplante Werte</h3><p>Nur diese Angaben werden später in der Änderungsübersicht technisch übersetzt und erneut validiert.</p></div>${valueFields}${this._formActions("Vormerkung speichern")}</form>`;
  }

  _renderAssistantDiagnostics(dialog) {
    const diagnostics = dialog.diagnostics || {};
    const provider = diagnostics.provider || {};
    const session = diagnostics.session || {};
    const plugins = Array.isArray(diagnostics.plugins) ? diagnostics.plugins : [];
    const records = Array.isArray(diagnostics.records) ? diagnostics.records.slice().reverse() : [];
    const lastStatus = provider.last_error_code
      ? `Fehler: ${provider.last_error_code}${provider.last_error_status ? ` / HTTP ${provider.last_error_status}` : ""}`
      : (provider.last_success_at ? "Letzter Aufruf erfolgreich" : "Noch kein Aufruf protokolliert");
    return `${this._renderModalHeader("Assistenten-Diagnose", "Nur Administratoren · keine Prompts, Reisekontexte oder API-Schlüssel")}
      <div class="assistant-diagnostics-body">
        <div class="preview-grid diagnostics-grid">
          <div><span>Primärmodell</span><strong>${escapeHtml(provider.primary_model || "—")}</strong></div>
          <div><span>Fallback</span><strong>${escapeHtml(provider.fallback_model || "Aus")}</strong></div>
          <div><span>Logische Aufrufe</span><strong>${Number(provider.total_calls || 0)}</strong></div>
          <div><span>API-Versuche</span><strong>${Number(provider.api_attempts || 0)}</strong></div>
          <div><span>Erfolgreich</span><strong>${Number(provider.successful_calls || 0)}</strong></div>
          <div><span>Fehlgeschlagen</span><strong>${Number(provider.failed_calls || 0)}</strong></div>
          <div><span>Wiederholt</span><strong>${Number(provider.retried_calls || 0)}</strong></div>
          <div><span>Fallback genutzt</span><strong>${Number(provider.fallback_calls || 0)}</strong></div>
          <div><span>Rate-Limits</span><strong>${Number(provider.rate_limited_calls || 0)}</strong></div>
          <div><span>Tageslimit</span><strong>${Number(provider.daily_quota_exhausted_calls || 0)}</strong></div>
          <div><span>Warteschlange</span><strong>${Number(provider.queue_depth || 0)} / ${Number(provider.max_queue || 0)}</strong></div>
          <div><span>Mindestabstand</span><strong>${Number(provider.min_request_interval || 0)} s</strong></div>
          <div><span>Schutzpause</span><strong>${Number(provider.cooldown_remaining_seconds || 0).toFixed(1)} s</strong></div>
          <div><span>Timeout</span><strong>${Number(provider.request_timeout || 0)} s</strong></div>
          <div><span>Tokens gesamt</span><strong>${Number(provider.total_tokens || 0).toLocaleString("de-DE")}</strong></div>
          <div><span>Letzter Status</span><strong>${escapeHtml(lastStatus)}</strong></div>
        </div>
        <section class="diagnostics-section">
          <h3>Gesprächsspeicher</h3>
          <p>${Number(session.total_message_count || 0)} Nachrichten insgesamt · ${Number(session.recent_message_count || 0)} aktuell im Kurzzeitfenster · ${Number(session.compacted_message_count || 0)} lokal zusammengefasst · ${Number(session.basket_count || 0)} Vormerkungen · ${Number(session.request_cache_count || 0)} idempotente Antworten im Cache.</p>
          <p>${Number(session.usage?.logical_calls || 0)} Sitzungsaufrufe · ${Number(session.usage?.prompt_tokens || 0).toLocaleString("de-DE")} Eingabetokens · ${Number(session.usage?.candidate_tokens || 0).toLocaleString("de-DE")} Ausgabetokens.</p>
        </section>
        <section class="diagnostics-section">
          <h3>Plugins</h3>
          ${plugins.length ? `<div class="diagnostics-plugin-list">${plugins.map((plugin) => `<span class="assistant-model"><ha-icon icon="mdi:puzzle-outline"></ha-icon>${escapeHtml(plugin.title || plugin.name || plugin.id || "Plugin")}${plugin.enabled === false ? " (aus)" : ""}</span>`).join("")}</div>` : `<p class="muted">Keine Plugins registriert.</p>`}
        </section>
        <section class="diagnostics-section">
          <h3>Letzte Aufrufe</h3>
          ${records.length ? `<div class="diagnostics-records">${records.map((record) => `<article class="diagnostics-record ${record.status === "ok" ? "ok" : "error"}">
            <div><strong>${escapeHtml(record.kind || "request")}</strong><span>${escapeHtml(record.created_at || "")}</span></div>
            <p>${escapeHtml(record.request_id || "—")} · ${Number(record.duration_ms || 0)} ms · ${escapeHtml(record.status || "—")}</p>
            ${record.context_metadata ? `<small>Kontext: ${escapeHtml(JSON.stringify(record.context_metadata))}</small>` : ""}
            ${record.provider ? `<small>Provider: ${escapeHtml(JSON.stringify(record.provider))}</small>` : ""}
            ${record.basket_outcome && Object.keys(record.basket_outcome).length ? `<small>Änderungskorb: ${escapeHtml(JSON.stringify(record.basket_outcome))}</small>` : ""}
            ${record.error ? `<small class="diagnostic-error">${escapeHtml(record.error)}</small>` : ""}
          </article>`).join("")}</div>` : `<p class="muted">Noch keine Diagnoseeinträge vorhanden.</p>`}
        </section>
      </div>
      <div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Schließen</button></div>`;
  }

  _renderTripForm(dialog) {
    const trip = dialog.trip;
    return `${this._renderModalHeader("Reise bearbeiten", `Revision ${dialog.revision}`)}<form data-form="trip" data-revision="${dialog.revision}" class="form-grid">${this._field("title", "Titel", trip.title, "text", true, "full")}${this._selectField("status", "Status", trip.status, ["planned", "tentative", "confirmed", "completed", "cancelled"])}${this._field("start_date", "Startdatum", trip.start_date || "", "date")}${this._field("end_date", "Enddatum", trip.end_date || "", "date")}${this._textarea("notes", "Notizen", trip.notes || "", "full")}${this._formActions("Reise speichern")}</form>`;
  }

  _renderDayForm(dialog) {
    const day = dialog.day || {};
    const media = this._mediaFrom(day) || {};
    const add = dialog.mode === "add";
    return `${this._renderModalHeader(add ? "Reisetag hinzufügen" : "Reisetag bearbeiten", add ? "Neuer Eintrag in der Route" : `Tag ${day.sequence}`)}<form data-form="day" data-mode="${dialog.mode}" data-day-id="${escapeHtml(day.id || "")}" data-revision="${dialog.revision}" class="form-grid">${this._field("title", "Titel", day.title || "", "text", true, "full")}${this._field("date", "Datum", day.date || "", "date")}${this._field("position", "Position", day.sequence || "", "number", false, "", "1")}${this._field("start", "Start", day.start || "", "text")}${this._field("end", "Ziel", day.end || "", "text")}${this._field("distance_km", "Entfernung (km)", day.distance_km ?? "", "number", false, "", "0", "0.1")}${this._field("drive_minutes", "Fahrzeit (Minuten)", day.drive_minutes ?? "", "number", false, "", "0")}${this._selectField("status", "Status", day.status || "planned", ["planned", "tentative", "confirmed", "completed", "cancelled"])}${this._textarea("notes", "Notizen", day.notes || "", "full")}<div class="form-section full"><h3>Bild</h3><p>Optionales Titelbild für den Reisetag.</p></div>${this._field("image_url", "Bild-URL", media.image_url || "", "text", false, "full")}${this._field("image_alt", "Alternativtext", media.alt || "", "text", false, "full")}${this._field("image_attribution", "Bildnachweis", media.attribution || "", "text", false, "full")}${this._field("image_source_url", "Quellseite", media.source_url || "", "text", false, "full")}${this._hiddenField("image_provider", media.provider || "manual")}${this._formActions(add ? "Reisetag hinzufügen" : "Änderungen speichern")}</form>`;
  }

  _renderStopForm(dialog) {
    const stop = dialog.stop || {};
    const location = stop.location || {};
    const media = this._mediaFrom(stop) || {};
    const transport = stop?.details?.transport && typeof stop.details.transport === "object" ? stop.details.transport : {};
    const add = dialog.mode === "add";
    return `${this._renderModalHeader(add ? "Stopp hinzufügen" : "Stopp bearbeiten", this._findDay(dialog.dayId)?.title || "Reisetag")}<form data-form="stop" data-mode="${dialog.mode}" data-day-id="${escapeHtml(dialog.dayId)}" data-stop-id="${escapeHtml(stop.id || "")}" data-revision="${dialog.revision}" class="form-grid">${this._field("name", "Name", stop.name || "", "text", true, "full")}${this._selectField("stop_type", "Typ", stop.type || "waypoint", ["waypoint", "start", "destination", "overnight", "campsite", "camping", "parking", "sightseeing", "attraction", "activity", "restaurant", "shopping", "ferry", "charging", "fuel", "service", "water", "waste", "laundry", "border", "break", "viewpoint", "fishing"])}${this._field("position", "Position", "", "number", false, "", "1")}${this._field("arrival_time", "Ankunft", stop.arrival_time || "", "time")}${this._field("departure_time", "Abfahrt", stop.departure_time || "", "time")}${this._archiveSelect("segment_mode_to_next", "Etappe zum nächsten Stopp", transport.mode_to_next || "auto", [{value:"auto",label:"Automatisch"},{value:"driving",label:"Straße / Auto"},{value:"ferry",label:"Fähre"},{value:"break",label:"Keine automatische Verbindung"}])}${this._archiveSelect("ferry_role", "Fährrolle", transport.ferry_role || "", [{value:"",label:"Keine / automatisch"},{value:"departure",label:"Abfahrtsterminal"},{value:"arrival",label:"Ankunftsterminal"}])}<div class="notice neutral full"><ha-icon icon="mdi:ferry"></ha-icon><div><strong>Fährstrecken</strong><span>Für eine korrekte Fährlinie sollten Abfahrts- und Ankunftsterminal als zwei Stopps mit GPS vorhanden sein. Die Etappe vom Abfahrtsterminal zum Ankunftsterminal wird als „Fähre“ markiert.</span></div></div>${this._field("address", "Adresse", location.address || "", "text", false, "full")}${this._field("city", "Ort", location.city || "", "text")}${this._field("country_code", "Land (ISO)", location.country_code || "", "text")}${this._field("latitude", "Breitengrad", location.latitude ?? location.lat ?? "", "number", false, "", "-90", "any")}${this._field("longitude", "Längengrad", location.longitude ?? location.lon ?? location.lng ?? "", "number", false, "", "-180", "any")}${this._textarea("notes", "Notizen", stop.notes || "", "full")}<div class="form-section full"><h3>Zielbild</h3><p>Du kannst eine Bild-URL hinterlegen oder nach dem Speichern über „Bild suchen“ Wikimedia Commons verwenden.</p></div>${this._field("image_url", "Bild-URL", media.image_url || "", "text", false, "full")}${this._field("image_alt", "Alternativtext", media.alt || "", "text", false, "full")}${this._field("image_attribution", "Bildnachweis", media.attribution || "", "text", false, "full")}${this._field("image_source_url", "Quellseite", media.source_url || "", "text", false, "full")}${this._hiddenField("image_provider", media.provider || "manual")}${this._formActions(add ? "Stopp hinzufügen" : "Änderungen speichern")}</form>`;
  }

  _renderConfirmDialog(dialog) {
    return `${this._renderModalHeader(dialog.title)}<div class="confirm-body"><ha-icon icon="${dialog.destructive ? "mdi:alert-outline" : "mdi:help-circle-outline"}"></ha-icon><p>${escapeHtml(dialog.message)}</p></div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="${dialog.destructive ? "danger-button" : "primary-button"}" type="button" data-action="confirm-dialog">${escapeHtml(dialog.confirmLabel)}</button></div>`;
  }

  _renderHandoffPreview(dialog) {
    const preview = dialog.preview || {};
    const operations = preview.operation_results || [];
    return `${this._renderModalHeader("Übergabe-Vorschau", dialog.handoff?.title || dialog.handoff?.id || "ChangeSet")}<div class="preview-body"><div class="preview-status ${preview.applicable ? "ready" : "blocked"}"><ha-icon icon="${preview.applicable ? "mdi:check-decagram-outline" : "mdi:alert-circle-outline"}"></ha-icon><div><strong>${preview.applicable ? "Bereit zur Übernahme" : "Nicht anwendbar"}</strong><span>${escapeHtml(preview.reason || `Zielrevision ${preview.target_revision ?? "—"}`)}</span></div></div><div class="preview-grid"><div><span>Basisrevision</span><strong>${escapeHtml(preview.base_revision ?? dialog.handoff?.base_revision ?? "—")}</strong></div><div><span>Aktuelle Revision</span><strong>${escapeHtml(preview.current_revision ?? this._currentRevision())}</strong></div><div><span>Operationen</span><strong>${escapeHtml(preview.operation_count ?? dialog.handoff?.operation_count ?? 0)}</strong></div><div><span>Löschungen</span><strong>${preview.destructive || dialog.handoff?.destructive ? "Ja" : "Nein"}</strong></div></div>${operations.length ? `<ol class="operation-list">${operations.map((operation) => `<li><strong>${escapeHtml(operation.op || operation.operation || "Änderung")}</strong><pre>${escapeHtml(JSON.stringify(operation, null, 2))}</pre></li>`).join("")}</ol>` : ""}</div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Schließen</button>${preview.applicable && this._canApprove() && this._data?.selected_is_active ? `<button class="primary-button" type="button" data-action="apply-handoff" data-handoff-id="${escapeHtml(dialog.handoff.id)}">Übernehmen</button>` : ""}</div>`;
  }

  _renderImageSearch(dialog) {
    const results = dialog.results || [];
    return `${this._renderModalHeader("Zielbild auswählen", dialog.query)}<div class="image-search-body">${results.length ? `<div class="image-search-grid">${results.map((image, index) => `<article class="image-result">${this._renderDestinationImage({ ...image, context: image.title }, { compact: true })}<div><strong>${escapeHtml(image.title || image.alt)}</strong><span>${escapeHtml(image.attribution || "Wikimedia Commons")}</span><button class="primary-button" type="button" data-action="choose-image" data-image-index="${index}">Dieses Bild verwenden</button></div></article>`).join("")}</div>` : `<div class="empty-state compact-empty"><ha-icon icon="mdi:image-search-outline"></ha-icon><h2>Keine passenden Bilder gefunden</h2><p>Bearbeite den Namen oder Ort des Ziels und starte die Suche erneut.</p></div>`}<div class="notice info"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Bildquelle</strong><span>Die Suche läuft nur auf ausdrücklichen Klick über Wikimedia Commons. Bildnachweis und Quellseite werden mitgespeichert.</span></div></div></div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Schließen</button></div>`;
  }

  _field(name, label, value, type = "text", required = false, className = "", min = "", step = "") {
    return `<label class="form-field ${className}"><span>${escapeHtml(label)}</span><input name="${escapeHtml(name)}" type="${escapeHtml(type)}" value="${escapeHtml(value)}" ${required ? "required" : ""} ${min !== "" ? `min="${escapeHtml(min)}"` : ""} ${step !== "" ? `step="${escapeHtml(step)}"` : ""}></label>`;
  }

  _hiddenField(name, value) {
    return `<input type="hidden" name="${escapeHtml(name)}" value="${escapeHtml(value)}">`;
  }

  _textarea(name, label, value, className = "") {
    return `<label class="form-field ${className}"><span>${escapeHtml(label)}</span><textarea name="${escapeHtml(name)}" rows="4">${escapeHtml(value)}</textarea></label>`;
  }

  _selectField(name, label, value, options) {
    return `<label class="form-field"><span>${escapeHtml(label)}</span><select name="${escapeHtml(name)}">${options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(this._statusLabel(option))}</option>`).join("")}</select></label>`;
  }

  _formActions(saveLabel) {
    return `<div class="modal-actions full"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="primary-button" type="submit">${escapeHtml(saveLabel)}</button></div>`;
  }

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
  }

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
  }

  _mapColors() {
    const styles = getComputedStyle(this);
    const primary = cleanText(styles.getPropertyValue("--primary-color")) || "#039be5";
    const ferry = cleanText(styles.getPropertyValue("--accent-color")) || "#7e57c2";
    const muted = cleanText(styles.getPropertyValue("--secondary-text-color")) || "#78909c";
    return { primary, ferry, muted };
  }

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
      const ferryStop = point.stopType === "ferry";
      const markerColor = ferryStop ? colors.ferry : colors.primary;
      const icon = Leaflet.divIcon({
        className: "roadplanner-map-marker",
        html: `<span style="display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:${escapeHtml(markerColor)};color:#fff;border:2px solid #fff;box-shadow:0 2px 7px rgba(0,0,0,.35);font:700 12px system-ui,sans-serif">${index + 1}</span>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
        tooltipAnchor: [0, -14],
      });
      const marker = Leaflet.marker([point.lat, point.lon], { icon, interactive: true });
      marker.bindTooltip(`${index + 1}. ${point.label || `Stopp ${index + 1}`}`, { direction: "top" });
      layers.push(marker);
    }
    return layers;
  }

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
  }

  _styles() {
    return `<style>
      :host {
        display: block;
        height: 100%;
        min-height: 100%;
        overflow: hidden;
        color: var(--primary-text-color, #212121);
        background: var(--primary-background-color, #f5f5f5);
        font-family: var(--paper-font-body1_-_font-family, system-ui, sans-serif);
      }
      * { box-sizing: border-box; }
      button, input, select, textarea { font: inherit; }
      button { -webkit-tap-highlight-color: transparent; }
      a { color: var(--primary-color); }
      .app { height: 100%; display: grid; grid-template-rows: auto auto 1fr; overflow: hidden; position: relative; }
      .app.busy { cursor: progress; }
      .topbar { min-height: 64px; padding: max(10px, env(safe-area-inset-top)) 18px 10px; display: flex; align-items: center; justify-content: space-between; gap: 16px; background: var(--app-header-background-color, var(--primary-background-color)); border-bottom: 1px solid var(--divider-color); z-index: 4; }
      .topbar-start, .topbar-actions, .title-line { display: flex; align-items: center; gap: 12px; min-width: 0; }
      .title-group { min-width: 0; }
      .title-line { gap: 8px; }
      .title-group h1 { margin: 0; font-size: 20px; line-height: 1.2; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .subtitle { color: var(--secondary-text-color); font-size: 12px; margin-top: 3px; }
      .app-icon { width: 40px; height: 40px; border-radius: 13px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 16%, transparent); color: var(--primary-color); flex: 0 0 auto; }
      .app-icon ha-icon { --mdc-icon-size: 25px; }
      .icon-button { border: 0; background: transparent; color: var(--primary-text-color); width: 44px; height: 44px; border-radius: 14px; display: grid; place-items: center; cursor: pointer; }
      .icon-button:hover { background: var(--secondary-background-color); }
      .menu-button { display: none; }
      .view-badge, .status-badge, .state-pill, .count-badge, .sequence-badge { display: inline-flex; align-items: center; justify-content: center; border-radius: 999px; font-weight: 700; }
      .view-badge { padding: 4px 8px; font-size: 11px; color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 14%, transparent); }
      .trip-select { display: flex; align-items: center; gap: 8px; min-width: 220px; padding: 7px 10px; border: 1px solid var(--divider-color); border-radius: 12px; background: var(--card-background-color); }
      .trip-select ha-icon { color: var(--primary-color); }
      .trip-select select { width: 100%; min-width: 0; border: 0; outline: 0; background: transparent; color: var(--primary-text-color); }
      .tabs { display: flex; align-items: stretch; overflow-x: auto; scrollbar-width: none; padding: 0 16px; background: var(--card-background-color); border-bottom: 1px solid var(--divider-color); z-index: 3; }
      .tabs::-webkit-scrollbar { display: none; }
      .tab { min-height: 54px; border: 0; border-bottom: 3px solid transparent; background: transparent; color: var(--secondary-text-color); padding: 0 16px; display: flex; align-items: center; gap: 8px; font-weight: 650; cursor: pointer; white-space: nowrap; }
      .tab.active { color: var(--primary-color); border-bottom-color: var(--primary-color); }
      .tab ha-icon { --mdc-icon-size: 21px; }
      .count-badge { min-width: 22px; height: 22px; padding: 0 6px; font-size: 11px; color: white; background: var(--error-color, #d32f2f); }
      .count-badge.warning { background: var(--warning-color, #f57c00); }
      .content { overflow: auto; overscroll-behavior: contain; padding: 24px max(18px, calc((100vw - 1320px) / 2)); padding-bottom: max(36px, calc(24px + env(safe-area-inset-bottom))); }
      .hero-card, .panel-card, .toolbar-card, .map-card, .route-flow-card, .handoff-card, .trip-card, .stop-card, .total-day-card { background: var(--card-background-color); border: 1px solid var(--divider-color); box-shadow: var(--ha-card-box-shadow, none); border-radius: 22px; }
      .hero-card { overflow: hidden; display: grid; grid-template-columns: 1fr; min-height: 220px; margin-bottom: 18px; }
      .hero-card.with-image { grid-template-columns: minmax(260px, 42%) 1fr; }
      .hero-image { min-height: 260px; }
      .hero-copy { padding: clamp(22px, 4vw, 44px); display: flex; flex-direction: column; justify-content: center; align-items: flex-start; }
      .hero-copy h2 { margin: 6px 0 10px; font-size: clamp(28px, 5vw, 48px); line-height: 1.04; }
      .hero-copy p { margin: 0 0 18px; color: var(--secondary-text-color); max-width: 70ch; line-height: 1.55; }
      .hero-meta { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-bottom: 20px; color: var(--secondary-text-color); }
      .hero-meta span { display: flex; align-items: center; gap: 7px; }
      .eyebrow { display: block; color: var(--primary-color); font-size: 12px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
      .stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin-bottom: 18px; }
      .stat-card { min-height: 130px; padding: 20px; border-radius: 20px; background: var(--card-background-color); border: 1px solid var(--divider-color); display: flex; flex-direction: column; justify-content: center; }
      .stat-card ha-icon { color: var(--primary-color); margin-bottom: 12px; }
      .stat-card strong { font-size: 26px; }
      .stat-card span { color: var(--secondary-text-color); margin-top: 3px; }
      .panel-card, .toolbar-card, .route-flow-card, .handoff-card { padding: 22px; margin-bottom: 18px; }
      .toolbar-card { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
      .toolbar-card h2, .panel-card h2, .section-heading h2 { margin: 4px 0 0; font-size: 23px; }
      .toolbar-card p { margin: 7px 0 0; color: var(--secondary-text-color); }
      .toolbar-actions, .button-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
      .section-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
      .section-heading.compact { margin-bottom: 14px; }
      .section-heading > ha-icon { color: var(--primary-color); --mdc-icon-size: 34px; }
      .primary-button, .secondary-button, .danger-button, .text-button { text-decoration: none; min-height: 42px; border-radius: 13px; padding: 9px 15px; border: 0; display: inline-flex; align-items: center; justify-content: center; gap: 8px; font-weight: 700; cursor: pointer; }
      .primary-button { background: var(--primary-color); color: var(--text-primary-color, white); }
      .secondary-button { background: var(--secondary-background-color); color: var(--primary-text-color); border: 1px solid var(--divider-color); }
      .danger-button { background: var(--error-color, #d32f2f); color: white; }
      .text-button { background: transparent; color: var(--primary-color); }
      .danger-text { color: var(--error-color, #d32f2f); }
      .compact-button { min-height: 36px; padding: 7px 10px; margin-left: auto; }
      button:disabled { opacity: .45; cursor: not-allowed; }
      .next-day-grid, .facts-grid, .preview-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
      .next-day-grid > div, .facts-grid > div, .preview-grid > div { padding: 14px; border-radius: 14px; background: var(--secondary-background-color); display: flex; flex-direction: column; gap: 5px; }
      .next-day-grid span, .facts-grid span, .preview-grid span { color: var(--secondary-text-color); font-size: 12px; }
      .settings-list { display: grid; gap: 0; margin-bottom: 16px; }
      .setting-row { min-height: 50px; display: flex; justify-content: space-between; align-items: center; gap: 12px; border-bottom: 1px solid var(--divider-color); }
      .setting-row:last-child { border-bottom: 0; }
      .state-pill { padding: 5px 9px; font-size: 11px; }
      .state-pill.on { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .state-pill.off { color: var(--secondary-text-color); background: var(--secondary-background-color); }
      .status-dot { width: 12px; height: 12px; border-radius: 50%; background: var(--disabled-color); }
      .status-dot.success { background: var(--success-color, #2e7d32); box-shadow: 0 0 0 5px color-mix(in srgb, var(--success-color, #2e7d32) 14%, transparent); }
      .notice { border-radius: 16px; padding: 14px 16px; margin: 12px 0; display: flex; align-items: center; gap: 12px; }
      .notice > div { display: flex; flex-direction: column; gap: 3px; }
      .notice span { color: var(--secondary-text-color); }
      .notice.info { background: color-mix(in srgb, var(--info-color, #0288d1) 12%, transparent); }
      .notice.warning { background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .notice.danger { background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .view-notice { margin-top: 0; }
      .route-layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, .8fr); gap: 18px; align-items: start; }
      .route-main { min-width: 0; }
      .day-facts { position: sticky; top: 0; }
      .day-toolbar .day-select { min-width: 250px; }
      .day-select { display: flex; flex-direction: column; gap: 5px; color: var(--secondary-text-color); font-size: 12px; }
      .day-select select { min-height: 44px; border: 1px solid var(--divider-color); border-radius: 12px; padding: 0 12px; background: var(--card-background-color); color: var(--primary-text-color); }
      .map-card { overflow: hidden; margin-bottom: 18px; }
      .map-stage { height: clamp(300px, 52vh, 560px); position: relative; background: var(--secondary-background-color); }
      .map-stage ha-map { display: block; width: 100%; height: 100%; opacity: 0; transition: opacity .2s ease; }
      .map-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--secondary-text-color); pointer-events: none; }
      .map-ready .map-stage ha-map { opacity: 1; }
      .map-ready .map-overlay { display: none; }
      .map-failed .map-overlay span { display: none; }
      .map-failed .map-overlay::after { content: "Kartenkomponente nicht verfügbar"; }
      .map-key { display: flex; gap: 8px; overflow-x: auto; padding: 10px 14px; border-top: 1px solid var(--divider-color); scrollbar-width: thin; }
      .map-key-item, .map-key-more { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; min-height: 30px; padding: 4px 10px 4px 5px; border-radius: 999px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 11px; }
      .map-key-item b { width: 22px; height: 22px; display: grid; place-items: center; border-radius: 50%; background: var(--primary-color); color: white; font-size: 10px; }
      .map-caption { padding: 10px 14px; display: flex; gap: 8px; align-items: center; color: var(--secondary-text-color); font-size: 12px; border-top: 1px solid var(--divider-color); }
      .map-unavailable { padding: 0; }
      .map-placeholder { min-height: 230px; padding: 30px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 8px; color: var(--secondary-text-color); }
      .map-placeholder ha-icon { --mdc-icon-size: 46px; color: var(--primary-color); }
      .route-flow-card { overflow-x: auto; }
      .route-flow { display: flex; align-items: stretch; min-width: max-content; padding: 18px 8px 8px; }
      .flow-item { width: 180px; position: relative; display: grid; grid-template-columns: 44px 1fr; gap: 10px; align-items: start; }
      .flow-node { width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center; background: var(--primary-color); color: white; position: relative; z-index: 2; }
      .flow-copy { display: flex; flex-direction: column; gap: 3px; padding-top: 2px; }
      .flow-copy strong { max-width: 120px; }
      .flow-copy span { color: var(--secondary-text-color); font-size: 12px; }
      .flow-line { position: absolute; height: 4px; width: 136px; left: 42px; top: 19px; background: color-mix(in srgb, var(--primary-color) 50%, var(--divider-color)); }
      .notes-block { white-space: pre-wrap; line-height: 1.5; color: var(--secondary-text-color); }
      .image-section { overflow: hidden; }
      .image-gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .gallery-item { margin: 0; min-width: 0; border: 1px solid var(--divider-color); border-radius: 16px; overflow: hidden; background: var(--secondary-background-color); }
      .gallery-item figcaption { padding: 11px 12px; display: flex; flex-direction: column; gap: 4px; }
      .gallery-item figcaption span { color: var(--secondary-text-color); font-size: 11px; }
      .destination-image { height: 100%; min-height: 220px; position: relative; overflow: hidden; background: var(--secondary-background-color); }
      .destination-image.compact { min-height: 170px; height: 190px; }
      .destination-image img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .image-fallback { display: none; position: absolute; inset: 0; align-items: center; justify-content: center; flex-direction: column; gap: 7px; color: var(--secondary-text-color); }
      .destination-image.image-error img { display: none; }
      .destination-image.image-error .image-fallback { display: flex; }
      .empty-inline { min-height: 120px; display: flex; align-items: center; justify-content: center; gap: 16px; color: var(--secondary-text-color); text-align: left; }
      .empty-inline ha-icon { --mdc-icon-size: 42px; color: var(--primary-color); }
      .empty-inline > div { display: flex; flex-direction: column; gap: 4px; }
      .stop-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(310px, 1fr)); gap: 16px; }
      .stop-card { overflow: hidden; min-width: 0; }
      .stop-image-placeholder, .trip-card-placeholder { height: 190px; display: flex; align-items: center; justify-content: center; flex-direction: column; gap: 8px; background: linear-gradient(135deg, color-mix(in srgb, var(--primary-color) 14%, var(--secondary-background-color)), var(--secondary-background-color)); color: var(--primary-color); }
      .stop-image-placeholder ha-icon, .trip-card-placeholder ha-icon { --mdc-icon-size: 48px; }
      .stop-card-body { padding: 18px; }
      .stop-card-heading { display: flex; gap: 10px; align-items: flex-start; }
      .stop-card-heading h3 { margin: 0 0 3px; }
      .stop-card-heading span:not(.sequence-badge) { color: var(--secondary-text-color); font-size: 12px; }
      .sequence-badge { width: 28px; height: 28px; background: var(--primary-color); color: white; flex: 0 0 auto; }
      .stop-meta { display: grid; gap: 6px; margin: 14px 0; color: var(--secondary-text-color); font-size: 12px; }
      .stop-meta span { display: flex; align-items: center; gap: 7px; }
      .stop-card-body p { white-space: pre-wrap; line-height: 1.5; }
      .attribution { color: var(--secondary-text-color); font-size: 11px; margin: 8px 0; }
      .stop-actions { margin-top: 14px; }
      .trip-route-graphic { overflow: hidden; }
      .journey-track { display: flex; align-items: stretch; overflow-x: auto; padding: 8px 2px 14px; scrollbar-width: thin; }
      .journey-node { flex: 0 0 230px; min-height: 122px; border: 1px solid var(--divider-color); border-radius: 17px; padding: 14px; background: var(--secondary-background-color); color: var(--primary-text-color); display: grid; grid-template-columns: 38px 1fr; gap: 11px; text-align: left; cursor: pointer; }
      .journey-node:hover, .journey-node:focus-visible { border-color: var(--primary-color); outline: none; }
      .journey-dot { width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: var(--primary-color); color: white; font-weight: 800; }
      .journey-copy { min-width: 0; display: flex; flex-direction: column; gap: 5px; }
      .journey-copy small, .journey-copy span { color: var(--secondary-text-color); }
      .journey-copy strong { font-size: 15px; line-height: 1.25; }
      .journey-copy span { font-size: 12px; line-height: 1.35; }
      .journey-line { flex: 0 0 48px; height: 4px; margin-top: 31px; background: color-mix(in srgb, var(--primary-color) 55%, var(--divider-color)); }
      .total-route-list { margin-top: 22px; }
      .total-day-card { margin-bottom: 12px; padding: 12px; display: grid; grid-template-columns: 52px 120px 1fr auto; gap: 14px; align-items: center; cursor: pointer; }
      .total-day-card:hover { border-color: var(--primary-color); }
      .total-day-sequence { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); font-size: 18px; font-weight: 800; }
      .total-day-image .destination-image { min-height: 80px; height: 80px; border-radius: 12px; }
      .total-day-copy > span, .total-day-copy p { color: var(--secondary-text-color); }
      .total-day-copy h3 { margin: 3px 0; }
      .total-day-copy p { margin: 0 0 8px; }
      .total-day-copy > div { display: flex; flex-wrap: wrap; gap: 7px; }
      .total-day-copy > div span { padding: 4px 7px; border-radius: 8px; background: var(--secondary-background-color); font-size: 11px; }
      .chevron { color: var(--secondary-text-color); }
      .trip-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 18px; }
      .trip-card { overflow: hidden; }
      .trip-card.active { border-color: color-mix(in srgb, var(--success-color, #2e7d32) 65%, var(--divider-color)); }
      .trip-card.selected { box-shadow: 0 0 0 2px var(--primary-color); }
      .trip-card-body { padding: 18px; }
      .trip-title-row { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
      .trip-title-row h3 { margin: 4px 0 0; }
      .trip-card-body > p { color: var(--secondary-text-color); }
      .trip-stats { display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 16px; }
      .trip-stats span { padding: 5px 8px; border-radius: 9px; background: var(--secondary-background-color); font-size: 12px; }
      .status-badge { padding: 5px 9px; font-size: 11px; }
      .status-badge.success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .status-badge.warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .status-badge.danger { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .status-badge.neutral { color: var(--secondary-text-color); background: var(--secondary-background-color); }
      .handoff-list { display: grid; gap: 14px; }
      .handoff-heading { display: flex; justify-content: space-between; gap: 16px; }
      .handoff-heading h3 { margin: 4px 0 6px; }
      .handoff-heading p { margin: 0; color: var(--secondary-text-color); }
      .handoff-meta { display: flex; flex-wrap: wrap; gap: 9px 16px; margin: 15px 0; color: var(--secondary-text-color); font-size: 12px; }
      .handoff-meta span { display: flex; align-items: center; gap: 6px; }
      .operation-summary { padding: 11px; border-radius: 12px; background: var(--secondary-background-color); margin-bottom: 12px; }
      .loading-state, .empty-state { min-height: 360px; display: flex; align-items: center; justify-content: center; flex-direction: column; text-align: center; gap: 9px; color: var(--secondary-text-color); }
      .empty-state ha-icon { --mdc-icon-size: 56px; color: var(--primary-color); }
      .empty-state h2 { margin: 4px 0 0; color: var(--primary-text-color); }
      .empty-state p { max-width: 55ch; margin: 0 0 8px; }
      .compact-empty { min-height: 230px; border: 1px dashed var(--divider-color); border-radius: 20px; padding: 20px; }
      .spinner { width: 38px; height: 38px; border: 4px solid color-mix(in srgb, var(--primary-color) 20%, transparent); border-top-color: var(--primary-color); border-radius: 50%; animation: spin .8s linear infinite; }
      .spinner.small { width: 28px; height: 28px; border-width: 3px; }
      @keyframes spin { to { transform: rotate(360deg); } }
      .progress { position: absolute; top: 0; left: 0; right: 0; height: 3px; z-index: 20; overflow: hidden; background: color-mix(in srgb, var(--primary-color) 20%, transparent); }
      .progress::after { content: ""; display: block; width: 35%; height: 100%; background: var(--primary-color); animation: progress 1s ease-in-out infinite; }
      @keyframes progress { from { transform: translateX(-120%); } to { transform: translateX(390%); } }
      .toast-host { position: fixed; right: 22px; top: max(18px, env(safe-area-inset-top)); z-index: 1000; pointer-events: none; }
      .toast { max-width: min(420px, calc(100vw - 32px)); padding: 13px 16px; border-radius: 15px; color: white; display: flex; align-items: center; gap: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.22); pointer-events: auto; }
      .toast.success { background: var(--success-color, #2e7d32); }
      .toast.error { background: var(--error-color, #d32f2f); }
      .modal-backdrop { position: absolute; inset: 0; z-index: 25; background: rgba(0,0,0,.55); display: flex; align-items: center; justify-content: center; padding: 24px; }
      .modal { width: min(760px, 100%); max-height: min(880px, calc(100% - 20px)); overflow: auto; border-radius: 24px; background: var(--card-background-color); color: var(--primary-text-color); box-shadow: 0 24px 70px rgba(0,0,0,.35); }
      .modal-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 22px 12px; position: sticky; top: 0; background: var(--card-background-color); z-index: 2; }
      .modal-header h2 { margin: 0; }
      .modal-header p { margin: 5px 0 0; color: var(--secondary-text-color); }
      .action-error-body { padding: 18px 22px 8px; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; align-items: start; }
      .action-error-icon { width: 48px; height: 48px; border-radius: 16px; display: grid; place-items: center; color: white; background: var(--error-color, #d32f2f); }
      .action-error-icon ha-icon { --mdc-icon-size: 29px; }
      .action-error-body p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
      .action-error-request { margin-top: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; color: var(--secondary-text-color); }
      .action-error-request code { user-select: all; max-width: 100%; overflow-wrap: anywhere; padding: 5px 8px; border-radius: 8px; background: var(--secondary-background-color); color: var(--primary-text-color); }
      .action-error-actions { flex-wrap: wrap; }
      .action-error-actions .primary-button { margin-left: auto; }
      .form-grid { padding: 10px 22px 22px; display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
      .form-field { display: flex; flex-direction: column; gap: 6px; color: var(--secondary-text-color); font-size: 12px; }
      .form-field.full, .form-section.full, .modal-actions.full { grid-column: 1 / -1; }
      .form-field input, .form-field select, .form-field textarea { width: 100%; min-height: 45px; border-radius: 12px; border: 1px solid var(--divider-color); background: var(--primary-background-color); color: var(--primary-text-color); padding: 10px 12px; outline: 0; }
      .form-field textarea { resize: vertical; }
      .form-field input:focus, .form-field select:focus, .form-field textarea:focus { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 20%, transparent); }
      .form-section { padding-top: 8px; border-top: 1px solid var(--divider-color); }
      .form-section h3 { margin: 0 0 3px; }
      .form-section p { margin: 0; color: var(--secondary-text-color); }
      .modal-actions { padding: 16px 22px max(22px, env(safe-area-inset-bottom)); display: flex; justify-content: flex-end; gap: 10px; }
      .confirm-body { padding: 18px 26px; display: flex; align-items: center; gap: 16px; }
      .confirm-body ha-icon { --mdc-icon-size: 42px; color: var(--warning-color, #f57c00); }
      .preview-body, .image-search-body { padding: 8px 22px 20px; }
      .preview-status { padding: 15px; border-radius: 16px; display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
      .preview-status.ready { background: color-mix(in srgb, var(--success-color, #2e7d32) 12%, transparent); }
      .preview-status.blocked { background: color-mix(in srgb, var(--warning-color, #f57c00) 12%, transparent); }
      .preview-status > div { display: flex; flex-direction: column; gap: 3px; }
      .operation-list { padding-left: 22px; }
      .operation-list pre { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--secondary-background-color); padding: 10px; border-radius: 10px; }
      .image-search-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .image-result { border: 1px solid var(--divider-color); border-radius: 16px; overflow: hidden; background: var(--secondary-background-color); }
      .image-result > div:last-child { padding: 12px; display: flex; flex-direction: column; gap: 8px; }
      .image-result > div:last-child span { color: var(--secondary-text-color); font-size: 11px; }
      .assistant-setup { display: grid; grid-template-columns: auto 1fr; gap: 20px; align-items: start; }
      .assistant-setup-icon, .assistant-avatar { width: 58px; height: 58px; border-radius: 18px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); }
      .assistant-setup-icon ha-icon, .assistant-avatar ha-icon { --mdc-icon-size: 34px; }
      .assistant-setup h2 { margin: 5px 0 8px; }
      .assistant-setup p { color: var(--secondary-text-color); line-height: 1.55; }
      .assistant-toolbar { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
      .assistant-toolbar h2 { margin: 4px 0 6px; }
      .assistant-toolbar p { margin: 0; color: var(--secondary-text-color); max-width: 76ch; }
      .assistant-toolbar-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
      .assistant-model { min-height: 40px; padding: 8px 12px; border-radius: 12px; display: inline-flex; align-items: center; gap: 7px; color: var(--secondary-text-color); background: var(--secondary-background-color); font-size: 12px; }
      .assistant-health { min-height: 40px; padding: 8px 12px; border-radius: 12px; display: inline-flex; align-items: center; gap: 7px; font-size: 12px; font-weight: 700; background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .assistant-health.success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 11%, transparent); }
      .assistant-health.warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 11%, transparent); }
      .assistant-status-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
      .assistant-status-card { margin-bottom: 0; min-width: 0; display: flex; align-items: flex-start; gap: 12px; padding: 15px 16px; }
      .assistant-status-card > ha-icon { --mdc-icon-size: 25px; color: var(--primary-color); margin-top: 2px; }
      .assistant-status-card > div { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
      .assistant-status-card span { color: var(--secondary-text-color); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }
      .assistant-status-card strong { overflow-wrap: anywhere; }
      .assistant-status-card small { color: var(--secondary-text-color); line-height: 1.35; overflow-wrap: anywhere; }
      .assistant-retry-notice { align-items: center; }
      .assistant-retry-notice button { margin-left: auto; flex: 0 0 auto; }
      .assistant-layout { display: grid; grid-template-columns: minmax(0, 1.7fr) minmax(300px, .7fr); gap: 18px; align-items: stretch; }
      .assistant-chat, .assistant-basket { margin-bottom: 0; min-width: 0; }
      .assistant-chat { padding: 0; overflow: hidden; display: grid; grid-template-rows: auto minmax(360px, 1fr); min-height: min(720px, calc(100vh - 250px)); }
      .assistant-thread { overflow: auto; overscroll-behavior: contain; padding: 22px; display: flex; flex-direction: column; gap: 16px; scroll-behavior: smooth; }
      .assistant-welcome { margin: auto; max-width: 720px; text-align: center; padding: 24px 0; }
      .assistant-welcome .assistant-avatar { margin: 0 auto 14px; }
      .assistant-welcome h3 { margin: 0 0 8px; font-size: 25px; }
      .assistant-welcome > p { color: var(--secondary-text-color); margin: 0 auto 22px; max-width: 62ch; line-height: 1.55; }
      .quick-prompt-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
      .quick-prompt-grid button { min-height: 110px; padding: 14px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); color: var(--primary-text-color); display: flex; flex-direction: column; align-items: flex-start; justify-content: space-between; gap: 12px; text-align: left; cursor: pointer; }
      .quick-prompt-grid button:hover { border-color: var(--primary-color); }
      .quick-prompt-grid ha-icon { color: var(--primary-color); }
      .assistant-message { display: flex; gap: 11px; max-width: min(860px, 92%); }
      .assistant-message.user { align-self: flex-end; flex-direction: row-reverse; }
      .assistant-message.assistant { align-self: flex-start; }
      .assistant-message.status { opacity: .9; }
      .message-avatar { width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; flex: 0 0 auto; background: var(--secondary-background-color); color: var(--primary-color); }
      .message-avatar ha-icon { --mdc-icon-size: 20px; }
      .assistant-message.user .message-avatar { background: color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .message-body { min-width: 0; padding: 13px 15px; border-radius: 18px; background: var(--secondary-background-color); border: 1px solid var(--divider-color); }
      .assistant-message.user .message-body { background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); }
      .assistant-message.status .message-body { border-style: dashed; }
      .message-meta { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 7px; font-size: 11px; }
      .message-meta span { color: var(--secondary-text-color); }
      .message-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.55; }
      .message-text .assistant-inline-link { display: inline-flex; max-width: 100%; align-items: center; gap: 4px; color: var(--primary-color); font-weight: 700; text-decoration: underline; text-decoration-thickness: 1px; text-underline-offset: 2px; vertical-align: baseline; overflow-wrap: anywhere; word-break: break-word; }
      .message-text .assistant-inline-link ha-icon { --mdc-icon-size: 15px; flex: 0 0 auto; }
      .message-text .assistant-inline-link span { min-width: 0; overflow-wrap: anywhere; }
      .message-text .assistant-inline-link.google-maps { padding: 2px 6px; border-radius: 8px; background: color-mix(in srgb, var(--primary-color) 10%, transparent); text-decoration: none; }
      .message-text .assistant-inline-link:hover { filter: brightness(1.08); }
      .message-text .assistant-inline-link:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 6px; }
      .assistant-pending-group { display: grid; gap: 10px; }
      .assistant-message.pending { opacity: .92; }
      .assistant-message.pending.thinking .message-body { border-style: dashed; }
      .assistant-thinking { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; color: var(--secondary-text-color); }
      .assistant-thinking > span { width: 7px; height: 7px; border-radius: 999px; background: var(--primary-color); animation: roadplanner-thinking 1.15s infinite ease-in-out; }
      .assistant-thinking > span:nth-child(2) { animation-delay: .16s; }
      .assistant-thinking > span:nth-child(3) { animation-delay: .32s; }
      .assistant-thinking strong { margin-left: 3px; font-size: 12px; font-weight: 700; }
      @keyframes roadplanner-thinking { 0%, 80%, 100% { opacity: .28; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
      .message-basket-status { margin-top: 11px; padding: 9px 10px; border-radius: 11px; display: flex; align-items: flex-start; gap: 7px; font-size: 11px; line-height: 1.4; border: 1px solid var(--divider-color); background: var(--card-background-color); }
      .message-basket-status ha-icon { --mdc-icon-size: 17px; flex: 0 0 auto; }
      .message-basket-status.success { color: var(--success-color, #2e7d32); border-color: color-mix(in srgb, var(--success-color, #2e7d32) 35%, var(--divider-color)); }
      .message-basket-status.warning { color: var(--warning-color, #f57c00); border-color: color-mix(in srgb, var(--warning-color, #f57c00) 35%, var(--divider-color)); }
      .message-sources { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--divider-color); display: flex; flex-wrap: wrap; gap: 7px; }
      .message-sources > span { width: 100%; color: var(--secondary-text-color); font-size: 10px; text-transform: uppercase; letter-spacing: .06em; }
      .message-sources a { max-width: 100%; padding: 6px 8px; border-radius: 9px; background: var(--card-background-color); display: inline-flex; align-items: center; gap: 5px; font-size: 11px; text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .message-sources a ha-icon { --mdc-icon-size: 15px; }
      .assistant-composer { padding: 14px; border-top: 1px solid var(--divider-color); background: var(--card-background-color); }
      .assistant-composer-top { border-top: 0; border-bottom: 1px solid var(--divider-color); }
      .assistant-composer-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin: 0 4px 7px; }
      .assistant-composer-heading label { color: var(--secondary-text-color); font-size: 11px; }
      .assistant-composer-heading span { display: inline-flex; align-items: center; gap: 5px; color: var(--secondary-text-color); font-size: 10px; }
      .assistant-composer-heading ha-icon { --mdc-icon-size: 15px; }
      .assistant-toolbar-primary { margin-bottom: 14px; }
      .assistant-main-actions .assistant-briefing-button { min-height: 44px; }
      .assistant-technical { margin-top: 18px; padding: 0; overflow: hidden; }
      .assistant-technical > summary { list-style: none; cursor: pointer; padding: 15px 18px; display: flex; justify-content: space-between; align-items: center; gap: 14px; }
      .assistant-technical > summary::-webkit-details-marker { display: none; }
      .assistant-technical > summary span { display: inline-flex; align-items: center; gap: 8px; font-weight: 800; }
      .assistant-technical > summary small { color: var(--secondary-text-color); }
      .assistant-technical[open] > summary { border-bottom: 1px solid var(--divider-color); }
      .assistant-technical-content { padding: 16px; display: grid; gap: 14px; }
      .assistant-technical-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 9px; }
      .assistant-technical .assistant-status-grid { margin: 0; }
      .assistant-technical .assistant-status-card { background: var(--secondary-background-color); border: 1px solid var(--divider-color); border-radius: 14px; }
      .assistant-composer > label { display: block; color: var(--secondary-text-color); font-size: 11px; margin: 0 0 6px 4px; }
      .assistant-input-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 9px; align-items: end; }
      .assistant-input-row textarea { width: 100%; min-height: 54px; max-height: 180px; resize: vertical; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--primary-background-color); color: var(--primary-text-color); padding: 13px 14px; outline: none; }
      .assistant-input-row textarea:focus { border-color: var(--primary-color); box-shadow: 0 0 0 2px color-mix(in srgb, var(--primary-color) 18%, transparent); }
      .assistant-send { min-height: 52px; }
      .assistant-hint { margin: 8px 4px 0; display: flex; align-items: center; gap: 5px; color: var(--secondary-text-color); font-size: 11px; }
      .assistant-hint ha-icon { --mdc-icon-size: 16px; color: var(--success-color, #2e7d32); }
      .assistant-basket { position: sticky; top: 0; align-self: start; }
      .basket-counter { min-width: 38px; height: 38px; border-radius: 13px; display: grid; place-items: center; background: color-mix(in srgb, var(--primary-color) 14%, transparent); color: var(--primary-color); font-weight: 800; }
      .basket-list { display: grid; gap: 10px; margin-bottom: 16px; max-height: min(480px, 55vh); overflow: auto; }
      .basket-item { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: 10px; align-items: start; padding: 12px; border: 1px solid var(--divider-color); border-radius: 15px; background: var(--secondary-background-color); }
      .basket-item-icon { width: 34px; height: 34px; border-radius: 11px; display: grid; place-items: center; background: var(--card-background-color); color: var(--primary-color); }
      .basket-item-icon ha-icon { --mdc-icon-size: 20px; }
      .basket-item-copy { min-width: 0; }
      .basket-item-copy > strong { display: block; line-height: 1.35; overflow-wrap: anywhere; }
      .basket-item-copy p { margin: 5px 0 0; color: var(--secondary-text-color); font-size: 11px; line-height: 1.4; }
      .basket-item-label { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 5px; font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--secondary-text-color); }
      .basket-item-label b { color: var(--primary-color); }
      .basket-item-actions { display: flex; flex-direction: column; gap: 2px; }
      .basket-map-link { margin-top: 8px; }
      .basket-map-link .text-link { display: inline-flex; align-items: center; gap: 5px; font-size: .82rem; font-weight: 700; color: var(--primary-color); text-decoration: none; }
      .basket-item-actions .icon-button { width: 36px; height: 36px; border-radius: 11px; }
      .draft-summary-grid { margin: 0 22px 18px; }
      .basket-remove { width: 34px; height: 34px; border-radius: 10px; }
      .basket-empty { min-height: 230px; padding: 20px; border: 1px dashed var(--divider-color); border-radius: 16px; display: flex; align-items: center; justify-content: center; flex-direction: column; text-align: center; gap: 8px; color: var(--secondary-text-color); margin-bottom: 16px; }
      .basket-empty ha-icon { --mdc-icon-size: 40px; color: var(--primary-color); }
      .basket-empty strong { color: var(--primary-text-color); }
      .basket-empty span { font-size: 12px; line-height: 1.45; }
      .full-width { width: 100%; }
      .basket-footnote { margin: 10px 2px 0; color: var(--secondary-text-color); font-size: 11px; line-height: 1.45; }
      .assistant-diagnostics-body { padding: 8px 22px 6px; }
      .diagnostics-grid { margin-bottom: 18px; }
      .diagnostics-section { padding: 16px 0; border-top: 1px solid var(--divider-color); }
      .diagnostics-section h3 { margin: 0 0 8px; }
      .diagnostics-section p { margin: 0; color: var(--secondary-text-color); line-height: 1.5; }
      .diagnostics-plugin-list { display: flex; flex-wrap: wrap; gap: 8px; }
      .diagnostics-records { display: grid; gap: 9px; }
      .diagnostics-record { padding: 12px; border: 1px solid var(--divider-color); border-left-width: 4px; border-radius: 13px; background: var(--secondary-background-color); overflow: hidden; }
      .diagnostics-record.ok { border-left-color: var(--success-color, #2e7d32); }
      .diagnostics-record.error { border-left-color: var(--warning-color, #f57c00); }
      .diagnostics-record > div { display: flex; justify-content: space-between; gap: 12px; }
      .diagnostics-record > div span { color: var(--secondary-text-color); font-size: 11px; }
      .diagnostics-record p, .diagnostics-record small { display: block; margin: 5px 0 0; color: var(--secondary-text-color); overflow-wrap: anywhere; }
      .diagnostics-record small { font-family: var(--code-font-family, monospace); white-space: pre-wrap; }
      .diagnostics-record .diagnostic-error { color: var(--error-color, #d32f2f); }
      .inherited-stop { border-style: dashed; background: color-mix(in srgb, var(--primary-color) 4%, var(--card-background-color)); }
      .inherited-badge { margin: 10px 0 0 38px; padding: 7px 9px; border-radius: 10px; display: inline-flex; align-items: center; gap: 6px; color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 10%, transparent); font-size: 11px; font-weight: 700; }
      .inherited-badge ha-icon { --mdc-icon-size: 16px; }
      .assistant-input-actions { display: flex; align-items: center; gap: 8px; }
      .assistant-attach { flex: 0 0 auto; border: 1px solid var(--divider-color); background: var(--secondary-background-color); }
      .archive-toolbar { align-items: flex-start; }
      .archive-toolbar-actions { justify-content: flex-end; }
      .archive-stats { margin-top: 0; }
      .archive-summary-card { border-left: 4px solid var(--primary-color); }
      .section-count { min-width: 36px; height: 36px; display: grid; place-items: center; border-radius: 12px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-weight: 800; }
      .archive-paste-zone { min-height: 128px; border: 2px dashed var(--divider-color); border-radius: 18px; display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 14px; padding: 20px; background: var(--secondary-background-color); cursor: text; outline: none; }
      .archive-paste-zone:focus, .archive-paste-zone.drag-active { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 7%, var(--secondary-background-color)); }
      .archive-paste-zone > ha-icon { color: var(--primary-color); --mdc-icon-size: 34px; }
      .archive-paste-zone strong, .archive-paste-zone span { display: block; }
      .archive-paste-zone span { margin-top: 4px; color: var(--secondary-text-color); line-height: 1.45; }
      .archive-card-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
      .archive-document-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; padding: 16px; border: 1px solid var(--divider-color); border-radius: 18px; background: var(--secondary-background-color); min-width: 0; }
      .archive-card-icon, .archive-row-icon { width: 46px; height: 46px; display: grid; place-items: center; border-radius: 14px; color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); }
      .archive-card-icon ha-icon { --mdc-icon-size: 27px; }
      .archive-card-main { min-width: 0; }
      .archive-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
      .archive-card-heading > div { min-width: 0; }
      .archive-card-heading span:not(.status-badge) { color: var(--secondary-text-color); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
      .archive-card-heading h3 { margin: 3px 0 0; font-size: 17px; overflow-wrap: anywhere; }
      .archive-card-main > p { margin: 10px 0; color: var(--secondary-text-color); line-height: 1.45; }
      .archive-card-meta { display: flex; flex-wrap: wrap; gap: 7px 12px; color: var(--secondary-text-color); font-size: 11px; }
      .archive-card-meta span { display: inline-flex; align-items: center; gap: 4px; }
      .archive-card-meta ha-icon { --mdc-icon-size: 15px; }
      .archive-card-actions { margin-top: 12px; }
      .archive-list { display: grid; gap: 9px; }
      .archive-row { display: grid; grid-template-columns: auto minmax(0, 1fr) auto auto; gap: 12px; align-items: center; padding: 12px; border: 1px solid var(--divider-color); border-radius: 15px; background: var(--secondary-background-color); }
      .archive-row-copy { min-width: 0; }
      .archive-row-copy strong, .archive-row-copy span, .archive-row-copy small { display: block; }
      .archive-row-copy span, .archive-row-copy small { color: var(--secondary-text-color); font-size: 11px; margin-top: 3px; line-height: 1.35; }
      .archive-row-value { text-align: right; white-space: nowrap; }
      .archive-row-value strong, .archive-row-value span { display: block; }
      .archive-row-value span { color: var(--secondary-text-color); font-size: 11px; margin-top: 3px; }
      .archive-row-actions { display: flex; align-items: center; }
      .archive-row-actions .icon-button { width: 38px; height: 38px; border-radius: 11px; }
      .todo-check { width: 44px; height: 44px; display: grid; place-items: center; border: 0; background: transparent; color: var(--primary-color); cursor: pointer; }
      .archive-todo-row.done { opacity: .62; }
      .archive-todo-row.done .archive-row-copy strong { text-decoration: line-through; }
      .archive-todo-row.due-overdue { border-color: color-mix(in srgb, var(--error-color, #d32f2f) 55%, var(--divider-color)); }
      .archive-todo-row.due-today { border-color: color-mix(in srgb, var(--warning-color, #f57c00) 55%, var(--divider-color)); }
      .todo-badges { display: grid; justify-items: end; gap: 5px; }
      .due-badge, .priority-badge { padding: 5px 8px; border-radius: 999px; font-size: 10px; font-weight: 800; background: var(--secondary-background-color); white-space: nowrap; }
      .due-badge.due-overdue { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .due-badge.due-today, .due-badge.due-upcoming { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 12%, transparent); }
      .priority-badge { background: var(--secondary-background-color); }
      .priority-high { color: var(--error-color, #d32f2f); background: color-mix(in srgb, var(--error-color, #d32f2f) 12%, transparent); }
      .priority-low { color: var(--secondary-text-color); }
      .status-badge.status-success { color: var(--success-color, #2e7d32); background: color-mix(in srgb, var(--success-color, #2e7d32) 13%, transparent); }
      .status-badge.status-warning { color: var(--warning-color, #f57c00); background: color-mix(in srgb, var(--warning-color, #f57c00) 13%, transparent); }
      .status-badge.status-info { color: var(--info-color, var(--primary-color)); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
      .warning-text { color: var(--warning-color, #f57c00); }
      .day-archive-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }
      .day-archive-grid > div { padding: 14px; border-radius: 15px; background: var(--secondary-background-color); min-width: 0; }
      .archive-mini-heading { display: block; margin-bottom: 8px; color: var(--secondary-text-color); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }
      .archive-mini-item { width: 100%; border: 0; border-top: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); display: grid; grid-template-columns: auto minmax(0, 1fr); align-items: center; gap: 8px; padding: 9px 0; text-align: left; cursor: pointer; }
      .archive-mini-item:first-of-type { border-top: 0; }
      .archive-mini-item ha-icon { color: var(--primary-color); --mdc-icon-size: 20px; }
      .archive-mini-item strong, .archive-mini-item small { display: block; overflow-wrap: anywhere; }
      .archive-mini-item small { color: var(--secondary-text-color); margin-top: 2px; }
      .archive-day-total { display: block; font-size: 21px; margin-bottom: 3px; }
      .stop-archive-summary { margin-top: 10px; }
      .stop-archive-counts { display: flex; flex-wrap: wrap; gap: 7px; }
      .stop-archive-counts span { display: inline-flex; align-items: center; gap: 4px; padding: 5px 8px; border-radius: 999px; background: var(--secondary-background-color); color: var(--secondary-text-color); font-size: 11px; }
      .stop-archive-counts ha-icon { --mdc-icon-size: 15px; }
      .stop-archive-actions { margin-top: 7px; }
      .stop-archive-actions .text-button { min-height: 34px; padding: 5px 8px; font-size: 11px; }
      .checkbox-field { min-height: 52px; display: flex; align-items: flex-start; gap: 10px; padding: 11px; border: 1px solid var(--divider-color); border-radius: 13px; background: var(--secondary-background-color); }
      .checkbox-field input { width: 20px; height: 20px; margin-top: 2px; accent-color: var(--primary-color); }
      .checkbox-field span, .checkbox-field strong, .checkbox-field small { display: block; }
      .checkbox-field small { margin-top: 3px; color: var(--secondary-text-color); line-height: 1.35; }
      .archive-review-form .form-section { margin-top: 6px; }
      .archive-analysis-todos { display: grid; gap: 12px; }
      .archive-analysis-todo { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; padding: 14px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--secondary-background-color); }
      .archive-analysis-todo .checkbox-field, .archive-analysis-todo .form-field.full { grid-column: 1 / -1; }
      .count-badge.info { background: color-mix(in srgb, var(--primary-color) 88%, white); }
      .message-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
      .message-actions .text-button { min-height: 34px; padding: 6px 10px; font-size: 11px; }
      .decision-intro, .media-toolbar { display: flex; justify-content: space-between; gap: 20px; align-items: center; }
      .decision-intro h2, .media-toolbar h2 { margin: 4px 0 6px; }
      .decision-list { display: grid; gap: 20px; }
      .decision-card { overflow: hidden; padding: 0; }
      .decision-heading { display: flex; justify-content: space-between; gap: 18px; padding: 20px 22px 14px; }
      .decision-heading h2 { margin: 3px 0 5px; }
      .decision-heading p { margin: 0; color: var(--secondary-text-color); }
      .decision-counter { flex: 0 0 auto; align-self: flex-start; padding: 7px 10px; border-radius: 999px; background: var(--secondary-background-color); font-size: 12px; font-weight: 800; }
      .decision-slide { display: grid; grid-template-columns: minmax(280px, .9fr) minmax(320px, 1.1fr); min-height: 420px; }
      .decision-image { position: relative; min-height: 360px; background: var(--secondary-background-color); overflow: hidden; display: grid; place-items: center; }
      .decision-image img { width: 100%; height: 100%; object-fit: cover; position: absolute; inset: 0; }
      .decision-image small { position: absolute; left: 10px; right: 10px; bottom: 10px; padding: 5px 8px; border-radius: 8px; background: rgba(0,0,0,.58); color: white; font-size: 10px; z-index: 1; }
      .decision-image.empty { color: var(--secondary-text-color); align-content: center; gap: 10px; text-align: center; padding: 24px; }
      .decision-image.empty ha-icon { --mdc-icon-size: 56px; }
      .decision-copy { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
      .decision-title-row { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
      .decision-title-row h3 { font-size: 25px; margin: 3px 0 0; }
      .decision-copy > p { margin: 0; line-height: 1.55; }
      .decision-metrics { display: flex; flex-wrap: wrap; gap: 8px; }
      .decision-metrics span { display: inline-flex; align-items: center; gap: 6px; padding: 7px 10px; border-radius: 999px; background: var(--secondary-background-color); font-size: 12px; font-weight: 700; }
      .decision-metrics ha-icon { --mdc-icon-size: 18px; color: var(--primary-color); }
      .decision-procon { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
      .decision-procon > div { padding: 14px; border-radius: 14px; background: var(--secondary-background-color); }
      .decision-procon ul { margin: 8px 0 0; padding-left: 18px; color: var(--secondary-text-color); }
      .decision-procon li + li { margin-top: 4px; }
      .decision-actions { margin-top: auto; }
      .decision-footer { display: grid; grid-template-columns: auto 1fr auto auto; align-items: center; gap: 10px; padding: 12px 18px; border-top: 1px solid var(--divider-color); }
      .decision-dots { display: flex; justify-content: center; gap: 7px; }
      .decision-dot { width: 10px; height: 10px; border-radius: 999px; border: 0; background: var(--divider-color); padding: 0; cursor: pointer; }
      .decision-dot.active { width: 24px; background: var(--primary-color); }
      .media-toolbar-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }
      .import-toolbar { align-items: flex-start; }
      .import-toolbar > div:first-child { max-width: 760px; }
      .import-stats { margin-top: 0; }
      .import-explainer { border-left: 4px solid var(--primary-color); }
      .import-flow { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 12px 0; }
      .import-flow span { padding: 8px 11px; border-radius: 999px; background: var(--secondary-background-color); font-weight: 700; font-size: 12px; }
      .import-flow ha-icon { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
      .import-card-grid { display: grid; gap: 14px; }
      .import-card { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; }
      .import-card-icon { width: 52px; height: 52px; display: grid; place-items: center; border-radius: 16px; background: color-mix(in srgb, var(--primary-color) 12%, var(--card-background-color)); color: var(--primary-color); }
      .import-card-icon ha-icon { --mdc-icon-size: 30px; }
      .import-card-copy { min-width: 0; }
      .import-card-copy p { color: var(--secondary-text-color); line-height: 1.5; }
      .import-card-title { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
      .import-card-title h3 { margin: 2px 0 0; overflow-wrap: anywhere; }
      .attachment-purpose-body, .universal-import-review-body { padding: 8px 22px 22px; display: grid; gap: 16px; }
      .attachment-summary { display: flex; align-items: center; gap: 12px; padding: 14px; border-radius: 16px; background: var(--secondary-background-color); }
      .attachment-summary ha-icon { --mdc-icon-size: 34px; color: var(--primary-color); }
      .attachment-summary span, .attachment-purpose-card small { display: block; margin-top: 4px; color: var(--secondary-text-color); }
      .attachment-purpose-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
      .attachment-purpose-card { appearance: none; border: 1px solid var(--divider-color); border-radius: 18px; padding: 18px; background: var(--secondary-background-color); color: inherit; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 14px; text-align: left; cursor: pointer; }
      .attachment-purpose-card:hover, .attachment-purpose-card:focus-visible { border-color: var(--primary-color); outline: none; background: color-mix(in srgb, var(--primary-color) 6%, var(--secondary-background-color)); }
      .attachment-purpose-card ha-icon { --mdc-icon-size: 32px; color: var(--primary-color); }
      .import-review-section { border: 1px solid var(--divider-color); border-radius: 16px; padding: 14px; }
      .import-review-section h3 { margin: 0 0 8px; }
      .import-review-section p, .import-review-section ul { margin: 0; line-height: 1.55; color: var(--secondary-text-color); }
      .import-preview-list { display: grid; gap: 8px; max-height: 320px; overflow: auto; }
      .import-preview-item { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 10px; align-items: start; padding: 10px; border-radius: 12px; background: var(--secondary-background-color); }
      .import-preview-item ha-icon { color: var(--primary-color); }
      .import-preview-item span { display: block; margin-top: 3px; color: var(--secondary-text-color); font-size: 12px; }
      .universal-import-actions { flex-wrap: wrap; }
      .onedrive-setup-form code { font-family: var(--code-font-family, monospace); }
      .setup-steps { margin: 8px 0 14px; padding-left: 22px; display: grid; gap: 7px; color: var(--secondary-text-color); line-height: 1.45; }
      .inline-link-button { display: inline-flex; width: fit-content; text-decoration: none; }
      .media-stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
      .media-stat { display: flex; gap: 13px; align-items: center; min-height: 90px; }
      .media-stat > ha-icon { --mdc-icon-size: 32px; color: var(--primary-color); }
      .media-stat strong, .media-stat span { display: block; }
      .media-stat strong { font-size: 24px; }
      .media-stat span { color: var(--secondary-text-color); font-size: 12px; }
      .media-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
      .media-card { border: 1px solid var(--divider-color); border-radius: 18px; background: var(--card-background-color); overflow: hidden; display: grid; grid-template-rows: 190px auto auto; box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,.12)); }
      .media-card.cover { border-color: color-mix(in srgb, var(--primary-color) 60%, var(--divider-color)); }
      .media-thumb { position: relative; width: 100%; height: 190px; border: 0; padding: 0; background: var(--secondary-background-color); cursor: pointer; overflow: hidden; }
      .media-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .cover-badge { position: absolute; top: 9px; left: 9px; display: inline-flex; align-items: center; gap: 4px; padding: 5px 8px; border-radius: 999px; background: rgba(0,0,0,.68); color: white; font-size: 10px; font-weight: 800; }
      .cover-badge ha-icon { --mdc-icon-size: 15px; }
      .media-card-copy { padding: 12px 14px 7px; min-width: 0; }
      .media-card-title { display: flex; justify-content: space-between; gap: 8px; align-items: flex-start; }
      .media-card-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .media-card-copy > span, .media-card-copy > small { display: block; color: var(--secondary-text-color); margin-top: 5px; font-size: 11px; }
      .media-card-actions { display: flex; justify-content: flex-end; gap: 4px; padding: 4px 8px 10px; }
      .media-card-actions .icon-button { width: 38px; height: 38px; }
      .onedrive-auth-body { display: grid; justify-items: center; gap: 15px; padding: 26px 24px; text-align: center; }
      .onedrive-auth-body > ha-icon { --mdc-icon-size: 58px; color: #0078d4; }
      .device-code { font-size: 31px; letter-spacing: .12em; font-weight: 900; padding: 14px 18px; border-radius: 14px; background: var(--secondary-background-color); user-select: all; }
      .experience-album { display: grid; gap: 10px; }
      .experience-album-heading { display: flex; justify-content: space-between; align-items: end; gap: 12px; }
      .experience-album-heading > div { display: grid; gap: 2px; }
      .experience-album-heading small { color: var(--secondary-text-color); }
      .experience-album-strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(92px, 1fr)); gap: 8px; }
      .experience-album.compact { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
      .experience-album.compact .experience-album-strip { grid-template-columns: repeat(5, minmax(0, 1fr)); }
      .experience-album-thumb { position: relative; border: 0; padding: 0; min-height: 78px; border-radius: 10px; overflow: hidden; background: var(--secondary-background-color); cursor: pointer; }
      .experience-album-thumb img { width: 100%; height: 100%; min-height: 78px; max-height: 130px; object-fit: cover; display: block; }
      .experience-album-thumb ha-icon { position: absolute; top: 5px; right: 5px; color: #fff; filter: drop-shadow(0 1px 3px #000); }
      .stop-experience-cover { position: relative; width: 100%; min-height: 180px; border: 0; padding: 0; overflow: hidden; background: var(--secondary-background-color); cursor: pointer; }
      .stop-experience-cover img { width: 100%; height: 100%; min-height: 180px; max-height: 260px; object-fit: cover; display: block; }
      .stop-experience-cover span { position: absolute; left: 10px; bottom: 10px; display: inline-flex; align-items: center; gap: 6px; padding: 6px 9px; border-radius: 999px; background: rgba(0,0,0,.68); color: #fff; font-size: 12px; font-weight: 700; }
      .day-experience-album { margin-top: 16px; }
      .media-gallery { padding: 0 18px 14px; }
      .media-gallery-stage { height: min(62vh, 680px); min-height: 320px; display: grid; place-items: center; background: #111; border-radius: 16px; overflow: hidden; }
      .media-gallery-stage img { max-width: 100%; max-height: 100%; object-fit: contain; }
      .media-gallery-caption { display: grid; gap: 4px; padding: 12px 4px 0; }
      .media-gallery-caption span, .media-gallery-caption small { color: var(--secondary-text-color); }
      .media-gallery-actions { justify-content: center; }
      .onedrive-sync-notice > div { min-width: 0; display: grid; gap: 5px; }
      .onedrive-sync-notice span, .onedrive-sync-notice small { line-height: 1.45; overflow-wrap: anywhere; word-break: break-word; }
      .onedrive-current-folder { display: -webkit-box; max-width: 100%; overflow: hidden; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; word-break: break-word; }
      .onedrive-current-folder b { font-weight: 800; }
      .muted { color: var(--secondary-text-color); }

      /* 2.6.3: keep the panel inside the real HA/webview width. */
      :host { width: 100%; max-width: 100%; min-width: 0; container-type: inline-size; }
      .app, .content { width: 100%; max-width: 100%; min-width: 0; }
      .content { overflow-x: hidden; }
      .content > * { min-width: 0; max-width: 100%; }
      .topbar { width: 100%; max-width: 100%; min-width: 0; overflow: hidden; }
      .topbar-start { flex: 1 1 0; min-width: 0; overflow: hidden; }
      .topbar-actions { flex: 0 1 auto; min-width: 0; max-width: 48%; overflow: hidden; }
      .trip-select { max-width: 100%; min-width: 0; }
      .assistant-layout,
      .assistant-chat,
      .assistant-thread,
      .assistant-composer,
      .assistant-input-row { width: 100%; max-width: 100%; min-width: 0; }
      .assistant-thread { overflow-x: hidden; }
      .assistant-message { min-width: 0; }
      .message-body { flex: 1 1 auto; width: auto; max-width: 100%; min-width: 0; overflow: hidden; }
      .message-meta { min-width: 0; flex-wrap: wrap; }
      .message-text, .message-basket-status, .message-sources { min-width: 0; max-width: 100%; }
      .message-text { word-break: break-word; }
      .message-sources a { min-width: 0; }
      .assistant-input-row textarea { min-width: 0; max-width: 100%; }

      @container (max-width: 720px) {
        .assistant-layout { grid-template-columns: minmax(0, 1fr); }
        .assistant-basket { position: static; }
        .assistant-message { width: 100%; max-width: 100%; }
        .assistant-message .message-body { flex: 1 1 0%; max-width: calc(100% - 45px); }
        .assistant-thread { padding-left: 10px; padding-right: 10px; }
        .assistant-input-row { grid-template-columns: minmax(0, 1fr); }
        .assistant-input-actions { width: 100%; min-width: 0; }
        .assistant-send { width: 100%; }
        .topbar-actions .icon-button[data-action="refresh"] { display: none; }
        .trip-select { width: min(36vw, 140px); }
      }

      @media (max-width: 900px) {
        .assistant-layout { grid-template-columns: 1fr; }
        .assistant-status-grid { grid-template-columns: 1fr; }
        .assistant-basket { position: static; }
        .assistant-chat { min-height: 620px; }
        .hero-card.with-image { grid-template-columns: 1fr; }
        .hero-image { max-height: 340px; }
        .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .route-layout { grid-template-columns: 1fr; }
        .day-facts { position: static; }
        .trip-select { min-width: 0; width: min(42vw, 280px); }
      }
      @media (max-width: 680px) {
        .assistant-toolbar { align-items: stretch; flex-direction: column; }
        .assistant-toolbar-primary .assistant-main-actions { display: grid; grid-template-columns: 1fr; }
        .assistant-toolbar-primary .assistant-main-actions button { width: 100%; }
        .assistant-technical > summary { align-items: flex-start; flex-direction: column; }
        .assistant-technical-actions { align-items: stretch; flex-direction: column; }
        .assistant-technical-actions > * { width: 100%; justify-content: center; }
        .assistant-composer-heading { align-items: flex-start; flex-direction: column; gap: 4px; }
        .assistant-toolbar-actions { justify-content: flex-start; }
        .assistant-retry-notice { align-items: flex-start; flex-direction: column; }
        .assistant-retry-notice button { margin-left: 0; width: 100%; }
        .assistant-chat { min-height: calc(100vh - 210px); }
        .assistant-thread { padding: 14px 10px; overflow-x: hidden; }
        .assistant-message { width: 100%; max-width: 100%; min-width: 0; }
        .assistant-message .message-body { flex: 1 1 0%; max-width: calc(100% - 45px); }
        .quick-prompt-grid { grid-template-columns: 1fr; }
        .quick-prompt-grid button { min-height: 76px; }
        .assistant-input-row { grid-template-columns: 1fr; }
        .assistant-input-actions { width: 100%; }
        .assistant-attach { width: 52px; }
        .assistant-send { width: 100%; }
        .archive-card-grid, .day-archive-grid, .attachment-purpose-grid { grid-template-columns: 1fr; }
        .import-card { grid-template-columns: 1fr; }
        .import-card-icon { width: 46px; height: 46px; }
        .import-card-title { flex-direction: column; }
        .import-toolbar { align-items: stretch; flex-direction: column; }
        .archive-row { grid-template-columns: auto minmax(0, 1fr) auto; }
        .archive-row-value { grid-column: 2; text-align: left; }
        .archive-row-actions { grid-column: 3; grid-row: 1 / span 2; flex-direction: column; }
        .archive-analysis-todo { grid-template-columns: 1fr; }
        .archive-analysis-todo .checkbox-field, .archive-analysis-todo .form-field.full { grid-column: auto; }
        .decision-intro, .media-toolbar { align-items: stretch; flex-direction: column; }
        .decision-slide { grid-template-columns: 1fr; }
        .experience-album.compact .experience-album-strip { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .decision-image { min-height: 280px; }
        .decision-procon { grid-template-columns: 1fr; }
        .decision-footer { grid-template-columns: auto 1fr auto; }
        .decision-footer > .text-button { grid-column: 1 / -1; }
        .media-stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .media-toolbar-actions { justify-content: flex-start; }
        .assistant-setup { grid-template-columns: 1fr; }
        .topbar { padding-left: 8px; padding-right: 8px; min-height: 58px; }
        .menu-button { display: grid; }
        .app-icon { display: none; }
        .title-group h1 { font-size: 17px; }
        .view-badge { display: none; }
        .trip-select { width: min(36vw, 140px); max-width: 100%; padding: 5px 7px; }
        .topbar-actions .icon-button[data-action="refresh"] { display: none; }
        .trip-select ha-icon { display: none; }
        .topbar-actions { gap: 2px; }
        .tabs { padding: 0 4px; }
        .tab { padding: 0 12px; min-height: 50px; }
        .tab span:not(.count-badge) { font-size: 12px; }
        .content { padding: 14px 10px max(24px, calc(14px + env(safe-area-inset-bottom))); }
        .panel-card, .toolbar-card, .route-flow-card, .handoff-card { padding: 16px; border-radius: 18px; }
        .toolbar-card, .day-toolbar { align-items: stretch; flex-direction: column; }
        .day-toolbar .day-select { min-width: 0; }
        .hero-copy { padding: 22px 18px; }
        .hero-copy h2 { font-size: 31px; }
        .stat-grid { gap: 9px; }
        .stat-card { min-height: 105px; padding: 14px; border-radius: 16px; }
        .stat-card strong { font-size: 21px; }
        .next-day-grid, .facts-grid, .preview-grid { grid-template-columns: 1fr; }
        .map-stage { height: 46vh; min-height: 300px; }
        .image-gallery, .stop-grid, .trip-grid, .image-search-grid { grid-template-columns: 1fr; }
        .total-day-card { grid-template-columns: 44px 1fr auto; }
        .total-day-image { display: none; }
        .form-grid { grid-template-columns: 1fr; padding: 8px 16px 18px; }
        .form-field.full, .form-section.full, .modal-actions.full { grid-column: auto; }
        .modal-backdrop { align-items: flex-end; padding: 0; }
        .modal { width: 100%; max-height: 92%; border-radius: 24px 24px 0 0; padding-bottom: env(safe-area-inset-bottom); }
        .modal-header { padding: 18px 16px 10px; }
        .modal-actions { padding-left: 16px; padding-right: 16px; }
        .toast-host { left: 10px; right: 10px; top: max(10px, env(safe-area-inset-top)); }
        .toast { max-width: 100%; }
        .view-notice { align-items: flex-start; flex-wrap: wrap; }
        .compact-button { margin-left: 0; width: 100%; }
      }
      @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }
      }
    </style>`;
  }
}

if (!customElements.get("roadplanner-panel")) {
  customElements.define("roadplanner-panel", RoadplannerPanel);
}
