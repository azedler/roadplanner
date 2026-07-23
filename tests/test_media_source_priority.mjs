import fs from "node:fs";

const source = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/roadplanner-panel.js",
  "utf8",
);

const stopCardStart = source.indexOf("_renderStopCard(day, stop, index)");
const stopCardEnd = source.indexOf("_renderTotalRoute()", stopCardStart);
if (stopCardStart < 0 || stopCardEnd < 0) {
  throw new Error("Stop card renderer not found");
}
const stopCard = source.slice(stopCardStart, stopCardEnd);
const ownIndex = stopCard.indexOf("experienceCover ?");
const planningIndex = stopCard.indexOf("destinationImages.length ?");
if (ownIndex < 0 || planningIndex < 0 || ownIndex > planningIndex) {
  throw new Error("Own travel photos must be preferred before planning images");
}
if (!stopCard.includes("allExperienceMedia.length")) {
  throw new Error("Full own-photo album must remain accessible");
}

for (const token of [
  'display_source_by_stop',
  'display_source_by_day',
  'smart_local_metadata',
]) {
  const mediaSource = fs.readFileSync(
    "custom_components/roadplanner_mcp/media_intelligence.py",
    "utf8",
  );
  if (!mediaSource.includes(token)) {
    throw new Error(`Missing smart media presentation contract: ${token}`);
  }
}

console.log("Media source priority tests passed.");
