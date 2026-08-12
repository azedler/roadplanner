/**
 * The three comparison mixes, through real ffmpeg.
 *
 * An A/B/C listening test answers nothing if the variants differ by
 * anything other than their architecture. Two ways that quietly happens,
 * and both are measured here rather than reasoned about:
 *
 * **One of them is louder.** A listener reliably prefers the loud one
 * and then reports it as the better arrangement. The whole experiment
 * would come back with a confident wrong answer, and nothing in the
 * finished files would show why.
 *
 * **The layers are summed at one level.** An atmosphere under a piece of
 * music is not the same thing as two pieces of music at once, and a mux
 * that ignored a layer's own volume would have produced the second while
 * calling it the first.
 *
 * Everything below runs against generated tones, so it costs nothing and
 * needs no provider. What it proves is the arithmetic of the mix, which
 * is the part that has to be right before any audio is paid for.
 */
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  MAX_GAIN_DB,
  analyseArgs,
  buildFilterGraph,
  gainForTarget,
  loudnessArgs,
  muxArgs,
  parseLoudness,
} from "../apps/roadplanner_renderer/src/audiomux.mjs";

const FFMPEG = process.env.ROADPLANNER_FFMPEG || "ffmpeg";

function run(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(FFMPEG, args, { stdio: ["ignore", "ignore", "pipe"] });
    let stderr = "";
    child.stderr.on("data", (chunk) => {
      stderr = `${stderr}${chunk}`.slice(-8000);
    });
    child.on("error", reject);
    child.on("close", (code) =>
      code === 0 ? resolve(stderr) : reject(new Error(`ffmpeg ${code}: ${stderr.slice(-500)}`)),
    );
  });
}

async function measure(file) {
  return parseLoudness(await run(loudnessArgs(file)));
}

async function main() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-mix-"));
  const seconds = 12;

  // A silent film, and two pieces of "music" that are plainly different:
  // a quiet low tone standing in for the atmosphere, a louder one for
  // the piece on top of it.
  const film = path.join(dir, "film.mp4");
  await run([
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", `testsrc=size=320x180:rate=30:duration=${seconds}`,
    "-c:v", "libx264", "-pix_fmt", "yuv420p", film,
  ]);
  const bed = path.join(dir, "bed.wav");
  const accent = path.join(dir, "accent.wav");
  await run([
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", `sine=frequency=110:duration=${seconds}`,
    "-af", "volume=0.9", bed,
  ]);
  await run([
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", `sine=frequency=440:duration=${seconds}`,
    "-af", "volume=0.9", accent,
  ]);

  const layer = (file, volume, role) => ({
    path: file,
    role,
    volume,
    startSeconds: 0,
    seconds,
    fadeInSeconds: 1.5,
    fadeOutSeconds: 2.5,
  });

  // The same gains the integration plans with, kept here as literals so
  // this test fails when the mix changes rather than following it.
  const VARIANTS = {
    A: [layer(accent, 0.42, "score")],
    B: [layer(bed, 0.42 * 0.38, "bed"), layer(accent, 0.42, "accent")],
    C: [layer(bed, 0.42 * 0.72, "bed")],
  };

  const TARGET = -20;
  const measured = {};
  const gains = {};
  for (const [name, sections] of Object.entries(VARIANTS)) {
    const output = path.join(dir, `film-${name}.mp4`);
    // Two passes, and the first one is the point: the mix is measured
    // as a mix, then moved by one static number. A single-pass
    // normaliser would ride the level while the music plays, and the
    // fassung most changed by that is the sparse one - so the
    // comparison would silently become "architecture versus
    // architecture plus an automatic level rider".
    const premix = parseLoudness(await run(analyseArgs({ sections })));
    gains[name] = gainForTarget(premix.integratedLufs, TARGET, {
      truePeakDbfs: premix.truePeakDbfs,
      ceilingDbtp: -1.5,
    });
    if (gains[name] === null) {
      throw new Error(`Variante ${name} wurde vor dem Mux nicht gemessen.`);
    }
    await run(
      muxArgs({
        video: film,
        sections,
        output,
        filmSeconds: seconds,
        gainDb: gains[name],
      }),
    );
    measured[name] = await measure(output);
  }

  // 1. Every fassung was actually measured. A null here is the meter
  //    having said nothing, and a report built on that would be quoting
  //    numbers nobody took.
  for (const [name, found] of Object.entries(measured)) {
    if (found.integratedLufs === null || found.truePeakDbfs === null) {
      throw new Error(`Variante ${name} wurde nicht gemessen: ${JSON.stringify(found)}`);
    }
  }

  // 2. They land at a comparable loudness, so the listening test is
  //    about the architecture and not about the volume knob.
  const levels = Object.values(measured).map((found) => found.integratedLufs);
  const spread = Math.max(...levels) - Math.min(...levels);
  if (spread > 1.5) {
    throw new Error(
      `Die Fassungen liegen ${spread.toFixed(1)} LU auseinander: ${JSON.stringify(measured)}`,
    );
  }
  for (const [name, found] of Object.entries(measured)) {
    if (Math.abs(found.integratedLufs - TARGET) > 2.0) {
      throw new Error(`Variante ${name} liegt bei ${found.integratedLufs} LUFS statt ${TARGET}`);
    }
  }

  // 3. Nothing clips. The ceiling is a true-peak one, so a sample-peak
  //    reading of exactly 0.0 would already be a file somebody hears
  //    crackle on a different decoder.
  for (const [name, found] of Object.entries(measured)) {
    if (found.truePeakDbfs > -0.5) {
      throw new Error(`Variante ${name} übersteuert: ${found.truePeakDbfs} dBFS`);
    }
  }

  // 4. The layered fassung really is layered: the bed's own level
  //    reaches the filter graph rather than being replaced by the
  //    shared one. Read from the graph, because a mix that sounded
  //    plausible while both layers played at one level is exactly the
  //    failure this checks for.
  const graph = buildFilterGraph(VARIANTS.B, { volume: 0.42, gainDb: gains.B });
  // Only the per-layer chains, found by their input label rather than
  // by matching "volume=" across the whole graph - the static level
  // correction is also a volume filter, and a check that counted it
  // would have reported three layers in a two-layer mix.
  const volumes = graph
    .split(";")
    .filter((stage) => /^\[\d+:a\]/.test(stage))
    .map((stage) => Number(/volume=([0-9.]+)/.exec(stage)?.[1]));
  if (volumes.length !== 2) {
    throw new Error(`Erwartet zwei Pegel, gefunden: ${volumes.join(", ")}`);
  }
  if (!(volumes[0] < volumes[1] / 2)) {
    throw new Error(`Das Klangbett liegt nicht unter dem Akzent: ${volumes.join(" / ")}`);
  }
  if (!/\[mixed\]volume=-?\d+\.\d\ddB\[levelled\]/.test(graph)) {
    throw new Error(`Die statische Pegelkorrektur fehlt im Filtergraphen: ${graph}`);
  }
  // Static, so it must appear exactly once and not as a per-moment
  // filter. A dynamic normaliser in this position is the failure this
  // whole two-pass arrangement exists to avoid.
  if (/loudnorm|dynaudnorm|speechnorm/.test(graph)) {
    throw new Error("Ein dynamischer Normalisierer gehört nicht in den Vergleich.");
  }
  if (!graph.includes("alimiter=")) {
    throw new Error("Der Begrenzer fehlt im Filtergraphen.");
  }

  // 5. And a soundtrack that asks for no loudness match is left alone -
  //    every film built before this comparison expects exactly that.
  const untouched = buildFilterGraph(VARIANTS.A, { volume: 0.42 });
  if (untouched.includes("[levelled]")) {
    throw new Error("Ohne Ziel darf keine Lautheit angeglichen werden.");
  }

  // 6. The preview fades are in the graph and are the QA excerpt's, not
  //    a decision about how the finished film's music begins.
  if (!untouched.includes("afade=t=in:st=0:d=1.500")) {
    throw new Error(`Kein Preview-Einblenden im Graphen: ${untouched}`);
  }
  if (!untouched.includes("afade=t=out")) {
    throw new Error("Kein Preview-Ausblenden im Graphen.");
  }

  // 7. And when the correction is not enough - a layer that decoded to
  //    near silence, a gain of zero - the shortfall is visible rather
  //    than swallowed. Three fassungen that quietly differ by two
  //    decibels would return a confident wrong answer, so the bound
  //    that produces that case has to be findable from the numbers.
  const bounded = gainForTarget(-60, TARGET, { truePeakDbfs: -55, ceilingDbtp: -1.5 });
  if (bounded !== MAX_GAIN_DB) {
    throw new Error(`Die Pegelkorrektur ist nicht begrenzt: ${bounded}`);
  }
  if (-60 + bounded > TARGET - 1.0) {
    throw new Error("Ein begrenzter Fall muss unter dem Ziel bleiben, sonst ist er nicht erkennbar.");
  }

  await fs.rm(dir, { recursive: true, force: true });
  const readable = Object.entries(measured)
    .map(
      ([name, found]) =>
        `${name} ${found.integratedLufs} LUFS / ${found.truePeakDbfs} dBTP ` +
        `(${gains[name] >= 0 ? "+" : ""}${gains[name].toFixed(1)} dB)`,
    )
    .join(" · ");
  console.log(`Music architecture mix tests passed (${readable}).`);
}

await main();
