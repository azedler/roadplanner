/**
 * One way to draw a button that does something again.
 *
 * The panel had grown nine of them, worded in six different verbs across
 * five cards - auffrischen, neu berechnen, neu bewerten, neu einlesen,
 * synchronisieren, aktualisieren - each invented where it was needed.
 * The live complaint was that there are too many switches for refreshing
 * things, and that is true; but the sharper problem is that the wording
 * described what the code does, when what somebody standing in front of
 * the button needs to know is whether it costs money, whether it
 * overwrites a decision they made, and whether it will take a while.
 *
 * So the words come from the backend, which declares them once
 * (`panel_action_costs.py`), and this draws them the same way every
 * time. A card no longer describes its own button; it names the action
 * and the label, and the explanation follows from what the action *is*.
 *
 * The one visual rule worth having: **a paid action must never look like
 * a free one.** Everything else here is consistency; that one is safety.
 */
import { escapeHtml } from "./core-helpers.js";

export const COST_RECOMPUTE = "recompute";
export const COST_FETCH = "fetch";
export const COST_MODEL = "model";

const ICONS = {
  recompute: "mdi:calculator-variant-outline",
  fetch: "mdi:cloud-download-outline",
  model: "mdi:auto-awesome",
};

/**
 * The hint under, or on, an action - assembled from the declaration.
 *
 * `effect` says what changes, `note` is the caveat that makes it safe to
 * press or the warning that makes it deliberate. Both come from the
 * backend so that the sentence a user reads and the sentence a test
 * checks are the same sentence.
 */
/**
 * The declaration for an action, under either spelling.
 *
 * The backend names actions with underscores (`media_curate_days`); the
 * DOM names them with dashes, and that is the form every attribute in
 * the panel carries. Every call site passes the dashed form, so a
 * straight lookup into the underscore-keyed table missed
 * every entry - and a missed entry is not a missing tooltip, it is a
 * paid button drawn as a free one, which is the single rule this module
 * exists to enforce. Normalising here rather than at the call sites
 * means it cannot be forgotten by the next one.
 */
/**
 * DOM actions that RUN a declared action under a different name.
 *
 * A click handler is free to call whatever backend action it likes, and
 * several do: "Stopps anreichern" runs `prepare_place_enrichment`,
 * the stop form's Park4Night button runs `park4night_lookup`. Their cost
 * is the cost of what they run - but a lookup by their own name finds
 * nothing, so they were drawn as free buttons while billing a provider.
 * This map is the missing half of the underscore/dash normalisation, and
 * the cost test reads it too, so a new alias cannot be added silently.
 */
export const ACTION_ALIASES = {
  "integrity-prepare-locations": "prepare_place_enrichment",
  "stop-p4n-lookup": "park4night_lookup",
  "assistant-send": "assistant_chat",
  "story-video-analyze": "media_video_analyze",
};

const entryFor = (costs, action) => {
  const name = String(action || "");
  const alias = ACTION_ALIASES[name];
  return (
    costs?.[name]
    || costs?.[name.replace(/-/g, "_")]
    || (alias ? costs?.[alias] : null)
    || null
  );
};

export const actionHint = (costs, action) => {
  const entry = entryFor(costs, action);
  if (!entry) return "";
  return [entry.effect, entry.note].filter(Boolean).join(" ");
};

export const actionCost = (costs, action) => entryFor(costs, action)?.cost || "";

/**
 * A button for a repeatable action.
 *
 * `costs` is the table from the panel payload. When an action is not
 * declared there it is drawn as an ordinary button - an edit that does
 * what it says needs no explanation, and explaining everything would
 * bury the few that genuinely need it.
 */
export const actionButton = (costs, action, label, options = {}) => {
  const { busy = false, busyLabel = "", disabled = false, extra = "" } = options;
  const cost = actionCost(costs, action);
  const hint = actionHint(costs, action);
  const paid = cost === COST_MODEL;
  // Paid actions are drawn as secondary buttons with the model icon and
  // carry their cost in the label itself, so the word "Kontingent" or
  // "kostet" is on screen and not only in a tooltip nobody opens on a
  // tablet.
  const className = paid ? "secondary-button action-model" : "text-button";
  const icon = ICONS[cost] || "mdi:refresh";
  const text = busy && busyLabel ? busyLabel : label;
  return `<button
    class="${className}"
    type="button"
    data-action="${escapeHtml(action)}"
    ${busy || disabled ? "disabled" : ""}
    ${hint ? `title="${escapeHtml(hint)}"` : ""}
    ${extra}
  ><ha-icon icon="${icon}"></ha-icon> ${escapeHtml(text)}</button>`;
};

/**
 * The sentence a card shows beside a group of these buttons.
 *
 * Rendered once per card rather than once per button: four hints stacked
 * under four buttons is the same wall of text the cleanup was meant to
 * remove.
 */
export const actionNotes = (costs, actions) => {
  const lines = actions
    .map((action) => {
      const entry = entryFor(costs, action);
      if (!entry) return "";
      return `${entry.effect}${entry.note ? ` ${entry.note}` : ""}`;
    })
    .filter(Boolean);
  if (!lines.length) return "";
  return `<p class="action-notes">${lines.map(escapeHtml).join(" ")}</p>`;
};
