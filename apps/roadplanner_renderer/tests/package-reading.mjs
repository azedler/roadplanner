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

import { renderTripDayVideo } from "../src/render.mjs";
import { MAX_PACKAGE_IMAGE_BYTES } from "../src/protocol.mjs";

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

await verifyAMissingPackageIsRefused();
await verifyAMissingImageIsRefused();
await verifyAChangedSizeIsRefused();
await verifyChangedBytesAreRefused();
await verifyASymlinkIsRefusedRatherThanFollowed();
await verifyAnOversizedImageIsRefusedBeforeItIsRead();
await verifyABrokenManifestIsRefused();
console.log("Renderer app package reading tests passed.");
