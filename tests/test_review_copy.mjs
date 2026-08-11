/**
 * What a review copy is allowed to change, and what it must not.
 *
 * A review copy exists so a cut can be judged without waiting an hour and
 * without moving two hundred megabytes. That only works if the copy is
 * the same film: the same length, the same shape, the same soundtrack at
 * the same moments. Every property below is one of those, and each has a
 * concrete way of going wrong that would leave the copy looking fine.
 *
 * No ffmpeg is started here - this module builds arguments and does
 * arithmetic, both of which are testable without an encoder. The end-to-
 * end run against a real one lives in `test_review_copy_encode.py`.
 */
import assert from "node:assert/strict";

import {
  AUDIO_BITRATE_BPS,
  DEFAULT_REVIEW_PROFILE,
  MIN_VIDEO_BPS,
  maxVideoBps,
  REVIEW_COPY_PROFILES,
  REVIEW_TARGET_BYTES,
  reviewBitrate,
  reviewCopyArgs,
  reviewProfile,
  reviewScaleFilter,
} from "../apps/roadplanner_renderer/src/review_copy.mjs";
import { RENDER_PROFILES } from "../apps/roadplanner_renderer/src/render_profiles.mjs";

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("nur die kleinen Profile sind Reviewprofile", () => {
  for (const id of REVIEW_COPY_PROFILES) {
    assert.ok(RENDER_PROFILES[id], `${id} steht nicht in der Profiltabelle`);
  }
  assert.ok(REVIEW_COPY_PROFILES.includes(DEFAULT_REVIEW_PROFILE));
  // A "review copy" in 4K would be a re-encode that answers nothing.
  assert.ok(!REVIEW_COPY_PROFILES.includes("uhd_4k"));
  assert.ok(!REVIEW_COPY_PROFILES.includes("high_quality"));
});

test("ein unbekanntes Profil liefert den Standard, nie einen Fehler", () => {
  // The id arrives from a job file. A throw here would fail a job whose
  // only fault is a typo, after the film it copies was already made.
  for (const value of ["", null, undefined, "nope", "uhd_4k", "../etc"]) {
    assert.equal(reviewProfile(value).id, DEFAULT_REVIEW_PROFILE, String(value));
  }
  assert.equal(reviewProfile("review_480").id, "review_480");
});

test("die Bitrate folgt der Länge, nicht einer festen Zahl", () => {
  // The whole point of deriving it: a two-minute film and a twenty-minute
  // film cannot share one bitrate and both land near the target.
  const short = reviewBitrate({ durationSeconds: 120 });
  const long = reviewBitrate({ durationSeconds: 1200 });
  assert.ok(short > long, `${short} muss über ${long} liegen`);
});

test("eine abgeleitete Bitrate trifft die Zielgröße", () => {
  const seconds = 743; // the real film: 12:23
  const bitrate = reviewBitrate({ durationSeconds: seconds, hasAudio: false });
  const predicted = (bitrate * seconds) / 8;
  const ratio = predicted / REVIEW_TARGET_BYTES;
  assert.ok(ratio > 0.9 && ratio < 1.1, `Vorhersage ${ratio.toFixed(2)}x der Zielgröße`);
});

test("die Tonspur wird vom Budget abgezogen, nicht vergessen", () => {
  const seconds = 743;
  const silent = reviewBitrate({ durationSeconds: seconds, hasAudio: false });
  const scored = reviewBitrate({ durationSeconds: seconds, hasAudio: true });
  assert.equal(silent - scored, AUDIO_BITRATE_BPS);
});

test("die Bitrate bleibt in ihrem Band", () => {
  // A four-hour film would otherwise derive something unwatchable, and a
  // ten-second one something far above what the source itself carries.
  assert.equal(reviewBitrate({ durationSeconds: 14400 }), MIN_VIDEO_BPS);
  assert.equal(
    reviewBitrate({ durationSeconds: 10, profile: RENDER_PROFILES.review_720 }),
    maxVideoBps(RENDER_PROFILES.review_720),
  );
  // Nothing about a broken input may produce NaN in a command line.
  for (const value of [0, -5, NaN, null, undefined, "x"]) {
    const rate = reviewBitrate({ durationSeconds: value });
    assert.ok(Number.isInteger(rate) && rate >= MIN_VIDEO_BPS, String(value));
  }
});

test("die Obergrenze folgt der Größe, nicht einer festen Zahl", () => {
  // Measured, not assumed: with ONE flat ceiling a two-minute 1440p film
  // derived the same bitrate at both sizes, and the 480p copy came out
  // at 87 MB - byte for byte as large as the 720p one, and no more
  // watchable for it. "Kleiner" had stopped meaning anything.
  const small = reviewBitrate({ durationSeconds: 120, profile: RENDER_PROFILES.review_480 });
  const large = reviewBitrate({ durationSeconds: 120, profile: RENDER_PROFILES.review_720 });
  assert.ok(small < large, `480p ${small} muss unter 720p ${large} liegen`);
  // And on a film of real length the target size still governs both, so
  // the ceiling only ever bites where it should.
  const longSmall = reviewBitrate({ durationSeconds: 743, profile: RENDER_PROFILES.review_480 });
  const longLarge = reviewBitrate({ durationSeconds: 743, profile: RENDER_PROFILES.review_720 });
  assert.equal(longSmall, longLarge);
});

test("ohne Profil wird die kleinste Obergrenze angenommen", () => {
  // A missing profile may make a copy smaller than intended. It must
  // never make one larger than the profile could use.
  const guessed = reviewBitrate({ durationSeconds: 60 });
  assert.ok(guessed <= maxVideoBps(RENDER_PROFILES.review_480), guessed);
});

test("eine Kopie wird nie größer als ihre Quelle", () => {
  // `min(iw, W)` rather than `W`: upscaling a 480p film to 720p makes a
  // bigger file containing strictly less than the original.
  const filter = reviewScaleFilter(RENDER_PROFILES.review_720);
  assert.ok(filter.includes("min(iw"), filter);
  assert.ok(filter.includes("min(ih"), filter);
  assert.ok(filter.includes("force_original_aspect_ratio=decrease"), filter);
  // The pair that the portrait clips failed without.
  assert.ok(filter.includes("force_divisible_by=2"), filter);
});

test("das Seitenverhältnis wird nicht verzerrt", () => {
  const filter = reviewScaleFilter(RENDER_PROFILES.review_480);
  // A stretch would be `scale=w=854:h=480` with no aspect handling at all.
  assert.ok(!/scale=w=\d+:h=\d+:/.test(filter), filter);
});

test("nichts an der Zeit wird angefasst", () => {
  const args = reviewCopyArgs({
    source: "/a/film.mp4",
    output: "/b/copy.mp4",
    profile: RENDER_PROFILES.review_720,
    bitrateBps: 900_000,
    hasAudio: true,
  });
  // A copy that is shorter than the film is not a smaller version of it.
  for (const forbidden of ["-ss", "-t", "-to", "-r", "-fps_mode", "-vsync"]) {
    assert.ok(!args.includes(forbidden), `${forbidden} darf nicht vorkommen: ${args}`);
  }
});

test("eine vorhandene Tonspur überlebt, eine fehlende entsteht nicht", () => {
  const scored = reviewCopyArgs({
    source: "/a.mp4",
    output: "/b.mp4",
    profile: RENDER_PROFILES.review_720,
    bitrateBps: 900_000,
    hasAudio: true,
  });
  assert.ok(scored.includes("-c:a"), scored);
  assert.ok(!scored.includes("-an"), scored);
  assert.ok(scored.join(" ").includes("-map 0:a:0"), scored);
  // No audio filter: a soundtrack must arrive at the same moments it did.
  assert.ok(!scored.includes("-af"), scored);
  assert.ok(!scored.includes("-filter:a"), scored);

  const silent = reviewCopyArgs({
    source: "/a.mp4",
    output: "/b.mp4",
    profile: RENDER_PROFILES.review_720,
    bitrateBps: 900_000,
    hasAudio: false,
  });
  // Not a silent track. The same rule the render itself follows: a film
  // with no music must produce a file with no audio stream, or "did the
  // music arrive?" stops being answerable from the file.
  assert.ok(silent.includes("-an"), silent);
  assert.ok(!silent.includes("-c:a"), silent);
});

test("keine Zahl im Kommando kann NaN werden", () => {
  const args = reviewCopyArgs({
    source: "/a.mp4",
    output: "/b.mp4",
    profile: RENDER_PROFILES.review_480,
    bitrateBps: undefined,
    hasAudio: false,
  });
  assert.ok(!args.some((value) => String(value).includes("NaN")), args);
});

test("das Kommando spricht nur über die zwei Dateien, die es bekommen hat", () => {
  const args = reviewCopyArgs({
    source: "/share/x/film.mp4",
    output: "/share/y/copy.mp4",
    profile: RENDER_PROFILES.review_720,
    bitrateBps: 900_000,
    hasAudio: false,
  });
  const paths = args.filter((value) => String(value).includes("/"));
  assert.deepEqual(paths, ["/share/x/film.mp4", "/share/y/copy.mp4"]);
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
  } catch (err) {
    failed += 1;
    console.error(`FEHLGESCHLAGEN: ${name}\n  ${err.message}`);
  }
}
if (failed) {
  console.error(`${failed} von ${tests.length} Prüfungen fehlgeschlagen.`);
  process.exit(1);
}
console.log(`Review copy tests passed (${tests.length}).`);
