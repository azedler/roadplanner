/**
 * Making text fit inside the frame, without cutting it off.
 *
 * Reported from the first full watch-through: "Text läuft teilweise
 * unten aus dem Bild." Two different bugs wear that description.
 *
 * The first is a caption clamped to two lines with `overflow: hidden`.
 * Nothing runs out of the frame there - a sentence simply ends mid-word,
 * which is worse, because the viewer cannot tell whether they missed
 * anything. Cutting text is not a layout strategy.
 *
 * The second is every other text in the film: a chapter title, an
 * opening paragraph, a written page. None of them had a bound at all.
 * A long German title at 62 px wraps to three lines, and the third one
 * is below the frame.
 *
 * So the fix has three parts and this module is the first: given a box
 * and a string, work out the largest size at which the whole string
 * fits. The second part is a safe area the box is derived from. The
 * third is the planner, which sends text that cannot fit over a
 * photograph to its own scene instead - because sometimes the honest
 * answer is not a smaller font.
 *
 * Why estimate rather than measure
 * --------------------------------
 *
 * Remotion renders frames across parallel browser tabs, each seeking an
 * arbitrary frame. A measured layout would have to settle before the
 * frame is captured, and a text that resizes on frame 3 and not on
 * frame 4 is a text that jitters. An estimate is stable by
 * construction: the same string always gets the same size, on every
 * frame and in every tab.
 *
 * The estimate is deliberately pessimistic. Guessing a text is wider
 * than it is costs a font size; guessing it is narrower puts it off the
 * screen, which is the bug being fixed.
 */

/** Frame geometry. 1280x720 is the development profile. */
export const FRAME_WIDTH = 1280;
export const FRAME_HEIGHT = 720;

/**
 * The safe area, in pixels of the 1280x720 frame.
 *
 * The bottom is the largest because that is where a player's controls
 * sit, where a phone's home indicator sits, and where the reported
 * overflow happened. Nothing readable may live below it.
 */
export const SAFE_BOTTOM = 72;
export const SAFE_SIDE = 96;
export const SAFE_TOP = 56;
export const MAX_TEXT_WIDTH = FRAME_WIDTH - SAFE_SIDE * 2;

/**
 * Average glyph width as a fraction of the font size, for a humanist
 * sans at these weights. Measured against the actual faces the renderer
 * has, rounded UP - see the pessimism note above.
 */
const WIDTH_RATIO = { 400: 0.52, 600: 0.545, 700: 0.56 };

/** Characters that fit on one line at a given size. */
export function charsPerLine(fontSize, width, weight = 400) {
  const ratio = WIDTH_RATIO[weight] ?? WIDTH_RATIO[400];
  return Math.max(1, Math.floor(width / (fontSize * ratio)));
}

/**
 * How many lines this text needs, wrapping on whole words.
 *
 * Word-aware because German is: "Übernachtungsmöglichkeit" does not
 * break, and a character-count estimate that assumes it does will
 * cheerfully report two lines for something that needs three.
 */
export function lineCount(text, fontSize, width, weight = 400) {
  const words = String(text ?? "").trim().split(/\s+/).filter(Boolean);
  if (!words.length) return 0;
  const perLine = charsPerLine(fontSize, width, weight);
  let lines = 1;
  let used = 0;
  for (const word of words) {
    const needed = used === 0 ? word.length : used + 1 + word.length;
    if (needed <= perLine) {
      used = needed;
      continue;
    }
    lines += 1;
    // A single word longer than the line takes as many lines as it needs
    // rather than pretending to fit.
    used = word.length > perLine ? word.length % perLine : word.length;
    if (word.length > perLine) lines += Math.floor(word.length / perLine) - 1;
  }
  return lines;
}

/**
 * The largest size at which the whole text fits the box, or the floor.
 *
 * Returns what was decided AND whether it actually fits, because the
 * caller has a real choice to make when it does not: a caption that
 * cannot fit over a photograph belongs in its own scene, and only the
 * caller knows whether that is available.
 */
export function fitText(text, options = {}) {
  const {
    width = MAX_TEXT_WIDTH,
    maxLines = 3,
    maxFontSize = 40,
    minFontSize = 22,
    weight = 400,
    lineHeight = 1.35,
    maxHeight = null,
  } = options;
  const clean = String(text ?? "").trim();
  if (!clean) return { fontSize: maxFontSize, lines: 0, fits: true, text: "" };

  for (let size = maxFontSize; size >= minFontSize; size -= 1) {
    const lines = lineCount(clean, size, width, weight);
    const height = lines * size * lineHeight;
    if (lines <= maxLines && (maxHeight === null || height <= maxHeight)) {
      return { fontSize: size, lines, fits: true, text: clean };
    }
  }
  const lines = lineCount(clean, minFontSize, width, weight);
  const height = lines * minFontSize * lineHeight;
  return {
    fontSize: minFontSize,
    lines,
    // Not a failure to render - a failure to fit. The text is still
    // returned whole; what the caller must not do is draw it here.
    fits: lines <= maxLines && (maxHeight === null || height <= maxHeight),
    text: clean,
  };
}

/**
 * The box a caption may use over a photograph.
 *
 * Derived from the safe area rather than typed in twice, so moving the
 * safe area moves the caption with it.
 */
export function captionBox() {
  return {
    width: MAX_TEXT_WIDTH,
    maxLines: 3,
    maxFontSize: 32,
    minFontSize: 24,
    lineHeight: 1.34,
    maxHeight: 150,
  };
}

export default { fitText, lineCount, charsPerLine, captionBox };
