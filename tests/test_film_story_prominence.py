"""Three faults the first accepted film showed, as the behaviour that replaces them.

All three were found in `reise(9)` - a real, watched film, not a fixture:

1. a fast food stop naming a day ("Von MAX Burgers hinauf zur Kungsgrottan"),
2. the same photograph able to reach two chapters,
3. two frames of one burst side by side, reading as a stutter.

None of them is fixed by naming the offender. A brand in a rule would
survive exactly until the next brand, so what is checked here is the
generic mechanism: the editor is TOLD what a stop was, its cache key can
SEE that, a photograph belongs to one day, and a burst is spread.
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

_PACKAGE = "roadplanner_prominence_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

director = importlib.import_module(f"{_PACKAGE}.story_director")
manifest = importlib.import_module(f"{_PACKAGE}.travel_story_manifest")
builder = importlib.import_module(f"{_PACKAGE}.story_context_builder")
alloc = importlib.import_module(f"{_PACKAGE}.film_photo_allocation")
relevance = importlib.import_module(f"{_PACKAGE}.stop_relevance")

DIRECTOR_SOURCE = (INTEGRATION / "story_director.py").read_text(encoding="utf-8")


def _context(days, media, curations=None):
    return {
        "trip_id": "t",
        "trip": {"title": "Reise"},
        "revision": 1,
        "crew": None,
        "direction": None,
        "days": days,
        "media": media,
        "day_curations": curations or {},
    }


# --- 1. a functional stop must not name a day ---------------------------


def verify_the_editor_is_told_when_a_stop_was_only_a_supply_stop() -> None:
    """The brief is the only thing the model can read.

    It used to say "MAX Burgers [Start, restaurant, 09:00]" - a named
    place, marked as the beginning of the day, with nothing to say it was
    a sandwich on the way. The model wrote the title that description
    invites. It was not wrong; it was uninformed.
    """
    burger = {"name": "Irgendein Schnellrestaurant", "kind": "fast_food"}
    assert relevance.is_functional(burger), "die Klassifikation kennt den Typ nicht"
    line = director._brief_stop(burger, "Start")
    assert "Versorgungsstopp" in line, line

    # And an ordinary place is not demoted by the same mechanism.
    lake = {"name": "Ein See", "kind": "nature"}
    assert "Versorgungsstopp" not in director._brief_stop(lake, "Tagesziel")


def verify_the_rule_is_in_the_instructions_and_names_no_brand() -> None:
    """Told once, generically, where the titles are actually written."""
    assert "Versorgungsstopp" in director.CHAPTER_SYSTEM_PROMPT
    assert "Titel" in director.CHAPTER_SYSTEM_PROMPT
    for brand in ("max burgers", "mcdonald", "burger king", "shell", "aral", "lidl"):
        assert brand not in DIRECTOR_SOURCE.casefold(), brand


def verify_the_cache_key_can_see_what_a_stop_means() -> None:
    """Otherwise no fix could ever reach a stored direction.

    The context hash carried stop_id, name and kind. A better
    classification moves none of the three, so the cached edit that put a
    burger stop in a title would have survived every improvement to the
    thing that produced it - for ever, and for free, which is worse.
    """
    def hash_with(kind):
        return manifest.story_context_hash(
            {
                "trip": {"title": "Reise"},
                "facts": {},
                "chapters": [
                    {
                        "chapter_id": "day-1",
                        "index": 0,
                        "date": "2026-07-01",
                        "base": {"title": "Tag"},
                        "facts": {},
                        "stops": [{"stop_id": "s1", "name": "Ein Halt", "kind": kind}],
                        "media": [],
                    }
                ],
            }
        )

    assert hash_with("fast_food") != hash_with("nature"), (
        "eine Änderung der Stopp-Bedeutung lässt den Cache-Schlüssel unberührt"
    )


# --- 2. one photograph belongs to one day -------------------------------


def verify_a_photograph_cannot_reach_two_chapters() -> None:
    """It could: a photo linked to day A whose stop also appears in day B.

    Both days collected it, so the same picture was in the film twice,
    with nothing reporting it. The day it is LINKED to wins, because that
    is the assignment somebody made.
    """
    days = [
        {"id": "day-1", "title": "A", "stops": [{"id": "s1", "name": "Ort"}]},
        {"id": "day-2", "title": "B", "stops": [{"id": "s1", "name": "Ort"}]},
    ]
    media = [
        {
            "id": "m1",
            "linked_day_id": "day-1",
            "linked_stop_id": "s1",
            "taken_at": "2026-07-01T10:00:00Z",
        }
    ]
    chapters = builder._build(_context(days, media))["chapters"]
    used = [item["media_id"] for chapter in chapters for item in chapter["media"]]
    assert used == ["m1"], used


def verify_a_photograph_without_a_day_is_claimed_once() -> None:
    """The remaining path: found only through a stop two days share."""
    days = [
        {"id": "day-1", "title": "A", "stops": [{"id": "s1", "name": "Ort"}]},
        {"id": "day-2", "title": "B", "stops": [{"id": "s1", "name": "Ort"}]},
    ]
    media = [{"id": "m2", "linked_stop_id": "s1", "taken_at": "2026-07-01T10:00:00Z"}]
    chapters = builder._build(_context(days, media))["chapters"]
    used = [item["media_id"] for chapter in chapters for item in chapter["media"]]
    assert used == ["m2"], used


# --- 3. a burst is spread, never thinned --------------------------------


def verify_two_frames_of_one_burst_are_not_adjacent() -> None:
    """The series cap said how many. This says where."""
    series = {"a1": "A", "a2": "A", "b1": "B", "c1": "C"}
    ordered = alloc.spread_series(["a1", "a2", "b1", "c1"], series)
    groups = [series[media_id] for media_id in ordered]
    assert all(
        first != second for first, second in zip(groups, groups[1:])
    ), groups


def verify_spreading_never_removes_a_picture() -> None:
    """A reordering, not a second selection."""
    ids = ["a1", "a2", "b1", "b2", "c1"]
    series = {"a1": "A", "a2": "A", "b1": "B", "b2": "B", "c1": "C"}
    assert sorted(alloc.spread_series(ids, series)) == sorted(ids)
    # A day that is genuinely one long burst keeps every picture and its
    # order, rather than being distorted to hide what it was.
    only = ["a1", "a2", "a3"]
    assert alloc.spread_series(only, {media: "A" for media in only}) == only


def verify_spreading_is_deterministic() -> None:
    """The renderer draws frames in parallel; nothing here may wobble."""
    ids = ["a1", "b1", "a2", "c1", "b2"]
    series = {"a1": "A", "a2": "A", "b1": "B", "b2": "B", "c1": "C"}
    first = alloc.spread_series(list(ids), series)
    assert all(alloc.spread_series(list(ids), series) == first for _ in range(5))


for check in (
    verify_the_editor_is_told_when_a_stop_was_only_a_supply_stop,
    verify_the_rule_is_in_the_instructions_and_names_no_brand,
    verify_the_cache_key_can_see_what_a_stop_means,
    verify_a_photograph_cannot_reach_two_chapters,
    verify_a_photograph_without_a_day_is_claimed_once,
    verify_two_frames_of_one_burst_are_not_adjacent,
    verify_spreading_never_removes_a_picture,
    verify_spreading_is_deterministic,
):
    check()

print("Film story prominence tests passed.")
