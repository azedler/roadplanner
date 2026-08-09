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
  parseCrew,
  parseFilmPackage,
  parseMapContext,
  parseMusic,
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

for (const type of ["map_start", "map_leg", "map_full", "crew"]) {
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
          color_top: "#3a4b5c",
          color_bottom: "#101820",
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
// The surround colours reach a stylesheet, so anything that is not a
// plain hex colour is replaced rather than passed through.
assert.equal(film.chapters[0].photos[0].colorTop, "#3a4b5c");
const painted = filmPackage();
painted.chapters[0].images[0].color_top = "red; background: url(x)";
assert.equal(
  parseFilmPackage(JSON.stringify(painted)).chapters[0].photos[0].colorTop,
  "#161d29",
);

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

// --- the crew: names and faces, never an address -----------------------

// Absent is normal: a trip without a crew record has no crew scene.
assert.equal(parseCrew(null), null);

const crew = (overrides = {}) => ({
  members: [
    { name: "Aron", path: "crew/member-00.jpg", size_bytes: 4321, sha256: HASH },
    { name: "Nina", path: "", size_bytes: 0, sha256: "" },
  ],
  ...overrides,
});

const parsedCrew = parseCrew(crew());
assert.equal(parsedCrew.members.length, 2);
// A member whose portrait could not be prepared still travels: leaving
// somebody out of the crew because their photo failed to open would be
// the worst possible way to handle that.
assert.equal(parsedCrew.members[1].path, "");

// The rule that matters most. The crew portrait route is guarded only by
// an unguessable filename - a bearer secret, not a session - so a link
// in a package written to a shared folder would be a copy of that
// capability sitting on disk.
assert.throws(
  () =>
    parseCrew({
      members: [{ name: "Aron", portrait_url: "/api/roadplanner/crew_portrait/abc.jpg" }],
    }),
  /Adressen/,
);
assert.throws(
  () => parseCrew({ members: [{ name: "Aron", note: "https://example.invalid/face.jpg" }] }),
  /Adressen/,
);
// The path is built from the position, so a member cannot point at
// another member's file - or anywhere else.
assert.throws(
  () => parseCrew({ members: [{ name: "A", path: "crew/member-04.jpg", size_bytes: 1, sha256: HASH }] }),
  /Crewbildpfad/,
);
assert.throws(() => parseCrew({ members: [{ name: "" }] }), /Namen/);

// --- the music: a file in the job folder, or nothing -------------------

assert.equal(parseMusic(null), null);
const track = parseMusic({
  path: "music/track.mp3",
  size_bytes: 3_000_000,
  sha256: HASH,
  volume: 0.4,
  title: "Abendlicht",
});
assert.equal(track.path, "music/track.mp3");
assert.equal(track.volume, 0.4);
// The volume reaches an audio node, so it is clamped rather than trusted.
assert.equal(parseMusic({ ...crewlessMusic(), volume: 9 }).volume, 1);
assert.equal(parseMusic({ ...crewlessMusic(), volume: -3 }).volume, 0);
// A path from the user's machine is not a file in the job folder.
for (const path of ["/media/music/x.mp3", "../../etc/passwd", "music/track.exe", "track.mp3"]) {
  assert.throws(() => parseMusic({ ...crewlessMusic(), path }), /Auftragsordner|Größe/);
}

function crewlessMusic() {
  return { path: "music/track.mp3", size_bytes: 3_000_000, sha256: HASH };
}

// --- a generated score, in sections ------------------------------------
//
// A chosen file plays as one looped track; a generated score plays as a
// few long pieces that hand over to each other. Absent sections mean the
// former, which is what every job built before this looked like.

assert.deepEqual(parseMusic(crewlessMusic()).sections, []);

const section = (overrides = {}) => ({
  path: "music/section-01.mp3",
  size_bytes: 2_000_000,
  sha256: HASH,
  start_seconds: 0,
  seconds: 120,
  fade_in_seconds: 2,
  fade_out_seconds: 4,
  ...overrides,
});

const scored = parseMusic({
  ...crewlessMusic(),
  sections: [
    section(),
    section({ path: "music/section-02.mp3", start_seconds: 116, fade_in_seconds: 4 }),
  ],
});
assert.equal(scored.sections.length, 2);
assert.equal(scored.sections[1].startSeconds, 116);
assert.equal(scored.sections[1].fadeInSeconds, 4);
// The second starts before the first ends: the handover is a crossfade,
// not a cut, and the plan is what decides that - not the renderer.
assert.ok(
  scored.sections[1].startSeconds <
    scored.sections[0].startSeconds + scored.sections[0].seconds,
);

// Every section is a file with a hash, checked exactly like the track.
assert.throws(() => parseMusic({ ...crewlessMusic(), sections: {} }), /Liste/);
assert.throws(
  () => parseMusic({ ...crewlessMusic(), sections: [section({ path: "../x.mp3" })] }),
  /Auftragsordner/,
);
assert.throws(
  () => parseMusic({ ...crewlessMusic(), sections: [section({ sha256: "nope" })] }),
  /SHA-256/,
);
assert.throws(
  () => parseMusic({ ...crewlessMusic(), sections: [section({ size_bytes: 0 })] }),
  /Größe/,
);
assert.throws(
  () => parseMusic({ ...crewlessMusic(), sections: [section(), section(), section(), section(), section()] }),
  /Zu viele/,
);
// Nonsense timing degrades to zero rather than reaching an audio node.
const oddTiming = parseMusic({
  ...crewlessMusic(),
  sections: [section({ start_seconds: -5, seconds: "lang", fade_in_seconds: NaN })],
});
assert.equal(oddTiming.sections[0].startSeconds, 0);
assert.equal(oddTiming.sections[0].seconds, 0);
assert.equal(oddTiming.sections[0].fadeInSeconds, 0);

console.log("Renderer app film map tests passed.");
