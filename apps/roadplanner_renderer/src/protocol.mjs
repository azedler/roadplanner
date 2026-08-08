/**
 * The app's own implementation of the shared contract.
 *
 * It deliberately does NOT import anything from Roadplanner's Python side:
 * the two implementations are independent on purpose, so a change on one
 * side that breaks the agreement shows up as a failing test rather than
 * being silently absorbed by shared code. The contract is the JSON, not a
 * library.
 *
 * Node built-ins only — the app ships with zero npm dependencies, which is
 * the cheapest possible supply chain for something that runs beside a
 * user's Home Assistant.
 */
import { createHash } from "node:crypto";

export const SCHEMA_VERSION = 1;
export const PROTOCOL_VERSION = 1;
export const RENDERER_NAME = "roadplanner-renderer-poc";

export const APP_STATES = ["starting", "ready", "degraded", "stopping"];
export const JOB_STATES = [
  "queued",
  "claimed",
  "running",
  "completed",
  "failed",
  "expired",
];
export const TERMINAL_JOB_STATES = new Set(["completed", "failed", "expired"]);

export const ACTIONS = [
  "ping",
  "create_test_artifact",
  "render_remotion_test",
  "render_trip_day",
  "render_trip_film",
];
export const ARTIFACT_TEXT = "roadplanner-renderer-poc.txt";
export const ARTIFACT_IMAGE = "roadplanner-renderer-poc.svg";
export const ARTIFACT_VIDEO = "roadplanner-remotion-test.mp4";
export const ARTIFACT_TRIP_DAY_VIDEO = "roadplanner-trip-day.mp4";
export const ARTIFACT_TRIP_FILM_VIDEO = "roadplanner-trip-film.mp4";

export const MAX_JSON_BYTES = 64 * 1024;
export const MAX_MESSAGE_LENGTH = 120;

// --- the render package -------------------------------------------------

export const PACKAGE_VERSION = 1;
export const PACKAGE_FILENAME = "package.json";
export const MAX_PACKAGE_IMAGES = 5;
export const MAX_PACKAGE_IMAGE_BYTES = 400 * 1024;
export const MAX_PACKAGE_BYTES = 3 * 1024 * 1024;
export const MAX_PACKAGE_STOPS = 12;

const SHA256_RE = /^[0-9a-f]{64}$/;

/**
 * The one place an image filename is built, and it is built from a number.
 *
 * The package deliberately carries no filenames. Nothing a writer put in
 * the manifest can therefore reach a path join — the defence is that the
 * string never exists, not that it is escaped.
 */
export function packageImageFilename(index) {
  if (!Number.isInteger(index) || index < 1 || index > MAX_PACKAGE_IMAGES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültiger Bildindex im Renderpaket.");
  }
  return `photo-${index}.jpg`;
}

/**
 * Validate the package with this app's own rules.
 *
 * Roadplanner validates before writing; this validates before reading.
 * The two implementations are independent on purpose — the contract is
 * the JSON, and a disagreement has to surface as a failure rather than be
 * absorbed by shared code.
 */
export function parsePackage(raw) {
  if (Buffer.byteLength(raw) > MAX_JSON_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket überschreitet die Größengrenze.");
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket ist kein gültiges JSON.");
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket ist kein Objekt.");
  }
  if (payload.package_version !== PACKAGE_VERSION) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Nicht unterstützte Version des Renderpakets.",
    );
  }
  const day = payload.day;
  if (day === null || typeof day !== "object" || Array.isArray(day) || !cleanText(day.day_id, 80)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket ohne Tagesangaben.");
  }
  const images = payload.images;
  if (!Array.isArray(images) || images.length < 1 || images.length > MAX_PACKAGE_IMAGES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket enthält keine gültige Bildliste.");
  }
  const seen = new Set();
  let total = 0;
  const declared = images.map((entry) => {
    if (entry === null || typeof entry !== "object" || Array.isArray(entry)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag ist kein Objekt.");
    }
    const filename = packageImageFilename(entry.index);
    if (seen.has(entry.index)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Doppelter Bildindex im Renderpaket.");
    }
    seen.add(entry.index);
    const size = entry.size_bytes;
    if (!Number.isInteger(size) || size <= 0 || size > MAX_PACKAGE_IMAGE_BYTES) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag mit ungültiger Größe.");
    }
    if (!SHA256_RE.test(String(entry.sha256 ?? ""))) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag ohne gültigen SHA-256.");
    }
    total += size;
    return { index: entry.index, filename, sizeBytes: size, sha256: entry.sha256 };
  });
  if (total > MAX_PACKAGE_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket überschreitet die Größengrenze.");
  }
  const stops = Array.isArray(payload.stops) ? payload.stops : [];
  if (stops.length > MAX_PACKAGE_STOPS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Renderpaket enthält zu viele Stopps.");
  }
  return {
    tripTitle: cleanText(payload.trip_title, 120),
    day: {
      dayId: cleanText(day.day_id, 80),
      date: cleanText(day.date, 40),
      title: cleanText(day.title, 120),
      summary: cleanText(day.summary, 400),
      number: Number.isInteger(day.number) ? day.number : null,
      count: Number.isInteger(day.count) ? day.count : null,
      distanceKm: typeof day.distance_km === "number" ? day.distance_km : null,
      durationMinutes:
        typeof day.duration_minutes === "number" ? day.duration_minutes : null,
    },
    stops: stops
      .filter((stop) => stop !== null && typeof stop === "object" && !Array.isArray(stop))
      .map((stop) => ({ name: cleanText(stop.name, 80), time: cleanText(stop.time, 10) }))
      .filter((stop) => stop.name),
    images: declared,
  };
}

export const ERROR_INVALID_JOB = "INVALID_JOB";
export const ERROR_UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL";
export const ERROR_EXPIRED = "EXPIRED";
export const ERROR_INTERNAL = "INTERNAL";

const JOB_ID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export class ProtocolError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
    this.retryable = false;
  }
}

export const formatTime = (date) =>
  `${new Date(date).toISOString().slice(0, 19)}Z`;

export const parseTime = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return null;
  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? null : parsed;
};

export const cleanText = (value, limit = MAX_MESSAGE_LENGTH) =>
  String(value ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .slice(0, limit);

export const sha256 = (data) => createHash("sha256").update(data).digest("hex");

export const isJobId = (value) => JOB_ID_RE.test(String(value ?? ""));

/**
 * Parse a job file. Every rejection carries a code, because "the app
 * ignored my job" is the one failure mode a file channel cannot explain by
 * itself — there is no connection to refuse.
 */
export function parseJob(raw, { now }) {
  if (Buffer.byteLength(raw) > MAX_JSON_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag überschreitet die Größengrenze.");
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag ist kein gültiges JSON.");
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Auftrag ist kein Objekt.");
  }
  // Version first: an unknown protocol must be refused, not interpreted.
  if (
    payload.schema_version !== SCHEMA_VERSION ||
    payload.protocol_version !== PROTOCOL_VERSION
  ) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Der Auftrag verwendet eine nicht unterstützte Protokollversion.",
    );
  }
  if (!isJobId(payload.job_id)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültige Job-ID.");
  }
  if (!ACTIONS.includes(payload.action)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Unbekannte Aktion.");
  }
  const message = cleanText(payload?.input?.message);
  if (!message) {
    throw new ProtocolError(ERROR_INVALID_JOB, "input.message fehlt.");
  }
  const expires = parseTime(payload.expires_at);
  if (expires === null) {
    throw new ProtocolError(ERROR_INVALID_JOB, "expires_at fehlt oder ist ungültig.");
  }
  if (expires <= now) {
    throw new ProtocolError(ERROR_EXPIRED, "Der Auftrag ist abgelaufen.");
  }
  return { jobId: payload.job_id, action: payload.action, message, expires };
}

export const envelope = (extra) => ({
  schema_version: SCHEMA_VERSION,
  protocol_version: PROTOCOL_VERSION,
  ...extra,
});

export function buildStatus({ jobId, state, progress, updatedAt, error }) {
  if (!JOB_STATES.includes(state)) {
    throw new ProtocolError(ERROR_INTERNAL, `Unbekannter Jobzustand: ${state}`);
  }
  const status = envelope({
    job_id: jobId,
    state,
    updated_at: formatTime(updatedAt),
  });
  if (typeof progress === "number") {
    status.progress = Math.max(0, Math.min(1, progress));
  }
  if (error) {
    status.error = {
      code: error.code || ERROR_INTERNAL,
      message: cleanText(error.message, 300),
      retryable: Boolean(error.retryable),
    };
  }
  return status;
}

/**
 * A deterministic test image. No external assets, no fonts to resolve, no
 * browser — the point is to prove that a *renderable* artefact survives the
 * channel, not to render anything difficult.
 */
export function buildSvg({ jobId, message, timestamp }) {
  const shortId = String(jobId).slice(0, 8);
  const escape = (value) =>
    String(value).replace(
      /[<>&"]/g,
      (char) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[char],
    );
  return `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360" role="img" aria-label="Roadplanner Renderer App PoC">
  <rect width="640" height="360" fill="#101725"/>
  <rect x="24" y="24" width="592" height="312" fill="none" stroke="#3d7dca" stroke-width="2"/>
  <text x="56" y="120" fill="#f5f7fa" font-family="sans-serif" font-size="30">Roadplanner Renderer App PoC</text>
  <text x="56" y="176" fill="#9fb3c8" font-family="sans-serif" font-size="19">${escape(message)}</text>
  <text x="56" y="228" fill="#9fb3c8" font-family="monospace" font-size="17">Job ${escape(shortId)}</text>
  <text x="56" y="268" fill="#9fb3c8" font-family="monospace" font-size="17">${escape(timestamp)}</text>
</svg>
`;
}

export function buildText({ jobId, message, timestamp }) {
  return [
    "Roadplanner Renderer App PoC",
    `Job:       ${jobId}`,
    `Nachricht: ${message}`,
    `Erzeugt:   ${timestamp}`,
    `Renderer:  ${RENDERER_NAME}`,
    "",
    "Dieses Artefakt beweist ausschliesslich den Weg Auftrag -> App -> Ergebnis.",
    "Es enthaelt kein Remotion, keinen Browser und keine Reisedaten.",
    "",
  ].join("\n");
}

export function buildResult({ jobId, completedAt, artifacts, video, timings }) {
  const result = envelope({
    job_id: jobId,
    state: "completed",
    completed_at: formatTime(completedAt),
    artifacts,
  });
  // Only present for a render job. What ffprobe measured, not what the job
  // asked for - the difference is the whole point of validating.
  if (video) result.video = video;
  if (timings) result.timings = timings;
  return result;
}

export function buildHeartbeat({ state, startedAt, now, appVersion }) {
  if (!APP_STATES.includes(state)) {
    throw new ProtocolError(ERROR_INTERNAL, `Unbekannter App-Zustand: ${state}`);
  }
  return envelope({
    renderer: RENDERER_NAME,
    app_version: appVersion,
    state,
    started_at: formatTime(startedAt),
    heartbeat_at: formatTime(now),
    capabilities: [...ACTIONS],
  });
}


// --- the film package ---------------------------------------------------

export const FILM_PACKAGE_VERSION = 1;
export const FILM_MANIFEST_FILENAME = "film.json";
export const MAX_FILM_JSON_BYTES = 512 * 1024;
export const MAX_FILM_CHAPTERS = 45;
export const MAX_FILM_PHOTOS_PER_CHAPTER = 4;
export const MAX_FILM_IMAGES = 90;
export const MAX_FILM_IMAGE_BYTES = 280 * 1024;

const FILM_PHOTO_RE = /^photos\/c(\d{2})-([1-4])\.jpg$/;

export const FILM_PLAN_VERSION = 1;
export const FILM_PLAN_FPS = 30;
// The finite library. A type outside it is refused rather than drawn.
export const FILM_SCENE_TYPES = new Set([
  "intro",
  "chapter_card",
  "photo",
  "hero",
  "collage",
  "text",
  "outro",
  "outro_collage",
  "map_start",
  "map_leg",
  "map_full",
  "crew",
]);
// Bounds, so a malformed plan cannot ask for an hour of video or ten
// thousand sequences.
const MAX_FILM_SCENES = 400;
const MAX_FILM_SCENE_FRAMES = 900;
const MAX_FILM_TOTAL_FRAMES = 30 * 900;

/**
 * The one place a film photo path is accepted.
 *
 * It is matched against a pattern that only integers can satisfy, and the
 * numbers in it are checked against the chapter it claims to belong to.
 * A path is the one thing in a package that could reach outside the job
 * directory, so it is the one thing that is never taken on trust.
 */
export function filmPhotoPath(value, chapterIndex, position) {
  const text = String(value ?? "");
  const match = FILM_PHOTO_RE.exec(text);
  if (!match) {
    throw new ProtocolError(ERROR_INVALID_JOB, `Ungültiger Bildpfad: ${text.slice(0, 60)}`);
  }
  if (Number(match[1]) !== chapterIndex || Number(match[2]) !== position) {
    throw new ProtocolError(
      ERROR_INVALID_JOB,
      "Bildpfad passt nicht zu seiner Kapitelposition.",
    );
  }
  return text;
}

/** Validate a whole-trip film package with this app's own rules. */
export function parseFilmPackage(raw) {
  if (Buffer.byteLength(raw) > MAX_FILM_JSON_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket überschreitet die Größengrenze.");
  }
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket ist kein gültiges JSON.");
  }
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket ist kein Objekt.");
  }
  if (payload.film_package_version !== FILM_PACKAGE_VERSION) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Nicht unterstützte Version des Filmpakets.",
    );
  }
  const chapters = payload.chapters;
  if (!Array.isArray(chapters) || !chapters.length || chapters.length > MAX_FILM_CHAPTERS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket ohne gültige Kapitelliste.");
  }
  let images = 0;
  const parsed = chapters.map((chapter, index) => {
    if (chapter === null || typeof chapter !== "object" || Array.isArray(chapter)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kapitel ist kein Objekt.");
    }
    if (chapter.index !== index) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kapitel steht nicht an seiner Position.");
    }
    const rawImages = Array.isArray(chapter.images) ? chapter.images : [];
    if (rawImages.length > MAX_FILM_PHOTOS_PER_CHAPTER) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kapitel mit zu vielen Bildern.");
    }
    const photos = rawImages.map((image, position) => {
      if (image === null || typeof image !== "object") {
        throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag ist kein Objekt.");
      }
      const size = image.size_bytes;
      if (!Number.isInteger(size) || size <= 0 || size > MAX_FILM_IMAGE_BYTES) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag mit ungültiger Größe.");
      }
      if (!/^[0-9a-f]{64}$/.test(String(image.sha256 ?? ""))) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Bildeintrag ohne gültigen SHA-256.");
      }
      images += 1;
      return {
        path: filmPhotoPath(image.path, index, position + 1),
        sizeBytes: size,
        sha256: image.sha256,
        // How the picture is shaped. A portrait photograph filled into a
        // 16:9 frame loses its top and bottom, which is where the sky and
        // the person are, so the composition needs to know before it
        // decides how to place it.
        width: Number.isInteger(image.width) && image.width > 0 ? image.width : 0,
        height: Number.isInteger(image.height) && image.height > 0 ? image.height : 0,
        orientation: FILM_ORIENTATIONS.has(image.orientation)
          ? image.orientation
          : "landscape",
        // Two colours sampled from the picture itself, so the space
        // beside an upright photograph can be filled without filtering
        // a full frame thirty times a second.
        colorTop: filmColour(image.color_top, "#161d29"),
        colorBottom: filmColour(image.color_bottom, "#0d121a"),
      };
    });
    return {
      chapterId: cleanText(chapter.chapter_id, 200),
      index,
      date: cleanText(chapter.date, 40),
      title: cleanText(chapter.title, 120),
      // Not cleanText: a hand-written story may have paragraphs, and
      // collapsing them here would silently undo the editor.
      story: String(chapter.story ?? "").slice(0, 1200),
      storySource: cleanText(chapter.story_source, 20) || "composed",
      importance: cleanText(chapter.importance, 20) || "normal",
      storyRole: cleanText(chapter.story_role, 20) || "journey",
      dayNumber: Number.isInteger(chapter.day_number) ? chapter.day_number : index + 1,
      distanceKm: typeof chapter.distance_km === "number" ? chapter.distance_km : null,
      durationMinutes:
        typeof chapter.duration_minutes === "number" ? chapter.duration_minutes : null,
      stopCount: Number.isInteger(chapter.stop_count) ? chapter.stop_count : 0,
      photoCount: Number.isInteger(chapter.photo_count) ? chapter.photo_count : 0,
      stops: (Array.isArray(chapter.stops) ? chapter.stops : [])
        .map((stop) => cleanText(stop, 80))
        .filter(Boolean)
        .slice(0, 6),
      photos,
    };
  });
  if (images > MAX_FILM_IMAGES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket enthält zu viele Bilder.");
  }
  const trip = payload.trip ?? {};
  return {
    manifestContentHash: cleanText(payload.manifest_content_hash, 64),
    trip: {
      title: cleanText(trip.title, 120),
      startDate: cleanText(trip.start_date, 40),
      endDate: cleanText(trip.end_date, 40),
      chapterCount: Number.isInteger(trip.chapter_count) ? trip.chapter_count : parsed.length,
      distanceKm: typeof trip.distance_km === "number" ? trip.distance_km : null,
      photoCount: Number.isInteger(trip.photo_count) ? trip.photo_count : 0,
    },
    chapters: parsed,
    narrative: parseNarrative(payload.narrative),
    crew: parseCrew(payload.crew),
    characters: parseCharacters(payload.characters),
    music: parseMusic(payload.music),
    mapContext: parseMapContext(payload.map_context),
    scenes: parseScenePlan(payload.scene_plan, parsed.length),
  };
}

export const FILM_ORIENTATIONS = new Set(["landscape", "portrait", "square"]);


// --- the cast, as pictures rather than as drawings -----------------------

export const MAX_CHARACTER_BYTES = 260 * 1024;
export const CHARACTER_KINDS = new Set(["vehicle", "crew"]);
export const CHARACTER_VARIANTS = new Set(["map", "side"]);
const CHARACTER_PATH_RE = /^characters\/(vehicle|crew)-(map|side)\.png$/;

/**
 * Confirmed character illustrations that travel with the job.
 *
 * Absent is the normal case and always will be: a trip whose vehicle has
 * no approved illustration gets the drawn camper, which is a worse
 * picture and a complete film. Nothing here may be a link, for the same
 * reason as the crew portraits.
 */
export function parseCharacters(value) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Figurenangaben sind kein Objekt.");
  }
  const assets = value.assets;
  if (!Array.isArray(assets) || !assets.length || assets.length > 4) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Figurenangaben ohne gültige Bilder.");
  }
  return {
    assets: assets.map((asset) => {
      if (asset === null || typeof asset !== "object" || Array.isArray(asset)) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Figurenbild ist kein Objekt.");
      }
      if (!CHARACTER_KINDS.has(asset.kind) || !CHARACTER_VARIANTS.has(asset.variant)) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Figurenbild mit unbekannter Art.");
      }
      const path = String(asset.path ?? "");
      const match = CHARACTER_PATH_RE.exec(path);
      if (!match || match[1] !== asset.kind || match[2] !== asset.variant) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Ungültiger Figurenbildpfad.");
      }
      return {
        kind: asset.kind,
        variant: asset.variant,
        path,
        sha256: (() => {
          if (!/^[0-9a-f]{64}$/.test(String(asset.sha256 ?? ""))) {
            throw new ProtocolError(ERROR_INVALID_JOB, "Figurenbild ohne gültige Prüfsumme.");
          }
          return asset.sha256;
        })(),
        sizeBytes: (() => {
          const size = asset.size_bytes;
          if (!Number.isInteger(size) || size <= 0 || size > MAX_CHARACTER_BYTES) {
            throw new ProtocolError(ERROR_INVALID_JOB, "Figurenbild mit ungültiger Größe.");
          }
          return size;
        })(),
      };
    }),
  };
}

// --- the crew, and the music -------------------------------------------

export const MAX_CREW_MEMBERS = 6;
export const MAX_CREW_PORTRAIT_BYTES = 120 * 1024;
const CREW_PATH_RE = /^crew\/member-(\d{2})\.jpg$/;

/** The one place a crew portrait path is accepted, built from a number. */
export function crewPortraitPath(value, index) {
  const text = String(value ?? "");
  const match = CREW_PATH_RE.exec(text);
  if (!match || Number(match[1]) !== index) {
    throw new ProtocolError(ERROR_INVALID_JOB, `Ungültiger Crewbildpfad: ${text.slice(0, 60)}`);
  }
  return text;
}

/**
 * Who is travelling: display names, and portraits that are files.
 *
 * Absent is normal - a trip without a crew record simply has no crew
 * scene. What is refused is anything that looks like a link: the crew
 * portrait route is guarded by an unguessable filename, which is a
 * bearer secret rather than a session, and a package written into a
 * shared folder must not carry one.
 */
export function parseCrew(value) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Crewangaben sind kein Objekt.");
  }
  const members = value.members;
  if (!Array.isArray(members) || !members.length || members.length > MAX_CREW_MEMBERS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Crewangaben ohne gültige Mitglieder.");
  }
  return {
    members: members.map((member, index) => {
      if (member === null || typeof member !== "object" || Array.isArray(member)) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Crewmitglied ist kein Objekt.");
      }
      const name = cleanText(member.name, 40);
      if (!name) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Crewmitglied ohne Namen.");
      }
      for (const entry of Object.values(member)) {
        const text = String(entry ?? "");
        if (text.includes("://") || text.startsWith("/api/")) {
          throw new ProtocolError(ERROR_INVALID_JOB, "Crewangaben dürfen keine Adressen enthalten.");
        }
      }
      const path = member.path ? crewPortraitPath(member.path, index) : "";
      const size = member.size_bytes;
      if (path) {
        if (!Number.isInteger(size) || size <= 0 || size > MAX_CREW_PORTRAIT_BYTES) {
          throw new ProtocolError(ERROR_INVALID_JOB, "Crewbild mit ungültiger Größe.");
        }
        if (!/^[0-9a-f]{64}$/.test(String(member.sha256 ?? ""))) {
          throw new ProtocolError(ERROR_INVALID_JOB, "Crewbild ohne gültigen SHA-256.");
        }
      }
      return { name, path, sizeBytes: path ? size : 0, sha256: path ? member.sha256 : "" };
    }),
  };
}

export const MUSIC_FILENAME = "music/track";
export const MAX_MUSIC_BYTES = 40 * 1024 * 1024;
export const MUSIC_EXTENSIONS = new Set([".mp3", ".m4a", ".ogg", ".wav", ".flac"]);

/**
 * The soundtrack, or nothing.
 *
 * A film without music has to stay a complete film, so absent is a
 * first-class answer rather than a failure. What travels is a file in
 * the job folder and a volume - never a path from the user's machine,
 * because that string would be a filesystem location arriving from
 * outside and reaching a reader.
 */
export function parseMusic(value) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Musikangaben sind kein Objekt.");
  }
  const path = String(value.path ?? "");
  const extension = path.slice(path.lastIndexOf("."));
  if (!path.startsWith(`${MUSIC_FILENAME}`) || !MUSIC_EXTENSIONS.has(extension)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Musikdatei liegt nicht im Auftragsordner.");
  }
  const size = value.size_bytes;
  if (!Number.isInteger(size) || size <= 0 || size > MAX_MUSIC_BYTES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Musikdatei mit ungültiger Größe.");
  }
  if (!/^[0-9a-f]{64}$/.test(String(value.sha256 ?? ""))) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Musikdatei ohne gültigen SHA-256.");
  }
  const volume = typeof value.volume === "number" ? value.volume : 0.45;
  return {
    path,
    sizeBytes: size,
    sha256: value.sha256,
    // Clamped here rather than trusted: it reaches an audio node.
    volume: Math.max(0, Math.min(1, volume)),
    title: cleanText(value.title, 80),
  };
}

const HEX_COLOUR_RE = /^#[0-9a-f]{6}$/;

/** A colour, or the fallback. It reaches a stylesheet, so it is matched. */
function filmColour(value, fallback) {
  const text = String(value ?? "").toLowerCase();
  return HEX_COLOUR_RE.test(text) ? text : fallback;
}

// --- the map context ----------------------------------------------------

export const MAP_CONTEXT_VERSION = 1;
export const MAP_SEGMENT_MODES = new Set(["driving", "ferry", "break", "direct"]);
export const MAX_MAP_POINTS = 6000;

/**
 * The trip's geography, checked coordinate by coordinate.
 *
 * It is the only numeric input that reaches a projection, and a
 * projection given NaN silently draws nothing rather than failing — a
 * blank map with a successful exit code is the worst outcome available
 * here, so every value is proven finite and in range before it is used.
 *
 * Absent is legal and is not the same as broken: a trip whose roadbook
 * has no coordinates simply has no map, and the film renders without
 * one. Present-but-malformed is refused.
 */
export function parseMapContext(value) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkontext ist kein Objekt.");
  }
  if (value.map_context_version !== MAP_CONTEXT_VERSION) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Nicht unterstützte Version des Kartenkontexts.",
    );
  }
  const chapters = value.chapters;
  if (!Array.isArray(chapters) || !chapters.length || chapters.length > MAX_FILM_CHAPTERS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkontext ohne gültige Kapitelliste.");
  }
  let points = 0;
  const parsed = chapters.map((chapter) => {
    if (chapter === null || typeof chapter !== "object" || Array.isArray(chapter)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkapitel ist kein Objekt.");
    }
    const chapterId = cleanText(chapter.chapter_id, 200);
    if (!chapterId) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkapitel ohne Kennung.");
    }
    const segments = chapter.segments;
    if (!Array.isArray(segments) || !segments.length) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkapitel ohne Streckenabschnitte.");
    }
    const lines = segments.map((segment) => {
      if (segment === null || typeof segment !== "object" || Array.isArray(segment)) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Streckenabschnitt ist kein Objekt.");
      }
      if (!MAP_SEGMENT_MODES.has(segment.mode)) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Streckenabschnitt mit unbekannter Art.");
      }
      const raw = segment.points;
      if (!Array.isArray(raw) || raw.length < 2) {
        throw new ProtocolError(ERROR_INVALID_JOB, "Streckenabschnitt ohne Punkte.");
      }
      points += raw.length;
      return { mode: segment.mode, points: raw.map(mapPoint) };
    });
    return {
      chapterId,
      index: Number.isInteger(chapter.index) ? chapter.index : 0,
      segments: lines,
      start: mapPoint(chapter.start),
      end: mapPoint(chapter.end),
      bbox: mapBbox(chapter.bbox),
      hasFerry: Boolean(chapter.has_ferry),
      estimated: Boolean(chapter.estimated),
      places: mapPlaces(chapter.places),
    };
  });
  if (points > MAX_MAP_POINTS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Kartenkontext enthält zu viele Punkte.");
  }
  return {
    bbox: mapBbox(value.bbox),
    start: mapPoint(value.start),
    end: mapPoint(value.end),
    hasFerry: Boolean(value.has_ferry),
    pointCount: points,
    chapters: parsed,
  };
}


export const MAX_MAP_PLACES = 4;

/**
 * The named places a day puts on the map.
 *
 * Absent is normal: a day whose stops have no coordinates has no places,
 * and the map simply shows no labels. What is refused is a list long
 * enough to bury the map in type - the reduced style is a requirement,
 * not a preference, and four labels is where a 1280-pixel frame stops
 * being readable.
 */
function mapPlaces(value) {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Orte im Kartenkapitel sind keine Liste.");
  }
  if (value.length > MAX_MAP_PLACES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Zu viele Orte im Kartenkapitel.");
  }
  return value.map((place) => {
    if (place === null || typeof place !== "object" || Array.isArray(place)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Ort im Kartenkapitel ist kein Objekt.");
    }
    const name = cleanText(place.name, 42);
    if (!name) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Ort ohne Namen.");
    }
    return { name, point: mapPoint(place.point), rank: place.rank === 0 ? 0 : 1 };
  });
}

/** One [lon, lat] pair, refused unless it is a real place on Earth. */
function mapPoint(value) {
  if (!Array.isArray(value) || value.length < 2) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültige Koordinate im Kartenkontext.");
  }
  const lon = value[0];
  const lat = value[1];
  if (
    typeof lon !== "number" ||
    typeof lat !== "number" ||
    !Number.isFinite(lon) ||
    !Number.isFinite(lat) ||
    lon < -180 ||
    lon > 180 ||
    lat < -90 ||
    lat > 90
  ) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültige Koordinate im Kartenkontext.");
  }
  return [lon, lat];
}

function mapBbox(value) {
  if (!Array.isArray(value) || value.length !== 4) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Ungültiger Kartenausschnitt.");
  }
  const [west, south, east, north] = [
    mapPoint([value[0], value[1]]),
    mapPoint([value[2], value[3]]),
  ].flat();
  if (east < west || north < south) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Kartenausschnitt ist verdreht.");
  }
  return [west, south, east, north];
}

/** The trip-level arc, or null. Absent and empty must stay different. */
function parseNarrative(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const arc = {
    titleVariant: cleanText(value.title_variant, 120),
    subtitle: cleanText(value.subtitle, 120),
    opening: String(value.opening ?? "").slice(0, 1200),
    closing: String(value.closing ?? "").slice(0, 1200),
    motifs: (Array.isArray(value.motifs) ? value.motifs : [])
      .map((motif) => cleanText(motif, 80))
      .filter(Boolean)
      .slice(0, 5),
  };
  const empty =
    !arc.titleVariant && !arc.subtitle && !arc.opening && !arc.closing && !arc.motifs.length;
  return empty ? null : arc;
}

/**
 * The shot list, checked before anything is drawn.
 *
 * Every scene type is looked up in a fixed library, so a type this build
 * has no component for has to be refused here rather than rendered as a
 * blank frame nobody can explain. The same reasoning covers the frame
 * counts: they decide the length of the video, so a plan whose parts do
 * not add up to its stated total is not a plan.
 */
function parseScenePlan(value, chapterCount) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Filmpaket ohne Szenenplan.");
  }
  if (value.plan_version !== FILM_PLAN_VERSION) {
    throw new ProtocolError(
      ERROR_UNSUPPORTED_PROTOCOL,
      "Nicht unterstützte Version des Szenenplans.",
    );
  }
  if (value.fps !== FILM_PLAN_FPS) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Szenenplan mit fremder Bildrate.");
  }
  const scenes = value.scenes;
  if (!Array.isArray(scenes) || !scenes.length || scenes.length > MAX_FILM_SCENES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Szenenplan ohne gültige Szenenliste.");
  }
  let total = 0;
  const parsed = scenes.map((scene) => {
    if (scene === null || typeof scene !== "object" || Array.isArray(scene)) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Szene ist kein Objekt.");
    }
    const type = cleanText(scene.type, 30);
    if (!FILM_SCENE_TYPES.has(type)) {
      throw new ProtocolError(ERROR_INVALID_JOB, `Unbekannter Szenentyp: ${type}`);
    }
    const frames = scene.frames;
    if (!Number.isInteger(frames) || frames <= 0 || frames > MAX_FILM_SCENE_FRAMES) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Szene mit ungültiger Länge.");
    }
    total += frames;
    const chapterIndex = Number.isInteger(scene.chapter_index) ? scene.chapter_index : -1;
    if (chapterIndex >= chapterCount) {
      throw new ProtocolError(ERROR_INVALID_JOB, "Szene zeigt auf ein fehlendes Kapitel.");
    }
    return {
      type,
      chapterIndex,
      // The map is addressed by chapter id, not by position: the map
      // context skips chapters that have no geography, so its list and
      // the film's list are deliberately not the same length.
      chapterId: cleanText(scene.chapter_id, 200),
      frames,
      enter: cleanText(scene.enter, 20) || "fade",
      photos: (Array.isArray(scene.photos) ? scene.photos : [])
        .filter((position) => Number.isInteger(position) && position >= 0)
        .slice(0, MAX_FILM_PHOTOS_PER_CHAPTER),
      paths: (Array.isArray(scene.paths) ? scene.paths : [])
        .map((path, position) => filmPhotoPathAnywhere(path))
        .filter(Boolean)
        .slice(0, 8),
    };
  });
  if (total !== value.total_frames) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Szenenplan: Gesamtlänge passt nicht.");
  }
  if (total > MAX_FILM_TOTAL_FRAMES) {
    throw new ProtocolError(ERROR_INVALID_JOB, "Szenenplan ist zu lang.");
  }
  return parsed;
}

/**
 * A photo path for the closing collage, where the picture comes from
 * whichever chapter it came from - so it cannot be checked against one
 * position, only against the shape every film path has.
 */
function filmPhotoPathAnywhere(value) {
  const text = String(value ?? "");
  return FILM_PHOTO_RE.test(text) ? text : "";
}
