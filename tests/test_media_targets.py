"""How many pixels a picture gets, and what may never decide that.

The first 1440p check showed photographs looking softer than the text
and the graphics beside them. The cause was one number: every picture
was prepared at 900 px on its longest edge, and a landscape picture in a
full 1440p frame is drawn at 2790 - a 3.1x upscale inside the renderer.

Fixing it means letting the render profile influence the technical
preparation of an asset, which is the one place this project has spent
a lot of effort keeping it OUT of. So the line matters more than the
arithmetic: a profile may decide how many pixels a file has. It may not
decide which picture, in which order, cropped how, for how long.

Every number here is read from the rendering rather than chosen: the
collage grid from the composition's own layout function, the fit modes
from the composition's `objectFit`, the movement reserve from the Ken
Burns span in the same file. A brief suggested "1.15 to 1.30" as the
reserve; the code knows it is 1.09.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer" / "src"

_pkg = types.ModuleType("roadplanner_targets_pkg")
_pkg.__path__ = [str(PACKAGE_ROOT)]
sys.modules["roadplanner_targets_pkg"] = _pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_targets_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


profiles = load("render_profiles")
plan_module = load("trip_film_plan")
targets = load("media_targets")

COMPOSITION = (APP / "remotion" / "RoadplannerTripFilm.tsx").read_text(encoding="utf-8")
EXPORT_SOURCE = (PACKAGE_ROOT / "trip_film_export.py").read_text(encoding="utf-8")

LANDSCAPE = {"source_width": 4032, "source_height": 3024}
PORTRAIT = {"source_width": 3024, "source_height": 4032}


def _edge(scene_type: str, count: int, profile: str, **shape) -> int:
    return targets.required_edge(
        scene_type=scene_type, photo_count=count, profile_id=profile, **shape
    )


def verify_a_bigger_film_asks_for_bigger_pictures() -> None:
    """The whole point, stated as an ordering rather than as numbers."""
    order = ["review_480", "review_720", "full_hd", "high_quality", "uhd_4k"]
    heroes = [_edge("hero", 1, name, **LANDSCAPE) for name in order]
    assert heroes == sorted(heroes), heroes
    assert heroes[0] < heroes[-1]
    # And 1440p genuinely covers what the frame draws: 2560 wide, plus
    # the Ken Burns span, because a picture scaled during its scene has
    # to have those pixels before the scene starts.
    assert _edge("hero", 1, "high_quality", **LANDSCAPE) >= 2560, heroes


def verify_a_small_tile_never_gets_a_full_frame_asset() -> None:
    """§15: the saving is real, and it is where most of the pictures are.

    Four-, six- and nine-up tiles are already given more pixels than they
    draw. Preparing every one of them for a full frame would multiply the
    package for detail no viewer can see.
    """
    hero = _edge("hero", 1, "high_quality", **LANDSCAPE)
    for count in (2, 4, 6, 9):
        tile = _edge("collage", count, "high_quality", **LANDSCAPE)
        assert tile < hero, (count, tile, hero)
    # Strictly smaller as the tiles get smaller. A grid that gave a 9-up
    # tile more than a 4-up would mean the layout arithmetic disagrees
    # with the composition's.
    sizes = [_edge("collage", count, "high_quality", **LANDSCAPE) for count in (2, 4, 6, 9)]
    assert sizes == sorted(sizes, reverse=True), sizes


def verify_orientation_decides_which_axis_binds() -> None:
    """`contain` binds on the other axis, so an upright picture needs less.

    Reading the slot alone would have prepared every full-frame picture
    for 2790 px, including the upright ones that are drawn 1570 wide -
    a wasted megabyte each, on a decision the file itself answers.
    """
    wide = _edge("hero", 1, "high_quality", **LANDSCAPE)
    tall = _edge("hero", 1, "high_quality", **PORTRAIT)
    assert tall < wide, (tall, wide)
    # And the reason, checked against the composition rather than
    # assumed: only a landscape picture in a full frame is `cover`.
    assert targets.slot_fit("hero", "landscape") == "cover"
    assert targets.slot_fit("hero", "portrait") == "contain"
    assert targets.slot_fit("collage", "landscape") == "contain"
    assert 'objectFit: upright ? "contain" : "cover"' in COMPOSITION, (
        "die Komposition entscheidet den Fit anders als diese Tabelle"
    )


def verify_the_reserve_is_the_movement_the_film_actually_has() -> None:
    """Measured, not chosen. A guessed factor is either waste or short."""
    found = re.search(r"const span = hero \? ([\d.]+) : ([\d.]+);", COMPOSITION)
    assert found, "die Zoom-Spanne steht nicht mehr im Film"
    assert targets.ZOOM_HERO == 1 + float(found.group(1)), found.groups()
    assert targets.ZOOM_PHOTO == 1 + float(found.group(2)), found.groups()


def verify_the_collage_grid_matches_the_composition() -> None:
    """Two copies of one layout, compared rather than trusted.

    A tile that is a quarter of the frame on one side and a ninth on the
    other is this project's oldest bug wearing a new hat.
    """
    for name, value in (
        ("COLLAGE_MARGIN", targets.COLLAGE_MARGIN),
        ("COLLAGE_GUTTER", targets.COLLAGE_GUTTER),
        ("COLLAGE_CAPTION_ROOM", targets.COLLAGE_CAPTION_ROOM),
    ):
        found = re.search(rf"const {name} = ([\d.]+);", COMPOSITION)
        assert found, f"{name} fehlt in der Komposition"
        assert float(found.group(1)) == value, (name, found.group(1), value)
    # The column rule too: it decides the tile's width directly.
    for count, columns in ((1, 1), (2, 2), (4, 2), (6, 3), (9, 4)):
        assert targets.collage_columns(count) == columns, count


def verify_nothing_is_ever_enlarged_before_the_renderer() -> None:
    """§7: a small original stays small, and says so.

    Enlarging here would spend package bytes on detail that does not
    exist and hide the one fact worth knowing - that this photograph
    never had it.
    """
    package = (PACKAGE_ROOT / "trip_film_package.py").read_text(encoding="utf-8")
    body = package.split("def shrink_film_photo(", 1)[1].split("\ndef ", 1)[0]
    assert "image.thumbnail(" in body, body
    assert "resize(" not in body, (
        "irgendetwas skaliert hier wieder frei - thumbnail vergrößert nie"
    )
    assert "source_limited" in body, "der Fall wird nicht mehr benannt"


def verify_the_two_kinds_of_shortfall_stay_apart() -> None:
    """One is ours to fix. The other is not.

    Reporting them as one number sends somebody to check the thing that
    is fine - which is exactly what a single "expected a PNG with a
    transparent background" did on every rejection.
    """
    assert "source_limited" in EXPORT_SOURCE
    body = EXPORT_SOURCE.split("def _resolution_summary(", 1)[1].split("\ndef ", 1)[0]
    assert '"sufficient"' in body and '"source_limited"' in body, body
    # And a picture that falls short is NOT removed from the film.
    for verb in ("continue  # too small", "del ", ".remove("):
        assert verb not in body, body


def verify_the_profile_decides_pixels_and_nothing_else() -> None:
    """§4 and §20, as a structural rule rather than a hope.

    The scene planner must not be able to see the profile at all - if it
    could, the same trip would become two different films and the
    before/after comparison would be meaningless.
    """
    planner = (PACKAGE_ROOT / "trip_film_plan.py").read_text(encoding="utf-8")
    for forbidden in ("render_profile", "profile_id", "RENDER_PROFILES", "media_targets"):
        assert forbidden not in planner, (
            f"der Szenenplaner liest {forbidden} - dann ist der Plan nicht mehr "
            "unabhängig von der Ausgabegröße"
        )
    # And in the export the profile reaches exactly two technical places:
    # how large a picture is prepared, and how tall a clip is cut.
    uses = re.findall(r"profile_id[,)=\s]", EXPORT_SOURCE)
    assert uses, EXPORT_SOURCE[:0]
    for decision in ("prominent_by_chapter", "budget", "excerpt_range("):
        segment = EXPORT_SOURCE.split(decision, 1)[0][-400:]
        assert "profile_id" not in segment, (
            f"das Profil steht direkt vor {decision} - es darf nur die "
            "technische Aufbereitung beeinflussen"
        )


def verify_the_same_trip_plans_the_same_film_at_every_size() -> None:
    """§20 measured: the plan is bit-identical across profiles.

    The whole before/after comparison rests on this. A plan that shifted
    by one scene would make the second render a different film, and any
    verdict on it would be about something nobody asked.
    """
    chapters = [
        {
            "chapter_id": f"d{index}",
            "title": f"Tag {index + 1}",
            "story": "x" * 120,
            "images": [""] * 9,
            "day_number": index + 1,
        }
        for index in range(8)
    ]
    plan = plan_module.build_scene_plan(
        trip={}, chapters=chapters, narrative={}, map_context=None, outro_photos=[]
    )
    # Building it again cannot depend on a profile, because the planner
    # takes none - the argument does not exist. What CAN differ is the
    # target table, so that is what varies here.
    for name in profiles.RENDER_PROFILES:
        again = plan_module.build_scene_plan(
            trip={}, chapters=chapters, narrative={}, map_context=None, outro_photos=[]
        )
        assert again == plan, name
        slots = targets.photo_slots(plan)
        assert slots, "keine Bildslots im Plan"
        edges = {key: _edge(kind, count, name, **LANDSCAPE) for key, (kind, count) in slots.items()}
        assert len(edges) == len(slots)


def verify_a_picture_in_two_scenes_keeps_the_larger_claim() -> None:
    """A hero that is also in the day's collage has to satisfy both."""
    plan = {
        "scenes": [
            {"type": "collage", "chapter_id": "d0", "photos": [0, 1, 2, 3]},
            {"type": "hero", "chapter_id": "d0", "photos": [0]},
        ]
    }
    slots = targets.photo_slots(plan)
    assert slots[("d0", 0)] == ("hero", 1), slots
    assert slots[("d0", 3)] == ("collage", 4), slots
    # Order must not decide it: the same plan listed the other way round
    # answers the same.
    plan["scenes"].reverse()
    assert targets.photo_slots(plan)[("d0", 0)] == ("hero", 1)


def verify_the_video_render_proxy_follows_the_profile_but_not_the_analysis() -> None:
    """§12 and §13: a bigger copy for the film, no new analysis.

    The analysis proxy is small on purpose - it is what leaves the house.
    Raising the film's copy must not touch it, must not re-run anything,
    and must not enlarge a recording beyond its own height.
    """
    video = (PACKAGE_ROOT / "video_proxy.py").read_text(encoding="utf-8")
    module = load("video_proxy")
    assert module.ANALYSIS_HEIGHT == 360, "der Analyseproxy hat sich verändert"
    assert module.ANALYSIS_FPS == 8
    assert module.render_height("high_quality", 2160) == 1440
    assert module.render_height("review_480", 2160) == 480
    # Never above the recording itself.
    assert module.render_height("high_quality", 720) == 720
    assert module.render_height("uhd_4k", 1080) == 1080
    # The analysis path takes no height PARAMETER, so it cannot drift.
    # Its signature, not its prose: the body explains a libx264 failure
    # about a height not divisible by two, and searching the text for the
    # word flagged that comment - a check that trips over the
    # explanation of why the code is right is worse than none.
    signature = video.split("def analysis_args(", 1)[1].split(")", 1)[0]
    assert "height" not in signature, signature
    assert f"h={{ANALYSIS_HEIGHT}}" in video, "der Analyseproxy ist nicht mehr fest"
    # And nothing about a bigger clip reaches a provider.
    clips = EXPORT_SOURCE.split("async_cut_render_proxy(", 1)[0][-2000:]
    for provider in ("async_generate", "gemini", "Gemini", "analyze"):
        assert provider not in clips, provider


def verify_one_lossy_step_and_no_chroma_thrown_away() -> None:
    """§8: quality lost to encoding is as real as quality lost to pixels."""
    package = (PACKAGE_ROOT / "trip_film_package.py").read_text(encoding="utf-8")
    body = package.split("def shrink_film_photo(", 1)[1].split("\ndef ", 1)[0]
    # Exactly one save at full quality, and a second only as the fallback
    # when the first exceeded the budget.
    assert body.count("image.save(") == 2, body.count("image.save(")
    assert "subsampling=JPEG_SUBSAMPLING" in body
    assert "JPEG_SUBSAMPLING = 0" in package, "Chroma wird wieder weggeworfen"
    # Quality and budget follow the size rather than staying at the value
    # that was chosen for 900 px.
    module = load("trip_film_package")
    assert module.image_quality(2790) > module.image_quality(900)
    assert module.image_byte_budget(2790) > module.image_byte_budget(900)
    assert module.image_byte_budget(2790) <= module.MAX_PREPARED_IMAGE_BYTES


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Media target tests passed.")


if __name__ == "__main__":
    main()
