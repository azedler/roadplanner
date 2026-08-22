/**
 * Text that stays inside the frame, on the language that breaks layouts.
 *
 * Reported from the first full watch-through: "Text läuft teilweise
 * unten aus dem Bild." German is where this happens - the words are
 * long, they do not break, and a title that fits in English wraps to
 * three lines and puts the third below the frame.
 *
 * These checks use real sentences of the kind the story director
 * actually writes, not "lorem ipsum" of a convenient length.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  FRAME_HEIGHT,
  MAX_TEXT_WIDTH,
  SAFE_BOTTOM,
  SAFE_SIDE,
  captionBox,
  charsPerLine,
  fitText,
  lineCount,
} from "../apps/roadplanner_renderer/src/textfit.mjs";

/**
 * V7 (#375): the darkening behind the caption reaches the frame's edges.
 *
 * The gradient and the text's own width limit sat on ONE element, so the
 * scrim stopped at MAX_TEXT_WIDTH - 1088 of 1280 - and the remaining
 * 192 px stayed bright. On screen that is a hard vertical edge running
 * down through the photograph; it was measured in the rendered film at
 * exactly 85% of the frame width, which is 1088/1280.
 */
function verifyTheScrimIsAsWideAsTheFrame() {
  const composition = readFileSync(
    new URL("../apps/roadplanner_renderer/src/remotion/RoadplannerTripFilm.tsx", import.meta.url),
    "utf-8",
  );
  const caption = composition
    .split("const Caption: React.FC")[1]
    .split("const StoryCaption")[0];
  const scrim = caption.split("linear-gradient")[0];
  assert.match(scrim, /width: "100%"/, "the scrim element has to span the whole frame");
  assert.match(scrim, /boxSizing: "border-box"/, "or its padding would push it wider than the frame");
  // And the width limit has moved off it, onto the text inside.
  assert.ok(
    !/maxWidth: MAX_TEXT_WIDTH[\s\S]{0,200}linear-gradient/.test(caption),
    "the text width limit must not sit on the element that carries the gradient",
  );
  const afterGradient = caption.split("linear-gradient")[1];
  assert.match(
    afterGradient,
    /maxWidth: MAX_TEXT_WIDTH/,
    "the text still keeps the width it was fitted against",
  );
  // 1088 of 1280 - the number the edge was measured at, stated so the
  // relationship that caused it cannot come back unnoticed.
  assert.equal(MAX_TEXT_WIDTH, 1088);
}

const SHORT = "Erster Halt am See.";
const NORMAL =
  "Nach dem Aufbruch ging es über die Küstenstraße bis zum Fährhafen.";
const LONG =
  "Nach unserem Start bogen wir ab zur Älgsafari, wo uns mächtige Elche " +
  "und Bisons direkt auf dem Weg begegneten, und am Abend machten wir es " +
  "uns am Lagerfeuer gemütlich.";
const MONSTER =
  "Übernachtungsmöglichkeitensuche und Sehenswürdigkeitenbesichtigung " +
  "bei Nationalparkverwaltungsgebäuden";

function verifyTheSafeAreaIsRealRoomNotADecoration() {
  // The bottom inset is the one the complaint was about: a player's
  // controls, a phone's home indicator and the reported overflow all
  // live there.
  assert.ok(SAFE_BOTTOM >= 64, `unterer Rand zu knapp: ${SAFE_BOTTOM}`);
  assert.ok(SAFE_SIDE >= 80, `seitlicher Rand zu knapp: ${SAFE_SIDE}`);
  assert.equal(MAX_TEXT_WIDTH, 1280 - SAFE_SIDE * 2);
  assert.ok(MAX_TEXT_WIDTH < 1280, "die Textbreite ist schmaler als das Bild");
}

function verifyEveryRealisticCaptionFitsWithoutBeingCut() {
  const box = captionBox();
  for (const text of [SHORT, NORMAL, LONG]) {
    const fitted = fitText(text, box);
    assert.equal(fitted.text, text.trim(), "der Text wird nie beschnitten");
    assert.ok(
      fitted.fontSize >= box.minFontSize && fitted.fontSize <= box.maxFontSize,
      `Schriftgröße außerhalb der Grenzen: ${fitted.fontSize}`,
    );
    const height = fitted.lines * fitted.fontSize * box.lineHeight;
    assert.ok(
      height + SAFE_BOTTOM <= FRAME_HEIGHT,
      `${text.slice(0, 20)}… ragt unten heraus: ${height}px`,
    );
  }
}

function verifyAShorterTextGetsALargerSize() {
  const box = captionBox();
  const short = fitText(SHORT, box);
  const long = fitText(LONG, box);
  assert.ok(
    short.fontSize >= long.fontSize,
    `kurzer Text sollte nicht kleiner gesetzt werden: ${short.fontSize} vs ${long.fontSize}`,
  );
  assert.ok(short.lines <= long.lines, "und nicht mehr Zeilen brauchen");
}

function verifyTextThatCannotFitSaysSoInsteadOfOverflowing() {
  // A whole paragraph in a caption-sized box. The honest answer is
  // "this does not belong over a photograph" - and saying so is what
  // lets the planner give it its own scene instead of clamping it to
  // two lines and hoping.
  //
  // Note what does NOT trigger this: two long compounds still fit in
  // three lines at 29px. The box is generous on purpose; what it will
  // not do is pretend.
  const paragraph = `${LONG} ${LONG}`;
  const fitted = fitText(paragraph, captionBox());
  assert.equal(fitted.fits, false, "ein zu langer Text muss sich melden");
  assert.ok(fitted.text.length > 0, "und trotzdem vollständig bleiben");
}

function verifyAnUnbreakableWordIsCountedHonestly() {
  // "Übernachtungsmöglichkeitensuche" does not break. A character-count
  // estimate that assumes words split anywhere reports one line too few
  // and puts the last one off the screen.
  const naive = Math.ceil(MONSTER.length / charsPerLine(30, MAX_TEXT_WIDTH));
  const real = lineCount(MONSTER, 30, MAX_TEXT_WIDTH);
  assert.ok(real >= naive, `Wortumbruch wird unterschätzt: ${real} < ${naive}`);
  assert.ok(real >= 2, "zwei Komposita passen nicht in eine Zeile");
}

function verifyEmptyTextIsNotAScene() {
  const fitted = fitText("   ", captionBox());
  assert.equal(fitted.lines, 0);
  assert.equal(fitted.text, "");
  assert.equal(fitted.fits, true);
}

function verifyTheEstimateErrsTowardsTooWide() {
  // Pessimism is the point: guessing a text is wider than it is costs a
  // font size, guessing it is narrower puts it off the screen. At 30px
  // in a 1088px box, a humanist sans fits roughly 65-70 characters; the
  // estimate must not promise more than that.
  const perLine = charsPerLine(30, MAX_TEXT_WIDTH);
  assert.ok(perLine <= 72, `zu optimistisch: ${perLine} Zeichen je Zeile`);
  assert.ok(perLine >= 50, `zu pessimistisch: ${perLine} Zeichen je Zeile`);
}

for (const check of [
  verifyTheSafeAreaIsRealRoomNotADecoration,
  verifyEveryRealisticCaptionFitsWithoutBeingCut,
  verifyAShorterTextGetsALargerSize,
  verifyTextThatCannotFitSaysSoInsteadOfOverflowing,
  verifyAnUnbreakableWordIsCountedHonestly,
  verifyEmptyTextIsNotAScene,
  verifyTheEstimateErrsTowardsTooWide,
  verifyTheScrimIsAsWideAsTheFrame,
]) {
  check();
}

console.log("Text safe area tests passed.");
