import { selectComposition, renderMedia } from "@remotion/renderer";
import fs from "node:fs/promises";
const SERVE = process.env.SERVE;
const inputProps = JSON.parse(await fs.readFile(process.env.PROPS, "utf8"));
const browserExecutable = "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell";
const composition = await selectComposition({ serveUrl: SERVE, id: "roadplanner-trip-film", inputProps, browserExecutable });
console.log("frames", composition.durationInFrames, "seconds", composition.durationInFrames / 30);
let last = -1;
await renderMedia({
  composition, serveUrl: SERVE, codec: "h264", outputLocation: process.env.OUT,
  inputProps, browserExecutable, concurrency: 4,
  frameRange: process.env.RANGE ? process.env.RANGE.split("-").map(Number) : undefined,
  chromiumOptions: { gl: "swangle" },
  onProgress: ({ progress }) => {
    const pct = Math.floor(progress * 20) * 5;
    if (pct !== last) { last = pct; console.log("progress", pct); }
  },
});
console.log("done");
