"""The allocation only counts if the film actually asks it.

A rule that lives in a module nobody calls is a description of a system
that is not running. These checks are about the WIRING: that the decision
is made once, where the evidence is, and that no ceiling further down the
line silently cuts it back.

Two of them exist because this project has shipped the same bug four
times - one number written in two files, one side raised. They read both
files and compare, which is the only form of that test that works.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

# The context builder imports HomeAssistant for typing only.
if "homeassistant" not in sys.modules:
    _ha = types.ModuleType("homeassistant")
    _core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - stand-in
        """Stand-in for the real class."""

    _core.HomeAssistant = HomeAssistant
    sys.modules.update(
        {
            "homeassistant": _ha,
            "homeassistant.core": _core,
            "homeassistant.helpers": types.ModuleType("homeassistant.helpers"),
        }
    )

_PACKAGE = "roadplanner_wiring_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

alloc = importlib.import_module(f"{_PACKAGE}.film_photo_allocation")
manifest = importlib.import_module(f"{_PACKAGE}.travel_story_manifest")
builder = importlib.import_module(f"{_PACKAGE}.story_context_builder")
plan = importlib.import_module(f"{_PACKAGE}.trip_film_plan")

EXPORT = (INTEGRATION / "trip_film_export.py").read_text(encoding="utf-8")
CEILING = max(alloc.PHOTO_CAPS_BY_IMPORTANCE.values())


def verify_no_schema_ceiling_cuts_the_biggest_day() -> None:
    """Both files, read and compared - not two numbers typed twice.

    A major highlight may show 18 pictures. The manifest schema used to
    stop at 16 and the context builder at 14, so the two best pictures of
    the most important day of a trip would have been dropped with nothing
    reporting it.
    """
    assert manifest.MAX_MEDIA_PER_CHAPTER >= CEILING, manifest.MAX_MEDIA_PER_CHAPTER
    assert builder.MEDIA_PER_CHAPTER >= CEILING, builder.MEDIA_PER_CHAPTER


def verify_the_export_has_no_allocation_of_its_own() -> None:
    """One rule, in one place. The other copy is how a film diverges."""
    assert "allocate_photos" not in EXPORT
    assert "MAX_PHOTOS_PER_CHAPTER" not in EXPORT
    # It counts what the manifest carries instead.
    assert "def _film_budget" in EXPORT


def verify_a_fuller_day_buys_time_rather_than_density() -> None:
    """The finding that made this whole change safe to ship.

    A transition day ran exactly the same length with three pictures and
    with eight: everything past the first group was absorbed into it. So
    giving a well-photographed day more pictures did not give it more
    film - it gave the same seconds twice the density, which is the
    opposite of the complaint the floors were raised for.
    """
    chapter = {
        "chapter_id": "c",
        "title": "Ein Tag",
        "importance": "transition",
        "visual_style": "normal",
    }

    def measure(count: int) -> tuple[int, int]:
        scenes = plan._chapter_scenes(
            chapter, photo_count=count, index=0, has_map=True
        )
        frames = sum(int(scene["frames"]) for scene in scenes)
        largest = max(
            [len(scene["photos"]) for scene in scenes if scene["type"] == "collage"]
            or [0]
        )
        return frames, largest

    short_frames, _ = measure(3)
    long_frames, largest = measure(8)
    assert long_frames > short_frames, (short_frames, long_frames)
    # And no group is fuller than the floor was designed to hold.
    assert largest <= plan.GROUP_SIZE, largest


def verify_no_group_ever_exceeds_the_readable_size() -> None:
    """Across every importance and every plausible day."""
    for importance in alloc.PHOTO_CAPS_BY_IMPORTANCE:
        for count in range(1, CEILING + 1):
            scenes = plan._chapter_scenes(
                {
                    "chapter_id": "c",
                    "title": "Ein Tag",
                    "importance": importance,
                    "visual_style": "normal",
                },
                photo_count=count,
                index=0,
                has_map=True,
            )
            for scene in scenes:
                if scene["type"] == "collage":
                    assert len(scene["photos"]) <= plan.GROUP_SIZE, (
                        importance,
                        count,
                        len(scene["photos"]),
                    )
            # Every picture the day was given is still on screen somewhere.
            shown = sorted(
                index for scene in scenes for index in scene.get("photos") or []
            )
            assert shown == list(range(count)), (importance, count, shown)


def verify_a_day_without_analyses_keeps_its_whole_curation() -> None:
    """A bar applied to scores that do not exist would empty a chapter."""
    days = [{"id": "day-1"}]
    curations = {"day-1": {"media_ids": ["m0", "m1"], "analyses": {}}}
    assert builder._film_allocation(days, {}, curations, {}) == {}


def verify_the_allocation_reads_the_curation_and_the_edit() -> None:
    """Importance comes from the editing pass, scores from the curation."""
    strong = {"story_value": 5, "visual_quality": 4, "motifs": [], "shows": []}
    weak = {"story_value": 1, "visual_quality": 1, "motifs": [], "shows": []}
    media_ids = [f"m{index}" for index in range(9)]
    curations = {
        "day-1": {
            "media_ids": media_ids,
            "analyses": {
                media_id: dict(strong if index < 7 else weak)
                for index, media_id in enumerate(media_ids)
            },
        }
    }
    found = builder._film_allocation(
        [{"id": "day-1"}], {}, curations, {"day-1": {"importance": "transition"}}
    )
    # Seven earn a place; the transition ceiling of six is what stops it,
    # and the two weak ones were never in the running.
    assert len(found["day-1"]) == alloc.PHOTO_CAPS_BY_IMPORTANCE["transition"]
    assert set(found["day-1"]) <= set(media_ids[:7])


for check in (
    verify_no_schema_ceiling_cuts_the_biggest_day,
    verify_the_export_has_no_allocation_of_its_own,
    verify_a_fuller_day_buys_time_rather_than_density,
    verify_no_group_ever_exceeds_the_readable_size,
    verify_a_day_without_analyses_keeps_its_whole_curation,
    verify_the_allocation_reads_the_curation_and_the_edit,
):
    check()

print("Film allocation wiring tests passed.")
