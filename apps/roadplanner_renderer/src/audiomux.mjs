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
export function sectionFilter(section, index, { volume = DEFAULT_VOLUME } = {}) {
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
  parts.push(`volume=${Number(volume).toFixed(3)}`);
  if (start > 0) parts.push(`adelay=${start}|${start}`);
  return `[${index + 1}:a]${parts.join(",")}[a${index}]`;
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
  return [
    ...chains,
    mix,
    `[mixed]alimiter=limit=${PEAK_CEILING}:level=disabled[music]`,
  ].join(";");
}

/**
 * The full ffmpeg invocation for muxing music onto a finished film.
 *
 * `-c:v copy` is the whole point: the pictures are not touched, so this
 * costs seconds and loses no quality. `-shortest` is deliberately NOT
 * used - the film decides the length, and a soundtrack that came out a
 * little short must not truncate it.
 */
export function muxArgs({ video, sections, output, filmSeconds, volume = DEFAULT_VOLUME }) {
  if (!video || !output) throw new Error("Video und Ziel werden gebraucht.");
  const list = (sections || []).filter((entry) => entry && entry.path);
  if (!list.length) throw new Error("Ohne Musikabschnitte gibt es nichts zu mischen.");
  const length = seconds(filmSeconds);
  if (!length) throw new Error("Die gemessene Filmlänge fehlt.");
  const args = ["-hide_banner", "-loglevel", "error", "-y", "-i", video];
  for (const section of list) args.push("-i", section.path);
  args.push(
    "-filter_complex",
    buildFilterGraph(list, { volume }),
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

export default { buildFilterGraph, muxArgs, sectionFilter, sectionsEnd };
