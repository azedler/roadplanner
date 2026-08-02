import fs from "node:fs";

// Live request: plain Enter in the Reisebegleiter textarea kept SENDING
// half-typed messages. Enter now inserts a newline (browser default);
// sending is the button or Ctrl/Cmd+Enter.
const panel = fs.readFileSync(
  "custom_components/roadplanner_mcp/frontend/roadplanner-panel.js",
  "utf8",
);

if (!panel.includes('event.key === "Enter" && (event.ctrlKey || event.metaKey)')) {
  throw new Error("Sending must require Ctrl/Cmd+Enter in the chat textarea");
}
const enterHandler = panel.split('textarea[name=\'message\']"');
if (/event\.key === "Enter" && !event\.shiftKey && !event\.isComposing/.test(panel)) {
  throw new Error("Plain Enter must no longer submit the chat composer");
}

console.log("Assistant composer Enter behavior tests passed.");
