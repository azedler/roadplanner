/**
 * The camera move, checked as a move rather than as a picture.
 *
 * The brief asks for two things that pull against each other: an
 * overview, so a day is placed in the whole journey, and a detail view,
 * so the drive can be seen. The film answers both with one shot - the
 * camera glides from one to the other - and the thing that can go wrong
 * with a glide is that it is not one. A cut, a lurch, a rush at the
 * start: none of those are visible in a single frame, only in the
 * relationship between consecutive frames, which is what is measured
 * here.
 *
 * The same argument as the movement tests: the interesting claim is
 * about motion, so the test has to sample motion.
 */
import assert from "node:assert/strict";

import {
  CAPTION_STRIP,
  blendCamera,
  cameraFor,
  cameraTransform,
  onScreen,
} from "../apps/roadplanner_renderer/src/camera.mjs";

const FRAME = { width: 1280, height: 720 };

// A journey shaped like a real one: a wide spread of days, and one day
// that is a small part of it, far from the middle of the whole.
const JOURNEY = [];
for (let step = 0; step <= 60; step += 1) {
  JOURNEY.push([200 + step * 14, 180 + Math.sin(step / 7) * 130]);
}
const DAY = JOURNEY.slice(46, 50);

const overview = cameraFor(FRAME, JOURNEY, { fill: 0.78, maxZoom: 1.6, bottom: CAPTION_STRIP });
const detail = cameraFor(FRAME, DAY, { fill: 0.58, maxZoom: 22, bottom: CAPTION_STRIP });

// --- the caption strip is really kept free ------------------------------

{
  // Every point of the day has to sit above the caption, or the
  // orientation text covers the very route it is describing.
  const usableBottom = FRAME.height - CAPTION_STRIP;
  for (const point of DAY) {
    const [, y] = onScreen(FRAME, detail, point);
    assert.ok(
      y <= usableBottom + 1,
      `Punkt liegt bei y=${y.toFixed(1)} unter dem Beschriftungsstreifen (${usableBottom})`,
    );
    assert.ok(y >= -1, `Punkt liegt bei y=${y.toFixed(1)} über dem Bildrand`);
  }
  // And the same for the overview, which is the shot the recap reads on.
  for (const point of JOURNEY) {
    const [, y] = onScreen(FRAME, overview, point);
    assert.ok(y <= usableBottom + 1 && y >= -1, `Überblickspunkt bei y=${y.toFixed(1)}`);
  }
}

// --- the glide has no jump in it ---------------------------------------

{
  const APPROACH = 40;
  const path = [];
  for (let frame = 0; frame <= APPROACH; frame += 1) {
    path.push(blendCamera(overview, detail, frame / APPROACH));
  }
  // Ends are exactly the two cameras, so nothing snaps into place when
  // the drive begins or when the recap ends.
  assert.deepEqual(path[0], overview, "der Anflug muss auf dem Überblick beginnen");
  assert.ok(
    Math.abs(path[APPROACH].zoom - detail.zoom) < 1e-9 &&
      Math.abs(path[APPROACH].x - detail.x) < 1e-9 &&
      Math.abs(path[APPROACH].y - detail.y) < 1e-9,
    "der Anflug muss genau auf der Tagesansicht enden",
  );

  // Zoom only ever grows here, and it grows by a ratio that never
  // doubles from one frame to the next.
  let worstRatio = 1;
  for (let frame = 1; frame <= APPROACH; frame += 1) {
    const ratio = path[frame].zoom / path[frame - 1].zoom;
    assert.ok(ratio >= 1 - 1e-9, `Zoom springt bei Bild ${frame} zurück`);
    worstRatio = Math.max(worstRatio, ratio);
  }
  assert.ok(worstRatio < 1.35, `Zoom springt um Faktor ${worstRatio.toFixed(2)} je Bild`);

  // The screen speed of a point on the route: the honest measure of a
  // jump, because it folds pan and zoom together. It must never spike
  // against its own average.
  const probe = DAY[0];
  const speeds = [];
  for (let frame = 1; frame <= APPROACH; frame += 1) {
    const [x0, y0] = onScreen(FRAME, path[frame - 1], probe);
    const [x1, y1] = onScreen(FRAME, path[frame], probe);
    speeds.push(Math.hypot(x1 - x0, y1 - y0));
  }
  const mean = speeds.reduce((sum, value) => sum + value, 0) / speeds.length;
  const worst = Math.max(...speeds);
  assert.ok(worst < mean * 2.4, `Bildpunkt springt ${worst.toFixed(1)}px gegen ${mean.toFixed(1)}px Mittel`);

  // Eased at both ends: the move starts and stops at rest instead of
  // switching velocity on, which is what reads as a cut.
  assert.ok(speeds[0] < mean * 0.5, "der Anflug beginnt nicht aus dem Stand");
  assert.ok(speeds[speeds.length - 1] < mean * 0.5, "der Anflug kommt nicht zur Ruhe");
}

// --- zoom is blended as a ratio, not as a number ------------------------

{
  const from = { x: 0, y: 0, zoom: 1 };
  const to = { x: 0, y: 0, zoom: 16 };
  const middle = blendCamera(from, to, 0.5);
  // Half way between 1x and 16x is 4x. Linear interpolation would say
  // 8.5x, and that is what makes an automatic zoom rush and then crawl.
  assert.ok(Math.abs(middle.zoom - 4) < 1e-9, `Mitte des Zooms ist ${middle.zoom}`);
}

// --- the transform agrees with the arithmetic ---------------------------

{
  // `onScreen` is used for labels and the camper, the transform for the
  // map itself. If they disagreed, a label would drift away from the
  // place it names.
  const camera = { x: 300, y: 220, zoom: 3 };
  const point = [340, 260];
  const [x, y] = onScreen(FRAME, camera, point);
  const parts = cameraTransform(FRAME, camera).match(/-?\d+(?:\.\d+)?/g).map(Number);
  const [tx, ty, scale, bx, by] = parts;
  assert.ok(Math.abs(tx + (point[0] + bx) * scale - x) < 1e-9, "Transform und onScreen weichen in x ab");
  assert.ok(Math.abs(ty + (point[1] + by) * scale - y) < 1e-9, "Transform und onScreen weichen in y ab");
}

// --- degenerate input is survivable -------------------------------------

{
  const empty = cameraFor(FRAME, [], { bottom: CAPTION_STRIP });
  assert.equal(empty.zoom, 1);
  assert.ok(Number.isFinite(empty.x) && Number.isFinite(empty.y));
  const single = cameraFor(FRAME, [[100, 100]], { bottom: CAPTION_STRIP });
  const [, y] = onScreen(FRAME, single, [100, 100]);
  assert.ok(y < FRAME.height - CAPTION_STRIP, "ein einzelner Punkt gehört über die Beschriftung");
  const broken = cameraFor(FRAME, [[Number.NaN, 5], [10, 20]], { bottom: CAPTION_STRIP });
  assert.ok(Number.isFinite(broken.x) && Number.isFinite(broken.y) && Number.isFinite(broken.zoom));
}

console.log("Camera move tests passed.");
