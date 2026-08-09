/**
 * A paid button must never look like a free one.
 *
 * That is the one safety rule in `action-button.js`, and it was quietly
 * broken: the backend declares actions with underscores
 * (`media_curate_days`), every call site passes the DOM spelling with
 * dashes (`media-curate-days`), and the lookup was a plain index. So it
 * missed - and a miss is not a lost tooltip. It falls through to the
 * "not declared, draw it as an ordinary button" branch, which renders a
 * model-priced action as a bare text button with no cost, no icon and
 * no title.
 *
 * The Python contract test could not catch it: it checks that the paid
 * actions go THROUGH this helper, not what the helper then draws. So
 * this runs the helper.
 */

import assert from "node:assert/strict";
import {
  COST_MODEL,
  actionButton,
  actionCost,
  actionHint,
  actionNotes,
} from "../custom_components/roadplanner_mcp/frontend/lib/action-button.js";

// Shaped exactly like the panel payload: keys as Python writes them.
const COSTS = {
  media_curate_days: {
    cost: COST_MODEL,
    effect: "Lässt Gemini die Fotos bewerten.",
    note: "Verbraucht Kontingent.",
  },
  media_reassign: {
    cost: "recompute",
    effect: "Ordnet Fotos neu zu.",
    note: "Manuelle Zuordnungen bleiben.",
  },
};

function verifyTheDomSpellingFindsTheDeclaration() {
  assert.equal(actionCost(COSTS, "media-curate-days"), COST_MODEL);
  assert.equal(actionCost(COSTS, "media_curate_days"), COST_MODEL);
  assert.match(actionHint(COSTS, "media-curate-days"), /Kontingent/);
  assert.match(actionNotes(COSTS, ["media-curate-days"]), /Kontingent/);
}

function verifyAPaidActionIsDrawnAsAPaidButton() {
  const html = actionButton(COSTS, "media-curate-days", "Bilder auswählen lassen");
  assert.match(html, /action-model/, "eine bezahlte Aktion braucht die bezahlte Klasse");
  assert.match(html, /mdi:auto-awesome/);
  assert.match(html, /title="[^"]*Kontingent/);
}

function verifyAFreeActionStaysFree() {
  const html = actionButton(COSTS, "media-reassign", "Zuordnungen neu berechnen");
  assert.doesNotMatch(html, /action-model/);
  assert.match(html, /text-button/);
}

function verifyAnUndeclaredActionIsAnOrdinaryButton() {
  const html = actionButton(COSTS, "story-select", "Kapitel");
  assert.doesNotMatch(html, /action-model/);
  assert.doesNotMatch(html, /title=/);
  assert.equal(actionNotes(COSTS, ["story-select"]), "");
}

for (const check of [
  verifyTheDomSpellingFindsTheDeclaration,
  verifyAPaidActionIsDrawnAsAPaidButton,
  verifyAFreeActionStaysFree,
  verifyAnUndeclaredActionIsAnOrdinaryButton,
]) {
  check();
}

console.log("Action button tests passed.");
