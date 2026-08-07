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
    // After the probe, not before: adopting a job re-renders, and doing
    // that while the probe is still writing its own fields would show a
    // half-filled card.
    await this._rendererAppAdoptRunningJob();
  },

  /**
   * Load the day list for the mini export.
   *
   * Kept separate from the environment probe: the day list depends on the
   * selected trip and is worth re-reading after a trip change, while the
   * environment does not change between renders.
   */
  async _rendererAppLoadDays() {
    if (this._rendererAppDaysLoading) return;
    this._rendererAppDaysLoading = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "renderer_app_trip_days",
        { trip_id: this._selectedTripId },
        "",
        { refresh: false, blockUi: false, errorTitle: "Die Reisetage konnten nicht geladen werden" },
      );
      this._rendererAppTripDays = result?.renderer_app_trip_days || [];
      if (
        !this._rendererAppDayId ||
        !this._rendererAppTripDays.some((day) => day.day_id === this._rendererAppDayId)
      ) {
        // Preselect a day that can actually be exported, so the first
        // click does something instead of explaining why it cannot.
        const usable = this._rendererAppTripDays.find((day) => day.exportable);
        this._rendererAppDayId = usable ? usable.day_id : "";
      }
    } finally {
      this._rendererAppDaysLoading = false;
      this._render({ preserveScroll: true });
    }
  },

  async _rendererAppMiniExport() {
    if (!this._rendererAppDayId) return;
    const result = await this._runAction(
      "renderer_app_trip_day",
      { trip_id: this._selectedTripId, day_id: this._rendererAppDayId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorTitle: "Der Mini-Export konnte nicht gestartet werden",
      },
    );
    if (!result?.renderer_app_job?.job_id) return;
    this._rendererAppKind = "trip_day";
    // The status file the poll returns knows nothing about the package -
    // it carries a job state and nothing else. What was handed over is
    // kept here, or the result would be unable to say how much travelled.
    this._rendererAppPackage = {
      package_bytes: result.renderer_app_job.package_bytes,
      image_count: result.renderer_app_job.image_count,
      stop_count: result.renderer_app_job.stop_count,
      day_title: result.renderer_app_job.day_title,
      day_date: result.renderer_app_job.day_date,
    };
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    // A link to the previous video would be a lie about this one.
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
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
    this._rendererAppPackage = null;
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    // A link to the previous video would be a lie about this one.
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  /**
   * Find a job that is already running, and take it over.
   *
   * The browser is not where a render lives. A trip film takes a quarter
   * of an hour, and in that time a phone locks and Home Assistant reloads
   * its page - at which point every variable this card kept is gone while
   * the job runs on in another container. Asking the exchange folder is
   * the only way back to it, and it is also how a result that finished
   * while nobody was looking becomes reachable again.
   */
  _rendererAppAdoptOnce() {
    // Called from render, so it has to be free after the first time and
    // must never make the render wait on a network round trip.
    if (this._rendererAppAdoptTried) return;
    this._rendererAppAdoptTried = true;
    void this._rendererAppEnsureStatus();
    void this._rendererAppAdoptRunningJob();
  },

  /**
   * Ask whether the app is alive, once, before anybody claims it is not.
   *
   * The story card read `_rendererAppStatus?.online` and said "die
   * Renderer-App ist nicht erreichbar" when it was false - but that field
   * was only ever filled by pressing "Umgebung prüfen" in a different
   * card. So on any freshly loaded page the card announced the app as
   * unreachable and disabled the film button, while the app was running
   * perfectly well and had just finished a film. Not knowing is not the
   * same as knowing it is down, and only one of the two may be said out
   * loud.
   */
  async _rendererAppEnsureStatus() {
    if (this._rendererAppStatus || this._rendererAppStatusLoading) return;
    this._rendererAppStatusLoading = true;
    try {
      const status = await this._runAction("renderer_app_status", {}, "", {
        refresh: false,
        blockUi: false,
        errorTitle: "",
      });
      if (status?.renderer_app_status) {
        this._rendererAppStatus = status.renderer_app_status;
        this._rendererAppRedraw();
      }
    } finally {
      this._rendererAppStatusLoading = false;
    }
  },

  async _rendererAppAdoptRunningJob() {
    if (this._rendererAppJob || this._rendererAppPolling) return;
    this._rendererAppRecentPending = true;
    const result = await this._runAction("renderer_app_recent_jobs", {}, "", {
      refresh: false,
      blockUi: false,
      errorTitle: "",
    });
    this._rendererAppRecentPending = false;
    // `_runAction` reports a failure by returning null rather than by
    // throwing, so a catch block here would never fire and every failure
    // would read as "no jobs". The presence of the list is the only
    // honest way to tell an answer from a non-answer - and the two need
    // very different reactions from whoever is looking at the card.
    const answered = Boolean(result && Array.isArray(result.renderer_app_recent_jobs));
    this._rendererAppRecent = answered ? result.renderer_app_recent_jobs : [];
    this._rendererAppRecentAsked = answered;
    const active = result?.renderer_app_active_job;
    const recent = this._rendererAppRecent;
    if (!recent.length) {
      this._rendererAppRedraw();
      return;
    }
    const adopted = active || recent[0];
    if (!adopted?.job_id) return;
    this._rendererAppJob = adopted;
    this._rendererAppKind = adopted.kind || "";
    // The package facts were only ever in the browser that submitted the
    // job. Rather than invent them, the card shows the job without them.
    this._rendererAppPackage = null;
    this._rendererAppResult = active ? null : result?.renderer_app_result || null;
    this._rendererAppRedraw();
    if (active) this._pollRendererAppJob(active.job_id);
  },

  /**
   * Poll until the job settles, bounded by wall-clock time rather than by
   * a number of attempts.
   *
   * A count is a duration in disguise, and the disguise is what makes it
   * wrong: 150 attempts sounded generous until a trip film took fourteen
   * minutes and the card stopped watching after five, leaving a render
   * that was going perfectly well looking abandoned.
   */
  async _pollRendererAppJob(jobId) {
    if (this._rendererAppPolling) return;
    this._rendererAppPolling = true;
    const deadline =
      Date.now() + (this._rendererAppKind === "trip_film" ? 45 : 15) * 60 * 1000;
    try {
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 2000));
        if (!this.isConnected) return;
        const result = await this._runAction(
          "renderer_app_job_status",
          { job_id: jobId },
          "",
          { refresh: false, blockUi: false, errorTitle: "" },
        ).catch(() => null);
        if (!result?.renderer_app_job) continue;
        const before = this._rendererAppJob;
        this._rendererAppJob = result.renderer_app_job;
        if (result.renderer_app_result) this._rendererAppResult = result.renderer_app_result;
        // A percentage that ticked up is not a reason to rebuild the page.
        // The whole shadow DOM is replaced on render, and the scroll
        // offset is restored against a document that has not finished
        // laying out - so every two seconds the view jumped back to the
        // top while the render was still going (live report). Structural
        // change earns a render; a number does not.
        //
        // And when the number is not on screen at all - the user is on
        // another tab, a dialog covers the card - there is nothing to
        // show, so nothing is drawn. The earlier version fell back to a
        // full render in exactly that case, which is how the Erinnerungen
        // tab and an open form both ended up twitching every two seconds
        // (live reports). "Nothing to update" is not a reason to rebuild
        // the page; it is the reason not to.
        const structural =
          !before || Boolean(before.terminal) !== Boolean(result.renderer_app_job.terminal);
        if (structural) this._rendererAppRedraw();
        else this._rendererAppPatchProgress();
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
            this._rendererAppRedraw();
          }
          return;
        }
      }
    } finally {
      this._rendererAppPolling = false;
    }
  },

  /**
   * Make the finished video fetchable.
   *
   * The result sits in the exchange folder, which no Home Assistant view
   * serves - so a film that rendered perfectly was still only reachable
   * with a file browser. This copies it into the video library that
   * already exists, and hands back the same kind of unguessable link the
   * other exports use rather than inventing a second way in.
   */
  async _rendererAppDownload() {
    const jobId = this._rendererAppJob?.job_id;
    if (!jobId || this._rendererAppDownloading) return;
    this._rendererAppDownloading = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "renderer_app_download",
        { job_id: jobId },
        "",
        {
          refresh: false,
          blockUi: false,
          errorTitle: "Das Video konnte nicht bereitgestellt werden",
        },
      );
      this._rendererAppDownloadUrl = result?.renderer_app_download_url || "";
    } finally {
      this._rendererAppDownloading = false;
      this._render({ preserveScroll: true });
    }
  },

  /**
   * Redraw, unless the user is in the middle of something.
   *
   * A render replaces the whole shadow DOM, so doing it under an open
   * dialog tears that dialog down and builds a new one - which is why a
   * vehicle form visibly jumped every two seconds while a film rendered
   * (live report), and why anything typed into it was at risk.
   *
   * Skipping costs nothing: `_closeDialog` renders on its way out, so
   * whatever changed while the dialog was open is drawn the moment it
   * closes. The same rule already governs background refreshes; the
   * progress poll had simply not been told about it.
   */
  _rendererAppRedraw() {
    if (this._dialog || this._storyAnyDirty?.()) return;
    this._render({ preserveScroll: true });
  },

  _rendererAppProgressPercent() {
    return Math.round((Number(this._rendererAppJob?.progress) || 0) * 100);
  },

  /**
   * Write the new percentage into the nodes that already show it.
   *
   * Does nothing when no such node is on screen, and that is the whole
   * point: the user may be on another tab, reading, scrolling or typing
   * while a render runs, and a progress tick has no business
   * interrupting any of that. The card is drawn by the normal render
   * path when it next appears; it does not need this poll to fetch it.
   */
  _rendererAppPatchProgress() {
    const job = this._rendererAppJob;
    const nodes = this.shadowRoot?.querySelectorAll("[data-renderer-progress]");
    if (!job || !nodes?.length) return false;
    const percent = this._rendererAppProgressPercent();
    nodes.forEach((node) => {
      node.textContent =
        node.dataset.rendererProgress === "story"
          ? `${job.state || "läuft"} · ${percent} %`
          : `${percent} %`;
    });
    return true;
  },

  /**
   * Say what the exchange folder answered - including "nothing".
   *
   * Live report: the card stayed empty with a finished film on disk, and
   * an empty card cannot be told apart from a card whose question failed
   * or was never asked. Those three need different reactions from the
   * person looking at it, so they get different sentences.
   */
  _rendererAppRecentLine() {
    if (!this._rendererAppAdoptTried || this._rendererAppRecentPending) return "";
    if (!this._rendererAppRecentAsked) {
      return "<small>Die Auftragsliste hat nicht geantwortet – vermutlich läuft noch eine ältere Roadplanner-Version. Nach dem Update die Seite neu laden.</small>";
    }
    const recent = this._rendererAppRecent || [];
    if (!recent.length) {
      return "<small>Der Austauschordner meldet keine Aufträge – weder laufende noch fertige.</small>";
    }
    const newest = recent[0];
    return `<small>${escapeHtml(String(recent.length))} Auftrag/Aufträge im Austauschordner, neuester: ${escapeHtml(newest.kind || "unbekannter Typ")} · ${escapeHtml(String(newest.state || "?"))}${newest.updated_at ? ` · ${escapeHtml(String(newest.updated_at))}` : ""}</small>`;
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
      // The line that answers "why is this card empty?" - the report is
      // what gets sent when something is wrong, so it has to carry it.
      `Auftragsliste: ${
        !this._rendererAppRecentAsked
          ? "keine Antwort (ältere Version?)"
          : (this._rendererAppRecent || []).length === 0
            ? "leer"
            : (this._rendererAppRecent || [])
                .map((entry) => `${entry.kind || "?"}/${entry.state || "?"}/${entry.updated_at || "?"}`)
                .join(", ")
      }`,
      this._rendererAppPackage
        ? `Renderpaket: ${Math.round((this._rendererAppPackage.package_bytes || 0) / 1024)} kB, ${this._rendererAppPackage.image_count} Bilder, ${this._rendererAppPackage.stop_count} Stopps (${this._rendererAppPackage.day_date} ${this._rendererAppPackage.day_title})`
        : "",
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
    // The card that shows jobs has to look for one, exactly like the
    // story card does. Hanging this on the environment probe instead
    // meant a finished film stayed invisible until somebody happened to
    // press "Umgebung prüfen" - which nobody does when they are looking
    // for their video.
    this._rendererAppAdoptOnce();
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

    // --- the mini export with real data ---------------------------------
    // Offered only once the app is reachable: a day list is pointless if
    // nothing can render it, and a disabled dropdown explains less than
    // the app line above it already does.
    const days = this._rendererAppTripDays;
    const exportable = (days || []).filter((day) => day.exportable);
    const selectedDay = (days || []).find((day) => day.day_id === this._rendererAppDayId);
    let miniExportBlock = "";
    if (status?.online) {
      const options = (days || [])
        .map((day) => {
          const label = [
            `Tag ${day.number}`,
            day.date,
            day.title || "ohne Titel",
            day.photo_count ? `${day.photo_count} Fotos` : "keine Fotos",
          ]
            .filter(Boolean)
            .join(" · ");
          return `<option value="${escapeHtml(day.day_id)}"${day.day_id === this._rendererAppDayId ? " selected" : ""}${day.exportable ? "" : " disabled"}>${escapeHtml(label)}</option>`;
        })
        .join("");
      miniExportBlock = `<div class="notice neutral"><div>
        <strong>Mini-Export mit echten Daten</strong>
        <small>Ein einzelner Reisetag: Titel, Datum, Stopps und bis zu fünf verkleinerte Fotokopien ohne Aufnahmedaten. Es wird weder das Roadbook noch die Mediathek übergeben.</small>
        <div class="button-row">
          ${
            days
              ? `<select class="renderer-app-day-select" data-action="renderer-app-day"${exportable.length ? "" : " disabled"}>${options || "<option value=\"\">Keine Reisetage</option>"}</select>`
              : ""
          }
          <button class="secondary-button" type="button" data-action="renderer-app-load-days"${this._rendererAppDaysLoading ? " disabled" : ""}><ha-icon icon="mdi:calendar-search"></ha-icon> ${this._rendererAppDaysLoading ? "Lade …" : days ? "Tage neu laden" : "Reisetage laden"}</button>
          <button class="secondary-button" type="button" data-action="renderer-app-trip-day"${selectedDay?.exportable && !running ? "" : " disabled"}><ha-icon icon="mdi:filmstrip"></ha-icon> Tagesvideo erzeugen</button>
        </div>
        ${days && !exportable.length ? "<small>Kein Reisetag dieser Reise hat ein Foto - ohne Bild ergibt der Mini-Export keinen Nachweis.</small>" : ""}
      </div></div>`;
    }

    let jobBlock = "";
    if (running) {
      const percent = Math.round((Number(job.progress) || 0) * 100);
      const label =
        this._rendererAppKind === "trip_film"
          ? "Reisefilm wird gerendert"
          : this._rendererAppKind === "trip_day"
            ? "Tagesvideo wird gerendert"
          : this._rendererAppKind === "render"
            ? "Testvideo wird gerendert"
            : "Testauftrag läuft";
      // The percentage sits in its own node so a tick can be written into
      // it without rebuilding the page around it.
      jobBlock = `<div class="notice neutral trip-video-status"><div class="spinner small"></div><span>${label} … <span data-renderer-progress="card">${percent} %</span></span></div>`;
    } else if (job?.state === "completed" && result?.video) {
      const v = result.video;
      const t = result.timings || {};
      // .notice is a flex row and stacks only what sits inside a single
      // child div - text with <br> would become one column per element.
      const pack = this._rendererAppPackage;
      jobBlock = `<div class="notice neutral"><div>
        <strong>${this._rendererAppKind === "trip_film" ? "Reisefilm erzeugt" : this._rendererAppKind === "trip_day" ? "Tagesvideo erzeugt" : "Testvideo erzeugt"}</strong>
        ${
          pack
            ? `<span>${escapeHtml([pack.day_date, pack.day_title].filter(Boolean).join(" · "))}</span>
        <small>Renderpaket ${escapeHtml(String(Math.round((pack.package_bytes || 0) / 1024)))} kB · ${escapeHtml(String(pack.image_count || 0))} Bilder · ${escapeHtml(String(pack.stop_count || 0))} Stopps</small>`
            : ""
        }
        <span>${escapeHtml(v.codec)} · ${escapeHtml(String(v.width))} × ${escapeHtml(String(v.height))} · ${escapeHtml(String(v.duration_seconds))} s · ${escapeHtml(String(Math.round((v.size_bytes || 0) / 1024)))} kB</span>
        <small>gesamt ${escapeHtml(String(t.total ?? "?"))} s – Browser ${escapeHtml(String(t.browser_start ?? "?"))} s, Render ${escapeHtml(String(t.render ?? "?"))} s, ffprobe ${escapeHtml(String(t.probe ?? "?"))} s</small>
        <div class="button-row">
          <button class="secondary-button" type="button" data-action="renderer-app-download"${this._rendererAppDownloading ? " disabled" : ""}><ha-icon icon="mdi:download"></ha-icon> ${this._rendererAppDownloading ? "Wird bereitgestellt …" : "Video herunterladen"}</button>
        </div>
        ${
          this._rendererAppDownloadUrl
            ? `<a class="renderer-app-download" href="${escapeHtml(this._rendererAppDownloadUrl)}" download>Bereit – hier speichern</a>`
            : "<small>Die Datei liegt im Austauschordner der App. Das Herunterladen legt eine Kopie in der Videobibliothek ab.</small>"
        }
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
      ${this._rendererAppRecentLine()}
      ${canEdit ? miniExportBlock : ""}
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
