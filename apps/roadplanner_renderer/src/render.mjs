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
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { statfsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import { openBrowser, renderMedia, selectComposition } from "@remotion/renderer";

import {
  MAX_JSON_BYTES,
  MAX_PACKAGE_IMAGE_BYTES,
  PACKAGE_FILENAME,
  parsePackage,
} from "./protocol.mjs";

const execFileAsync = promisify(execFile);

const BUNDLE_DIR = process.env.ROADPLANNER_BUNDLE_DIR || "/opt/roadplanner-renderer/bundle";
const BROWSER = process.env.ROADPLANNER_BROWSER || "/usr/bin/chromium";
const FFPROBE = process.env.ROADPLANNER_FFPROBE || "ffprobe";

export const COMPOSITION_ID = "roadplanner-remotion-test";
export const TRIP_DAY_COMPOSITION_ID = "roadplanner-trip-day";

// The trip-day video is as long as the day has photos, so its length is
// computed by the composition rather than fixed here. These are the bounds
// a sane result has to fall inside; a composition that produced anything
// outside them has a bug, and rendering it would only hide that.
export const TRIP_DAY_MIN_SECONDS = 6;
export const TRIP_DAY_MAX_SECONDS = 25;

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
    await fs.access(BUNDLE_DIR);
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
      serveUrl: BUNDLE_DIR,
      id: compositionId,
      inputProps,
      puppeteerInstance: browser,
    });
    if (finalExpected === null) finalExpected = expected(composition);

    const renderStarted = Date.now();
    // A wall-clock ceiling on the render itself. Without it a wedged
    // browser holds the single worker for as long as the container lives.
    let timer;
    const deadline = new Promise((_resolve, reject) => {
      timer = setTimeout(
        () =>
          reject(
            new RenderError(
              "RENDER_TIMEOUT",
              `Der Render hat die Zeitgrenze von ${Math.round(LIMITS.renderTimeoutMs / 1000)} s überschritten.`,
            ),
          ),
        LIMITS.renderTimeoutMs,
      );
    });
    const render = renderMedia({
      composition,
      serveUrl: BUNDLE_DIR,
      codec: "h264",
      outputLocation: partial,
      inputProps,
      puppeteerInstance: browser,
      x264Preset: "veryfast",
      crf: 20,
      // One browser tab: a Home Assistant box shares its cores with
      // everything else the user runs on it.
      concurrency: 1,
      onProgress: ({ progress }) => onProgress?.(progress),
    });
    try {
      await Promise.race([render, deadline]);
    } finally {
      clearTimeout(timer);
    }
    timings.render = (Date.now() - renderStarted) / 1000;

    // A file larger than the channel will carry is a failure here, not a
    // surprise on the Home Assistant side.
    const written = await fs.stat(partial);
    if (written.size > LIMITS.maxOutputBytes) {
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
