/**
 * The day's text runs once.
 *
 * The planner gives a long caption a scene of its own INSTEAD of the
 * overlay - "so it gets a scene of its own, before the pictures, and the
 * pictures keep their room". Nothing carried that decision to the
 * pictures, so the composition drew the same sentence a second time over
 * the first photograph, over the collage and over the clip. Measured on
 * the Finnland trip: up to 22 of 23 chapters read their text twice.
 *
 * The flag is DERIVED from the plan, never declared beside it. One
 * decision, one place that makes it - which is the whole point, because
 * a second field saying the same thing is how the two got out of step in
 * the first place.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { parseFilmPackage } from "../apps/roadplanner_renderer/src/protocol.mjs";

const HASH = "a".repeat(64);

const image = (path) => ({
  path,
  size_bytes: 1234,
  sha256: HASH,
  width: 1600,
  height: 900,
  orientation: "landscape",
  color_top: "#3a4b5c",
  color_bottom: "#101820",
});

const chapter = (index, story) => ({
  chapter_id: `day-${index + 1}`,
  index,
  date: "2026-06-01",
  title: `Tag ${index + 1}`,
  story,
  images: [image(`photos/c0${index}-1.jpg`)],
});

const filmPackage = (scenes) => ({
  film_package_version: 1,
  manifest_content_hash: HASH,
  trip: { title: "Reise", start_date: "", end_date: "", chapter_count: 2 },
  chapters: [chapter(0, "Ein sehr langer Tagestext."), chapter(1, "Kurz.")],
  scene_plan: {
    plan_version: 1,
    fps: 30,
    total_frames: scenes.reduce((sum, scene) => sum + scene.frames, 0),
    scenes,
  },
});

const scene = (type, chapterIndex, frames, photos = []) => ({
  type,
  chapter_id: chapterIndex >= 0 ? `day-${chapterIndex + 1}` : "",
  chapter_index: chapterIndex,
  frames,
  enter: "fade",
  photos,
});

function verify_a_chapter_with_its_own_text_page_is_marked() {
  const film = parseFilmPackage(
    JSON.stringify(
      filmPackage([
        scene("text", 0, 120),
        scene("photo", 0, 75, [0]),
        scene("photo", 1, 75, [0]),
      ]),
    ),
  );
  assert.equal(film.chapters[0].storyHasOwnScene, true, "this chapter already showed its text");
  assert.equal(film.chapters[1].storyHasOwnScene, false, "this one did not, so it keeps the overlay");
}

function verify_a_plan_without_text_pages_marks_nothing() {
  const film = parseFilmPackage(
    JSON.stringify(filmPackage([scene("photo", 0, 75, [0]), scene("photo", 1, 75, [0])])),
  );
  assert.equal(film.chapters[0].storyHasOwnScene, false);
  assert.equal(film.chapters[1].storyHasOwnScene, false);
}

function verify_a_text_page_belonging_to_no_chapter_marks_no_chapter() {
  // The intro and outro carry no chapter index. A -1 must not be read as
  // "chapter 0 has its page" - that would silence the first day's text.
  const film = parseFilmPackage(
    JSON.stringify(
      filmPackage([scene("text", -1, 120), scene("photo", 0, 75, [0]), scene("photo", 1, 75, [0])]),
    ),
  );
  assert.equal(film.chapters[0].storyHasOwnScene, false, "a chapterless page silences nobody");
}

function verify_an_excerpt_that_drops_the_page_gets_its_text_back() {
  // A QA excerpt re-slices the plan. If its slice no longer contains the
  // text page, the overlay is the only place the day's text can appear -
  // and because the flag is derived from the plan that is actually being
  // rendered, that happens by itself.
  const film = parseFilmPackage(
    JSON.stringify(filmPackage([scene("photo", 0, 75, [0]), scene("photo", 1, 75, [0])])),
  );
  assert.equal(film.chapters[0].storyHasOwnScene, false);
}

function verify_every_overlay_in_the_composition_goes_through_the_one_rule() {
  const source = readFileSync(
    new URL("../apps/roadplanner_renderer/src/remotion/RoadplannerTripFilm.tsx", import.meta.url),
    "utf-8",
  );
  const declaration = "const StoryCaption";
  assert.ok(source.includes(declaration), "the rule has a home");
  const body = source.slice(source.indexOf(declaration));
  const rest = body.slice(body.indexOf("\n", body.indexOf(";")));
  const strays = rest.match(/<Caption\s+text=\{chapter\.story\}/g) || [];
  assert.deepEqual(
    strays,
    [],
    "a caption drawn from chapter.story must go through StoryCaption, or the text runs twice again",
  );
  const used = source.match(/<StoryCaption chapter=\{chapter\} \/>/g) || [];
  assert.equal(used.length, 3, "photo, collage and clip - the three places a day's text was drawn");
}

const checks = Object.entries({
  verify_a_chapter_with_its_own_text_page_is_marked,
  verify_a_plan_without_text_pages_marks_nothing,
  verify_a_text_page_belonging_to_no_chapter_marks_no_chapter,
  verify_an_excerpt_that_drops_the_page_gets_its_text_back,
  verify_every_overlay_in_the_composition_goes_through_the_one_rule,
});

for (const [name, check] of checks) {
  await check();
  console.log(`ok - ${name}`);
}
console.log(`\n${checks.length} checks passed`);
