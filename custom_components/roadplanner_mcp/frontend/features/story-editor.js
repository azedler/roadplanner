import { actionButton } from "../lib/action-button.js";
import { cleanText, escapeHtml } from "../lib/core-helpers.js";

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

  /**
   * Load the story when the tab is opened, rather than asking first.
   *
   * There used to be a button, and the reason for it has gone. Building
   * a manifest calls no model and costs nothing: it is a read of the
   * roadbook, cached against the revision. So "Reisegeschichte öffnen"
   * asked the reader to confirm the very thing they had already asked
   * for by opening the tab - a click whose only possible answer was yes.
   *
   * Once per trip, and free after that, in the same shape as
   * `_rendererAppAdoptOnce`: called from render, never making the render
   * wait on it.
   */
  _storyLoadOnce() {
    if (this._storyLoadTriedFor === this._selectedTripId) return;
    this._storyLoadTriedFor = this._selectedTripId;
    // Quiet: this is the tab loading itself, not the reader asking for
    // something. A failure becomes the card's own state below.
    // Deferred by a microtask, not called straight away. `_storyLoad`
    // renders before its first await, and this runs FROM a render - so a
    // direct call would rebuild the page in the middle of building it.
    void Promise.resolve().then(() => this._storyLoad({ quiet: true }));
  },

  /**
   * Forget the story when the trip changes.
   *
   * Nothing did this, so switching trips left the previous trip's
   * chapters on screen under the new trip's name - and its drafts in the
   * editor, one save away from being written to a day that belongs to
   * somebody else's journey.
   */
  _storyResetForTrip() {
    this._storyManifest = null;
    this._storyChapterId = "";
    this._storyDrafts = {};
    this._storyDirector = null;
    this._storyLoadTriedFor = null;
    this._storyLoadFailed = false;
  },

  async _storyLoad({ force = false, quiet = false } = {}) {
    if (this._storyLoading) return;
    this._storyLoading = true;
    this._storyLoadFailed = false;
    this._render({ preserveScroll: true });
    try {
      const result = await this._runAction(
        "story_manifest",
        { trip_id: this._selectedTripId, force },
        "",
        {
          refresh: false,
          blockUi: false,
          errorMode: quiet ? "silent" : "toast",
          errorTitle: "Die Reisegeschichte konnte nicht geladen werden",
        },
      );
      if (result?.story_manifest) {
        this._storyManifest = result.story_manifest;
        if (!this._storyChapterId) {
          this._storyChapterId = this._storyChapters()[0]?.chapter_id || "";
        }
      } else {
        // `_runAction` never throws - it returns null and shows a toast.
        // Remembered here because the automatic attempt happens once, so
        // without this the card would have no way back from a failure.
        this._storyLoadFailed = true;
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
      { refresh: false, blockUi: false, errorMode: "silent", errorTitle: "" },
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
        ${actionButton(
          this._actionCosts(),
          "story-direct",
          status?.has_direction ? "Reise neu schreiben lassen" : "Reise redigieren lassen",
          { busy, busyLabel: "Redigiert …" },
        )}
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
      { trip_id: this._selectedTripId, music: this._storyFilmTrack || "" },
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
          ? `<small>${escapeHtml(String(film.chapter_count))} Kapitel · ${escapeHtml(String(film.planned_photo_count))} Bilder (bis ${escapeHtml(String(film.photos_per_chapter))} je Tag) · ${escapeHtml(String(film.chapters_without_photos))} Tage ohne Fotos</small>
             ${
               // Whether the film gets a map is a property of the trip's
               // stored routes, and it used to be invisible until the
               // film came back without one - with no way to tell the
               // data apart from the version that rendered it.
               Number(film.mapped_chapters || 0) > 0
                 ? `<small>Karte: ${escapeHtml(String(film.mapped_chapters))} von ${escapeHtml(String(film.chapter_count))} Tagen${film.map_has_ferry ? " · mit Fährstrecke" : ""}${Number(film.estimated_map_chapters || 0) > 0 ? ` · ${escapeHtml(String(film.estimated_map_chapters))} davon nur als Luftlinie` : ""}</small>`
                 : `<small>Karte: keine. Für diese Reise sind weder berechnete Routen noch Koordinaten an den Stopps gespeichert – der Film läuft ohne Kartenszenen.</small>`
             }`
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
        ${this._renderStoryFilmMusic()}
        ${canEdit ? `<button class="secondary-button" type="button" data-action="story-film-render"${(online || !status) && !running ? "" : " disabled"}><ha-icon icon="mdi:movie-play-outline"></ha-icon> Reisefilm erzeugen</button>` : ""}
      </div>
      ${this._renderStoryFilmMusicPlan()}
      ${this._renderStoryFilmJobLine()}
      ${this._renderStoryTripDiagnosis()}
    </div></div>
    ${this._renderCharacterAssets()}`;
  },

  /**
   * What the film job is doing, said in the place the film was started.
   *
   * This exists because the previous answer was "look in the other card",
   * which stops being an answer the moment the page reloads and that card
   * has forgotten too. The job is read from the exchange folder, so this
   * line can appear on a page that never started anything.
   */
  /**
   * Which track plays under the film, chosen by name.
   *
   * A name, never a path: the backend matches it against the folder
   * listing before it opens anything, so nothing the browser sends can
   * become a file location. "Ohne Musik" is a first-class choice and the
   * default - a film with no soundtrack is a complete film.
   */
  _renderStoryFilmMusic() {
    const tracks = this._storyFilmMusic;
    // A reserved NAME, handled on the other side before the folder is
    // ever consulted - the film plays the sections that were generated
    // for it, and cannot cause any to be generated.
    const generated = this._storyFilmMusicOfferData;
    const hasGenerated = Boolean(generated && generated.sections && generated.cached);
    if (!Array.isArray(tracks)) {
      return `<button class="text-button" type="button" data-action="story-film-music"><ha-icon icon="mdi:music-note-outline"></ha-icon> Musik wählen</button>`;
    }
    if (!tracks.length && !hasGenerated) {
      return `<small class="hint">Keine Musik gefunden. Lege Audiodateien in <code>/media/roadplanner_music</code> ab – der Film läuft auch ohne.</small>`;
    }
    const chosen = this._storyFilmTrack || "";
    return `<label class="inline-select"><span>Musik</span><select data-action="story-film-track">
      <option value=""${chosen ? "" : " selected"}>Ohne Musik</option>
      ${
        hasGenerated
          ? `<option value="__generated__"${chosen === "__generated__" ? " selected" : ""}>KI-Musik (${escapeHtml(String(generated.cached))} Abschnitte)</option>`
          : ""
      }
      ${tracks
        .map(
          (track) =>
            `<option value="${escapeHtml(track.name)}"${chosen === track.name ? " selected" : ""}>${escapeHtml(track.name)}</option>`,
        )
        .join("")}
    </select></label>`;
  },

  async _storyFilmMusicLoad() {
    const result = await this._runAction("story_film_music", {}, "", {
      refresh: false,
      blockUi: false,
      errorMode: "dialog",
      errorTitle: "Die Musikauswahl konnte nicht geladen werden",
    });
    if (!result) return;
    this._storyFilmMusic = result.film_music || [];
    this._render({ preserveScroll: true });
  },

  /**
   * What generated music would cost, before anybody agrees to it.
   *
   * Free and read-only: it reads the manifest, times the film with the
   * same planner the render uses, and looks in the music folder for
   * sections already paid for. Asking somebody to approve a cost
   * without naming one is not asking, so the button that spends money
   * does not appear until this has.
   */
  async _storyFilmMusicOffer() {
    const result = await this._runAction(
      "story_film_music_offer",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Das Musikangebot konnte nicht berechnet werden",
      },
    );
    if (!result?.film_music_offer) return;
    this._storyFilmMusicOfferData = result.film_music_offer;
    this._render({ preserveScroll: true });
  },

  /** The one place in the story editor that spends money. */
  async _storyFilmMusicGenerate() {
    const offer = this._storyFilmMusicOfferData;
    if (!offer || !offer.new_generations) return;
    const result = await this._runAction(
      "story_film_music_generate",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Musik konnte nicht erzeugt werden",
      },
    );
    if (!result?.film_music_generated) return;
    const made = result.film_music_generated;
    this._showToast(
      `${made.generated} Abschnitt${made.generated === 1 ? "" : "e"} erzeugt` +
        (made.reused ? ` · ${made.reused} wiederverwendet` : ""),
      "success",
      7000,
    );
    // The folder has changed, so both the offer and the track list are
    // now stale in the same way.
    await this._storyFilmMusicLoad();
    await this._storyFilmMusicOffer();
  },

  /**
   * The soundtrack this film would get, and what it costs.
   *
   * Deliberately shows the sections rather than a single price: "vier
   * Abschnitte, drei schon da, einer neu" is a different decision from
   * "vier neu", and only one of them is worth pressing.
   */
  _renderStoryFilmMusicPlan() {
    const offer = this._storyFilmMusicOfferData;
    const canEdit = this._canEdit();
    if (!offer) {
      return canEdit
        ? `<div class="story-music-plan"><button class="text-button" type="button" data-action="story-film-music-offer"><ha-icon icon="mdi:auto-awesome"></ha-icon> KI-Musik: was würde sie kosten?</button></div>`
        : "";
    }
    if (!offer.sections) {
      return `<div class="story-music-plan"><small class="hint">Für eine Musikplanung fehlt die Länge des Films. Erzeuge zuerst die Vorschau.</small></div>`;
    }
    const sections = offer.section_state || [];
    const minutes = Math.round(Number(offer.seconds || 0) / 60);
    const chips = sections
      .map(
        (entry) =>
          `<span class="story-motif ${entry.cached_name ? "met" : "unmet"}"><ha-icon icon="${entry.cached_name ? "mdi:check-circle-outline" : "mdi:music-note-plus"}"></ha-icon>${escapeHtml(String(entry.label || entry.section || ""))} · ${escapeHtml(String(Math.round(Number(entry.seconds || 0))))}s</span>`,
      )
      .join("");
    const price = offer.reused
      ? "Alle Abschnitte sind schon erzeugt – ein weiterer Lauf kostet nichts."
      : `${offer.new_generations} von ${offer.sections} Abschnitten sind neu · geschätzt ${escapeHtml(String(offer.estimated_cost))} ${escapeHtml(String(offer.currency || "USD"))}`;
    const button =
      canEdit && offer.available && offer.new_generations
        ? actionButton(this._actionCosts(), "story-film-music-generate", `Musik erzeugen (${offer.estimated_cost} ${offer.currency || "USD"})`)
        : "";
    return `<div class="story-music-plan">
      <div class="story-curation-head"><span class="eyebrow">KI-Musik</span>${button}</div>
      <p class="story-curation-counts">Ein Soundtrack in ${escapeHtml(String(offer.sections))} Abschnitten für rund ${escapeHtml(String(minutes))} Minuten Film.</p>
      <div class="story-motifs">${chips}</div>
      <p class="hint">${escapeHtml(price)}</p>
      ${offer.available ? "" : `<p class="hint">Dafür ist kein Google-Schlüssel konfiguriert – der Film läuft ohne oder mit einem eigenen Titel.</p>`}
      ${offer.price_note ? `<small class="hint">${escapeHtml(String(offer.price_note))}</small>` : ""}
    </div>`;
  },

  _renderStoryFilmJobLine() {
    const job = this._rendererAppJob;
    if (!job || this._rendererAppKind !== "trip_film") return "";
    if (!job.terminal) {
      const percent = Math.round((Number(job.progress) || 0) * 100);
      return `<small class="story-film-job">Ein Reisefilm wird gerade gerendert (<span data-renderer-progress="story">${escapeHtml(String(job.state || "läuft"))} · ${percent} %</span>). Das dauert bei einer ganzen Reise viele Minuten – die Seite darf zwischendurch geschlossen werden.</small>`;
    }
    if (job.state === "completed") {
      // "It is in the other card" is a signpost, not an answer - and on a
      // phone that other card is behind a menu, two taps and a scroll.
      // The film was started here; it can be fetched here.
      // When it was made, not just that it was. A film that took a
      // quarter of an hour is easy to mistake for the one from
      // yesterday - which is exactly what happened when the map arrived
      // and an older film was downloaded to look for it.
      const made = job.updated_at ? this._formatTimestamp(job.updated_at) : "";
      return `<small class="story-film-job">Der zuletzt erzeugte Reisefilm ist fertig${made ? ` – erstellt am ${escapeHtml(made)}` : ""}.</small>
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="renderer-app-download"${this._rendererAppDownloading ? " disabled" : ""}><ha-icon icon="mdi:download"></ha-icon> ${this._rendererAppDownloading ? "Wird bereitgestellt …" : "Film herunterladen"}</button>
      </div>
      ${
        this._rendererAppDownloadUrl
          ? `<a class="renderer-app-download" href="${escapeHtml(this._rendererAppDownloadUrl)}" download>Bereit – hier speichern</a>`
          : ""
      }`;
    }
    // The renderer says WHY it refused, and saying only "failed" throws
    // that away. A package the installed add-on is too old to read looks
    // exactly like a crash until the reason is printed.
    const why = cleanText(job.reason || job.detail || "");
    return `<small class="story-film-job">Der zuletzt gestartete Reisefilm ist nicht fertig geworden (${escapeHtml(String(job.state || "unbekannt"))}).${why ? ` ${escapeHtml(why)}` : ""}</small>`;
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

  /** What the curation decided for this day, and how to overrule it. */
  _storyCuration(chapter) {
    const all = this._experienceData()?.day_curations || {};
    return all[chapter?.chapter_id] || null;
  },

  /** Curate the trip a few days at a time, because it takes minutes.
   *
   * Looking at a three-week trip is roughly one model call per day. Done
   * in one action that is long enough for the websocket to give up, and
   * the person is told "Connection lost" while the work carries on
   * behind them - which is exactly what happened on the first real run.
   *
   * So the backend does a few paid days per call and says how many are
   * left, and this loops until nothing is. A resumed run is cheap: days
   * already answered come from the cache and do not use up a batch.
   */
  async _storyCurate({ force = false } = {}) {
    let totals = null;
    let freshAfter = null;
    for (let round = 0; round < 40; round += 1) {
      const result = await this._runAction(
        "media_curate_days",
        // `force` on EVERY round, not only the first - round 0 alone left
        // days beyond the first batch pinned to their (even empty) cache.
        // But force alone re-paid days 1-4 in every round and never
        // reached day 5, so the backend answers round 0 with its own
        // start time (`run_marker`) and later rounds send it back: a day
        // whose record is younger than the marker is this run's own
        // finished work and is not forced again.
        {
          trip_id: this._selectedTripId,
          force,
          max_days: 4,
          ...(freshAfter ? { fresh_after: freshAfter } : {}),
        },
        "",
        {
          refresh: false,
          errorMode: "dialog",
          errorTitle: "Die Bildauswahl konnte nicht neu bestimmt werden",
        },
      );
      if (!result) return;
      if (!freshAfter && result.run_marker) freshAfter = String(result.run_marker);
      totals = result;
      const left = Number(result.remaining || 0);
      if (!left) break;
      this._showToast(`Bildauswahl läuft – noch ${left} Tage …`, "info", 4000);
    }
    if (!totals) return;
    const missing = Object.keys(totals.unmet || {}).length;
    this._showToast(
      `${totals.selected_count} Bilder aus ${totals.pool_count} Kandidaten gewählt` +
        (missing ? ` · ${missing} Tage ohne ihr Hauptmotiv` : ""),
      "success",
      7000,
    );
    await this._loadData({ silent: true, force: true });
    await this._storyLoad({ force: true, quiet: true });
  },

  /** Show it, keep it out, or hand it back to the curation. */
  async _storyPin(mediaId, pin) {
    if (!mediaId) return;
    const result = await this._runAction(
      "media_set_film_pin",
      { trip_id: this._selectedTripId, media_id: mediaId, pin },
      "",
      { refresh: false, errorMode: "toast" },
    );
    if (!result) return;
    await this._loadData({ silent: true, force: true });
    await this._storyLoad({ force: true, quiet: true });
  },

  /**
   * Why an important place is barely in the film.
   *
   * Free and read-only. It exists because that one symptom has at least
   * four causes needing opposite fixes - never accepted, never in the
   * pool, never connected to the motif, or curated and then dropped by
   * the scene plan - and choosing between them by eye is how a special
   * case for a single place gets written.
   */
  async _storyDiagnose(chapterId) {
    const result = await this._runAction(
      "media_diagnose_day",
      { trip_id: this._selectedTripId, day_id: chapterId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Diagnose konnte nicht erstellt werden",
      },
    );
    if (!result?.day_diagnosis) return;
    this._storyDiagnosis = { ...result.day_diagnosis, chapter_id: chapterId };
    this._render({ preserveScroll: true });
  },

  _renderStoryDiagnosis(chapter) {
    const found = this._storyDiagnosis;
    if (!found || found.chapter_id !== chapter.chapter_id) return "";
    const stages = found.stages || {};
    const row = (label, value) =>
      `<li><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? 0))}</strong></li>`;
    const motif = (entry) =>
      `<li><span>${escapeHtml(String(entry.label || entry.motif))}</span><strong>${escapeHtml(String(entry.in_pool))} im Pool · ${escapeHtml(String(entry.in_selection))} gewählt · ${escapeHtml(String(entry.in_film))} im Film</strong></li>`;
    return `<div class="story-diagnosis">
      <p class="story-curation-counts"><strong>Diagnose:</strong> ${escapeHtml(String(found.detail || ""))}</p>
      <ul class="story-diagnosis-list">
        ${row("Medien am Tag", stages.media_total)}
        ${row("technisch akzeptiert", stages.technically_accepted)}
        ${row("abgelehnt", stages.rejected)}
        ${row("Seriengruppen", stages.series_count)}
        ${row("Kandidatenpool", stages.pool_size)}
        ${row("analysiert", stages.analysed)}
        ${row("kuratiert", stages.selected)}
        ${row("im Film", stages.in_film)}
      </ul>
      ${
        (found.motifs || []).length
          ? `<ul class="story-diagnosis-list">${found.motifs.map(motif).join("")}</ul>`
          : `<p class="hint">Für diesen Tag ist kein Pflichtmotiv abgeleitet worden.</p>`
      }
      <button class="text-button" type="button" data-action="story-diagnose-close">Schließen</button>
    </div>`;
  },

  /**
   * The same counting for every day, in one answer.
   *
   * One day tells you about one day. The question actually being asked -
   * "is an important place under-represented, and where does that go
   * wrong?" - is about a pattern, and it also means nobody has to guess
   * which day to look at first.
   */
  async _storyDiagnoseTrip() {
    const result = await this._runAction(
      "media_diagnose_trip",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Diagnose konnte nicht erstellt werden",
      },
    );
    if (!result?.trip_diagnosis) return;
    this._storyTripDiagnosis = result.trip_diagnosis;
    this._render({ preserveScroll: true });
  },

  /** The whole report as plain text, because that is what gets sent on. */
  _storyDiagnosisText() {
    const found = this._storyTripDiagnosis;
    if (!found) return "";
    const lines = [
      `Reisediagnose: ${found.day_count} Tage, ${found.weak_day_count} auffällig`,
      Object.entries(found.by_verdict || {})
        .map(([verdict, count]) => `${verdict}=${count}`)
        .join(" · "),
      "",
    ];
    for (const day of found.days || []) {
      const stages = day.stages || {};
      lines.push(
        `Tag ${day.day_number} · ${day.title || day.day_id} · ${day.importance} · ${day.verdict}`,
      );
      lines.push(
        `  Medien ${stages.media_total} → akzeptiert ${stages.technically_accepted}` +
          ` → Pool ${stages.pool_size} → analysiert ${stages.analysed}` +
          ` → kuratiert ${stages.selected} → im Film ${stages.in_film}` +
          ` · Serien ${stages.series_count} · abgelehnt ${stages.rejected}`,
      );
      // What the curation said about its own last look. Without it a day
      // reading "analysiert 0" gives no way to tell "the model is off"
      // from "the daily limit ran out" from "no thumbnail was reachable",
      // and those need opposite fixes.
      if (day.analysis_note) lines.push(`  Hinweis der Bildauswahl: ${day.analysis_note}`);
      for (const motif of day.motifs || []) {
        lines.push(
          `  Motiv "${motif.label}": ${motif.in_pool} im Pool, ${motif.in_selection} gewählt, ${motif.in_film} im Film`,
        );
      }
      if (day.verdict !== "ok") lines.push(`  → ${day.detail}`);
      lines.push("");
    }
    return lines.join("\n");
  },

  _renderStoryTripDiagnosis() {
    const found = this._storyTripDiagnosis;
    const canEdit = this._canEdit();
    if (!found) {
      return canEdit
        ? `<button class="text-button" type="button" data-action="story-diagnose-trip"><ha-icon icon="mdi:stethoscope"></ha-icon>Ganze Reise prüfen: wo gehen Bilder verloren?</button>`
        : "";
    }
    const weak = (found.days || []).filter((day) => day.verdict !== "ok");
    const row = (day) =>
      `<li><span>Tag ${escapeHtml(String(day.day_number))} · ${escapeHtml(String(day.title || day.day_id))}</span><strong>${escapeHtml(String(day.verdict))}</strong></li>`;
    return `<div class="story-diagnosis">
      <p class="story-curation-counts"><strong>${escapeHtml(String(found.day_count))} Tage geprüft · ${escapeHtml(String(found.weak_day_count))} auffällig</strong></p>
      ${
        weak.length
          ? `<ul class="story-diagnosis-list">${weak.map(row).join("")}</ul>
             <p class="hint">${escapeHtml(String(weak[0].detail || ""))}</p>`
          : `<p class="hint">Auf jedem Tag ist jedes Pflichtmotiv angemessen vertreten.</p>`
      }
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="story-diagnose-copy"><ha-icon icon="mdi:content-copy"></ha-icon>Bericht kopieren</button>
        <button class="text-button" type="button" data-action="story-diagnose-trip-close">Schließen</button>
      </div>
    </div>`;
  },

  _renderStoryCuration(chapter) {
    const curation = this._storyCuration(chapter);
    const canEdit = this._canEdit();
    const curateButton = canEdit
      ? actionButton(
          this._actionCosts(),
          "media-curate-days",
          curation ? "Bildauswahl erneuern" : "Bilder auswählen lassen",
          // "erneuern" only means something if it is actually forced: the
          // dispatcher reads `data-force="1"` off THIS element, and no
          // button ever set it, so every press ran without force and any
          // day whose pool had not changed since its last look came back
          // from the cache with "analysiert 0" - "force" was never true,
          // no matter how many times it was pressed.
          { extra: curation ? 'data-force="1"' : "" },
        )
      : "";
    if (!curation) {
      return `<div class="story-curation"><div class="story-curation-head"><span class="eyebrow">Bildauswahl</span>${curateButton}</div><p class="hint">Für diesen Tag hat noch niemand entschieden, welche Fotos ihn erzählen. Bis dahin nimmt der Film die lokal bestbewerteten.</p></div>`;
    }
    const must = curation.must_cover || (curation.brief || {}).must_cover || [];
    const missing = (curation.coverage || {}).unmet || [];
    const covered = (curation.coverage || {}).met || [];
    // The token is what matches; the label is what a person reads.
    // "smalandet" is a database key wearing a chip.
    const labels = (curation.brief || {}).labels || curation.labels || {};
    const motifChip = (name, met) =>
      `<span class="story-motif ${met ? "met" : "unmet"}" title="${escapeHtml(name)}"><ha-icon icon="${met ? "mdi:check-circle-outline" : "mdi:alert-circle-outline"}"></ha-icon>${escapeHtml(labels[name] || name)}</span>`;
    const motifs = must.length
      ? `<div class="story-motifs">${covered.map((name) => motifChip(name, true)).join("")}${missing.map((name) => motifChip(name, false)).join("")}</div>`
      : "";
    const selected = curation.media_ids || [];
    const reasons = curation.reasons || {};
    const byId = new Map((this._experienceData()?.media || []).map((item) => [item.id, item]));
    // Only the chosen pictures are rendered by default. Eighty thumbnails
    // on a phone is not an overview, it is a download.
    const tile = (mediaId, chosen) => {
      const media = byId.get(mediaId);
      if (!media) return "";
      const pin = media.film_pin || "";
      return `<figure class="story-pick ${chosen ? "" : "spare"} ${pin ? `pin-${escapeHtml(pin)}` : ""}">
        <img src="${escapeHtml(this._safeUrl(media.thumbnail_url))}" alt="${escapeHtml(media.caption || media.name || "Reisefoto")}" loading="lazy" decoding="async">
        ${chosen && reasons[mediaId] ? `<figcaption>${escapeHtml(reasons[mediaId])}</figcaption>` : ""}
        ${canEdit ? `<div class="story-pick-actions">
          <button class="icon-button" type="button" data-action="story-pin" data-media-id="${escapeHtml(mediaId)}" data-pin="${pin === "show" ? "" : "show"}" title="${pin === "show" ? "Feste Auswahl aufheben" : "Im Film zeigen"}"><ha-icon icon="${pin === "show" ? "mdi:pin" : "mdi:pin-outline"}"></ha-icon></button>
          <button class="icon-button" type="button" data-action="story-pin" data-media-id="${escapeHtml(mediaId)}" data-pin="${pin === "hero" ? "" : "hero"}" title="Als Kapitelbild"><ha-icon icon="${pin === "hero" ? "mdi:star" : "mdi:star-outline"}"></ha-icon></button>
          <button class="icon-button" type="button" data-action="story-pin" data-media-id="${escapeHtml(mediaId)}" data-pin="${pin === "exclude" ? "" : "exclude"}" title="${pin === "exclude" ? "Wieder zulassen" : "Nicht verwenden"}"><ha-icon icon="${pin === "exclude" ? "mdi:eye-off" : "mdi:eye-off-outline"}"></ha-icon></button>
        </div>` : ""}
      </figure>`;
    };
    const spares = (curation.pool_media_ids || []).filter((mediaId) => !selected.includes(mediaId));
    const open = this._storySparesOpen === chapter.chapter_id;
    return `<div class="story-curation">
      <div class="story-curation-head"><span class="eyebrow">Bildauswahl</span><span>${canEdit ? `<button class="text-button" type="button" data-action="story-diagnose" data-chapter-id="${escapeHtml(chapter.chapter_id)}" title="Kostenlos. Zählt die ganze Kette und sagt, an welcher Stufe Bilder verloren gehen."><ha-icon icon="mdi:stethoscope"></ha-icon>Warum fehlt etwas?</button>` : ""}${curateButton}</span></div>
      ${this._renderStoryDiagnosis(chapter)}
      <p class="story-curation-counts"><strong>${escapeHtml(String(selected.length))} von ${escapeHtml(String(curation.photo_count || 0))}</strong> Fotos ausgewählt · ${escapeHtml(String(curation.pool_size || 0))} Kandidaten · ${escapeHtml(String(curation.series_count || 0))} Momente${curation.note ? ` · ${escapeHtml(curation.note)}` : ""}</p>
      ${motifs}
      ${missing.length ? `<p class="hint story-missing">Kein Bild zeigt: ${escapeHtml(missing.join(", "))}. Falls doch eines existiert, kannst du es unten fest auswählen.</p>` : ""}
      <div class="story-picks">${selected.map((mediaId) => tile(mediaId, true)).join("")}</div>
      ${spares.length ? `<button class="text-button" type="button" data-action="story-spares" data-chapter-id="${escapeHtml(chapter.chapter_id)}"><ha-icon icon="${open ? "mdi:chevron-up" : "mdi:chevron-down"}"></ha-icon>Weitere Fotos (${escapeHtml(String(spares.length))})</button>` : ""}
      ${open ? `<div class="story-picks spares">${spares.map((mediaId) => tile(mediaId, false)).join("")}</div>` : ""}
    </div>`;
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
    // Opening the tab IS the request. See _storyLoadOnce.
    this._storyLoadOnce();
    const manifest = this._storyManifest;
    if (!manifest) {
      return `<section class="panel-card">
        <div class="section-heading compact"><div><span class="eyebrow">Redaktion</span><h2>Reisegeschichte</h2></div></div>
        <p class="hint">Die Kapitel entstehen aus dem Roadbook, den Tageszusammenfassungen und den zugeordneten Fotos. Bearbeitet werden nur Titel und Text – Stopps, Zeiten und Strecken bleiben unberührt.</p>
        ${
          this._storyLoadFailed
            ? `<p class="hint">Die Reisegeschichte ließ sich gerade nicht laden – oft ist die Verbindung nur kurz weg gewesen. Der Reise ist dabei nichts passiert: gelesen wird hier nur.</p>
               <div class="button-row"><button class="primary-button" type="button" data-action="story-load"${this._storyLoading ? " disabled" : ""}><ha-icon icon="mdi:refresh"></ha-icon> ${this._storyLoading ? "Lädt …" : "Erneut versuchen"}</button></div>`
            : `<p class="hint"><ha-icon icon="mdi:book-open-page-variant-outline"></ha-icon> Die Reisegeschichte wird geladen …</p>`
        }
        ${
          // Below the rest, not above it: on the closed card this block
          // is a footnote about the last film, and putting it first
          // pushed the thing the card exists for underneath a download
          // link (live screenshot).
          this._renderStoryFilmJobLine()
        }
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
        ${this._renderStoryCuration(chapter)}
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
