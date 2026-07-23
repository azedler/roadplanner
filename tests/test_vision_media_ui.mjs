import fs from "node:fs";

const source = fs.readFileSync("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", "utf8");

if (!source.includes('data-action="media-curate-stop"')) {
  throw new Error("Stop albums must expose manual Vision re-evaluation when enabled");
}
if (!source.includes('"media_curate_stop"')) {
  throw new Error("The panel must call the media_curate_stop backend action");
}
if (!source.includes('data-action="media-curate-trip"')) {
  throw new Error("The media page must allow a bounded trip-level re-evaluation when Vision is enabled");
}
if (!source.includes('"media_curate_trip"')) {
  throw new Error("The panel must call the media_curate_trip backend action");
}
if (!source.includes("Lokal · keine Bilder werden an Gemini gesendet")) {
  throw new Error("Local-only privacy mode must be visible in the media UI");
}
if (!source.includes("Hybrid · lokal vorgefiltert · Gemini Vision")) {
  throw new Error("Hybrid Vision mode must be visible in the media UI");
}
if (!source.includes("Lokal vorgefiltert · KI kuratiert")) {
  throw new Error("Hybrid curation must be transparent in the UI");
}
if (!source.includes("lokal vorausgewählt")) {
  throw new Error("Planning galleries must identify local preselection");
}
if (!source.includes("media_vision_enabled")) {
  throw new Error("Vision controls must depend on explicit backend configuration");
}

console.log("Vision media UI contract tests passed.");
