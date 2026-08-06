import { escapeHtml } from "../lib/core-helpers.js";

/**
 * Experimental Remotion panel, deliberately kept apart from the production
 * video export: its own status, its own button, its own result. A test
 * render must never be mistaken for - or overwrite - the trip's video.
 *
 * The render button only becomes available once the read-only diagnosis
 * reports READY. Nothing here installs or downloads anything; when the
 * runtime is missing, the honest report IS the result of the experiment.
 */
export const remotionSpikeMixin = {
  async _remotionDiagnose() {
    if (this._remotionDiagnosing) return;
    this._remotionDiagnosing = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction("remotion_diagnose", {}, "", {
        refresh: false,
        blockUi: false,
        errorTitle: "Die Umgebungsprüfung ist fehlgeschlagen",
      });
      if (result?.remotion_diagnosis) this._remotionDiagnosis = result.remotion_diagnosis;
    } finally {
      this._remotionDiagnosing = false;
      this._render({ preserveScroll: true });
    }
  },

  async _remotionTestRender() {
    if (!this._remotionDiagnosis?.ready) return;
    const result = await this._runAction("remotion_test_render", {}, "", {
      refresh: false,
      blockUi: false,
      errorTitle: "Der Testrender konnte nicht gestartet werden",
    });
    if (!result?.status) return;
    this._remotionStatus = result.status;
    this._render({ preserveScroll: true });
    this._pollRemotionStatus();
  },

  async _remotionCancel() {
    const result = await this._runAction("remotion_cancel", {}, "", {
      refresh: false,
      blockUi: false,
      errorTitle: "Der Abbruch ist fehlgeschlagen",
    });
    if (result?.status) this._remotionStatus = result.status;
    this._render({ preserveScroll: true });
  },

  async _pollRemotionStatus() {
    if (this._remotionPolling) return;
    this._remotionPolling = true;
    try {
      for (let attempt = 0; attempt < 240; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 4000));
        if (!this.isConnected) return;
        const result = await this._runAction("remotion_status", {}, "", {
          refresh: false,
          blockUi: false,
          errorTitle: "",
        }).catch(() => null);
        if (result?.status) {
          this._remotionStatus = result.status;
          this._render({ preserveScroll: true });
          if (result.status.state !== "running") return;
        }
      }
    } finally {
      this._remotionPolling = false;
    }
  },

  _remotionReportText() {
    // What the handover asks to be pasted into the write-up. Paths are
    // included here because the operator explicitly copies this; the
    // rendered panel above stays free of raw system paths.
    const diagnosis = this._remotionDiagnosis || {};
    const status = this._remotionStatus || {};
    const details = diagnosis.details || {};
    const output = status.output || {};
    return [
      "Roadplanner Remotion-Spike – Live-Ergebnis",
      `Status: ${diagnosis.status || "nicht geprüft"}`,
      `Plattform: ${details.platform || "?"} / ${details.machine || "?"}`,
      `Node: ${details.node_version || "nicht gefunden"}`,
      `npm: ${details.npm_path ? "vorhanden" : "nicht gefunden"}`,
      `Browser: ${details.browser_path || "nicht gefunden"}`,
      `Renderer: ${details.renderer_path || "nicht konfiguriert"}`,
      `ffmpeg/ffprobe: ${details.ffmpeg_path ? "ja" : "nein"} / ${details.ffprobe_path ? "ja" : "nein"}`,
      `Ausgabeordner beschreibbar: ${details.output_writable ? "ja" : "nein"}`,
      `Testrender: ${status.status || "nicht gelaufen"}`,
      output.duration_seconds
        ? `Ergebnis: ${output.duration_seconds} s · ${output.width}x${output.height} · ${output.codec} · ${Math.round((output.size_bytes || 0) / 1024)} kB`
        : "Ergebnis: –",
      status.error ? `Fehler: ${status.error}` : "",
    ].filter(Boolean).join("\n");
  },

  _renderRemotionSpike() {
    const diagnosis = this._remotionDiagnosis;
    const status = this._remotionStatus;
    const details = diagnosis?.details || {};
    const running = status?.state === "running";
    const canEdit = this._canEdit();

    const line = (label, value, ok) =>
      `<div><span>${escapeHtml(label)}</span><strong class="${ok ? "" : "muted"}">${escapeHtml(value)}</strong></div>`;

    let result = "";
    if (running) {
      const percent = Math.round((Number(status.progress) || 0) * 100);
      result = `<div class="notice neutral trip-video-status"><div class="spinner small"></div><span>Testvideo wird erzeugt … ${percent} %${status.stage ? ` (${escapeHtml(status.stage)})` : ""}</span></div>
        ${canEdit ? `<div class="button-row"><button class="text-button danger-text" type="button" data-action="remotion-cancel">Abbrechen</button></div>` : ""}`;
    } else if (status?.state === "ready" && status.output) {
      const out = status.output;
      result = `<div class="notice neutral"><strong>Remotion-Test erfolgreich</strong><br>${escapeHtml(String(out.duration_seconds))} s · ${escapeHtml(String(out.width))} × ${escapeHtml(String(out.height))} · ${escapeHtml(out.codec)} · ${escapeHtml(String(Math.round((out.size_bytes || 0) / 1024)))} kB</div>`;
    } else if (status?.state === "error") {
      result = `<div class="notice warning"><strong>Remotion kann in dieser Umgebung noch nicht lokal ausgeführt werden.</strong><br>Grund: ${escapeHtml(status.error || "unbekannt")}<br>Es wurde nichts installiert oder verändert.</div>`;
    }

    return `<section class="panel-card">
      <div class="section-heading compact">
        <div><span class="eyebrow">Experiment</span><h2>Remotion-Unterprozess</h2></div>
      </div>
      <p class="hint">Technischer Machbarkeitstest. Roadplanner installiert dabei nichts und lädt nichts herunter – fehlt eine Voraussetzung, ist genau das das Ergebnis. Der produktive Videoexport bleibt unberührt.</p>
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="remotion-diagnose"${this._remotionDiagnosing ? " disabled" : ""}><ha-icon icon="mdi:stethoscope"></ha-icon> ${this._remotionDiagnosing ? "Prüfe …" : "Umgebung prüfen"}</button>
        ${canEdit ? `<button class="secondary-button" type="button" data-action="remotion-test-render"${diagnosis?.ready && !running ? "" : " disabled"}><ha-icon icon="mdi:filmstrip"></ha-icon> Testvideo rendern</button>` : ""}
        ${diagnosis ? `<button class="text-button" type="button" data-action="remotion-copy-report"><ha-icon icon="mdi:clipboard-text-outline"></ha-icon> Diagnosebericht kopieren</button>` : ""}
      </div>
      ${diagnosis ? `<div class="facts-grid">
        ${line("Node", details.node_version || "nicht gefunden", Boolean(details.node_version))}
        ${line("npm", details.npm_path ? "vorhanden" : "nicht gefunden", Boolean(details.npm_path))}
        ${line("Browser", details.browser_path ? "vorhanden" : "nicht gefunden", Boolean(details.browser_path))}
        ${line("Renderer", details.renderer_path ? "konfiguriert" : "nicht konfiguriert", Boolean(details.renderer_configured))}
        ${line("ffmpeg", details.ffmpeg_path ? "vorhanden" : "nicht gefunden", Boolean(details.ffmpeg_path))}
        ${line("Ausgabeordner", details.output_writable ? "beschreibbar" : "nicht beschreibbar", Boolean(details.output_writable))}
      </div>
      <div class="notice ${diagnosis.ready ? "neutral" : "warning"}">${escapeHtml(diagnosis.summary_de || "")}<br><small>${escapeHtml(diagnosis.recommended_next_step_de || "")}</small></div>` : ""}
      ${result}
    </section>`;
  },
};
