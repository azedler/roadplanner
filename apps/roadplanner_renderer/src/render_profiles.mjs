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
    label: "Review schnell · 480p",
    description: "Schnellste Abnahmeversion für Entwicklung und Filmabnahme.",
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
    label: "Review detailliert · 720p",
    description: "Kleine Datei mit guter Lesbarkeit für die feinere Abnahme.",
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
 * of a 720p one and takes correspondingly longer, so a ceiling measured
 * at 720p would kill it - which is exactly the failure the fixed ceiling
 * caused before, one variable further along.
 */
export function pixelFactor(profile) {
  const entry = profile ?? RENDER_PROFILES[DEFAULT_RENDER_PROFILE];
  const base = DESIGN_WIDTH * DESIGN_HEIGHT;
  return Math.max(0.25, (entry.width * entry.height) / base);
}
