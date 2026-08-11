/**
 * An upright photograph in a landscape tile may not be cropped to fit.
 *
 * The day collage states this rule in its own comment, twice, because it
 * learned it twice: "a wall of memories may not crop them. An upright
 * photograph in a landscape tile loses its sky and its subject to
 * `cover`" - and "a box in both dimensions, so nothing can run off the
 * frame ... which is what let an upright photograph in the lower row
 * overflow the bottom."
 *
 * The closing collage - the LAST shot of the film - did both of the
 * things that comment forbids: `objectFit: "cover"`, and a grid whose
 * rows were sized by whatever was in them. Landscape pictures beside it
 * looked correct, which is what makes this read as "some of my photos
 * are broken" rather than "this scene has a style".
 *
 * The rule lived in a comment in one component. A comment is not a rule
 * a second component has to obey, so it is one here instead.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const FILM = readFileSync(
  new URL("../apps/roadplanner_renderer/src/remotion/RoadplannerTripFilm.tsx", import.meta.url),
  "utf8",
);

/** The body of one React component, from its declaration to the next one. */
function component(name) {
  const start = FILM.indexOf(`const ${name}: React.FC`);
  assert.ok(start >= 0, `${name} gibt es nicht mehr`);
  const rest = FILM.slice(start + 1);
  const next = rest.search(/\nconst \w+: React\.FC/);
  return next === -1 ? rest : rest.slice(0, next);
}

/**
 * Every scene that shows several pictures at once, side by side. In these
 * a picture gets a slot rather than the frame, so it must fit into its
 * slot instead of filling it.
 */
const TILED_SCENES = ["CollageScene", "OutroCollageScene"];

function verifyNoTiledSceneCropsAPhotograph() {
  for (const name of TILED_SCENES) {
    const body = component(name);
    assert.ok(
      body.includes('objectFit: "contain"'),
      `${name} zeigt Kachelbilder nicht mit "contain"`,
    );
    assert.ok(
      !/objectFit:\s*"cover"/.test(body),
      `${name} beschneidet Kachelbilder mit "cover" - ein hochkantes Foto ` +
        "verliert dort Himmel und Motiv",
    );
  }
}

function verifyEveryTileIsABoxInBothDimensions() {
  // The failure this prevents is the other half of the same bug: a tile
  // whose height comes from its picture makes a tall picture push the
  // row - and the bottom row off the frame.
  const collage = component("CollageScene");
  assert.ok(/height: `\$\{tile\.height\}%`/.test(collage), "CollageScene: Kachel ohne Höhe");

  const outro = component("OutroCollageScene");
  assert.ok(
    outro.includes("gridTemplateRows"),
    "OutroCollageScene: Zeilenhöhe wird den Bildern überlassen",
  );
  assert.ok(
    outro.includes("minHeight: 0"),
    "OutroCollageScene: Grid-Kacheln schrumpfen nicht unter ihren Inhalt",
  );
}

function verifyTheFullFrameSceneStillDecidesByOrientation() {
  // The counterpart, so this is not read as "contain everywhere". A
  // landscape photograph that has the whole 16:9 frame to itself SHOULD
  // fill it; only an upright one must be contained. That distinction is
  // the reason orientation is carried through the package at all.
  const photo = component("PhotoScene");
  assert.ok(
    /objectFit: upright \? "contain" : "cover"/.test(photo),
    "PhotoScene entscheidet nicht mehr nach Orientierung",
  );
}

for (const check of [
  verifyNoTiledSceneCropsAPhotograph,
  verifyEveryTileIsABoxInBothDimensions,
  verifyTheFullFrameSceneStillDecidesByOrientation,
]) {
  check();
}

console.log("Upright photos in tiles checked.");
