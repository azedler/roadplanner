import { PANEL_STYLES } from "./lib/styles.js";

// The version this very module was loaded with. Home Assistant registers
// the panel as ".../roadplanner-panel.js?v=<integration version>", so a
// mismatch against the version the backend reports means the browser is
// still running pre-update code - the recurring "keine Veränderung nach
// dem Update" confusion (live report).
const LOADED_MODULE_VERSION = (() => {
  try {
    return new URL(import.meta.url).searchParams.get("v") || "";
  } catch (err) {
    return "";
  }
})();
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
import { assistantMixin } from "./features/assistant.js";
import { routeMapMixin } from "./features/route-map.js";
import { tripDayStopMixin } from "./features/trip-day-stop.js";
import { crewMixin } from "./features/crew.js";
import { remotionSpikeMixin } from "./features/remotion-spike.js";
import { pitchesMixin } from "./features/pitches.js";

// Mirror of panel.py's _PROVIDER_CALL_ACTIONS: these run shielded
// server-side and finish even when the client connection dies.
const SERVER_CONTINUING_ACTIONS = new Set([
  "assistant_chat",
  "assistant_prepare",
  "assistant_test",
  "assistant_briefing",
  "export_trip_video",
  "generate_trip_summaries",
  "remotion_diagnose",
  "remotion_test_render",
  "park4night_lookup",
  "place_link_lookup",
]);

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
    this._actionChain = Promise.resolve();
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
    // Dragging the crew crop box: pointer events so the face region can be
    // MOVED, not only resized (live request).
    this.shadowRoot.addEventListener("pointerdown", (event) => {
      const frame = event.target?.closest?.("[data-crew-crop-frame]");
      if (!frame || !this._canEdit()) return;
      this._crewCropFrame = frame;
      frame.setPointerCapture?.(event.pointerId);
      event.preventDefault();
      this._applyCrewCropTap(frame.closest("form"), frame, event);
    });
    this.shadowRoot.addEventListener("pointermove", (event) => {
      if (!this._crewCropFrame) return;
      event.preventDefault();
      this._applyCrewCropTap(this._crewCropFrame.closest("form"), this._crewCropFrame, event);
    });
    for (const endEvent of ["pointerup", "pointercancel"]) {
      this.shadowRoot.addEventListener(endEvent, () => { this._crewCropFrame = null; });
    }
    this.shadowRoot.addEventListener("change", (event) => this._handleChange(event));
    this.shadowRoot.addEventListener("submit", (event) => this._handleSubmit(event));
    this.shadowRoot.addEventListener("error", (event) => {
      const image = event.target?.closest?.("img[data-destination-image]");
      if (image) image.closest(".destination-image")?.classList.add("image-error");
    }, true);
    this.shadowRoot.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this._dialog) this._closeDialog();
      const textarea = event.target?.closest?.("textarea[name='message']");
      // Enter inserts a NEWLINE (live request: plain Enter kept sending
      // half-typed messages); sending is the button or Ctrl/Cmd+Enter.
      if (textarea && event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) {
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
        this._maybeLoadExportStatus();
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

  _runAction(action, data = {}, successMessage = "Änderung gespeichert", options = {}) {
    // Actions are SERIALIZED, never silently dropped: the old
    // `if (this._busy) return null` discarded a save/move/delete without
    // any feedback whenever another action was still running (e.g. a
    // multi-second route calculation) - the dialog closed and every edit
    // was lost, indistinguishable from success. Long-running opt-outs
    // (blockUi: false, e.g. video export) bypass the queue so they never
    // stall it.
    const opts = options || {};
    if (opts.blockUi === false) {
      return this._runActionNow(action, data, successMessage, opts);
    }
    const run = () => this._runActionNow(action, data, successMessage, opts);
    const chained = this._actionChain.then(run, run);
    this._actionChain = chained.catch(() => {});
    return chained;
  }

  async _runActionNow(action, data = {}, successMessage = "Änderung gespeichert", options = {}) {
    const {
      refresh = true,
      errorMode = "toast",
      errorTitle = "Roadplanner-Aktion fehlgeschlagen",
      retry = null,
      blockUi = true,
    } = options || {};
    const tripScopedActions = new Set([
      "update_trip",
      "add_day",
      "update_day",
      "remove_day",
      "add_stop",
      "update_stop",
      "remove_stop",
      "pitch_option_save",
      "pitch_option_delete",
      "pitch_option_set_status",
      "pitch_set_strategy",
      "pitch_option_activate",
      "pitch_update_preferences",
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
    if (blockUi) this._setBusy(true);
    try {
      const result = await this._send({ type: WS_ACTION, action, data: payload });
      if (successMessage) this._showToast(successMessage, "success");
      if (refresh) await this._loadData({ silent: true, force: true });
      return result;
    } catch (error) {
      const message = this._errorMessage(error);
      if (this._isConnectionLostError(error) && SERVER_CONTINUING_ACTIONS.has(action)) {
        // Backgrounding the app kills the WebSocket, but these actions are
        // shielded server-side and run to completion anyway (the draft/
        // reply/handoff arrives regardless). A scary error dialog here made
        // users retry and produced duplicates - inform, then re-check.
        this._showToast(
          "Verbindung kurz unterbrochen - Roadplanner arbeitet auf dem Server weiter. Das Ergebnis erscheint gleich automatisch (z. B. unter „Übergaben“). Bitte nicht erneut starten.",
          "success",
          9000,
        );
        window.setTimeout(() => {
          if (this._dialog) this._refreshQueued = true;
          else void this._loadData({ silent: true, force: true });
        }, 5000);
        return null;
      }
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
        // With a dialog open, reloading now would re-render the shadow
        // root and rebuild the form from stale data, wiping typed input
        // right when the user needs it to retry - queue instead; the
        // refresh flushes when the dialog closes.
        if (this._dialog) this._refreshQueued = true;
        else await this._loadData({ silent: true, force: true });
      }
      return null;
    } finally {
      if (blockUi) this._setBusy(false);
      // Never flush a queued refresh while a dialog is open: _loadData
      // re-renders the whole shadow root, erasing typed form input and
      // detaching the form element mid-interaction (the Park4Night
      // prefill then wrote into detached DOM while the toast claimed
      // success). _closeDialog flushes the queue instead.
      if (this._refreshQueued && !this._dialog) {
        this._refreshQueued = false;
        await this._loadData({ silent: true, force: true });
      }
    }
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
    await this._copyToClipboard(text, "Fehlerdetails kopiert");
  }

  async _copyToClipboard(text, successMessage) {
    // navigator.clipboard is unavailable on non-secure origins and in some
    // WebViews - the textarea fallback is what makes copying work on the
    // phone at all.
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
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
    }
    this._showToast(successMessage, "success", 3000);
  }

  _isConnectionLostError(error) {
    if (Number(error?.code) === 3) return true;
    const message = String(error?.message || error?.error?.message || error || "").toLowerCase();
    return message.includes("connection lost") || message.includes("connection is closed");
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
    const cropSize = event.target.closest('input[type="range"][data-action="crew-crop-size"]');
    if (cropSize) {
      this._resizeCrewCrop(cropSize.closest("form"));
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
    } else if (select.dataset.action === "select-video-style") {
      this._videoStyle = select.value;
    } else if (select.dataset.action === "pitch-select-day") {
      this._pitchSelectedDayId = select.value;
      this._render({ preserveScroll: true });
    } else if (select.dataset.action === "pitch-strategy" && this._canEdit()) {
      void this._runPitchAction("pitch_set_strategy", cleanText(select.dataset.dayId), {
        strategy: select.value,
      }, "Strategie gespeichert");
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
      this._maybeLoadExportStatus();
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
    } else if (action === "integrity-repair-days") {
      void this._planDayCalendarRepair();
    } else if (action === "day-calendar-repair-submit") {
      void this._proposeDayCalendarRepair();
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
    } else if (action === "place-manual-check-map") {
      // Open the coordinates AS CURRENTLY TYPED so the user can verify the
      // point on Google Maps BEFORE confirming it - nothing is saved here.
      const form = target.closest("form[data-place-manual-form]");
      if (!form) return;
      const values = Object.fromEntries(new FormData(form).entries());
      const latitudeText = cleanText(values.latitude).replace(",", ".");
      const longitudeText = cleanText(values.longitude).replace(",", ".");
      const latitude = Number(latitudeText);
      const longitude = Number(longitudeText);
      if (!latitudeText || !longitudeText
        || !Number.isFinite(latitude) || !Number.isFinite(longitude)
        || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
        this._showToast("Bitte zuerst Breiten- und Längengrad eintragen, dann prüfen.", "error", 5000);
        return;
      }
      window.open(
        `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${latitude},${longitude}`)}`,
        "_blank",
        "noopener,noreferrer",
      );
    } else if (action === "stop-p4n-lookup" && this._canEdit()) {
      void this._runStopFormP4nLookup(target.closest("form[data-form='stop']"));
    } else if (action === "place-p4n-apply") {
      if (this._dialog?.type !== "place-enrichment") return;
      const selectedStopId = cleanText(target.dataset.stopId);
      if (!selectedStopId) return;
      // Prefill the existing manual-confirmation path with the page facts -
      // AI-read coordinates are confirmed by the user like hand-typed ones
      // and stored as manually confirmed, never as provider-verified.
      this._dialog.manualEntries = {
        ...(this._dialog.manualEntries || {}),
        [selectedStopId]: {
          ...(this._dialog.manualEntries?.[selectedStopId] || {}),
          name: cleanText(target.dataset.name) || undefined,
          city: cleanText(target.dataset.city) || undefined,
          country_code: cleanText(target.dataset.countryCode) || undefined,
          latitude: cleanText(target.dataset.latitude),
          longitude: cleanText(target.dataset.longitude),
        },
      };
      this._dialog.selections = {
        ...(this._dialog.selections || {}),
        [selectedStopId]: "__manual__",
      };
      this._showToast("Park4Night-Position in den manuellen Kartenpunkt übernommen - bitte prüfen und bestätigen.", "success", 5000);
      this._render({ preserveScroll: true });
    } else if (action === "pitch-routes") {
      void this._loadPitchRoutes(cleanText(target.dataset.dayId));
    } else if (action === "place-link-lookup") {
      void this._runPlaceLinkLookup(cleanText(target.dataset.stopId));
    } else if (action === "assistant-scroll-basket") {
      this.shadowRoot?.querySelector(".assistant-basket")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else if (action === "pitch-option-link-lookup") {
      // Never a dead button: without edit rights the press explains itself
      // instead of doing nothing at all (live report: "Es erscheint
      // weiterhin keine Fehler" - and nothing else either).
      if (this._canEdit()) {
        void this._runPitchOptionLinkLookup(target.closest("form[data-form='pitch-option']"));
      } else {
        this._showToast(
          this._data?.selected_is_active
            ? "Zum Lesen von Links fehlt dir die Bearbeitungsberechtigung."
            : "Diese Reise ist nicht die aktive Planung - Links können hier nicht übernommen werden.",
          "error",
          6000,
        );
      }
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
    } else if (action === "media-filter") {
      this._mediaFilter = cleanText(target.dataset.filter) || "all";
      this._mediaPage = 0;
      this._render({ preserveScroll: true });
    } else if (action === "media-page") {
      const delta = Number(target.dataset.delta || 0);
      this._mediaPage = Math.max(0, Number(this._mediaPage || 0) + (Number.isFinite(delta) ? delta : 0));
      this._render({ preserveScroll: true });
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
    } else if (action === "reload-app") {
      // A pull-to-refresh gesture doesn't reach the native app shell here -
      // the panel's own scrollable content swallows it - so after an update
      // there was no in-app way to force a real reload of the page/module
      // code (as opposed to just the data, which "Neu laden" already does).
      // A full reload re-fetches roadplanner-panel.js with a fresh "?v="
      // version query and, thanks to the always-revalidating static view,
      // every submodule it imports too.
      const doReload = () => {
        // A plain reload can still be answered from the frontend's service
        // worker cache; a one-shot query parameter forces a fresh document
        // and with it a fresh panel module URL. Falls back to a plain
        // reload wherever that is not possible.
        try {
          const target = new URL(window.location.href);
          target.searchParams.set("rp", String(Date.now()));
          window.location.replace(target.toString());
        } catch (err) {
          window.location.reload();
        }
      };
      if (this._dialog) {
        this._confirm(
          "App aktualisieren?",
          "Eine offene Eingabe (z. B. ein Formular) geht dabei verloren. Bereits gespeicherte Roadbook-Daten sind davon nicht betroffen.",
          "Jetzt neu laden",
          doReload,
          true,
        );
      } else {
        doReload();
      }
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
    } else if (action === "add-crew-person" && this._canEdit()) {
      this._dialog = { type: "crew-person-form", person: null };
      this._render({ preserveScroll: true });
    } else if (action === "edit-crew-person" && this._canEdit()) {
      this._dialog = { type: "crew-person-form", person: this._crewPersonById(target.dataset.personId) };
      this._render({ preserveScroll: true });
    } else if (action === "crew-pick-reference" && this._canEdit()) {
      // Pure DOM update - a re-render would reset the form's typed values.
      this._setCrewReferencePhoto(
        target.closest("form"),
        target.dataset.mediaId || "",
        target.dataset.thumbUrl || "",
        target.dataset.largeUrl || "",
      );
      target.classList.add("selected");
    } else if (action === "crew-clear-reference" && this._canEdit()) {
      this._setCrewReferencePhoto(target.closest("form"), "", "", "");
    } else if (action === "crew-photo-prev" || action === "crew-photo-next") {
      const pickerForm = target.closest("form");
      const grid = pickerForm?.querySelector("[data-crew-photo-grid]");
      const current = Number(grid?.dataset.page || 0);
      this._showCrewPhotoPage(pickerForm, current + (action === "crew-photo-next" ? 1 : -1));
    } else if (action === "crew-crop-reset" && this._canEdit()) {
      this._setCrewReferenceCrop(target.closest("form"), null);
    } else if (action === "retire-crew-person" && this._canEdit()) {
      const person = this._crewPersonById(target.dataset.personId);
      this._confirm(
        "Person stilllegen?",
        `${person?.name || "Diese Person"} wird bei künftigen Reisen nicht mehr zur Auswahl angeboten. Bereits ausgewählte Reisen bleiben unverändert.`,
        "Stilllegen",
        () => this._runAction("crew_person_retire", { person_id: target.dataset.personId }, "Person stillgelegt"),
      );
    } else if (action === "reactivate-crew-person" && this._canEdit()) {
      void this._runAction("crew_person_reactivate", { person_id: target.dataset.personId }, "Person reaktiviert");
    } else if (action === "add-crew-vehicle" && this._canEdit()) {
      this._dialog = { type: "crew-vehicle-form", vehicle: null };
      this._render({ preserveScroll: true });
    } else if (action === "edit-crew-vehicle" && this._canEdit()) {
      this._dialog = { type: "crew-vehicle-form", vehicle: this._crewVehicleById(target.dataset.vehicleId) };
      this._render({ preserveScroll: true });
    } else if (action === "retire-crew-vehicle" && this._canEdit()) {
      const vehicle = this._crewVehicleById(target.dataset.vehicleId);
      this._confirm(
        "Fahrzeug stilllegen?",
        `${vehicle?.name || "Dieses Fahrzeug"} wird bei künftigen Reisen nicht mehr zur Auswahl angeboten. Bereits ausgewählte Reisen bleiben unverändert.`,
        "Stilllegen",
        () => this._runAction("crew_vehicle_retire", { vehicle_id: target.dataset.vehicleId }, "Fahrzeug stillgelegt"),
      );
    } else if (action === "reactivate-crew-vehicle" && this._canEdit()) {
      void this._runAction("crew_vehicle_reactivate", { vehicle_id: target.dataset.vehicleId }, "Fahrzeug reaktiviert");
    } else if (action === "pitch-open-tab") {
      if (dayId) this._pitchSelectedDayId = dayId;
      this._activeTab = "pitches";
      this._render();
    } else if (action === "pitch-add-option" && this._canEdit()) {
      this._dialog = { type: "pitch-option", dayId, option: null };
      this._render({ preserveScroll: true });
    } else if (action === "pitch-edit-option" && this._canEdit()) {
      const pitchDay = this._findDay(dayId);
      const pitchOption = this._pitchPlan(pitchDay).options.find((item) => item.id === target.dataset.optionId);
      if (!pitchOption) return;
      this._dialog = { type: "pitch-option", dayId, option: pitchOption };
      this._render({ preserveScroll: true });
    } else if (action === "pitch-activate" && this._canEdit()) {
      this._activatePitchOption(dayId, target.dataset.optionId);
    } else if (action === "pitch-reject" && this._canEdit()) {
      void this._runPitchAction("pitch_option_set_status", dayId, {
        option_id: target.dataset.optionId,
        status: "rejected",
      }, "Option verworfen");
    } else if (action === "pitch-restore" && this._canEdit()) {
      void this._runPitchAction("pitch_option_set_status", dayId, {
        option_id: target.dataset.optionId,
        status: "backup",
      }, "Option wiederhergestellt");
    } else if (action === "pitch-option-images" && this._canEdit()) {
      this._searchPitchOptionImages(dayId, target.dataset.optionId);
    } else if (action === "pitch-delete" && this._canEdit()) {
      const deletedOptionId = target.dataset.optionId;
      this._confirm(
        "Stellplatz-Option löschen?",
        "Die Option wird endgültig aus dem Tag entfernt. Zum bloßen Aussortieren reicht Verwerfen.",
        "Löschen",
        async () => {
          const result = await this._runPitchAction("pitch_option_delete", dayId, { option_id: deletedOptionId }, "Option gelöscht");
          if (result) this._deletePitchOptionGallery(deletedOptionId);
        },
        true,
      );
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
      const stopCount = Number(day?.stop_count) || 0;
      this._confirm(
        "Reisetag löschen?",
        stopCount
          // The stops go WITH the day - they belong to it, they are not
          // re-homed anywhere. That has to be said before the click, not
          // discovered afterwards.
          ? `${day?.title || "Dieser Reisetag"} wird entfernt, zusammen mit ${stopCount} ${stopCount === 1 ? "Stopp" : "Stopps"}. Das lässt sich nicht rückgängig machen.`
          : `${day?.title || "Dieser Reisetag"} wird entfernt. Der Tag enthält keine Stopps.`,
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
    } else if (action === "export-trip-pdf") {
      void this._exportTripPdf();
    } else if (action === "generate-trip-summaries" && this._canEdit()) {
      this._generateTripSummaries();
    } else if (action === "export-trip-video") {
      void this._exportTripVideo();
    } else if (action === "open-last-trip-video") {
      void this._openLastTripVideo();
    } else if (action === "open-last-trip-pdf") {
      void this._openLastTripPdf();
    } else if (action === "remotion-diagnose") {
      this._remotionDiagnose();
    } else if (action === "remotion-test-render" && this._canEdit()) {
      this._remotionTestRender();
    } else if (action === "remotion-cancel" && this._canEdit()) {
      this._remotionCancel();
    } else if (action === "remotion-copy-report") {
      this._copyToClipboard(this._remotionReportText(), "Diagnosebericht kopiert");
    } else if (action === "run-system-check") {
      void this._runSystemCheck();
    } else if (action === "copy-system-check") {
      void this._copySystemCheck();
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
    } else if (action === "rebase-handoff" && this._canApprove()) {
      this._confirm(
        "Übergabe neu aufsetzen?",
        "Die Übergabe wird gegen den aktuellen Stand der Reise neu geprüft und, falls sie weiterhin passt, mit der aktuellen Revision gespeichert. Sie muss danach noch separat übernommen werden. Passt sie nicht mehr (z.B. weil ein referenzierter Tag oder Stopp inzwischen gelöscht wurde), bleibt die Übergabe unverändert und du bekommst eine Fehlermeldung.",
        "Neu aufsetzen",
        async () => {
          await this._runAction("rebase_handoff", {
            handoff_id: handoffId,
            expected_trip_id: this._selectedTripId,
          }, "Übergabe neu aufgesetzt");
        },
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

    if (formType === "pitch-option") {
      await this._submitPitchOptionForm(form, values);
      return;
    }

    if (formType === "pitch-preferences") {
      await this._submitPitchPreferencesForm(form, values);
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

    if (formType === "crew-person") {
      const mode = form.dataset.mode;
      const value = {
        name: cleanText(values.name),
        kind: cleanText(values.kind) || "person",
        note: String(values.note || ""),
        reference_media_id: cleanText(values.reference_media_id),
        reference_crop: (() => {
          try {
            return values.reference_crop ? JSON.parse(String(values.reference_crop)) : null;
          } catch (err) {
            return null;
          }
        })(),
      };
      const result = mode === "add"
        ? await this._runAction("crew_person_add", { value }, "Person hinzugefügt")
        : await this._runAction("crew_person_update", { person_id: form.dataset.personId, patch: value }, "Person gespeichert");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    if (formType === "crew-vehicle") {
      const mode = form.dataset.mode;
      const value = {
        name: cleanText(values.name),
        description: String(values.description || ""),
        // Same picker as a person's portrait (live request: "Vielleicht
        // auch vom Fahrzeug ein Bild?").
        reference_media_id: cleanText(values.reference_media_id),
        reference_crop: (() => {
          try {
            return values.reference_crop ? JSON.parse(String(values.reference_crop)) : null;
          } catch (err) {
            return null;
          }
        })(),
      };
      const result = mode === "add"
        ? await this._runAction("crew_vehicle_add", { value }, "Fahrzeug hinzugefügt")
        : await this._runAction("crew_vehicle_update", { vehicle_id: form.dataset.vehicleId, patch: value }, "Fahrzeug gespeichert");
      if (result) this._closeDialog({ flushRefresh: false });
      return;
    }

    const expectedRevision = Number.parseInt(form.dataset.revision || "", 10);
    if (!Number.isInteger(expectedRevision)) {
      this._showToast("Die Bearbeitungsrevision fehlt. Bitte Dialog neu öffnen.", "error");
      return;
    }

    if (formType === "trip") {
      const selectedPersonIds = Array.from(form.querySelectorAll('input[name="traveler_ids"]:checked')).map((input) => input.value);
      const travelers = selectedPersonIds
        .map((personId) => this._crewPersonById(personId))
        .filter(Boolean)
        .map((person) => ({ person_id: person.id, name: person.name, kind: person.kind, note: person.note }));
      const vehicleId = cleanText(values.vehicle_id);
      const vehicle = vehicleId ? this._crewVehicleById(vehicleId) : null;
      this._closeDialog({ flushRefresh: false });
      await this._runAction("update_trip", {
        expected_revision: expectedRevision,
        patch: {
          title: cleanText(values.title),
          status: cleanText(values.status) || "planned",
          start_date: cleanText(values.start_date) || null,
          end_date: cleanText(values.end_date) || null,
          notes: String(values.notes || ""),
          travelers,
          vehicle: vehicle ? { vehicle_id: vehicle.id, name: vehicle.name, description: vehicle.description } : {},
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
      this._expandedDays.add(common.day_id);
      // Close only AFTER the server accepted the save: closing first threw
      // away every typed field on any rejection (lone latitude, stale
      // revision, ...) with nothing but a toast - the same result-gated
      // pattern the media/archive/crew forms have always used.
      let result;
      if (mode === "add") {
        result = await this._runAction("add_stop", common, "Stopp hinzugefügt");
      } else {
        result = await this._runAction("update_stop", {
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
      if (result) this._closeDialog();
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
            <button class="icon-button" type="button" data-action="refresh" aria-label="Daten neu laden" title="Daten neu laden">
              <ha-icon icon="mdi:refresh"></ha-icon>
            </button>
            <button class="icon-button" type="button" data-action="reload-app" aria-label="App aktualisieren" title="App aktualisieren (nach einem Update, falls sich nichts ändert)">
              <ha-icon icon="mdi:cellphone-arrow-down"></ha-icon>
            </button>
          </div>
        </header>
        ${this._renderTabs()}
        <main class="content">
          ${this._renderStaleModuleNotice()}
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
      ["pitches", "mdi:caravan", "Stellplätze", 0, ""],
      ["total-route", "mdi:map-marker-path", "Gesamtroute", 0, ""],
      ["import", "mdi:file-import-outline", "Import", importReadyCount, "info"],
      // "mdi:map-multiple-outline" does not exist in Material Design Icons,
      // so this entry rendered with no icon at all while every other one had
      // theirs (live report: "Für Reisen habe ich noch immer kein Symbol").
      // A suitcase also reads better here than a third map icon next to
      // "Reise" and "Gesamtroute".
      ["trips", "mdi:bag-suitcase-outline", "Reisen", 0, ""],
      ["crew", "mdi:account-group-outline", "Crew & Fahrzeuge", 0, ""],
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
      <details class="tool-tabs">
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
    if (this._activeTab === "pitches") return this._renderPitches();
    if (this._activeTab === "media") return this._renderMedia();
    if (this._activeTab === "archive") return this._renderArchive();
    if (this._activeTab === "day-route") return this._renderDayRoute();
    if (this._activeTab === "total-route") return this._renderTotalRoute();
    if (this._activeTab === "trips") return this._renderTrips();
    if (this._activeTab === "crew") return this._renderCrewManage();
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

  _versionSummary() {
    // Backend AND loaded interface, side by side. Without this pair, "the
    // new function is missing" and "the panel is still the old one" look
    // identical from a phone - which cost several rounds of guesswork.
    const backendVersion = cleanText(this._data?.integration_version);
    if (!LOADED_MODULE_VERSION || LOADED_MODULE_VERSION === backendVersion) {
      return backendVersion;
    }
    return `${backendVersion} · Oberfläche ${LOADED_MODULE_VERSION}`;
  }

  _renderStaleModuleNotice() {
    const backendVersion = cleanText(this._data?.integration_version);
    if (!backendVersion || !LOADED_MODULE_VERSION || backendVersion === LOADED_MODULE_VERSION) return "";
    return `<div class="notice warning view-notice">
      <ha-icon icon="mdi:cellphone-arrow-down"></ha-icon>
      <div><strong>Ältere Oberfläche geladen</strong><span>Roadplanner läuft auf ${escapeHtml(backendVersion)}, geladen ist noch ${escapeHtml(LOADED_MODULE_VERSION)}. Neue Funktionen erscheinen erst nach dem Aktualisieren.</span></div>
      <button class="primary-button compact-button" type="button" data-action="reload-app">App aktualisieren</button>
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

  _statCard(icon, value, label) {
    return `<article class="stat-card">
      <ha-icon icon="${icon}"></ha-icon>
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(label)}</span>
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
    return `<article class="handoff-card"><div class="handoff-heading"><div><span class="eyebrow">${escapeHtml(handoff.source || "extern")}</span><h3>${escapeHtml(handoff.title || handoff.id)}</h3><p>${escapeHtml(handoff.preview || "")}</p></div><span class="status-badge ${this._statusClass(handoff.status)}">${escapeHtml(this._statusLabel(handoff.status))}</span></div><div class="handoff-meta"><span><ha-icon icon="mdi:clock-outline"></ha-icon>${escapeHtml(this._formatTimestamp(handoff.received_at))}</span><span><ha-icon icon="mdi:format-list-bulleted"></ha-icon>${handoff.operation_count} Operationen</span><span><ha-icon icon="mdi:file-document-refresh"></ha-icon>Basis ${handoff.base_revision}</span><span><ha-icon icon="mdi:help-circle-outline"></ha-icon>${handoff.open_question_count} offene Fragen</span></div>${operations ? `<div class="operation-summary">${escapeHtml(operations)}</div>` : ""}${handoff.last_error ? `<div class="notice danger">${escapeHtml(handoff.last_error)}</div>` : ""}${conflict ? `<div class="notice warning">Die ausgewählte Reise steht auf Revision ${this._currentRevision()}. Die Vorschau zeigt den Konflikt.</div>` : ""}<div class="button-row"><button class="secondary-button" type="button" data-action="preview-handoff" data-handoff-id="${escapeHtml(handoff.id)}"><ha-icon icon="mdi:eye-outline"></ha-icon> Vorschau</button>${this._canApprove() ? `<button class="primary-button" type="button" data-action="apply-handoff" data-handoff-id="${escapeHtml(handoff.id)}" ${canApply ? "" : "disabled"}><ha-icon icon="mdi:check-bold"></ha-icon> Übernehmen</button>${conflict ? `<button class="secondary-button" type="button" data-action="rebase-handoff" data-handoff-id="${escapeHtml(handoff.id)}"><ha-icon icon="mdi:refresh"></ha-icon> Neu aufsetzen</button>` : ""}<button class="text-button danger-text" type="button" data-action="archive-handoff" data-handoff-id="${escapeHtml(handoff.id)}">Ablehnen</button>` : ""}</div></article>`;
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
    else if (this._dialog.type === "pitch-option") body = this._renderPitchOptionForm(this._dialog);
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
    else if (this._dialog.type === "day-calendar-repair") body = this._renderDayCalendarRepair(this._dialog);
    else if (this._dialog.type === "place-enrichment") body = this._renderPlaceEnrichment(this._dialog);
    else if (this._dialog.type === "crew-person-form") body = this._renderCrewPersonForm(this._dialog);
    else if (this._dialog.type === "crew-vehicle-form") body = this._renderCrewVehicleForm(this._dialog);
    return `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-label="Roadplanner Dialog">${body}</section></div>`;
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

  _renderConfirmDialog(dialog) {
    return `${this._renderModalHeader(dialog.title)}<div class="confirm-body"><ha-icon icon="${dialog.destructive ? "mdi:alert-outline" : "mdi:help-circle-outline"}"></ha-icon><p>${escapeHtml(dialog.message)}</p></div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Abbrechen</button><button class="${dialog.destructive ? "danger-button" : "primary-button"}" type="button" data-action="confirm-dialog">${escapeHtml(dialog.confirmLabel)}</button></div>`;
  }

  _renderHandoffPreview(dialog) {
    const preview = dialog.preview || {};
    const operations = preview.operation_results || [];
    return `${this._renderModalHeader("Übergabe-Vorschau", dialog.handoff?.title || dialog.handoff?.id || "ChangeSet")}<div class="preview-body"><div class="preview-status ${preview.applicable ? "ready" : "blocked"}"><ha-icon icon="${preview.applicable ? "mdi:check-decagram-outline" : "mdi:alert-circle-outline"}"></ha-icon><div><strong>${preview.applicable ? "Bereit zur Übernahme" : "Nicht anwendbar"}</strong><span>${escapeHtml(preview.reason || `Zielrevision ${preview.target_revision ?? "—"}`)}</span></div></div><div class="preview-grid"><div><span>Basisrevision</span><strong>${escapeHtml(preview.base_revision ?? dialog.handoff?.base_revision ?? "—")}</strong></div><div><span>Aktuelle Revision</span><strong>${escapeHtml(preview.current_revision ?? this._currentRevision())}</strong></div><div><span>Operationen</span><strong>${escapeHtml(preview.operation_count ?? dialog.handoff?.operation_count ?? 0)}</strong></div><div><span>Löschungen</span><strong>${preview.destructive || dialog.handoff?.destructive ? "Ja" : "Nein"}</strong></div></div>${operations.length ? `<ol class="operation-list">${operations.map((operation) => `<li><strong>${escapeHtml(operation.op || operation.operation || "Änderung")}</strong><pre>${escapeHtml(JSON.stringify(operation, null, 2))}</pre></li>`).join("")}</ol>` : ""}</div><div class="modal-actions"><button class="secondary-button" type="button" data-action="close-dialog">Schließen</button>${preview.status === "revision_conflict" && this._canApprove() ? `<button class="secondary-button" type="button" data-action="rebase-handoff" data-handoff-id="${escapeHtml(dialog.handoff.id)}"><ha-icon icon="mdi:refresh"></ha-icon> Neu aufsetzen</button>` : ""}${preview.applicable && this._canApprove() && this._data?.selected_is_active ? `<button class="primary-button" type="button" data-action="apply-handoff" data-handoff-id="${escapeHtml(dialog.handoff.id)}">Übernehmen</button>` : ""}</div>`;
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

}

Object.assign(RoadplannerPanel.prototype, universalImportMixin);
Object.assign(RoadplannerPanel.prototype, placeEnrichmentMixin);
Object.assign(RoadplannerPanel.prototype, archiveMixin);
Object.assign(RoadplannerPanel.prototype, mediaMixin);
Object.assign(RoadplannerPanel.prototype, decisionsIntegrityMixin);
Object.assign(RoadplannerPanel.prototype, assistantMixin);
Object.assign(RoadplannerPanel.prototype, routeMapMixin);
Object.assign(RoadplannerPanel.prototype, tripDayStopMixin);
Object.assign(RoadplannerPanel.prototype, crewMixin);
Object.assign(RoadplannerPanel.prototype, remotionSpikeMixin);
Object.assign(RoadplannerPanel.prototype, pitchesMixin);

if (!customElements.get("roadplanner-panel")) {
  customElements.define("roadplanner-panel", RoadplannerPanel);
}
