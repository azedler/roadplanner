"""Importance as a ceiling, quality as the demand.

The old rule handed each day a fixed number by importance. It failed in
both directions at once: a major highlight with seven mediocre pictures
was padded to fourteen, a normal day with twelve strong ones was cut to
six, and on a real trip 109 of 260 places in the film went unused while
122 curated pictures waited for one.

Every check here is one of those failures, written as the behaviour that
must replace it. The analyses use the field names the curation ACTUALLY
stores - `visual_quality`, `story_value`, `motifs`, `shows` - because a
test that invents a plausible shape agrees with the bug instead of
catching it, which has happened in this project before.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_allocation_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

alloc = importlib.import_module(f"{_PACKAGE}.film_photo_allocation")
curation = importlib.import_module(f"{_PACKAGE}.media_curation")

BAR = 11.0


def _analysis(*, story: int, quality: int, motifs=(), shows=()):
    """The real stored shape. story_value counts double in the score."""
    return {
        "story_value": story,
        "visual_quality": quality,
        "motifs": list(motifs),
        "shows": list(shows),
    }


STRONG = _analysis(story=5, quality=4)   # 5*2 + 4 = 14
WEAK = _analysis(story=2, quality=3)     # 2*2 + 3 = 7


def _day(count, analysis, **extra):
    """A day as `allocate_trip` takes it; `_solo` strips the trip-only key."""
    ids = [f"m{index}" for index in range(count)]
    day = {
        "chapter_id": extra.pop("chapter_id", "day-1"),
        "curated": ids,
        "analyses": {media_id: dict(analysis) for media_id in ids},
    }
    day.update(extra)
    return day


def _solo(day):
    """One day for `earned_for_day`, which has no notion of a trip."""
    return {key: value for key, value in day.items() if key != "chapter_id"}


def verify_the_scale_is_what_the_threshold_is_read_against() -> None:
    """0-20, not 0-5. A bar picked for the wrong scale gates nothing."""
    assert curation.semantic_score(STRONG) == 14.0, curation.semantic_score(STRONG)
    assert curation.semantic_score(WEAK) == 7.0
    perfect = _analysis(story=5, quality=5)
    perfect.update({"emotion": 5, "uniqueness": 5})
    assert curation.semantic_score(perfect) == 20.0, curation.semantic_score(perfect)


def verify_a_mediocre_major_highlight_is_not_padded() -> None:
    """The first half of the old failure: a ceiling is not a target."""
    day = _day(14, WEAK, importance="major_highlight")
    # Three of the fourteen are actually good.
    for media_id in ("m0", "m1", "m2"):
        day["analyses"][media_id] = dict(STRONG)
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert result["cap"] == 18, result["cap"]
    assert len(result["media_ids"]) == 3, result["media_ids"]


def verify_a_strong_normal_day_gets_more_than_the_old_six() -> None:
    """The other half: good material is no longer cut to a quota."""
    day = _day(12, STRONG, importance="normal")
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert result["cap"] == 10
    assert len(result["media_ids"]) == 10, result["media_ids"]


def verify_a_transition_day_may_exceed_the_old_three() -> None:
    """A day with strong material gets up to six - and not its story rank."""
    day = _day(9, STRONG, importance="transition")
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert result["cap"] == 6
    assert len(result["media_ids"]) == 6, result["media_ids"]


def verify_a_series_contributes_at_most_two_and_its_best() -> None:
    """Six attempts at a moose are one moment, not six pictures."""
    day = _day(6, STRONG, importance="highlight")
    day["series_by_media"] = {media_id: "burst" for media_id in day["curated"]}
    # Make the ranking unambiguous: m4 and m5 are the strongest.
    day["analyses"]["m4"] = _analysis(story=5, quality=5)
    day["analyses"]["m5"] = _analysis(story=5, quality=5)
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert len(result["media_ids"]) == 2, result["media_ids"]
    assert set(result["media_ids"]) == {"m4", "m5"}, result["media_ids"]


def verify_a_pinned_third_series_image_survives() -> None:
    """A decision by hand outranks the burst cap."""
    day = _day(6, STRONG, importance="highlight")
    day["series_by_media"] = {media_id: "burst" for media_id in day["curated"]}
    day["pinned"] = ["m0"]
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert "m0" in result["media_ids"]
    assert result["reasons"]["m0"] == alloc.REASON_PINNED
    assert len(result["media_ids"]) == 3, result["media_ids"]


def verify_an_excluded_image_is_never_selected() -> None:
    """Not by quality, not by coverage, not by pin."""
    day = _day(4, STRONG, importance="normal")
    day["excluded"] = ["m0"]
    day["pinned"] = ["m0"]
    day["must_cover"] = ["elch"]
    day["analyses"]["m0"] = _analysis(story=5, quality=5, shows=["elch"])
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert "m0" not in result["media_ids"], result["media_ids"]


def verify_a_coverage_exception_rescues_an_unmet_motif() -> None:
    """The only picture of the day's subject, just below the bar."""
    day = _day(4, STRONG, importance="normal")
    day["must_cover"] = ["wolfsschanze"]
    day["analyses"]["m3"] = _analysis(story=2, quality=2, shows=["wolfsschanze"])
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert "m3" in result["media_ids"], result["media_ids"]
    assert result["reasons"]["m3"] == alloc.REASON_COVERAGE
    assert result["coverage_exceptions"] == ["m3"]


def verify_no_exception_when_the_motif_is_already_covered() -> None:
    """Not a general softening of the bar."""
    day = _day(4, STRONG, importance="normal")
    day["must_cover"] = ["elch"]
    day["analyses"]["m0"] = _analysis(story=5, quality=4, shows=["elch"])
    day["analyses"]["m3"] = _analysis(story=1, quality=1, shows=["elch"])
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert "m3" not in result["media_ids"], result["media_ids"]
    assert result["coverage_exceptions"] == []


def verify_pins_survive_the_daily_cap_and_push_out_automatics() -> None:
    """A cap reduces the automatic picks, never a decision by hand."""
    day = _day(9, STRONG, importance="transition")  # cap 6
    day["pinned"] = ["m0", "m1", "m2", "m3", "m4", "m5", "m6"]
    result = alloc.earned_for_day(**_solo(day), threshold=BAR)
    assert len(result["pinned_kept"]) == 7, result["pinned_kept"]
    assert all(
        result["reasons"][media_id] == alloc.REASON_PINNED
        for media_id in result["media_ids"]
    ), result["reasons"]


def verify_a_film_under_the_cap_is_not_padded() -> None:
    """260 is a ceiling, not a target: free places stay free."""
    days = [_day(3, STRONG, chapter_id=f"d{index}", importance="major_highlight")
            for index in range(4)]
    result = alloc.allocate_trip(days, threshold=BAR, global_cap=260)
    assert result["total"] == 12, result["total"]
    assert result["unused_budget"] == 248
    assert result["globally_removed"] == []


def verify_a_film_over_the_cap_is_reduced_globally_by_merit() -> None:
    """Not back to per-day quotas - that is the rule being replaced."""
    days = []
    for index in range(30):
        day = _day(10, STRONG, chapter_id=f"d{index}", importance="normal")
        days.append(day)
    result = alloc.allocate_trip(days, threshold=BAR, global_cap=260)
    assert result["total"] == 260, result["total"]
    assert len(result["globally_removed"]) == 40


def verify_the_global_reduction_protects_pins_and_coverage() -> None:
    """What may never be dropped to make room."""
    days = []
    for index in range(30):
        day = _day(10, STRONG, chapter_id=f"d{index}", importance="normal")
        if index == 0:
            day["pinned"] = ["m0"]
            day["must_cover"] = ["elch"]
            day["analyses"]["m9"] = _analysis(story=1, quality=1, shows=["elch"])
        days.append(day)
    result = alloc.allocate_trip(days, threshold=BAR, global_cap=200)
    first = result["days"]["d0"]
    assert "m0" in first["media_ids"], first
    assert first["reasons"]["m0"] == alloc.REASON_PINNED
    assert "m9" in first["media_ids"], "eine Coverage-Ausnahme wird nicht weggekürzt"
    assert result["total"] == 200


def verify_importance_never_becomes_a_fixed_allocation_again() -> None:
    """The whole point: same material, different rank, same demand.

    Two days with identical pictures differ only in what the story says
    they were. Under the old rule that alone changed the count. Now it
    only changes the ceiling - which neither day reaches.
    """
    plain = alloc.earned_for_day(**_solo(_day(4, STRONG, importance="normal")), threshold=BAR)
    grand = alloc.earned_for_day(
        **_solo(_day(4, STRONG, importance="major_highlight")), threshold=BAR
    )
    assert len(plain["media_ids"]) == len(grand["media_ids"]) == 4
    assert plain["cap"] != grand["cap"]


def verify_visual_richness_never_edits_the_story() -> None:
    """Derived for the panel, and one-way on purpose."""
    rich = alloc.earned_for_day(**_solo(_day(14, STRONG, importance="transition")), threshold=BAR)
    assert alloc.visual_richness(rich) == "high"
    # The label says the material is rich; the day is still a transition.
    assert rich["importance"] == "transition"
    assert rich["cap"] == alloc.PHOTO_CAPS_BY_IMPORTANCE["transition"]


for check in (
    verify_the_scale_is_what_the_threshold_is_read_against,
    verify_a_mediocre_major_highlight_is_not_padded,
    verify_a_strong_normal_day_gets_more_than_the_old_six,
    verify_a_transition_day_may_exceed_the_old_three,
    verify_a_series_contributes_at_most_two_and_its_best,
    verify_a_pinned_third_series_image_survives,
    verify_an_excluded_image_is_never_selected,
    verify_a_coverage_exception_rescues_an_unmet_motif,
    verify_no_exception_when_the_motif_is_already_covered,
    verify_pins_survive_the_daily_cap_and_push_out_automatics,
    verify_a_film_under_the_cap_is_not_padded,
    verify_a_film_over_the_cap_is_reduced_globally_by_merit,
    verify_the_global_reduction_protects_pins_and_coverage,
    verify_importance_never_becomes_a_fixed_allocation_again,
    verify_visual_richness_never_edits_the_story,
):
    check()

print("Film photo allocation tests passed.")
