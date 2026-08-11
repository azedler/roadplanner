/**
 * Making a small copy of a film that already exists.
 *
 * The film is the expensive thing. Twelve minutes of it take an hour to
 * draw and land somewhere north of two hundred megabytes, and the one
 * question asked of it afterwards - "does this cut work?" - does not need
 * a single one of those pixels. It needs to be watchable and it needs to
 * survive an upload.
 *
 * So this is deliberately NOT a render. Nothing here starts a browser,
 * reads a package, or knows what a scene is. It takes one finished MP4
 * and re-encodes it smaller: same aspect ratio, same duration, same
 * audio, same everything except the number of pixels and the bitrate.
 * A review copy that differed from the film in any other way would be
 * worthless, because the whole point is to judge the film by looking at
 * the copy.
 *
 * Arguments and arithmetic only - no process is started in this module,
 * so all of it is testable without ffmpeg installed. The one place that
 * runs it is `render.mjs`, which already owns the encoder.
 */

import { DEFAULT_RENDER_PROFILE, RENDER_PROFILES } from "./render_profiles.mjs";

/** Which profiles a review copy may be made in. */
export const REVIEW_COPY_PROFILES = ["review_480", "review_720"];
export const DEFAULT_REVIEW_PROFILE = "review_720";

/**
 * How big a review copy is aimed to be - and it belongs to the PROFILE.
 *
 * One shared target was the first attempt, and it made the two profiles
 * indistinguishable where it mattered: at real film length the target
 * decides the bitrate, so a twelve-minute film came out at the same
 * number of bytes in both sizes. The smaller picture was only compressed
 * less, which is not what anybody choosing "schnell" is asking for.
 *
 * So each review profile aims at its own size, and the two purposes are
 * different on purpose:
 *
 * - **480p** exists for iteration speed and a file that uploads
 *   anywhere. Around 50 MB for twelve minutes.
 * - **720p** exists to judge type, maps, cropping and fine movement.
 *   Around 90 MB for the same film.
 *
 * Not a hard limit either way - it is the number the bitrate is derived
 * from, and a single-pass encode lands near it rather than on it.
 */
export function reviewTargetBytes(profile) {
  const declared = Number(profile?.reviewTargetBytes);
  if (Number.isFinite(declared) && declared > 0) return declared;
  // A profile with no target of its own gets the small one. Aiming too
  // low produces a file somebody can still watch and still send; aiming
  // too high produces one that will not upload.
  return RENDER_PROFILES[REVIEW_COPY_PROFILES[0]].reviewTargetBytes;
}

/** Enough for speech and music under it, and small enough to ignore. */
export const AUDIO_BITRATE_BPS = 96_000;

/**
 * The floor: a very long film would otherwise derive a bitrate at which
 * nothing is recognisable, and an unwatchable copy answers no question.
 */
export const MIN_VIDEO_BPS = 250_000;

/**
 * The ceiling, per pixel rather than flat - and this is a measurement,
 * not a preference.
 *
 * A flat ceiling was the first attempt, and a two-minute film at 1440p
 * showed what is wrong with it: the target-derived rate ran into the same
 * number at both sizes, so the 480p copy came out at 87 MB, byte for byte
 * as large as the 720p one and no more watchable for it. "Smaller" had
 * quietly stopped meaning anything.
 *
 * 0.13 bits per pixel per frame is around where h264 stops looking
 * visibly starved at this preset. It gives roughly 1.6 Mbit/s at 480p and
 * 3.6 at 720p - well above what a full-length film derives from the
 * target size, so this only ever bites on a short one, which is exactly
 * where a ceiling belongs.
 */
export const BITS_PER_PIXEL = 0.13;

/** What this profile may spend on one second of video, at most. */
export function maxVideoBps(profile) {
  const width = Number(profile?.width) || 0;
  const height = Number(profile?.height) || 0;
  const fps = Number(profile?.fps) || 30;
  return Math.max(MIN_VIDEO_BPS, Math.round(width * height * fps * BITS_PER_PIXEL));
}

/** The one review profile the caller asked for, or the default. */
export function reviewProfile(id) {
  const key = String(id ?? "");
  if (!REVIEW_COPY_PROFILES.includes(key)) {
    return RENDER_PROFILES[DEFAULT_REVIEW_PROFILE] ?? RENDER_PROFILES[DEFAULT_RENDER_PROFILE];
  }
  return RENDER_PROFILES[key];
}

/**
 * What bitrate lands a film of this length near the target size.
 *
 * Deliberately arithmetic rather than a fixed number per profile: the
 * films this is used on run anywhere from two minutes to twenty, and one
 * bitrate that suits both does not exist. The audio, when there is any,
 * is subtracted first - it is small, but a copy that overshoots because
 * nobody counted the soundtrack is the same class of mistake as a budget
 * that forgot the clips.
 */
export function reviewBitrate({
  durationSeconds,
  targetBytes = null,
  hasAudio = false,
  profile = null,
}) {
  // Both the target and the ceiling belong to the size being encoded.
  // Without a profile the smallest one is assumed, which can only ever
  // make a copy smaller than intended - never larger than it could use.
  const chosen = profile ?? RENDER_PROFILES[REVIEW_COPY_PROFILES[0]];
  const ceiling = maxVideoBps(chosen);
  const seconds = Number(durationSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return MIN_VIDEO_BPS;
  const budget = Number(targetBytes ?? reviewTargetBytes(chosen));
  if (!Number.isFinite(budget) || budget <= 0) return MIN_VIDEO_BPS;
  const total = (budget * 8) / seconds;
  const forVideo = total - (hasAudio ? AUDIO_BITRATE_BPS : 0);
  return Math.round(Math.min(ceiling, Math.max(MIN_VIDEO_BPS, forVideo)));
}

/**
 * The scale filter, and the reason it looks like that.
 *
 * `min(iw, W)` rather than plain `W`: a review copy must never be larger
 * than what it was made from. Upscaling a 480p film to 720p produces a
 * bigger file that contains strictly less than the original did, which is
 * the opposite of everything this module is for.
 *
 * `force_original_aspect_ratio=decrease` keeps the shape; `force_divisible_by=2`
 * keeps h264 able to encode the result. Both are needed together - the
 * pair without the second is where the portrait clips failed before.
 */
export function reviewScaleFilter(profile) {
  const width = Math.max(2, Number(profile?.width) || 0);
  const height = Math.max(2, Number(profile?.height) || 0);
  return (
    `scale=w=min(iw\\,${width}):h=min(ih\\,${height})` +
    ":force_original_aspect_ratio=decrease:force_divisible_by=2"
  );
}

/**
 * The whole command, as arguments.
 *
 * What is deliberately absent is as important as what is here:
 *
 * - **no `-r`**, so the frame rate is the film's and the timing cannot
 *   shift;
 * - **no `-ss`/`-t`**, so the copy is the whole film;
 * - **no filter on the audio**, so a soundtrack arrives at the same
 *   moments it did in the film;
 * - **`-map` by stream rather than by guess**, so a film without audio
 *   produces a copy without audio rather than a silent track.
 */
export function reviewCopyArgs({
  source,
  output,
  profile,
  bitrateBps,
  hasAudio = false,
}) {
  const rate = Math.max(MIN_VIDEO_BPS, Math.round(Number(bitrateBps) || MIN_VIDEO_BPS));
  const args = [
    "-nostdin",
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-i",
    String(source),
    "-map",
    "0:v:0",
  ];
  if (hasAudio) args.push("-map", "0:a:0");
  args.push(
    "-vf",
    reviewScaleFilter(profile),
    "-c:v",
    "libx264",
    "-preset",
    String(profile?.x264Preset || "veryfast"),
    "-b:v",
    String(rate),
    // A ceiling and a buffer around the average, so a busy minute cannot
    // spend the whole film's budget. 1.45x is the usual headroom for
    // single-pass h264; two seconds of buffer at that rate is what lets
    // it average out over a scene rather than a frame.
    "-maxrate",
    String(Math.round(rate * 1.45)),
    "-bufsize",
    String(Math.round(rate * 2)),
    "-pix_fmt",
    "yuv420p",
  );
  if (hasAudio) {
    args.push("-c:a", "aac", "-b:a", String(AUDIO_BITRATE_BPS));
  } else {
    // Not a silent track: a film with no music must produce a copy with
    // no audio stream, or "did the music arrive?" stops being answerable
    // from the file - the same rule the render itself follows.
    args.push("-an");
  }
  args.push(
    // The copy is made to be sent somewhere and played there, often
    // before it has finished downloading.
    "-movflags",
    "+faststart",
    String(output),
  );
  return args;
}

export default {
  BITS_PER_PIXEL,
  DEFAULT_REVIEW_PROFILE,
  maxVideoBps,
  REVIEW_COPY_PROFILES,
  reviewBitrate,
  reviewTargetBytes,
  reviewCopyArgs,
  reviewProfile,
  reviewScaleFilter,
};
