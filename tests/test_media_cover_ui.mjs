import fs from "node:fs";

const source =
  fs.readFileSync("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", "utf8") +
  fs.readFileSync("custom_components/roadplanner_mcp/frontend/features/media.js", "utf8") +
  fs.readFileSync("custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js", "utf8");

if (!source.includes("_tripCoverImage()")) throw new Error("Trip hero must use the central trip-cover resolver");
if (source.includes("(nextDay && this._dayCoverImage(nextDay)) || this._tripImages(1)[0]")) {
  throw new Error("Trip hero must not fall back to the first arbitrary trip photo");
}
for (const field of ["is_cover", "is_day_cover", "is_trip_cover"]) {
  if (!source.includes(`name=\"${field}\"`)) throw new Error(`Missing manual cover field ${field}`);
}
// Own photos render regardless of external-gallery failures; per explicit
// user feedback the "Eigene Bilder vorhanden" notice itself is suppressed
// when own photos exist (see test_memories_paging_filters.mjs).
if (!source.includes('if (hasOwnImages) return "";')) {
  throw new Error("With own photos present, no external-image notice may be shown");
}
if (!source.includes('data-action="delete-stop"')) throw new Error("Manual stop deletion must be visible");
if (!source.includes("uses_access_point")) throw new Error("Derived navigation access must be visible");
console.log("Media cover and destination UI tests passed.");
