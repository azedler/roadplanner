/**
 * The review copy against a real encoder.
 *
 * The arithmetic is checked next door without ffmpeg. This file exists
 * because the arithmetic is not the risk: the risk is that ffmpeg does
 * something other than what the arguments look like they say. That has
 * already happened twice in this project - `force_divisible_by` behaving
 * differently beside `force_original_aspect_ratio`, and rotation being
 * applied by ffmpeg itself while a filter was written to apply it again.
 * Both were found by measuring and would not have been found by reading.
 *
 * So a real film is encoded here, in four shapes, and every claim the
 * copy makes is read back off the produced file.
 *
 * Skipped, not failed, where ffmpeg is absent: this must be able to run
 * in a container that has none.
 */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import {
  reviewBitrate,
  reviewCopyArgs,
  reviewProfile,
} from "../apps/roadplanner_renderer/src/review_copy.mjs";

const execFileAsync = promisify(execFile);
const FFMPEG = process.env.ROADPLANNER_FFMPEG || "ffmpeg";
const FFPROBE = process.env.ROADPLANNER_FFPROBE || "ffprobe";

async function available() {
  try {
    await execFileAsync(FFMPEG, ["-version"]);
    await execFileAsync(FFPROBE, ["-version"]);
    return true;
  } catch {
    return false;
  }
}

/** What the file actually is, read back rather than assumed. */
async function probe(file) {
  const { stdout } = await execFileAsync(FFPROBE, [
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_format",
    "-show_streams",
    file,
  ]);
  const parsed = JSON.parse(stdout);
  const video = parsed.streams.find((s) => s.codec_type === "video");
  return {
    width: Number(video.width),
    height: Number(video.height),
    codec: String(video.codec_name),
    hasAudio: parsed.streams.some((s) => s.codec_type === "audio"),
    duration: Number(parsed.format.duration),
    bytes: Number(parsed.format.size),
  };
}

/** A source film: coloured motion, so the encoder has something to do. */
async function makeSource(file, { width, height, seconds, audio }) {
  const args = [
    "-nostdin",
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    `testsrc2=size=${width}x${height}:rate=30:duration=${seconds}`,
  ];
  if (audio) {
    args.push("-f", "lavfi", "-i", `sine=frequency=440:duration=${seconds}`);
  }
  args.push("-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p");
  if (audio) args.push("-c:a", "aac", "-shortest");
  args.push(file);
  await execFileAsync(FFMPEG, args);
}

async function copy(source, output, profileId) {
  const facts = await probe(source);
  const profile = reviewProfile(profileId);
  const bitrate = reviewBitrate({
    durationSeconds: facts.duration,
    hasAudio: facts.hasAudio,
  });
  await execFileAsync(
    FFMPEG,
    // The PRODUCTION argument builder, not a second one written here.
    // A test with its own copy of the command proves only that the copy
    // works, which is this project's fourth failure pattern.
    reviewCopyArgs({
      source,
      output,
      profile,
      bitrateBps: bitrate,
      hasAudio: facts.hasAudio,
    }),
  );
  return { source: facts, copy: await probe(output) };
}

async function main() {
  if (!(await available())) {
    console.log("Review copy encode tests skipped (kein ffmpeg).");
    return;
  }
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-review-"));
  let failed = 0;
  const check = (name, fn) => {
    try {
      fn();
    } catch (err) {
      failed += 1;
      console.error(`FEHLGESCHLAGEN: ${name}\n  ${err.message}`);
    }
  };
  try {
    // 1. The ordinary case: a 720p film with a soundtrack, copied to 480p.
    {
      const src = path.join(dir, "film.mp4");
      const out = path.join(dir, "film-480.mp4");
      await makeSource(src, { width: 1280, height: 720, seconds: 4, audio: true });
      const { source, copy: made } = await copy(src, out, "review_480");
      check("die Kopie ist kleiner", () => {
        assert.deepEqual([made.width, made.height], [854, 480]);
      });
      check("das Seitenverhältnis bleibt", () => {
        const before = source.width / source.height;
        const after = made.width / made.height;
        assert.ok(Math.abs(before - after) < 0.02, `${before} != ${after}`);
      });
      check("die Länge bleibt", () => {
        assert.ok(
          Math.abs(made.duration - source.duration) < 0.2,
          `${made.duration} != ${source.duration}`,
        );
      });
      check("die Tonspur überlebt", () => assert.equal(made.hasAudio, true));
      check("h264 kommt heraus", () => assert.equal(made.codec, "h264"));
    }

    // 2. A film without music must produce a copy without an audio stream -
    //    not a silent one. "Did the music arrive?" has to stay answerable
    //    from the file itself.
    {
      const src = path.join(dir, "stumm.mp4");
      const out = path.join(dir, "stumm-480.mp4");
      await makeSource(src, { width: 1280, height: 720, seconds: 3, audio: false });
      const { copy: made } = await copy(src, out, "review_480");
      check("kein Ton wird erfunden", () => assert.equal(made.hasAudio, false));
    }

    // 3. Never upscale. A 480p source asked for a 720p copy must stay 480p:
    //    a bigger file containing strictly less than the original is the
    //    opposite of what this feature is for.
    {
      const src = path.join(dir, "klein.mp4");
      const out = path.join(dir, "klein-720.mp4");
      await makeSource(src, { width: 854, height: 480, seconds: 3, audio: false });
      const { source, copy: made } = await copy(src, out, "review_720");
      check("aus klein wird nicht groß", () => {
        assert.ok(made.width <= source.width, `${made.width} > ${source.width}`);
        assert.ok(made.height <= source.height, `${made.height} > ${source.height}`);
      });
    }

    // 4. A portrait film. The shape ffmpeg has already refused once in
    //    this project, with "height not divisible by 2".
    {
      const src = path.join(dir, "hoch.mp4");
      const out = path.join(dir, "hoch-480.mp4");
      await makeSource(src, { width: 720, height: 1280, seconds: 3, audio: false });
      const { source, copy: made } = await copy(src, out, "review_480");
      check("hochkant übersteht die Kopie", () => {
        assert.equal(made.width % 2, 0, `Breite ${made.width}`);
        assert.equal(made.height % 2, 0, `Höhe ${made.height}`);
        const before = source.width / source.height;
        const after = made.width / made.height;
        assert.ok(Math.abs(before - after) < 0.03, `${before} != ${after}`);
        // The box is 854x480; a portrait film fits by its height.
        assert.ok(made.height <= 480, `Höhe ${made.height}`);
      });
    }

    // 5. An odd-sized source. Nothing in the pipeline promises even
    //    dimensions, and h264 cannot encode odd ones.
    {
      const src = path.join(dir, "krumm.mp4");
      const out = path.join(dir, "krumm-480.mp4");
      await makeSource(src, { width: 1278, height: 718, seconds: 2, audio: false });
      const { copy: made } = await copy(src, out, "review_480");
      check("krumme Maße werden gerade", () => {
        assert.equal(made.width % 2, 0, `Breite ${made.width}`);
        assert.equal(made.height % 2, 0, `Höhe ${made.height}`);
      });
    }
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
  if (failed) {
    console.error(`${failed} Prüfungen fehlgeschlagen.`);
    process.exit(1);
  }
  console.log("Review copy encode tests passed.");
}

await main();
