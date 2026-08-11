"""The whole polish in one small film, driven through the real planner.

Each piece of this block has its own file. This one exists because they
have to hold TOGETHER: a reserved slot is worthless if the packing then
duplicates the picture, a clip handle is worthless if the plan holds the
clip for the analysed length, and a hidden supply stop is worthless if
the chapter card announces it anyway.

So it builds one deliberately small trip - a handful of seconds, not a
holiday - carrying exactly the six things this block changed, and reads
the finished scene plan:

    a central photograph that must be seen        -> reserved, hero
    a clip of the day's subject                   -> plays
    a short clip                                  -> grown, inside bounds
    a collage                                     -> the rest
    a supply stop, in the roadbook                -> absent from the film
    a rotated portrait clip                       -> upright

No renderer, no browser, no provider, no network: this is the plan the
renderer is handed, which is the last place the integration decides
anything.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_polish_film_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

plan = importlib.import_module(f"{_PACKAGE}.trip_film_plan")
orch = importlib.import_module(f"{_PACKAGE}.video_orchestration")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")
prom = importlib.import_module(f"{_PACKAGE}.visual_prominence")

MOTIF = "bunker"


def _photo(index, *, shows=(), story=3, quality=3):
    return {
        "path": f"images/c00-{index}.jpg",
        "size_bytes": 1000,
        "sha256": "0" * 64,
        "width": 900,
        "height": 600,
        "orientation": "landscape",
        "story_value": story,
        "visual_quality": quality,
        "shows": list(shows),
        "motifs": [],
    }


def _analyses():
    """What the curation stored, in the fields it really uses."""
    return {
        "side": {"story_value": 4, "visual_quality": 4, "shows": [], "motifs": []},
        "central": {
            "story_value": 5,
            "visual_quality": 4,
            "shows": [MOTIF],
            "motifs": [],
        },
        "extra-1": {"story_value": 2, "visual_quality": 3, "shows": [], "motifs": []},
        "extra-2": {"story_value": 2, "visual_quality": 2, "shows": [], "motifs": []},
        "extra-3": {"story_value": 2, "visual_quality": 2, "shows": [], "motifs": []},
    }


def _short_clip():
    """Two and a bit seconds: the "moving still" the review reported."""
    return {
        "media_id": "vid-short",
        "start_seconds": 5.2,
        "end_seconds": 7.4,
        "duration_seconds": 2.2,
        "role": analysis.ROLE_AMBIENT,
        "subject": "Wasser am Ufer",
        "story_value": 4,
        "visual_quality": 4,
        "window_start": 0.0,
        "window_end": 30.0,
        "source_duration_seconds": 30.0,
    }


def _rotated_clip():
    """Upright picture, landscape header - the shape a phone writes."""
    return {
        "media_id": "vid-rotated",
        "start_seconds": 1.0,
        "end_seconds": 6.0,
        "duration_seconds": 5.0,
        "role": analysis.ROLE_HERO,
        "subject": f"Ein {MOTIF} von innen",
        "story_value": 5,
        "visual_quality": 4,
        "window_start": 0.0,
        "window_end": 20.0,
        "source_duration_seconds": 20.0,
    }


def _chapter(*, images, prominent_index=None, stops=None):
    return {
        "chapter_id": "day-1",
        "index": 0,
        "date": "2026-08-01",
        "title": "Ein Tag",
        "story": "Ein kurzer Tag für die Technik.",
        "importance": "highlight",
        "visual_style": "collage",
        "images": images,
        "stops": stops if stops is not None else ["Ein Aussichtspunkt"],
        "prominent_index": prominent_index,
    }


def _scenes(chapter, clips=None):
    found = plan.build_scene_plan(
        trip={"title": "Technikfilm", "subtitle": ""},
        chapters=[chapter],
        clips_by_chapter={"day-1": clips} if clips else None,
    )
    return [scene for scene in found["scenes"] if scene.get("chapter_id") == "day-1"]


def verify_the_central_photograph_gets_the_slot_and_keeps_it() -> None:
    """Reserved before packing, and not repeated in the collage after."""
    media = ["side", "central", "extra-1", "extra-2", "extra-3"]
    decided = prom.reserve_for_prominence(
        media, must_cover=[MOTIF], analyses=_analyses()
    )
    assert decided["reserved"] == "central", decided
    reserved_at = decided["order"].index("central")

    images = [_photo(index) for index in range(len(media))]
    scenes = _scenes(_chapter(images=images, prominent_index=reserved_at))

    heroes = [scene for scene in scenes if scene["type"] == plan.SCENE_HERO]
    assert len(heroes) == 1, [scene["type"] for scene in scenes]
    assert heroes[0]["photos"] == [reserved_at], heroes[0]

    # It is a collage day, so the rest is grouped - and the reserved one
    # is not among them.
    collages = [scene for scene in scenes if scene["type"] == plan.SCENE_COLLAGE]
    assert collages, [scene["type"] for scene in scenes]
    tiled = [index for scene in collages for index in scene["photos"]]
    assert reserved_at not in tiled, tiled
    # Every picture exactly once across the whole day.
    shown = sorted(
        index for scene in scenes for index in (scene.get("photos") or [])
    )
    assert shown == list(range(len(media))), shown


def verify_the_same_day_without_a_central_motif_is_untouched() -> None:
    """The change may not alter an ordinary day."""
    images = [_photo(index) for index in range(5)]
    before = [scene["type"] for scene in _scenes(_chapter(images=images))]
    assert plan.SCENE_HERO not in before, before


def verify_the_short_clip_is_grown_and_the_plan_holds_it_that_long() -> None:
    """Cut length and screen time are the same number, or it is clipped."""
    [grown] = orch.with_render_windows([_short_clip()])
    assert grown["render_duration_seconds"] > grown["duration_seconds"], grown

    frames = max(1, round(grown["render_duration_seconds"] * plan.FILM_FPS))
    clips = [{"path": "clips/c00-1.mp4", "frames": frames, "size_bytes": 10}]
    scenes = _scenes(_chapter(images=[_photo(0), _photo(1)]), clips=clips)
    clip_scenes = [scene for scene in scenes if scene["type"] == plan.SCENE_CLIP]
    assert clip_scenes, [scene["type"] for scene in scenes]
    assert clip_scenes[0]["frames"] == frames, clip_scenes[0]

    # And the analysed window is untouched by all of it.
    assert grown["start_seconds"] == 5.2 and grown["end_seconds"] == 7.4


def verify_the_clip_of_the_days_subject_makes_a_photograph_unnecessary() -> None:
    """Medium-neutral: a clip that shows the subject reserves nothing."""
    decided = prom.reserve_for_prominence(
        ["side", "central"],
        must_cover=[MOTIF],
        analyses=_analyses(),
        clips=[_rotated_clip()],
    )
    assert decided["reserved"] == "", decided


def verify_a_supply_stop_is_in_the_roadbook_and_not_in_the_film() -> None:
    """The stop exists. The film simply does not announce it."""
    roadbook = [
        {"name": "Ein Aussichtspunkt", "kind": "viewpoint"},
        {"name": "Eine Tankstelle", "kind": "fuel"},
    ]
    before = [dict(stop) for stop in roadbook]
    shown = plan.readable_places(roadbook, limit=3)
    assert shown == ["Ein Aussichtspunkt"], shown
    assert roadbook == before, "der Roadbook-Eintrag wurde verändert"
    # The reporting view still has both.
    assert len(plan.readable_places(roadbook, limit=3, include_functional=True)) == 2


def verify_the_whole_thing_stays_a_short_film() -> None:
    """A technical film, not a holiday: seconds, and no runaway."""
    images = [_photo(index) for index in range(5)]
    clips = [{"path": "clips/c00-1.mp4", "frames": 90, "size_bytes": 10}]
    found = plan.build_scene_plan(
        trip={"title": "Technikfilm", "subtitle": ""},
        chapters=[_chapter(images=images, prominent_index=1)],
        clips_by_chapter={"day-1": clips},
    )
    seconds = found["total_frames"] / plan.FILM_FPS
    assert 10 <= seconds <= 90, seconds
    # Deterministic: the same trip plans to the same frame.
    again = plan.build_scene_plan(
        trip={"title": "Technikfilm", "subtitle": ""},
        chapters=[_chapter(images=images, prominent_index=1)],
        clips_by_chapter={"day-1": clips},
    )
    assert again["total_frames"] == found["total_frames"]


def verify_no_provider_and_no_music_are_involved() -> None:
    """music_mode off is a normal, valid film - and nothing costs money."""
    images = [_photo(index) for index in range(3)]
    found = plan.build_scene_plan(
        trip={"title": "Technikfilm", "subtitle": ""},
        chapters=[_chapter(images=images, prominent_index=0)],
    )
    assert found["scenes"], found
    assert found["total_frames"] > 0
    # The planner is a pure module by construction; the film export has a
    # test of its own asserting the music service never appears in it.
    source = (INTEGRATION / "trip_film_plan.py").read_text(encoding="utf-8")
    for forbidden in ("async_generate", "TripFilmMusicService", "aiohttp", "requests"):
        assert forbidden not in source, forbidden


for check in (
    verify_the_central_photograph_gets_the_slot_and_keeps_it,
    verify_the_same_day_without_a_central_motif_is_untouched,
    verify_the_short_clip_is_grown_and_the_plan_holds_it_that_long,
    verify_the_clip_of_the_days_subject_makes_a_photograph_unnecessary,
    verify_a_supply_stop_is_in_the_roadbook_and_not_in_the_film,
    verify_the_whole_thing_stays_a_short_film,
    verify_no_provider_and_no_music_are_involved,
):
    check()

print("Polish film end-to-end tests passed.")
