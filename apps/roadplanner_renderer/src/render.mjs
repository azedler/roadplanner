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
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import { openBrowser, renderMedia, selectComposition } from "@remotion/renderer";

const execFileAsync = promisify(execFile);

const BUNDLE_DIR = process.env.ROADPLANNER_BUNDLE_DIR || "/opt/roadplanner-renderer/bundle";
const BROWSER = process.env.ROADPLANNER_BROWSER || "/usr/bin/chromium";
const FFPROBE = process.env.ROADPLANNER_FFPROBE || "ffprobe";

export const COMPOSITION_ID = "roadplanner-remotion-test";
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

/** Refuse anything that is not the video we asked for. */
export function assertExpected(facts) {
  const problems = [];
  if (facts.codec !== EXPECTED.codec) problems.push(`Codec ${facts.codec}`);
  if (!facts.container.includes("mp4")) problems.push(`Container ${facts.container}`);
  if (facts.width !== EXPECTED.width || facts.height !== EXPECTED.height) {
    problems.push(`Auflösung ${facts.width}x${facts.height}`);
  }
  if (Math.abs(facts.fps - EXPECTED.fps) > 0.01) problems.push(`Bildrate ${facts.fps}`);
  if (
    Math.abs(facts.duration_seconds - EXPECTED.durationSeconds) >
    EXPECTED.durationToleranceSeconds
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
  const timings = {};
  const startedAt = Date.now();
  await fs.mkdir(path.dirname(outputPath), { recursive: true });

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
      id: COMPOSITION_ID,
      inputProps: { title },
      puppeteerInstance: browser,
    });

    const renderStarted = Date.now();
    await renderMedia({
      composition,
      serveUrl: BUNDLE_DIR,
      codec: "h264",
      outputLocation: partial,
      inputProps: { title },
      puppeteerInstance: browser,
      x264Preset: "veryfast",
      crf: 20,
      // One browser tab: a Home Assistant box shares its cores with
      // everything else the user runs on it.
      concurrency: 1,
      onProgress: ({ progress }) => onProgress?.(progress),
    });
    timings.render = (Date.now() - renderStarted) / 1000;
  } catch (err) {
    await fs.rm(partial, { force: true }).catch(() => {});
    throw new RenderError("RENDER_FAILED", "Der Render ist fehlgeschlagen.", err?.message);
  } finally {
    await browser.close().catch(() => {});
  }

  const probeStarted = Date.now();
  const facts = assertExpected(await probe(partial));
  timings.probe = (Date.now() - probeStarted) / 1000;

  await fs.rename(partial, outputPath);
  timings.total = (Date.now() - startedAt) / 1000;
  return { facts, timings };
}
