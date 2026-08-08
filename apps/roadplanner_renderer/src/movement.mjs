/**
 * How the camper moves along a route, in metres rather than in vertices.
 *
 * The film's map judders, and the brief guessed why: the camper is being
 * carried from route point to route point instead of driving the metres
 * between them. Reading the old code, that guess is half right - the
 * position *was* interpolated along the line - and the actual causes are
 * three, all of which this module exists to remove.
 *
 * **1. The arc length was measured in degrees.** `hypot(dLon, dLat)`
 * treats a degree of longitude as a degree of latitude. At 60°N a degree
 * of longitude is 55.7 km and a degree of latitude is 111.3 km, so the
 * same real speed produced exactly twice the progress heading east as
 * heading north. On a route that turns, the camper visibly speeds up and
 * slows down for no reason anybody could see.
 *
 * **2. The heading came from vertex offsets.** It looked four points back
 * and three ahead - and after simplification those points are metres
 * apart in a bend and tens of kilometres apart on a motorway. The
 * look-ahead was therefore anywhere between 20 m and 200 km and changed
 * discontinuously as the index stepped. That is the rotation jitter.
 *
 * **3. The drawn route ran ahead of the camper and doubled back.** The
 * revealed part was `points[0 … index]` - and `index` is the vertex
 * *after* the camper - with the camper's position appended behind it. So
 * at every single vertex the line sprang forward and came back. That one
 * is the stop-and-go, and it is also why the route and the camper did
 * not agree.
 *
 * Everything here is a pure function of its arguments. The same plan
 * therefore produces the same motion, frame for frame, on every render.
 */

/** Metres per degree of latitude. Good to a few parts per thousand. */
const METRES_PER_DEGREE = 111_320;

/**
 * Metres between two [lon, lat] points.
 *
 * Equirectangular, with longitude scaled by the cosine of the latitude
 * the two points share. Over a leg of a day's drive the error against a
 * great circle is far below the width of the line being drawn, and it
 * costs two multiplications instead of six trigonometric calls per
 * frame.
 */
export function metres(a, b) {
  const meanLat = ((a[1] + b[1]) / 2) * (Math.PI / 180);
  const dx = (b[0] - a[0]) * Math.cos(meanLat) * METRES_PER_DEGREE;
  const dy = (b[1] - a[1]) * METRES_PER_DEGREE;
  return Math.hypot(dx, dy);
}

/**
 * Cumulative distance along a line, in metres, starting at zero.
 *
 * Returned rather than recomputed per frame: this is the one expensive
 * part of the movement, and it depends only on the geometry.
 */
export function arcLengths(points) {
  const lengths = [0];
  for (let index = 1; index < points.length; index += 1) {
    lengths.push(lengths[index - 1] + metres(points[index - 1], points[index]));
  }
  return lengths;
}

/** A route prepared once, then asked many times where the camper is. */
export function prepareRoute(points) {
  const clean = [];
  for (const point of points || []) {
    if (!Array.isArray(point) || point.length < 2) continue;
    const last = clean[clean.length - 1];
    // A repeated coordinate contributes a zero-length segment, which is a
    // division by zero waiting to happen and says nothing about the road.
    if (last && last[0] === point[0] && last[1] === point[1]) continue;
    clean.push([point[0], point[1]]);
  }
  const lengths = arcLengths(clean);
  return { points: clean, lengths, total: lengths[lengths.length - 1] || 0 };
}

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

/**
 * The point `distance` metres along the route.
 *
 * Returns the position and the index of the vertex it has just passed,
 * so a caller can draw the line up to exactly here - and no further.
 */
export function pointAtDistance(route, distance) {
  const { points, lengths, total } = route;
  if (!points.length) return { position: [0, 0], index: 0 };
  if (points.length === 1 || total <= 0) {
    return { position: [...points[0]], index: 0 };
  }
  const target = clamp(distance, 0, total);
  // Binary search rather than a scan: a chapter can carry a few hundred
  // points and this runs for every frame of every map scene.
  let low = 1;
  let high = points.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (lengths[middle] < target) low = middle + 1;
    else high = middle;
  }
  const index = low;
  const span = lengths[index] - lengths[index - 1];
  const t = span > 0 ? (target - lengths[index - 1]) / span : 0;
  const from = points[index - 1];
  const to = points[index];
  return {
    position: [from[0] + (to[0] - from[0]) * t, from[1] + (to[1] - from[1]) * t],
    index: index - 1,
  };
}

/**
 * Which way the camper faces, looked up over a fixed distance.
 *
 * Metres, not vertices - that is the whole point. The window is the same
 * length whether the road is a dense hairpin or a straight kept by two
 * points a hundred kilometres apart, so the direction changes smoothly
 * as the camper drives rather than jumping when an index steps.
 *
 * Looking slightly ahead as well as behind makes the vehicle lean into a
 * bend before reaching it, the way a driver does.
 */
export function bearingWindow(route, frames) {
  // How far the camper travels in one frame decides how long the window
  // has to be. A fixed one was the first attempt and it failed on a real
  // day: 365 km compressed into eight seconds is 1.5 km per frame, and a
  // 2.4 km window barely overlapped between frames - so a right-angle
  // turn arrived as a 55° snap in a single frame. Ten frames' worth of
  // road, floored so a short leg still gets a usable window and capped so
  // a very long one does not average the whole day into one direction.
  //
  // Measured on a deliberately cruel test route - a true right angle in a
  // single vertex, which real routing data never produces - this takes
  // the worst turn from 55° per frame down to about 10°, or a third of a
  // second for a ninety degree change of direction.
  const perFrame = frames > 0 ? route.total / frames : route.total;
  const span = Math.max(400, Math.min(perFrame * 10, 14_000));
  return { behind: span * 0.4, ahead: span * 0.6 };
}

export function bearingAtDistance(route, distance, { behind = 900, ahead = 1500 } = {}) {
  const { total } = route;
  if (total <= 0) return 0;
  // Near the ends the window would run off the route, so it is slid back
  // inside instead of being clipped - a clipped window shrinks to
  // nothing at the finish and the heading snaps.
  const span = Math.min(behind + ahead, total);
  let start = clamp(distance - behind, 0, total - span);
  const from = pointAtDistance(route, start).position;
  const to = pointAtDistance(route, start + span).position;
  const meanLat = ((from[1] + to[1]) / 2) * (Math.PI / 180);
  const dx = (to[0] - from[0]) * Math.cos(meanLat);
  const dy = to[1] - from[1];
  if (dx === 0 && dy === 0) return 0;
  return (Math.atan2(dy, dx) * 180) / Math.PI;
}

/**
 * The route as far as the camper has come, and not one metre further.
 *
 * The vertices already passed, then the camper's own position as the
 * final point. This is what makes "die sichtbare Route endet am
 * aktuellen Camperstand" true by construction rather than by luck.
 */
export function routeUpTo(route, distance) {
  const { points } = route;
  if (points.length < 2) return points.map((point) => [...point]);
  const head = pointAtDistance(route, distance);
  const drawn = points.slice(0, head.index + 1).map((point) => [...point]);
  const last = drawn[drawn.length - 1];
  if (!last || last[0] !== head.position[0] || last[1] !== head.position[1]) {
    drawn.push([...head.position]);
  }
  return drawn;
}

/**
 * How much of the day is driven at a given moment.
 *
 * A vehicle that is at full speed in the first frame and stops dead in
 * the last one looks like a cut, not like a drive. This eases away from
 * the start and into the destination, and is flat in between so the
 * middle of a long leg travels at an honest constant speed.
 *
 * `smoothstep` on the two ends only, so the eased fraction still reaches
 * exactly 0 and exactly 1 - the camper has to arrive at the day's real
 * endpoint, not near it.
 */
export function easeTravel(fraction, { rampIn = 0.18, rampOut = 0.22 } = {}) {
  const t = clamp(fraction, 0, 1);
  const flat = Math.max(0, 1 - rampIn - rampOut);
  // Built from a speed profile rather than guessed: speed rises over the
  // first ramp, holds, and falls over the last. The distance covered is
  // that profile's integral, and a smoothstep ramp covers exactly half
  // the ground a constant speed would - which is what makes the total
  // land on 1 exactly, so the camper arrives at the day's real endpoint
  // rather than near it.
  const rise = (u) => u * u * u - (u * u * u * u) / 2;
  const fall = (u) => 0.5 - ((1 - u) ** 3 - (1 - u) ** 4 / 2);
  const total = rampIn / 2 + flat + rampOut / 2;
  if (total <= 0) return t;
  let travelled;
  if (rampIn > 0 && t < rampIn) {
    travelled = rampIn * rise(t / rampIn);
  } else if (rampOut > 0 && t > 1 - rampOut) {
    travelled = rampIn / 2 + flat + rampOut * fall((t - (1 - rampOut)) / rampOut);
  } else {
    travelled = rampIn / 2 + (t - rampIn);
  }
  return clamp(travelled / total, 0, 1);
}


/**
 * A value that follows another one without copying every twitch.
 *
 * Used for the camera: it should trail the camper rather than mirror it,
 * so a bend does not throw the whole frame sideways. Deterministic
 * because it is computed from the frame number, not accumulated - a
 * renderer draws frames out of order and in parallel, so anything that
 * depended on the previous frame's value would differ between runs.
 */
export function damped(samples, frame, window = 12) {
  const first = Math.max(0, frame - window + 1);
  let sum = 0;
  let weight = 0;
  for (let index = first; index <= frame; index += 1) {
    // Newer frames count for more, so the follow is responsive without
    // being sharp.
    const w = index - first + 1;
    sum += samples(index) * w;
    weight += w;
  }
  return weight > 0 ? sum / weight : samples(frame);
}
