import { actionButton } from "../lib/action-button.js";
import { cleanText, escapeHtml } from "../lib/core-helpers.js";

/**
 * The reserved name that means "the score generated for this film".
 *
 * A reserved NAME, not a path: the backend recognises it before it ever
 * looks in the music folder, so it can never resolve to a file. Spelled
 * once here rather than typed into every template - it must match
 * `GENERATED_MUSIC` in trip_film_export.py exactly, and a second literal
 * is how those two drift apart.
 */
const GENERATED_MUSIC = "__generated__";

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
    // The film block is trip state too. The offer prices ONE trip's
    // sections; carried across a switch it gated the next trip's render
    // with the previous trip's answer - skipping the price dialog, or
    // asking money for music that exists (found in review). The armed
    // mux intent deliberately survives: it carries its own trip and job.
    this._storyFilm = null;
    this._storyFilmTrack = "";
    this._storyFilmMusicOfferData = null;
    this._storyLatestFilm = undefined;
    this._storyLatestFilmAsked = false;
    // The renderer block is trip state too, and it did NOT reset: the
    // adopted job, the film source and the mix jobs of the previous
    // trip survived the switch, so its render was shown - and muxable,
    // and copyable - under the next trip (audited live). Cleared here,
    // and the adoption flag reset so opening the story tab adopts anew
    // for the trip that is actually on screen.
    this._rendererAppJob = null;
    this._rendererAppKind = "";
    this._rendererAppResult = null;
    this._rendererAppRecent = [];
    this._rendererAppRecentAsked = false;
    this._rendererAppAdoptTried = false;
    this._storyFilmSetSource("", { isExcerpt: false });
    this._storyMusicPrototypeJobs = {};
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
      // The tables come from the backend rather than being written here
      // too. A profile list that exists in the panel as well is the same
      // one-number-in-two-places bug that has cost this project four
      // releases, only in a third deployable.
      if (Array.isArray(result.render_profiles)) {
        this._storyFilmProfiles = result.render_profiles;
      }
      if (Array.isArray(result.review_profiles)) {
        this._storyFilmReviewProfiles = result.review_profiles;
      }
      this._render({ preserveScroll: true });
    }
  },

  /**
   * Which finished film the next step works from - all of it at once.
   *
   * Three facts belong to one film: which job made it, whether it is an
   * excerpt, and whether it carries a soundtrack. They used to be set
   * separately, and the third one was set in exactly one place - the
   * restore-after-reload path. So every render started in this session
   * moved the first two on and left the third describing the PREVIOUS
   * film. Somebody who had mixed onto a silent excerpt and then rendered
   * a new one with music selected was still told the source was silent:
   * the three fassung buttons stayed up, and the mux refused what the
   * panel had just offered (live report).
   *
   * That is this project's most repeated mistake in a new costume - a
   * value that keeps answering after the thing it described is gone. So
   * the source is set as a unit, and the soundtrack starts out UNKNOWN
   * rather than inherited. Unknown is a third value on purpose: the film
   * has not been measured yet, and "not measured" must not be spoken as
   * "silent" - that guess is what would offer a mix that cannot work.
   * `_storyFilmSourceMeasured` fills it in once the job ends.
   */
  _storyFilmSetSource(jobId, { isExcerpt }) {
    this._storyFilmSourceJobId = jobId;
    this._storyFilmSourceIsExcerpt = isExcerpt;
    this._storyFilmSourceHasAudio = undefined;
  },

  /**
   * The soundtrack question answered by measurement, not by intention.
   *
   * `has_audio` comes from ffprobe on the finished file. Deriving it from
   * "a track was selected before the render" would be a guess wearing the
   * costume of a fact, and it would be wrong for every film whose render
   * dropped the music for a reason nobody saw.
   *
   * Only a real boolean is taken. A missing field leaves the answer
   * unknown, which is the honest state and the one the buttons already
   * know how to treat.
   */
  _storyFilmSourceMeasured(jobId, result) {
    if (!jobId || jobId !== this._storyFilmSourceJobId) return;
    const found = (result || {}).video;
    if (!found || typeof found.has_audio !== "boolean") return;
    this._storyFilmSourceHasAudio = found.has_audio;
  },

  /**
   * "Film erstellen", with the music orchestrated around it.
   *
   * The user made two decisions; this turns them into the right sequence
   * without asking anything the status block has not already answered:
   *
   * - Ohne Musik, or an own file: one render, as always.
   * - KI-Musik, already generated: the film renders SILENT, and the
   *   score is muxed onto the finished MP4 as soon as the render
   *   completes. Music comes last - fitted to the measured length,
   *   loudness-matched, video stream copied. Baking it into the render
   *   package is the architecture this project already left once.
   * - KI-Musik, not generated yet: one confirmation naming the price
   *   and the number of provider calls, then generate, then the same
   *   silent-render-plus-mux. Cancelling starts nothing at all.
   *
   * If the panel is closed during the render, the mux flag dies with
   * it - that is the one gap, and the finished film then carries the
   * "Musik auflegen" button as the manual fallback.
   */
  async _storyFilmRender() {
    // One in flight. The button stays visible while music generates and
    // the package builds; without this a second press starts a second
    // orchestration over the same state.
    if (this._storyFilmPreparing) return;
    const wantsGenerated = this._storyFilmTrack === GENERATED_MUSIC;
    if (!wantsGenerated) {
      await this._storyFilmRenderSubmit();
      return;
    }
    if (!this._storyFilmMusicOfferData) await this._storyFilmMusicOffer();
    const offer = this._storyFilmMusicOfferData;
    if (!offer) return;
    const missing = Number(offer.new_generations || 0);
    if (missing > 0) {
      if (offer.available === false) {
        this._showToast(
          String(offer.unavailable_reason || "KI-Musik ist nicht verfügbar."),
          "error",
          9000,
        );
        return;
      }
      const cost = (Number(offer.estimated_cost) || 0).toFixed(2);
      const currency = String(offer.currency || "USD");
      this._confirm(
        "Musik zuerst erzeugen?",
        `${missing} Musikabschnitt${missing === 1 ? "" : "e"} für diesen Film `
          + `${missing === 1 ? "ist" : "sind"} noch nicht erzeugt - das sind `
          + `${missing} Aufruf${missing === 1 ? "" : "e"} bei ${String(offer.model || "Lyria")}, `
          + `geschätzt ${cost} ${currency}. Danach startet der Film automatisch; `
          + "die Musik wird nach dem Render aufgelegt und für weitere "
          + "Rendergrößen wiederverwendet.",
        `Erzeugen und rendern (${cost} ${currency})`,
        async () => {
          // Busy from the first paid second. The generation takes one
          // provider call per section, and a button that looks idle
          // while money is being spent invites a second press.
          this._storyFilmPreparing = true;
          this._render({ preserveScroll: true });
          const made = await this._storyFilmMusicGenerate();
          // Decided on the GENERATION, not on the offer refresh that
          // follows it: async_generate returns only after every section
          // is stored, while a failed refresh would leave the stale
          // "still missing" offer standing - and swallowing the render
          // after a successful, paid generation breaks the sentence the
          // dialog just promised.
          if (made) {
            await this._storyFilmRenderSubmit();
          } else {
            this._storyFilmPreparing = false;
            this._render({ preserveScroll: true });
          }
        },
      );
      return;
    }
    await this._storyFilmRenderSubmit();
  },

  /**
   * Why the render did not start - the server's own words, when it said any.
   *
   * The card used to answer this question by GUESSING, and the guess was
   * the same sentence every time: "die Renderer-App läuft nicht oder ist
   * noch nicht bereit". On a live system that was false in the most
   * expensive way possible - the app was running, its own diagnosis said
   * ready, a test render worked, and the actual reason ("Bildposition
   * liegt ausserhalb des erlaubten Bereichs") existed and was thrown
   * away. Three days were spent on a service that had nothing wrong with
   * it. An absent answer rendered as a state, again.
   *
   * The fallback stays for the one case it was written for: a failure
   * with no message at all.
   */
  _storyFilmStartReason(action, fallback) {
    const last = this._lastActionError;
    const message = last && last.action === action ? String(last.message || "").trim() : "";
    if (!message) return fallback;
    return `${message} (gemeldet von Roadplanner, nicht von der Renderer-App)`;
  },

  async _storyFilmRenderSubmit() {
    this._storyFilmStartError = "";
    // Said before anything is fetched, because between this click and
    // the first progress number lies the whole package build: a couple
    // of hundred photographs downloaded and shrunk, every recording a
    // clip comes from fetched and cut, the package written. That is
    // minutes, and it used to happen in complete silence - so the button
    // read as broken until it suddenly was not (live report).
    this._storyFilmPreparing = true;
    this._render({ preserveScroll: true });
    const result = await this._runAction(
      "story_film_render",
      {
        trip_id: this._selectedTripId,
        music: this._storyFilmTrack || "",
        // The id that is on screen, resolved the same way the picker
        // resolved it - never the raw field, which is empty until
        // somebody touches the select.
        profile: this._storyFilmChosen(
          this._storyFilmProfileTable("render"),
          this._storyFilmProfile,
        ),
      },
      "Reisefilm wird gerendert",
      { refresh: false, blockUi: false, errorTitle: "Der Reisefilm konnte nicht gestartet werden" },
    );
    this._storyFilmPreparing = false;
    if (!result?.renderer_app_job?.job_id) {
      // Live report: "Aktuell passiert nichts beim Drücken von Film
      // erzeugen." It was this line. `_runAction` returns null for every
      // failure and shows a toast that is gone in seconds, so a render
      // that could not start looked exactly like a button that does
      // nothing - and the reasonable reaction to that is to press it
      // again. The card says it now, and keeps saying it.
      this._storyFilmStartError = this._storyFilmStartReason(
        "story_film_render",
        "Der Film konnte nicht gestartet werden. Häufigster Grund: Die "
        + "Renderer-App läuft nicht oder ist noch nicht bereit. Der "
        + "genaue Grund steht im Home-Assistant-Protokoll unter "
        + "„roadplanner\u201c.",
      );
      this._render({ preserveScroll: true });
      return;
    }
    this._rendererAppKind = "trip_film";
    // Decided AT SUBMIT, because that is when the choice was made - and
    // tied to THIS job and THIS trip. The poll sees other jobs finish
    // too, and the user may browse another trip while the render runs;
    // neither retracts nor redirects a decision already paid for. A bare
    // boolean here fired the mux on whatever completed next and billed
    // it to whatever trip was on screen (found in review).
    this._storyFilmMuxAfterJobId =
      this._storyFilmTrack === GENERATED_MUSIC ? result.renderer_app_job.job_id : "";
    this._storyFilmMuxAfterTripId = this._selectedTripId;
    // The film this render will produce, and therefore the only thing a
    // review copy may later be made from.
    this._storyFilmSetSource(result.renderer_app_job.job_id, { isExcerpt: false });
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
    // A running MUX blocks the button too: it works on the film that
    // was just rendered, and starting a second render under it tangles
    // two jobs' state in one card.
    const running =
      ["trip_film", "film_music"].includes(this._rendererAppKind)
      && job && !job.terminal && job.state;
    const startError = running ? "" : String(this._storyFilmStartError || "");
    const preparing = Boolean(this._storyFilmPreparing) && !running;
    return `<div class="notice neutral"><div>
      <strong>Reisefilm aus dieser Geschichte</strong>
      <small>Ein Film über die ganze Reise, ein Kapitel je Tag, aus genau diesen Titeln, Texten und Bildern. Tage ohne Fotos werden als solche gezeigt und nicht übersprungen.</small>
      ${startError ? `<small class="hint"><strong>${escapeHtml(startError)}</strong></small>` : ""}
      ${
        preparing
          ? `<small class="hint"><strong>Der Film wird vorbereitet.</strong> Alle Bilder werden geholt und verkleinert, die Aufnahmen der Videomomente geladen und geschnitten, dann das Paket geschrieben. Das dauert einige Minuten, <em>bevor</em> die erste Fortschrittszahl erscheint - der Knopf ist so lange still, aber nicht untätig. Die Seite darf verlassen werden.</small>`
          : ""
      }
      ${
        film
          ? `<small>${escapeHtml(String(film.chapter_count))} Kapitel · ${escapeHtml(String(film.planned_photo_count))} Bilder ausgewählt (bis ${escapeHtml(String(film.photos_per_chapter))} je Tag) · ${escapeHtml(String(film.chapters_without_photos))} Tage ohne Fotos</small>
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
      <div class="film-choices">
        ${this._renderStoryFilmProfile()}
        ${this._renderStoryFilmMusicChoice()}
      </div>
      ${this._renderStoryFilmMusicStatus()}
      ${this._renderStoryFilmSummary()}
      <div class="button-row">
        ${canEdit ? `<button class="primary-button" type="button" data-action="story-film-render"${(online || !status) && !running && !preparing ? "" : " disabled"}><ha-icon icon="mdi:movie-play-outline"></ha-icon>${preparing ? " Film wird vorbereitet …" : " Film erstellen"}</button>` : ""}
        <button class="secondary-button" type="button" data-action="story-film-preview"><ha-icon icon="mdi:filmstrip-box-multiple"></ha-icon> ${film ? "Vorschau aktualisieren" : "Was käme in den Film?"}</button>
      </div>
      ${this._renderStoryFilmJobLine()}
    </div></div>`;
  },

  /**
   * Everything the film needs before it starts, in one place.
   *
   * Read from the preview, the profile table and the music offer that
   * are already loaded - nothing here asks for anything. The point is
   * that the last thing somebody sees before pressing the button is a
   * plain account of what they are about to get, including what it
   * costs, rather than four pickers whose combined meaning they have to
   * assemble themselves.
   *
   * Only figures that are actually known. A summary that invents a
   * duration for a trip nobody previewed would be worse than no summary:
   * it would be believed.
   */
  _renderStoryFilmSummary() {
    const film = this._storyFilm;
    if (!film) return "";
    const profiles = this._storyFilmProfileTable("render");
    const chosen = this._storyFilmChosen(profiles, this._storyFilmProfile);
    const profile = profiles.find((entry) => entry.id === chosen);
    const offer = this._storyFilmMusicOfferData;
    const wantsMusic = this._storyFilmTrack === GENERATED_MUSIC;
    const rows = [];
    const seconds = Number(film.film_seconds || 0);
    if (seconds > 0) rows.push(["Dauer", this._storyFilmClock(seconds)]);
    if (profile) {
      rows.push([
        "Qualität",
        `${profile.label} · ${profile.width}×${profile.height} · ${profile.fps} fps`,
      ]);
    }
    // "Ausgewählt" on purpose. Selected, available and rendered are three
    // different numbers, and folding them together is how a film promised
    // more photographs than it showed. This is what the plan CHOSE; the
    // days it could not fill are named separately rather than averaged
    // into the same figure. How many video moments end up in the cut is
    // not known until the package is built, so it is not claimed here.
    const media = [`${film.planned_photo_count} Bilder ausgewählt`];
    if (Number(film.chapters_without_photos || 0) > 0) {
      media.push(`${film.chapters_without_photos} Tage ohne Fotos`);
    }
    rows.push(["Medien", media.join(" · ")]);
    if (!wantsMusic) {
      rows.push(["Musik", this._storyFilmTrack ? `Datei „${this._storyFilmTrack}“` : "Ohne Musik"]);
    } else if (offer) {
      const fresh = Number(offer.new_generations || 0);
      rows.push(["Musik", fresh ? "KI-Musik · wird neu erzeugt" : "KI-Musik · vorhandener Soundtrack"]);
      rows.push([
        "Zusätzliche Musikkosten",
        `${(Number(offer.estimated_cost) || 0).toFixed(2)} ${escapeHtml(String(offer.currency || "USD"))}`,
      ]);
    } else {
      rows.push(["Musik", "KI-Musik"]);
    }
    return `<div class="film-summary"><dl>
      ${rows
        .map(
          ([label, value]) =>
            `<div><dt>${escapeHtml(String(label))}</dt><dd>${escapeHtml(String(value))}</dd></div>`,
        )
        .join("")}
    </dl></div>`;
  },

  /**
   * The things that are still worth having and no longer worth showing.
   *
   * A QA excerpt, a small review copy, an own soundtrack file: each one
   * exists for a good reason, and none of them is part of "make a film
   * of our trip". They sat in the same button row as the film itself,
   * which made a page of six equally weighted controls out of a decision
   * with two parts. Collapsed, not deleted - somebody who wants a 60
   * second excerpt knows they want one.
   */
  _renderStoryFilmAdvancedExports() {
    const canEdit = this._canEdit();
    const status = this._rendererAppStatus;
    const online = Boolean(status?.online);
    const job = this._rendererAppJob;
    const running = job && !job.terminal && job.state;
    const blocked = !((online || !status) && !running);
    return `<details data-section="film-advanced" class="film-advanced">
      <summary><span><ha-icon icon="mdi:export-variant"></ha-icon>Weitere Exportoptionen</span><small>Prüfausschnitt, Review-Kopie, eigene Musikdatei</small></summary>
      <div class="assistant-technical-content">
        <small class="hint">Review-Fassungen sind kleine Abnahmekopien zum Anschauen und Verschicken – nicht der eigentliche Film.</small>
        ${
          this._storyFilmTrack === GENERATED_MUSIC
            ? `<small class="hint">Der Prüfausschnitt wird ohne KI-Musik gerendert – die Musik kommt erst auf den fertigen ganzen Film.</small>`
            : ""
        }
        <div class="button-row">
          ${canEdit ? `<button class="secondary-button" type="button" data-action="story-film-qa-render"${blocked ? " disabled" : ""}><ha-icon icon="mdi:magnify-scan"></ha-icon> Prüfausschnitt (60–90 s)</button>` : ""}
        </div>
        ${this._renderStoryFilmMusic()}
      </div>
    </details>`;
  },

  /**
   * The finished film, where the story ends up.
   *
   * The film used to be reachable only as a download link at the bottom
   * of the job block that made it - which means it existed as evidence
   * that a render had worked, and not as the thing the whole feature is
   * for. Here it is the first thing on the page: a title, a picture, and
   * a way to watch it.
   *
   * The LAST SUCCESSFUL one, from the same record the player uses. A
   * render that failed this morning does not take last week's film off
   * the page.
   */
  _renderStoryLatestFilm() {
    if (this._storyLatestFilm === undefined && !this._storyLatestFilmAsked) {
      this._storyLatestFilmAsked = true;
      void this._storyLoadLatestFilm();
    }
    const film = this._storyLatestFilm;
    if (!film || !film.url) return "";
    const parts = [];
    if (Number(film.duration_seconds) > 0) parts.push(this._storyFilmClock(film.duration_seconds));
    if (film.width && film.height) parts.push(`${film.width}×${film.height}`);
    parts.push(film.has_music ? "mit Musik" : "ohne Musik");
    return `<div class="latest-film">
      <video src="${escapeHtml(String(film.url))}" controls preload="metadata" playsinline></video>
      <div class="latest-film-meta">
        <strong>Der Reisefilm</strong>
        <small>${escapeHtml(parts.join(" · "))}</small>
        <div class="button-row">
          <button class="secondary-button" type="button" data-action="player-enter"><ha-icon icon="mdi:television-play"></ha-icon> Im Player ansehen</button>
        </div>
      </div>
    </div>`;
  },

  async _storyLoadLatestFilm() {
    const result = await this._runAction(
      "player_latest_film",
      { trip_id: this._selectedTripId },
      "",
      { refresh: false, blockUi: false, errorTitle: "" },
    ).catch(() => null);
    // `null` is an answer: this trip has no finished film. Stored as
    // null rather than left undefined so the question is not asked again
    // on every render.
    this._storyLatestFilm = result?.player_latest_film || null;
    this._render({ preserveScroll: true });
  },

  /** Seconds as mm:ss, because "912 s" is not a film length. */
  _storyFilmClock(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, "0")}`;
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
  /**
   * The whole music decision, in two options.
   *
   * There used to be one list holding "Ohne Musik", a generated score and
   * every file in the music folder, and around it a cue sheet, a style
   * lock, a plan, an architecture comparison and three fassungen. All of
   * that still exists and still works; none of it is a decision somebody
   * makes while sending a film to their family. The question here is the
   * only one they actually have: should there be music or not.
   *
   * Own files are not gone - they moved to the advanced exports, where
   * somebody who deliberately put an mp3 in the folder will look.
   */
  _renderStoryFilmMusicChoice() {
    const wants = this._storyFilmTrack === GENERATED_MUSIC;
    return `<label class="inline-select"><span>Musik</span><select data-action="story-film-music-choice">
      <option value=""${wants ? "" : " selected"}>Ohne Musik</option>
      <option value="${GENERATED_MUSIC}"${wants ? " selected" : ""}>Mit KI-Musik</option>
    </select></label>`;
  },

  /**
   * What choosing KI-Musik means for this trip, in money and in work.
   *
   * Two states, and the difference between them is the entire point.
   * Music that already exists is reused by every further render, at
   * every size, for nothing - and somebody who does not know that will
   * avoid re-rendering a film they should be re-rendering. Music that
   * does not exist yet costs a named amount, once.
   *
   * The numbers come from the real plan. Nothing here is a constant: a
   * hard-coded "5 Abschnitte · 0,40 USD" would be right for exactly one
   * trip and quietly wrong for every other.
   */
  _renderStoryFilmMusicStatus() {
    if (this._storyFilmTrack !== GENERATED_MUSIC) return "";
    const offer = this._storyFilmMusicOfferData;
    const canEdit = this._canEdit();
    if (!offer) {
      return `<div class="film-music-status"><small class="hint">Der Musikstand für diese Reise ist noch nicht geladen.</small>
        <button class="text-button" type="button" data-action="story-film-music-offer"><ha-icon icon="mdi:refresh"></ha-icon> Musikstand prüfen</button></div>`;
    }
    const fresh = Number(offer.new_generations || 0);
    const total = Number(offer.sections || 0);
    const cost = (Number(offer.estimated_cost) || 0).toFixed(2);
    const currency = escapeHtml(String(offer.currency || "USD"));
    if (fresh === 0 && total > 0) {
      return `<div class="film-music-status">
        <strong>KI-Musik · vorhandener Soundtrack</strong>
        <small>${escapeHtml(String(total))} Abschnitte sind bereits erzeugt. Zusätzliche Kosten: 0,00 ${currency}.</small>
        <small class="hint">Weitere Renderdurchgänge – auch in einer anderen Größe – verwenden diese Musik erneut und kosten nichts.</small>
        ${
          canEdit
            ? `<button class="text-button" type="button" data-action="story-film-music-regenerate"><ha-icon icon="mdi:music-note-plus"></ha-icon> Neue Musikvariante erzeugen</button>`
            : ""
        }
      </div>`;
    }
    if (!offer.available) {
      return `<div class="film-music-status">
        <strong>KI-Musik ist nicht verfügbar</strong>
        <small>${escapeHtml(String(offer.unavailable_reason || "Kein Grund gemeldet."))}</small>
      </div>`;
    }
    return `<div class="film-music-status">
      <strong>KI-Musik wird einmal erzeugt</strong>
      <small>Geplante Musikabschnitte: ${escapeHtml(String(total))}${
        Number(offer.cached || 0) > 0 ? ` (${escapeHtml(String(offer.cached))} davon schon vorhanden)` : ""
      }</small>
      <small>Geschätzte Zusatzkosten: ca. ${cost} ${currency}</small>
      <small class="hint">Die Musik wird einmal erzeugt und danach für weitere Rendergrößen wiederverwendet.</small>
    </div>`;
  },

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
          ? `<option value="${GENERATED_MUSIC}"${chosen === GENERATED_MUSIC ? " selected" : ""}>KI-Musik (${escapeHtml(String(generated.cached))} Abschnitte)</option>`
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

  /**
   * How large the film is rendered.
   *
   * Pixels only. Whichever size is chosen, the film is the same film:
   * the same scenes in the same order, the same photographs, the same
   * clips, the same seconds. The small ones exist because a full-size
   * render takes about an hour, and looking at a cut twelve times means
   * twelve hours of waiting that answer no question.
   */
  /**
   * Which id a picker is actually showing.
   *
   * Not `|| ""`: with nothing chosen the browser selects the FIRST
   * option, while an empty value makes the backend use its default. Those
   * are different profiles, and the film would come out at a size the
   * panel had never shown. So the default travels with the list and is
   * resolved here, in one place, for both pickers.
   */
  _storyFilmChosen(profiles, chosen) {
    if (chosen && profiles.some((entry) => entry.id === chosen)) return chosen;
    const fallback = profiles.find((entry) => entry.default) || profiles[0];
    return fallback ? fallback.id : "";
  },

  /**
   * The size tables, from the ordinary panel payload.
   *
   * They used to arrive ONLY in the answer to "Was käme in den Film?",
   * which meant the size picker and the whole review-copy offer were
   * invisible until somebody pressed an unrelated button - and a film
   * that renders in the default size forever is exactly what a hidden
   * choice produces. They travel with the payload now; the preview
   * answer still refreshes them, which costs nothing.
   */
  _storyFilmProfileTable(kind) {
    const local = kind === "review" ? this._storyFilmReviewProfiles : this._storyFilmProfiles;
    if (Array.isArray(local) && local.length) return local;
    const data = this._experienceData();
    const carried = kind === "review" ? data?.review_profiles : data?.render_profiles;
    return Array.isArray(carried) ? carried : [];
  },

  _renderStoryFilmProfile() {
    const profiles = this._storyFilmProfileTable("render");
    if (!profiles.length) return "";
    const chosen = this._storyFilmChosen(profiles, this._storyFilmProfile);
    const note = profiles.find((entry) => entry.id === chosen);
    return `<label class="inline-select"><span>Größe</span><select data-action="story-film-profile">
      ${profiles
        .map(
          (entry) =>
            `<option value="${escapeHtml(String(entry.id))}"${chosen === entry.id ? " selected" : ""}>${escapeHtml(String(entry.label))}${entry.recommended ? " ★" : ""}</option>`,
        )
        .join("")}
    </select></label>${
      note
        ? `<small class="hint">${escapeHtml(String(note.description))}${note.experimental ? " Experimentell." : ""}</small>`
        : ""
    }`;
  },

  /**
   * A small copy of the film that is already there.
   *
   * Not a second render. The finished MP4 is re-encoded smaller, which
   * takes minutes instead of an hour and costs nothing: no photograph is
   * fetched, no model is asked, nothing leaves the box. It exists because
   * a twelve-minute film is over two hundred megabytes and the question
   * asked of it afterwards - does this cut work? - does not need them.
   */
  /**
   * Stop a render that is running.
   *
   * The marker is written and the card keeps polling. The renderer
   * notices between frames, ends its work, cleans up and writes the
   * outcome - so the answer arrives the same way every other answer
   * about that job does, rather than being guessed here.
   */
  async _rendererAppCancel() {
    const jobId = this._rendererAppJob?.job_id;
    if (!jobId || this._rendererAppJob?.terminal || this._rendererAppCancelling) return;
    this._rendererAppCancelling = true;
    this._render({ preserveScroll: true });
    try {
      await this._runAction(
        "renderer_app_cancel",
        { job_id: jobId },
        "Abbruch angefordert",
        {
          refresh: false,
          blockUi: false,
          errorMode: "dialog",
          errorTitle: "Der Render konnte nicht abgebrochen werden",
        },
      );
    } finally {
      this._rendererAppCancelling = false;
      this._render({ preserveScroll: true });
    }
  },

  /**
   * The quality check: a piece of the film, at the size being judged.
   *
   * Not a shorter film - a window into this one. The package is built
   * exactly as a full render builds it, and only the frames drawn
   * change, so what comes back is evidence about the film somebody is
   * actually going to ship.
   */
  async _storyFilmQaRender() {
    this._storyFilmStartError = "";
    // The package build is the same couple of hundred photographs a full
    // render fetches. Only the drawing is short, so the silence before
    // the first progress number is just as long.
    this._storyFilmPreparing = true;
    this._render({ preserveScroll: true });
    const result = await this._runAction(
      "story_film_qa_render",
      {
        trip_id: this._selectedTripId,
        music: this._storyFilmTrack || "",
        profile: this._storyFilmChosen(
          this._storyFilmProfileTable("render"),
          this._storyFilmQaProfile || "high_quality",
        ),
        chapter_id: this._storyFilmQaChapterId || "",
        start_seconds: this._storyFilmQaStartSeconds || "",
      },
      "Prüfausschnitt wird gerendert",
      { refresh: false, blockUi: false, errorTitle: "Der Prüfausschnitt konnte nicht gestartet werden" },
    );
    this._storyFilmPreparing = false;
    if (!result?.renderer_app_job?.job_id) {
      this._storyFilmStartError = this._storyFilmStartReason(
        "story_film_qa_render",
        "Der Prüfausschnitt konnte nicht gestartet werden. Der genaue Grund "
        + "steht im Home-Assistant-Protokoll unter „roadplanner\u201c.",
      );
      this._render({ preserveScroll: true });
      return;
    }
    // Its own kind. Announcing a sixty-second excerpt as "ein Reisefilm
    // wird gerendert - das dauert viele Minuten" makes a two-minute job
    // look like a ninety-minute one, and the job already carries what it
    // needs to say otherwise.
    this._rendererAppKind = "film_excerpt";
    // Marked as an excerpt, because an excerpt is a source for nothing:
    // a score laid out for twelve minutes does not go onto ninety
    // seconds, and a review copy of a piece answers a question nobody
    // asked. The comparison fassungen are the one exception, and they
    // need it silent - which is measured, not assumed.
    this._storyFilmSetSource(result.renderer_app_job.job_id, { isExcerpt: true });
    this._storyFilmExcerpt = result.renderer_app_job.excerpt || null;
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  /**
   * What the chosen excerpt contains - and what could not be weighed.
   *
   * The second half matters as much as the first: a scene plan carries
   * no orientation, so nothing balanced portrait against landscape. Not
   * saying so would let "automatisch gewählt" read as "everything was
   * considered".
   */
  _renderStoryFilmExcerpt() {
    const found = this._storyFilmExcerpt;
    if (!found) return "";
    const names = {
      chapter_transition: "Kapitelwechsel",
      map: "Karte",
      map_drive: "Kartenfahrt",
      text: "Text",
      hero: "Hero",
      collage: "Collage",
      clip: "Videoclip",
    };
    const has = Object.entries(found.contains || {})
      .filter(([, present]) => present)
      .map(([key]) => names[key] || key);
    const missing = (found.missing || []).map((key) => names[key] || key);
    const start = Math.round(Number(found.start_seconds) || 0);
    return `<small class="hint">Ausschnitt ab ${Math.floor(start / 60)}:${String(start % 60).padStart(2, "0")} · ${escapeHtml(String(Math.round(Number(found.seconds) || 0)))} s · ${escapeHtml(String(found.scene_count || 0))} Szenen (${escapeHtml(String(found.reason || ""))}).
      ${has.length ? `Enthält: ${escapeHtml(has.join(", "))}.` : ""}
      ${missing.length ? ` Ohne: ${escapeHtml(missing.join(", "))}.` : ""}
      Nicht gewichtet: Hoch-/Querformat – der Szenenplan führt keine Ausrichtung.</small>`;
  },

  async _storyFilmReviewCopy() {
    const sourceId = this._storyFilmSourceJobId || "";
    if (!sourceId) return;
    const result = await this._runAction(
      "story_film_review_copy",
      {
        job_id: sourceId,
        profile: this._storyFilmChosen(
          this._storyFilmProfileTable("review"),
          this._storyFilmReviewProfile,
        ),
      },
      "Review-Kopie wird erstellt",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Review-Kopie konnte nicht gestartet werden",
      },
    );
    if (!result?.renderer_app_job?.job_id) return;
    // The card watches the copy now, but `_storyFilmSourceJobId` keeps
    // pointing at the film: a second copy, in the other size, is a
    // reasonable next thing to want.
    this._rendererAppKind = "review_copy";
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    // The link points at the film this copy was made from. Leaving it
    // would offer the two-hundred-megabyte version under the label of
    // the small one.
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  /**
   * The soundtrack onto the film that was just rendered.
   *
   * Free, and it has to look free: every section it uses was generated
   * and paid for at the offer, and this only reads them off the disk.
   * The film keeps its own length here - measured by ffprobe rather than
   * estimated - which is the whole reason the music goes on last.
   */
  /**
   * The second half of "Film erstellen mit KI-Musik".
   *
   * Called by the job poll on every terminal transition. The intent
   * names the ONE job it was armed for; any other job completing - an
   * older render adopted after a reload, a review copy, the mux itself -
   * is not a match and changes nothing. One shot on the named job,
   * whatever its outcome: the intent is cleared before the success
   * check, so a failed or cancelled render can never leave a primed mux
   * behind.
   *
   * The handoff waits for the poll guard rather than hoping. The poll
   * that noticed the completion still holds `_rendererAppPolling` while
   * it finishes its status read; a mux started under that guard would
   * submit fine but its own poll would be silently dropped. So this
   * retries until the guard falls, bounded - and past the bound it runs
   * anyway, because a submitted-but-unwatched mux is recoverable from
   * the job list while an unsubmitted one is simply missing music.
   */
  _storyFilmMaybeAutoMux(job) {
    const wanted = this._storyFilmMuxAfterJobId || "";
    if (!wanted || !job || !job.terminal) return;
    if (job.job_id !== wanted) return;
    this._storyFilmMuxAfterJobId = "";
    if (job.state !== "completed") return;
    const tripId = this._storyFilmMuxAfterTripId || this._selectedTripId;
    let tries = 0;
    const start = () => {
      if (this._rendererAppPolling && tries < 120) {
        tries += 1;
        window.setTimeout(start, 250);
        return;
      }
      void this._storyFilmAddMusic(wanted, tripId);
    };
    window.setTimeout(start, 0);
  },

  async _storyFilmAddMusic(jobId = "", tripId = "") {
    // Explicit for the auto path: it fires up to an hour after submit,
    // and both the selected trip and the source job may have moved on by
    // then. The manual button passes nothing and means "what is on
    // screen", which is what a button means.
    const sourceId = jobId || this._storyFilmSourceJobId || "";
    if (!sourceId) return;
    const result = await this._runAction(
      "story_film_add_music",
      { trip_id: tripId || this._selectedTripId, job_id: sourceId },
      "Musik wird aufgelegt",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Musik konnte nicht aufgelegt werden",
      },
    );
    if (!result?.renderer_app_job?.job_id) return;
    // The film with its music becomes the new source: a review copy made
    // from here carries the soundtrack, and that is the version somebody
    // sends out. Copying the silent cut would answer the one question a
    // review of a finished film cannot answer.
    this._storyFilmSetSource(result.renderer_app_job.job_id, { isExcerpt: false });
    this._rendererAppKind = "film_music";
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
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
   * The music architecture comparison, as small a panel as it can be.
   *
   * Three fassungen of the same excerpt, one line each, and two
   * buttons. Deliberately not a music studio: what is being decided is
   * whether a travel film wants one piece, an atmosphere with a layer
   * over it, or the atmosphere alone - and that is decided by listening,
   * not by turning knobs here.
   */
  _renderStoryMusicPrototype() {
    const found = this._storyMusicPrototype;
    const canEdit = this._canEdit();
    if (!found) {
      return canEdit
        ? `<div class="film-music-plan"><button class="text-button" type="button" data-action="story-music-prototype-offer"><ha-icon icon="mdi:ab-testing"></ha-icon> Musikarchitektur vergleichen (A/B/C)</button></div>`
        : "";
    }
    const rows = (found.variants || [])
      .map((entry) => {
        // Three states, and "cached" is a different sentence from
        // "generated" - one of them means the next attempt is free.
        const state = entry.ready
          ? "vorhanden"
          : "noch nicht erzeugt";
        // The layers are laid out from the excerpt's own clock and are
        // as long as it is, so mixing them onto a whole film would give
        // a minute of music and then fourteen minutes of silence.
        //
        // But this side cannot always TELL. An excerpt and a full film
        // produce the same artifact, so after a page reload the panel
        // has adopted a job it can only call "a film". Hiding the button
        // on that would refuse the one case it is meant for - somebody
        // who rendered an excerpt, reloaded, and came back to mix it.
        // So `=== false` rather than falsy: only a film this session
        // KNOWS is a whole film suppresses it, and the refusal that
        // cannot be bypassed lives where the measured length is.
        const wrongSource = this._storyFilmSourceIsExcerpt === false;
        // A film that already has a soundtrack cannot take a second one:
        // the mux refuses it, and rightly. Only `true` suppresses the
        // button - an unread film stays offered, because not knowing is
        // not the same as knowing.
        const alreadyScored = this._storyFilmSourceHasAudio === true;
        const mixable =
          entry.ready && this._storyFilmSourceJobId && !wrongSource && !alreadyScored;
        // The job that produced THIS fassung, remembered per variant.
        // A review copy has to be made from it and not from the excerpt
        // the three were mixed onto - that one is the silent cut, and
        // three silent clips is exactly the review that answers nothing.
        const mixed = (this._storyMusicPrototypeJobs || {})[String(entry.variant)];
        return `<li>
          <strong>${escapeHtml(String(entry.label || entry.variant))}</strong>
          <small>${escapeHtml(String(entry.question || ""))}</small>
          <small class="hint">${escapeHtml(state)}</small>
          ${
            mixable && canEdit
              ? `<button class="text-button" type="button" data-action="story-music-prototype-mix" data-variant="${escapeHtml(String(entry.variant))}"><ha-icon icon="mdi:music-note-plus"></ha-icon> Fassung ${escapeHtml(String(entry.variant))} auflegen</button>`
              : ""
          }
          ${
            mixed && canEdit
              ? `<button class="text-button" type="button" data-action="story-music-prototype-review" data-variant="${escapeHtml(String(entry.variant))}"><ha-icon icon="mdi:content-duplicate"></ha-icon> Kleine Kopie von ${escapeHtml(String(entry.variant))}</button>`
              : ""
          }
        </li>`;
      })
      .join("");
    const excerpt = found.excerpt || {};
    const currency = String(found.currency || "USD");
    // §16: model, purpose, how many requests, what one request delivers,
    // and the estimate - on screen BEFORE the button that spends. A
    // single total says nothing about what it buys, and the word
    // "kostenpflichtig" said afterwards is not a disclosure.
    const facts = [
      ["Modell", String(found.model || "")],
      [
        "Zweck",
        `Architekturvergleich A/B/C über ${Math.round(Number(found.window_seconds || 0))} s Film`,
      ],
      [
        "Generierungen",
        `${found.generation_count} neu${found.reused_count ? ` · ${found.reused_count} vorhanden` : ""}`,
      ],
      ["Provider-Tracklänge", `bis ${found.provider_track_seconds} s je Anfrage`],
      [
        "Geschätzt",
        found.generation_count
          ? `${found.generation_count} × ${found.price_per_generation} ${currency} = ${found.estimated_cost} ${currency}`
          : "0 – nichts Neues",
      ],
    ]
      .filter(([, value]) => value)
      .map(
        ([name, value]) =>
          `<li><span>${escapeHtml(name)}</span><strong>${escapeHtml(String(value))}</strong></li>`,
      )
      .join("");
    const button =
      canEdit && found.generation_count && found.ready !== false
        ? actionButton(
            this._actionCosts(),
            "story-music-prototype-generate",
            `${found.generation_count} Stück erzeugen (${found.estimated_cost} ${currency})`,
          )
        : "";
    return `<div class="film-music-plan">
      <h4>Musikarchitektur-Prototyp</h4>
      <small>Derselbe Ausschnitt (${escapeHtml(
        String(Math.round(Number(found.window_seconds || 0))),
      )} s ab ${escapeHtml(
        String(Math.round(Number(excerpt.start_seconds || 0))),
      )} s), drei Tonfassungen. Nur der Ton ändert sich.</small>
      <ul class="plain-list">${rows}</ul>
      ${
        this._storyFilmSourceHasAudio === true
          ? `<small class="hint">Im vorhandenen Ausschnitt ist schon Musik zu hören – er wurde mit ausgewählter Musik gerendert. Für den Vergleich braucht es den stummen: oben „Musik“ auf „Ohne Musik“ stellen und den Prüfausschnitt neu rendern.</small>`
          : ""
      }
      ${
        this._storyFilmSourceJobId && this._storyFilmSourceIsExcerpt === false
          ? `<small class="hint">Zuletzt wurde ein ganzer Film gerendert. Die Fassungen gehören auf den Prüfausschnitt – erzeuge ihn zuerst, sonst spielt die Musik nur seinen Anfang.</small>`
          : ""
      }
      ${
        !this._storyFilmSourceJobId && this._rendererAppRecentAsked
          ? `<small class="hint">Zum Auflegen fehlt noch der Prüfausschnitt.</small>`
          : ""
      }
      ${
        !this._storyFilmSourceJobId && !this._rendererAppRecentAsked
          ? // Not "the excerpt is missing" - the job list has not been
            // read yet, and those are different sentences. Telling
            // somebody to render something they already have is how an
            // absent answer sends them off to redo finished work.
              `<small class="hint">Die Aufträge der Renderer-App sind noch nicht gelesen - vorhandene Ausschnitte und Mischungen erscheinen gleich.</small>`
          : ""
      }
      <ul class="story-music-facts">${facts}</ul>
      <small class="hint">${escapeHtml(String(found.notice || ""))}</small>
      ${
        found.ready === false
          ? `<small class="hint">${escapeHtml(String(found.reason || ""))}</small>`
          : ""
      }
      ${
        found.generation_count
          ? `<p class="hint">Das ist ein kostenpflichtiger Aufruf bei Google und wird pro Anfrage abgerechnet – nicht pro Sekunde. Die Stücke werden gespeichert und für weitere Mischungen wiederverwendet.</p>`
          : ""
      }
      ${button}
    </div>`;
  },

  /** What the comparison would cost. Reads and prices, orders nothing. */
  async _storyMusicPrototypeOffer() {
    const result = await this._runAction(
      "story_music_prototype_offer",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Der Musikvergleich konnte nicht berechnet werden",
      },
    );
    if (!result?.music_prototype) return;
    this._storyMusicPrototype = result.music_prototype;
    this._render({ preserveScroll: true });
  },

  /**
   * The one button here that spends money, and it says how much first.
   *
   * At most three generations for three fassungen, because two of them
   * share the atmosphere layer. Nothing about the film's full soundtrack
   * is ordered from here.
   */
  async _storyMusicPrototypeGenerate() {
    const found = this._storyMusicPrototype;
    if (!found || !found.generation_count) return;
    const result = await this._runAction(
      "story_music_prototype_generate",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Musik konnte nicht erzeugt werden",
      },
    );
    if (!result?.music_prototype) return;
    this._storyMusicPrototype = result.music_prototype;
    const made = result.music_prototype;
    this._showToast(
      `${made.generated} Stück erzeugt` + (made.reused ? ` · ${made.reused} wiederverwendet` : ""),
      "success",
      7000,
    );
    await this._storyFilmMusicLoad();
    this._render({ preserveScroll: true });
  },

  /** One fassung onto the rendered excerpt. Free - the pieces exist. */
  async _storyMusicPrototypeMix(variant) {
    if (!variant || !this._storyFilmSourceJobId) return;
    const result = await this._runAction(
      "story_music_prototype_mix",
      {
        trip_id: this._selectedTripId,
        job_id: this._storyFilmSourceJobId,
        variant,
      },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Fassung konnte nicht aufgelegt werden",
      },
    );
    if (!result?.renderer_app_job?.job_id) return;
    // Remembered per variant rather than replacing the source job. The
    // excerpt stays the source, because the next fassung is mixed onto
    // the SAME silent film - muxing onto a film that already has audio
    // is refused, and rightly so.
    this._storyMusicPrototypeJobs = {
      ...(this._storyMusicPrototypeJobs || {}),
      [String(variant)]: result.renderer_app_job.job_id,
    };
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppKind = "film_music";
    this._rendererAppResult = null;
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
  },

  /** A small copy of ONE fassung, made from that fassung's own job. */
  async _storyMusicPrototypeReview(variant) {
    const jobId = (this._storyMusicPrototypeJobs || {})[String(variant)];
    if (!jobId) return;
    const result = await this._runAction(
      "story_film_review_copy",
      {
        job_id: jobId,
        profile: this._storyFilmChosen(
          this._storyFilmProfileTable("review"),
          this._storyFilmReviewProfile,
        ),
      },
      "Review-Kopie wird erstellt",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Review-Kopie konnte nicht gestartet werden",
      },
    );
    if (!result?.renderer_app_job?.job_id) return;
    this._rendererAppKind = "review_copy";
    this._rendererAppJob = result.renderer_app_job;
    this._rendererAppResult = null;
    this._rendererAppDownloadUrl = "";
    this._render({ preserveScroll: true });
    this._pollRendererAppJob(result.renderer_app_job.job_id);
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

  /**
   * A different take of the same score, asked for on purpose.
   *
   * The only route in the product that pays for music that already
   * exists. Everything else - a second render, another size, a review
   * copy, a page reload, a restarted Home Assistant - reuses what is
   * there, which is the promise the status block makes. So this one asks
   * first, and it asks with the numbers: what it costs, how many
   * provider calls, and that the pieces now on disk are replaced.
   */
  async _storyFilmMusicRegenerate() {
    const offer = this._storyFilmMusicOfferData;
    if (!offer) {
      await this._storyFilmMusicOffer();
      return;
    }
    const sections = Number(offer.sections || 0);
    if (!sections) return;
    const price = Number(offer.price_per_generation) || 0;
    const total = (sections * price).toFixed(2);
    const currency = String(offer.currency || "USD");
    this._confirm(
      "Neue Musikvariante erzeugen?",
      `Alle ${sections} Abschnitte werden neu erzeugt – ${sections} Aufrufe bei `
        + `${String(offer.model || "Lyria")}, geschätzt ${total} ${currency}. `
        + "Die bisherigen Stücke dieser Reise werden dabei ersetzt. "
        + "Für einen normalen Filmrender ist das nicht nötig: der verwendet die "
        + "vorhandene Musik und kostet nichts.",
      `Neu erzeugen (${total} ${currency})`,
      () => this._storyFilmMusicGenerate({ force: true }),
      true,
    );
  },

  /** The one place in the story editor that spends money. */
  async _storyFilmMusicGenerate({ force = false } = {}) {
    const offer = this._storyFilmMusicOfferData;
    if (!offer || (!offer.new_generations && !force)) return;
    const result = await this._runAction(
      "story_film_music_generate",
      { trip_id: this._selectedTripId, force },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Musik konnte nicht erzeugt werden",
      },
    );
    if (!result?.film_music_generated) return null;
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
    return made;
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
    const currency = String(offer.currency || "USD");
    const price = offer.reused
      ? "Alle Abschnitte sind schon erzeugt – ein weiterer Lauf kostet nichts."
      : `${offer.new_generations} von ${offer.sections} Abschnitten sind neu · geschätzt ${escapeHtml(String(offer.estimated_cost))} ${escapeHtml(currency)}`;
    const button =
      canEdit && offer.available && offer.new_generations
        ? actionButton(this._actionCosts(), "story-film-music-generate", `Musik erzeugen (${offer.estimated_cost} ${currency})`)
        : "";
    // §38: what is ordered, from whom, how long, at what price - before
    // the button that spends money is pressed. A single total says
    // nothing about what it buys, and "kostenpflichtig" said afterwards
    // is not a disclosure.
    const facts = [
      ["Modell", String(offer.model || "")],
      [
        "Generierungen",
        `${offer.new_generations} neu${offer.cached ? ` · ${offer.cached} vorhanden` : ""}`,
      ],
      ["Audio", `${Math.round(Number(offer.audio_seconds || 0))} s für ${minutes} Minuten Film`],
      [
        "Preis",
        offer.reused
          ? "0 – nichts Neues"
          : `${offer.new_generations} × ${offer.price_per_generation} ${currency} = ${offer.estimated_cost} ${currency}`,
      ],
      [
        "Abschnitte geplant von",
        // The two values the service actually writes. Anything else is
        // shown as it stands rather than silently read as "arithmetic".
        { arithmetik: "der Verteilung im Film", gemini: "einem Modell" }[
          String(offer.planned_by || "")
        ] || String(offer.planned_by || "—"),
      ],
    ]
      .filter(([, value]) => value)
      .map(
        ([name, value]) =>
          `<li><span>${escapeHtml(name)}</span><strong>${escapeHtml(String(value))}</strong></li>`,
      )
      .join("");
    return `<div class="story-music-plan">
      <div class="story-curation-head"><span class="eyebrow">KI-Musik</span>${button}</div>
      <p class="story-curation-counts">Ein Soundtrack in ${escapeHtml(String(offer.sections))} Abschnitten für rund ${escapeHtml(String(minutes))} Minuten Film.</p>
      <div class="story-motifs">${chips}</div>
      <ul class="story-music-facts">${facts}</ul>
      <p class="hint">${escapeHtml(price)}</p>
      ${
        offer.reused
          ? ""
          : `<p class="hint">Das ist ein kostenpflichtiger Aufruf bei Google und wird pro Generierung abgerechnet – nicht pro Minute.${offer.assets_are_stored ? " Die erzeugten Titel werden gespeichert und beim nächsten Rendern wiederverwendet, du zahlst also einmal dafür." : ""}</p>`
      }
      ${
        offer.available
          ? ""
          : `<p class="hint">${escapeHtml(String(offer.unavailable_reason || "Musik kann gerade nicht erzeugt werden."))}</p>`
      }
      ${offer.price_note ? `<small class="hint">${escapeHtml(String(offer.price_note))}</small>` : ""}
    </div>`;
  },

  /**
   * The offer to make a small copy, beside the film it would copy.
   *
   * Only for a film, never for a copy: copying a copy would compress
   * something already compressed and answer nothing. Which is why this
   * reads the artefact the job produced rather than assuming.
   */
  _renderStoryFilmReviewCopy() {
    const profiles = this._storyFilmProfileTable("review");
    if (!this._canEdit() || !profiles.length) return "";
    // Only beside a film, never beside a copy. Without this the button
    // would offer to copy the copy: the source is read from
    // `results/<job>/roadplanner-trip-film.mp4`, which a review-copy job
    // never produced, so it would fail with a missing file - a confusing
    // way to say "that made no sense".
    if (!this._storyFilmSourceJobId || this._storyFilmSourceIsExcerpt) return "";
    const chosen = this._storyFilmChosen(profiles, this._storyFilmReviewProfile);
    return `<label class="inline-select"><span>Kopie</span><select data-action="story-film-review-profile">
      ${profiles
        .map(
          (entry) =>
            `<option value="${escapeHtml(String(entry.id))}"${chosen === entry.id ? " selected" : ""}>${escapeHtml(String(entry.label))}</option>`,
        )
        .join("")}
    </select></label>
    <button class="secondary-button" type="button" data-action="story-film-review-copy"><ha-icon icon="mdi:content-duplicate"></ha-icon> Review-Kopie erstellen</button>
    ${this._renderStoryFilmAddMusic()}`;
  },

  /**
   * The offer to put the score on, beside the film it goes on.
   *
   * Only when there is music to put on. Offering it with nothing
   * generated would be a button that can only fail, and the failure
   * would read as "the mux is broken" rather than "order the music
   * first".
   */
  _renderStoryFilmAddMusic() {
    const offer = this._storyFilmMusicOfferData;
    if (!this._storyFilmSourceJobId || this._storyFilmSourceIsExcerpt) return "";
    if (!offer) return "";
    // Every section already generated - anything less would put a score
    // on that goes quiet partway through.
    const ready = (offer.section_state || []).length
      && (offer.section_state || []).every((entry) => entry.cached_name);
    if (!ready) return "";
    return `<button class="secondary-button" type="button" data-action="story-film-add-music"><ha-icon icon="mdi:music-note-plus"></ha-icon> Musik auflegen</button>`;
  },

  _renderStoryFilmJobLine() {
    const job = this._rendererAppJob;
    const kind = this._rendererAppKind;
    // Three kinds of work, three sets of words. A table rather than a
    // boolean: the boolean was `isCopy`, and a third kind arriving would
    // have been described as "ein Reisefilm wird gerendert" - close
    // enough to look right and wrong about the one thing that matters,
    // which is how long to expect to wait.
    const WORK = {
      trip_film: {
        running:
          "Ein Reisefilm wird gerade gerendert (%s). Das dauert bei einer ganzen Reise viele Minuten – die Seite darf zwischendurch geschlossen werden.",
        done: "Der zuletzt erzeugte Reisefilm ist fertig",
        download: "Film herunterladen",
        cancelled: "Der Render wurde abgebrochen",
        failed: "Der zuletzt gestartete Reisefilm ist nicht fertig geworden",
      },
      review_copy: {
        running:
          "Eine kleine Kopie des Films wird erstellt (%s). Das ist kein neuer Render – der fertige Film wird nur kleiner gerechnet, und das dauert einige Minuten.",
        done: "Die kleine Kopie ist fertig",
        download: "Kopie herunterladen",
        cancelled: "Die Review-Kopie wurde abgebrochen",
        failed: "Die Review-Kopie ist nicht fertig geworden",
      },
      film_excerpt: {
        running:
          "Ein Prüfausschnitt wird gerendert (%s). Das Paket ist so groß wie bei einem ganzen Film, gezeichnet werden aber nur 60 bis 90 Sekunden – es dauert Minuten, nicht Stunden.",
        done: "Der Prüfausschnitt ist fertig",
        download: "Prüfausschnitt herunterladen",
        cancelled: "Der Prüfausschnitt wurde abgebrochen",
        failed: "Der Prüfausschnitt ist nicht fertig geworden",
      },
      film_music: {
        running:
          "Die Musik wird auf den Film gelegt (%s). Die Bilder werden dabei nur kopiert, das dauert Sekunden bis wenige Minuten.",
        done: "Der Film mit Musik ist fertig",
        download: "Film mit Musik herunterladen",
        cancelled: "Das Auflegen wurde abgebrochen",
        failed: "Die Musik ist nicht auf den Film gekommen",
      },
    };
    const words = WORK[kind];
    if (!job || !words) return "";
    if (!job.terminal) {
      const percent = Math.round((Number(job.progress) || 0) * 100);
      // A render can run for an hour. A stop that only exists as "restart
      // the add-on" is not a stop somebody can find.
      const stop = this._canEdit()
        ? `<div class="button-row"><button class="text-button" type="button" data-action="renderer-app-cancel"${this._rendererAppCancelling ? " disabled" : ""}><ha-icon icon="mdi:stop-circle-outline"></ha-icon> ${this._rendererAppCancelling ? "Wird abgebrochen …" : "Abbrechen"}</button></div>`
        : "";
      const progress = `<span data-renderer-progress="story">${escapeHtml(String(job.state || "läuft"))} · ${percent} %</span>`;
      return `${stop}<small class="story-film-job">${words.running.replace("%s", progress)}</small>`;
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
      return `<small class="story-film-job">${words.done}${made ? ` – erstellt am ${escapeHtml(made)}` : ""}.</small>
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="renderer-app-download"${this._rendererAppDownloading ? " disabled" : ""}><ha-icon icon="mdi:download"></ha-icon> ${this._rendererAppDownloading ? "Wird bereitgestellt …" : words.download}</button>
        ${this._renderStoryFilmReviewCopy()}
      </div>
      ${
        this._rendererAppDownloadUrl
          ? `<a class="renderer-app-download" href="${escapeHtml(this._rendererAppDownloadUrl)}" download="${escapeHtml(this._rendererAppDownloadName || "reisefilm.mp4")}">Bereit – hier speichern</a>`
          : ""
      }`;
    }
    // The renderer says WHY it refused, and saying only "failed" throws
    // that away. A package the installed add-on is too old to read looks
    // exactly like a crash until the reason is printed.
    if (job.state === "cancelled") {
      // Said as what it was. Reporting a stop somebody asked for as a
      // failure sends them looking for a cause that does not exist.
      return `<small class="story-film-job">${words.cancelled}. Es ist nichts kaputt – ein neuer Lauf kann jederzeit gestartet werden.</small>`;
    }
    // The renderer writes `error: {code, message}` - it always has. This
    // read `job.reason || job.detail`, and NEITHER FIELD EXISTS on a
    // status, so every failure this card ever showed said "(failed)" and
    // nothing else, while the reason sat in the file unread. A default
    // that can lie is the mistake this project keeps making; the fields
    // are named after the ones the protocol actually validates.
    const why = cleanText(job.error?.message || "");
    const code = cleanText(job.error?.code || "");
    const said = [why, code && !why.includes(code) ? `(${code})` : ""]
      .filter(Boolean)
      .join(" ");
    return `<small class="story-film-job">${words.failed}.${
      said ? ` ${escapeHtml(said)}` : ` Der Renderer hat keinen Grund hinterlassen (${escapeHtml(String(job.state || "unbekannt"))}).`
    }</small>`;
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
      if (result.quota_exhausted) break;
      const left = Number(result.remaining || 0);
      if (!left) break;
      this._showToast(`Bildauswahl läuft – noch ${left} Tage …`, "info", 4000);
    }
    if (!totals) return;
    const missing = Object.keys(totals.unmet || {}).length;
    if (totals.quota_exhausted) {
      // The run stopped because the day's paid budget is gone, and saying
      // "273 Bilder gewählt" here would report that as a success - the
      // reader would go looking for a fault in the photographs instead of
      // simply coming back tomorrow.
      this._showToast(
        "Tageslimit für die KI-Bildauswahl erreicht – noch nicht angesehene Tage " +
          "bleiben ohne Analyse. Morgen erneut „Bildauswahl erneuern“ drücken; " +
          "Tage ohne Analyse kommen dann zuerst dran.",
        "error",
        12000,
      );
    } else {
      this._showToast(
        `${totals.selected_count} Bilder aus ${totals.pool_count} Kandidaten gewählt` +
          (missing ? ` · ${missing} Tage ohne ihr Hauptmotiv` : ""),
        "success",
        7000,
      );
    }
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
      // The gap nobody could see. The curation chooses what tells a day;
      // the film has room for what that day's importance buys, and those
      // are different numbers. When half the chosen pictures never reach
      // the screen, that belongs in the first three lines of the report
      // rather than nowhere.
      Number.isFinite(Number(found.film_total))
        ? `Kuratiert ${found.curated_total} Bilder → im Film ${found.film_total}`
        : "Filmmenge unbekannt (kein Manifest geladen)",
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
          ` → kuratiert ${stages.selected} → im Film ${
            day.film_photo_budget == null ? "?" : stages.in_film
          }` +
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

  /**
   * What a quality-earned photo allocation would do to this trip.
   *
   * The bar a picture has to clear cannot be chosen in a repository: the
   * scores live here, on this Home Assistant, and only their real
   * distribution can justify a number. So the panel offers the run and
   * the report gets pasted back into the conversation that picks it.
   */
  async _storySimulateAllocation() {
    const result = await this._runAction(
      "media_simulate_allocation",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Simulation konnte nicht erstellt werden",
      },
    );
    if (!result?.allocation_simulation) return;
    this._storyAllocationSimulation = result.allocation_simulation;
    this._render({ preserveScroll: true });
  },

  _renderStoryAllocationSimulation() {
    const found = this._storyAllocationSimulation;
    const canEdit = this._canEdit();
    if (!found) {
      return canEdit
        ? `<button class="text-button" type="button" data-action="story-simulate-allocation"><ha-icon icon="mdi:scale-balance"></ha-icon>Bildzuteilung simulieren (kostenlos)</button>`
        : "";
    }
    const distribution = found.distribution || {};
    const row = (entry) =>
      `<li><span>Schwelle ${escapeHtml(String(entry.threshold))}</span><strong>${escapeHtml(String(entry.total_from_pool))} Bilder · ${escapeHtml(String(entry.days_empty))} leere Tage</strong></li>`;
    return `<div class="story-diagnosis">
      <p class="story-curation-counts"><strong>${escapeHtml(String(found.day_count))} Tage simuliert · heute ${escapeHtml(String(found.old_total))} Bilder im Film</strong></p>
      ${
        distribution.count
          ? `<p class="hint">${escapeHtml(String(distribution.count))} bewertete Bilder · Median ${escapeHtml(String(distribution.median))} von 20 · p75 ${escapeHtml(String(distribution.p75))} · p90 ${escapeHtml(String(distribution.p90))}</p>`
          : `<p class="hint">Für diese Reise sind noch keine Bildanalysen gespeichert - ohne sie kann keine Schwelle bestimmt werden.</p>`
      }
      <ul class="story-diagnosis-list">${(found.candidates || []).map(row).join("")}</ul>
      <p class="hint">Nichts davon ist entschieden: Der vollständige Bericht enthält die Verteilung und jede Schwelle Tag für Tag.</p>
      <div class="button-row">
        <button class="secondary-button" type="button" data-action="story-simulate-allocation-copy"><ha-icon icon="mdi:content-copy"></ha-icon>Bericht kopieren</button>
        <button class="text-button" type="button" data-action="story-simulate-allocation-close">Schließen</button>
      </div>
    </div>`;
  },

  /**
   * What the trip's videos would cost to look at, and what came back.
   *
   * Two steps on purpose. The offer is free and read-only and says the
   * price, the cloud transfer and the number of NEW calls; the run is the
   * one that spends money. Nobody should discover the cost afterwards.
   */
  async _storyVideoOffer() {
    const result = await this._runAction(
      "media_video_offer",
      { trip_id: this._selectedTripId },
      "",
      {
        refresh: false,
        blockUi: false,
        errorMode: "dialog",
        errorTitle: "Die Videos konnten nicht geprüft werden",
      },
    );
    if (!result?.video_offer) return;
    this._storyVideoOfferData = result.video_offer;
    // A run that is still going on the server survives a reload, a locked
    // phone and a closed tab. Whoever opens the card next should see it,
    // and should not be offered a button that would start a second one.
    if (result.video_offer.running) {
      this._storyVideoRunning = true;
      void this._storyVideoWatch();
    }
    this._render({ preserveScroll: true });
  },

  /**
   * The paid run - and, just as important, a card that says it is running.
   *
   * Live report: "Nach einem Klick passiert ich denke nichts." It was
   * running. One window can take a minute - the recording is downloaded,
   * a proxy is cut, Gemini is asked - and twenty of them take the better
   * part of half an hour, during which this card said nothing at all: no
   * busy state, no counter, one toast at the very end. A long action with
   * no sign of life is indistinguishable from a button that does nothing,
   * and the reasonable reaction to a button that does nothing is to press
   * it again, which would have paid for the same analyses twice.
   *
   * So the run reports itself. The progress is not invented: the free,
   * read-only offer is asked again every fifteen seconds and its
   * "Analysen im Cache" is the actual number of stored answers, because
   * the service saves each window as it finishes.
   */
  async _storyVideoAnalyze() {
    if (this._storyVideoRunning) return;
    this._storyVideoRunning = true;
    this._storyVideoStartedAt = Date.now();
    this._storyVideoResult = null;
    this._render({ preserveScroll: true });
    void this._storyVideoWatch();
    try {
      const result = await this._runAction(
        "media_video_analyze",
        { trip_id: this._selectedTripId },
        "KI-Videoanalyse abgeschlossen",
        {
          refresh: false,
          blockUi: false,
          errorMode: "dialog",
          errorTitle: "Die Videoanalyse ist fehlgeschlagen",
        },
      );
      if (result?.video_analysis) this._storyVideoResult = result.video_analysis;
    } finally {
      // Not decided here. `_runAction` returns null for every failure,
      // including the dropped WebSocket that leaves the run going on the
      // server - so the browser cannot tell "finished" from "no longer
      // watching". The offer knows, and it is free.
      await this._storyVideoOffer();
      this._storyVideoRunning = Boolean(this._storyVideoOfferData?.running);
      this._render({ preserveScroll: true });
    }
  },

  /**
   * Ask the free offer for the count while the paid run is going.
   *
   * Deliberately the read-only endpoint: it costs nothing, and its
   * numbers come from the same store the run writes into, so the card
   * cannot show a progress that never happened.
   */
  async _storyVideoWatch() {
    if (this._storyVideoWatching) return;
    this._storyVideoWatching = true;
    // Long, because the run is: twenty windows on a Home Assistant box
    // with a phone-video download each. The loop ends when the server
    // says the run ended, not when a guessed duration is up.
    const deadline = Date.now() + 90 * 60 * 1000;
    try {
      while (Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 15000));
        if (!this.isConnected) return;
        const result = await this._runAction(
          "media_video_offer",
          { trip_id: this._selectedTripId },
          "",
          { refresh: false, blockUi: false, errorTitle: "" },
        ).catch(() => null);
        if (!result?.video_offer) continue;
        const before = this._storyVideoOfferData || {};
        this._storyVideoOfferData = result.video_offer;
        // The server decides whether a run is going. A dropped WebSocket
        // ends the call here while the run continues over there, so the
        // browser's own flag is the wrong thing to loop on.
        const stillRunning = Boolean(result.video_offer.running);
        if (!stillRunning) {
          this._storyVideoRunning = false;
          this._render({ preserveScroll: true });
          return;
        }
        // A counter that ticked up is not a reason to rebuild the page -
        // the whole shadow DOM is replaced on render and the scroll jumps.
        if (Boolean(before.running) !== stillRunning) {
          this._storyVideoRunning = true;
          this._render({ preserveScroll: true });
        } else {
          this._storyVideoPatchProgress();
        }
      }
    } finally {
      this._storyVideoWatching = false;
    }
  },

  _storyVideoPatchProgress() {
    const nodes = this.shadowRoot?.querySelectorAll("[data-story-video-progress]");
    if (!nodes?.length) return false;
    const offer = this._storyVideoOfferData || {};
    const done = Number(offer.windows_cached || 0);
    const total = done + Number(offer.windows_new || 0);
    const minutes = Math.max(
      1,
      Math.round((Date.now() - (this._storyVideoStartedAt || Date.now())) / 60000),
    );
    nodes.forEach((node) => {
      // "10 von 21 fertig" read as "this run did ten in a second" when
      // ten of them were answered by an earlier run. The number is the
      // trip's, so it says so.
      node.textContent = `insgesamt ${done} von ${total} Analysen beantwortet · dieser Lauf seit ${minutes} min`;
    });
    return true;
  },

  _renderStoryVideoAnalysis() {
    const offer = this._storyVideoOfferData;
    const done = this._storyVideoResult;
    const canEdit = this._canEdit();
    const running = Boolean(this._storyVideoRunning || offer?.running);
    // The last run's outcome comes from the STORE, not from the response
    // to the click. A run of eleven windows takes long enough that the
    // WebSocket drops or the page gets reloaded, and with the summary
    // living only in that response, every failure reason went with it -
    // leaving "10 im Cache · 11 neu" and no way to find out why the
    // eleven had not worked without reading a log file.
    const report = done || offer?.last_run;
    const failures = (report?.failed || []).filter(Boolean);
    // Refused is its own outcome. The model looked at the request and
    // declined - which happens to ordinary family recordings, wrongly and
    // routinely. Counting it as "analysed" without saying so would make a
    // clip vanish from the film with nothing to explain it.
    const refused = (report?.refused || []).filter(Boolean);
    const lastRun = report
      ? `<p class="hint"><strong>Letzter Lauf:</strong> ${escapeHtml(String(report.analysed ?? 0))} von ${escapeHtml(String(report.planned ?? report.analysed ?? 0))} analysiert · ${escapeHtml(String(report.segments ?? 0))} Momente gefunden${
          failures.length
            ? ` · <strong>${escapeHtml(String(failures.length))} fehlgeschlagen</strong>`
            : ""
        }${
          refused.length
            ? ` · <strong>${escapeHtml(String(refused.length))} vom Modell abgelehnt</strong>`
            : ""
        }</p>${
          refused.length
            ? `<p class="hint">Abgelehnt heißt: Die KI hat sich die Aufnahme nicht angesehen - das passiert bei privaten Aufnahmen auch dann, wenn nichts daran ist. Sie wird nicht erneut gefragt (das Ergebnis wäre dasselbe) und erscheint nicht als Clip im Film. Die Fotos dieses Tages sind davon unberührt.</p>
               <ul class="story-diagnosis-list">${refused
                 .slice(0, 3)
                 .map(
                   (entry) =>
                     `<li>${escapeHtml(String(entry.name || entry.media_id || "Aufnahme"))}</li>`,
                 )
                 .join("")}</ul>`
            : ""
        }${
          failures.length
            ? // The reason, not just the count. A number without a cause
              // sends somebody to fix the one thing that is not broken.
              `<ul class="story-diagnosis-list">${failures
                .slice(0, 5)
                .map(
                  (entry) =>
                    `<li>${escapeHtml(String(entry.name || entry.media_id || "Aufnahme"))}: ${escapeHtml(String(entry.reason || "kein Grund angegeben"))}</li>`,
                )
                .join("")}${failures.length > 5 ? `<li>… und ${escapeHtml(String(failures.length - 5))} weitere mit denselben oder ähnlichen Gründen</li>` : ""}</ul>`
            : ""
        }`
      : "";
    if (!offer) {
      return canEdit
        ? `<button class="text-button" type="button" data-action="story-video-offer"><ha-icon icon="mdi:movie-search-outline"></ha-icon>Videos prüfen: was würde eine KI-Analyse kosten?</button>`
        : "";
    }
    // Below a cent, "0,00 €" reads like "free" - which it nearly is, but
    // the honest phrasing is the one that does not have to be corrected.
    const price =
      offer.estimated_eur >= 0.01
        ? `rund ${String(offer.estimated_eur).replace(".", ",")} €`
        : "unter 0,01 €";
    return `<div class="story-diagnosis">
      <p class="story-curation-counts"><strong>${escapeHtml(String(offer.videos_found))} Videos gefunden</strong> · ${escapeHtml(String(offer.technically_usable))} technisch brauchbar · ${escapeHtml(String(offer.windows_cached))} Analysen im Cache · ${escapeHtml(String(offer.segments_stored))} gespeicherte Momente</p>
      ${
        offer.enabled
          ? offer.windows_new > 0
            ? `<p class="hint"><strong>${escapeHtml(String(offer.windows_new))} neue Analysen</strong> (${escapeHtml(String(offer.analysis_seconds))} s Video, ${price}). Dabei werden <strong>verkleinerte, tonlose Ausschnitte</strong> an Google/Gemini übertragen - nie das Original, nie mit Ort, Tag oder Namen.</p>`
            : `<p class="hint">Alles ist bereits analysiert. Ein erneuter Lauf würde nichts kosten und nichts ändern.</p>`
          : `<p class="hint">Die KI-Videoanalyse ist ausgeschaltet. Sie überträgt Ausschnitte an Google und wird deshalb bewusst in den Einstellungen eingeschaltet.</p>`
      }
      ${
        (offer.skipped || []).length
          ? `<p class="hint">${escapeHtml(String(offer.skipped.length))} übersprungen: ${escapeHtml((offer.skipped[0] || {}).reason || "")}${offer.skipped.length > 1 ? " u. a." : ""}</p>`
          : ""
      }
      ${
        running
          ? `<p class="hint"><strong>Die Analyse läuft.</strong> <span data-story-video-progress>insgesamt ${escapeHtml(String(offer.windows_cached || 0))} von ${escapeHtml(String(Number(offer.windows_cached || 0) + Number(offer.windows_new || 0)))} Analysen beantwortet</span>. Pro Aufnahme wird zuerst das Original geladen - bei einem Handyvideo sind das schnell ein paar hundert Megabyte, und die erste Zahl bewegt sich deshalb minutenlang nicht. Jede fertige Analyse ist sofort gespeichert: Die Seite darf verlassen werden, und ein Abbruch verliert nur den Rest. Wo der Lauf gerade steht, steht im Home-Assistant-Protokoll.</p>`
          : ""
      }
      ${lastRun}
      <div class="button-row">
        ${
          canEdit && offer.enabled && offer.windows_new > 0 && !running
            ? actionButton(this._actionCosts(), "story-video-analyze", `KI-Videoanalyse starten (${price})`)
            : ""
        }
        <button class="text-button" type="button" data-action="story-video-close">Schließen</button>
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
      ${this._renderStoryLatestFilm()}
      ${this._renderStoryFilm()}
      ${this._renderStoryFilmAdvancedExports()}
      ${this._renderCharacterAssets()}
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
