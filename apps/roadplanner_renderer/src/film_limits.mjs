/**
 * How long a film render may take, and how that is decided.
 *
 * Its own module because it has to be testable without a browser: the
 * render module imports Remotion, which is not installed where the tests
 * run, and a limit nobody can check is how the last one expired unnoticed.
 */
import process from "node:process";

export const FILM_LIMITS = {
  // The FLOOR, not the ceiling.
  //
  // It was the ceiling once, justified by a measurement: 126 ms per frame,
  // "the longest film this builds - twenty-five days, about 9 000 frames",
  // doubled because a Home Assistant box is not a developer machine. That
  // sentence carried its own expiry date and it expired. When the film
  // plan stopped packing a day's photographs into ever-fuller collages,
  // the film grew by half and the frame count roughly doubled while this
  // constant stayed where it was - so two real renders died past the
  // halfway mark with nothing at the end, which is precisely the failure
  // the doubling had been meant to prevent.
  //
  // A ceiling derived from one film length breaks the next time the film
  // gets longer. So the real one is computed per render, from the frames
  // actually being drawn, and this is only the smallest it may ever be.
  renderTimeoutMs: Number(process.env.ROADPLANNER_FILM_TIMEOUT_MS || 2_400_000),
  // What one frame may cost before a render counts as hopeless rather than
  // slow. Three times the 126 ms measured on a developer machine, because
  // the target is a box that also runs a household.
  msPerFrame: Number(process.env.ROADPLANNER_FILM_MS_PER_FRAME || 400),
  // And the guard that actually catches a wedged browser: no progress at
  // all for this long. A wall clock cannot tell "slow" from "stuck"; this
  // can, which is why the ceiling no longer has to try.
  stallTimeoutMs: Number(process.env.ROADPLANNER_FILM_STALL_MS || 600_000),
  // What a finished film may weigh.
  //
  // 512 MB was measured against 720p, where a twelve-minute film lands
  // at 221 MB - comfortable. It stops being comfortable the moment a
  // profile is chosen: the same film at 1440p is projected at 670-880 MB
  // and at 4K well past a gigabyte. The render would run for an hour and
  // a half and then be refused for being what it was asked to be, which
  // is the worst possible moment to find out.
  maxOutputBytes: Number(process.env.ROADPLANNER_FILM_MAX_BYTES || 2048 * 1024 * 1024),
  // Free space demanded BEFORE the browser starts, for the baseline
  // profile. Scaled by the profile's pixels at the call site - unlike
  // render time, file size really does follow pixel count, so this is
  // the one place that factor belongs.
  minFreeBytes: Number(process.env.ROADPLANNER_FILM_MIN_FREE_BYTES || 768 * 1024 * 1024),
};

/**
 * The wall-clock ceiling for a composition of this many frames.
 *
 * `pixels` is how much work one frame is against the 720p baseline. It is
 * a parameter for the same reason the frame count is: the ceiling was
 * derived once from one film at one size, and both of those grew. A 4K
 * frame is nine times the pixels of a 720p one, so a ceiling measured at
 * 720p would kill a 4K render halfway through - which is exactly the
 * failure the per-frame budget was introduced to end, one variable
 * further along.
 */
export function renderCeilingMs(limits, durationInFrames, pixels = 1) {
  const frames = Number(durationInFrames) || 0;
  const perFrame = Number(limits?.msPerFrame) || 0;
  const factor = Number(pixels) > 0 ? Number(pixels) : 1;
  return Math.max(Number(limits?.renderTimeoutMs) || 0, Math.round(frames * perFrame * factor));
}
