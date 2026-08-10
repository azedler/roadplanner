"""The simulation has to be readable, honest, and free.

Readable, because its whole purpose is to be pasted back into a
conversation that picks a threshold. Honest, because a report that
quietly falls back to a default where data is missing would justify a
number with its own assumption. Free, because a person presses this
button to AVOID spending anything.

The analyses use the field names the curation actually stores, for the
same reason as everywhere else in this project: a test that invents a
plausible shape agrees with the bug.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_simulation_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

sim = importlib.import_module(f"{_PACKAGE}.film_allocation_simulation")
alloc = importlib.import_module(f"{_PACKAGE}.film_photo_allocation")

MODULE = (ROOT / "custom_components" / "roadplanner_mcp" / "film_allocation_simulation.py").read_text(
    encoding="utf-8"
)


def _analysis(*, story: int, quality: int, shows=()):
    return {
        "story_value": story,
        "visual_quality": quality,
        "motifs": [],
        "shows": list(shows),
    }


STRONG = _analysis(story=5, quality=4)   # 14
MIDDLING = _analysis(story=3, quality=4)  # 10
WEAK = _analysis(story=2, quality=3)      # 7


def _day(number, *, pool_count, curated_count, analysis, importance="normal", old=None):
    ids = [f"d{number}m{index}" for index in range(pool_count)]
    return {
        "chapter_id": f"day-{number}",
        "day_number": number,
        "title": f"Tag {number}",
        "importance": importance,
        "pool": ids,
        "curated": ids[:curated_count],
        "analyses": {media_id: dict(analysis) for media_id in ids},
        "old_selected": old,
    }


def verify_the_distribution_is_read_off_the_pool_not_the_selection() -> None:
    """Every analysed picture counts, including the ones not selected.

    The distribution is what a threshold is chosen against. Reading it
    off the curated fourteen would report the shape of an already-filtered
    set and put the bar wherever that filter happened to end.
    """
    result = sim.simulate([_day(1, pool_count=20, curated_count=14, analysis=STRONG)])
    assert result["distribution"]["count"] == 20, result["distribution"]
    assert result["distribution"]["median"] == 14.0


def verify_both_sources_are_reported_because_the_curation_also_caps() -> None:
    """A major highlight cannot reach 18 out of a selection of 14.

    Which of the two limits actually binds decides whether changing the
    allocation would change anything at all, so the report may not
    quietly pick one.
    """
    day = _day(
        1, pool_count=20, curated_count=14, analysis=STRONG, importance="major_highlight"
    )
    result = sim.simulate([day], thresholds=(11.0,), reference=11.0)
    entry = result["candidates"][0]
    assert entry["total_from_curated"] == 14, entry
    assert entry["total_from_pool"] == 18, entry
    row = result["days"][0]
    assert row["new_from_curated"] == 14 and row["new_from_pool"] == 18, row


def verify_a_higher_bar_never_yields_more_pictures() -> None:
    """Monotonic, or the numbers cannot be compared at all."""
    days = [
        _day(1, pool_count=20, curated_count=14, analysis=STRONG),
        _day(2, pool_count=20, curated_count=14, analysis=MIDDLING),
        _day(3, pool_count=20, curated_count=14, analysis=WEAK),
    ]
    result = sim.simulate(days)
    totals = [entry["total_from_pool"] for entry in result["candidates"]]
    assert totals == sorted(totals, reverse=True), totals
    assert totals[0] > totals[-1], totals


def verify_an_unknown_old_count_stays_unknown() -> None:
    """Without a manifest there is no film to compare against.

    Printing the curation's own number under the heading "im Film" is
    exactly the class of bug this project keeps repeating: an absent
    answer rendered as a state.
    """
    result = sim.simulate([_day(1, pool_count=6, curated_count=6, analysis=STRONG, old=None)])
    assert result["days"][0]["old_selected"] is None
    assert result["old_total"] == 0
    report = sim.format_report(result)
    assert "|           ? |" in report, report


def verify_the_report_names_the_scale_and_the_caps() -> None:
    """A number without its scale is what made 2.4 look like a threshold."""
    result = sim.simulate([_day(1, pool_count=6, curated_count=6, analysis=STRONG, old=3)])
    report = sim.format_report(result, check_days=[1])
    assert "Skala 0-20" in report
    for name, cap in alloc.PHOTO_CAPS_BY_IMPORTANCE.items():
        assert f"{name} {cap}" in report, name
    assert "Prüffälle" in report
    assert "Tag 1" in report


def verify_an_empty_trip_reports_nothing_rather_than_zeroes() -> None:
    """No analyses is a finding, not a distribution with a median of 0."""
    result = sim.simulate([])
    assert result["distribution"] == {"count": 0}
    assert "Keine gespeicherten Analysen" in sim.format_report(result)


def verify_the_simulation_can_never_cost_anything() -> None:
    """The button says "kostenlos". This is what keeps that true."""
    for forbidden in (
        "async_analyze",
        "gemini",
        "Gemini",
        "reserve_vision_call",
        "async_generate",
        "save_day_curation",
    ):
        assert forbidden not in MODULE, forbidden


for check in (
    verify_the_distribution_is_read_off_the_pool_not_the_selection,
    verify_both_sources_are_reported_because_the_curation_also_caps,
    verify_a_higher_bar_never_yields_more_pictures,
    verify_an_unknown_old_count_stays_unknown,
    verify_the_report_names_the_scale_and_the_caps,
    verify_an_empty_trip_reports_nothing_rather_than_zeroes,
    verify_the_simulation_can_never_cost_anything,
):
    check()

print("Film allocation simulation tests passed.")
