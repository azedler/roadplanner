/**
 * The renderer app's worker loop.
 *
 * It watches one directory, claims jobs, writes a status and produces the
 * artefacts a job asks for: two small files for the plain test, or a
 * five-second H.264 video rendered with Remotion.
 *
 * The route was proven first, deliberately, with no renderer involved at
 * all — so that when Remotion was added, a failure could only mean "the
 * render is broken" and never "the app cannot be deployed". Both jobs
 * still exist for exactly that reason: `create_test_artifact` keeps
 * answering the deployment question without touching a browser.
 *
 * Three properties carry the design:
 *
 * - **Every file is written atomically**: temp name in the same directory,
 *   then rename. Roadplanner polls this directory and must never read half
 *   a file.
 * - **A job is claimed by renaming it** out of `jobs/` into `processing/`.
 *   Rename fails for whoever loses the race, so a job cannot be picked up
 *   twice — including by a second copy of this app started by mistake.
 * - **Terminal is terminal.** Once a job is completed/failed/expired, this
 *   process never writes a running status over it again.
 *
 * Polling rather than fs.watch: watching a directory across a container
 * boundary is exactly where inotify is least reliable (different
 * filesystems, bind mounts), and a 1 s poll on a directory with a handful
 * of entries costs nothing measurable.
 */
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  ARTIFACT_IMAGE,
  ARTIFACT_TEXT,
  ARTIFACT_TRIP_DAY_VIDEO,
  ARTIFACT_VIDEO,
  ERROR_INTERNAL,
  ProtocolError,
  TERMINAL_JOB_STATES,
  buildHeartbeat,
  buildResult,
  buildStatus,
  buildSvg,
  buildText,
  formatTime,
  isJobId,
  parseJob,
  sha256,
} from "./protocol.mjs";

const APP_VERSION = process.env.ROADPLANNER_APP_VERSION || "0.0.0-dev";
const EXCHANGE_DIR =
  process.env.ROADPLANNER_EXCHANGE_DIR || "/share/roadplanner-renderer/poc-v1";
const POLL_INTERVAL_MS = Number(process.env.ROADPLANNER_POLL_MS || 1000);
const HEARTBEAT_INTERVAL_MS = 5000;
// Anything older than this in results/ is from a previous run nobody came
// back for. Bounded cleanup keeps a shared folder from growing forever.
const RESULT_RETENTION_MS = 24 * 60 * 60 * 1000;
// Input packages are kept far shorter than results. A result is a video
// the user asked for; an input package is somebody's photos, including of
// their home, sitting in a directory every app can read. An hour is long
// enough to cover an app that was briefly stopped, and short enough that
// an abandoned package does not become a permanent copy.
const INPUT_RETENTION_MS = 60 * 60 * 1000;
const MAX_JOB_FILE_BYTES = 64 * 1024;

/**
 * Bounds on what one worker will take on.
 *
 * ONE job at a time, deliberately. A Home Assistant box shares its cores
 * with everything else the household runs on it, and two concurrent
 * renders would make both slower and the machine less responsive without
 * finishing any sooner. The guard is explicit rather than implied by the
 * sequential loop, so it survives someone parallelising that loop later.
 */
const MAX_CONCURRENT_JOBS = 1;
// A whole job, not just its render: claim, parse, render, probe, hash.
const MAX_JOB_DURATION_MS = Number(process.env.ROADPLANNER_MAX_JOB_MS || 420_000);
// Anything left in results/ beyond this is from a run nobody came back
// for; the shared folder must not grow without bound.
const MAX_RESULT_BYTES = Number(
  process.env.ROADPLANNER_MAX_RESULT_BYTES || 512 * 1024 * 1024,
);

let activeJobs = 0;

const DIRS = {
  jobs: path.join(EXCHANGE_DIR, "jobs"),
  processing: path.join(EXCHANGE_DIR, "processing"),
  status: path.join(EXCHANGE_DIR, "status"),
  results: path.join(EXCHANGE_DIR, "results"),
  // Where a job's input package waits. Written by Roadplanner before the
  // job file exists, and removed by this app as soon as the job ends -
  // real travel data must not outlive the job it was prepared for.
  inputs: path.join(EXCHANGE_DIR, "inputs"),
};
const HEARTBEAT_FILE = path.join(EXCHANGE_DIR, "renderer-status.json");

const startedAt = new Date();
let running = true;
let currentState = "starting";
/** Job ids this process has already finished — terminal stays terminal. */
const finished = new Set();

const log = (level, message, extra = {}) => {
  process.stdout.write(
    `${JSON.stringify({ ts: formatTime(new Date()), level, message, ...extra })}\n`,
  );
};

/** Write via a temp file in the SAME directory, so the rename is atomic. */
async function writeAtomic(target, contents) {
  const temporary = `${target}.${process.pid}.part`;
  await fs.writeFile(temporary, contents, "utf8");
  await fs.rename(temporary, target);
}

async function writeHeartbeat() {
  try {
    await writeAtomic(
      HEARTBEAT_FILE,
      `${JSON.stringify(
        buildHeartbeat({
          state: currentState,
          startedAt,
          now: new Date(),
          appVersion: APP_VERSION,
        }),
        null,
        2,
      )}\n`,
    );
  } catch (err) {
    log("error", "heartbeat konnte nicht geschrieben werden", { detail: String(err) });
  }
}

async function writeStatus(jobId, state, extra = {}) {
  if (finished.has(jobId) && !TERMINAL_JOB_STATES.has(state)) {
    // Refusing this is the whole guarantee: a late write must not
    // resurrect a job Roadplanner already saw finish.
    log("warn", "status nach terminalem Zustand verworfen", { job_id: jobId, state });
    return;
  }
  if (TERMINAL_JOB_STATES.has(state)) finished.add(jobId);
  await writeAtomic(
    path.join(DIRS.status, `${jobId}.json`),
    `${JSON.stringify(
      buildStatus({ jobId, state, updatedAt: new Date(), ...extra }),
      null,
      2,
    )}\n`,
  );
}

/**
 * Read a job file, refusing anything oversized before it is parsed.
 *
 * `lstat`, not `stat`: `stat` follows the link and would report the target,
 * which is exactly what a symlink check must not do. The exchange folder is
 * writable by another container, so a link out of it is the one way a file
 * there could reach something it should not.
 */
async function readJobFile(file) {
  const stat = await fs.lstat(file);
  if (stat.isSymbolicLink()) {
    throw new ProtocolError("INVALID_JOB", "Symlink wird nicht verarbeitet.");
  }
  if (stat.size > MAX_JOB_FILE_BYTES) {
    throw new ProtocolError("INVALID_JOB", "Auftragsdatei überschreitet die Größengrenze.");
  }
  return fs.readFile(file, "utf8");
}

async function produceArtifacts(jobId, message) {
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const timestamp = formatTime(new Date());
  const files = [
    { filename: ARTIFACT_TEXT, kind: "text", body: buildText({ jobId, message, timestamp }) },
    { filename: ARTIFACT_IMAGE, kind: "image", body: buildSvg({ jobId, message, timestamp }) },
  ];
  const artifacts = [];
  for (const file of files) {
    await writeAtomic(path.join(folder, file.filename), file.body);
    artifacts.push({
      kind: file.kind,
      filename: file.filename,
      size_bytes: Buffer.byteLength(file.body, "utf8"),
      sha256: sha256(Buffer.from(file.body, "utf8")),
    });
  }
  // result.json is written LAST and atomically: its presence is the signal
  // that every artefact beside it is complete.
  await writeAtomic(
    path.join(folder, "result.json"),
    `${JSON.stringify(buildResult({ jobId, completedAt: new Date(), artifacts }), null, 2)}\n`,
  );
  return artifacts;
}

/**
 * Render the Remotion test video and describe what was produced.
 *
 * The mp4 is hashed like every other artefact, but it is never read back
 * into memory beyond that: it is megabytes of binary that nothing on the
 * Home Assistant side displays.
 */
async function produceRemotionVideo(jobId, message, onProgress) {
  // Loaded only when a render is actually asked for. The plain test job
  // must keep working - and keep answering the deployment question - even
  // where the renderer cannot load at all.
  const { renderTestVideo } = await import("./render.mjs");
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, ARTIFACT_VIDEO);

  const { facts, timings } = await renderTestVideo({
    outputPath: target,
    title: message,
    onProgress,
  });

  const bytes = await fs.readFile(target);
  const artifacts = [
    {
      kind: "video",
      filename: ARTIFACT_VIDEO,
      size_bytes: bytes.length,
      sha256: sha256(bytes),
    },
  ];
  await writeAtomic(
    path.join(folder, "result.json"),
    `${JSON.stringify(
      buildResult({ jobId, completedAt: new Date(), artifacts, video: facts, timings }),
      null,
      2,
    )}\n`,
  );
  return { facts, timings };
}


/**
 * Render one real trip day from the package that came with the job.
 *
 * Same shape as the test render: produce, hash, describe. The difference
 * is that the input is real travel data sitting in a shared directory,
 * which is why the render module verifies every image against the hash the
 * package declared before it uses it.
 */
async function produceTripDayVideo(jobId, onProgress) {
  const { renderTripDayVideo } = await import("./render.mjs");
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, ARTIFACT_TRIP_DAY_VIDEO);

  const { facts, timings } = await renderTripDayVideo({
    outputPath: target,
    inputsDir: path.join(DIRS.inputs, jobId),
    onProgress,
  });

  const bytes = await fs.readFile(target);
  const artifacts = [
    {
      kind: "video",
      filename: ARTIFACT_TRIP_DAY_VIDEO,
      size_bytes: bytes.length,
      sha256: sha256(bytes),
    },
  ];
  await writeAtomic(
    path.join(folder, "result.json"),
    `${JSON.stringify(
      buildResult({ jobId, completedAt: new Date(), artifacts, video: facts, timings }),
      null,
      2,
    )}\n`,
  );
  return { facts, timings };
}

/**
 * Remove a job's input package.
 *
 * Unconditional, and in the job's `finally`: whether the render succeeded,
 * failed or was refused, the day's photos have no reason to stay in a
 * directory every other app can read. A failure to delete is logged rather
 * than thrown - it must not turn a finished job into a failed one.
 */
async function discardInputs(jobId) {
  const folder = path.join(DIRS.inputs, jobId);
  try {
    await fs.rm(folder, { recursive: true, force: true });
  } catch (err) {
    log("warn", "Renderpaket konnte nicht aufgeräumt werden", {
      job_id: jobId,
      detail: String(err),
    });
  }
}

async function handleJob(name) {
  const jobId = name.replace(/\.json$/i, "");
  if (!isJobId(jobId)) {
    log("warn", "Dateiname ist keine Job-ID, wird ignoriert", { name });
    return;
  }
  if (activeJobs >= MAX_CONCURRENT_JOBS) {
    // Left in jobs/ on purpose: the next sweep picks it up. Refusing it
    // would turn a busy moment into a failed job.
    return;
  }
  const source = path.join(DIRS.jobs, name);
  const claimed = path.join(DIRS.processing, name);
  try {
    // Claim by rename. Losing this race is normal and simply means
    // somebody else has the job.
    await fs.rename(source, claimed);
  } catch {
    return;
  }
  log("info", "Auftrag übernommen", { job_id: jobId });

  activeJobs += 1;
  const jobStarted = Date.now();
  const jobDeadline = setTimeout(() => {
    log("error", "Auftrag überschreitet die Gesamtdauer", { job_id: jobId });
  }, MAX_JOB_DURATION_MS);

  try {
    await writeStatus(jobId, "claimed", { progress: 0 });
    const job = parseJob(await readJobFile(claimed), { now: Date.now() });
    await writeStatus(jobId, "running", { progress: 0.5 });
    if (job.action === "ping") {
      await writeStatus(jobId, "completed", { progress: 1 });
    } else if (job.action === "render_remotion_test") {
      // A render takes tens of seconds, so progress is reported while it
      // runs - otherwise Home Assistant cannot tell it apart from a hang.
      let lastReported = 0;
      const { facts, timings } = await produceRemotionVideo(jobId, job.message, (progress) => {
        const rounded = Math.floor(progress * 20) / 20;
        if (rounded > lastReported) {
          lastReported = rounded;
          void writeStatus(jobId, "running", { progress: rounded }).catch(() => {});
        }
      });
      await writeStatus(jobId, "completed", { progress: 1 });
      log("info", "Remotion-Render abgeschlossen", {
        job_id: jobId,
        seconds: timings.total,
        size_bytes: facts.size_bytes,
      });
    } else if (job.action === "render_trip_day") {
      let lastReported = 0;
      const { facts, timings } = await produceTripDayVideo(jobId, (progress) => {
        const rounded = Math.floor(progress * 20) / 20;
        if (rounded > lastReported) {
          lastReported = rounded;
          void writeStatus(jobId, "running", { progress: rounded }).catch(() => {});
        }
      });
      await writeStatus(jobId, "completed", { progress: 1 });
      log("info", "Tagesvideo abgeschlossen", {
        job_id: jobId,
        seconds: timings.total,
        size_bytes: facts.size_bytes,
        photos: facts.photo_count,
      });
    } else {
      await produceArtifacts(jobId, job.message);
      await writeStatus(jobId, "completed", { progress: 1 });
    }
    log("info", "Auftrag abgeschlossen", { job_id: jobId });
  } catch (err) {
    // RenderError is not imported at module level (see above), so it is
    // recognised by the shape it promises rather than by identity.
    const classified =
      err instanceof ProtocolError || (typeof err?.code === "string" && err.code !== "");
    const state = classified && err.code === "EXPIRED" ? "expired" : "failed";
    await writeStatus(jobId, state, {
      error: {
        // A classified failure keeps its own code - "BROWSER_MISSING" and
        // "OUTPUT_INVALID" are results worth reporting, and flattening them
        // to INTERNAL would throw away the finding.
        code: classified ? err.code : ERROR_INTERNAL,
        // Only the message, never a stack or a path: this string is shown
        // in Home Assistant's UI.
        message: classified ? err.message : "Interner Fehler bei der Verarbeitung.",
        retryable: false,
      },
    });
    // `String(err)` is the message the user already sees. The cause lives
    // in `err.detail` - the browser's own stderr, ffprobe's complaint -
    // and it is the only thing that makes a failure diagnosable. It goes
    // to the app log, which the operator reads, and never into the status
    // file, which crosses the exchange directory into the panel.
    log("warn", "Auftrag fehlgeschlagen", {
      job_id: jobId,
      state,
      detail: String(err),
      cause: String(err?.detail ?? "").slice(0, 600),
    });
  } finally {
    clearTimeout(jobDeadline);
    activeJobs -= 1;
    const seconds = (Date.now() - jobStarted) / 1000;
    if (seconds * 1000 > MAX_JOB_DURATION_MS) {
      log("warn", "Auftrag hat die Gesamtdauer überschritten", {
        job_id: jobId,
        seconds,
      });
    }
    await fs.rm(claimed, { force: true }).catch(() => {});
    await discardInputs(jobId);
  }
}

/** Total bytes below a directory, used to bound the results folder. */
async function directorySize(root) {
  let total = 0;
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    const entries = await fs.readdir(current, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else {
        const stat = await fs.stat(full).catch(() => null);
        if (stat) total += stat.size;
      }
    }
  }
  return total;
}

/**
 * A job left in processing/ when this process died is not running any more
 * — nothing is going to finish it. Reporting it as failed on startup is
 * what stops a Home Assistant restart from leaving a job "running" forever.
 */
async function recoverInterrupted() {
  const names = await fs.readdir(DIRS.processing).catch(() => []);
  for (const name of names) {
    const jobId = name.replace(/\.json$/i, "");
    if (!isJobId(jobId)) continue;
    await writeStatus(jobId, "failed", {
      error: {
        code: "INTERRUPTED",
        message: "Die Renderer-App wurde während der Verarbeitung neu gestartet.",
        retryable: true,
      },
    }).catch(() => {});
    await fs.rm(path.join(DIRS.processing, name), { force: true }).catch(() => {});
    await discardInputs(jobId);
    log("warn", "unterbrochenen Auftrag als fehlgeschlagen markiert", { job_id: jobId });
  }
}

async function cleanupOldResults() {
  const cutoff = Date.now() - RESULT_RETENTION_MS;
  const names = (await fs.readdir(DIRS.results).catch(() => [])).filter(isJobId);
  const folders = [];
  for (const name of names) {
    const folder = path.join(DIRS.results, name);
    const stat = await fs.stat(folder).catch(() => null);
    if (!stat) continue;
    if (stat.mtimeMs < cutoff) {
      await fs.rm(folder, { recursive: true, force: true }).catch(() => {});
      await fs.rm(path.join(DIRS.status, `${name}.json`), { force: true }).catch(() => {});
      finished.delete(name);
      log("info", "altes Ergebnis aufgeräumt", { job_id: name });
      continue;
    }
    folders.push({ name, folder, mtime: stat.mtimeMs });
  }

  // Age alone does not bound a folder that fills up faster than it ages.
  // Oldest first until the total is back under the budget.
  let total = await directorySize(DIRS.results);
  folders.sort((a, b) => a.mtime - b.mtime);
  for (const entry of folders) {
    if (total <= MAX_RESULT_BYTES) break;
    const size = await directorySize(entry.folder);
    await fs.rm(entry.folder, { recursive: true, force: true }).catch(() => {});
    await fs.rm(path.join(DIRS.status, `${entry.name}.json`), { force: true }).catch(() => {});
    finished.delete(entry.name);
    total -= size;
    log("info", "Ergebnis wegen Platzgrenze aufgeräumt", { job_id: entry.name });
  }

  // An input package whose job never arrived - Roadplanner wrote it while
  // this app was stopped, and then gave up. Nothing will ever claim it, and
  // it holds real photos, so it goes as soon as it is clearly abandoned.
  const inputCutoff = Date.now() - INPUT_RETENTION_MS;
  for (const name of (await fs.readdir(DIRS.inputs).catch(() => [])).filter(isJobId)) {
    const folder = path.join(DIRS.inputs, name);
    const stat = await fs.stat(folder).catch(() => null);
    if (!stat || stat.mtimeMs >= inputCutoff) continue;
    const pending =
      (await fs.stat(path.join(DIRS.jobs, `${name}.json`)).catch(() => null)) ||
      (await fs.stat(path.join(DIRS.processing, `${name}.json`)).catch(() => null));
    if (pending) continue;
    await fs.rm(folder, { recursive: true, force: true }).catch(() => {});
    log("info", "verwaistes Renderpaket aufgeräumt", { job_id: name });
  }

  // A crashed render can leave a .part behind; nothing ever reads those.
  for (const dir of [DIRS.results, DIRS.jobs, DIRS.status]) {
    for (const entry of await fs.readdir(dir).catch(() => [])) {
      if (entry.endsWith(".part") || entry.includes(".part.")) {
        await fs.rm(path.join(dir, entry), { force: true }).catch(() => {});
      }
    }
  }
}

async function main() {
  for (const dir of Object.values(DIRS)) {
    await fs.mkdir(dir, { recursive: true });
  }
  log("info", "Renderer-App startet", { version: APP_VERSION, exchange_dir: EXCHANGE_DIR });
  await writeHeartbeat();
  await recoverInterrupted();
  await cleanupOldResults();

  currentState = "ready";
  await writeHeartbeat();
  const heartbeat = setInterval(writeHeartbeat, HEARTBEAT_INTERVAL_MS);
  // Unref so a pending heartbeat timer cannot hold the process open during
  // shutdown; SIGTERM has to mean SIGTERM.
  heartbeat.unref();

  let sweeps = 0;
  while (running) {
    try {
      const names = (await fs.readdir(DIRS.jobs)).filter((name) => name.endsWith(".json"));
      for (const name of names.sort()) {
        if (!running) break;
        await handleJob(name);
      }
      if ((sweeps += 1) % 600 === 0) await cleanupOldResults();
    } catch (err) {
      // A broken directory must degrade the app, not kill it: Roadplanner
      // can then show "degraded" instead of the app simply vanishing.
      currentState = "degraded";
      log("error", "Durchlauf fehlgeschlagen", { detail: String(err) });
      await writeHeartbeat();
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }

  clearInterval(heartbeat);
  currentState = "stopping";
  await writeHeartbeat();
  log("info", "Renderer-App beendet");
}

const shutdown = (signal) => {
  if (!running) return;
  running = false;
  log("info", "Signal empfangen, fahre herunter", { signal });
};
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

main().catch((err) => {
  log("error", "Renderer-App abgestürzt", { detail: String(err) });
  process.exitCode = 1;
});
