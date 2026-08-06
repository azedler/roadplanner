/**
 * Render one validated job and report progress as JSONL on stdout.
 *
 * The contract with Python (see custom_components/roadplanner_mcp/
 * remotion_job.py) is deliberately narrow:
 *
 *   argv[2] = path to a JSON job file
 *   stdout  = one JSON object per line, nothing else
 *   stderr  = human/technical noise, bounded and stored separately
 *   exit 0  = a complete, valid video was written
 *
 * stdout carries protocol ONLY. Remotion and its browser are chatty, and a
 * stray log line in the middle of the stream would corrupt the protocol -
 * so every diagnostic goes to stderr and the exit code, not the channel
 * Python parses.
 *
 * Nothing here downloads anything. `ensureBrowser` is never called; a
 * missing browser is reported as BROWSER_MISSING and the run stops. That
 * restraint is the point of the spike: the question is whether this
 * environment can already run the renderer, not whether we can make it.
 */
import { readFileSync, mkdirSync, existsSync, rmSync, renameSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const SCHEMA_VERSION = 1;
const ALLOWED_COMPOSITIONS = new Set(["roadplanner-test"]);
const ALLOWED_CODECS = new Set(["h264"]);

/** One protocol line. Never anything else on stdout. */
const emit = (payload) => {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
};

const fail = (code, message, detail) => {
  emit({
    event: "failed",
    code,
    message,
    technical_detail: String(detail ?? "").slice(0, 600),
  });
  process.exit(1);
};

const readJob = (jobPath) => {
  let raw;
  try {
    raw = readFileSync(jobPath, "utf-8");
  } catch (err) {
    fail("RENDER_FAILED", "Der Job konnte nicht gelesen werden.", err?.message);
  }
  let job;
  try {
    job = JSON.parse(raw);
  } catch (err) {
    fail("RENDER_FAILED", "Der Job ist kein gültiges JSON.", err?.message);
  }
  if (job?.schema_version !== SCHEMA_VERSION) {
    fail("RENDER_FAILED", "Nicht unterstützte Job-Schemaversion.", String(job?.schema_version));
  }
  if (!ALLOWED_COMPOSITIONS.has(job?.composition)) {
    fail("RENDER_FAILED", "Unbekannte Komposition.", String(job?.composition));
  }
  if (!ALLOWED_CODECS.has(job?.render?.codec)) {
    fail("RENDER_FAILED", "Nicht unterstützter Codec.", String(job?.render?.codec));
  }
  if (job?.runtime?.allow_downloads) {
    // Belt and braces: Python pins this to false, and the renderer
    // refuses to run if it ever arrives true.
    fail("RENDER_FAILED", "Downloads sind in diesem Modus nicht erlaubt.", "allow_downloads");
  }
  if (typeof job?.output_path !== "string" || !job.output_path) {
    fail("RENDER_FAILED", "Kein Ausgabepfad im Job.", "output_path");
  }
  return job;
};

const main = async () => {
  const jobPath = process.argv[2];
  if (!jobPath) {
    fail("RENDER_FAILED", "Kein Jobpfad übergeben.", "argv[2]");
  }
  const job = readJob(jobPath);
  emit({ event: "started", job_id: job.job_id });

  const { bundle } = await import("@remotion/bundler");
  const { selectComposition, renderMedia } = await import("@remotion/renderer");

  const entry = resolve(new URL("../src/index.ts", import.meta.url).pathname);
  const outputPath = resolve(job.output_path);
  // Write to a neighbouring temporary file and only move it into place
  // once it is complete - a half-written mp4 must never look like a
  // result. The suffix stays .mp4 because Remotion validates the output
  // extension against the codec before it starts.
  const tempPath = `${outputPath.replace(/\.mp4$/i, "")}.part.mp4`;
  mkdirSync(dirname(outputPath), { recursive: true });
  if (existsSync(tempPath)) rmSync(tempPath, { force: true });

  emit({ event: "phase", name: "bundle", progress: 0.05 });
  let serveUrl;
  try {
    serveUrl = await bundle({
      entryPoint: entry,
      onProgress: (percent) => {
        emit({
          event: "phase",
          name: "bundle",
          progress: 0.05 + (Math.max(0, Math.min(100, percent)) / 100) * 0.2,
        });
      },
    });
  } catch (err) {
    fail("RENDER_FAILED", "Das Remotion-Bundle konnte nicht gebaut werden.", err?.message);
  }

  const browserExecutable = job?.runtime?.browser_executable || null;

  emit({ event: "phase", name: "compose", progress: 0.28 });
  let composition;
  try {
    composition = await selectComposition({
      serveUrl,
      id: job.composition,
      inputProps: job.input,
      browserExecutable,
      chromiumOptions: { headless: true },
    });
  } catch (err) {
    const detail = String(err?.message ?? "");
    // A browser that is absent and one that cannot start are different
    // findings for the report, so they get different codes.
    const missing = /browser|chrome|chromium|executable/i.test(detail);
    const libraries = /shared librar|\.so|GLIBC|libnss|libatk/i.test(detail);
    fail(
      libraries ? "BROWSER_LIBRARIES_MISSING" : missing ? "BROWSER_MISSING" : "RENDER_FAILED",
      libraries
        ? "Der Browser konnte wegen fehlender Systembibliotheken nicht starten."
        : missing
          ? "Es wurde kein startbarer Browser gefunden."
          : "Die Komposition konnte nicht geladen werden.",
      detail,
    );
  }

  const fps = job.render.fps;
  const durationInFrames = Math.max(1, Math.round(job.input.duration_seconds * fps));

  emit({ event: "phase", name: "render", progress: 0.3 });
  try {
    await renderMedia({
      composition: {
        ...composition,
        width: job.render.width,
        height: job.render.height,
        fps,
        durationInFrames,
      },
      serveUrl,
      codec: job.render.codec,
      crf: job.render.crf,
      x264Preset: job.render.x264_preset,
      outputLocation: tempPath,
      inputProps: job.input,
      browserExecutable,
      chromiumOptions: { headless: true },
      onProgress: ({ progress, renderedFrames }) => {
        emit({
          event: "progress",
          progress: 0.3 + Math.max(0, Math.min(1, progress)) * 0.7,
          rendered_frames: renderedFrames ?? 0,
          total_frames: durationInFrames,
        });
      },
    });
  } catch (err) {
    if (existsSync(tempPath)) rmSync(tempPath, { force: true });
    fail("RENDER_FAILED", "Der Render ist fehlgeschlagen.", err?.message);
  }

  let size = 0;
  try {
    size = statSync(tempPath).size;
  } catch (err) {
    fail("OUTPUT_INVALID", "Es wurde keine Ausgabedatei geschrieben.", err?.message);
  }
  if (size < 1024) {
    rmSync(tempPath, { force: true });
    fail("OUTPUT_INVALID", "Die Ausgabedatei ist unplausibel klein.", String(size));
  }
  renameSync(tempPath, outputPath);
  emit({ event: "completed", output_path: outputPath });
};

main().catch((err) => {
  fail("RENDER_FAILED", "Unerwarteter Fehler im Renderer.", err?.stack ?? err?.message);
});

// Keeps the module a module even if a bundler inspects it.
export const RENDERER_ENTRY = pathToFileURL(import.meta.url).href;
