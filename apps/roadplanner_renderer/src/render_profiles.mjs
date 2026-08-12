/**
 * How large the film is rendered - and nothing else.
 *
 * A render profile decides pixels, not content. The same FilmScenePlan
 * must produce the same film at every size: the same scenes in the same
 * order, the same photographs, the same clips, the same seconds. That
 * separation is the whole point of this table, and it is why the frame
 * rate is not in the caller's hands - thirty everywhere, so a profile
 * cannot quietly change the timing of a plan.
 *
 * The composition is authored at 1280x720 and stays there. A profile
 * scales that design surface, so nothing in the layout has to know which
 * profile is running; see the film component's design-surface wrapper.
 *
 * The same table exists on the integration side. A test reads both files
 * and compares them, because a profile that means 1440p in one deployable
 * and 1080p in the other is this project's oldest bug in a new place.
 */

/** The surface every layout is authored against. Never rendered directly. */
export const DESIGN_WIDTH = 1280;
export const DESIGN_HEIGHT = 720;

/** One frame rate for every profile. Deliberately not a setting. */
export const FILM_FPS = 30;

export const RENDER_PROFILES = {
  review_480: {
    id: "review_480",
    width: 854,
    height: 480,
    fps: FILM_FPS,
    // Visibly compressed, still readable: this exists to be looked at and
    // judged, not kept.
    crf: 30,
    x264Preset: "veryfast",
    // 50 MB for a twelve-minute film. The purpose is iteration speed and
    // a file that uploads anywhere without thinking about it.
    reviewTargetBytes: 50 * 1024 * 1024,
    label: "Review schnell · 480p",
    description:
      "Kleinste Datei, schnellster Render. Für schnelle Runden und zum Verschicken.",
    suffix: "review-480p",
    experimental: false,
    recommended: false,
  },
  review_720: {
    id: "review_720",
    width: 1280,
    height: 720,
    fps: FILM_FPS,
    // More compressed than the ordinary 720p output was, on purpose: this
    // is the version that has to survive an upload.
    crf: 28,
    x264Preset: "veryfast",
    // 90 MB for the same film - close to twice the small one, and that
    // difference is the entire reason both exist. Two profiles that
    // produced the same number of bytes would be one profile with two
    // names: the smaller picture would only be compressed less, which is
    // not what anybody picking "schnell" is asking for.
    reviewTargetBytes: 90 * 1024 * 1024,
    label: "Review detailliert · 720p",
    description:
      "Mehr Bilddetail für Schrift, Karten und Bewegung. Deutlich größere Datei.",
    suffix: "review-720p",
    experimental: false,
    recommended: false,
  },
  full_hd: {
    id: "full_hd",
    width: 1920,
    height: 1080,
    fps: FILM_FPS,
    crf: 21,
    x264Preset: "veryfast",
    label: "Full HD · 1080p",
    description: "Normale hochwertige Ausgabe.",
    suffix: "1080p",
    experimental: false,
    recommended: false,
  },
  high_quality: {
    id: "high_quality",
    width: 2560,
    height: 1440,
    fps: FILM_FPS,
    crf: 20,
    x264Preset: "veryfast",
    label: "Hohe Qualität · 1440p",
    description: "Für Archiv, Tablet und Fernseher. Empfohlen für finale Filme.",
    suffix: "1440p",
    experimental: false,
    recommended: true,
  },
  uhd_4k: {
    id: "uhd_4k",
    width: 3840,
    height: 2160,
    fps: FILM_FPS,
    crf: 20,
    x264Preset: "veryfast",
    label: "4K · experimentell",
    description:
      "Sehr lange Renderzeit, deutlich mehr Speicher, hoher RAM- und CPU-Bedarf. "
      + "Ob das auf einer bestimmten Home-Assistant-Hardware sinnvoll läuft, ist offen.",
    suffix: "4k",
    experimental: true,
    recommended: false,
  },
};

/** What a job means when it says nothing. The size rendered until now. */
export const DEFAULT_RENDER_PROFILE = "review_720";

export function renderProfile(id) {
  return RENDER_PROFILES[String(id ?? "")] ?? RENDER_PROFILES[DEFAULT_RENDER_PROFILE];
}

/**
 * How much work one frame of this profile is, against the 720p baseline.
 *
 * Used to scale the render deadline. A 4K frame is nine times the pixels
 * of a 720p one, so a ceiling measured at 720p would kill it - which is
 * the fixed-ceiling failure again, one variable further along.
 *
 * It only ever grows the budget, never shrinks it, and that floor is a
 * measurement rather than caution. The same trip rendered on the same
 * machine:
 *
 *     720p   161 ms per frame
 *     480p   139 ms per frame     - 2.25x fewer pixels, 14% less time
 *
 * At most 13% of a frame's cost is the pixels. The rest is layout and
 * JavaScript, and that is the same work whatever size it is drawn at. So
 * scaling the deadline DOWN by pixel count budgets a small render as if
 * it were quick when it is not: at 480p the guard had shrunk to 1.28x
 * the real duration, against 2.49x at 720p - thinnest exactly on the
 * profile meant for fast rounds, which is the wrong way round.
 */
export function pixelFactor(profile) {
  const entry = profile ?? RENDER_PROFILES[DEFAULT_RENDER_PROFILE];
  const base = DESIGN_WIDTH * DESIGN_HEIGHT;
  return Math.max(1, (entry.width * entry.height) / base);
}
