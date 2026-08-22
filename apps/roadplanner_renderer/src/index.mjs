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
import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

import {
  ARTIFACT_IMAGE,
  ARTIFACT_TEXT,
  ARTIFACT_TRIP_DAY_VIDEO,
  ARTIFACT_FILM_WITH_MUSIC,
  ARTIFACT_REVIEW_COPY,
  ARTIFACT_TRIP_FILM_VIDEO,
  CANCEL_DIR,
  MUSIC_PACKAGE_FILENAME,
  parseMusicPackage,
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
  MAX_FILM_TOTAL_FRAMES,
} from "./protocol.mjs";
import { FILM_LIMITS, renderCeilingMs } from "./film_limits.mjs";
import { pixelFactor, renderProfile } from "./render_profiles.mjs";

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

/**
 * How long a FILM job may take before something is clearly wrong.
 *
 * Derived, not chosen. It was a flat half hour beside a render whose own
 * ceiling follows the film - and every real film tripped it: the live log
 * shows "Auftrag überschreitet die Gesamtdauer" on two renders that then
 * went on to finish successfully after 6 765 s and 6 776 s. A limit that
 * breaks on every legitimate run teaches everyone to ignore it, which is
 * worse than having none.
 *
 * So it reads the same source the render does, for the longest film the
 * protocol permits at the size this job is actually drawn at, plus the
 * time everything around the render costs. What still catches a wedged
 * browser is `FILM_LIMITS.stallTimeoutMs` - no progress at all - because
 * a wall clock cannot tell "slow" from "stuck" and never could.
 */
const filmJobLimitMs = (profileId) =>
  renderCeilingMs(
    FILM_LIMITS,
    MAX_FILM_TOTAL_FRAMES,
    pixelFactor(renderProfile(profileId)),
  ) + MAX_JOB_DURATION_MS;

/**
 * How much the results folder may hold.
 *
 * Also derived, and for a reason the live log spelled out: at 512 MB the
 * budget was smaller than ONE film. A 1440p journey lands at 584 MB, so
 * the moment it was written the folder was over budget and the oldest
 * results were dropped - three of them in one second, including films
 * somebody still wanted. That is what left a finished film impossible to
 * score afterwards: the mux reads its source from exactly this folder.
 *
 * The floor is therefore "a film and its scored version, side by side" -
 * that pairing MUST be able to coexist, because producing the second one
 * reads the first. Two of the largest film the renderer will produce,
 * which keeps this number honest when the size limit moves again.
 */
let maxResultBytes = Number(
  process.env.ROADPLANNER_MAX_RESULT_BYTES || FILM_LIMITS.maxOutputBytes * 2,
);

/**
 * How many of the newest results the space cleanup may never touch.
 *
 * The loop below evicts oldest-first while the folder is over budget, and
 * `folders` contains EVERY surviving folder - including the one written
 * seconds ago. When a single film is larger than the whole budget, that
 * loop ran until it had deleted the film the worker had just finished.
 * The live log caught it: three results dropped in one second, the
 * newest among them, and putting music on that film was impossible ever
 * after because the mux reads its source from exactly this folder.
 *
 * Three, because that is one journey: the film, the same film with its
 * soundtrack, and a review copy. Everything older than those may go.
 */
const KEEP_RECENT_RESULTS = 3;

/**
 * Which results were dropped for space, so the reason survives them.
 *
 * "Zu diesem Auftrag gibt es kein Ergebnis" is true and useless - it
 * reads as "this job never produced anything" when what happened is
 * "your film was deleted to make room". Roadplanner reads this file to
 * say the second thing.
 */
const PRUNED_LEDGER = "pruned.json";
const MAX_PRUNED_ENTRIES = 200;

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
  // Where a stop is asked for. One file per job, named after it and
  // empty: the request carries no data, so there is nothing in it that
  // could be wrong except the name - and the name is a job id.
  cancel: path.join(EXCHANGE_DIR, CANCEL_DIR),
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
/**
 * A finished video's size and digest, without ever holding it in memory.
 *
 * This used to be `fs.readFile(target)` followed by `sha256(bytes)`, and
 * it was by far the largest allocation this process ever made: the
 * moment a render finished, a 584 MB journey became a 584 MB buffer.
 * The live log shows what that cost - "Reisefilm abgeschlossen", then
 * "Auftrag abgeschlossen", then `Killed`, the signature of an
 * out-of-memory kill. The watchdog was off, so the add-on stayed down,
 * and the automatic scoring that should have followed the render never
 * ran: the film came out silent although music had been chosen.
 *
 * Nothing ever needed those bytes. The result manifest wants a length
 * and a digest, and both can be had a chunk at a time - so a two-hour
 * film now costs the same handful of megabytes as a ten-second one.
 */
async function describeVideoArtifact(file, filename) {
  const hash = createHash("sha256");
  let size = 0;
  for await (const chunk of createReadStream(file, { highWaterMark: 4 * 1024 * 1024 })) {
    size += chunk.length;
    hash.update(chunk);
  }
  return {
    kind: "video",
    filename,
    size_bytes: size,
    sha256: hash.digest("hex"),
  };
}

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

  const artifacts = [await describeVideoArtifact(target, ARTIFACT_VIDEO)];
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

  const artifacts = [await describeVideoArtifact(target, ARTIFACT_TRIP_DAY_VIDEO)];
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

/**
 * Remove a result folder that never got a result.
 *
 * The folder has to exist before the render starts - the partial file is
 * written inside it. So a job that fails leaves an empty directory behind,
 * and `result.json` being absent is exactly what "incomplete" means, since
 * it is the last file written. Harmless to read, but it is litter in a
 * shared folder, and cleanup by age would keep it for a day.
 */
async function discardIncompleteResult(jobId) {
  const folder = path.join(DIRS.results, jobId);
  try {
    await fs.stat(path.join(folder, "result.json"));
    return;
  } catch {
    // No result.json: nothing here is worth keeping.
  }
  try {
    await fs.rm(folder, { recursive: true, force: true });
  } catch (err) {
    log("warn", "unvollständiges Ergebnis konnte nicht aufgeräumt werden", {
      job_id: jobId,
      detail: String(err),
    });
  }
}

/**
 * Render a whole trip from its film package.
 *
 * Same shape as the other producers. The difference is scale: this one can
 * run for ten minutes and read seventy photos, so the job deadline it runs
 * under is the film's, not the clip's.
 */
async function produceTripFilm(jobId, profileId, onProgress, isCancelled, frameRange) {
  const { renderTripFilmVideo } = await import("./render.mjs");
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, ARTIFACT_TRIP_FILM_VIDEO);

  const { facts, timings } = await renderTripFilmVideo({
    outputPath: target,
    inputsDir: path.join(DIRS.inputs, jobId),
    profileId,
    onProgress,
    isCancelled,
    frameRange,
  });

  const artifacts = [await describeVideoArtifact(target, ARTIFACT_TRIP_FILM_VIDEO)];
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
 * Make a small copy of a film an earlier job produced.
 *
 * The source is found from a job id, never from a name in the job file:
 * `results/<source_job_id>/roadplanner-trip-film.mp4` is built from a
 * value that has already been matched against the job-id pattern, and the
 * filename is a constant. There is nothing here for a crafted job to
 * steer, which is a stronger guarantee than sanitising a path would be.
 *
 * The copy gets its own results folder, like every other job. A review
 * copy is an artefact somebody asked for, not a second file smuggled into
 * a finished job's result.
 */
async function produceReviewCopy(jobId, sourceJobId, profileId, onProgress, isCancelled) {
  if (!isJobId(sourceJobId)) {
    throw new ProtocolError(ERROR_INTERNAL, "Ungültige Quell-Job-ID.");
  }
  const { createReviewCopy } = await import("./render.mjs");
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, ARTIFACT_REVIEW_COPY);

  // Whichever film that job produced. A mux job's folder holds the
  // vertonte film and a render job's the silent one, never both - so
  // this is a lookup rather than a choice. Without it the copy somebody
  // sends out for review would be the silent cut, which is the one
  // question a review of a finished film cannot answer.
  let sourcePath = path.join(DIRS.results, sourceJobId, ARTIFACT_FILM_WITH_MUSIC);
  try {
    await fs.access(sourcePath);
  } catch {
    sourcePath = path.join(DIRS.results, sourceJobId, ARTIFACT_TRIP_FILM_VIDEO);
  }

  const { facts, timings } = await createReviewCopy({
    sourcePath,
    outputPath: target,
    profileId,
    onProgress,
    isCancelled,
  });

  const artifacts = [await describeVideoArtifact(target, ARTIFACT_REVIEW_COPY)];
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
 * The soundtrack onto a film that already exists.
 *
 * Cheapest job that produces a film: the video stream is copied, so a
 * twelve-minute score goes on in seconds. That is what makes the order
 * right - the film is rendered first, its length is then measured rather
 * than estimated, and a soundtrack somebody does not like costs another
 * few seconds instead of another render.
 *
 * The audio arrives in this job's own inputs folder, exactly as a film
 * package does, and is verified byte for byte before ffmpeg sees it.
 */
async function produceFilmMusic(jobId, sourceJobId, onProgress, isCancelled) {
  if (!isJobId(sourceJobId)) {
    throw new ProtocolError(ERROR_INTERNAL, "Ungültige Quell-Job-ID.");
  }
  const inputs = path.join(DIRS.inputs, jobId);
  let manifest;
  try {
    manifest = await fs.readFile(path.join(inputs, MUSIC_PACKAGE_FILENAME), "utf8");
  } catch {
    throw new ProtocolError(ERROR_INTERNAL, "Zu diesem Auftrag gibt es kein Musikpaket.");
  }
  const parsed = parseMusicPackage(manifest);

  // Same defence the film package gets: the declared hash decides, and a
  // file whose bytes disagree with it never reaches a decoder.
  const sections = [];
  for (const section of parsed.sections) {
    const file = path.join(inputs, section.path);
    const bytes = await fs.readFile(file).catch(() => null);
    if (!bytes) {
      throw new ProtocolError(ERROR_INTERNAL, `Musikdatei fehlt: ${section.path}`);
    }
    if (sha256(bytes) !== section.sha256) {
      throw new ProtocolError(ERROR_INTERNAL, "Musikdatei: SHA-256 stimmt nicht.");
    }
    sections.push({ ...section, path: file });
  }

  const { muxFilmMusic } = await import("./render.mjs");
  const folder = path.join(DIRS.results, jobId);
  await fs.mkdir(folder, { recursive: true });
  const target = path.join(folder, ARTIFACT_FILM_WITH_MUSIC);

  const { facts, timings } = await muxFilmMusic({
    sourcePath: path.join(DIRS.results, sourceJobId, ARTIFACT_TRIP_FILM_VIDEO),
    outputPath: target,
    sections,
    volume: parsed.volume,
    // Only present for the architecture comparison, where three
    // fassungen of one film have to be judged against each other and
    // the louder one would otherwise simply win.
    targetLufs: parsed.targetLufs,
    truePeakDbtp: parsed.truePeakDbtp,
    variant: parsed.variant,
    onProgress,
    isCancelled,
  });

  const artifacts = [await describeVideoArtifact(target, ARTIFACT_FILM_WITH_MUSIC)];
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
 * Has somebody asked for this job to stop?
 *
 * Read from disk on every call rather than watched, for the same reason
 * everything else here is: the request comes from another container, and
 * a one-line existence check between progress reports costs nothing
 * measurable against a frame that takes 140 ms to draw.
 */
async function cancelRequested(jobId) {
  try {
    await fs.access(path.join(DIRS.cancel, `${jobId}.json`));
    return true;
  } catch {
    return false;
  }
}

/** The request is answered; the marker has done its work. */
async function clearCancel(jobId) {
  await fs.rm(path.join(DIRS.cancel, `${jobId}.json`), { force: true }).catch(() => {});
}

/**
 * Markers nobody came back for.
 *
 * A cancel for a job that had already finished, or one written while the
 * app was down, would otherwise sit in the folder and stop a LATER job
 * that happened to be handed the same id - which cannot occur with uuid4,
 * but a directory that grows without bound is its own problem.
 */
async function cleanupOldCancels() {
  const cutoff = Date.now() - RESULT_RETENTION_MS;
  for (const name of await fs.readdir(DIRS.cancel).catch(() => [])) {
    const file = path.join(DIRS.cancel, name);
    const stat = await fs.stat(file).catch(() => null);
    if (stat && stat.mtimeMs < cutoff) {
      await fs.rm(file, { force: true }).catch(() => {});
    }
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
  // Set from the file's own action before parsing, so a film is not
  // reported as overrunning a limit that was never meant for it.
  let jobLimitMs = MAX_JOB_DURATION_MS;
  let jobDeadline = setTimeout(() => {
    log("error", "Auftrag überschreitet die Gesamtdauer", { job_id: jobId });
  }, jobLimitMs);

  try {
    await writeStatus(jobId, "claimed", { progress: 0 });
    const job = parseJob(await readJobFile(claimed), { now: Date.now() });
    // Both jobs work on a whole film rather than a clip: one draws twelve
    // minutes of video, the other re-encodes twelve minutes of it. The
    // copy is far quicker, but "quicker than an hour" is still minutes,
    // and the clip's ceiling was never meant for either.
    if (job.action === "render_trip_film" || job.action === "create_review_copy") {
      clearTimeout(jobDeadline);
      jobLimitMs = filmJobLimitMs(job.renderProfile);
      jobDeadline = setTimeout(() => {
        log("error", "Auftrag überschreitet die Gesamtdauer", { job_id: jobId });
      }, jobLimitMs);
    }
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
    } else if (job.action === "create_review_copy") {
      let lastReported = 0;
      const { facts, timings } = await produceReviewCopy(
        jobId,
        job.sourceJobId,
        job.renderProfile,
        (progress) => {
          const rounded = Math.floor(progress * 100) / 100;
          if (rounded > lastReported) {
            lastReported = rounded;
            void writeStatus(jobId, "running", { progress: rounded }).catch(() => {});
          }
        },
        () => cancelRequested(jobId),
      );
      await writeStatus(jobId, "completed", { progress: 1 });
      log("info", "Review-Kopie abgeschlossen", {
        job_id: jobId,
        source_job_id: job.sourceJobId,
        profile: facts.render_profile,
        seconds: timings.total,
        size_bytes: facts.size_bytes,
        source_size_bytes: facts.source_size_bytes,
      });
    } else if (job.action === "add_music") {
      let lastReported = 0;
      const { facts, timings } = await produceFilmMusic(
        jobId,
        job.sourceJobId,
        (progress) => {
          const rounded = Math.floor(progress * 100) / 100;
          if (rounded > lastReported) {
            lastReported = rounded;
            void writeStatus(jobId, "running", { progress: rounded }).catch(() => {});
          }
        },
        () => cancelRequested(jobId),
      );
      await writeStatus(jobId, "completed", { progress: 1 });
      log("info", "Musik aufgelegt", {
        job_id: jobId,
        source_job_id: job.sourceJobId,
        sections: facts.music_sections,
        // The measured length the score was fitted to, which is the whole
        // point of putting the music on last.
        film_seconds: facts.measured_seconds,
        seconds: timings.total,
        size_bytes: facts.size_bytes,
      });
    } else if (job.action === "render_trip_film") {
      let lastReported = 0;
      const { facts, timings } = await produceTripFilm(
        jobId,
        job.renderProfile,
        (progress) => {
        // Finer steps than the clip: ten minutes at 5 % granularity would
        // look frozen for half a minute at a time.
          const rounded = Math.floor(progress * 100) / 100;
          if (rounded > lastReported) {
            lastReported = rounded;
            void writeStatus(jobId, "running", { progress: rounded }).catch(() => {});
          }
        },
        () => cancelRequested(jobId),
        job.frameRange,
      );
      await writeStatus(jobId, "completed", { progress: 1 });
      log("info", "Reisefilm abgeschlossen", {
        job_id: jobId,
        seconds: timings.total,
        size_bytes: facts.size_bytes,
        chapters: facts.chapter_count,
        photos: facts.photo_count,
        chapters_without_photos: facts.chapters_without_photos,
        frame_range: job.frameRange ? job.frameRange.join("-") : "",
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
    // Three terminal outcomes, not two. A render somebody stopped is not
    // a render that broke: calling it "failed" sends them looking for a
    // cause that does not exist, and makes the reasonable next move -
    // press it again - look like the fix.
    let state = "failed";
    if (classified && err.code === "EXPIRED") state = "expired";
    else if (classified && err.code === "CANCELLED") state = "cancelled";
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
    log(state === "cancelled" ? "info" : "warn",
      state === "cancelled" ? "Auftrag abgebrochen" : "Auftrag fehlgeschlagen", {
      job_id: jobId,
      state,
      detail: String(err),
      cause: String(err?.detail ?? "").slice(0, 600),
    });
  } finally {
    clearTimeout(jobDeadline);
    activeJobs -= 1;
    const seconds = (Date.now() - jobStarted) / 1000;
    if (seconds * 1000 > jobLimitMs) {
      log("warn", "Auftrag hat die Gesamtdauer überschritten", {
        job_id: jobId,
        seconds,
      });
    }
    await fs.rm(claimed, { force: true }).catch(() => {});
    await discardInputs(jobId);
    await discardIncompleteResult(jobId);
    // The request has been answered either way. Leaving the marker would
    // make the NEXT thing this worker is told about that job read as
    // "stop", and a cancel that outlives its job is a trap.
    await clearCancel(jobId);
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
    await discardIncompleteResult(jobId);
    log("warn", "unterbrochenen Auftrag als fehlgeschlagen markiert", { job_id: jobId });
  }
}

/**
 * Remember that these results were dropped for space.
 *
 * Bounded and best-effort: the ledger exists so a later "where is my
 * film?" can be answered with the truth instead of "there is no result",
 * and a ledger that failed to write must never break the cleanup that
 * was the actual job.
 */
async function recordPruned(jobIds) {
  const file = path.join(DIRS.results, PRUNED_LEDGER);
  let known = [];
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed?.pruned)) known = parsed.pruned;
  } catch {
    // No ledger yet, or an unreadable one. Either way it is rebuilt.
  }
  const at = formatTime(new Date());
  const merged = [
    ...known.filter((entry) => entry && typeof entry === "object" && isJobId(entry.job_id)),
    ...jobIds.map((jobId) => ({ job_id: jobId, reason: "space", pruned_at: at })),
  ].slice(-MAX_PRUNED_ENTRIES);
  try {
    await writeAtomic(file, `${JSON.stringify({ pruned: merged }, null, 2)}\n`);
  } catch (err) {
    log("warn", "Verzeichnis der aufgeräumten Ergebnisse nicht schreibbar", {
      detail: String(err),
    });
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
  // Oldest first until the total is back under the budget - but the
  // newest results are off limits whatever the arithmetic says. Without
  // that, a single film larger than the whole budget made this loop run
  // until it had deleted the film the worker had just produced.
  let total = await directorySize(DIRS.results);
  folders.sort((a, b) => a.mtime - b.mtime);
  const evictable =
    folders.length > KEEP_RECENT_RESULTS ? folders.slice(0, -KEEP_RECENT_RESULTS) : [];
  const pruned = [];
  for (const entry of evictable) {
    if (total <= maxResultBytes) break;
    const size = await directorySize(entry.folder);
    await fs.rm(entry.folder, { recursive: true, force: true }).catch(() => {});
    await fs.rm(path.join(DIRS.status, `${entry.name}.json`), { force: true }).catch(() => {});
    finished.delete(entry.name);
    total -= size;
    pruned.push(entry.name);
    log("info", "Ergebnis wegen Platzgrenze aufgeräumt", { job_id: entry.name });
  }
  if (pruned.length) await recordPruned(pruned);
  if (total > maxResultBytes) {
    // Said out loud rather than solved by deleting something that is
    // still wanted. A folder that stays over budget on its newest few
    // results is a disk question, not a cleanup question.
    log("warn", "Ergebnisordner bleibt über der Platzgrenze", {
      bytes: total,
      limit: maxResultBytes,
      kept: Math.min(folders.length, KEEP_RECENT_RESULTS),
    });
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

  await cleanupOldCancels();

  // A crashed render can leave a .part behind; nothing ever reads those.
  for (const dir of [DIRS.results, DIRS.jobs, DIRS.status]) {
    for (const entry of await fs.readdir(dir).catch(() => [])) {
      if (entry.endsWith(".part") || entry.includes(".part.")) {
        await fs.rm(path.join(dir, entry), { force: true }).catch(() => {});
      }
    }
  }
}

/**
 * What the user set in the app's own configuration.
 *
 * Only one thing is readable here so far, and it is the one a user could
 * not otherwise reach: how much room the results folder may take. The
 * environment variable still wins, because that is the deliberate
 * override; the option exists so somebody with a small disk - or a very
 * large film - is not stuck with a number they cannot see.
 */
async function applyAddonOptions() {
  let raw;
  try {
    raw = await fs.readFile("/data/options.json", "utf8");
  } catch {
    return; // Not running as a Home Assistant app. Defaults stand.
  }
  let options;
  try {
    options = JSON.parse(raw);
  } catch {
    log("warn", "Die App-Konfiguration ist kein gültiges JSON - Standardwerte gelten");
    return;
  }
  const gib = Number(options?.max_result_gib);
  if (process.env.ROADPLANNER_MAX_RESULT_BYTES) return;
  if (Number.isFinite(gib) && gib > 0) {
    maxResultBytes = Math.round(gib * 1024 * 1024 * 1024);
    log("info", "Platzgrenze für Ergebnisse aus der Konfiguration", {
      bytes: maxResultBytes,
    });
  }
}

async function main() {
  for (const dir of Object.values(DIRS)) {
    await fs.mkdir(dir, { recursive: true });
  }
  await applyAddonOptions();
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
