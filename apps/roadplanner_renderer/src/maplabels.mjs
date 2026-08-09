/**
 * Which place names the map actually draws, and where.
 *
 * Every label used to be drawn: at its point, always to the right, with
 * a halo and nothing else. On a sparse map that is fine. On a real one
 * it produces the two failures somebody notices immediately without
 * being able to name them.
 *
 * **Names on top of each other.** Two stops forty kilometres apart are
 * twenty pixels apart on a map of Scandinavia, and two haloed names in
 * the same twenty pixels are not two names - they are a smudge. Neither
 * can be read, so drawing both is strictly worse than drawing one.
 *
 * **Names off the edge.** The text was anchored at the start and always
 * ran right. A stop near the right-hand edge ran its name out of the
 * frame, and a stop near the left could not do the same because nothing
 * ever flipped.
 *
 * So this decides, per frame, from geometry alone: which labels are
 * drawn, and on which side of their dot. Pure and stateless, which
 * matters more here than it looks - Remotion renders frames across
 * parallel workers, and anything that remembered the last frame would
 * pick different labels depending on which worker drew it.
 *
 * The ordering rule is the interesting one. Ties are not broken by
 * position on screen, because the screen moves: a label that wins at
 * frame 100 and loses at frame 101 flickers. They are broken by the
 * order the caller supplied, which is the map's own ranking - the
 * day's destination before the places passed on the way.
 */

/** Roughly how wide a name is, in a sans-serif at this size. */
export function estimateWidth(text, fontSize) {
  const characters = String(text ?? "").length;
  // 0.56 em per character is a little generous for lower case and a
  // little tight for capitals. Generous is the safe direction: the
  // consequence of over-estimating is a label that flips to the other
  // side slightly early, and of under-estimating is one that leaves the
  // frame.
  return characters * fontSize * 0.56;
}

/** The rectangle a label would occupy, given the side it is drawn on. */
export function labelBox(label, side) {
  const width = estimateWidth(label.text, label.fontSize);
  const height = label.fontSize * 1.15;
  const gap = label.gap ?? 10;
  const left = side === "left" ? label.x - gap - width : label.x + gap;
  return {
    left,
    right: left + width,
    top: label.y - height * 0.75,
    bottom: label.y + height * 0.35,
  };
}

const overlaps = (a, b, padding = 2) =>
  a.left - padding < b.right &&
  a.right + padding > b.left &&
  a.top - padding < b.bottom &&
  a.bottom + padding > b.top;

/**
 * Place the labels that can be read, and drop the ones that cannot.
 *
 * `labels` are `{text, x, y, fontSize, gap?, keep?}` in priority order.
 * `keep` marks a label that is drawn even where it collides - the one
 * thing on the map that has to be named, whatever else is near it.
 *
 * Returns the same objects with `side` and a box, in the order given.
 */
export function placeLabels(labels, options = {}) {
  const width = options.width ?? 1280;
  const height = options.height ?? 720;
  const margin = options.margin ?? 12;
  const placed = [];
  const taken = [];
  for (const label of labels || []) {
    if (!label || !String(label.text ?? "").trim()) continue;
    // Off-screen entirely: not a label at all.
    if (label.x < -200 || label.y < -200 || label.x > width + 200 || label.y > height + 200) {
      continue;
    }
    // Right first, because that is where a reader expects a name to
    // continue; left only when right would leave the frame.
    let side = "right";
    let box = labelBox(label, side);
    if (box.right > width - margin) {
      const flipped = labelBox(label, "left");
      // Flip only if the other side is genuinely better. A name wider
      // than the frame fits nowhere, and flipping it would move the
      // clipping from one edge to the other rather than fixing it.
      if (flipped.left >= margin) {
        side = "left";
        box = flipped;
      }
    }
    const collides = taken.some((other) => overlaps(box, other));
    if (collides && !label.keep) continue;
    placed.push({ ...label, side, box });
    taken.push(box);
  }
  return placed;
}

export default { estimateWidth, labelBox, placeLabels };
