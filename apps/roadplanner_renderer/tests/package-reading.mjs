/**
 * What the renderer refuses to render.
 *
 * The trip-day job reads real photos out of a directory that every app
 * with a `/share` mount can write to. The checks that matter therefore all
 * happen *before* a browser is started, and that is exactly what this file
 * pins: every case below must fail with a package error, never with a
 * render or browser error. A `BROWSER_MISSING` here would mean the bytes
 * had already been accepted.
 *
 * It lives beside the app rather than in the repository's test folder
 * because it imports the render module, which needs the app's
 * dependencies installed. The repository suite must keep running without
 * them.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { renderTripDayVideo, renderTripFilmVideo } from "../src/render.mjs";
import {
  MAX_PACKAGE_IMAGE_BYTES,
  filmPhotoPath,
  parseFilmPackage,
} from "../src/protocol.mjs";

const PHOTO = Buffer.from("nicht wirklich ein JPEG, aber Bytes sind Bytes");

async function makeInputs(mutate) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-inputs-"));
  const pkg = {
    package_version: 1,
    job_id: "11111111-2222-3333-4444-555555555555",
    trip_title: "Finnland 2026",
    day: {
      day_id: "day-7",
      date: "2026-08-05",
      title: "Von Tampere nach Vaasa",
      summary: "",
      number: 7,
      count: 21,
      distance_km: 214.6,
      duration_minutes: 195,
    },
    stops: [{ name: "Vaasa", time: "" }],
    images: [
      {
        index: 1,
        size_bytes: PHOTO.length,
        sha256: createHash("sha256").update(PHOTO).digest("hex"),
      },
    ],
  };
  await fs.writeFile(path.join(root, "photo-1.jpg"), PHOTO);
  await fs.writeFile(path.join(root, "package.json"), JSON.stringify(pkg));
  await mutate?.(root, pkg);
  return root;
}

/** Render and return the error code, never letting a success pass. */
async function codeFor(inputsDir) {
  const output = path.join(inputsDir, "out.mp4");
  try {
    await renderTripDayVideo({ outputPath: output, inputsDir });
  } catch (err) {
    return err?.code ?? "OHNE_CODE";
  }
  throw new Error("Der Render hätte abgelehnt werden müssen");
}

async function verifyAMissingPackageIsRefused() {
  const root = await makeInputs(async (dir) => {
    await fs.rm(path.join(dir, "package.json"));
  });
  assert.equal(await codeFor(root), "PACKAGE_MISSING");
}

async function verifyAMissingImageIsRefused() {
  const root = await makeInputs(async (dir) => {
    await fs.rm(path.join(dir, "photo-1.jpg"));
  });
  assert.equal(await codeFor(root), "PACKAGE_MISSING");
}

async function verifyAChangedSizeIsRefused() {
  const root = await makeInputs(async (dir) => {
    await fs.writeFile(path.join(dir, "photo-1.jpg"), Buffer.concat([PHOTO, PHOTO]));
  });
  assert.equal(await codeFor(root), "PACKAGE_INVALID");
}

async function verifyChangedBytesAreRefused() {
  // Same length, different content: only the hash catches this, which is
  // the whole reason the package carries one.
  const swapped = Buffer.from(PHOTO);
  swapped[0] ^= 0xff;
  const root = await makeInputs(async (dir) => {
    await fs.writeFile(path.join(dir, "photo-1.jpg"), swapped);
  });
  assert.equal(await codeFor(root), "PACKAGE_INVALID");
}

async function verifyASymlinkIsRefusedRatherThanFollowed() {
  // The one way a file in a shared directory could reach something
  // outside it. `lstat` is what makes this detectable at all.
  const root = await makeInputs(async (dir) => {
    await fs.rm(path.join(dir, "photo-1.jpg"));
    await fs.symlink("/etc/hostname", path.join(dir, "photo-1.jpg"));
  });
  assert.equal(await codeFor(root), "PACKAGE_INVALID");
}

async function verifyAnOversizedImageIsRefusedBeforeItIsRead() {
  const root = await makeInputs(async (dir) => {
    await fs.writeFile(
      path.join(dir, "photo-1.jpg"),
      Buffer.alloc(MAX_PACKAGE_IMAGE_BYTES + 1024),
    );
  });
  assert.equal(await codeFor(root), "PACKAGE_INVALID");
}

async function verifyABrokenManifestIsRefused() {
  const root = await makeInputs(async (dir) => {
    await fs.writeFile(path.join(dir, "package.json"), "{ kein JSON");
  });
  assert.equal(await codeFor(root), "INVALID_JOB");
}

// --- the whole-trip film -------------------------------------------------

const FILM_PHOTO = Buffer.from("auch kein echtes JPEG, aber Bytes");

async function makeFilmInputs(mutate) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "roadplanner-film-inputs-"));
  await fs.mkdir(path.join(root, "photos"), { recursive: true });
  await fs.writeFile(path.join(root, "photos", "c00-1.jpg"), FILM_PHOTO);
  const pkg = {
    film_package_version: 1,
    job_id: "11111111-2222-3333-4444-555555555555",
    manifest_content_hash: "a".repeat(64),
    trip: {
      title: "Finnland 2026",
      start_date: "2026-07-17",
      end_date: "2026-08-06",
      chapter_count: 1,
      distance_km: 2140.5,
      photo_count: 812,
    },
    chapters: [
      {
        chapter_id: "day-1",
        index: 0,
        date: "2026-07-17",
        title: "Etappe 1",
        story: "Am ersten Tag ging es los.",
        story_source: "composed",
        day_number: 1,
        distance_km: 100,
        duration_minutes: 90,
        stop_count: 2,
        photo_count: 40,
        stops: ["Tampere", "Vaasa"],
        images: [
          {
            path: "photos/c00-1.jpg",
            size_bytes: FILM_PHOTO.length,
            sha256: createHash("sha256").update(FILM_PHOTO).digest("hex"),
          },
        ],
      },
    ],
  };
  await fs.writeFile(path.join(root, "film.json"), JSON.stringify(pkg));
  await mutate?.(root, pkg);
  return root;
}

async function filmCodeFor(inputsDir) {
  const output = path.join(inputsDir, "out.mp4");
  try {
    await renderTripFilmVideo({ outputPath: output, inputsDir });
  } catch (err) {
    return err?.code ?? "OHNE_CODE";
  }
  throw new Error("Der Film hätte abgelehnt werden müssen");
}

function verifyAFilmPhotoPathIsBuiltFromNumbers() {
  assert.equal(filmPhotoPath("photos/c00-1.jpg", 0, 1), "photos/c00-1.jpg");
  for (const bad of [
    "photos/../../etc/passwd",
    "/etc/passwd",
    "photos/c0-1.jpg",
    "photos/c00-4.jpg",
    "photos/c00-1.png",
    "",
    null,
  ]) {
    assert.throws(() => filmPhotoPath(bad, 0, 1), /./, `angenommen: ${String(bad)}`);
  }
  // A path that is well-formed but belongs to another chapter is refused,
  // which is what stops one chapter's picture appearing in another.
  assert.throws(() => filmPhotoPath("photos/c01-1.jpg", 0, 1), /Kapitelposition/);
}

function verifyAFilmPackageIsCheckedBeforeItIsUsed() {
  const base = {
    film_package_version: 1,
    trip: {},
    chapters: [{ chapter_id: "day-1", index: 0, images: [] }],
  };
  assert.equal(parseFilmPackage(JSON.stringify(base)).chapters.length, 1);
  for (const [label, payload] of [
    ["falsche Version", { ...base, film_package_version: 2 }],
    ["ohne Kapitel", { ...base, chapters: [] }],
    ["Kapitel verschoben", { ...base, chapters: [{ chapter_id: "day-1", index: 3, images: [] }] }],
    [
      "zu viele Bilder",
      {
        ...base,
        chapters: [
          {
            chapter_id: "day-1",
            index: 0,
            images: Array.from({ length: 4 }, () => ({
              path: "photos/c00-1.jpg",
              size_bytes: 10,
              sha256: "a".repeat(64),
            })),
          },
        ],
      },
    ],
  ]) {
    assert.throws(() => parseFilmPackage(JSON.stringify(payload)), /./, label);
  }
}

async function verifyAFilmWithChangedBytesIsRefused() {
  const swapped = Buffer.from(FILM_PHOTO);
  swapped[0] ^= 0xff;
  const root = await makeFilmInputs(async (dir) => {
    await fs.writeFile(path.join(dir, "photos", "c00-1.jpg"), swapped);
  });
  assert.equal(await filmCodeFor(root), "PACKAGE_INVALID");
}

async function verifyAFilmWithAMissingPhotoIsRefused() {
  const root = await makeFilmInputs(async (dir) => {
    await fs.rm(path.join(dir, "photos", "c00-1.jpg"));
  });
  assert.equal(await filmCodeFor(root), "PACKAGE_MISSING");
}

async function verifyAFilmWithoutItsManifestIsRefused() {
  const root = await makeFilmInputs(async (dir) => {
    await fs.rm(path.join(dir, "film.json"));
  });
  assert.equal(await filmCodeFor(root), "PACKAGE_MISSING");
}

await verifyAMissingPackageIsRefused();
await verifyAMissingImageIsRefused();
await verifyAChangedSizeIsRefused();
await verifyChangedBytesAreRefused();
await verifyASymlinkIsRefusedRatherThanFollowed();
await verifyAnOversizedImageIsRefusedBeforeItIsRead();
await verifyABrokenManifestIsRefused();
verifyAFilmPhotoPathIsBuiltFromNumbers();
verifyAFilmPackageIsCheckedBeforeItIsUsed();
await verifyAFilmWithChangedBytesIsRefused();
await verifyAFilmWithAMissingPhotoIsRefused();
await verifyAFilmWithoutItsManifestIsRefused();
console.log("Renderer app package reading tests passed.");
