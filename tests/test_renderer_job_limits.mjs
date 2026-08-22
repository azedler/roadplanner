/**
 * The worker's own limits, measured against the films it actually makes.
 *
 * Three numbers in `index.mjs` had drifted away from reality, and the
 * live log of 2026-08-22 caught all three in one run:
 *
 *  - A results folder budget of 512 MB, smaller than ONE 1440p film
 *    (584 MB). The film was pruned within seconds of being made -
 *    "Ergebnis wegen Platzmangels aufgeräumt", three jobs in one second -
 *    and putting music on it afterwards became impossible, because the
 *    mux reads its source from exactly that folder.
 *  - A flat half-hour job ceiling beside a render whose own ceiling
 *    follows the film. Every real film tripped it: two renders logged
 *    "Auftrag überschreitet die Gesamtdauer" and then finished
 *    successfully after 6 765 s and 6 776 s. A limit that breaks on every
 *    legitimate run teaches everyone to ignore it.
 *  - `fs.readFile` on the finished film, to hash it. A 584 MB journey
 *    became a 584 MB buffer at the moment the render ended, and the
 *    add-on was killed right there: "Reisefilm abgeschlossen", "Auftrag
 *    abgeschlossen", `Killed`.
 *
 * The remedy for the first two is the same one this project keeps
 * needing: derive the number from the one that already exists, and let a
 * test read BOTH files and compare.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { FILM_LIMITS, renderCeilingMs } from "../apps/roadplanner_renderer/src/film_limits.mjs";
import {
  RENDER_PROFILES,
  pixelFactor,
  renderProfile,
} from "../apps/roadplanner_renderer/src/render_profiles.mjs";
import { MAX_FILM_TOTAL_FRAMES } from "../apps/roadplanner_renderer/src/protocol.mjs";

const WORKER = readFileSync(
  new URL("../apps/roadplanner_renderer/src/index.mjs", import.meta.url),
  "utf-8",
);
// The OTHER deployable. Both sides hold a limit for the same file, and
// this project has paid four times for one of them being raised alone.
const PY_PROTOCOL = readFileSync(
  new URL("../custom_components/roadplanner_mcp/renderer_app_protocol.py", import.meta.url),
  "utf-8",
);
const pythonNumber = (name) => {
  const match = PY_PROTOCOL.match(new RegExp(`^${name} = ([^\\n#]+)`, "m"));
  assert.ok(match, `${name} not found in renderer_app_protocol.py`);
  // The constants are written as plain arithmetic on purpose ("2048 *
  // 1024 * 1024"), so the file stays readable. Only digits, spaces and
  // the operators that appear there are accepted.
  const expression = match[1].trim();
  assert.match(expression, /^[\d\s*+]+$/, `${name} is not a plain number: ${expression}`);
  return Function(`"use strict"; return (${expression});`)();
};

// The film from the live report: 1 h 53 min of rendering, 584 MB, 1440p.
const OBSERVED_FILM_SECONDS = 6776;
const OBSERVED_FILM_BYTES = 584_308_476;
const MAX_JOB_DURATION_MS = 420_000;
const KEEP_RECENT_RESULTS_IN_SOURCE = Number(
  (WORKER.match(/const KEEP_RECENT_RESULTS = (\d+);/) || [])[1] || 0,
);

const filmJobLimitMs = (profileId) =>
  renderCeilingMs(FILM_LIMITS, MAX_FILM_TOTAL_FRAMES, pixelFactor(renderProfile(profileId))) +
  MAX_JOB_DURATION_MS;

function verify_the_results_budget_is_derived_from_the_film_size() {
  assert.match(
    WORKER,
    /MAX_RESULT_BYTES[\s\S]{0,400}FILM_LIMITS\.maxOutputBytes \* 2/,
    "the folder budget must follow the size limit, not sit beside it as a second number",
  );
}

function verify_a_film_and_its_scored_version_fit_side_by_side() {
  // The pairing that MUST coexist: producing the second one reads the
  // first. A budget below this is what made a finished film unscorable.
  const budget = FILM_LIMITS.maxOutputBytes * 2;
  assert.ok(
    budget > OBSERVED_FILM_BYTES * 2,
    `the film that was pruned still would not fit: ${budget} vs ${OBSERVED_FILM_BYTES * 2}`,
  );
}

function verify_the_old_budget_would_have_failed_this_test() {
  assert.ok(
    512 * 1024 * 1024 < OBSERVED_FILM_BYTES,
    "the old budget was smaller than a single film - that is the bug, stated",
  );
}

function verify_no_real_film_trips_the_job_ceiling() {
  for (const id of Object.keys(RENDER_PROFILES)) {
    const limit = filmJobLimitMs(id);
    assert.ok(
      limit > OBSERVED_FILM_SECONDS * 1000,
      `${id}: a film that finished in ${OBSERVED_FILM_SECONDS} s would still be reported as overrunning`,
    );
  }
}

function verify_a_bigger_profile_gets_more_time() {
  assert.ok(
    filmJobLimitMs("uhd_4k") > filmJobLimitMs("review_720"),
    "the ceiling has to follow the work, or the next profile breaks it again",
  );
}

function verify_the_worker_derives_that_ceiling_rather_than_naming_one() {
  assert.match(
    WORKER,
    /const filmJobLimitMs = \(profileId\) =>\s*\n?\s*renderCeilingMs\(/,
    "the job ceiling must read the same source the render reads",
  );
  assert.ok(
    !/ROADPLANNER_MAX_FILM_JOB_MS/.test(WORKER),
    "the flat half hour must be gone, not merely raised",
  );
}

function verify_a_wedged_render_is_still_caught_by_something() {
  // The ceiling is now loose by design, so it cannot be the guard. A wall
  // clock never could tell "slow" from "stuck"; no-progress-at-all can.
  assert.ok(FILM_LIMITS.stallTimeoutMs > 0, "no stall watchdog");
  assert.ok(
    FILM_LIMITS.stallTimeoutMs < filmJobLimitMs("review_720"),
    "the stall guard has to fire long before the wall clock",
  );
}

function verify_the_finished_film_is_never_read_into_memory() {
  assert.ok(
    !/const bytes = await fs\.readFile\(target\)/.test(WORKER),
    "hashing the film by reading all of it is what got the add-on killed",
  );
  const helper = WORKER.split("async function describeVideoArtifact(")[1] ?? "";
  const body = helper.split("\nasync function ")[0];
  assert.match(body, /createReadStream\(/, "the digest has to be taken a chunk at a time");
  assert.match(body, /hash\.update\(chunk\)/, body);
  assert.match(body, /size \+= chunk\.length/, "and the size counted along the way");
  const finalisers = WORKER.match(/describeVideoArtifact\(target, /g) || [];
  assert.equal(finalisers.length, 5, "every artefact that is a video goes through it");
}

function verify_both_deployables_allow_the_same_single_film_size() {
  // The contract test #374 asks for, and the remedy this project keeps
  // needing: read BOTH files and compare, rather than trusting that the
  // two numbers were changed together.
  assert.equal(
    pythonNumber("MAX_FILM_ARTIFACT_BYTES"),
    FILM_LIMITS.maxOutputBytes,
    "the integration and the renderer disagree on how large one film may be",
  );
}

function verify_the_film_job_ttl_outlives_a_real_render() {
  const ttl = pythonNumber("FILM_JOB_TTL_SECONDS");
  assert.ok(
    ttl > OBSERVED_FILM_SECONDS,
    `a film that renders in ${OBSERVED_FILM_SECONDS} s expires at a TTL of ${ttl} s`,
  );
  // One worker takes one job at a time, so a film submitted while
  // another is rendering waits that whole render before it is claimed.
  assert.ok(
    ttl > OBSERVED_FILM_SECONDS * 2,
    `a film queued behind another one expires at a TTL of ${ttl} s`,
  );
  assert.ok(
    pythonNumber("JOB_TTL_SECONDS") < ttl,
    "a small job must not inherit the film's patience",
  );
}

function verify_the_cleanup_cannot_delete_the_film_it_just_wrote() {
  const body = WORKER.split("async function cleanupOldResults()")[1] ?? "";
  const loop = body.split("An input package whose job never arrived")[0];
  assert.match(
    loop,
    /const evictable =\s*\n?\s*folders\.length > KEEP_RECENT_RESULTS \? folders\.slice\(0, -KEEP_RECENT_RESULTS\) : \[\];/,
    "the newest results have to be held back before anything is evicted",
  );
  assert.match(loop, /for \(const entry of evictable\)/, "the loop must walk the evictable set");
  assert.ok(
    !/for \(const entry of folders\)\s*\{\s*\n\s*if \(total/.test(loop),
    "the space loop must not walk every folder, including the newest",
  );
  assert.ok(KEEP_RECENT_RESULTS_IN_SOURCE >= 2, "a film and its scored version, at least");
}

function verify_a_result_dropped_for_space_leaves_its_reason_behind() {
  assert.match(WORKER, /await recordPruned\(pruned\)/, "nothing records why a result went");
  assert.match(WORKER, /reason: "space"/, "the ledger has to say WHY the result went");
  const integration = readFileSync(
    new URL("../custom_components/roadplanner_mcp/trip_film_export.py", import.meta.url),
    "utf-8",
  );
  assert.match(
    integration,
    /aus Platzgründen/,
    "the message still says 'no result' where the truth is 'deleted for space'",
  );
}

const checks = Object.entries({
  verify_both_deployables_allow_the_same_single_film_size,
  verify_the_film_job_ttl_outlives_a_real_render,
  verify_the_cleanup_cannot_delete_the_film_it_just_wrote,
  verify_a_result_dropped_for_space_leaves_its_reason_behind,
  verify_the_results_budget_is_derived_from_the_film_size,
  verify_a_film_and_its_scored_version_fit_side_by_side,
  verify_the_old_budget_would_have_failed_this_test,
  verify_no_real_film_trips_the_job_ceiling,
  verify_a_bigger_profile_gets_more_time,
  verify_the_worker_derives_that_ceiling_rather_than_naming_one,
  verify_a_wedged_render_is_still_caught_by_something,
  verify_the_finished_film_is_never_read_into_memory,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
