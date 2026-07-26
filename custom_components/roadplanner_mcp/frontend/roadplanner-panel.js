import { PANEL_STYLES } from "./lib/styles.js";
import {
  WS_GET_DATA,
  WS_ACTION,
  operationLabels,
  statusLabels,
  archiveDocumentTypeLabels,
  archiveExpenseCategoryLabels,
  archiveStatusLabels,
  stopIcons,
} from "./lib/constants.js";
import {
  escapeHtml,
  cleanText,
  newClientRequestId,
  nullableNumber,
  cloneObject,
} from "./lib/core-helpers.js";
import { universalImportMixin } from "./features/universal-import.js";
import { placeEnrichmentMixin } from "./features/place-enrichment.js";
import { archiveMixin } from "./features/archive.js";
import { mediaMixin } from "./features/media.js";
import { decisionsIntegrityMixin } from "./features/decisions-integrity.js";

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
    this._assistantLastFailedAt = 0;
    this._assistantDiagnostics = null;
    this._assistantAutoBriefingRequested = new Set();
    this._assistantSubmitInFlight = false;
    this._assistantPrepareInFlight = false;
    this._assistantPending = null;
    this._decisionCreateInFlightMessageId = "";
    this._actionErrorRetry = null;
    this._archiveUploadContext = null;
    this._offlineDocumentIds = new Set();
    this._archiveDbPromise = null;
    this._decisionSlideIndexes = new Map();
    this._decisionSwipe = null;
    this._destinationGallerySwipe = null;
    this._destinationAutoFillRequested = new Set();
    this._destinationAutoFillInFlight = false;
    this._onedriveAuth = null;

    this.shadowRoot.addEventListener("pointerdown", (event) => {
      const button = event.target?.closest?.("[data-action='assistant-send']");
      if (button) {
        event.preventDefault();
        event.stopPropagation();
        void this._submitAssistantComposer(button.closest("form"));
        return;
      }
      const prepareButton = event.target?.closest?.("[data-action='assistant-prepare']");
      if (prepareButton && !prepareButton.disabled) {
        event.preventDefault();
        event.stopPropagation();
        void this._prepareAssistantChanges();
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
    this.shadowRoot.addEventListener("pointerdown", (event) => {
      const stage = event.target?.closest?.("[data-destination-gallery-stage]");
      if (!stage || event.target?.closest?.("button, a")) return;
      this._destinationGallerySwipe = {
        pointerId: event.pointerId,
        x: event.clientX,
        y: event.clientY,
      };
    });
    this.shadowRoot.addEventListener("pointerup", (event) => {
      const swipe = this._destinationGallerySwipe;
      this._destinationGallerySwipe = null;
      if (!swipe || swipe.pointerId !== event.pointerId) return;
      const dx = event.clientX - swipe.x;
      const dy = event.clientY - swipe.y;
      if (Math.abs(dx) < 55 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
      this._stepDestinationGallery(dx < 0 ? 1 : -1);
    });
    this.shadowRoot.addEventListener("pointercancel", () => {
      this._decisionSwipe = null;
      this._destinationGallerySwipe = null;
    });
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
      const assistantForm = event.target
        ?.closest?.("textarea[name='message']")
        ?.closest("form[data-form='assistant-chat']");
      if (!zone && !assistantForm && this._dialog?.type !== "archive-paste-text") return;
      if (assistantForm && !this._canEdit()) return;
      const file = this._clipboardFileFromData(event.clipboardData);
      if (!file) return;
      event.preventDefault();
      if (!assistantForm) this._closeDialog({ flushRefresh: false });
      void this._uploadArchiveFile(file, {
        source: assistantForm ? "assistant" : "clipboard_paste",
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
        void this._maybeAutoPopulateDestinationGalleries(payload);
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

  async _prepareDayLocations(dayId) {
    const id = cleanText(dayId);
    if (!id) return null;
    const retry = () => this._prepareDayLocations(id);
    const result = await this._runAction("assistant_prepare_locations", {
      trip_id: this._selectedTripId,
      day_id: id,
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "GPS-Vervollständigung konnte nicht vorbereitet werden",
      retry,
    });
    if (!result) return null;
    if (result.assistant && this._data) {
      this._data = { ...this._data, assistant: result.assistant };
      this._signature = "";
    }
    this._activeTab = "assistant";
    this._showToast(`${Number(result.draft_count || 0)} GPS-Änderungen vorgemerkt`, "success", 4500);
    this._render({ preserveScroll: false });
    return result;
  }

  async _prepareTripLocations() {
    const retry = () => this._prepareTripLocations();
    const result = await this._runAction("assistant_prepare_trip_locations", {
      trip_id: this._selectedTripId,
    }, "", {
      refresh: false,
      errorMode: "dialog",
      errorTitle: "GPS-Vervollständigung konnte nicht vorbereitet werden",
      retry,
    });
    if (!result) return null;
    if (result.assistant && this._data) {
      this._data = { ...this._data, assistant: result.assistant };
      this._signature = "";
    }
    this._dialog = null;
    this._activeTab = "assistant";
    this._showToast(`${Number(result.draft_count || 0)} GPS-Änderungen für ${Number(result.day_count || 0)} Tage vorgemerkt`, "success", 5200);
    this._render({ preserveScroll: false });
    return result;
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
    const technicalMessage = cleanText(this._errorMessage(message)) || "Unbekannter Roadplanner-Fehler";
    const normalizedAction = cleanText(action);
    const referenceFailure = normalizedAction === "assistant_prepare"
      && /(day_ref|day_id|Tages-ID|Tagesreferenz|Reisetag)/i.test(technicalMessage);
    const visibleMessage = referenceFailure
      ? "Die Zuordnung eines Reisetags war nicht eindeutig. Bitte versuche es erneut oder bearbeite den betreffenden Stopp."
      : technicalMessage;
    this._actionErrorRetry = typeof retry === "function" ? retry : null;
    this._dialog = {
      type: "action-error",
      title: cleanText(title) || "Roadplanner-Aktion fehlgeschlagen",
      message: visibleMessage,
      technicalMessage,
      requestId: this._requestIdFromMessage(technicalMessage),
      action: normalizedAction,
    };
    this._render({ preserveScroll: true });
  }

  async _copyActionError() {
    const dialog = this._dialog?.type === "action-error" ? this._dialog : null;
    if (!dialog) return;
    const text = [
      dialog.title,
      dialog.technicalMessage || dialog.message,
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

  _stopTimeMinutes(stop) {
    const value = cleanText(stop?.arrival_time || stop?.departure_time);
    const match = /^(\d{2}):(\d{2})(?::\d{2})?$/.exec(value);
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    if (!Number.isInteger(hour) || !Number.isInteger(minute) || hour > 23 || minute > 59) return null;
    return hour * 60 + minute;
  }

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
  }

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
  }

  _stopMovePosition(day, stopId, delta) {
    const state = this._stopOrderState(day, stopId);
    if (!Number.isInteger(delta) || ![-1, 1].includes(delta) || state.index < 0) return null;
    const position = state.index + 1 + delta;
    if (position < 1 || position > state.stops.length) return null;
    return position;
  }

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

  _canonicalDayModel(day) {
    return day?.canonical && typeof day.canonical === "object" && !Array.isArray(day.canonical)
      ? day.canonical
      : null;
  }

  _dayRoadbookStops(day) {
    const model = this._canonicalDayModel(day);
    if (Array.isArray(model?.stops)) return model.stops;
    return this._canonicalStops(day?.stops || []);
  }

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
  }

  _displayStopSequence(stop, fallback) {
    if (stop?._inherited) return "S";
    const value = Number(stop?.display_sequence);
    return Number.isInteger(value) && value > 0 ? value : fallback;
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

  _normalizeAssistantMarkdownUrl(value) {
    return String(value ?? "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, "")
      .trim();
  }

  _assistantMarkdownLinks(value) {
    const text = String(value ?? "");
    const links = [];
    let cursor = 0;
    while (cursor < text.length) {
      const start = text.indexOf("[", cursor);
      if (start < 0) break;
      const labelEnd = text.indexOf("](", start + 1);
      if (labelEnd < 0 || labelEnd - start > 241) {
        cursor = start + 1;
        continue;
      }
      const label = text.slice(start + 1, labelEnd).replace(/\s+/g, " ").trim();
      if (!label) {
        cursor = start + 1;
        continue;
      }
      const urlStart = labelEnd + 2;
      let depth = 0;
      let end = -1;
      for (let index = urlStart; index < text.length; index += 1) {
        const character = text[index];
        if (character === "(") depth += 1;
        else if (character === ")") {
          if (depth > 0) depth -= 1;
          else {
            end = index;
            break;
          }
        }
      }
      if (end < 0) break;
      const rawUrl = text.slice(urlStart, end);
      const normalizedUrl = this._normalizeAssistantMarkdownUrl(rawUrl);
      if (/^https?:\/\//i.test(normalizedUrl)) {
        links.push({
          start,
          end: end + 1,
          label,
          url: normalizedUrl,
          raw: text.slice(start, end + 1),
        });
      }
      cursor = end + 1;
    }
    return links;
  }

  _renderAssistantContent(value) {
    const text = String(value ?? "");
    const markdownLinks = this._assistantMarkdownLinks(text);
    let cursor = 0;
    let output = "";
    for (const match of markdownLinks) {
      output += this._linkifyAssistantPlainText(text.slice(cursor, match.start));
      const candidate = this._trimAssistantUrlCandidate(match.url);
      const link = this._renderAssistantLink(candidate.url, match.label);
      output += link || this._linkifyAssistantPlainText(match.raw);
      output += escapeHtml(candidate.suffix);
      cursor = match.end;
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
      sequence: this._displayStopSequence(stop, index + 1),
      markerLabel: cleanText(stop?.marker_label) || String(this._displayStopSequence(stop, index + 1)),
      inherited: Boolean(stop?._inherited),
      timestamp,
    };
  }

  _dayRoutePoints(day) {
    return this._effectiveDayStops(day)
      .map((stop, index) => this._coordinate(stop, day, index))
      .filter(Boolean);
  }

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
  }

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
    const model = this._canonicalDayModel(day);
    if (cleanText(model?.start_label)) return cleanText(model.start_label);
    const stops = this._effectiveDayStops(day);
    return stops[0]?.name || day?.start || "?";
  }

  _effectiveDayEnd(day) {
    const model = this._canonicalDayModel(day);
    if (cleanText(model?.end_label)) return cleanText(model.end_label);
    const stops = this._effectiveDayStops(day);
    return stops.at(-1)?.name || day?.end || "?";
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
      for (const stop of this._canonicalStops(day.stops || [])) {
        const gallery = this._destinationGalleryForStop(stop.id);
        const primary = this._destinationGalleryPrimary(gallery);
        if (primary && !seen.has(primary.image_url)) {
          seen.add(primary.image_url);
          result.push({ ...primary, context: `${day.title} · ${stop.name}` });
        } else {
          add(stop, `${day.title} · ${stop.name}`);
        }
      }
      if (result.length >= limit) break;
    }
    return result.slice(0, limit);
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
    } else if (select.dataset.action === "move-stop-position" && this._canEdit()) {
      void this._moveStop(
        cleanText(select.dataset.dayId),
        cleanText(select.dataset.stopId),
        Number.parseInt(select.value, 10),
      );
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
    } else if (action === "integrity-open") {
      this._dialog = { type: "travel-integrity" };
      this._render({ preserveScroll: true });
    } else if (action === "integrity-refresh") {
      void this._loadData({ silent: true, force: true });
    } else if (action === "integrity-prepare-locations") {
      void this._preparePlaceEnrichment();
    } else if (action === "place-enrichment-select") {
      if (this._dialog?.type !== "place-enrichment") return;
      const selectedStopId = cleanText(target.dataset.stopId);
      const candidateId = cleanText(target.dataset.candidateId);
      if (!selectedStopId || !candidateId) return;
      this._dialog.selections = {
        ...(this._dialog.selections || {}),
        [selectedStopId]: candidateId,
      };
      this._render({ preserveScroll: true });
    } else if (action === "place-enrichment-ai-retry") {
      if (this._dialog?.type !== "place-enrichment") return;
      const scope = this._dialog.scope || {};
      void this._preparePlaceEnrichment({
        dayId: scope.dayId,
        stopId: scope.stopId,
        useAiCleanup: true,
      });
    } else if (action === "place-cleanup-toggle") {
      if (this._dialog?.type !== "place-enrichment") return;
      const selectedStopId = cleanText(target.dataset.stopId);
      if (!selectedStopId) return;
      const current = Boolean(this._dialog.cleanupConfirmations?.[selectedStopId]);
      this._dialog.cleanupConfirmations = {
        ...(this._dialog.cleanupConfirmations || {}),
        [selectedStopId]: !current,
      };
      this._render({ preserveScroll: true });
    } else if (action === "place-manual-select") {
      if (this._dialog?.type !== "place-enrichment") return;
      const form = target.closest("form[data-place-manual-form]");
      const selectedStopId = cleanText(target.dataset.stopId || form?.dataset.stopId);
      if (!form || !selectedStopId) return;
      const values = Object.fromEntries(new FormData(form).entries());
      if (!cleanText(values.latitude) || !cleanText(values.longitude)) {
        this._showToast("Breiten- und Längengrad werden für den manuellen Kartenpunkt benötigt", "error", 5000);
        return;
      }
      this._dialog.manualEntries = {
        ...(this._dialog.manualEntries || {}),
        [selectedStopId]: values,
      };
      this._dialog.selections = {
        ...(this._dialog.selections || {}),
        [selectedStopId]: "__manual__",
      };
      this._render({ preserveScroll: true });
    } else if (action === "place-enrichment-submit") {
      void this._submitPlaceEnrichment();
    } else if (action === "integrity-open-day") {
      const selectedDayId = cleanText(target.dataset.dayId);
      if (selectedDayId) this._selectedDayId = selectedDayId;
      this._dialog = null;
      this._activeTab = "day-route";
      this._render({ preserveScroll: false });
    } else if (action === "integrity-auto-images") {
      void (async () => {
        const result = await this._runAction("auto_populate_destination_galleries", {
          trip_id: this._selectedTripId,
          limit: 12,
        }, "Planungsbilder werden ergänzt");
        if (result) {
          this._dialog = { type: "travel-integrity" };
          this._render({ preserveScroll: true });
        }
      })();
    } else if (action === "integrity-recalculate-routes") {
      this._dialog = null;
      void this._calculateTripRoutes(true);
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
    } else if (action === "decision-gallery-open") {
      const decision = (this._experienceData().decisions || []).find((item) => item.id === target.dataset.decisionId);
      const option = (decision?.options || []).find((item) => item.id === target.dataset.optionId);
      const images = Array.isArray(option?.images) && option.images.length
        ? option.images.slice(0, 3)
        : (option?.image?.image_url ? [option.image] : []);
      if (images.length) {
        this._dialog = {
          type: "destination-gallery",
          images,
          index: Math.max(0, Math.min(images.length - 1, Number(target.dataset.imageIndex || 0))),
          title: option.title || decision?.title || "Entscheidungsbilder",
          readOnly: true,
        };
        this._render({ preserveScroll: true });
      }
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
    } else if (action === "destination-gallery-open") {
      const gallery = this._destinationGalleryForStop(stopId);
      const images = this._destinationGalleryImages(gallery);
      if (images.length) {
        const primary = this._destinationGalleryPrimary(gallery) || images[0];
        const ordered = [primary, ...images.filter((image) => image.id !== primary.id)];
        this._dialog = {
          type: "destination-gallery",
          images: ordered,
          index: Math.max(0, Math.min(ordered.length - 1, Number(target.dataset.imageIndex || 0))),
          title: this._findStop(dayId, stopId)?.name || "Stoppbilder",
          dayId,
          stopId,
          primaryImageId: gallery?.primary_image_id || primary.id,
          readOnly: !this._canEdit(),
        };
        this._render({ preserveScroll: true });
      }
    } else if (action === "destination-gallery-prev" || action === "destination-gallery-next") {
      this._stepDestinationGallery(action.endsWith("next") ? 1 : -1);
    } else if (action === "destination-gallery-primary") {
      void this._updateDestinationGalleryFromDialog({ primaryOnly: true });
    } else if (action === "destination-gallery-remove-image") {
      void this._updateDestinationGalleryFromDialog({ removeCurrent: true });
    } else if (action === "destination-gallery-move-left" || action === "destination-gallery-move-right") {
      void this._updateDestinationGalleryFromDialog({ move: action.endsWith("right") ? 1 : -1 });
    } else if (action === "destination-gallery-refresh") {
      void this._refreshDestinationGallery(dayId, stopId);
    } else if (action === "destination-gallery-delete") {
      this._confirm("Bildergalerie entfernen?", "Die externen Originalbilder werden nicht gelöscht. Nur die gespeicherte Auswahl für diesen Stopp wird entfernt.", "Galerie entfernen", () => this._runAction("delete_destination_gallery", { trip_id: this._selectedTripId, stop_id: stopId }, "Bildergalerie entfernt"), true);
    } else if (action === "media-curate-stop") {
      void this._runAction("media_curate_stop", {
        trip_id: this._selectedTripId,
        day_id: target.dataset.dayId || "",
        stop_id: target.dataset.stopId || "",
        force: true,
      }, "Bildauswahl aktualisiert", {
        errorMode: "dialog",
        errorTitle: "Bildauswahl konnte nicht aktualisiert werden",
      });
    } else if (action === "media-curate-trip") {
      void this._runAction("media_curate_trip", {
        trip_id: this._selectedTripId,
        force: true,
        limit: Number(target.dataset.limit || 5),
      }, "Bildauswahl für die Reise aktualisiert", {
        errorMode: "dialog",
        errorTitle: "Bildauswahl konnte nicht aktualisiert werden",
      });
    } else if (action === "media-open-album") {
      const dayId = target.dataset.dayId || "";
      const stopId = target.dataset.stopId || "";
      const media = stopId ? this._experienceAllMediaForStop(stopId) : this._experienceAllMediaForDay(dayId);
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
    } else if (action === "open-stop-order" && this._canEdit()) {
      const day = this._findDay(dayId);
      if (this._dayRoadbookStops(day).length < 2) {
        this._showToast("Für diesen Tag gibt es noch nichts umzusortieren.", "error");
        return;
      }
      this._dialog = { type: "stop-order", dayId };
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
        `${stop?.name || "Dieser Stopp"} wird aus diesem Reisetag entfernt. Verknüpfte Fotos und Dokumente bleiben erhalten.`,
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
      const delta = action.endsWith("up") ? -1 : 1;
      const position = this._stopMovePosition(day, stopId, delta);
      if (position === null) return;
      void this._moveStop(dayId, stopId, position);
    } else if (action === "complete-day-locations" && this._canEdit()) {
      void this._preparePlaceEnrichment({ dayId });
    } else if (action === "complete-stop-place" && this._canEdit()) {
      void this._preparePlaceEnrichment({ dayId, stopId });
    } else if (action === "calculate-day-route" && this._canEdit()) {
      void this._calculateDayRoute(dayId, target.dataset.force === "true");
    } else if (action === "calculate-trip-routes" && this._canEdit()) {
      void this._calculateTripRoutes(target.dataset.force === "true");
    } else if (action === "search-stop-images" && this._canEdit()) {
      const stop = this._findStop(dayId, stopId);
      const day = this._findDay(dayId);
      const city = cleanText(stop?.location?.city);
      const country = cleanText(stop?.location?.country_code);
      this._searchImages({
        targetType: "stop",
        dayId,
        stopId,
        query: [stop?.name, this._statusLabel(stop?.type), city, country].filter(Boolean).join(" ").slice(0, 180),
      });
    } else if (action === "search-day-images" && this._canEdit()) {
      const day = this._findDay(dayId);
      this._searchImages({
        targetType: "day",
        dayId,
        query: [day?.title, day?.end].filter(Boolean).join(" "),
      });
    } else if (action === "image-result-toggle") {
      this._toggleImageSearchResult(Number(target.dataset.imageIndex));
    } else if (action === "image-result-primary") {
      this._setImageSearchPrimary(Number(target.dataset.imageIndex));
    } else if (action === "save-image-gallery") {
      void this._saveImageSearchGallery();
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

  _assistantFailureResolved(messages = []) {
    const failedText = cleanText(this._assistantLastFailedText);
    if (!failedText) return true;
    let failedUserIndex = -1;
    for (let index = 0; index < messages.length; index += 1) {
      const message = messages[index] || {};
      if (message.role === "user" && cleanText(message.content) === failedText) {
        failedUserIndex = index;
      }
    }
    if (failedUserIndex >= 0) {
      return messages.slice(failedUserIndex + 1).some((message) => message?.role === "assistant");
    }
    const failedAt = Number(this._assistantLastFailedAt || 0);
    if (!failedAt) return false;
    return messages.some((message) => {
      if (message?.role !== "assistant") return false;
      const created = Date.parse(message.created_at || "");
      return Number.isFinite(created) && created >= failedAt;
    });
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
      this._assistantLastFailedAt = Date.now();
      this._render({ preserveScroll: true });
      return false;
    }

    this._assistantLastFailedText = "";
    this._assistantLastFailedRequestId = "";
    this._assistantLastFailedAt = 0;
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
    if (this._assistantPrepareInFlight) return null;
    const assistant = this._data?.assistant || {};
    if (!assistant.basket_count) {
      this._showToast("Es sind noch keine Änderungen vorgemerkt", "error");
      return null;
    }
    if (!this._data?.selected_is_active) {
      this._showToast("Bitte diese Reise zuerst als aktiv setzen", "error");
      return null;
    }
    if (this._busy) {
      this._showToast("Roadplanner verarbeitet noch eine andere Aktion. Bitte kurz erneut versuchen.", "error", 5000);
      return null;
    }

    this._assistantPrepareInFlight = true;
    this._render({ preserveScroll: true });
    const retry = () => this._prepareAssistantChanges();
    try {
      const result = await this._runAction("assistant_prepare", {
        trip_id: this._selectedTripId,
      }, "", {
        refresh: false,
        errorMode: "dialog",
        errorTitle: "Änderungsentwurf konnte nicht erstellt werden",
        retry,
      });
      if (!result) return null;
      if (result.assistant && this._data) {
        this._data = { ...this._data, assistant: result.assistant };
        this._signature = "";
      }
      if (!result.handoff) {
        this._showActionError(
          "Roadplanner hat den Entwurf verarbeitet, aber keine prüfbare Übergabe zurückgegeben.",
          {
            title: "Änderungsübersicht konnte nicht geöffnet werden",
            action: "assistant_prepare",
            retry,
          },
        );
        return null;
      }
      this._activeTab = "handoffs";
      await this._loadData({ silent: true, force: true });
      this._showToast("Änderungsentwurf erstellt", "success", 4000);
      return result;
    } finally {
      this._assistantPrepareInFlight = false;
      if (this._activeTab === "assistant") this._render({ preserveScroll: true });
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
          is_day_cover: Boolean(form.querySelector("input[name='is_day_cover']")?.checked),
          is_trip_cover: Boolean(form.querySelector("input[name='is_trip_cover']")?.checked),
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
    this.shadowRoot.innerHTML = `${PANEL_STYLES}${this._renderApp()}`;
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
    const primary = [
      ["overview", "mdi:map-outline", "Reise"],
      ["day-route", "mdi:white-balance-sunny", "Heute"],
      ["media", "mdi:image-multiple-outline", "Erinnerungen"],
      ["assistant", "mdi:message-processing-outline", "Reisebegleiter"],
    ];
    const tools = [
      ["decisions", "mdi:cards-playing-outline", "Entscheidungen", decisionCount, "info"],
      ["archive", "mdi:file-document-multiple-outline", "Dokumente & Kosten", todoTiming.urgent || todoTiming.upcoming, todoTiming.urgent ? "" : "warning"],
      ["total-route", "mdi:map-marker-path", "Gesamtroute", 0, ""],
      ["import", "mdi:file-import-outline", "Import", importReadyCount, "info"],
      ["trips", "mdi:map-multiple-outline", "Reisen", 0, ""],
      ["handoffs", "mdi:inbox-arrow-down", "Übergaben", pending, ""],
    ];
    const primaryIds = new Set(primary.map(([id]) => id));
    const activeTool = tools.find(([id]) => id === this._activeTab);
    const badgeFor = (id) => {
      if (id === "assistant" && drafts) return `<span class="count-badge">${drafts}</span>`;
      if (id === "media" && mediaReviewCount) return `<span class="count-badge warning">${mediaReviewCount}</span>`;
      return "";
    };
    return `<div class="navigation-shell">
      <nav class="tabs primary-tabs" aria-label="Roadplanner Reisephasen">
        ${primary.map(([id, icon, label]) => `
          <button type="button" class="tab ${this._activeTab === id ? "active" : ""}" data-tab="${id}">
            <ha-icon icon="${icon}"></ha-icon>
            <span>${label}</span>
            ${badgeFor(id)}
          </button>
        `).join("")}
      </nav>
      <details class="tool-tabs" ${activeTool && !primaryIds.has(this._activeTab) ? "open" : ""}>
        <summary><ha-icon icon="mdi:dots-horizontal-circle-outline"></ha-icon><span>${activeTool ? escapeHtml(activeTool[2]) : "Mehr"}</span></summary>
        <nav class="tool-tab-grid" aria-label="Roadplanner Werkzeuge">
          ${tools.map(([id, icon, label, count, badgeClass]) => `<button type="button" class="tool-tab ${this._activeTab === id ? "active" : ""}" data-tab="${id}"><ha-icon icon="${icon}"></ha-icon><span>${label}</span>${count ? `<span class="count-badge ${badgeClass || ""}">${count}</span>` : ""}</button>`).join("")}
        </nav>
      </details>
    </div>`;
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
    if (this._assistantLastFailedText && this._assistantFailureResolved(messages)) {
      this._assistantLastFailedText = "";
      this._assistantLastFailedRequestId = "";
      this._assistantLastFailedAt = 0;
    }
    const showRetryNotice = Boolean(this._assistantLastFailedText);
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

      ${showRetryNotice ? `<div class="notice warning assistant-retry-notice"><ha-icon icon="mdi:reload-alert"></ha-icon><div><strong>Die letzte Nachricht wurde nicht beantwortet</strong><span>Der Text bleibt erhalten. Roadplanner kann ihn mit aktuellem Reisekontext erneut senden.</span></div><button class="secondary-button compact-button" type="button" data-action="assistant-retry"><ha-icon icon="mdi:reload"></ha-icon> Erneut senden</button></div>` : ""}

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
          <button class="primary-button full-width" type="button" data-action="assistant-prepare" aria-busy="${this._assistantPrepareInFlight ? "true" : "false"}" ${basketEnabled && basket.length && this._data.selected_is_active && !this._assistantPrepareInFlight ? "" : "disabled"}><ha-icon icon="${this._assistantPrepareInFlight ? "mdi:loading mdi-spin" : "mdi:clipboard-text-search-outline"}"></ha-icon> ${this._assistantPrepareInFlight ? "Entwurf wird erstellt …" : "Änderungen prüfen"}</button>
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

  _experienceData() {
    return this._data?.experience || { decisions: [], media: [], destination_galleries: {}, presentation: {}, stats: {}, by_day: {}, by_stop: {}, onedrive: {} };
  }

  _experiencePresentation() {
    const value = this._experienceData().presentation;
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  _experienceCoverForStop(stopId) {
    const coverId = this._experiencePresentation().stop_covers?.[stopId];
    return coverId ? this._experienceMediaByIds([coverId])[0] || null : this._experienceMediaForStop(stopId)[0] || null;
  }

  _tripCoverImage() {
    const presentation = this._experiencePresentation();
    const travelCoverId = cleanText(presentation.trip_cover);
    if (travelCoverId) {
      const media = this._experienceMediaByIds([travelCoverId])[0];
      if (media) return { ...media, image_url: media.thumbnail_url, provider: "onedrive", attribution: "Eigenes Reisefoto", context: this._data?.summary?.trip?.title };
    }
    const planning = presentation.planning_trip_cover;
    if (planning?.image_url || planning?.thumbnail_url) {
      return { ...planning, image_url: planning.thumbnail_url || planning.image_url, context: this._data?.summary?.trip?.title };
    }
    return this._mediaFrom(this._data?.summary?.trip);
  }

  _dayCoverImage(day) {
    const presentation = this._experiencePresentation();
    const travelCoverId = presentation.day_covers?.[day?.id];
    if (travelCoverId) {
      const media = this._experienceMediaByIds([travelCoverId])[0];
      if (media) return { ...media, image_url: media.thumbnail_url, provider: "onedrive", attribution: "Eigenes Reisefoto", context: day?.title };
    }
    const planning = presentation.planning_day_covers?.[day?.id];
    if (planning?.image_url || planning?.thumbnail_url) return { ...planning, image_url: planning.thumbnail_url || planning.image_url, context: day?.title };
    for (const stop of this._dayRoadbookStops(day)) {
      const own = this._experienceCoverForStop(stop.id);
      if (own) return { ...own, image_url: own.thumbnail_url, provider: "onedrive", attribution: "Eigenes Reisefoto", context: stop.name };
      const gallery = this._destinationGalleryForStop(stop.id);
      const primary = this._destinationGalleryPrimary(gallery);
      if (primary) return { ...primary, context: stop.name };
    }
    return this._mediaFrom(day);
  }

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
  }

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
  }

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
  }

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
  }

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
  }

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
  }

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
  }

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
    else if (this._dialog.type === "stop-order") body = this._renderStopOrderDialog(this._dialog);
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
    else if (this._dialog.type === "destination-gallery") body = this._renderDestinationGallery(this._dialog);
    else if (this._dialog.type === "travel-integrity") body = this._renderTravelIntegrity(this._dialog);
    else if (this._dialog.type === "place-enrichment") body = this._renderPlaceEnrichment(this._dialog);
    return `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-label="Roadplanner Dialog">${body}</section></div>`;
  }

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
  }

  _renderActionErrorDialog(dialog) {
    const requestLine = dialog.requestId
      ? `<div class="action-error-request"><span>Anfrage</span><code>${escapeHtml(dialog.requestId)}</code></div>`
      : "";
    const technicalDetails = dialog.technicalMessage && dialog.technicalMessage !== dialog.message
      ? `<details class="action-error-details"><summary>Technische Details</summary><code>${escapeHtml(dialog.technicalMessage)}</code></details>`
      : "";
    return `${this._renderModalHeader(dialog.title || "Roadplanner-Aktion fehlgeschlagen", "Die Meldung bleibt geöffnet, bis du sie schließt.")}
      <div class="action-error-body">
        <div class="action-error-icon"><ha-icon icon="mdi:alert-circle-outline"></ha-icon></div>
        <div><p>${escapeHtml(dialog.message || "Unbekannter Roadplanner-Fehler")}</p>${requestLine}${technicalDetails}</div>
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
}

Object.assign(RoadplannerPanel.prototype, universalImportMixin);
Object.assign(RoadplannerPanel.prototype, placeEnrichmentMixin);
Object.assign(RoadplannerPanel.prototype, archiveMixin);
Object.assign(RoadplannerPanel.prototype, mediaMixin);
Object.assign(RoadplannerPanel.prototype, decisionsIntegrityMixin);

if (!customElements.get("roadplanner-panel")) {
  customElements.define("roadplanner-panel", RoadplannerPanel);
}
