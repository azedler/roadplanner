/**
 * Music onto a film that already exists.
 *
 * The old order was backwards and it was noticed from outside: you had
 * to order music for a film you had not seen, because Remotion mixes
 * audio while it renders. The length was an estimate and a different
 * soundtrack meant rendering everything again.
 *
 * These checks pin the three properties that make the new order worth
 * having: the pictures are copied and never re-encoded, the film decides
 * the length, and the handover between sections is a crossfade rather
 * than a dip.
 */

import assert from "node:assert/strict";
import {
  DEFAULT_VOLUME,
  buildFilterGraph,
  muxArgs,
  sectionFilter,
  sectionsEnd,
} from "../apps/roadplanner_renderer/src/audiomux.mjs";

const SECTIONS = [
  { path: "music/section-01.mp3", startSeconds: 0, seconds: 118, fadeInSeconds: 2, fadeOutSeconds: 4 },
  { path: "music/section-02.mp3", startSeconds: 114, seconds: 118, fadeInSeconds: 4, fadeOutSeconds: 5 },
];

function verifyThePicturesAreNeverReEncoded() {
  // This is the whole reason the music can come last. A second Remotion
  // pass is twenty minutes and a fresh set of compression artefacts;
  // copying the video stream is seconds and lossless.
  const args = muxArgs({
    video: "film.mp4",
    sections: SECTIONS,
    output: "out.mp4",
    filmSeconds: 232,
  });
  const joined = args.join(" ");
  assert.match(joined, /-c:v copy/);
  assert.doesNotMatch(joined, /libx264|-c:v h264/);
  assert.match(joined, /-map 0:v:0/);
  assert.match(joined, /-c:a aac/);
}

function verifyTheFilmDecidesTheLength() {
  const args = muxArgs({
    video: "film.mp4",
    sections: SECTIONS,
    output: "out.mp4",
    filmSeconds: 232,
  });
  assert.match(args.join(" "), /-t 232\.000/);
  // `-shortest` would let a soundtrack that came out short truncate the
  // pictures. That is the one failure nobody would forgive.
  assert.doesNotMatch(args.join(" "), /-shortest/);
  // And a length nobody measured is refused rather than guessed.
  assert.throws(
    () => muxArgs({ video: "f.mp4", sections: SECTIONS, output: "o.mp4" }),
    /Filmlänge/,
  );
}

function verifyTheHandoverIsACrossfadeAndNotADip() {
  const graph = buildFilterGraph(SECTIONS);
  // normalize=0 is the point: normalising divides by the number of
  // inputs, so every overlap would drop in volume - the exact opposite
  // of a crossfade.
  assert.match(graph, /amix=inputs=2:normalize=0/);
  // Each section fades out while the next fades in, and the second is
  // delayed to start before the first has finished.
  assert.match(graph, /afade=t=out/);
  assert.match(graph, /adelay=114000\|114000/);
  assert.ok(
    SECTIONS[1].startSeconds < SECTIONS[0].startSeconds + SECTIONS[0].seconds,
    "die Abschnitte müssen sich überlappen",
  );
  // A limiter after the sum, because two overlapping sections add up.
  assert.match(graph, /alimiter/);
}

function verifyADelayIsWrittenForBothChannels() {
  // A single adelay value silently delays only the left channel on some
  // builds - the kind of fault nobody hears until it is in a film.
  const filter = sectionFilter({ startSeconds: 30, seconds: 60 }, 0);
  assert.match(filter, /adelay=30000\|30000/);
}

function verifyASectionIsTrimmedToWhatItWasOrderedFor() {
  // A generated track may be longer than its section. Untrimmed it would
  // play over its successor instead of handing over to it.
  const filter = sectionFilter({ startSeconds: 0, seconds: 90 }, 0);
  assert.match(filter, /atrim=0:90\.000/);
  // Fades can never overlap each other on a very short section.
  const short = sectionFilter(
    { startSeconds: 0, seconds: 4, fadeInSeconds: 10, fadeOutSeconds: 10 },
    0,
  );
  assert.match(short, /afade=t=in:st=0:d=2\.000/);
  assert.match(short, /afade=t=out:st=2\.000:d=2\.000/);
}

function verifyOneSectionNeedsNoMixer() {
  const graph = buildFilterGraph([SECTIONS[0]]);
  assert.doesNotMatch(graph, /amix/);
  assert.match(graph, /\[music\]$/);
}

function verifyMusicOffIsNotAnEmptyMix() {
  // "No music" must never become a silent track muxed in anyway. The
  // caller has to not call this at all, so it refuses rather than
  // producing a graph that sums nothing.
  assert.equal(buildFilterGraph([]), "");
  assert.throws(
    () => muxArgs({ video: "f.mp4", sections: [], output: "o.mp4", filmSeconds: 100 }),
    /nichts zu mischen/,
  );
}

function verifyTheLevelLeavesTheePicturesInFront() {
  assert.ok(DEFAULT_VOLUME < 0.5, DEFAULT_VOLUME);
  assert.match(buildFilterGraph(SECTIONS), /volume=0\.420/);
  assert.equal(sectionsEnd(SECTIONS), 232);
  assert.equal(sectionsEnd([]), 0);
}

for (const check of [
  verifyThePicturesAreNeverReEncoded,
  verifyTheFilmDecidesTheLength,
  verifyTheHandoverIsACrossfadeAndNotADip,
  verifyADelayIsWrittenForBothChannels,
  verifyASectionIsTrimmedToWhatItWasOrderedFor,
  verifyOneSectionNeedsNoMixer,
  verifyMusicOffIsNotAnEmptyMix,
  verifyTheLevelLeavesTheePicturesInFront,
]) {
  check();
}

console.log("Audio mux tests passed.");
