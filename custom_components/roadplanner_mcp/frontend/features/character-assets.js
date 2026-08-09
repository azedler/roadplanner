import { escapeHtml } from "../lib/core-helpers.js";

/**
 * The camper's picture: upload it, look at it, then let films use it.
 *
 * The film could already draw a character - a small camper following
 * the route on the map, a larger one in the opening. What it never had
 * was a way for one to arrive. An image model would be the obvious
 * route and is deliberately not the first one: it costs money on every
 * attempt and returns a different vehicle each time, and being the SAME
 * vehicle in every film is the entire value of the asset.
 *
 * So the first route needs no provider: somebody uploads a drawing of
 * their own camper. Two views, because the film uses them differently -
 * a small one for the map, where a silhouette has to survive being
 * forty pixels wide, and a clean side view for the opening.
 *
 * **An uploaded picture is not in the film yet.** It waits until
 * somebody has looked at it and said so. That is not ceremony: a wrong
 * picture, silently promoted, is a wrong camper in every film from then
 * on, and nothing about the file would say why.
 */

const KIND_LABELS = { vehicle: "Camper", crew: "Crew" };
const VARIANT_LABELS = { map: "klein für die Karte", side: "groß für den Vorspann" };

export const characterAssetsMixin = {
  _characterState() {
    return this._characterAssets || null;
  },

  /** Free: reads a folder. Loaded once when the film card is shown. */
  _characterLoadOnce() {
    if (this._characterLoadTried) return;
    this._characterLoadTried = true;
    void Promise.resolve().then(() => this._characterLoad());
  },

  async _characterLoad() {
    const result = await this._runAction("character_assets", {}, "", {
      refresh: false,
      blockUi: false,
      errorMode: "silent",
    });
    if (!result?.character_assets) return;
    this._characterAssets = result.character_assets;
    this._render({ preserveScroll: true });
  },

  /** Open the file picker for one role. */
  _characterPick(kind, variant) {
    this._characterUploadContext = { kind, variant };
    const input = this.shadowRoot?.querySelector("#roadplanner-character-input");
    if (!input) return;
    input.value = "";
    input.click();
  },

  async _handleCharacterFileInput(input) {
    const file = input?.files?.[0];
    const context = this._characterUploadContext;
    this._characterUploadContext = null;
    if (!file || !context) return;
    // Read in the browser and send as a data URL. The picture is small
    // by the time it is stored, and the panel already has an
    // authenticated channel - the alternative would be minting a new
    // upload URL whose only guard is being unguessable.
    const image = await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => resolve("");
      reader.readAsDataURL(file);
    });
    if (!image) {
      this._showToast("Die Datei konnte nicht gelesen werden.", "error", 6000);
      return;
    }
    const result = await this._runAction(
      "character_asset_upload",
      { kind: context.kind, variant: context.variant, image },
      "",
      {
        refresh: false,
        errorMode: "dialog",
        errorTitle: "Das Bild konnte nicht übernommen werden",
      },
    );
    if (!result?.character_asset) return;
    this._showToast("Bild hochgeladen – bitte ansehen und bestätigen.", "info", 6000);
    await this._characterLoad();
  },

  async _characterConfirm(filename) {
    const result = await this._runAction(
      "character_asset_confirm",
      { filename },
      "",
      { refresh: false, errorMode: "toast" },
    );
    if (!result?.character_assets) return;
    this._characterAssets = result.character_assets;
    this._showToast("Ab jetzt zeichnet der Film dieses Bild.", "success", 5000);
    this._render({ preserveScroll: true });
  },

  async _characterDiscard(filename) {
    const result = await this._runAction(
      "character_asset_discard",
      { filename },
      "",
      { refresh: false, errorMode: "toast" },
    );
    if (!result?.character_assets) return;
    this._characterAssets = result.character_assets;
    this._render({ preserveScroll: true });
  },

  _renderCharacterAssets() {
    const state = this._characterState();
    const canEdit = this._canEdit();
    if (!state) {
      this._characterLoadOnce();
      return "";
    }
    const confirmed = new Map(
      (state.confirmed || []).map((entry) => [`${entry.kind}-${entry.variant}`, entry]),
    );
    const pending = state.pending || [];
    const slot = (kind, variant) => {
      const key = `${kind}-${variant}`;
      const waiting = pending.find(
        (entry) => entry.kind === kind && entry.variant === variant,
      );
      const has = confirmed.has(key);
      return `<div class="character-slot">
        <span class="eyebrow">${escapeHtml(KIND_LABELS[kind] || kind)} · ${escapeHtml(VARIANT_LABELS[variant] || variant)}</span>
        ${
          waiting
            ? `<figure class="character-candidate">
                 <img src="${escapeHtml(waiting.preview)}" alt="Vorschlag für ${escapeHtml(KIND_LABELS[kind] || kind)}">
                 <figcaption>Wartet auf Bestätigung – noch nicht im Film.</figcaption>
               </figure>
               ${
                 canEdit
                   ? `<div class="button-row">
                        <button class="secondary-button" type="button" data-action="character-confirm" data-filename="${escapeHtml(waiting.filename)}"><ha-icon icon="mdi:check"></ha-icon> Übernehmen</button>
                        <button class="text-button danger-text" type="button" data-action="character-discard" data-filename="${escapeHtml(waiting.filename)}">Verwerfen</button>
                      </div>`
                   : ""
               }`
            : `<small class="hint">${has ? "Im Film in Verwendung." : "Noch kein Bild – der Film zeichnet ein einfaches Symbol."}</small>`
        }
        ${
          canEdit
            ? `<button class="text-button" type="button" data-action="character-upload" data-kind="${escapeHtml(kind)}" data-variant="${escapeHtml(variant)}"><ha-icon icon="mdi:upload-outline"></ha-icon> ${has || waiting ? "Anderes Bild wählen" : "Bild hochladen"}</button>`
            : ""
        }
      </div>`;
    };
    return `<div class="notice neutral"><div>
      <strong>Der Camper im Film</strong>
      <small>Ein PNG mit transparentem Hintergrund, quadratisch. Es wird auf Filmgröße gebracht und der durchsichtige Rand abgeschnitten – damit der Camper in jedem Film gleich groß sitzt.</small>
      <div class="character-slots">
        ${slot("vehicle", "map")}
        ${slot("vehicle", "side")}
      </div>
    </div></div>`;
  },
};
