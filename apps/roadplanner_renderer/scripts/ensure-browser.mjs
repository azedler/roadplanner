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

/**
 * Where Remotion puts the download. Not `~/.cache` — it walks up from the
 * working directory to the nearest `package.json` and unpacks into
 * `node_modules/.remotion`. Guessing that path once cost a build; it is
 * only a fallback here, and the value `ensureBrowser()` returns wins.
 */
const CACHE_ROOTS = [
  path.resolve(process.cwd(), "node_modules/.remotion"),
  path.join(process.env.HOME || "/root", ".cache", "remotion"),
];

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

// The status carries the resolved executable path. Reading it beats
// reconstructing a cache layout that belongs to Remotion, not to us.
const status = await ensureBrowser({ logLevel: "info" });

let source = null;
if (status && typeof status.path === "string" && existsSync(status.path)) {
  source = status.path;
} else {
  for (const root of CACHE_ROOTS) {
    source = findShell(root);
    if (source) break;
  }
}

if (!source) {
  console.error(
    `Kein chrome-headless-shell gefunden. ensureBrowser meldete ${JSON.stringify(status)}; ` +
      `durchsucht wurden ${CACHE_ROOTS.join(", ")}.`,
  );
  process.exit(1);
}

// Copied to a fixed path rather than left in the build cache: the runtime
// stage must not depend on the builder's directory layout - and the next
// `npm ci` deletes node_modules, cache and all.
const shellRoot = path.dirname(source);
mkdirSync(TARGET, { recursive: true });
cpSync(shellRoot, TARGET, { recursive: true, dereference: true });

// Remotion names the binary `headless_shell` on some platforms. The
// runtime is told one fixed path, so the name is normalised here rather
// than leaving the Dockerfile to guess which one it got.
const binary = path.join(TARGET, "chrome-headless-shell");
const copied = path.join(TARGET, path.basename(source));
if (copied !== binary) cpSync(copied, binary, { dereference: true });
chmodSync(binary, 0o755);

process.stdout.write(
  `${JSON.stringify({
    event: "browser-ready",
    source,
    binary,
    size_bytes: statSync(binary).size,
  })}\n`,
);
