import fs from "node:fs";

// Live report: after the Reisebegleiter files a change, the Änderungskorb sat
// at the very bottom - the user had to scroll past the whole chat history.
// A compact quick bar with the basket count, the same "Änderungen prüfen"
// action and a jump-to-details button now sits directly under the composer.
const assistant = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/features/assistant.js",
  "utf8",
);
const panel = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/roadplanner-panel.js",
  "utf8",
);
const styles = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/lib/styles.js",
  "utf8",
);

for (const required of [
  'class="assistant-basket-quickbar panel-card"',
  "Änderungskorb: ${basket.length} vorgemerkt",
  'data-action="assistant-scroll-basket"',
]) {
  if (!assistant.includes(required)) {
    throw new Error(`Missing basket quick bar contract: ${required}`);
  }
}
// The quick bar only appears with a non-empty basket and renders ABOVE the
// chat layout, i.e. before the thread markup in the template.
if (assistant.indexOf("assistant-basket-quickbar") > assistant.indexOf('<section class="assistant-layout">')) {
  throw new Error("The quick bar must render above the chat layout");
}
if (!assistant.includes("basketEnabled && basket.length ? `<section class=\"assistant-basket-quickbar")) {
  throw new Error("The quick bar must be gated on a non-empty enabled basket");
}
if (!panel.includes('action === "assistant-scroll-basket"') || !panel.includes("scrollIntoView")) {
  throw new Error("The jump-to-basket action is not wired in the dispatcher");
}
if (!styles.includes(".assistant-basket-quickbar")) {
  throw new Error("Quick bar styling is missing");
}

console.log("Assistant basket quick bar tests passed.");
