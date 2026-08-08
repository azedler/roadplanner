/**
 * Where the map is looked at from, as arithmetic.
 *
 * The map component owns projecting the world and painting it. What it
 * does *not* own is the question the brief actually cares about: does the
 * view move without jumping. That question is answerable with numbers
 * alone - a camera is three of them - so the numbers live here, in plain
 * JavaScript a test can drive, rather than inside a component that only
 * exists once a browser does.
 *
 * The same reasoning as `movement.mjs`, and for the same reason: a claim
 * about smoothness that nothing measures is a claim about intent.
 *
 * A camera is `{x, y, zoom}`: a point in projected pixels that sits in
 * the middle of the frame, and how much the projection is magnified
 * around it. Screen position follows from
 *
 *     screen = frameCentre + zoom * (point - camera)
 *
 * and every function here is consistent with that one line.
 */

/**
 * The strip along the foot of the frame that the caption covers.
 *
 * The caption is not decoration laid over the map - it carries the day,
 * the place and the country, which is the orientation the brief asks
 * for, and it is on screen the whole time. So the map's usable area is
 * the frame minus this strip. Fitting to the whole frame instead is what
 * put routes underneath the caption gradient.
 */
export const CAPTION_STRIP = 236;

/**
 * The camera that shows a whole set of projected points.
 *
 * `fill` is how much of the usable area the content should occupy, so
 * there is air around it rather than content touching the edges.
 * `bottom` reserves pixels at the foot: the fit uses the height above
 * it, and the centre is pushed up so the content lands in the middle of
 * what stays visible.
 */
export function cameraFor(frame, points, { fill = 0.62, maxZoom = 26, bottom = 0 } = {}) {
  const width = frame.width;
  const height = frame.height;
  const usable = Math.max(1, height - Math.max(0, bottom));
  if (!points || !points.length) {
    return { x: width / 2, y: usable / 2, zoom: 1 };
  }
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const point of points) {
    if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) continue;
    if (point[0] < minX) minX = point[0];
    if (point[0] > maxX) maxX = point[0];
    if (point[1] < minY) minY = point[1];
    if (point[1] > maxY) maxY = point[1];
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) {
    return { x: width / 2, y: usable / 2, zoom: 1 };
  }
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const zoom = Math.min(
    maxZoom,
    Math.max(1, Math.min((width * fill) / spanX, (usable * fill) / spanY)),
  );
  // screen = height/2 + zoom * (point - camera.y). Asking for the content
  // centre to land at usable/2 rearranges to exactly this offset.
  const shift = (height - usable) / (2 * zoom);
  return { x: (minX + maxX) / 2, y: (minY + maxY) / 2 + shift, zoom };
}

/**
 * A camera between two others.
 *
 * Two decisions, both about how the move *feels*.
 *
 * The fraction is smoothstepped, so the move starts and ends at rest
 * instead of switching velocity on at the first frame - the same reason
 * the camper's travel is eased.
 *
 * The magnification is interpolated logarithmically, because zoom is a
 * ratio: half way between 1x and 16x should look like 4x, not like 8.5x.
 * Interpolating it linearly is what makes an automatic zoom rush at the
 * start and crawl at the end.
 */
export function blendCamera(from, to, t) {
  const clamped = t <= 0 ? 0 : t >= 1 ? 1 : t;
  const eased = clamped * clamped * (3 - 2 * clamped);
  return {
    x: from.x + (to.x - from.x) * eased,
    y: from.y + (to.y - from.y) * eased,
    zoom: Math.exp(Math.log(from.zoom) + (Math.log(to.zoom) - Math.log(from.zoom)) * eased),
  };
}

/** Where a projected point lands on screen under a camera. */
export function onScreen(frame, camera, point) {
  return [
    frame.width / 2 + (point[0] - camera.x) * camera.zoom,
    frame.height / 2 + (point[1] - camera.y) * camera.zoom,
  ];
}

/** The SVG transform that puts the whole map under a camera at once. */
export function cameraTransform(frame, camera) {
  return `translate(${frame.width / 2} ${frame.height / 2}) scale(${camera.zoom}) translate(${-camera.x} ${-camera.y})`;
}
