/**
 * Turning an original recording into the two copies the pipeline needs.
 *
 * A phone video is the wrong thing to work with twice over. It is far
 * too large to send to a model - four minutes of 4K is hundreds of
 * megabytes and most of those pixels answer no question anybody asked -
 * and its codec, frame rate and rotation are whatever the camera felt
 * like, which a renderer cannot rely on.
 *
 * So two proxies, with different jobs and therefore different settings:
 *
 * - the **analysis proxy** is small on purpose. It exists to be looked
 *   at by a model and then deleted. Every byte above "you can tell what
 *   is happening" is money and privacy spent for nothing.
 * - the **render proxy** is the clip the film plays. Normalised codec,
 *   frame rate and resolution, rotation baked in, so the composition
 *   never has to ask what it is holding.
 *
 * Two rules run through all of it:
 *
 * **The original is never touched.** Every call here writes a new file
 * somewhere else. There is no in-place flag anywhere in this module,
 * and there must not be one - the source of these files is somebody's
 * photo library.
 *
 * **Metadata does not travel.** GPS, device, serial numbers and the
 * original timestamps are stripped from both proxies. The analysis
 * proxy goes to a cloud provider, and location data attached to a
 * family video is exactly the kind of thing that must be a decision
 * rather than a default.
 *
 * Arguments only - no process is started here, so all of it is testable
 * without ffmpeg installed.
 */

/** What a model needs to see to judge a moment - and not a pixel more. */
export const ANALYSIS_HEIGHT = 360;
export const ANALYSIS_FPS = 5;
export const ANALYSIS_CRF = 32;

/** What the film plays. Matches the composition, so nothing is guessed. */
export const RENDER_HEIGHT = 720;
export const RENDER_FPS = 30;
export const RENDER_CRF = 21;

/** Bumped when a setting here changes what a proxy IS. Part of the cache key. */
export const PROXY_VERSION = 1;

const seconds = (value) =>
  Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;

/**
 * Where a segment starts and how long it runs, as ffmpeg wants it.
 *
 * `-ss` before `-i` seeks by keyframe and is fast; after `-i` it is
 * exact and slow. Both are used, in that order: the fast seek gets
 * close, the exact one lands. Getting this the wrong way round is how a
 * clip ends up starting two seconds early.
 */
function segmentArgs(segment) {
  const start = seconds(segment?.startSeconds);
  const duration = seconds(segment?.durationSeconds);
  const args = [];
  if (start > 0) {
    // Seek to a keyframe a little before the target, then trim exactly.
    const rough = Math.max(0, start - 2);
    args.push("-ss", rough.toFixed(3));
    if (start - rough > 0) args.push("-accurate_seek");
  }
  return { pre: args, start, duration };
}

/** Strip everything that says where and on what this was recorded. */
const PRIVACY_ARGS = [
  "-map_metadata",
  "-1",
  "-map_chapters",
  "-1",
  // The container's own rotation entry is applied to the pixels below
  // and must not also be left in the file, or a player would turn it
  // twice.
  "-metadata:s:v",
  "rotate=0",
];

/**
 * The small copy that goes to a model.
 *
 * Muted on purpose. The audio of a family video is conversation, and
 * sending it to a cloud provider to find out whether a moment looks
 * good is not a trade anybody agreed to. What the analysis may say
 * about sound is a flag on the ORIGINAL, decided from metadata, never
 * from a recording that left the house.
 */
export function analysisProxyArgs({ source, output, segment = null, height = ANALYSIS_HEIGHT }) {
  if (!source || !output) throw new Error("Quelle und Ziel werden gebraucht.");
  const { pre, start, duration } = segmentArgs(segment);
  const args = ["-hide_banner", "-loglevel", "error", "-y", ...pre, "-i", source];
  if (start > 0) args.push("-ss", start.toFixed(3), "-copyts", "-start_at_zero");
  if (duration > 0) args.push("-t", duration.toFixed(3));
  args.push(
    "-an",
    "-vf",
    // Even height, because H.264 cannot encode an odd one and the error
    // it gives says nothing about the cause.
    `scale=-2:${Math.round(height / 2) * 2}`,
    "-r",
    String(ANALYSIS_FPS),
    "-c:v",
    "libx264",
    "-preset",
    "veryfast",
    "-crf",
    String(ANALYSIS_CRF),
    "-pix_fmt",
    "yuv420p",
    ...PRIVACY_ARGS,
    output,
  );
  return args;
}

/**
 * The clip the film plays.
 *
 * The rotation is applied to the pixels here rather than left as a
 * container flag, because the composition draws frames and never reads
 * a rotation entry - a clip that relies on one arrives sideways.
 */
export function renderProxyArgs({
  source,
  output,
  segment = null,
  height = RENDER_HEIGHT,
  rotation = 0,
  muted = true,
}) {
  if (!source || !output) throw new Error("Quelle und Ziel werden gebraucht.");
  const { pre, start, duration } = segmentArgs(segment);
  const args = ["-hide_banner", "-loglevel", "error", "-y", ...pre, "-i", source];
  if (start > 0) args.push("-ss", start.toFixed(3), "-copyts", "-start_at_zero");
  if (duration > 0) args.push("-t", duration.toFixed(3));

  const filters = [];
  const turn = ((Number(rotation) || 0) % 360 + 360) % 360;
  if (turn === 90) filters.push("transpose=1");
  else if (turn === 180) filters.push("transpose=1,transpose=1");
  else if (turn === 270) filters.push("transpose=2");
  filters.push(`scale=-2:${Math.round(height / 2) * 2}`);

  args.push(
    // Silent by default, and the default is the point: a private
    // conversation must never end up in a film because nobody said no.
    ...(muted ? ["-an"] : ["-c:a", "aac", "-b:a", "160k"]),
    "-vf",
    filters.join(","),
    "-r",
    String(RENDER_FPS),
    "-c:v",
    "libx264",
    "-preset",
    "medium",
    "-crf",
    String(RENDER_CRF),
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    ...PRIVACY_ARGS,
    output,
  );
  return args;
}

/**
 * What a proxy was made from and with, as one key.
 *
 * Content, segment, profile and version - the four things that change
 * what the file IS. A proxy whose key matches is the same proxy, so a
 * re-render reuses it rather than transcoding again.
 */
export function proxyCacheKey({ contentHash, segment, profile, version = PROXY_VERSION }) {
  const start = seconds(segment?.startSeconds).toFixed(3);
  const duration = seconds(segment?.durationSeconds).toFixed(3);
  return [String(contentHash || ""), profile, start, duration, `v${version}`].join(":");
}

export default { analysisProxyArgs, renderProxyArgs, proxyCacheKey };
