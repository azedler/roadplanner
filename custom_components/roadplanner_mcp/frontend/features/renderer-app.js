import { escapeHtml } from "../lib/core-helpers.js";

/**
 * Panel for the renderer-app proof of concept.
 *
 * Deliberately separate from the Remotion card next to it: that spike asked
 * whether Home Assistant could run Node itself (it cannot), this one asks
 * whether a separate app can carry the runtime instead. They report
 * different things and must never be read as one status.
 *
 * The card has to stay honest about three different absences, because they
 * mean different things and only one of them is a problem:
 *
 * - no Supervisor: no app can ever be installed here, and that is the
 *   answer to the feasibility question, not a fault;
 * - Supervisor present but no heartbeat: the app is simply not installed
 *   or not started, which is the normal state for everyone;
 * - heartbeat present but stale: the app WAS running and stopped, which is
 *   the one case worth showing as a warning.
 */
/**
 * Show the returned SVG without ever letting it run.
 *
 * The exchange folder is writable by another container, and the SHA-256
 * that "verifies" an artefact sits in the same file as the artefact - so
 * whoever can forge one can forge both. The bytes are therefore untrusted,
 * and injecting them into the panel's DOM would be a script-injection path
 * straight into Home Assistant's frontend.
 *
 * An `<img>` with a data: URL renders SVG as an image: scripts, foreign
 * objects and external references are all inert in that context. The
 * picture still proves what the PoC needs it to - that a renderable
 * artefact survived the channel intact.
 */
function inertSvgTag(svg) {
  const bytes = new TextEncoder().encode(String(svg || ""));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const encoded = window.btoa(binary);
  return `<img class="renderer-app-artifact" alt="Testartefakt der Renderer-App" src="data:image/svg+xml;base64,${encoded}">`;
}

export const rendererAppMixin = {
  async _rendererAppProbe() {
    if (this._rendererAppProbing) return;
    this._rendererAppProbing = true;
    this._render({ preserveScroll: true });
    try {
      const environment = await this._runAction("renderer_app_environment", {}, "", {
        refresh: false,
        blockUi: false,
        errorTitle: "Die Umgebungsprüfung ist fehlgeschlagen",
      });
      if (environment?.renderer_app_environment) {
        this._rendererAppEnvironment = environment.renderer_app_environment;
      }
      const status = await this._runAction("renderer_app_status", {}, "", {
        refresh: false,
        blockUi: false,
        errorTitle: "",
      }).catch(() => null);
      if (status?.renderer_app_status) {
        this._rendererAppStatus = status.renderer_app_status;
      }
    } finally {
      this._rendererAppProbing = false;
      this._render({ preserveScroll: true });
    }
  },

  async _rendererAppRun(action = "renderer_app_run") {
    const result = await this._runAction(action, {}, "", {
      refresh: false,
      blockUi: false,
      errorTitle:
        action === "renderer_app_render"
          ? "Der Testrender konnte nicht gestartet werden"
          : "Der Testauftrag konnte nicht übergeben werden",
    });
    if (!result?.renderer_app_job?.job_id) return;
    // The status file carries no action, so the kind of job is remembered
    // here - otherwise a render would be announced as a plain test.
    this._rendererAppKind = action === "renderer_app_render" ? "render" : "test";
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  async _pollRendererAppJob(jobId) {
    if (this._rendererAppPolling) return;
    this._rendererAppPolling = true;
    try {
      for (let attempt = 0; attempt < 150; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        if (!this.isConnected) return;
        const result = await this._runAction(
          "renderer_app_job_status",
          { job_id: jobId },
          "",
          { refresh: false, blockUi: false, errorTitle: "" },
        ).catch(() => null);
        if (!result?.renderer_app_job) continue;
        this._rendererAppJob = result.renderer_app_job;
        if (result.renderer_app_result) this._rendererAppResult = result.renderer_app_result;
        this._render({ preserveScroll: true });
        if (result.renderer_app_job.terminal) {
          // The App line otherwise keeps showing whatever the last
          // environment probe saw - which is stale after an app update.
          const status = await this._runAction("renderer_app_status", {}, "", {
            refresh: false,
            blockUi: false,
            errorTitle: "",
          }).catch(() => null);
          if (status?.renderer_app_status) {
            this._rendererAppStatus = status.renderer_app_status;
            this._render({ preserveScroll: true });
          }
          return;
        }
      }
    } finally {
      this._rendererAppPolling = false;
    }
  },

  _rendererAppReportText() {
    const environment = this._rendererAppEnvironment || {};
    const details = environment.details || {};
    const status = this._rendererAppStatus || {};
    const job = this._rendererAppJob || {};
    const result = this._rendererAppResult;
    const field = (key, present, absent) => {
      if (!Object.prototype.hasOwnProperty.call(details, key)) return "nicht geprüft";
      return details[key] ? present : absent;
    };
    return [
      "Roadplanner Renderer-App-PoC – Live-Ergebnis",
      `Status: ${environment.status || "nicht geprüft"}`,
      `Plattform: ${details.platform || "?"} / ${details.machine || "?"} (${details.arch || "?"})`,
      `Supervisor: ${field("supervisor", "ja", "nein")}`,
      `/share vorhanden: ${field("share_exists", "ja", "nein")}`,
      `Austauschordner: ${details.exchange_dir || "?"}`,
      `Austauschordner beschreibbar: ${field("exchange_writable", "ja", "nein")}`,
      `App installiert: ${status.installed ? "ja" : "nein"}`,
      `App-Zustand: ${status.state || "–"}${status.online ? " (online)" : ""}`,
      `App-Version: ${status.app_version || "–"}`,
      `Heartbeat-Alter: ${status.age_seconds === undefined || status.age_seconds === null ? "–" : `${status.age_seconds} s`}`,
      `Testauftrag: ${job.job_id || "nicht gelaufen"}`,
      `Jobzustand: ${job.state || "–"}`,
      result?.video
        ? `Video: ${result.video.codec} ${result.video.width}x${result.video.height}, ${result.video.duration_seconds} s, ${result.video.size_bytes} B`
        : "",
      result?.timings
        ? `Zeiten: gesamt ${result.timings.total} s (Browser ${result.timings.browser_start} s, Render ${result.timings.render} s, ffprobe ${result.timings.probe} s)`
        : "",
      job.error ? `Jobfehler: ${job.error.code} – ${job.error.message}` : "",
      result
        ? `Artefakte: ${result.artifacts.map((a) => `${a.filename} (${a.size_bytes} B)`).join(", ")}`
        : "Artefakte: –",
      status.reason ? `Hinweis: ${status.reason}` : "",
    ]
      .filter(Boolean)
      .join("\n");
  },

  _renderRendererApp() {
    const environment = this._rendererAppEnvironment;
    const details = environment?.details || {};
    const status = this._rendererAppStatus;
    const job = this._rendererAppJob;
    const result = this._rendererAppResult;
    const canEdit = this._canEdit();
    const running = Boolean(job && !job.terminal && job.state);

    const line = (label, value, ok) =>
      `<div><span>${escapeHtml(label)}</span><strong class="${ok ? "" : "muted"}">${escapeHtml(value)}</strong></div>`;

    // Three absences, three different meanings - see the module comment.
    let appLine = "nicht geprüft";
    let appOk = false;
    if (status) {
      if (!status.installed) {
        appLine = "nicht installiert";
      } else if (status.online) {
        appLine = `${status.state} · ${status.app_version || "?"}`;
        appOk = true;
      } else if (status.state) {
        appLine = `${status.state} · Heartbeat veraltet`;
      } else {
        appLine = status.reason || "nicht erreichbar";
      }
    }

    let jobBlock = "";
    if (running) {
      const percent = Math.round((Number(job.progress) || 0) * 100);
      const label =
        this._rendererAppKind === "render" ? "Testvideo wird gerendert" : "Testauftrag läuft";
      jobBlock = `<div class="notice neutral trip-video-status"><div class="spinner small"></div><span>${label} … ${percent} %</span></div>`;
    } else if (job?.state === "completed" && result?.video) {
      const v = result.video;
      const t = result.timings || {};
      // .notice is a flex row and stacks only what sits inside a single
      // child div - text with <br> would become one column per element.
      jobBlock = `<div class="notice neutral"><div>
        <strong>Testvideo erzeugt</strong>
        <span>${escapeHtml(v.codec)} · ${escapeHtml(String(v.width))} × ${escapeHtml(String(v.height))} · ${escapeHtml(String(v.duration_seconds))} s · ${escapeHtml(String(Math.round((v.size_bytes || 0) / 1024)))} kB</span>
        <small>gesamt ${escapeHtml(String(t.total ?? "?"))} s – Browser ${escapeHtml(String(t.browser_start ?? "?"))} s, Render ${escapeHtml(String(t.render ?? "?"))} s, ffprobe ${escapeHtml(String(t.probe ?? "?"))} s</small>
        <small>Die Datei liegt im Austauschordner; sie wird bewusst nicht ins Panel geladen.</small>
      </div></div>`;
    } else if (job?.state === "completed" && result) {
      jobBlock = `<div class="notice neutral"><div>
        <strong>Testauftrag erfolgreich</strong>
        ${result.artifacts
          .map((item) => `<span>${escapeHtml(item.filename)} · ${escapeHtml(String(item.size_bytes))} B</span>`)
          .join("")}
      </div></div>
      ${result.svg ? inertSvgTag(result.svg) : ""}`;
    } else if (job?.state && job.terminal) {
      jobBlock = `<div class="notice warning"><div>
        <strong>Testauftrag ${escapeHtml(job.state)}</strong>
        ${job.error ? `<span>${escapeHtml(job.error.code)}: ${escapeHtml(job.error.message)}</span>` : ""}
      </div></div>`;
    }

    return `<section class="panel-card">
      <div class="section-heading compact">
        <div><span class="eyebrow">Experiment</span><h2>Renderer-App</h2></div>
      </div>
      <p class="hint">Machbarkeitsnachweis für eine optionale Home-Assistant-App als späteren Renderer. Ohne diese App funktioniert Roadplanner unverändert – „nicht installiert“ ist kein Fehler. Es wird nichts installiert und nichts heruntergeladen.</p>
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="renderer-app-probe"${this._rendererAppProbing ? " disabled" : ""}><ha-icon icon="mdi:lan-connect"></ha-icon> ${this._rendererAppProbing ? "Prüfe …" : "Umgebung prüfen"}</button>
        ${canEdit ? `<button class="secondary-button" type="button" data-action="renderer-app-run"${status?.online && !running ? "" : " disabled"}><ha-icon icon="mdi:play-box-outline"></ha-icon> Testauftrag senden</button>` : ""}
        ${canEdit ? `<button class="secondary-button" type="button" data-action="renderer-app-render"${status?.online && !running ? "" : " disabled"}><ha-icon icon="mdi:movie-open-play-outline"></ha-icon> Testvideo rendern</button>` : ""}
        ${environment ? `<button class="text-button" type="button" data-action="renderer-app-copy-report"><ha-icon icon="mdi:clipboard-text-outline"></ha-icon> Bericht kopieren</button>` : ""}
      </div>
      ${
        environment
          ? `<div class="facts-grid">
        ${line("Supervisor", details.supervisor ? "vorhanden" : "nicht vorhanden", Boolean(details.supervisor))}
        ${line("/share", details.share_exists ? "vorhanden" : "nicht vorhanden", Boolean(details.share_exists))}
        ${line("Austauschordner", details.exchange_writable ? "beschreibbar" : "nicht beschreibbar", Boolean(details.exchange_writable))}
        ${line("Architektur", details.arch || "?", Boolean(details.arch))}
        ${line("App", appLine, appOk)}
      </div>
      <div class="notice ${environment.ready ? "neutral" : "warning"}"><div>
        <strong>${escapeHtml(environment.summary_de || "")}</strong>
        <small>${escapeHtml(environment.recommended_next_step_de || "")}</small>
      </div></div>`
          : ""
      }
      ${jobBlock}
    </section>`;
  },
};
