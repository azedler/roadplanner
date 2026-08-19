/**
 * Putting the music on a film that already exists.
 *
 * The old order was backwards and it was noticed from the outside: you
 * had to order music for a film you had not seen, because Remotion mixes
 * audio while it renders and the track therefore had to be in the
 * package before the first frame existed. The film's length was an
 * estimate, and changing the music meant rendering the whole film again.
 *
 * So the music comes last. The film is rendered without it, its exact
 * length is then a measured fact rather than a guess, the plan is
 * finalised against that number, and the audio is muxed into the
 * finished file.
 *
 * The reason this is cheap is the reason it is worth doing: **the video
 * stream is copied, not re-encoded.** Muxing a soundtrack onto a
 * seven-minute film is seconds of work and loses nothing, where a second
 * Remotion pass is twenty minutes and a fresh set of compression
 * artefacts. Trying a different soundtrack stops being a decision about
 * whether it is worth the wait.
 *
 * This module builds arguments and nothing else. No process is started
 * here, so the whole filter graph - delays, fades, crossfades, levels -
 * is testable without ffmpeg being installed, which is also what keeps
 * it testable in CI.
 */

/** Never louder than this, so the pictures stay in front. */
export const DEFAULT_VOLUME = 0.42;
/** Headroom before the limiter, so a loud section cannot clip. */
export const PEAK_CEILING = 0.9;

/**
 * Where a comparison mix is brought to, in EBU R128 terms.
 *
 * Only used when a caller asks for it. Three soundtracks under the same
 * pictures cannot be judged if one of them is simply louder - a listener
 * reliably prefers the loud one and reports it as the better
 * arrangement. Matching integrated loudness removes that, and it is the
 * only way the question "which architecture works" gets asked at all.
 */
export const DEFAULT_TARGET_LUFS = -20;
export const DEFAULT_TRUE_PEAK_DBTP = -1.5;

const seconds = (value) =>
  Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;

/**
 * One filter chain per section: delayed to its start, faded at both ends.
 *
 * `adelay` takes milliseconds and needs a value per channel, so the
 * stereo pair is written out - a single value silently delays only the
 * left channel on some builds, which is the kind of fault nobody hears
 * until it is in a finished film.
 */
export function sectionFilter(
  section,
  index,
  { volume = DEFAULT_VOLUME, inputOffset = 1 } = {},
) {
  // A section may carry its own level, and a layered mix depends on it:
  // the atmosphere sits far under the piece on top of it, and summing
  // both at one shared volume is not a layered mix, it is two tracks at
  // once. Falling back to the shared value keeps every existing
  // sequential soundtrack exactly as it was.
  const level = Number.isFinite(Number(section.volume))
    ? Math.max(0, Number(section.volume))
    : Number(volume);
  const start = Math.round(seconds(section.startSeconds) * 1000);
  const length = seconds(section.seconds);
  const fadeIn = Math.min(seconds(section.fadeInSeconds), length / 2);
  const fadeOut = Math.min(seconds(section.fadeOutSeconds), length / 2);
  const parts = [
    // Trimmed first: a generated track may be longer than the section it
    // was ordered for, and an untrimmed one would play over its
    // successor instead of handing over to it.
    `atrim=0:${length.toFixed(3)}`,
    "asetpts=PTS-STARTPTS",
  ];
  if (fadeIn > 0) parts.push(`afade=t=in:st=0:d=${fadeIn.toFixed(3)}`);
  if (fadeOut > 0) {
    parts.push(
      `afade=t=out:st=${Math.max(0, length - fadeOut).toFixed(3)}:d=${fadeOut.toFixed(3)}`,
    );
  }
  parts.push(`volume=${level.toFixed(3)}`);
  if (start > 0) parts.push(`adelay=${start}|${start}`);
  // The mux has the film as input 0, so the audio starts at 1. A graph
  // that only measures the music has no film in it and starts at 0.
  // Getting this wrong does not fail loudly - ffmpeg simply resolves a
  // different stream - so it is a parameter rather than a constant.
  return `[${index + inputOffset}:a]${parts.join(",")}[a${index}]`;
}

/**
 * The whole graph: every section placed, then summed.
 *
 * `amix` with `normalize=0` is deliberate. Normalising would divide the
 * level by the number of inputs, so a film whose sections overlap for
 * four seconds would dip in volume at every handover - the exact
 * opposite of a crossfade. The sections are already at their own level
 * and their fades cross; the sum is what should be heard.
 *
 * The limiter afterwards is the safety net for the overlap, where two
 * sections briefly add up.
 */
export function buildFilterGraph(sections, options = {}) {
  const list = (sections || []).filter(Boolean);
  if (!list.length) return "";
  const chains = list.map((section, index) => sectionFilter(section, index, options));
  const inputs = list.map((_section, index) => `[a${index}]`).join("");
  const mix =
    list.length === 1
      ? `${inputs}anull[mixed]`
      : `${inputs}amix=inputs=${list.length}:normalize=0:dropout_transition=0[mixed]`;
  const stages = [...chains, mix];
  // Loudness matching, and only when asked for. It belongs to the
  // FINISHED mix rather than to the individual layers: what a listener
  // compares is the variant, and normalising the parts would flatten
  // exactly the internal balance a layered variant is being judged on.
  //
  // A STATIC gain, deliberately. `loudnorm` in a single pass normalises
  // dynamically - it pulls quiet stretches up as they happen - and the
  // material most affected by that is sparse, even, atmospheric audio,
  // which is exactly what the atmosphere variant is. The comparison
  // would then be between one architecture and another architecture
  // plus an automatic level rider, and the pumping would be reported as
  // "the bed sounds artificial". So the mix is measured first and moved
  // by one number, which changes the level and nothing else.
  let last = "mixed";
  if (options.gainDb !== undefined && options.gainDb !== null) {
    const gain = Number(options.gainDb);
    if (!Number.isFinite(gain)) throw new Error("Die Pegelkorrektur ist keine Zahl.");
    stages.push(`[mixed]volume=${gain.toFixed(2)}dB[levelled]`);
    last = "levelled";
  }
  stages.push(`[${last}]alimiter=limit=${PEAK_CEILING}:level=disabled[music]`);
  return stages.join(";");
}

/**
 * The largest static move that is ever applied to reach a target.
 *
 * A mix that is thirty decibels below where it should be is not quiet,
 * it is broken - a missing layer, a gain of zero, a file of silence -
 * and lifting it would turn a fault into a hiss nobody can explain.
 */
export const MAX_GAIN_DB = 18;

/**
 * What to move the mix by, from what it measured and where it should be.
 *
 * Two limits, and the peak one is not decoration. Loudness says nothing
 * about peaks: a quiet, dynamic mix can be eight decibels below target
 * and still have a transient near full scale, and lifting it by those
 * eight would clip it. So the move is whichever of the two is smaller,
 * and the result is a level that is as close to the target as the
 * material allows rather than one that reached it by distorting.
 */
export function gainForTarget(
  measuredLufs,
  targetLufs,
  { truePeakDbfs = null, ceilingDbtp = DEFAULT_TRUE_PEAK_DBTP } = {},
) {
  // `Number(null)` is 0, not NaN. Every absent measurement here would
  // therefore have arrived as a perfectly plausible zero: an unmeasured
  // mix would have been "0 LUFS" and lowered by eighteen decibels, and
  // an unmeasured peak would have been "0 dBFS" and capped every gain
  // at -1.5 dB. Both are silent wrong answers, which is why the check
  // is for a real number rather than for a truthy one.
  const number = (value) =>
    value === null || value === undefined || value === "" ? NaN : Number(value);
  const measured = number(measuredLufs);
  const target = number(targetLufs);
  if (!Number.isFinite(measured) || !Number.isFinite(target)) return null;
  let wanted = target - measured;
  const peak = number(truePeakDbfs);
  const ceiling = number(ceilingDbtp);
  if (Number.isFinite(peak) && Number.isFinite(ceiling)) {
    wanted = Math.min(wanted, ceiling - peak);
  }
  return Math.max(-MAX_GAIN_DB, Math.min(MAX_GAIN_DB, wanted));
}

/**
 * The same mix, decoded into a meter instead of into a file.
 *
 * The measurement has to happen on the FINISHED mix, after the layers
 * are summed at their own levels - the sum of two measured layers is
 * not the measurement of their sum. Nothing is written, so this costs a
 * decode and no disk.
 */
export function analyseArgs({ sections, volume = DEFAULT_VOLUME }) {
  const list = (sections || []).filter((entry) => entry && entry.path);
  if (!list.length) throw new Error("Ohne Musikabschnitte gibt es nichts zu messen.");
  const args = ["-hide_banner", "-nostats"];
  for (const section of list) args.push("-i", section.path);
  args.push(
    "-filter_complex",
    `${buildFilterGraph(list, { volume, inputOffset: 0 })};[music]ebur128=peak=true`,
    "-f",
    "null",
    "-",
  );
  return args;
}

/**
 * The full ffmpeg invocation for muxing music onto a finished film.
 *
 * `-c:v copy` is the whole point: the pictures are not touched, so this
 * costs seconds and loses no quality. `-shortest` is deliberately NOT
 * used - the film decides the length, and a soundtrack that came out a
 * little short must not truncate it.
 */
export function muxArgs({
  video,
  sections,
  output,
  filmSeconds,
  volume = DEFAULT_VOLUME,
  gainDb,
}) {
  if (!video || !output) throw new Error("Video und Ziel werden gebraucht.");
  const list = (sections || []).filter((entry) => entry && entry.path);
  if (!list.length) throw new Error("Ohne Musikabschnitte gibt es nichts zu mischen.");
  const length = seconds(filmSeconds);
  if (!length) throw new Error("Die gemessene Filmlänge fehlt.");
  const args = ["-hide_banner", "-loglevel", "error", "-y", "-i", video];
  for (const section of list) args.push("-i", section.path);
  args.push(
    "-filter_complex",
    buildFilterGraph(list, { volume, gainDb }),
    "-map",
    "0:v:0",
    "-map",
    "[music]",
    "-c:v",
    "copy",
    "-c:a",
    "aac",
    "-b:a",
    "192k",
    "-ar",
    "44100",
    "-ac",
    "2",
    // The FILM is the master, and its length is a measured fact by the
    // time this runs - that is the whole reason the music comes last.
    // `-shortest` would let a soundtrack that came out short truncate
    // the pictures, and no limit at all would let one that came out
    // long extend the video past its last frame.
    "-t",
    length.toFixed(3),
    "-movflags",
    "+faststart",
    output,
  );
  return args;
}

/** Where the last section stops, in seconds. */
export function sectionsEnd(sections) {
  return (sections || []).reduce(
    (latest, section) =>
      Math.max(latest, seconds(section.startSeconds) + seconds(section.seconds)),
    0,
  );
}

/**
 * The ffmpeg call that MEASURES a finished file instead of writing one.
 *
 * A report that says "the three variants are comparably loud" without a
 * number is a hope. This produces the numbers: integrated loudness,
 * loudness range and true peak, straight from ffmpeg's own R128 meter,
 * decoding to nowhere.
 */
export function loudnessArgs(media) {
  if (!media) throw new Error("Ohne Datei gibt es nichts zu messen.");
  return [
    "-hide_banner",
    "-nostats",
    "-i",
    media,
    "-map",
    "0:a:0",
    "-filter_complex",
    "ebur128=peak=true",
    "-f",
    "null",
    "-",
  ];
}

/**
 * The three numbers out of that call's summary.
 *
 * Anything not found comes back as `null` rather than as a zero. A
 * missing measurement that reads as "-0.0 dBFS" is an absent answer
 * wearing the costume of a result, and this project has already sent
 * somebody chasing one of those.
 */
export function parseLoudness(text) {
  const source = String(text || "");
  const summary = source.slice(source.lastIndexOf("Summary:"));
  const pick = (label) => {
    const found = new RegExp(`${label}:\\s*(-?\\d+(?:\\.\\d+)?)`).exec(summary);
    return found ? Number(found[1]) : null;
  };
  return {
    integratedLufs: pick("I"),
    loudnessRange: pick("LRA"),
    truePeakDbfs: pick("Peak"),
  };
}

/**
 * Above this peak, a track is carrying something somebody can hear.
 *
 * A Remotion render ALWAYS writes an AAC stream, whether or not the film
 * has music - an empty one measures around -91 dBFS. So "this file has
 * an audio stream" and "this film has a soundtrack" are two different
 * statements, and treating the first as the second is what refused every
 * silent excerpt as "already scored" (live report, measured: mean and
 * max both -91.0 dB).
 *
 * -60 dBFS sits far above any digital-silence floor and far below
 * anything a person would call quiet music, so nothing real lands near
 * the line.
 */
export const AUDIBLE_PEAK_DBFS = -60;

/** The ffmpeg call that measures how loud a file's audio actually is. */
export function volumeArgs(media) {
  if (!media) throw new Error("Ohne Datei gibt es nichts zu messen.");
  return [
    "-hide_banner",
    "-nostats",
    "-i",
    media,
    "-map",
    "0:a:0",
    "-filter_complex",
    "volumedetect",
    "-f",
    "null",
    "-",
  ];
}

/**
 * Mean and peak level out of that call, or nulls.
 *
 * Nulls rather than zeros, for the reason `parseLoudness` gives: 0 dBFS
 * is full scale, so an unmeasured file reported as zero would read as
 * the loudest possible signal.
 */
export function parseVolume(text) {
  const source = String(text || "");
  const pick = (label) => {
    const found = new RegExp(`${label}:\\s*(-?\\d+(?:\\.\\d+)?) dB`).exec(source);
    return found ? Number(found[1]) : null;
  };
  return { meanDbfs: pick("mean_volume"), maxDbfs: pick("max_volume") };
}

/**
 * Whether a measured track carries anything audible - or nothing known.
 *
 * Three values on purpose. `null` means the meter did not run, and that
 * must not be spoken as either answer: calling an unmeasured film silent
 * would mux music onto a film that already has some, and calling it
 * audible would refuse a film that is perfectly empty.
 */
export function isAudible(measured) {
  const peak = measured?.maxDbfs;
  if (typeof peak !== "number" || !Number.isFinite(peak)) return null;
  return peak > AUDIBLE_PEAK_DBFS;
}

export default {
  analyseArgs,
  AUDIBLE_PEAK_DBFS,
  buildFilterGraph,
  gainForTarget,
  isAudible,
  loudnessArgs,
  muxArgs,
  parseLoudness,
  parseVolume,
  sectionFilter,
  sectionsEnd,
  volumeArgs,
};
