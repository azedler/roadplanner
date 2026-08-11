/**
 * Render the test composition and prove what came out.
 *
 * Called by the worker, never by a user. Everything it needs is already in
 * the image: the bundle was built at image build time, the browser is a
 * system package, and ffprobe comes from the same image. **Nothing is
 * downloaded here** — `ensureBrowser` is deliberately never called, because
 * a renderer that fetches a browser onto someone's Home Assistant while a
 * job runs is not a renderer anyone should ship.
 *
 * Two things are measured rather than assumed:
 *
 * - the browser start and the render are timed separately, because they
 *   fail and scale for different reasons;
 * - **exit code 0 is a claim, ffprobe is the check.** Remotion returning
 *   without an error does not prove that a playable H.264 file of the right
 *   size and length exists on disk. The file only counts as a result after
 *   its codec, container, resolution and duration have been read back.
 */
import { execFile, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { statfsSync } from "node:fs";
import os from "node:os";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import { openBrowser, renderMedia, selectComposition } from "@remotion/renderer";

import { FILM_LIMITS, renderCeilingMs } from "./film_limits.mjs";
import {
  DEFAULT_RENDER_PROFILE,
  pixelFactor,
  renderProfile,
} from "./render_profiles.mjs";
import {
  DEFAULT_REVIEW_PROFILE,
  reviewBitrate,
  reviewCopyArgs,
  reviewProfile,
} from "./review_copy.mjs";
import {
  FILM_MANIFEST_FILENAME,
  MAX_FILM_IMAGE_BYTES,
  MAX_FILM_JSON_BYTES,
  MAX_MUSIC_BYTES,
  MAX_JSON_BYTES,
  MAX_PACKAGE_IMAGE_BYTES,
  PACKAGE_FILENAME,
  parseFilmPackage,
  parsePackage,
} from "./protocol.mjs";

const execFileAsync = promisify(execFile);

const BUNDLE_DIR = process.env.ROADPLANNER_BUNDLE_DIR || "/opt/roadplanner-renderer/bundle";
const BROWSER = process.env.ROADPLANNER_BROWSER || "/usr/bin/chromium";
const FFPROBE = process.env.ROADPLANNER_FFPROBE || "ffprobe";
// The encoder, not just the reader. It exists in the image on purpose:
// the renderer cuts video into clips, makes proxies and muxes a
// soundtrack into a finished film, and all three need ffmpeg rather
// than ffprobe. Named through the environment for the same reason
// ffprobe is - so a test can point it somewhere else.
export const FFMPEG = process.env.ROADPLANNER_FFMPEG || "ffmpeg";

export const COMPOSITION_ID = "roadplanner-remotion-test";
export const TRIP_DAY_COMPOSITION_ID = "roadplanner-trip-day";

// The trip-day video is as long as the day has photos, so its length is
// computed by the composition rather than fixed here. These are the bounds
// a sane result has to fall inside; a composition that produced anything
// outside them has a bug, and rendering it would only hide that.
export const TRIP_DAY_MIN_SECONDS = 6;
export const TRIP_DAY_MAX_SECONDS = 25;

export const TRIP_FILM_COMPOSITION_ID = "roadplanner-trip-film";
// A whole trip is minutes, not seconds. The bounds are wide because the
// length follows the number of days, and narrow enough that a runaway
// composition is still caught before it renders for an hour.
export const TRIP_FILM_MIN_SECONDS = 15;
export const TRIP_FILM_MAX_SECONDS = 900;

// A re-encode of a finished film, not a render. Fifteen minutes is many
// times what a twelve-minute film takes on the target box, and short
// enough that a wedged ffmpeg does not hold the only worker all evening.
export const REVIEW_COPY_TIMEOUT_MS = Number(
  process.env.ROADPLANNER_REVIEW_COPY_TIMEOUT_MS || 900_000,
);
/**
 * Hard limits.
 *
 * A renderer without them is a renderer that can fill a disk or occupy the
 * only worker forever. Every number below is generous for the five-second
 * test and small enough that a stuck run ends by itself.
 */
export const LIMITS = {
  // A five-second 720p render takes ~13 s on the target box and produces
  // ~260 kB. Both budgets are an order of magnitude above that, so they
  // only ever trigger on something genuinely wrong.
  renderTimeoutMs: Number(process.env.ROADPLANNER_RENDER_TIMEOUT_MS || 300_000),
  maxOutputBytes: Number(process.env.ROADPLANNER_MAX_OUTPUT_BYTES || 64 * 1024 * 1024),
  // Free space required before a render is even started - failing early
  // beats failing halfway through with a half-written file.
  minFreeBytes: Number(process.env.ROADPLANNER_MIN_FREE_BYTES || 512 * 1024 * 1024),
};
export const EXPECTED = {
  codec: "h264",
  container: "mov,mp4,m4a,3gp,3g2,mj2",
  width: 1280,
  height: 720,
  fps: 30,
  durationSeconds: 5,
  // A render that lands within a fifth of a second of five is the same
  // video; anything further out means the composition or the fps changed.
  durationToleranceSeconds: 0.2,
};

export class RenderError extends Error {
  constructor(code, message, detail) {
    super(message);
    this.code = code;
    this.detail = String(detail ?? "").slice(0, 600);
  }
}

/** Ask ffprobe what the file actually is. */
export async function probe(file) {
  let stdout;
  try {
    ({ stdout } = await execFileAsync(FFPROBE, [
      "-v",
      "error",
      "-print_format",
      "json",
      "-show_format",
      "-show_streams",
      file,
    ]));
  } catch (err) {
    throw new RenderError("OUTPUT_INVALID", "ffprobe konnte die Datei nicht lesen.", err?.message);
  }
  let parsed;
  try {
    parsed = JSON.parse(stdout);
  } catch (err) {
    throw new RenderError("OUTPUT_INVALID", "ffprobe lieferte kein gültiges JSON.", err?.message);
  }
  const video = (parsed.streams || []).find((s) => s.codec_type === "video");
  if (!video) {
    throw new RenderError("OUTPUT_INVALID", "Die Datei enthält keine Videospur.");
  }
  const [num, den] = String(video.r_frame_rate || "0/1").split("/");
  return {
    // Whether there IS a soundtrack, asked of the file rather than of
    // whoever passed it in. A review copy has to reproduce this exactly,
    // and a film with no music genuinely carries no audio stream.
    has_audio: (parsed.streams || []).some((s) => s.codec_type === "audio"),
    codec: String(video.codec_name || ""),
    container: String(parsed.format?.format_name || ""),
    width: Number(video.width) || 0,
    height: Number(video.height) || 0,
    fps: Number(den) ? Number(num) / Number(den) : 0,
    duration_seconds: Math.round(Number(parsed.format?.duration || 0) * 1000) / 1000,
    size_bytes: Number(parsed.format?.size) || 0,
  };
}

/**
 * Refuse anything that is not the video we asked for.
 *
 * The expectation is a parameter because the trip-day video's length
 * depends on how many photos the day had. Everything else — codec,
 * container, resolution, frame rate — is fixed for both compositions, and
 * a mismatch there means something changed that nobody meant to change.
 */
export function assertExpected(facts, expected = EXPECTED) {
  const problems = [];
  if (facts.codec !== expected.codec) problems.push(`Codec ${facts.codec}`);
  if (!facts.container.includes("mp4")) problems.push(`Container ${facts.container}`);
  if (facts.width !== expected.width || facts.height !== expected.height) {
    problems.push(`Auflösung ${facts.width}x${facts.height}`);
  }
  if (Math.abs(facts.fps - expected.fps) > 0.01) problems.push(`Bildrate ${facts.fps}`);
  if (
    Math.abs(facts.duration_seconds - expected.durationSeconds) >
    expected.durationToleranceSeconds
  ) {
    problems.push(`Dauer ${facts.duration_seconds} s`);
  }
  if (facts.size_bytes <= 0) problems.push("Dateigröße 0");
  if (problems.length) {
    throw new RenderError(
      "OUTPUT_INVALID",
      `Das Ergebnis entspricht nicht der Erwartung: ${problems.join(", ")}.`,
    );
  }
  return facts;
}

/**
 * Render to a temporary name and only then move it into place.
 *
 * The exchange folder is polled by Home Assistant; a partially written mp4
 * appearing under its final name would be read as a finished result.
 */
export async function renderTestVideo({ outputPath, title, onProgress }) {
  return renderComposition({
    outputPath,
    compositionId: COMPOSITION_ID,
    inputProps: { title },
    expected: EXPECTED,
    onProgress,
  });
}

/**
 * Render one real trip day from the package that came with the job.
 *
 * The reading rules are the point of this function, not the rendering:
 *
 * - **only files inside the job's own input directory are opened.** The
 *   directory is named after the job id, and the image names are built
 *   from integer indices, so no string out of the package ever reaches a
 *   path;
 * - **every image is checked against the hash the package declared**
 *   before it is used. The hash proves the bytes are the ones Roadplanner
 *   prepared — not that they came from Roadplanner, which no hash in the
 *   same directory could ever prove;
 * - **the photos are passed to the composition as data URIs.** The bundle
 *   is built with no public directory precisely so a composition cannot
 *   reach for assets of its own; handing the bytes in keeps that true.
 */
export async function renderTripDayVideo({ outputPath, inputsDir, onProgress }) {
  const started = Date.now();
  const packageRaw = await readBounded(
    path.join(inputsDir, PACKAGE_FILENAME),
    MAX_JSON_BYTES,
  );
  if (packageRaw === null) {
    throw new RenderError("PACKAGE_MISSING", "Zum Auftrag fehlt das Renderpaket.");
  }
  const parsed = parsePackage(packageRaw.toString("utf8"));

  const photos = [];
  for (const image of parsed.images) {
    const bytes = await readBounded(
      path.join(inputsDir, image.filename),
      MAX_PACKAGE_IMAGE_BYTES,
    );
    if (bytes === null) {
      throw new RenderError(
        "PACKAGE_MISSING",
        `Im Renderpaket fehlt ein angekündigtes Bild (${image.filename}).`,
      );
    }
    if (bytes.length !== image.sizeBytes) {
      throw new RenderError(
        "PACKAGE_INVALID",
        `${image.filename}: Größe weicht von der Ankündigung ab.`,
      );
    }
    if (createHash("sha256").update(bytes).digest("hex") !== image.sha256) {
      throw new RenderError("PACKAGE_INVALID", `${image.filename}: SHA-256 stimmt nicht.`);
    }
    photos.push(`data:image/jpeg;base64,${bytes.toString("base64")}`);
  }

  const packageSeconds = (Date.now() - started) / 1000;
  const result = await renderComposition({
    outputPath,
    compositionId: TRIP_DAY_COMPOSITION_ID,
    inputProps: {
      tripTitle: parsed.tripTitle,
      day: parsed.day,
      stops: parsed.stops,
      photos,
    },
    // The composition works out its own length from the number of photos,
    // so the expectation is read back from it rather than recomputed here.
    // Two places doing the same arithmetic is two places to get it wrong.
    expected: (composition) => {
      const seconds = composition.durationInFrames / EXPECTED.fps;
      if (seconds < TRIP_DAY_MIN_SECONDS || seconds > TRIP_DAY_MAX_SECONDS) {
        throw new RenderError(
          "OUTPUT_INVALID",
          `Die Komposition ergibt ${Math.round(seconds)} s - das liegt ausserhalb des erwarteten Rahmens.`,
        );
      }
      return { ...EXPECTED, durationSeconds: seconds, durationToleranceSeconds: 0.2 };
    },
    onProgress,
  });
  result.timings.package = packageSeconds;
  result.timings.total += packageSeconds;
  result.facts.photo_count = photos.length;
  result.facts.stop_count = parsed.stops.length;
  return result;
}

/**
 * Render a whole trip from its film package.
 *
 * The photos are served as files rather than embedded. Seventy pictures as
 * data URIs would be one serialised blob the browser has to hold whole; as
 * files the browser fetches each one when the frame needs it and lets it go
 * again. Remotion serves a local `serveUrl` directory over HTTP, so the
 * job gets its own copy of the bundle with a `photos/` folder beside it -
 * a copy rather than a shared folder, because two jobs sharing one
 * directory is a way for one trip's pictures to appear in another's film.
 */
export async function renderTripFilmVideo({
  outputPath,
  inputsDir,
  onProgress,
  profileId = DEFAULT_RENDER_PROFILE,
}) {
  const started = Date.now();
  // An unknown id falls back to the default rather than failing: a render
  // at the wrong size is recoverable, a job refused after the package was
  // already written is a lost trip's worth of preparation.
  const profile = renderProfile(profileId);
  const manifestRaw = await readBounded(
    path.join(inputsDir, FILM_MANIFEST_FILENAME),
    MAX_FILM_JSON_BYTES,
  );
  if (manifestRaw === null) {
    throw new RenderError("PACKAGE_MISSING", "Zum Auftrag fehlt das Filmpaket.");
  }
  const parsed = parseFilmPackage(manifestRaw.toString("utf8"));

  const stage = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-film-"));
  try {
    // The photos are verified and written FIRST, before the bundle is
    // copied. Copying a bundle for a package that turns out to be broken
    // is work nobody asked for, and it would also hide the real error
    // behind whatever the copy failed on.
    let photoBytes = 0;
    for (const chapter of parsed.chapters) {
      for (const photo of chapter.photos) {
        const bytes = await readBounded(
          path.join(inputsDir, photo.path),
          MAX_FILM_IMAGE_BYTES,
        );
        if (bytes === null) {
          throw new RenderError(
            "PACKAGE_MISSING",
            `Im Filmpaket fehlt ein angekündigtes Bild (${photo.path}).`,
          );
        }
        if (bytes.length !== photo.sizeBytes) {
          throw new RenderError(
            "PACKAGE_INVALID",
            `${photo.path}: Größe weicht von der Ankündigung ab.`,
          );
        }
        if (createHash("sha256").update(bytes).digest("hex") !== photo.sha256) {
          throw new RenderError("PACKAGE_INVALID", `${photo.path}: SHA-256 stimmt nicht.`);
        }
        const target = path.join(stage, photo.path);
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, bytes);
        photoBytes += bytes.length;
      }
    }
    // The crew portraits and the soundtrack travel with the job for the
    // same reason the photographs do: the renderer runs in another
    // container, and a link would either not resolve or - in the case of
    // the crew route - be a copy of a bearer secret sitting on disk.
    for (const member of parsed.crew?.members ?? []) {
      if (!member.path) continue;
      const bytes = await readBounded(path.join(inputsDir, member.path), MAX_FILM_IMAGE_BYTES);
      if (bytes === null) {
        throw new RenderError(
          "PACKAGE_MISSING",
          `Im Filmpaket fehlt ein angekündigtes Crewbild (${member.path}).`,
        );
      }
      if (createHash("sha256").update(bytes).digest("hex") !== member.sha256) {
        throw new RenderError("PACKAGE_INVALID", `${member.path}: SHA-256 stimmt nicht.`);
      }
      const target = path.join(stage, member.path);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, bytes);
    }
    for (const asset of parsed.characters?.assets ?? []) {
      const bytes = await readBounded(path.join(inputsDir, asset.path), MAX_FILM_IMAGE_BYTES);
      if (bytes === null) {
        throw new RenderError(
          "PACKAGE_MISSING",
          `Im Filmpaket fehlt ein angekündigtes Figurenbild (${asset.path}).`,
        );
      }
      if (createHash("sha256").update(bytes).digest("hex") !== asset.sha256) {
        throw new RenderError("PACKAGE_INVALID", `${asset.path}: SHA-256 stimmt nicht.`);
      }
      const target = path.join(stage, asset.path);
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, bytes);
    }
    for (const entries of Object.values(parsed.clips ?? {})) {
      for (const clip of entries) {
        const bytes = await readBounded(path.join(inputsDir, clip.path), MAX_MUSIC_BYTES);
        if (bytes === null) {
          throw new RenderError(
            "PACKAGE_MISSING",
            `Im Filmpaket fehlt ein angekündigter Clip (${clip.path}).`,
          );
        }
        if (createHash("sha256").update(bytes).digest("hex") !== clip.sha256) {
          throw new RenderError("PACKAGE_INVALID", `${clip.path}: SHA-256 stimmt nicht.`);
        }
        const target = path.join(stage, clip.path);
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, bytes);
      }
    }
    if (parsed.music) {
      // The single track and, when the score is generated, its sections.
      // Every one of them is announced with a hash, so every one of them
      // is checked - a section is not a lesser file.
      const announced = [
        { path: parsed.music.path, sha256: parsed.music.sha256 },
        ...(parsed.music.sections || []),
      ];
      const copied = new Set();
      for (const entry of announced) {
        if (copied.has(entry.path)) continue;
        copied.add(entry.path);
        const bytes = await readBounded(path.join(inputsDir, entry.path), MAX_MUSIC_BYTES);
        if (bytes === null) {
          throw new RenderError("PACKAGE_MISSING", "Im Filmpaket fehlt die angekündigte Musik.");
        }
        if (createHash("sha256").update(bytes).digest("hex") !== entry.sha256) {
          throw new RenderError("PACKAGE_INVALID", "Musikdatei: SHA-256 stimmt nicht.");
        }
        const target = path.join(stage, entry.path);
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, bytes);
      }
    }
    await fs.cp(BUNDLE_DIR, stage, { recursive: true });
    const prepareSeconds = (Date.now() - started) / 1000;

    const result = await renderComposition({
      outputPath,
      compositionId: TRIP_FILM_COMPOSITION_ID,
      serveUrl: stage,
      // The scene plan travels with the package and decides the length,
      // so the composition receives it rather than deriving one.
      inputProps: {
        trip: parsed.trip,
        chapters: parsed.chapters,
        narrative: parsed.narrative,
        scenes: parsed.scenes,
        mapContext: parsed.mapContext,
        crew: parsed.crew,
        characters: parsed.characters,
        clips: parsed.clips,
        music: parsed.music,
        // The only prop that is not content. The composition passes it to
        // calculateMetadata and nothing else reads it, which is what keeps
        // the film identical across profiles.
        renderProfile: profile.id,
      },
      limits: FILM_LIMITS,
      quality: profile,
      expected: (composition) => {
        const seconds = composition.durationInFrames / profile.fps;
        if (seconds < TRIP_FILM_MIN_SECONDS || seconds > TRIP_FILM_MAX_SECONDS) {
          throw new RenderError(
            "OUTPUT_INVALID",
            `Der Film ergibt ${Math.round(seconds)} s - das liegt ausserhalb des erwarteten Rahmens.`,
          );
        }
        return {
          ...EXPECTED,
          width: profile.width,
          height: profile.height,
          fps: profile.fps,
          durationSeconds: seconds,
          durationToleranceSeconds: 0.25,
        };
      },
      onProgress,
    });
    result.timings.prepare = prepareSeconds;
    result.timings.total += prepareSeconds;
    // Which size this is, said by the side that rendered it. A film whose
    // own result cannot answer "which profile was that?" is a file nobody
    // can place two weeks later.
    result.facts.render_profile = profile.id;
    result.facts.chapter_count = parsed.chapters.length;
    result.facts.photo_count = parsed.chapters.reduce(
      (sum, chapter) => sum + chapter.photos.length,
      0,
    );
    result.facts.chapters_without_photos = parsed.chapters.filter(
      (chapter) => !chapter.photos.length,
    ).length;
    result.facts.package_bytes = photoBytes;
    result.facts.mapped_chapters = parsed.mapContext?.chapters.length ?? 0;
    result.facts.crew_count = parsed.crew?.members.length ?? 0;
    result.facts.has_music = Boolean(parsed.music);
    result.facts.character_assets = parsed.characters?.assets.length ?? 0;
    result.facts.clip_count = Object.values(parsed.clips ?? {}).reduce(
      (sum, entries) => sum + entries.length,
      0,
    );
    return result;
  } finally {
    // The stage holds a copy of somebody's photos. It goes whether the
    // render succeeded or not.
    await fs.rm(stage, { recursive: true, force: true }).catch(() => {});
  }
}

/**
 * Make a small copy of a film that has already been rendered.
 *
 * The one job in this file that never starts a browser. It reads a
 * finished MP4 and writes a smaller one - which is why it can run in
 * minutes where the render it copies took an hour, and why it costs
 * nothing beyond CPU: no package is parsed, no photograph is opened, no
 * service is called.
 *
 * `sourcePath` is resolved by the caller from a job id, never from
 * anything in the job's own text. This function opens exactly what it is
 * handed and writes exactly where it is told.
 */
export async function createReviewCopy({
  sourcePath,
  outputPath,
  profileId = DEFAULT_REVIEW_PROFILE,
  // Null means "whatever this profile aims at". Only a caller with a
  // reason of its own passes a number here.
  targetBytes = null,
  onProgress,
}) {
  const startedAt = Date.now();
  const timings = {};
  const profile = reviewProfile(profileId);

  let stat;
  try {
    stat = await fs.lstat(sourcePath);
  } catch {
    throw new RenderError("PACKAGE_MISSING", "Zu diesem Auftrag gibt es keinen Film.");
  }
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new RenderError("PACKAGE_INVALID", "Die Quelle ist keine gewöhnliche Datei.");
  }

  const probeStarted = Date.now();
  const source = await probe(sourcePath);
  timings.probe_source = (Date.now() - probeStarted) / 1000;
  if (source.duration_seconds <= 0) {
    throw new RenderError("PACKAGE_INVALID", "Die Quelldatei hat keine lesbare Länge.");
  }

  const bitrate = reviewBitrate({
    durationSeconds: source.duration_seconds,
    targetBytes,
    hasAudio: source.has_audio,
    profile,
  });

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const partial = `${outputPath.replace(/\.mp4$/i, "")}.part.mp4`;
  const args = [
    "-progress",
    "pipe:1",
    "-nostats",
    ...reviewCopyArgs({
      source: sourcePath,
      output: partial,
      profile,
      bitrateBps: bitrate,
      hasAudio: source.has_audio,
    }),
  ];

  const encodeStarted = Date.now();
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(FFMPEG, args, { stdio: ["ignore", "pipe", "pipe"] });
      let stderr = "";
      let rest = "";
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        reject(
          new RenderError(
            "RENDER_TIMEOUT",
            `Die Review-Kopie hat die Zeitgrenze von ${Math.round(
              REVIEW_COPY_TIMEOUT_MS / 1000,
            )} s überschritten.`,
          ),
        );
      }, REVIEW_COPY_TIMEOUT_MS);
      child.stdout.on("data", (chunk) => {
        // ffmpeg's progress stream is key=value lines. Only one of them
        // is interesting, and it is in microseconds.
        rest += chunk.toString();
        const lines = rest.split("\n");
        rest = lines.pop() ?? "";
        for (const line of lines) {
          const [key, value] = line.split("=");
          if (key !== "out_time_us") continue;
          const done = Number(value) / 1_000_000 / source.duration_seconds;
          if (Number.isFinite(done)) onProgress?.(Math.max(0, Math.min(1, done)));
        }
      });
      child.stderr.on("data", (chunk) => {
        stderr = `${stderr}${chunk}`.slice(-600);
      });
      child.on("error", (err) => {
        clearTimeout(timer);
        reject(new RenderError("RENDER_FAILED", "ffmpeg konnte nicht gestartet werden.", err?.message));
      });
      child.on("close", (code) => {
        clearTimeout(timer);
        if (code === 0) resolve();
        else reject(new RenderError("RENDER_FAILED", "Die Review-Kopie ist fehlgeschlagen.", stderr));
      });
    });
  } catch (err) {
    // The half-written file goes with the failure. Nothing else in this
    // function created a temporary file, so this is the whole cleanup.
    await fs.rm(partial, { force: true }).catch(() => {});
    throw err instanceof RenderError
      ? err
      : new RenderError("RENDER_FAILED", "Die Review-Kopie ist fehlgeschlagen.", String(err));
  }
  timings.encode = (Date.now() - encodeStarted) / 1000;

  let facts;
  try {
    facts = await probe(partial);
  } catch (err) {
    await fs.rm(partial, { force: true }).catch(() => {});
    throw err;
  }
  // What must survive a copy, checked rather than assumed. The length is
  // the one that matters: a copy that is shorter than the film is not a
  // smaller version of it, it is a different film.
  const problems = [];
  if (facts.codec !== "h264") problems.push(`Codec ${facts.codec}`);
  if (Math.abs(facts.duration_seconds - source.duration_seconds) > 0.5) {
    problems.push(`Dauer ${facts.duration_seconds} s statt ${source.duration_seconds} s`);
  }
  if (facts.width > source.width || facts.height > source.height) {
    problems.push(`Vergrößerung auf ${facts.width}x${facts.height}`);
  }
  if (source.has_audio !== facts.has_audio) problems.push("Tonspur stimmt nicht");
  if (facts.size_bytes <= 0) problems.push("Dateigröße 0");
  if (problems.length) {
    await fs.rm(partial, { force: true }).catch(() => {});
    throw new RenderError(
      "OUTPUT_INVALID",
      `Die Review-Kopie entspricht nicht der Erwartung: ${problems.join(", ")}.`,
    );
  }

  await fs.rename(partial, outputPath);
  timings.total = (Date.now() - startedAt) / 1000;
  return {
    facts: {
      ...facts,
      render_profile: profile.id,
      video_bitrate_bps: bitrate,
      source_size_bytes: source.size_bytes,
      source_width: source.width,
      source_height: source.height,
    },
    timings,
  };
}

/** Read a file, refusing an oversized one and never following a symlink. */
async function readBounded(file, limit) {
  let stat;
  try {
    stat = await fs.lstat(file);
  } catch {
    return null;
  }
  if (stat.isSymbolicLink()) {
    throw new RenderError("PACKAGE_INVALID", `Symlink im Renderpaket: ${path.basename(file)}`);
  }
  if (!stat.isFile()) return null;
  if (stat.size > limit) {
    throw new RenderError(
      "PACKAGE_INVALID",
      `${path.basename(file)} überschreitet die Größengrenze.`,
    );
  }
  return fs.readFile(file);
}

async function renderComposition({
  outputPath,
  compositionId,
  inputProps,
  expected,
  onProgress,
  serveUrl = BUNDLE_DIR,
  limits = LIMITS,
  quality = null,
}) {
  const timings = {};
  const startedAt = Date.now();
  // Resolved after the composition is selected when it depends on it.
  let finalExpected = typeof expected === "function" ? null : expected;
  await fs.mkdir(path.dirname(outputPath), { recursive: true });

  // Checked before the browser starts: a render that cannot possibly land
  // should not cost thirteen seconds first.
  try {
    const fsStat = statfsSync(path.dirname(outputPath));
    const free = fsStat.bavail * fsStat.bsize;
    if (free < LIMITS.minFreeBytes) {
      throw new RenderError(
        "INSUFFICIENT_DISK_SPACE",
        `Zu wenig freier Speicher: ${Math.round(free / 1024 / 1024)} MB.`,
      );
    }
  } catch (err) {
    if (err instanceof RenderError) throw err;
    // statfs is not worth failing a render over; the size check below still
    // catches a full disk.
  }

  try {
    await fs.access(serveUrl);
  } catch (err) {
    throw new RenderError("RENDER_FAILED", "Das Remotion-Bundle fehlt im Image.", err?.message);
  }

  const browserStarted = Date.now();
  let browser;
  try {
    browser = await openBrowser("chrome", {
      browserExecutable: BROWSER,
      chromiumOptions: { gl: "swangle", headless: true },
    });
  } catch (err) {
    throw new RenderError(
      "BROWSER_MISSING",
      "Der Browser konnte nicht gestartet werden.",
      err?.message,
    );
  }
  timings.browser_start = (Date.now() - browserStarted) / 1000;

  const partial = `${outputPath.replace(/\.mp4$/i, "")}.part.mp4`;
  try {
    const composition = await selectComposition({
      serveUrl,
      id: compositionId,
      inputProps,
      puppeteerInstance: browser,
    });
    if (finalExpected === null) finalExpected = expected(composition);

    const renderStarted = Date.now();
    // Two guards, because "slow" and "stuck" are different failures and a
    // single wall clock cannot tell them apart. The ceiling scales with
    // the frames actually being drawn; the watchdog fires when nothing has
    // moved at all, which is what a wedged browser looks like.
    const ceilingMs = renderCeilingMs(
      limits,
      composition.durationInFrames,
      quality ? pixelFactor(quality) : 1,
    );
    const stallMs = Number(limits.stallTimeoutMs) || 0;
    let timer;
    let watchdog;
    let settled = false;
    // Re-armed on every progress report; a no-op until the watchdog exists,
    // so a run without one costs nothing.
    let rearmWatchdog = () => {};
    const deadline = new Promise((_resolve, reject) => {
      const fail = (error) => {
        if (settled) return;
        settled = true;
        reject(error);
      };
      timer = setTimeout(
        () =>
          fail(
            new RenderError(
              "RENDER_TIMEOUT",
              `Der Render hat die Zeitgrenze von ${Math.round(ceilingMs / 1000)} s ` +
                `für ${composition.durationInFrames} Bilder überschritten.`,
            ),
          ),
        ceilingMs,
      );
      if (stallMs > 0) {
        const arm = () => {
          clearTimeout(watchdog);
          watchdog = setTimeout(
            () =>
              fail(
                new RenderError(
                  "RENDER_STALLED",
                  `Der Render hat ${Math.round(stallMs / 1000)} s lang keinen ` +
                    "Fortschritt gemeldet.",
                ),
              ),
            stallMs,
          );
        };
        arm();
        rearmWatchdog = arm;
      }
    });
    const render = renderMedia({
      composition,
      serveUrl,
      codec: "h264",
      outputLocation: partial,
      inputProps,
      puppeteerInstance: browser,
      // A review copy exists to be small and looked at once; a film to
      // keep gets the quality it deserves. Both come from the profile
      // table rather than from a number typed here, so "smaller" is one
      // decision instead of two that can drift apart.
      x264Preset: quality?.x264Preset ?? "veryfast",
      crf: Number.isFinite(quality?.crf) ? quality.crf : 20,
      // One browser tab: a Home Assistant box shares its cores with
      // everything else the user runs on it.
      concurrency: 1,
      // A film with no music must contain no audio track at all, not a
      // silent one. A silent AAC stream costs bytes, makes some players
      // show an audio control that does nothing, and makes "did the
      // music arrive?" unanswerable from the file.
      enforceAudioTrack: false,
      onProgress: ({ progress }) => {
        rearmWatchdog();
        onProgress?.(progress);
      },
    });
    try {
      await Promise.race([render, deadline]);
    } finally {
      settled = true;
      clearTimeout(timer);
      clearTimeout(watchdog);
    }
    timings.render = (Date.now() - renderStarted) / 1000;

    // A file larger than the channel will carry is a failure here, not a
    // surprise on the Home Assistant side.
    const written = await fs.stat(partial);
    if (written.size > limits.maxOutputBytes) {
      throw new RenderError(
        "OUTPUT_INVALID",
        `Das Ergebnis ist mit ${Math.round(written.size / 1024 / 1024)} MB zu gross.`,
      );
    }
  } catch (err) {
    await fs.rm(partial, { force: true }).catch(() => {});
    throw new RenderError("RENDER_FAILED", "Der Render ist fehlgeschlagen.", err?.message);
  } finally {
    await browser.close().catch(() => {});
  }

  const probeStarted = Date.now();
  const facts = assertExpected(await probe(partial), finalExpected);
  timings.probe = (Date.now() - probeStarted) / 1000;

  await fs.rename(partial, outputPath);
  timings.total = (Date.now() - startedAt) / 1000;
  return { facts, timings };
}
