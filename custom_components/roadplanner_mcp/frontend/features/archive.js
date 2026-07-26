import {
  archiveDocumentTypeLabels,
  archiveExpenseCategoryLabels,
  archiveStatusLabels,
} from "../lib/constants.js";
import { escapeHtml, cleanText } from "../lib/core-helpers.js";

export const archiveMixin = {
  _archiveData() {
    return this._data?.travel_archive || { documents: [], expenses: [], todos: [], stats: {}, by_day: {}, by_stop: {} };
  },

  _archiveDocument(documentId) {
    return (this._archiveData().documents || []).find((item) => item.id === documentId) || null;
  },

  _archiveExpense(expenseId) {
    return (this._archiveData().expenses || []).find((item) => item.id === expenseId) || null;
  },

  _archiveTodo(todoId) {
    return (this._archiveData().todos || []).find((item) => item.id === todoId) || null;
  },

  _parseTodoDue(value) {
    const text = cleanText(value);
    if (!text) return null;
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
    const candidate = dateOnly ? new Date(`${text}T23:59:59`) : new Date(text);
    return Number.isNaN(candidate.getTime()) ? null : candidate;
  },

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
  },

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
  },

  _todoDueLabel(todo) {
    const state = this._todoDueState(todo);
    if (state === "overdue") return "Überfällig";
    if (state === "today") return "Heute fällig";
    if (state === "upcoming") return "In den nächsten 24 Stunden";
    return "";
  },

  _archiveLinks(dayId = "", stopId = "") {
    const day = cleanText(dayId);
    const stop = cleanText(stopId);
    return {
      day_ids: day ? [day] : [],
      stop_links: day && stop ? [{ day_id: day, stop_id: stop }] : [],
      people: [],
    };
  },

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
  },

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
  },

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
  },

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
  },

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
  },

  _supportedArchiveFile(files) {
    return (files || []).find((file) => file instanceof File && this._isSupportedArchiveMime(file.type || "", file.name || "")) || null;
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
  },

  async _removeCachedDocument(documentId) {
    await this._archiveCacheDelete(documentId).catch(() => undefined);
    this._offlineDocumentIds.delete(documentId);
    this._showToast("Lokale Gerätekopie entfernt", "success", 3500);
    this._render({ preserveScroll: true });
  },

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
  },

  _archiveCacheKey(documentId) {
    return `${this._selectedTripId || "trip"}:${documentId}`;
  },

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
  },

  async _archiveCacheGet(documentId) {
    const db = await this._archiveDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("files", "readonly");
      const request = tx.objectStore("files").get(this._archiveCacheKey(documentId));
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error || new Error("Lokale Dokumentkopie konnte nicht gelesen werden."));
    });
  },

  async _archiveCacheDelete(documentId) {
    const db = await this._archiveDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("files", "readwrite");
      tx.objectStore("files").delete(this._archiveCacheKey(documentId));
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error || new Error("Lokale Dokumentkopie konnte nicht gelöscht werden."));
    });
  },

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
  },

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
  },

  _archiveDayLabel(dayId) {
    const day = this._findDay(dayId);
    if (!day) return cleanText(dayId) || "Reise";
    return `${this._formatDate(day.date)} · ${day.title || day.id}`;
  },

  _archiveStopLabel(dayId, stopId) {
    const stop = this._findStop(dayId, stopId);
    if (!stop) return cleanText(stopId) || "Stopp";
    return stop.name || stop.id;
  },

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
  },

  _archiveLinkLabel(item) {
    const dayId = item?.day_id || item?.links?.day_ids?.[0] || item?.links?.stop_links?.[0]?.day_id || "";
    const stopId = item?.stop_id || item?.links?.stop_links?.[0]?.stop_id || "";
    if (dayId && stopId) return `${this._archiveDayLabel(dayId)} · ${this._archiveStopLabel(dayId, stopId)}`;
    if (dayId) return this._archiveDayLabel(dayId);
    return "Gesamte Reise";
  },

  _archiveRecordsForDay(dayId) {
    const archive = this._archiveData();
    const bucket = archive.by_day?.[dayId] || { documents: [], expenses: [], todos: [] };
    const byIds = (items, ids) => ids.map((id) => items.find((item) => item.id === id)).filter(Boolean);
    return {
      documents: byIds(archive.documents || [], bucket.documents || []),
      expenses: byIds(archive.expenses || [], bucket.expenses || []),
      todos: byIds(archive.todos || [], bucket.todos || []),
    };
  },

  _archiveRecordsForStop(dayId, stopId) {
    const archive = this._archiveData();
    const bucket = archive.by_stop?.[`${dayId}/${stopId}`] || { documents: [], expenses: [], todos: [] };
    const byIds = (items, ids) => ids.map((id) => items.find((item) => item.id === id)).filter(Boolean);
    return {
      documents: byIds(archive.documents || [], bucket.documents || []),
      expenses: byIds(archive.expenses || [], bucket.expenses || []),
      todos: byIds(archive.todos || [], bucket.todos || []),
    };
  },

  _archiveTotalText() {
    const totals = this._archiveData().stats?.totals_by_currency || {};
    const entries = Object.entries(totals);
    if (!entries.length) return "Noch keine Ausgaben";
    return entries.map(([currency, amount]) => this._formatMoney(amount, currency)).join(" · ");
  },

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
  },

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
  },

  _renderArchiveExpenseCard(item) {
    const category = archiveExpenseCategoryLabels[item.category] || item.category || "Sonstiges";
    return `<article class="archive-row">
      <div class="archive-row-icon"><ha-icon icon="mdi:cash"></ha-icon></div>
      <div class="archive-row-copy"><strong>${escapeHtml(item.merchant || category)}</strong><span>${escapeHtml(category)} · ${escapeHtml(item.date ? this._formatDate(item.date) : "Datum offen")} · ${escapeHtml(this._archiveLinkLabel(item))}</span>${item.notes ? `<small>${escapeHtml(item.notes)}</small>` : ""}</div>
      <div class="archive-row-value"><strong>${escapeHtml(this._formatMoney(item.amount, item.currency))}</strong><span>${escapeHtml(archiveStatusLabels[item.status] || item.status || "")}</span></div>
      ${this._canEdit() ? `<div class="archive-row-actions"><button class="icon-button" type="button" data-action="archive-edit-expense" data-expense-id="${escapeHtml(item.id)}" title="Ausgabe bearbeiten"><ha-icon icon="mdi:pencil-outline"></ha-icon></button><button class="icon-button danger-text" type="button" data-action="archive-delete-expense" data-expense-id="${escapeHtml(item.id)}" title="Ausgabe löschen"><ha-icon icon="mdi:delete-outline"></ha-icon></button></div>` : ""}
    </article>`;
  },

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
  },

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
  },

  _renderStopArchiveSummary(day, stop) {
    const sourceDayId = stop._inherited ? stop._sourceDayId : day.id;
    const records = this._archiveRecordsForStop(sourceDayId, stop.id);
    const openTodos = records.todos.filter((item) => item.status === "open");
    const count = records.documents.length + records.expenses.length + openTodos.length;
    return `<div class="stop-archive-summary">
      ${count ? `<div class="stop-archive-counts">${records.documents.length ? `<span><ha-icon icon="mdi:file-document-outline"></ha-icon>${records.documents.length}</span>` : ""}${records.expenses.length ? `<span><ha-icon icon="mdi:cash"></ha-icon>${records.expenses.length}</span>` : ""}${openTodos.length ? `<span><ha-icon icon="mdi:checkbox-blank-circle-outline"></ha-icon>${openTodos.length}</span>` : ""}</div>` : ""}
      ${this._canEdit() && !stop._inherited ? `<div class="button-row stop-archive-actions"><button class="text-button" type="button" data-action="archive-stop-attach" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:paperclip"></ha-icon> Dokument</button><button class="text-button" type="button" data-action="archive-add-expense" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:cash-plus"></ha-icon> Ausgabe</button><button class="text-button" type="button" data-action="archive-add-todo" data-day-id="${escapeHtml(day.id)}" data-stop-id="${escapeHtml(stop.id)}"><ha-icon icon="mdi:checkbox-marked-circle-plus-outline"></ha-icon> Aufgabe</button></div>` : ""}
    </div>`;
  },

  _archiveDayOptions(selected = "") {
    return [{ value: "", label: "Gesamte Reise / nicht zugeordnet" }, ...(this._data?.days?.days || []).map((day) => ({
      value: day.id,
      label: `${this._formatDate(day.date)} · ${day.title || day.id}`,
    }))];
  },

  _archiveStopOptions(selected = "") {
    const result = [{ value: "", label: "Kein konkreter Stopp" }];
    for (const day of this._data?.days?.days || []) {
      for (const stop of this._canonicalStops(day.stops || [])) {
        result.push({ value: `${day.id}::${stop.id}`, label: `${this._formatDate(day.date)} · ${stop.name || stop.id}` });
      }
    }
    return result;
  },

  _archiveSelectedLink(documentItem = {}, analysis = {}) {
    const resolved = analysis.resolved_links && typeof analysis.resolved_links === "object" ? analysis.resolved_links : {};
    const links = resolved.day_ids?.length || resolved.stop_links?.length ? resolved : (documentItem.links || {});
    const stopLink = links.stop_links?.[0] || null;
    return {
      dayId: stopLink?.day_id || links.day_ids?.[0] || "",
      stopRef: stopLink ? `${stopLink.day_id}::${stopLink.stop_id}` : "",
    };
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
  },
};
