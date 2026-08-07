/**
 * The app's own reading of the render package.
 *
 * Roadplanner validates a package before writing it; the app validates
 * before reading it. The two implementations share no code on purpose, so
 * this file checks the app's half against the shapes the Python builder
 * produces — a disagreement has to surface here rather than as a render
 * that quietly does the wrong thing with real travel data.
 *
 * Only `protocol.mjs` is imported, which has no dependencies. The parts
 * that need Remotion installed are tested by the renderer app's own
 * workflow, where the dependencies exist.
 */
import assert from "node:assert/strict";

import {
  MAX_PACKAGE_IMAGES,
  MAX_PACKAGE_IMAGE_BYTES,
  MAX_PACKAGE_STOPS,
  packageImageFilename,
  parsePackage,
} from "../apps/roadplanner_renderer/src/protocol.mjs";

const HASH = "a".repeat(64);

const packageJson = (overrides = {}) =>
  JSON.stringify({
    package_version: 1,
    job_id: "11111111-2222-3333-4444-555555555555",
    trip_title: "Finnland 2026",
    day: {
      day_id: "day-7",
      date: "2026-08-05",
      title: "Von Tampere nach Vaasa",
      summary: "Ein langer Fahrtag am See entlang.",
      number: 7,
      count: 21,
      distance_km: 214.6,
      duration_minutes: 195,
    },
    stops: [
      { name: "Tampere", time: "09:00" },
      { name: "Vaasa", time: "" },
    ],
    images: [{ index: 1, size_bytes: 1234, sha256: HASH }],
    total_image_bytes: 1234,
    ...overrides,
  });

function verifyAWellFormedPackageIsRead() {
  const parsed = parsePackage(packageJson());
  assert.equal(parsed.day.dayId, "day-7");
  assert.equal(parsed.day.distanceKm, 214.6);
  assert.equal(parsed.day.durationMinutes, 195);
  assert.equal(parsed.day.number, 7);
  assert.deepEqual(
    parsed.stops.map((stop) => stop.name),
    ["Tampere", "Vaasa"],
  );
  assert.deepEqual(parsed.images, [
    { index: 1, filename: "photo-1.jpg", sizeBytes: 1234, sha256: HASH },
  ]);
}

function verifyTheFilenameIsBuiltFromANumberOnly() {
  assert.equal(packageImageFilename(1), "photo-1.jpg");
  assert.equal(packageImageFilename(MAX_PACKAGE_IMAGES), `photo-${MAX_PACKAGE_IMAGES}.jpg`);
  for (const bad of [
    0,
    -1,
    1.5,
    MAX_PACKAGE_IMAGES + 1,
    "1",
    "../../etc/passwd",
    "1; rm -rf /",
    null,
    undefined,
    NaN,
  ]) {
    assert.throws(() => packageImageFilename(bad), /Bildindex/, `angenommen: ${String(bad)}`);
  }
}

function verifyAPackageCannotNameAFile() {
  // Even if a writer puts a filename in, it is never read back out - the
  // name the app uses comes from the index alone.
  const parsed = parsePackage(
    packageJson({
      images: [
        {
          index: 1,
          size_bytes: 10,
          sha256: HASH,
          filename: "../../../etc/shadow",
          path: "/etc/shadow",
        },
      ],
    }),
  );
  assert.equal(parsed.images[0].filename, "photo-1.jpg");
  assert.equal(JSON.stringify(parsed).includes("shadow"), false);
}

function verifyBrokenPackagesAreRefused() {
  const cases = [
    ["kein JSON", "{nicht wirklich"],
    ["kein Objekt", "[1,2,3]"],
    ["falsche Version", packageJson({ package_version: 2 })],
    ["ohne Tag", packageJson({ day: {} })],
    ["Tag ist kein Objekt", packageJson({ day: "Mittwoch" })],
    ["ohne Bilder", packageJson({ images: [] })],
    ["Bilder keine Liste", packageJson({ images: "photo-1.jpg" })],
    [
      "zu viele Bilder",
      packageJson({
        images: Array.from({ length: MAX_PACKAGE_IMAGES + 1 }, (_unused, i) => ({
          index: i + 1,
          size_bytes: 10,
          sha256: HASH,
        })),
      }),
    ],
    ["doppelter Index", packageJson({
      images: [
        { index: 1, size_bytes: 10, sha256: HASH },
        { index: 1, size_bytes: 10, sha256: "b".repeat(64) },
      ],
    })],
    ["ungültiger Hash", packageJson({ images: [{ index: 1, size_bytes: 10, sha256: "kurz" }] })],
    ["Größe null", packageJson({ images: [{ index: 1, size_bytes: 0, sha256: HASH }] })],
    [
      "Bild zu groß",
      packageJson({
        images: [{ index: 1, size_bytes: MAX_PACKAGE_IMAGE_BYTES + 1, sha256: HASH }],
      }),
    ],
    [
      "zu viele Stopps",
      packageJson({
        stops: Array.from({ length: MAX_PACKAGE_STOPS + 1 }, () => ({ name: "Stopp" })),
      }),
    ],
  ];
  for (const [label, raw] of cases) {
    assert.throws(() => parsePackage(raw), /./, `angenommen: ${label}`);
  }
}

function verifyMissingOptionalValuesStayMissing() {
  // A day without a distance must show no distance, never a zero - a
  // placeholder reads as data, which is the mistake the PDF made.
  const parsed = parsePackage(
    packageJson({
      day: { day_id: "day-1", date: "", title: "", summary: "", number: null, count: null },
      stops: [],
    }),
  );
  assert.equal(parsed.day.distanceKm, null);
  assert.equal(parsed.day.durationMinutes, null);
  assert.equal(parsed.day.number, null);
  assert.deepEqual(parsed.stops, []);
}

function verifyStopsAreBoundedAndCleaned() {
  const parsed = parsePackage(
    packageJson({
      stops: [
        { name: "  Vaasa  ", time: "10:00" },
        { name: "" },
        { name: "N".repeat(200), time: "x".repeat(50) },
        "kein Objekt",
        null,
      ],
    }),
  );
  assert.equal(parsed.stops[0].name, "Vaasa");
  assert.equal(parsed.stops.length, 2);
  assert.equal(parsed.stops[1].name.length, 80);
  assert.equal(parsed.stops[1].time.length, 10);
}

verifyAWellFormedPackageIsRead();
verifyTheFilenameIsBuiltFromANumberOnly();
verifyAPackageCannotNameAFile();
verifyBrokenPackagesAreRefused();
verifyMissingOptionalValuesStayMissing();
verifyStopsAreBoundedAndCleaned();
console.log("Renderer app trip day package tests passed.");
