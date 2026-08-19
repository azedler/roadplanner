/**
 * A stream is not a soundtrack.
 *
 * A Remotion render ALWAYS writes an AAC track. An empty one measures
 * about -91 dBFS - digital silence with a container around it. The mux
 * asked ffprobe whether an audio stream existed and refused every film
 * that had one, so it refused every film this renderer has ever made:
 *
 *   "Dieser Film hat bereits eine Tonspur - die Musik gehört auf den
 *    stummen Film. (PACKAGE_INVALID)"
 *
 * The advice was worse than the refusal. There is no way to render a
 * silent film here, so "render it without music" asked for a file that
 * cannot exist, and the three paid-for comparison fassungen could never
 * be laid on anything (live report, measured: mean and max both
 * -91.0 dB).
 *
 * The numbers in this file come from ffmpeg, not from a fixture written
 * by whoever wrote the fix: a real silent track and a real tone are
 * generated, measured, and run through the same functions production
 * uses. A test that hard-coded "-91" would have agreed with itself
 * while the parser drifted - which is a shape this repository has
 * shipped before.
 */

import assert from "node:assert/strict";
import { execFile, spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

import {
  AUDIBLE_PEAK_DBFS,
  isAudible,
  muxArgs,
  parseVolume,
  volumeArgs,
} from "../apps/roadplanner_renderer/src/audiomux.mjs";

const execFileAsync = promisify(execFile);
const FFMPEG = process.env.ROADPLANNER_FFMPEG || "ffmpeg";

/** Run an ffmpeg measuring call and hand back what it printed. */
async function measured(file) {
  const text = await new Promise((resolve) => {
    const child = spawn(FFMPEG, volumeArgs(file), { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("error", () => resolve(""));
    child.on("close", () => resolve(stderr));
  });
  return parseVolume(text);
}

async function make(file, audioFilter) {
  await execFileAsync(FFMPEG, [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    "color=c=black:s=320x240:r=25:d=2",
    "-f",
    "lavfi",
    "-i",
    audioFilter,
    "-t",
    "2",
    "-c:v",
    "libx264",
    "-pix_fmt",
    "yuv420p",
    "-c:a",
    "aac",
    "-shortest",
    file,
  ]);
}

async function verifyASilentTrackIsNotASoundtrack(dir) {
  // Exactly what a Remotion render of a film without music produces: a
  // real AAC stream carrying nothing.
  const silent = path.join(dir, "silent.mp4");
  await make(silent, "anullsrc=r=44100:cl=stereo");

  const level = await measured(silent);
  assert.equal(
    typeof level.maxDbfs,
    "number",
    "die Lautstärke einer stummen Spur konnte nicht gemessen werden",
  );
  // Measured, not assumed: whatever ffmpeg says, it must be far under
  // the line, and the FUNCTION must call it inaudible.
  assert.ok(
    level.maxDbfs < AUDIBLE_PEAK_DBFS,
    `eine stumme Spur misst ${level.maxDbfs} dBFS und läge über der Hörbarkeitsgrenze`,
  );
  assert.equal(
    isAudible(level),
    false,
    "eine stumme AAC-Spur wird wieder als vorhandene Musik gewertet",
  );
  return silent;
}

async function verifyARealToneIsOne(dir) {
  const loud = path.join(dir, "tone.mp4");
  await make(loud, "sine=frequency=440:sample_rate=44100");
  const level = await measured(loud);
  assert.ok(
    level.maxDbfs > AUDIBLE_PEAK_DBFS,
    `ein Sinuston misst ${level.maxDbfs} dBFS und gilt als stumm`,
  );
  assert.equal(isAudible(level), true, "hörbare Musik wird nicht mehr erkannt");
}

function verifyAnUnmeasuredTrackAnswersNeither() {
  // The third value, and the reason it exists. `Number(null)` is 0, and
  // 0 dBFS is full scale - an unmeasured file reported as a number would
  // be the loudest possible signal.
  assert.equal(isAudible({ meanDbfs: null, maxDbfs: null }), null);
  assert.equal(isAudible(null), null);
  assert.equal(isAudible({ maxDbfs: Number.NaN }), null);
}

async function verifyTheMuxDropsWhateverTheSourceCarried(dir) {
  // The silent track never needed removing - the mux maps the video and
  // the mixed audio and nothing else. Stated as a check because the
  // refusal was justified by a danger that this mapping already rules
  // out: there was never a way for two soundtracks to end up in one file.
  const args = muxArgs({
    video: path.join(dir, "silent.mp4"),
    sections: [{ path: path.join(dir, "tone.mp4"), startSeconds: 0, seconds: 2 }],
    output: path.join(dir, "out.mp4"),
    filmSeconds: 2,
  });
  const maps = args.filter((entry, index) => args[index - 1] === "-map");
  assert.deepEqual(
    maps,
    ["0:v:0", "[music]"],
    "der Mux übernimmt jetzt eine Spur aus der Quelle - genau das war nie der Fall",
  );
  assert.ok(args.includes("-c:v") && args[args.indexOf("-c:v") + 1] === "copy", args.join(" "));
}

async function verifyTheGuardListensInsteadOfCounting() {
  // The production path, read rather than re-implemented: the mux must
  // decide on a MEASUREMENT, and unknown must not block it.
  const source = await fs.readFile(
    new URL("../apps/roadplanner_renderer/src/render.mjs", import.meta.url),
    "utf8",
  );
  const body = source.slice(source.indexOf("export async function muxFilmMusic"));
  const guard = body.slice(0, body.indexOf("const list ="));
  const code = guard
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .split("\n")
    .map((line) => line.replace(/\/\/.*$/, ""))
    .join("\n");
  assert.ok(
    /measureVolume\(sourcePath\)/.test(code),
    "der Mux misst die Quelle nicht mehr, er zählt wieder Streams",
  );
  assert.ok(
    /audible === true/.test(code),
    "nur eine GEMESSENE Tonspur darf den Mux verweigern - unbekannt nicht",
  );
}

async function main() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-audible-"));
  try {
    await verifyASilentTrackIsNotASoundtrack(dir);
    await verifyARealToneIsOne(dir);
    verifyAnUnmeasuredTrackAnswersNeither();
    await verifyTheMuxDropsWhateverTheSourceCarried(dir);
    await verifyTheGuardListensInsteadOfCounting();
    console.log("Audible-not-merely-present tests passed.");
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

await main();
