/**
 * The renderer's side of the contract, without rendering anything.
 *
 * These checks run in the normal release suite, where no Remotion
 * dependency is installed - so they exercise the guard clauses that run
 * BEFORE the first `import("@remotion/...")`, and read the source for the
 * invariants that cannot be observed from outside. The actual render is a
 * separate CI job (`.github/workflows/remotion-spike.yml`).
 */
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const RENDERER = new URL("../remotion_renderer/scripts/render.mjs", import.meta.url);
const source = readFileSync(RENDERER, "utf-8");
// Comments explain that `ensureBrowser` must never be called - so the
// checks below run against the CODE with comments stripped, or the prose
// would be what the test finds.
const code = source
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

// --- invariants that only the source can show -------------------------

// The spike must never fetch a browser onto the user's machine. Remotion
// offers exactly one API for that, and it must not appear here.
assert.doesNotMatch(code, /ensureBrowser/, "the renderer must never fetch a browser");
assert.doesNotMatch(code, /child_process|execSync|spawnSync/, "no shelling out");

// stdout is protocol only. A stray console.log in the middle of the stream
// would corrupt every event Python reads after it.
assert.doesNotMatch(
  code,
  /console\.log/,
  "diagnostics belong on stderr, never on the protocol channel",
);

// A half-written file must never become the artefact.
assert.match(code, /\.part\.mp4/, "the output is staged under a temporary name");
assert.match(code, /renameSync/, "and only renamed once it is complete");

// The composition whitelist has to agree with the Python side.
const pythonJob = readFileSync(
  new URL("../custom_components/roadplanner_mcp/remotion_job.py", import.meta.url),
  "utf-8",
);
const pythonNames = [...pythonJob.matchAll(/ALLOWED_COMPOSITIONS = \(([^)]*)\)/g)]
  .flatMap((match) => [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]));
const nodeNames = [...code.matchAll(/ALLOWED_COMPOSITIONS = new Set\(\[([^\]]*)\]/g)]
  .flatMap((match) => [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]));
assert.deepEqual(
  nodeNames.sort(),
  pythonNames.sort(),
  "both sides must whitelist exactly the same compositions",
);

const root = readFileSync(
  new URL("../remotion_renderer/src/Root.tsx", import.meta.url),
  "utf-8",
);
for (const name of nodeNames) {
  assert.ok(root.includes(`id="${name}"`), `Root.tsx must register ${name}`);
}

// --- guard clauses, driven through a real process ---------------------

const workdir = mkdtempSync(join(tmpdir(), "remotion-contract-"));

const runWithJob = (job) => {
  const jobPath = join(workdir, `${Math.random().toString(36).slice(2)}.json`);
  writeFileSync(jobPath, JSON.stringify(job), "utf-8");
  const result = spawnSync(process.execPath, [RENDERER.pathname, jobPath], {
    encoding: "utf-8",
    timeout: 30000,
  });
  return result;
};

const baseJob = {
  schema_version: 1,
  job_id: "contract",
  composition: "roadplanner-test",
  output_path: join(workdir, "out.mp4"),
  input: { title: "T", subtitle: "S", duration_seconds: 5 },
  render: { width: 1280, height: 720, fps: 30, codec: "h264", crf: 20, x264_preset: "veryfast" },
  runtime: { browser_executable: null, allow_downloads: false },
};

const lastEvent = (stdout) => {
  const lines = String(stdout || "").trim().split("\n").filter(Boolean);
  assert.ok(lines.length, "the renderer must speak the protocol even when refusing");
  return JSON.parse(lines[lines.length - 1]);
};

for (const [label, job] of [
  ["unknown composition", { ...baseJob, composition: "fremd" }],
  ["unsupported codec", { ...baseJob, render: { ...baseJob.render, codec: "vp9" } }],
  ["wrong schema version", { ...baseJob, schema_version: 99 }],
  ["downloads requested", { ...baseJob, runtime: { allow_downloads: true } }],
  ["missing output path", { ...baseJob, output_path: "" }],
]) {
  const result = runWithJob(job);
  assert.equal(result.status, 1, `${label}: must exit non-zero`);
  const event = lastEvent(result.stdout);
  assert.equal(event.event, "failed", label);
  assert.ok(event.code, `${label}: a refusal names a code`);
}

// A job file that is not JSON at all must fail the same controlled way.
{
  const jobPath = join(workdir, "broken.json");
  writeFileSync(jobPath, "{not json", "utf-8");
  const result = spawnSync(process.execPath, [RENDERER.pathname, jobPath], {
    encoding: "utf-8",
    timeout: 30000,
  });
  assert.equal(result.status, 1);
  assert.equal(lastEvent(result.stdout).event, "failed");
}

// No job path at all.
{
  const result = spawnSync(process.execPath, [RENDERER.pathname], {
    encoding: "utf-8",
    timeout: 30000,
  });
  assert.equal(result.status, 1);
  assert.equal(lastEvent(result.stdout).event, "failed");
}

console.log("Remotion renderer contract tests passed.");
