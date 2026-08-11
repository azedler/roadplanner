"""A slot held open before packing, not a picture moved afterwards.

The fault this file is about survived one fix already, which is why it
gets a file of its own.

The day a trip was partly about had its central motif curated correctly,
scored correctly, and moved to the front correctly - and still appeared
only as a quarter-size collage tile, while a side motif held the screen
alone. Coverage was green. Prominence said "supporting". The reordering
ran. Nothing changed.

The reason is one branch in the scene grammar:

    if style == "collage":
        for position in range(0, len(indices), GROUP_SIZE):
            shots.append((SCENE_COLLAGE, ...))
        return shots

A day whose visual style is "collage" has **no prominent slot at all**.
Every picture goes into a tile, including the one moved to the front. So
on exactly the kind of day that has enough material to be a collage day,
there was nothing to be promoted INTO.

So the slot is now RESERVED before the rest is packed. These checks are
about that order of operations, and about the things it must not break:
the reserved picture may not also appear in a collage, a pinned hero
outranks it, an excluded picture never returns, and a day with nothing
central is packed exactly as before.

No place name appears here or in the code. The day that exposed this is a
test case, never a rule.
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

_PACKAGE = "roadplanner_reservation_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

prom = importlib.import_module(f"{_PACKAGE}.visual_prominence")
plan = importlib.import_module(f"{_PACKAGE}.trip_film_plan")
builder = importlib.import_module(f"{_PACKAGE}.story_context_builder")
analysis = importlib.import_module(f"{_PACKAGE}.video_analysis")

MOTIF = "bunker"


def _photo(*, story=3, quality=3, shows=()):
    return {
        "story_value": story,
        "visual_quality": quality,
        "motifs": [],
        "shows": list(shows),
    }


def _clip(*, subject="", story=4, quality=4, role=analysis.ROLE_AMBIENT, media_id="v1"):
    return {
        "media_id": media_id,
        "subject": subject,
        "role": role,
        "story_value": story,
        "visual_quality": quality,
        "start_seconds": 1.0,
        "end_seconds": 5.0,
    }


def _shape(style, count, reserved=None):
    return plan._shot_list(style, count, reserved)


# --- the fault itself ---------------------------------------------------


def verify_a_collage_day_had_no_prominent_slot_at_all() -> None:
    """The mechanism, stated as a fact about the old behaviour.

    Not a regression guard - a record of why reordering could not work,
    so nobody tries that fix a third time.
    """
    shots = _shape("collage", 8)
    assert all(kind == plan.SCENE_COLLAGE for kind, _, _ in shots), shots


def verify_a_reservation_creates_the_slot_a_collage_day_lacks() -> None:
    """Same day, same style, one reserved picture."""
    shots = _shape("collage", 8, reserved=0)
    assert shots[0][0] == plan.SCENE_HERO, shots
    assert shots[0][1] == [0], shots


def verify_the_reserved_picture_is_not_also_in_a_collage() -> None:
    """Reserved means taken out of the packing list, not copied out of it."""
    for style in ("collage", "normal", "hero", "compact", "map_focus"):
        for count in range(2, 15):
            for reserved in (0, 1, count - 1):
                shots = _shape(style, count, reserved=reserved)
                elsewhere = [
                    position
                    for kind, positions, _ in shots
                    for position in positions
                    if kind != plan.SCENE_HERO or positions != [reserved]
                ]
                assert elsewhere.count(reserved) == 0, (style, count, reserved, shots)


def verify_every_picture_is_still_shown_exactly_once() -> None:
    """A reservation may not lose a picture, or gain one."""
    for style in ("collage", "normal", "hero", "compact", "map_focus"):
        for count in range(1, 15):
            for reserved in (None, 0, count // 2, count - 1):
                shots = _shape(style, count, reserved=reserved)
                shown = sorted(
                    position for _, positions, _ in shots for position in positions
                )
                assert shown == list(range(count)), (style, count, reserved, shown)


def verify_a_day_without_a_reservation_is_packed_exactly_as_before() -> None:
    """The whole change has to be invisible on an ordinary day."""
    for style in ("collage", "normal", "hero", "compact", "map_focus"):
        for count in range(1, 15):
            assert _shape(style, count, reserved=None) == _shape(style, count), (
                style,
                count,
            )


def verify_an_out_of_range_reservation_is_ignored() -> None:
    """A stale index must not invent a scene or drop a picture."""
    for reserved in (-1, 99):
        assert _shape("collage", 5, reserved=reserved) == _shape("collage", 5)


# --- what gets reserved -------------------------------------------------


def verify_the_best_central_photograph_is_reserved() -> None:
    media_ids = ["side", "b1", "b2"]
    analyses = {
        "side": _photo(story=3, quality=4),
        "b1": _photo(story=5, quality=4, shows=[MOTIF]),
        "b2": _photo(story=2, quality=2, shows=[MOTIF]),
    }
    found = prom.reserve_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses
    )
    assert found["reserved"] == "b1", found
    assert found["order"][0] == "b1", found
    # A reordering and never a selection.
    assert sorted(found["order"]) == sorted(media_ids)


def verify_a_clip_that_already_opens_the_day_reserves_nothing() -> None:
    """No photograph is displaced to fix something that is not broken."""
    media_ids = ["side", "b1"]
    analyses = {"side": _photo(), "b1": _photo(shows=[MOTIF])}
    clips = [_clip(subject=f"Ein {MOTIF} im Wald", role=analysis.ROLE_HERO)]
    found = prom.reserve_for_prominence(
        media_ids, must_cover=[MOTIF], analyses=analyses, clips=clips
    )
    assert found["reserved"] == "", found
    assert found["order"] == media_ids


def verify_a_video_is_better_evidence_and_no_photograph_is_reserved() -> None:
    """Medium-neutral: a strong clip wins, and then nothing is displaced."""
    found = prom.reserve_for_prominence(
        ["other", "weak"],
        must_cover=[MOTIF],
        analyses={"other": _photo(), "weak": _photo(story=1, quality=1, shows=[MOTIF])},
        clips=[_clip(subject="anderes"), _clip(subject=f"{MOTIF} von innen", story=5, quality=5)],
    )
    assert found["state"]["best"][MOTIF]["media_type"] == "video", found
    assert found["reserved"] == "", found


def verify_an_absent_motif_invents_nothing() -> None:
    found = prom.reserve_for_prominence(
        ["a", "b"], must_cover=[MOTIF], analyses={"a": _photo(), "b": _photo()}
    )
    assert found["reserved"] == ""
    assert found["order"] == ["a", "b"]


def verify_several_central_motifs_reserve_one_slot_between_them() -> None:
    """A day has one opening. Two reservations would only demote the first."""
    media_ids = ["filler", "a1", "b1"]
    analyses = {
        "filler": _photo(),
        "a1": _photo(story=5, quality=5, shows=["alpha"]),
        "b1": _photo(story=4, quality=4, shows=["beta"]),
    }
    found = prom.reserve_for_prominence(
        media_ids, must_cover=["alpha", "beta"], analyses=analyses
    )
    assert found["reserved"] == "a1", found
    assert prom.PROMINENT_SLOTS == 1


def verify_it_is_deterministic() -> None:
    """Remotion draws frames in parallel tabs; nothing here may wobble."""
    media_ids = ["a", "b1", "c", "b2"]
    analyses = {
        "a": _photo(),
        "c": _photo(),
        "b1": _photo(story=4, quality=4, shows=[MOTIF]),
        "b2": _photo(story=4, quality=4, shows=[MOTIF]),
    }
    first = prom.reserve_for_prominence(
        list(media_ids), must_cover=[MOTIF], analyses=analyses
    )
    for _ in range(5):
        again = prom.reserve_for_prominence(
            list(media_ids), must_cover=[MOTIF], analyses=analyses
        )
        assert again["order"] == first["order"] and again["reserved"] == first["reserved"]


# --- who outranks whom --------------------------------------------------


def verify_a_pinned_hero_outranks_the_automatic_reservation() -> None:
    """A person's choice about their own holiday beats any score."""
    source = (INTEGRATION / "story_context_builder.py").read_text(encoding="utf-8")
    assert "if not hero_id or hero_id not in shown_ids:" in source, (
        "die Reservierung überschreibt einen von Hand gesetzten Hero"
    )
    assert "hero_media_id" in source


def verify_an_excluded_picture_cannot_be_reserved() -> None:
    """Exclusion happens in the allocation, before any of this runs."""
    entries = [
        {
            "chapter_id": "day-1",
            "importance": "highlight",
            "curated": ["keep", "banned"],
            "analyses": {
                "keep": _photo(story=4, quality=4),
                "banned": _photo(story=5, quality=5, shows=[MOTIF]),
            },
            "excluded": ["banned"],
            "must_cover": [MOTIF],
        }
    ]
    alloc = importlib.import_module(f"{_PACKAGE}.film_photo_allocation")
    result = alloc.allocate_trip(entries)
    chosen = result["days"]["day-1"]["media_ids"]
    assert "banned" not in chosen, chosen
    found = prom.reserve_for_prominence(
        list(chosen), must_cover=[MOTIF], analyses=entries[0]["analyses"]
    )
    assert found["reserved"] != "banned", found


def verify_no_place_or_brand_is_named_in_the_logic() -> None:
    """The day that exposed this is a test case, never a rule.

    Checked on CODE with comments stripped, deliberately. The project
    rule forbids a place name in a RULE - something that behaves
    differently because of where it is. A comment recording which live
    report a floor came from is the opposite of that: it is why the
    number is what it is, and removing it would make the code less
    truthful, not more compliant. `trip_film_plan.py` carries exactly
    such a comment, and it should stay.
    """
    for name in ("visual_prominence.py", "trip_film_plan.py"):
        body = (INTEGRATION / name).read_text(encoding="utf-8")
        code = "\n".join(
            line.split("#")[0] if not line.strip().startswith('"') else ""
            for line in body.splitlines()
            if not line.strip().startswith("#")
        ).casefold()
        for banned in ("wolfsschanze", "masuren", "gierloz", "ketrzyn"):
            assert banned not in code, (name, banned)


for check in (
    verify_a_collage_day_had_no_prominent_slot_at_all,
    verify_a_reservation_creates_the_slot_a_collage_day_lacks,
    verify_the_reserved_picture_is_not_also_in_a_collage,
    verify_every_picture_is_still_shown_exactly_once,
    verify_a_day_without_a_reservation_is_packed_exactly_as_before,
    verify_an_out_of_range_reservation_is_ignored,
    verify_the_best_central_photograph_is_reserved,
    verify_a_clip_that_already_opens_the_day_reserves_nothing,
    verify_a_video_is_better_evidence_and_no_photograph_is_reserved,
    verify_an_absent_motif_invents_nothing,
    verify_several_central_motifs_reserve_one_slot_between_them,
    verify_it_is_deterministic,
    verify_a_pinned_hero_outranks_the_automatic_reservation,
    verify_an_excluded_picture_cannot_be_reserved,
    verify_no_place_or_brand_is_named_in_the_logic,
):
    check()

print("Prominence reservation tests passed.")
