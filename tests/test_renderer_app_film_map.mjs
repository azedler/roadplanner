/**
 * The app's own reading of the map, checked against the shapes Python
 * writes.
 *
 * The map context is the first thing in a package whose numbers reach a
 * projection, and a projection handed a NaN does not fail - it silently
 * draws nothing. A blank map with a successful exit code is the worst
 * outcome available here, so every coordinate has to be refused before it
 * gets that far.
 *
 * Only `protocol.mjs` is imported. It has no dependencies, so these run in
 * the normal release suite where Remotion is not installed.
 */
import assert from "node:assert/strict";

import {
  FILM_SCENE_TYPES,
  MAP_CONTEXT_VERSION,
  parseFilmPackage,
  parseMapContext,
} from "../apps/roadplanner_renderer/src/protocol.mjs";

const HASH = "a".repeat(64);

const mapContext = (overrides = {}) => ({
  map_context_version: MAP_CONTEXT_VERSION,
  bbox: [10, 54, 13, 56],
  start: [10, 54],
  end: [13, 56],
  has_ferry: true,
  chapters: [
    {
      chapter_id: "day-1",
      index: 0,
      segments: [
        { mode: "driving", points: [[10, 54], [10.5, 54.4]] },
        { mode: "ferry", points: [[10.5, 54.4], [13, 56]] },
      ],
      start: [10, 54],
      end: [13, 56],
      bbox: [10, 54, 13, 56],
      has_ferry: true,
      estimated: false,
    },
  ],
  ...overrides,
});

// --- absent is legal, malformed is not ---------------------------------

// A trip whose roadbook has no coordinates has no map, and that is a film
// without a map rather than a failed render.
assert.equal(parseMapContext(null), null);
assert.equal(parseMapContext(undefined), null);

const parsed = parseMapContext(mapContext());
assert.equal(parsed.chapters.length, 1);
assert.equal(parsed.chapters[0].segments[1].mode, "ferry");
assert.equal(parsed.hasFerry, true);
assert.deepEqual(parsed.chapters[0].start, [10, 54]);

const refuses = (payload, why) => {
  assert.throws(() => parseMapContext(payload), why);
};

refuses(mapContext({ map_context_version: 99 }), /Version/);
refuses(mapContext({ chapters: [] }), /Kapitelliste/);
refuses(
  mapContext({
    chapters: [{ chapter_id: "d", segments: [{ mode: "flying", points: [[1, 1], [2, 2]] }] }],
  }),
  /Art/,
);
// The three shapes that would each reach a projection as a non-number.
for (const point of [["10", 54], [10, null], [10, 91], [Number.NaN, 54], [10]]) {
  refuses(
    mapContext({
      chapters: [
        {
          chapter_id: "d",
          index: 0,
          segments: [{ mode: "driving", points: [point, [11, 55]] }],
          start: [10, 54],
          end: [11, 55],
          bbox: [10, 54, 11, 55],
        },
      ],
    }),
    /Koordinate/,
  );
}
// A single point is not a line, and half a line drawn is worse than none.
refuses(
  mapContext({
    chapters: [
      {
        chapter_id: "d",
        index: 0,
        segments: [{ mode: "driving", points: [[10, 54]] }],
        start: [10, 54],
        end: [10, 54],
        bbox: [10, 54, 10, 54],
      },
    ],
  }),
  /Punkte/,
);
refuses(mapContext({ bbox: [13, 56, 10, 54] }), /verdreht/);

// --- the scene library knows the map ------------------------------------

for (const type of ["map_start", "map_leg", "map_full"]) {
  assert.ok(FILM_SCENE_TYPES.has(type), `${type} must be renderable`);
}

// --- a whole package, the way Python writes one -------------------------

const filmPackage = (overrides = {}) => ({
  film_package_version: 1,
  manifest_content_hash: HASH,
  trip: { title: "Reise", start_date: "", end_date: "", chapter_count: 1 },
  chapters: [
    {
      chapter_id: "day-1",
      index: 0,
      date: "2026-06-01",
      title: "Tag 1",
      story: "Eine Zeile.",
      images: [
        {
          path: "photos/c00-1.jpg",
          size_bytes: 1234,
          sha256: HASH,
          width: 600,
          height: 900,
          orientation: "portrait",
        },
      ],
    },
  ],
  map_context: mapContext(),
  scene_plan: {
    plan_version: 1,
    fps: 30,
    total_frames: 300,
    scenes: [
      { type: "map_start", chapter_id: "", chapter_index: -1, frames: 120, enter: "rise", photos: [] },
      { type: "map_leg", chapter_id: "day-1", chapter_index: 0, frames: 105, enter: "fade", photos: [] },
      { type: "photo", chapter_id: "day-1", chapter_index: 0, frames: 75, enter: "fade", photos: [0] },
    ],
  },
  ...overrides,
});

const film = parseFilmPackage(JSON.stringify(filmPackage()));
assert.equal(film.mapContext.chapters.length, 1);
// The map is addressed by chapter id, not by position: the context skips
// chapters with no geography, so the two lists are deliberately not the
// same length.
assert.equal(film.scenes[1].chapterId, "day-1");
assert.equal(film.scenes[0].chapterId, "");
// Shape travels with the picture. Without it a portrait photograph would
// be cropped to its middle third in a 16:9 frame.
assert.equal(film.chapters[0].photos[0].orientation, "portrait");
assert.equal(film.chapters[0].photos[0].width, 600);

// An unknown orientation falls back rather than reaching the composition.
const odd = filmPackage();
odd.chapters[0].images[0].orientation = "diagonal";
assert.equal(parseFilmPackage(JSON.stringify(odd)).chapters[0].photos[0].orientation, "landscape");

// A package with no map at all still parses - and its scenes still do.
const noMap = filmPackage({ map_context: null });
assert.equal(parseFilmPackage(JSON.stringify(noMap)).mapContext, null);

// A broken map is refused with the package rather than reaching a render.
assert.throws(
  () => parseFilmPackage(JSON.stringify(filmPackage({ map_context: mapContext({ bbox: [1] }) }))),
  /Kartenausschnitt/,
);

console.log("Renderer app film map tests passed.");
