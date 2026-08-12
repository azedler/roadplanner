/**
 * The soundtrack against a real encoder.
 *
 * The filter graph is checked next door without ffmpeg, and that check
 * is worth having - but it only proves the string is the string that was
 * intended. It cannot prove ffmpeg accepts it. A graph with one label
 * out of place fails at run time with "Invalid argument" and nothing
 * before that moment says so, which for this job means: after a
 * ninety-minute render, on the step that was supposed to be the cheap
 * one.
 *
 * So a real film is muxed here and every claim is read back off the
 * produced file: the pictures are byte-identical because they were
 * copied, there is exactly one audio track, the film keeps its own
 * length, and the sum of two overlapping sections does not clip.
 *
 * Skipped, not failed, where ffmpeg is absent.
 */
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";

import { muxArgs, sectionsEnd } from "../apps/roadplanner_renderer/src/audiomux.mjs";

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
  const audio = parsed.streams.filter((s) => s.codec_type === "audio");
  return {
    width: Number(video.width),
    height: Number(video.height),
    videoCodec: video.codec_name,
    audioTracks: audio.length,
    audioCodec: audio[0]?.codec_name ?? "",
    seconds: Number(parsed.format.duration),
  };
}

/** The loudest sample in the file, in dBFS. Zero would be clipping. */
async function peakDb(file) {
  const { stderr } = await execFileAsync(FFMPEG, [
    "-hide_banner",
    "-i",
    file,
    "-af",
    "volumedetect",
    "-f",
    "null",
    "-",
  ]).catch((err) => ({ stderr: String(err.stderr ?? "") }));
  const found = /max_volume:\s*(-?[\d.]+) dB/.exec(stderr);
  assert.ok(found, "volumedetect hat keinen Pegel gemeldet");
  return Number(found[1]);
}

async function main() {
  if (!(await available())) {
    console.log("Audio mux encode tests skipped (no ffmpeg).");
    return;
  }
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-mux-"));
  try {
    const film = path.join(dir, "film.mp4");
    const output = path.join(dir, "film-music.mp4");
    const FILM_SECONDS = 20;
    // A silent film, which is what a render produces: a soundtrack is
    // never mixed in while the frames are drawn any more.
    await execFileAsync(FFMPEG, [
      "-hide_banner", "-loglevel", "error", "-y",
      "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
      "-t", String(FILM_SECONDS),
      "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
      "-pix_fmt", "yuv420p", film,
    ]);
    const before = await probe(film);
    assert.equal(before.audioTracks, 0, "der stumme Film hat schon Ton");

    // Two sections that OVERLAP, because the overlap is the crossfade and
    // it is the only place two tracks add up. `normalize=0` is deliberate
    // there, so this is exactly where clipping would appear.
    const sections = [
      {
        path: path.join(dir, "one.mp3"),
        startSeconds: 0,
        seconds: 12,
        fadeInSeconds: 2,
        fadeOutSeconds: 2,
      },
      {
        path: path.join(dir, "two.mp3"),
        startSeconds: 10,
        seconds: 10,
        fadeInSeconds: 2,
        fadeOutSeconds: 3,
      },
    ];
    for (const [index, section] of sections.entries()) {
      await execFileAsync(FFMPEG, [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", `sine=frequency=${440 + index * 220}:duration=14`,
        "-c:a", "libmp3lame", section.path,
      ]);
    }
    // The score reaches past the film on purpose: a generated track is
    // longer than the section it was ordered for, and `atrim` is what
    // keeps it from playing over its successor.
    assert.ok(sectionsEnd(sections) >= FILM_SECONDS, "die Abschnitte decken den Film nicht");

    await execFileAsync(FFMPEG, muxArgs({
      video: film,
      sections,
      output,
      filmSeconds: FILM_SECONDS,
    }));

    const after = await probe(output);
    // Copied, not re-encoded: same size, same codec. Anything else is a
    // second generation of compression nobody asked for.
    assert.equal(after.width, before.width);
    assert.equal(after.height, before.height);
    assert.equal(after.videoCodec, before.videoCodec);
    // Exactly one. Two would be inaudible rather than obviously wrong.
    assert.equal(after.audioTracks, 1, `${after.audioTracks} Tonspuren`);
    assert.equal(after.audioCodec, "aac");
    // The film is the master. `-shortest` is not used, so a score that
    // came out short must not have truncated the pictures.
    assert.ok(
      Math.abs(after.seconds - FILM_SECONDS) < 0.5,
      `Film ${after.seconds} s statt ${FILM_SECONDS} s`,
    );

    // Where the two sections overlap they are summed without
    // normalising, and the limiter is the net under that. A file that
    // reached 0 dBFS would crackle at exactly the handover.
    const peak = await peakDb(output);
    assert.ok(peak < -0.5, `Pegel ${peak} dBFS - das übersteuert am Übergang`);

    console.log(
      `Audio mux encode tests passed (${after.seconds.toFixed(1)} s, `
      + `${after.audioTracks} Tonspur, Spitze ${peak} dBFS).`,
    );
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
}

await main();
