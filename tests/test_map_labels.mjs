/**
 * Place names that can actually be read.
 *
 * Every label used to be drawn: at its point, always to the right, with
 * a halo and nothing else. Two failures follow, and both are the kind a
 * viewer notices without being able to name.
 *
 * Two stops forty kilometres apart are twenty pixels apart on a map of
 * Scandinavia. Two haloed names in the same twenty pixels are not two
 * names - neither can be read, so drawing both is strictly worse than
 * drawing one. And a stop near the right edge ran its name out of the
 * frame, because nothing ever flipped it to the other side.
 */

import assert from "node:assert/strict";
import {
  estimateWidth,
  labelBox,
  placeLabels,
} from "../apps/roadplanner_renderer/src/maplabels.mjs";

const FRAME = { width: 1280, height: 720 };
const label = (text, x, y, extra = {}) => ({ text, x, y, fontSize: 19, ...extra });

function verifyTwoNamesInTheSameSpotBecomeOne() {
  const placed = placeLabels(
    [label("Åsmoarp", 400, 300), label("Smålandet", 404, 306)],
    FRAME,
  );
  assert.equal(placed.length, 1, "zwei Namen übereinander sind kein Name");
  // The first one given wins. Ties must NOT be broken by position: the
  // screen moves, and a label that wins at frame 100 and loses at 101
  // flickers.
  assert.equal(placed[0].text, "Åsmoarp");
}

function verifyThePriorityOrderDecidesAndNotTheScreen() {
  const first = placeLabels([label("Ziel", 400, 300), label("Unterwegs", 404, 306)], FRAME);
  const moved = placeLabels([label("Ziel", 700, 500), label("Unterwegs", 704, 506)], FRAME);
  assert.equal(first[0].text, moved[0].text);
}

function verifyNamesFarApartAreBothDrawn() {
  const placed = placeLabels(
    [label("Kiel", 200, 200), label("Malmö", 200, 400), label("Oslo", 700, 200)],
    FRAME,
  );
  assert.equal(placed.length, 3);
  assert.ok(placed.every((entry) => entry.side === "right"));
}

function verifyANameNearTheRightEdgeFlipsInsteadOfLeaving() {
  const placed = placeLabels([label("Sundsvall", 1240, 300)], FRAME);
  assert.equal(placed.length, 1);
  assert.equal(placed[0].side, "left");
  assert.ok(placed[0].box.right <= FRAME.width, placed[0].box);
  assert.ok(placed[0].box.left >= 0, placed[0].box);
}

function verifyANameThatFitsOnTheRightStaysOnTheRight() {
  const placed = placeLabels([label("Kiel", 300, 300)], FRAME);
  assert.equal(placed[0].side, "right");
  assert.ok(placed[0].box.left > 300);
}

function verifyANameWiderThanTheFrameIsNotMadeWorse() {
  // Flipping would move the clipping from one edge to the other rather
  // than fixing anything, so it stays where a reader expects it.
  const huge = { text: "x".repeat(400), x: 1200, y: 300, fontSize: 19 };
  const placed = placeLabels([huge], FRAME);
  assert.equal(placed.length, 1);
  assert.equal(placed[0].side, "right");
}

function verifyOffScreenNamesAreNotLabelsAtAll() {
  const placed = placeLabels(
    [label("Weit weg", -900, 300), label("Auch weit", 400, 2000), label("Hier", 400, 300)],
    FRAME,
  );
  assert.deepEqual(
    placed.map((entry) => entry.text),
    ["Hier"],
  );
}

function verifyTheOneThatMustBeNamedIsAlwaysDrawn() {
  const placed = placeLabels(
    [label("Nachbar", 400, 300), label("Wolfsschanze", 404, 306, { keep: true })],
    FRAME,
  );
  assert.equal(placed.length, 2);
  assert.ok(placed.some((entry) => entry.text === "Wolfsschanze"));
}

function verifyEmptyAndMissingInputIsNotACrash() {
  assert.deepEqual(placeLabels([], FRAME), []);
  assert.deepEqual(placeLabels(null), []);
  assert.deepEqual(placeLabels([label("  ", 400, 300)], FRAME), []);
}

function verifyTheWidthEstimateIsGenerousRatherThanTight() {
  // Over-estimating costs an early flip; under-estimating puts text
  // outside the frame. Only one of those is visible in a finished film.
  assert.ok(estimateWidth("Kiel", 19) > 4 * 19 * 0.5);
  assert.equal(estimateWidth("", 19), 0);
  const box = labelBox(label("Kiel", 100, 100), "right");
  assert.ok(box.right > box.left && box.bottom > box.top);
}

for (const check of [
  verifyTwoNamesInTheSameSpotBecomeOne,
  verifyThePriorityOrderDecidesAndNotTheScreen,
  verifyNamesFarApartAreBothDrawn,
  verifyANameNearTheRightEdgeFlipsInsteadOfLeaving,
  verifyANameThatFitsOnTheRightStaysOnTheRight,
  verifyANameWiderThanTheFrameIsNotMadeWorse,
  verifyOffScreenNamesAreNotLabelsAtAll,
  verifyTheOneThatMustBeNamedIsAlwaysDrawn,
  verifyEmptyAndMissingInputIsNotACrash,
  verifyTheWidthEstimateIsGenerousRatherThanTight,
]) {
  check();
}

console.log("Map label placement tests passed.");
