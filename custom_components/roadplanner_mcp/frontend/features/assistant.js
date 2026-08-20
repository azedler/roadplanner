import { escapeHtml, cleanText, newClientRequestId } from "../lib/core-helpers.js";
import { actionButton } from "../lib/action-button.js";

export const assistantMixin = {
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
  },

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
  },

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
  },

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
  },

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
  },

  _renderAssistantLink(url, label = "") {
    const details = this._assistantLinkDetails(url);
    if (!details) return "";
    const display = this._assistantLinkLabel(details.url, label);
    return `<a class="assistant-inline-link ${details.className}" href="${escapeHtml(details.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(details.url)}"><ha-icon icon="${details.icon}"></ha-icon><span>${escapeHtml(display)}</span></a>`;
  },

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
  },

  _normalizeAssistantMarkdownUrl(value) {
    return String(value ?? "")
      .replace(/[\u200B-\u200D\uFEFF]/g, "")
      .replace(/\s+/g, "")
      .trim();
  },

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
  },

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
  },

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
  },

  async _waitForAssistantIdle(timeoutMs = 6000) {
    const deadline = Date.now() + timeoutMs;
    while (this._busy && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return !this._busy;
  },

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
  },

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
  },

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
  },

  async _testAssistantConnection() {
    const result = await this._runAction("assistant_test", {
      trip_id: this._selectedTripId,
    }, "Gemini-Verbindung geprüft", { refresh: false, errorMode: "dialog", errorTitle: "Gemini-Verbindungstest fehlgeschlagen" });
    if (result) {
      this._showToast(result.ok ? "Gemini antwortet zuverlässig" : "Unerwartete Testantwort", result.ok ? "success" : "error", 5000);
    }
  },

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
  },

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
  },

  async _loadAssistantDiagnostics() {
    const result = await this._runAction("assistant_diagnostics", {
      trip_id: this._selectedTripId,
    }, "Assistenten-Diagnose geladen", { refresh: false, errorMode: "dialog", errorTitle: "Assistenten-Diagnose konnte nicht geladen werden" });
    if (!result) return;
    this._assistantDiagnostics = result;
    this._dialog = { type: "assistant-diagnostics", diagnostics: result };
    this._render({ preserveScroll: true });
  },

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
  },

  _assistantAutonomyLabel(level) {
    return {
      answers: "Nur Antworten",
      suggestions: "Antworten & Vorschläge",
      change_basket: "Gespräch & Änderungskorb",
    }[level] || "Gespräch & Änderungskorb";
  },

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
  },

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
      <div class="assistant-hint"><ha-icon icon="mdi:keyboard-return"></ha-icon>Enter erzeugt einen Zeilenumbruch · Senden über den Knopf oder Strg/Cmd+Enter.</div>
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
          ${assistant.copilot_enabled ? actionButton(this._actionCosts(), "assistant-briefing", "Tagesbriefing") : ""}
          <button class="secondary-button compact-button" type="button" data-action="assistant-clear" ${messages.length || basket.length ? "" : "disabled"}><ha-icon icon="mdi:message-refresh-outline"></ha-icon> Neue Unterhaltung</button>
        </div>
      </section>

      ${!this._data.selected_is_active ? `<div class="notice warning"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Planung im Lesemodus</strong><span>Du kannst diese Reise besprechen. Für die Änderungsübersicht muss sie zuerst als aktive Reise gesetzt werden.</span></div></div>` : ""}

      ${showRetryNotice ? `<div class="notice warning assistant-retry-notice"><ha-icon icon="mdi:reload-alert"></ha-icon><div><strong>Die letzte Nachricht wurde nicht beantwortet</strong><span>Der Text bleibt erhalten. Roadplanner kann ihn mit aktuellem Reisekontext erneut senden.</span></div><button class="secondary-button compact-button" type="button" data-action="assistant-retry"><ha-icon icon="mdi:reload"></ha-icon> Erneut senden</button></div>` : ""}

      ${basketEnabled && basket.length ? `<section class="assistant-basket-quickbar panel-card">
        <span class="basket-quick-label"><ha-icon icon="mdi:playlist-check"></ha-icon><strong>Änderungskorb: ${basket.length} vorgemerkt</strong></span>
        <div class="button-row compact-row">
          ${actionButton(this._actionCosts(), "assistant-prepare", "Änderungen prüfen", {
            busy: this._assistantPrepareInFlight,
            busyLabel: "Entwurf wird erstellt …",
            disabled: !this._data.selected_is_active,
            extra: `aria-busy="${this._assistantPrepareInFlight ? "true" : "false"}"`,
          })}
          <button class="text-button" type="button" data-action="assistant-scroll-basket"><ha-icon icon="mdi:arrow-down"></ha-icon> Details ansehen</button>
        </div>
      </section>` : ""}

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
          ${actionButton(this._actionCosts(), "assistant-prepare", "Änderungen prüfen", {
            busy: this._assistantPrepareInFlight,
            busyLabel: "Entwurf wird erstellt …",
            disabled: !(basketEnabled && basket.length && this._data.selected_is_active),
            extra: `aria-busy="${this._assistantPrepareInFlight ? "true" : "false"}"`,
          })}
          <p class="basket-footnote">Der Button erzeugt nur einen prüfbaren Entwurf. Das Reisegespräch läuft danach weiter; übernommen wird weiterhin separat in der Änderungsübersicht.</p>
        </aside>
      </section>

      <details data-section="assistant-details" class="assistant-technical panel-card">
        <summary><span><ha-icon icon="mdi:tools"></ha-icon>Technik & Diagnose</span><small>Providerstatus, Nutzung, Plugins und Fehlerdetails</small></summary>
        <div class="assistant-technical-content">
          <div class="assistant-technical-actions">
            <span class="assistant-model"><ha-icon icon="mdi:creation-outline"></ha-icon>${escapeHtml(assistant.model || settings.assistant_model || "Gemini")}</span>
            <span class="assistant-health ${healthView.className}"><ha-icon icon="${healthView.icon}"></ha-icon>${escapeHtml(healthView.label)}</span>
            ${actionButton(this._actionCosts(), "assistant-test", "Verbindung testen")}
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
  },

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
  },

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
  },

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
  },

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
  },

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
  },

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
  },
};
