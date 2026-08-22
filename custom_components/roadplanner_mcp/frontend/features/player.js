import { escapeHtml } from "../lib/core-helpers.js";

/**
 * The kitchen tablet.
 *
 * Roadplanner has always been an editor: something you sit down at and
 * work in. This is the other half - a screen mounted on a wall that
 * shows the trip and keeps showing it. The two want opposite things, so
 * they are two modes rather than one screen with the editing hidden.
 *
 * Three decisions shape everything here:
 *
 * - **The mode is a device preference, not trip data.** The tablet stays
 *   in the player and the desktop stays in the editor, at the same time,
 *   on the same trip. That can only be true if the choice lives in the
 *   browser - so it is localStorage, and nothing in the roadbook changes
 *   when somebody switches.
 * - **Autoplay is asked for, never insisted on.** A film with music is a
 *   film with sound, and every browser is entitled to refuse to start it
 *   unprompted. The refusal is not an error and not something to work
 *   around: it becomes one large friendly button, which is exactly one
 *   tap, and after that tap the loop runs.
 * - **The loop is the video element's own.** `loop` on the tag, not a
 *   timer watching for the end and calling play() again. A timer would
 *   have to survive a locked screen, a suspended tab and a paused
 *   process; the attribute is handled by the part of the browser whose
 *   job that is.
 */

const MODE_KEY = "roadplanner.ui-mode";
const TRIP_KEY = "roadplanner.player-trip";
const MODE_PLAYER = "player";
const MODE_EDITOR = "editor";

/** How long the controls stay up after somebody touches the screen. */
const CONTROLS_VISIBLE_MS = 4000;

/** Local preferences, defensively - a locked-down browser has no storage. */
function readPreference(key) {
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writePreference(key, value) {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    /* A browser that refuses storage still gets a working player, it
       just forgets the mode when the page reloads. That is a smaller
       failure than refusing to render. */
  }
}

export const playerMixin = {
  /**
   * Whether this device wants the player - ONE source, in memory.
   *
   * It used to be read straight out of localStorage on every render, and
   * a live diagnostic that asked the panel for `_playerMode` got
   * `undefined` back while the player was plainly on screen. Two answers
   * to one question, one of which was a field nobody had ever created.
   * The preference is now loaded once and kept here; storage is where it
   * survives a reload, not where it is looked up.
   */
  _playerModeActive() {
    if (this._playerMode === undefined) {
      this._playerMode = readPreference(MODE_KEY) === MODE_PLAYER;
    }
    return this._playerMode;
  },

  _playerSetMode(mode) {
    this._playerMode = mode === MODE_PLAYER;
    writePreference(MODE_KEY, this._playerMode ? MODE_PLAYER : MODE_EDITOR);
    // A mode switch is not a data change: nothing is saved, nothing is
    // refreshed from the server, the same trip stays selected.
    this._playerAutoplayBlocked = false;
    this._playerControlsVisible = true;
    this._render();
    if (this._playerMode) void this._playerLoadFilm();
  },

  /**
   * Which trip the player shows: the selected one, always.
   *
   * It used to be "the last trip chosen on THIS device", kept in
   * localStorage, and honoured whenever that trip still existed. The
   * result on a live system: after a reload the player stood there with
   * a TEST trip's title over the sentence "Für diese Reise wurde noch
   * kein Reisefilm erstellt", while the real journey - whose film had
   * just been rendered - was the selected one. Read on a wall-mounted
   * tablet, that reads as "the film is gone".
   *
   * A remembered trip that is not the selected one is not a preference,
   * it is a second answer to a question that already has one.
   */
  _playerTripId() {
    return this._selectedTripId || "";
  },

  /**
   * Drop a remembered player trip left behind by an older version.
   *
   * Called once when the panel has trips: the key is gone from the code,
   * so it must also go from the browsers that still carry it - otherwise
   * it sits there forever as a thing that looks like state.
   */
  _playerForgetStoredTrip() {
    if (this._playerTripForgotten) return;
    this._playerTripForgotten = true;
    if (readPreference(TRIP_KEY)) writePreference(TRIP_KEY, "");
  },

  async _playerLoadFilm() {
    this._playerForgetStoredTrip();
    const tripId = this._playerTripId();
    // Which trip the answer is ABOUT. Without it, "no film for this trip"
    // was printed under whatever title the next render happened to
    // compute - the exact sentence that sent somebody looking for a film
    // that was never missing.
    this._playerFilmTripId = tripId;
    if (!tripId) {
      this._playerFilm = null;
      this._playerLoaded = true;
      this._render();
      return;
    }
    const result = await this._runAction(
      "player_latest_film",
      { trip_id: tripId },
      "",
      { refresh: false, blockUi: false, errorTitle: "" },
    ).catch(() => null);
    // A trip switch while the request was in flight: the answer belongs
    // to the trip it was asked for, and that is no longer this screen.
    if (tripId !== this._playerTripId()) return;
    // `null` means "no film", which is a state and not a failure. The
    // difference from "we have not looked yet" is what `_playerLoaded`
    // carries: a spinner that never resolves is the worst of the three.
    this._playerFilm = result?.player_latest_film || null;
    this._playerLoaded = true;
    this._render();
    if (this._playerFilm) this._playerTryAutoplay();
  },

  /** The player follows the selected trip, or shows nothing at all. */
  _playerTripChanged() {
    this._playerFilm = null;
    this._playerFilmTripId = "";
    this._playerLoaded = false;
    this._playerAutoplayBlocked = false;
  },

  /**
   * Ask the browser to start. If it says no, say so plainly.
   *
   * No muted-autoplay trick: starting a travel film silently and hoping
   * somebody notices the speaker icon would be worse than asking. The
   * film has music because somebody paid for music.
   */
  _playerTryAutoplay() {
    const video = this.shadowRoot?.querySelector("[data-player-video]");
    if (!video) return;
    const attempt = video.play();
    if (!attempt || typeof attempt.catch !== "function") return;
    attempt
      .then(() => {
        if (!this._playerAutoplayBlocked) return;
        this._playerAutoplayBlocked = false;
        this._render();
      })
      .catch(() => {
        this._playerAutoplayBlocked = true;
        this._render();
      });
  },

  _playerStart() {
    const video = this.shadowRoot?.querySelector("[data-player-video]");
    if (!video) return;
    this._playerAutoplayBlocked = false;
    const attempt = video.play();
    if (attempt && typeof attempt.catch === "function") attempt.catch(() => {});
    this._playerShowControls();
    this._render();
  },

  _playerTogglePlay() {
    const video = this.shadowRoot?.querySelector("[data-player-video]");
    if (!video) return;
    if (video.paused) {
      const attempt = video.play();
      if (attempt && typeof attempt.catch === "function") attempt.catch(() => {});
    } else {
      video.pause();
    }
    this._playerShowControls();
  },

  _playerToggleMute() {
    const video = this.shadowRoot?.querySelector("[data-player-video]");
    if (!video) return;
    // Kept for the session rather than persisted: somebody who muted the
    // film for one phone call has not decided that this trip is silent.
    this._playerMuted = !video.muted;
    video.muted = this._playerMuted;
    this._playerShowControls();
    this._render();
  },

  _playerRestart() {
    const video = this.shadowRoot?.querySelector("[data-player-video]");
    if (!video) return;
    video.currentTime = 0;
    const attempt = video.play();
    if (attempt && typeof attempt.catch === "function") attempt.catch(() => {});
    this._playerShowControls();
  },

  _playerFullscreen() {
    const stage = this.shadowRoot?.querySelector("[data-player-stage]");
    if (!stage) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen?.();
      return;
    }
    void stage.requestFullscreen?.().catch(() => {});
  },

  /**
   * Show the controls, then let them fade.
   *
   * A mounted tablet showing a permanent row of buttons is a kiosk with
   * a toolbar. The film is the content; everything else appears when
   * somebody reaches for it.
   */
  _playerShowControls() {
    if (this._playerControlsTimer) window.clearTimeout(this._playerControlsTimer);
    if (!this._playerControlsVisible) {
      this._playerControlsVisible = true;
      this._render({ preserveScroll: true });
    }
    this._playerControlsTimer = window.setTimeout(() => {
      this._playerControlsVisible = false;
      // Only while something is actually playing. Hiding the controls of
      // a paused film leaves a still image nobody can start.
      const video = this.shadowRoot?.querySelector("[data-player-video]");
      if (video && !video.paused) this._render({ preserveScroll: true });
      else this._playerControlsVisible = true;
    }, CONTROLS_VISIBLE_MS);
  },

  _renderPlayer() {
    const film = this._playerFilm;
    // Loaded means: loaded FOR THE TRIP ON SCREEN. The old flag only said
    // that some answer had once arrived, so a trip switch left the
    // previous trip's answer standing under the new trip's name.
    const loaded =
      Boolean(this._playerLoaded) && this._playerFilmTripId === this._playerTripId();
    const trips = this._data?.trips?.trips || [];
    const tripId = this._playerTripId();
    const trip = trips.find((entry) => entry?.id === tripId);
    const title = trip?.title || this._data?.summary?.trip?.title || "Reisefilm";
    return `<div class="player-shell${this._playerControlsVisible === false ? " idle" : ""}" data-player-stage>
      <div class="player-chrome">
        <div class="player-title">
          <strong>${escapeHtml(String(title))}</strong>
          ${
            film && Number(film.duration_seconds) > 0
              ? `<small>${escapeHtml(this._playerClock(film.duration_seconds))}${film.has_music ? " · mit Musik" : ""}</small>`
              : ""
          }
        </div>
        <button class="icon-button" type="button" data-action="player-leave" aria-label="Player verlassen" title="Player verlassen">
          <ha-icon icon="mdi:pencil-outline"></ha-icon>
        </button>
      </div>
      ${loaded ? this._renderPlayerStage(film) : this._renderPlayerLoading()}
    </div>`;
  },

  _renderPlayerLoading() {
    return `<div class="player-message"><div class="spinner"></div><strong>Der Reisefilm wird gesucht</strong></div>`;
  },

  _renderPlayerStage(film) {
    if (!film || !film.url) {
      return `<div class="player-message">
        <ha-icon icon="mdi:movie-off-outline"></ha-icon>
        <strong>Für diese Reise wurde noch kein Reisefilm erstellt.</strong>
        <span>Im Editor lässt sich einer erzeugen – das dauert je nach Größe einige Minuten bis gut eine Stunde.</span>
        <button class="secondary-button" type="button" data-action="player-leave">Zum Editor</button>
      </div>`;
    }
    return `<div class="player-stage">
      <video
        data-player-video
        src="${escapeHtml(String(film.url))}"
        loop
        playsinline
        preload="metadata"
        ${this._playerMuted ? "muted" : ""}
        controls
      ></video>
      ${
        this._playerAutoplayBlocked
          ? `<button class="player-start" type="button" data-action="player-start">
              <ha-icon icon="mdi:play-circle-outline"></ha-icon>
              <span>Film starten</span>
              <small>Der Browser startet Filme mit Ton erst nach einer Berührung.</small>
            </button>`
          : ""
      }
      <div class="player-controls">
        <button class="icon-button" type="button" data-action="player-play" aria-label="Abspielen oder anhalten"><ha-icon icon="mdi:play-pause"></ha-icon></button>
        <button class="icon-button" type="button" data-action="player-mute" aria-label="${this._playerMuted ? "Ton einschalten" : "Ton ausschalten"}"><ha-icon icon="${this._playerMuted ? "mdi:volume-off" : "mdi:volume-high"}"></ha-icon></button>
        <button class="icon-button" type="button" data-action="player-restart" aria-label="Von vorn"><ha-icon icon="mdi:restart"></ha-icon></button>
        <button class="icon-button" type="button" data-action="player-fullscreen" aria-label="Vollbild"><ha-icon icon="mdi:fullscreen"></ha-icon></button>
      </div>
    </div>`;
  },

  _playerClock(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    const minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, "0")} min`;
  },
};

export default playerMixin;
