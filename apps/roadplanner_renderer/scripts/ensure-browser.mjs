/**
 * Fetch the headless shell ONCE, at image build time, and put it where the
 * runtime expects it.
 *
 * This is the one download in the whole app, and it happens while the image
 * is being built — never while a job runs, and never on a user's machine.
 * At runtime `REMOTION_SKIP_BROWSER_DOWNLOAD=1` and an explicit
 * `browserExecutable` make sure nothing is fetched even if the binary were
 * missing: a missing browser is a reported failure, not a download.
 *
 * `chrome-headless-shell` rather than a full Chromium: it is the same
 * rendering engine without the browser UI, the extension host, the sync
 * stack and everything else a renderer never touches. That difference is
 * most of the image size.
 */
import { ensureBrowser } from "@remotion/renderer";
import { chmodSync, cpSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const TARGET = process.env.ROADPLANNER_BROWSER_DIR || "/opt/roadplanner-renderer/browser";

/** Find the shell binary underneath a directory, wherever it was unpacked. */
function findShell(root) {
  if (!existsSync(root)) return null;
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name === "chrome-headless-shell" || entry.name === "headless_shell") {
        return full;
      }
    }
  }
  return null;
}

await ensureBrowser({ logLevel: "info" });

const cache = path.join(
  process.env.HOME || "/root",
  ".cache",
  "remotion",
  "chrome-headless-shell",
);
const source = findShell(cache);
if (!source) {
  console.error(`Kein chrome-headless-shell unter ${cache} gefunden.`);
  process.exit(1);
}

// Copied to a fixed path rather than left in a cache under $HOME: the
// runtime stage must not depend on which user or home directory the
// container happens to run with.
const shellRoot = path.dirname(source);
mkdirSync(TARGET, { recursive: true });
cpSync(shellRoot, TARGET, { recursive: true, dereference: true });
const binary = path.join(TARGET, path.basename(source));
chmodSync(binary, 0o755);

process.stdout.write(
  `${JSON.stringify({
    event: "browser-ready",
    binary,
    size_bytes: statSync(binary).size,
  })}\n`,
);
