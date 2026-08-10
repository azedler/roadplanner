/**
 * A ceiling measured against one film length expires with that length.
 *
 * The renderer had a fixed 2 400 s limit, justified in a comment by "the
 * longest film this builds - twenty-five days, about 9 000 frames". That
 * sentence carried its own expiry date. When the film plan stopped packing
 * a day's photographs into ever-fuller collages, the frame count roughly
 * doubled and the constant stayed where it was - so two real renders died
 * past the halfway mark with nothing at the end, which is exactly the
 * failure the number had been doubled to prevent.
 *
 * These checks pin the property rather than the number: the ceiling has to
 * follow the work, and a wedged browser has to be caught by something that
 * can tell "stuck" apart from "slow".
 */
import assert from "node:assert/strict";

import { readFileSync } from "node:fs";

import {
  FILM_LIMITS,
  renderCeilingMs,
} from "../apps/roadplanner_renderer/src/film_limits.mjs";

// 30 fps. The film that broke: roughly ten minutes of chapters plus an
// opening and a closing, against the ~9 000 frames the old ceiling was
// measured on.
const OLD_FILM_FRAMES = 9_000;
const CURRENT_FILM_FRAMES = 18_000;

function verifyTheCeilingFollowsTheFilm() {
  const short = renderCeilingMs(FILM_LIMITS, OLD_FILM_FRAMES);
  const long = renderCeilingMs(FILM_LIMITS, CURRENT_FILM_FRAMES);
  assert.ok(long > short, `a longer film must get longer: ${short} vs ${long}`);
}

function verifyTheFilmThatFailedWouldNowFit() {
  // At 400 ms per frame - three times the 126 ms measured on a developer
  // machine - the film that died at 55 % has room to finish.
  const ceiling = renderCeilingMs(FILM_LIMITS, CURRENT_FILM_FRAMES);
  assert.ok(
    ceiling >= CURRENT_FILM_FRAMES * 300,
    `too tight for a Home Assistant box: ${ceiling} ms`,
  );
  assert.ok(ceiling > 2_400_000, `the old constant must not still be the ceiling: ${ceiling}`);
}

function verifyTheOldConstantIsNowAFloor() {
  // A five-second composition does not get a five-second budget.
  assert.equal(renderCeilingMs(FILM_LIMITS, 1), FILM_LIMITS.renderTimeoutMs);
  assert.equal(renderCeilingMs(FILM_LIMITS, 0), FILM_LIMITS.renderTimeoutMs);
}

function verifyAWedgedRenderIsStillCaught() {
  // The ceiling grew, so the guard against a browser that stopped moving
  // cannot be the ceiling any more. It has to exist, and it has to be far
  // shorter than a legitimate long render.
  assert.ok(FILM_LIMITS.stallTimeoutMs > 0, "no stall watchdog");
  assert.ok(
    FILM_LIMITS.stallTimeoutMs < renderCeilingMs(FILM_LIMITS, CURRENT_FILM_FRAMES),
    "the watchdog must fire before the ceiling on a stuck render",
  );
}

function verifyTheRenderArmsAndRearmsTheWatchdog() {
  // A watchdog nobody re-arms turns a slow render into a failed one, which
  // is the bug this file exists about, one level down.
  const source = readSource();
  assert.ok(source.includes("rearmWatchdog()"), "progress does not re-arm the watchdog");
  assert.ok(source.includes("RENDER_STALLED"), "no stall error code");
  assert.ok(
    source.includes("clearTimeout(watchdog)"),
    "the watchdog outlives the render it guards",
  );
}

function readSource() {
  return readFileSync(
    new URL("../apps/roadplanner_renderer/src/render.mjs", import.meta.url),
    "utf8",
  );
}

for (const check of [
  verifyTheCeilingFollowsTheFilm,
  verifyTheFilmThatFailedWouldNowFit,
  verifyTheOldConstantIsNowAFloor,
  verifyAWedgedRenderIsStillCaught,
  verifyTheRenderArmsAndRearmsTheWatchdog,
]) {
  check();
}

console.log("Renderer film timeout tests passed.");
