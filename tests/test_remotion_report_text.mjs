/**
 * The copied diagnosis report is read as evidence for a Go/No-Go decision,
 * so every line of it has to be true.
 *
 * Live report from the camper: "Status: NODE_MISSING … ffmpeg/ffprobe:
 * nein / nein … Ausgabeordner beschreibbar: nein" - on a Home Assistant
 * that renders trip videos with that ffmpeg. The backend had returned
 * early on the first blocking finding, so those fields were never checked;
 * the report turned each empty field into a confident "nein".
 *
 * Two properties are pinned here: a checked-and-present fact is reported
 * as present, and a field the backend did not report says "nicht geprüft"
 * rather than borrowing the wording of a failed check.
 */
import assert from "node:assert/strict";

const { remotionSpikeMixin } = await import(
  new URL(
    "../custom_components/roadplanner_mcp/frontend/features/remotion-spike.js",
    import.meta.url,
  )
);

const report = (details, status) =>
  remotionSpikeMixin._remotionReportText.call({
    ...remotionSpikeMixin,
    _remotionDiagnosis: { status: status || "NODE_MISSING", details },
    _remotionStatus: null,
  });

// --- the live case: Node missing, but everything else WAS checked -------
const live = report({
  platform: "Linux",
  machine: "x86_64",
  node_path: "",
  npm_path: "",
  ffmpeg_path: "/usr/bin/ffmpeg",
  ffprobe_path: "/usr/bin/ffprobe",
  browser_path: "",
  renderer_path: "",
  renderer_configured: false,
  output_writable: true,
  free_bytes: 8 * 1024 * 1024 * 1024,
});

assert.match(live, /^Status: NODE_MISSING$/m);
assert.match(
  live,
  /^ffmpeg: ja \/ ffprobe: ja$/m,
  `ffmpeg is present and must be reported as such:\n${live}`,
);
assert.match(
  live,
  /^Ausgabeordner beschreibbar: ja$/m,
  `a writable directory must not be reported as unwritable:\n${live}`,
);
assert.match(live, /^Node: nicht gefunden$/m);

// --- a field the backend never reported must say so --------------------
const partial = report({ platform: "Linux", machine: "x86_64", node_path: "" });
assert.match(
  partial,
  /^ffmpeg: nicht geprüft \/ ffprobe: nicht geprüft$/m,
  `an absent key is not a negative finding:\n${partial}`,
);
assert.match(partial, /^Ausgabeordner beschreibbar: nicht geprüft$/m);

// --- node found but unspawnable is its own finding, not "missing" ------
const forbidden = report(
  { platform: "Linux", machine: "x86_64", node_path: "/usr/bin/node", ffmpeg_path: "" },
  "SUBPROCESS_FORBIDDEN",
);
assert.match(
  forbidden,
  /^Node: gefunden, aber nicht startbar \(\/usr\/bin\/node\)$/m,
  `"missing" and "cannot be spawned" are different results:\n${forbidden}`,
);
assert.match(forbidden, /^ffmpeg: nein \/ ffprobe: nicht geprüft$/m);

console.log("Remotion report text tests passed.");
