/**
 * The two copies a recording is turned into, and what must never travel.
 *
 * A phone video is the wrong thing to work with twice over: far too
 * large to send to a model, and in whatever codec, frame rate and
 * rotation the camera felt like.
 *
 * These checks pin the three properties that are not about quality:
 * the original is never touched, metadata never travels, and audio is
 * off unless somebody said otherwise.
 */

import assert from "node:assert/strict";
import {
  ANALYSIS_FPS,
  ANALYSIS_HEIGHT,
  RENDER_FPS,
  analysisProxyArgs,
  proxyCacheKey,
  renderProxyArgs,
} from "../apps/roadplanner_renderer/src/videoproxy.mjs";

const SOURCE = "/tmp/original.mov";
const SEGMENT = { startSeconds: 42.5, durationSeconds: 6 };

function verifyTheOriginalIsNeverTouched() {
  // The source of these files is somebody's photo library. There must be
  // no way to ask this module to write back into one.
  for (const args of [
    analysisProxyArgs({ source: SOURCE, output: "/tmp/a.mp4" }),
    renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4" }),
  ]) {
    assert.equal(args[args.length - 1] !== SOURCE, true);
    assert.equal(args.filter((value) => value === SOURCE).length, 1);
    assert.doesNotMatch(args.join(" "), /-i \/tmp\/original\.mov.*\/tmp\/original\.mov/);
  }
}

function verifyMetadataDoesNotTravel() {
  // The analysis proxy goes to a cloud provider. Location data attached
  // to a family video has to be a decision, never a default.
  for (const args of [
    analysisProxyArgs({ source: SOURCE, output: "/tmp/a.mp4" }),
    renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4" }),
  ]) {
    const joined = args.join(" ");
    assert.match(joined, /-map_metadata -1/);
    assert.match(joined, /-map_chapters -1/);
  }
}

function verifyTheAnalysisCopyIsSmallAndSilent() {
  const args = analysisProxyArgs({ source: SOURCE, output: "/tmp/a.mp4" }).join(" ");
  // Muted: the audio of a family video is conversation, and sending it
  // to a provider to find out whether a moment looks good is not a
  // trade anybody agreed to.
  assert.match(args, /-an/);
  assert.match(args, new RegExp(`scale=-2:${ANALYSIS_HEIGHT}`));
  assert.match(args, new RegExp(`-r ${ANALYSIS_FPS}`));
  assert.ok(ANALYSIS_HEIGHT < 720, "der Analyseproxy ist kleiner als der Film");
  assert.ok(ANALYSIS_FPS < RENDER_FPS);
}

function verifyTheRenderCopyIsSilentUnlessAsked() {
  const silent = renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4" }).join(" ");
  assert.match(silent, /-an/);
  // Opt-in, and only opt-in.
  const withSound = renderProxyArgs({
    source: SOURCE,
    output: "/tmp/r.mp4",
    muted: false,
  }).join(" ");
  assert.doesNotMatch(withSound, /-an/);
  assert.match(withSound, /-c:a aac/);
}

function verifyRotationIsBakedIntoThePixels() {
  // The composition draws frames and never reads a rotation entry, so a
  // clip that relies on one arrives sideways.
  assert.match(
    renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4", rotation: 90 }).join(" "),
    /transpose=1/,
  );
  assert.match(
    renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4", rotation: 270 }).join(" "),
    /transpose=2/,
  );
  assert.match(
    renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4", rotation: 180 }).join(" "),
    /transpose=1,transpose=1/,
  );
  const upright = renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4" }).join(" ");
  assert.doesNotMatch(upright, /transpose/);
  // ...and the flag must not survive into the file, or a player turns
  // the picture a second time.
  assert.match(upright, /rotate=0/);
}

function verifyASegmentSeeksFastThenExactly() {
  // -ss before -i seeks by keyframe and is fast; after -i it is exact
  // and slow. Both, in that order. The wrong way round is how a clip
  // starts two seconds early.
  const args = renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4", segment: SEGMENT });
  const inputAt = args.indexOf("-i");
  const before = args.slice(0, inputAt);
  const after = args.slice(inputAt);
  assert.ok(before.includes("-ss"), "grober Sprung vor dem Input");
  assert.ok(after.includes("-ss"), "genauer Schnitt nach dem Input");
  assert.match(args.join(" "), /-t 6\.000/);
  // A clip that starts at the beginning needs no seek at all.
  const whole = renderProxyArgs({ source: SOURCE, output: "/tmp/r.mp4" });
  assert.ok(!whole.includes("-ss"));
}

function verifyTheHeightIsAlwaysEven() {
  // H.264 cannot encode an odd height, and the error it gives says
  // nothing about the cause.
  for (const height of [361, 719, 355]) {
    const args = analysisProxyArgs({ source: SOURCE, output: "/tmp/a.mp4", height }).join(" ");
    const [, value] = args.match(/scale=-2:(\d+)/);
    assert.equal(Number(value) % 2, 0, `${height} → ${value}`);
  }
}

function verifyTheCacheKeyMovesOnlyWithWhatChangesTheFile() {
  const base = proxyCacheKey({ contentHash: "abc", segment: SEGMENT, profile: "render" });
  assert.equal(base, proxyCacheKey({ contentHash: "abc", segment: SEGMENT, profile: "render" }));
  assert.notEqual(base, proxyCacheKey({ contentHash: "xyz", segment: SEGMENT, profile: "render" }));
  assert.notEqual(base, proxyCacheKey({ contentHash: "abc", segment: SEGMENT, profile: "analysis" }));
  assert.notEqual(
    base,
    proxyCacheKey({ contentHash: "abc", segment: { ...SEGMENT, startSeconds: 1 }, profile: "render" }),
  );
  assert.notEqual(
    base,
    proxyCacheKey({ contentHash: "abc", segment: SEGMENT, profile: "render", version: 2 }),
  );
}

function verifyMissingPathsAreRefused() {
  assert.throws(() => analysisProxyArgs({ output: "/tmp/a.mp4" }), /Quelle und Ziel/);
  assert.throws(() => renderProxyArgs({ source: SOURCE }), /Quelle und Ziel/);
}

for (const check of [
  verifyTheOriginalIsNeverTouched,
  verifyMetadataDoesNotTravel,
  verifyTheAnalysisCopyIsSmallAndSilent,
  verifyTheRenderCopyIsSilentUnlessAsked,
  verifyRotationIsBakedIntoThePixels,
  verifyASegmentSeeksFastThenExactly,
  verifyTheHeightIsAlwaysEven,
  verifyTheCacheKeyMovesOnlyWithWhatChangesTheFile,
  verifyMissingPathsAreRefused,
]) {
  check();
}

console.log("Video proxy tests passed.");
