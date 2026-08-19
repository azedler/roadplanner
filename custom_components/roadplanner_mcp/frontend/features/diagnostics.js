/**
 * Where the development tooling lives now.
 *
 * Roadplanner grew a lot of instruments while the film was being built,
 * and they all ended up on the same surface as the trip itself: the
 * music cue sheet, the A/B/C architecture prototype, the allocation
 * simulation, the video-analysis cost check, the renderer environment
 * probe. Each one earned its place while it was being used. Together
 * they made the product read as a box of tools somebody left open.
 *
 * Nothing here is removed - it is moved. These blocks are the same
 * functions the story editor used to call; they are simply called from a
 * place somebody has to go looking for, which is the correct amount of
 * prominence for an instrument.
 *
 * Grouped by the question they answer rather than by which module they
 * happen to live in: somebody arriving here is asking "why does the film
 * look like that?" or "what did the music cost?", not "which mixin owns
 * this?".
 */

export const diagnosticsMixin = {
  _renderDiagnostics() {
    const trip = this._selectedTripId;
    return `
      <section class="panel-card">
        <div class="section-heading compact">
          <div>
            <span class="eyebrow">Technik</span>
            <h2>Diagnose</h2>
            <p>Werkzeuge aus der Entwicklung. Für den normalen Reisebetrieb
            wird hier nichts gebraucht - alles hier beantwortet die Frage
            „warum ist das so geworden?“.</p>
          </div>
        </div>
        ${
          trip
            ? ""
            : `<div class="empty-inline"><ha-icon icon="mdi:information-outline"></ha-icon><div><strong>Keine Reise ausgewählt</strong><span>Die meisten Prüfungen beziehen sich auf eine Reise.</span></div></div>`
        }
      </section>

      <details data-section="diagnostics-film" class="panel-card">
        <summary><span><ha-icon icon="mdi:movie-search-outline"></ha-icon>Film</span><small>Szenenplan, Bildzuteilung, Prüfausschnitt, Review-Kopie</small></summary>
        <div class="assistant-technical-content">
          ${this._renderStoryTripDiagnosis()}
          ${this._renderStoryAllocationSimulation()}
          ${this._renderStoryFilmExcerpt()}
          ${this._renderStoryFilmAdvancedExports()}
        </div>
      </details>

      <details data-section="diagnostics-music" class="panel-card">
        <summary><span><ha-icon icon="mdi:music-clef-treble"></ha-icon>Musik</span><small>Musikplan, Abschnitte, Provider und Kosten, A/B/C-Prototyp</small></summary>
        <div class="assistant-technical-content">
          ${this._renderStoryFilmMusicPlan()}
          ${this._renderStoryMusicPrototype()}
        </div>
      </details>

      <details data-section="diagnostics-video" class="panel-card">
        <summary><span><ha-icon icon="mdi:video-check-outline"></ha-icon>Video</span><small>Analyse, Clips, Kosten einer KI-Prüfung</small></summary>
        <div class="assistant-technical-content">
          ${this._renderStoryVideoAnalysis()}
        </div>
      </details>

      <details data-section="diagnostics-renderer" class="panel-card">
        <summary><span><ha-icon icon="mdi:server-network-outline"></ha-icon>Renderer</span><small>Umgebung, Austauschordner, Testrender, Auftragsliste</small></summary>
        <div class="assistant-technical-content">
          ${this._renderRendererApp()}
          ${this._renderRemotionSpike()}
        </div>
      </details>
    `;
  },
};

export default diagnosticsMixin;
