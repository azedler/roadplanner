import { escapeHtml } from "../lib/core-helpers.js";

/**
 * The story editor: a small editorial desk on top of the roadbook.
 *
 * It shows the trip as the TravelStoryManifest describes it - one chapter
 * per day - and lets exactly two things be rewritten: a chapter's title
 * and its story. Everything else on the page is a fact, shown so the
 * writer can see what the day actually was, and not editable here.
 *
 * Three decisions shape the interaction, and each of them is about not
 * losing somebody's writing:
 *
 * - **Drafts live per chapter, not in the DOM.** The panel replaces its
 *   whole shadow DOM on every render, so text typed into a textarea would
 *   vanish on the next one. Keystrokes go into `_storyDrafts[chapterId]`
 *   and the field is rendered from there, which also means paging through
 *   chapters keeps unfinished work.
 * - **Typing does not re-render.** The input handler stores and returns.
 *   Re-rendering on every keystroke would move the caret to the end of the
 *   field, which makes editing the middle of a sentence impossible.
 * - **A background refresh waits while something is unsaved**, the same
 *   way it waits while a dialog is open.
 *
 * The manifest is never edited here and never stored. Saving writes two
 * fields onto the day; the description is rebuilt from the canonical data
 * afterwards and comes back with the answer.
 */

const SOURCE_LABELS = {
  override: "von Hand geschrieben",
  directed: "von Gemini redigiert",
  stored: "aus der Tageszusammenfassung",
  composed: "aus den Fakten des Tages",
};
const SOURCE_ICONS = {
  override: "mdi:account-edit-outline",
  directed: "mdi:auto-awesome",
  stored: "mdi:text-box-outline",
  composed: "mdi:auto-fix",
};

/** How a day is weighted, in words rather than in an enum. */
const IMPORTANCE_LABELS = {
  transition: "Überführungstag",
  normal: "normaler Tag",
  highlight: "Höhepunkt",
  major_highlight: "großer Höhepunkt",
};

export const storyEditorMixin = {
  _storyChapters() {
    return this._storyManifest?.chapters || [];
  },

  _storyChapter() {
    const chapters = this._storyChapters();
    if (!chapters.length) return null;
    return (
      chapters.find((chapter) => chapter.chapter_id === this._storyChapterId) || chapters[0]
    );
  },

  /** The draft for a chapter, falling back to what the manifest says. */
  _storyDraft(chapter) {
    const drafts = this._storyDrafts || {};
    const draft = drafts[chapter.chapter_id] || {};
    return {
      title: draft.title !== undefined ? draft.title : chapter.title || "",
      story: draft.story !== undefined ? draft.story : chapter.story?.text || "",
    };
  },

  _storyDirty(chapter) {
    const draft = this._storyDraft(chapter);
    return (
      draft.title !== (chapter.title || "") || draft.story !== (chapter.story?.text || "")
    );
  },

  _storyAnyDirty() {
    return this._storyChapters().some((chapter) => this._storyDirty(chapter));
  },

  async _storyLoad({ force = false } = {}) {
    if (this._storyLoading) return;
    this._storyLoading = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "story_manifest",
        { trip_id: this._selectedTripId, force },
        "",
        {
          refresh: false,
          blockUi: false,
          errorTitle: "Die Reisegeschichte konnte nicht geladen werden",
        },
      );
      if (result?.story_manifest) {
        this._storyManifest = result.story_manifest;
        if (!this._storyChapterId) {
          this._storyChapterId = this._storyChapters()[0]?.chapter_id || "";
        }
      }
    } finally {
      this._storyLoading = false;
      this._render({ preserveScroll: true });
    }
    // Free, and it is what lets the card say whether a run would change
    // anything. Fetched after the manifest so both land in one render.
    if (!this._storyDirector) await this._storyDirectorStatus();
  },

  _storySelectChapter(chapterId) {
    // Drafts survive the move on purpose: paging away from half a sentence
    // and back must not lose it.
    this._storyChapterId = chapterId;
    this._render({ preserveScroll: true });
  },

  _storyStep(offset) {
    const chapters = this._storyChapters();
    const current = chapters.findIndex(
      (chapter) => chapter.chapter_id === this._storyChapter()?.chapter_id,
    );
    const next = chapters[Math.min(chapters.length - 1, Math.max(0, current + offset))];
    if (next) this._storySelectChapter(next.chapter_id);
  },

  /**
   * Store a keystroke. Deliberately does NOT render.
   */
  _handleStoryInput(event) {
    const field = event.target.closest("[data-story-field]");
    if (!field) return;
    const chapter = this._storyChapter();
    if (!chapter) return;
    this._storyDrafts = this._storyDrafts || {};
    const draft = { ...(this._storyDrafts[chapter.chapter_id] || {}) };
    draft[field.dataset.storyField] = field.value;
    this._storyDrafts[chapter.chapter_id] = draft;
    // The save button's disabled state depends on this, so it is updated
    // directly rather than through a render that would move the caret.
    const save = this.shadowRoot?.querySelector('[data-action="story-save"]');
    if (save) save.disabled = !this._storyDirty(chapter);
  },

  async _storySave() {
    const chapter = this._storyChapter();
    if (!chapter || !this._storyDirty(chapter)) return;
    const draft = this._storyDraft(chapter);
    const changes = {};
    if (draft.title !== (chapter.title || "")) changes.title = draft.title;
    if (draft.story !== (chapter.story?.text || "")) changes.story = draft.story;
    await this._storyWrite(chapter.chapter_id, changes, "Kapitel gespeichert");
  },

  async _storyReset() {
    const chapter = this._storyChapter();
    if (!chapter) return;
    await this._storyWrite(
      chapter.chapter_id,
      { title: "", story: "" },
      "Kapitel auf die automatische Fassung zurückgesetzt",
    );
  },

  async _storyWrite(chapterId, changes, successMessage) {
    this._storySaving = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "story_set_override",
        {
          trip_id: this._selectedTripId,
          day_id: chapterId,
          changes,
          // The revision the browser last saw. If somebody else edited the
          // trip meanwhile, the server refuses rather than letting this
          // older state win.
          expected_revision: this._currentRevision(),
        },
        successMessage,
        { refresh: true, errorTitle: "Das Kapitel konnte nicht gespeichert werden" },
      );
      if (result?.story_manifest) {
        this._storyManifest = result.story_manifest;
        // The draft has served its purpose; the manifest is the truth now.
        if (this._storyDrafts) delete this._storyDrafts[chapterId];
      }
    } finally {
      this._storySaving = false;
      this._render({ preserveScroll: true });
    }
  },

  async _storySetChapterImage(mediaId) {
    const chapter = this._storyChapter();
    if (!chapter || !mediaId) return;
    // Roadplanner already has exactly this concept - "Titelbild dieses
    // Reisetags", which takes precedence over the automatic pick and is
    // unique per day. Inventing a second one would give the same photo two
    // meanings that could disagree.
    await this._runAction(
      "media_update_assignment",
      {
        trip_id: this._selectedTripId,
        media_id: mediaId,
        patch: { is_day_cover: true },
      },
      "Kapitelbild gesetzt",
      { refresh: true, errorTitle: "Das Kapitelbild konnte nicht gesetzt werden" },
    );
    await this._storyLoad({ force: true });
  },

  /**
   * The story director: the editorial pass, and what it cost.
   *
   * The status is read whenever the section opens and is deliberately
   * free - it compares two hashes and calls nobody. Only the button
   * spends anything, and the card says so before it is pressed rather
   * than afterwards.
   */
  async _storyDirectorStatus() {
    const result = await this._runAction(
      "story_director_status",
      { trip_id: this._selectedTripId },
      "",
      { refresh: false, blockUi: false, errorTitle: "" },
    );
    if (result?.story_director) {
      this._storyDirector = result.story_director;
      this._render({ preserveScroll: true });
    }
  },

  async _storyDirectorRun({ force = false } = {}) {
    if (this._storyDirecting) return;
    this._storyDirecting = true;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "story_director_run",
        { trip_id: this._selectedTripId, force },
        "",
        {
          refresh: false,
          blockUi: false,
          errorTitle: "Die Reiseredaktion ist fehlgeschlagen",
        },
      );
      // _runAction reports failure by returning null, and its toast is
      // gone in six seconds. A run that failed has to stay visible, or
      // the card is indistinguishable from one that was never pressed.
      this._storyDirectorFailed = !result?.story_director_run;
      if (result?.story_director_run) {
        const run = result.story_director_run;
        this._showToast(
          run.reused
            ? "Die vorhandene Fassung passt noch - es wurde nichts neu erzeugt."
            : `${run.directed_chapters} Kapitel redigiert (${run.calls} Gemini-Aufrufe).${
                run.chapters_without_edit
                  ? ` ${run.chapters_without_edit} Kapitel blieben bei der automatischen Fassung.`
                  : ""
              }`,
          "success",
        );
        await this._storyLoad({ force: true });
        await this._storyDirectorStatus();
      }
    } finally {
      this._storyDirecting = false;
      this._render({ preserveScroll: true });
    }
  },

  async _storyDirectorDiscard() {
    if (this._storyDirecting) return;
    this._storyDirecting = true;
    this._render({ preserveScroll: true });
    try {
      await this._runAction(
        "story_director_discard",
        { trip_id: this._selectedTripId },
        "Redaktion verworfen",
        { refresh: false, blockUi: false, errorTitle: "Die Redaktion konnte nicht verworfen werden" },
      );
      await this._storyLoad({ force: true });
      await this._storyDirectorStatus();
    } finally {
      this._storyDirecting = false;
      this._render({ preserveScroll: true });
    }
  },

  _renderStoryDirector(manifest) {
    const status = this._storyDirector;
    const canEdit = this._canEdit();
    const narrative = manifest?.narrative;
    if (status && !status.available) {
      return `<div class="notice neutral"><div>
        <strong>Reiseredaktion</strong>
        <small>Für die Redaktion ist kein Gemini-Zugang eingerichtet. Die Kapitel bleiben bei den automatisch zusammengesetzten Texten.</small>
      </div></div>`;
    }
    const busy = Boolean(this._storyDirecting);
    // "Up to date" is a real answer here, not a hedge: the stored pass
    // records which version of the trip it was written about, so the card
    // can promise that pressing the button again would cost money and
    // change nothing.
    const state = !status
      ? ""
      : status.current
        ? `<small>Die Redaktion ist auf dem Stand dieser Reise – ${escapeHtml(String(status.directed_chapters))} von ${escapeHtml(String(status.chapter_count))} Kapiteln, ${escapeHtml(String(status.calls))} Gemini-Aufrufe.${status.directed_chapters < status.chapter_count ? " Die übrigen blieben bei der automatischen Fassung." : ""}</small>`
        : status.has_direction
          ? "<small>Die Reise hat sich seit der letzten Redaktion verändert. Ein neuer Durchgang würde die Texte auffrischen.</small>"
          : "<small>Noch nicht redigiert. Ein Durchgang liest die ganze Reise, legt den Reisebogen fest und schreibt danach die Tageskapitel – rund fünf Gemini-Aufrufe für eine dreiwöchige Reise.</small>";
    return `<div class="notice neutral"><div>
      <strong>Reiseredaktion mit Gemini</strong>
      <small>Gemini arbeitet als Redakteur, nicht als Faktenquelle: Es formuliert aus den vorhandenen Daten und darf nichts hinzuerfinden. Von Hand geschriebene Kapitel bleiben unangetastet.</small>
      ${state}
      ${this._storyDirectorFailed ? '<small class="story-director-failed">Der letzte Durchgang ist fehlgeschlagen. Die vorhandenen Texte sind unverändert; die Fehlermeldung stand in der Einblendung.</small>' : ""}
      ${
        narrative
          ? `<small class="story-arc">${escapeHtml([narrative.subtitle, (narrative.motifs || []).join(" · ")].filter(Boolean).join(" — "))}</small>`
          : ""
      }
      ${
        canEdit
          ? `<div class="button-row">
        <button class="secondary-button" type="button" data-action="story-direct"${busy ? " disabled" : ""}><ha-icon icon="mdi:auto-awesome"></ha-icon> ${busy ? "Redigiert …" : status?.has_direction ? "Redaktion auffrischen" : "Reise redigieren lassen"}</button>
        ${status?.has_direction ? `<button class="text-button" type="button" data-action="story-direct-discard"${busy ? " disabled" : ""}><ha-icon icon="mdi:backup-restore"></ha-icon> Redaktion verwerfen</button>` : ""}
      </div>`
          : ""
      }
    </div></div>`;
  },

  /**
   * The film: the manifest's first consumer.
   *
   * Deliberately placed here rather than beside the renderer experiments -
   * this is what the story layer is FOR, and the preview shows how much of
   * the trip the film would actually be able to say.
   */
  async _storyFilmPreview() {
    const result = await this._runAction(
      "story_film_preview",
      { trip_id: this._selectedTripId },
      "",
      { refresh: false, blockUi: false, errorTitle: "Die Filmvorschau ist fehlgeschlagen" },
    ).catch(() => null);
    if (result?.story_film_preview) {
      this._storyFilm = result.story_film_preview;
      this._render({ preserveScroll: true });
    }
  },

  async _storyFilmRender() {
    const result = await this._runAction(
      "story_film_render",
      { trip_id: this._selectedTripId },
      "Reisefilm wird gerendert",
      { refresh: false, blockUi: false, errorTitle: "Der Reisefilm konnte nicht gestartet werden" },
    );
    if (!result?.renderer_app_job?.job_id) return;
    this._rendererAppKind = "trip_film";
    this._rendererAppPackage = {
      package_bytes: result.renderer_app_job.package_bytes,
      image_count: result.renderer_app_job.image_count,
      stop_count: result.renderer_app_job.chapter_count,
      day_title: `${result.renderer_app_job.chapter_count} Kapitel`,
      day_date: `${result.renderer_app_job.chapters_without_photos} ohne Fotos`,
    };
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    // A link to the previous video would be a lie about this one.
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  _renderStoryFilm() {
    const film = this._storyFilm;
    const canEdit = this._canEdit();
    // Three states, not two. "Nobody has asked yet" must not be reported
    // as "the app is down" - that claim disabled the film button on every
    // freshly loaded page while the app was running fine.
    const status = this._rendererAppStatus;
    const online = Boolean(status?.online);
    const job = this._rendererAppJob;
    const running = this._rendererAppKind === "trip_film" && job && !job.terminal && job.state;
    return `<div class="notice neutral"><div>
      <strong>Reisefilm aus dieser Geschichte</strong>
      <small>Ein Film über die ganze Reise, ein Kapitel je Tag, aus genau diesen Titeln, Texten und Bildern. Tage ohne Fotos werden als solche gezeigt und nicht übersprungen.</small>
      ${
        film
          ? `<small>${escapeHtml(String(film.chapter_count))} Kapitel · ${escapeHtml(String(film.planned_photo_count))} Bilder (bis ${escapeHtml(String(film.photos_per_chapter))} je Tag) · ${escapeHtml(String(film.chapters_without_photos))} Tage ohne Fotos</small>`
          : ""
      }
      ${
        online
          ? ""
          : status
            ? `<small>Die Renderer-App ist nicht erreichbar (${escapeHtml(String(status.reason || status.state || "kein Lebenszeichen"))}) - der Film braucht sie.</small>`
            : "<small>Der Zustand der Renderer-App ist noch nicht bekannt.</small>"
      }
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="story-film-preview"><ha-icon icon="mdi:filmstrip-box-multiple"></ha-icon> ${film ? "Vorschau aktualisieren" : "Was käme in den Film?"}</button>
        ${canEdit ? `<button class="secondary-button" type="button" data-action="story-film-render"${(online || !status) && !running ? "" : " disabled"}><ha-icon icon="mdi:movie-play-outline"></ha-icon> Reisefilm erzeugen</button>` : ""}
      </div>
      ${this._renderStoryFilmJobLine()}
    </div></div>`;
  },

  /**
   * What the film job is doing, said in the place the film was started.
   *
   * This exists because the previous answer was "look in the other card",
   * which stops being an answer the moment the page reloads and that card
   * has forgotten too. The job is read from the exchange folder, so this
   * line can appear on a page that never started anything.
   */
  _renderStoryFilmJobLine() {
    const job = this._rendererAppJob;
    if (!job || this._rendererAppKind !== "trip_film") return "";
    if (!job.terminal) {
      const percent = Math.round((Number(job.progress) || 0) * 100);
      return `<small class="story-film-job">Ein Reisefilm wird gerade gerendert (<span data-renderer-progress="story">${escapeHtml(String(job.state || "läuft"))} · ${percent} %</span>). Das dauert bei einer ganzen Reise viele Minuten – die Seite darf zwischendurch geschlossen werden.</small>`;
    }
    if (job.state === "completed") {
      return `<small class="story-film-job">Der zuletzt erzeugte Reisefilm ist fertig. Er liegt in der Karte „Renderer-App".</small>`;
    }
    return `<small class="story-film-job">Der zuletzt gestartete Reisefilm ist nicht fertig geworden (${escapeHtml(String(job.state || "unbekannt"))}).</small>`;
  },

  // --- rendering -------------------------------------------------------

  _storyMediaItems(chapter) {
    const byId = new Map(
      (this._experienceData()?.media || []).map((item) => [item.id, item]),
    );
    return (chapter.media || [])
      .map((entry) => ({ ...entry, media: byId.get(entry.media_id) }))
      .filter((entry) => entry.media);
  },

  _renderStoryChapterStrip(chapters, current) {
    return `<div class="story-strip" role="tablist" aria-label="Kapitel">
      ${chapters
        .map((chapter) => {
          const edited = chapter.story?.source === "override" || chapter.title_overridden;
          const dirty = this._storyDirty(chapter);
          return `<button type="button" role="tab" class="story-chip ${chapter.chapter_id === current.chapter_id ? "active" : ""}" data-action="story-select" data-chapter-id="${escapeHtml(chapter.chapter_id)}" aria-selected="${chapter.chapter_id === current.chapter_id}">
            <span>Tag ${escapeHtml(String(chapter.facts?.day_number || chapter.index + 1))}</span>
            ${dirty ? '<i class="story-dot unsaved" title="ungespeichert"></i>' : edited ? '<i class="story-dot" title="von Hand bearbeitet"></i>' : ""}
          </button>`;
        })
        .join("")}
    </div>`;
  },

  _renderStoryFacts(chapter) {
    const facts = chapter.facts || {};
    const entries = [
      facts.distance_km ? [`${Math.round(facts.distance_km)} km`, "mdi:road-variant"] : null,
      facts.duration_minutes ? [this._storyDuration(facts.duration_minutes), "mdi:clock-outline"] : null,
      facts.stop_count ? [`${facts.stop_count} Stopps`, "mdi:map-marker-outline"] : null,
      facts.photo_count ? [`${facts.photo_count} Fotos`, "mdi:image-multiple-outline"] : null,
    ].filter(Boolean);
    if (!entries.length) return "";
    return `<div class="story-facts">${entries
      .map(([label, icon]) => `<span><ha-icon icon="${icon}"></ha-icon>${escapeHtml(label)}</span>`)
      .join("")}</div>`;
  },

  _storyDuration(minutes) {
    const hours = Math.floor(minutes / 60);
    const rest = Math.round(minutes % 60);
    if (!hours) return `${rest} min`;
    return rest ? `${hours} h ${rest} min` : `${hours} h`;
  },

  _renderStoryMedia(chapter) {
    const items = this._storyMediaItems(chapter);
    if (!items.length) {
      return `<div class="story-empty-media"><ha-icon icon="mdi:image-off-outline"></ha-icon><span>Für diesen Tag sind keine Fotos zugeordnet.</span></div>`;
    }
    const [lead, ...rest] = items;
    const canEdit = this._canEdit();
    const thumb = (entry) => `<button type="button" class="story-thumb ${entry.role === "day_cover" ? "is-cover" : ""}" data-action="story-chapter-image" data-media-id="${escapeHtml(entry.media_id)}" ${canEdit ? "" : "disabled"} title="${canEdit ? "Als Kapitelbild verwenden" : "Foto"}">
      <img src="${escapeHtml(this._safeUrl(entry.media.thumbnail_url))}" alt="${escapeHtml(entry.media.caption || entry.media.name || "Reisefoto")}" loading="lazy" decoding="async">
      ${entry.role === "day_cover" ? '<span class="story-thumb-badge"><ha-icon icon="mdi:star"></ha-icon></span>' : ""}
    </button>`;
    return `<figure class="story-lead">
      <img src="${escapeHtml(this._safeUrl(lead.media.thumbnail_url))}" alt="${escapeHtml(lead.media.caption || lead.media.name || "Reisefoto")}" loading="lazy" decoding="async">
      ${lead.media.caption ? `<figcaption>${escapeHtml(lead.media.caption)}</figcaption>` : ""}
    </figure>
    ${rest.length ? `<div class="story-thumbs">${rest.map(thumb).join("")}</div>` : ""}
    ${canEdit && items.length > 1 ? '<p class="hint story-hint">Ein Tippen auf ein kleines Foto macht es zum Kapitelbild. Roadplanner nutzt dafür dasselbe Titelbild wie überall sonst.</p>' : ""}`;
  },

  _renderStory() {
    if (!this._selectedTripId) {
      return `<section class="panel-card"><p class="hint">Zuerst eine Reise auswählen.</p></section>`;
    }
    // A film started here can outlive the page. Asking once, on the way
    // in, is what makes it findable again after a reload.
    this._rendererAppAdoptOnce();
    const manifest = this._storyManifest;
    if (!manifest) {
      return `<section class="panel-card">
        <div class="section-heading compact"><div><span class="eyebrow">Redaktion</span><h2>Reisegeschichte</h2></div></div>
        <p class="hint">Die Kapitel entstehen aus dem Roadbook, den Tageszusammenfassungen und den zugeordneten Fotos. Bearbeitet werden nur Titel und Text – Stopps, Zeiten und Strecken bleiben unberührt.</p>
        ${this._renderStoryFilmJobLine()}
        <div class="button-row"><button class="primary-button" type="button" data-action="story-load"${this._storyLoading ? " disabled" : ""}><ha-icon icon="mdi:book-open-page-variant-outline"></ha-icon> ${this._storyLoading ? "Lädt …" : "Reisegeschichte öffnen"}</button></div>
      </section>`;
    }
    const chapters = this._storyChapters();
    if (!chapters.length) {
      return `<section class="panel-card"><p class="hint">Diese Reise hat noch keine Tage.</p></section>`;
    }
    const chapter = this._storyChapter();
    const draft = this._storyDraft(chapter);
    const dirty = this._storyDirty(chapter);
    const canEdit = this._canEdit();
    const sources = manifest.story_sources || {};
    const edited = Number(sources.override || 0);
    const source = chapter.story?.source || "composed";
    const position = chapters.findIndex((item) => item.chapter_id === chapter.chapter_id);

    return `<section class="panel-card story-card">
      <div class="section-heading compact">
        <div><span class="eyebrow">Redaktion</span><h2>Reisegeschichte</h2></div>
        <button class="text-button" type="button" data-action="story-reload"${this._storyLoading ? " disabled" : ""}><ha-icon icon="mdi:refresh"></ha-icon> Neu aufbauen</button>
      </div>
      <div class="story-overview">
        <span><strong>${escapeHtml(String(chapters.length))}</strong> Kapitel</span>
        <span><strong>${escapeHtml(String(edited))}</strong> von Hand bearbeitet</span>
        <span><strong>${escapeHtml(String(Number(sources.directed || 0)))}</strong> von Gemini redigiert</span>
        <span><strong>${escapeHtml(String(Number(sources.stored || 0)))}</strong> aus Zusammenfassungen</span>
      </div>
      ${this._renderStoryDirector(manifest)}
      ${this._renderStoryFilm()}
      ${this._renderStoryChapterStrip(chapters, chapter)}

      <article class="story-chapter">
        <div class="story-chapter-head">
          <button class="icon-button" type="button" data-action="story-prev" ${position <= 0 ? "disabled" : ""} aria-label="Vorheriges Kapitel"><ha-icon icon="mdi:chevron-left"></ha-icon></button>
          <div>
            <span class="eyebrow">${escapeHtml([chapter.date, `Tag ${chapter.facts?.day_number || position + 1} von ${chapters.length}`].filter(Boolean).join(" · "))}</span>
            <span class="story-source ${escapeHtml(source)}"><ha-icon icon="${SOURCE_ICONS[source] || SOURCE_ICONS.composed}"></ha-icon>${escapeHtml(SOURCE_LABELS[source] || source)}</span>
            ${chapter.importance && chapter.importance !== "normal" ? `<span class="story-weight">${escapeHtml(IMPORTANCE_LABELS[chapter.importance] || chapter.importance)}</span>` : ""}
          </div>
          <button class="icon-button" type="button" data-action="story-next" ${position >= chapters.length - 1 ? "disabled" : ""} aria-label="Nächstes Kapitel"><ha-icon icon="mdi:chevron-right"></ha-icon></button>
        </div>

        <label class="story-title-field">
          <span>Kapiteltitel</span>
          <input type="text" data-story-field="title" maxlength="120" value="${escapeHtml(draft.title)}" placeholder="Wie soll dieser Tag heißen?" ${canEdit ? "" : "readonly"}>
        </label>

        ${this._renderStoryMedia(chapter)}
        ${this._renderStoryFacts(chapter)}

        <label class="story-text-field">
          <span>Story</span>
          <textarea data-story-field="story" rows="7" maxlength="1200" placeholder="Was war an diesem Tag los?" ${canEdit ? "" : "readonly"}>${escapeHtml(draft.story)}</textarea>
        </label>

        ${chapter.video_caption ? `<p class="story-caption"><span>Im Film</span>${escapeHtml(chapter.video_caption)}</p>` : ""}

        ${canEdit ? `<div class="button-row story-actions">
          <button class="primary-button" type="button" data-action="story-save"${dirty && !this._storySaving ? "" : " disabled"}><ha-icon icon="mdi:content-save-outline"></ha-icon> ${this._storySaving ? "Speichert …" : "Speichern"}</button>
          ${source === "override" || chapter.title_overridden ? `<button class="secondary-button" type="button" data-action="story-reset"${this._storySaving ? " disabled" : ""}><ha-icon icon="mdi:backup-restore"></ha-icon> Auf automatische Fassung zurücksetzen</button>` : ""}
          ${dirty ? '<span class="story-dirty-hint">Ungespeicherte Änderung</span>' : ""}
        </div>` : '<p class="hint">Zum Bearbeiten fehlen die Rechte.</p>'}
      </article>
    </section>`;
  },
};
