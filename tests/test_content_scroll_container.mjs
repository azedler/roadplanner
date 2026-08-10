/**
 * `.content` has to be a scroll container even when nothing above it has
 * a resolved height - which is the case that broke every way of scrolling
 * except dragging the scrollbar.
 *
 * Reproduced in a real headless Chromium before this fix: with a host
 * chain that resolves to `auto`, `height: 100%` on :host resolves to auto
 * too, `.app` grows to its full 4486px of content, the grid's
 * `minmax(0, 1fr)` row grows with it, and `.content` ends up an
 * overflow:auto box with NOTHING to scroll (`scrollHeight === clientHeight`).
 * The wheel then finds no scroll target inside the panel and the keyboard
 * finds none either, while the one scrollbar on screen belongs to an
 * ancestor - so dragging it worked and nothing else did. That is the
 * reported symptom exactly, and it is why `min-height: 0` (4.82.0) and the
 * focus fix (4.83.0) could not help: both presuppose a definite height
 * that never arrived.
 *
 * So the height is measured against the viewport instead of inherited
 * (`--rp-app-height`, set in roadplanner-panel.js's `_syncAppHeight`).
 * This test asserts the two halves of that contract without a browser:
 * the panel publishes a measured height, and the stylesheet consumes it
 * rather than depending on the ancestor chain.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const panelSource = readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/roadplanner-panel.js", import.meta.url),
  "utf8",
);
const stylesSource = readFileSync(
  new URL("../custom_components/roadplanner_mcp/frontend/lib/styles.js", import.meta.url),
  "utf8",
);

// --- the stylesheet must not depend on the ancestor chain for .app -------

const appRule = stylesSource
  .split("\n")
  .find((line) => line.trim().startsWith(".app {"));
assert.ok(appRule, ".app rule not found");
assert.match(
  appRule,
  /var\(--rp-app-height/,
  ".app must take the MEASURED height; height:100% resolves to auto whenever " +
    "the host chain has no resolved height, and then .content never overflows",
);
assert.match(appRule, /minmax\(0, 1fr\)/, "the content row must still be allowed to shrink");
assert.match(appRule, /100vh/, "a plain-vh fallback for engines without dvh");

// --- the panel must publish that measurement, and keep it current --------

assert.match(panelSource, /_syncAppHeight\(\)/, "the measurement helper must exist");
assert.match(
  panelSource,
  /setProperty\(\s*["']--rp-app-height["']/,
  "the measured height must reach CSS as --rp-app-height",
);
assert.match(
  panelSource,
  /addEventListener\("resize", this\._viewportListener\)/,
  "a rotated phone or an opened sidebar moves where the panel starts",
);
assert.match(
  panelSource,
  /removeEventListener\("resize", this\._viewportListener\)/,
  "the listener must not outlive the element",
);
// Measured from the DOCUMENT, not the raw viewport rect: while the page is
// still scrolled (which is precisely the broken state this repairs), a raw
// getBoundingClientRect().top is negative and would shrink the panel further
// on every wheel tick.
assert.match(
  panelSource,
  /getBoundingClientRect\(\)\.top \+ \(window\.scrollY \|\| 0\)/,
  "the offset must be document-relative so a scrolled page cannot feed back",
);

console.log("Content scroll container tests passed.");
