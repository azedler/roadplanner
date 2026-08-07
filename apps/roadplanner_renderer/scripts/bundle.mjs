/**
 * Bundle the composition ONCE, at image build time.
 *
 * Remotion's `bundle()` runs esbuild over the composition. Doing that on
 * every render would burn CPU on a user's machine for a result that never
 * changes between renders of the same image - and, more importantly, it is
 * work that belongs in the build, where it can fail loudly in CI rather
 * than quietly on someone's Home Assistant.
 *
 * The output directory is baked into the image and read-only at runtime.
 */
import { bundle } from "@remotion/bundler";
import path from "node:path";
import process from "node:process";

const OUT_DIR = process.env.ROADPLANNER_BUNDLE_DIR || "/opt/roadplanner-renderer/bundle";
const ENTRY = path.resolve("src/remotion/index.ts");

const started = Date.now();
const location = await bundle({
  entryPoint: ENTRY,
  outDir: OUT_DIR,
  // No public directory: the composition must not be able to reference
  // assets that would have to travel with a job.
  publicDir: null,
  onProgress: () => undefined,
});
process.stdout.write(
  `${JSON.stringify({
    event: "bundled",
    location,
    seconds: Math.round((Date.now() - started) / 100) / 10,
  })}\n`,
);
