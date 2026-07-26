import { escapeHtml } from "../lib/core-helpers.js";

export const universalImportMixin = {
  _importDocuments() {
    return (this._archiveData().documents || []).filter((item) => item?.analysis?.universal_import);
  },

  _universalImport(documentId) {
    const documentItem = this._archiveDocument(documentId);
    return documentItem?.analysis?.universal_import || null;
  },

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
  },

  _openUniversalImport(documentId) {
    const documentItem = this._archiveDocument(documentId);
    const importResult = documentItem?.analysis?.universal_import;
    if (!documentItem || !importResult) return;
    this._dialog = { type: "universal-import-review", document: documentItem, importResult };
    this._render({ preserveScroll: true });
  },

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
  },

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
  },

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
  },
};
