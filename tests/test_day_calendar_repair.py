"""Behavioral tests for the travel-calendar repair proposal.

Live report: "Die Tage sind verwurschtelt" - three untitled placeholder
days sat between the planned ones, pushing every real day behind them.
The repair must remove exactly those, re-date the rest along the order
the user planned, and never touch a day that carries any content.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_day_calendar_repair_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def _load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = _load("day_calendar_repair")
changeset_module = _load("changeset")


def _day(day_id: str, date: str, title: str = "", **extra) -> dict:
    return {
        "id": day_id,
        "date": date,
        "title": title or f"Tag {date}",
        "stops": [],
        **extra,
    }


def _planned(day_id: str, date: str, title: str) -> dict:
    return _day(day_id, date, title, stops=[{"id": f"{day_id}-s1", "name": "Stopp"}])


def verify_placeholder_days_are_the_only_ones_proposed_for_removal() -> None:
    days = [
        _planned("d1", "2026-08-03", "Gränna & Polkagris"),
        _day("d2", "2026-08-04"),  # placeholder: auto title, nothing else
        _day("d3", "2026-08-05", title="Ruhetag"),  # typed title -> keep
        _day("d4", "2026-08-06", notes="Fähre buchen"),  # a note -> keep
        _day("d5", "2026-08-07", distance_km=180),  # driven km -> keep
        _day("d6", "2026-08-08", status="confirmed"),  # confirmed -> keep
        _planned("d7", "2026-08-09", "Trelleborg → Rostock (TT-Line Fähre)"),
    ]
    plan = module.analyze_day_calendar(days)
    assert [entry["day_id"] for entry in plan["empty_days"]] == ["d2"]
    assert plan["day_count_before"] == 7
    assert plan["day_count_after"] == 6
    assert plan["last_day_title"] == "Trelleborg → Rostock (TT-Line Fähre)"


def verify_the_remaining_days_are_redated_along_the_planned_order() -> None:
    """The order is the plan; the dates follow from it, never the reverse."""
    days = [
        _planned("d1", "2026-08-03", "Tag A"),
        _day("d2", "2026-08-04"),
        _day("d3", "2026-08-05"),
        _planned("d4", "2026-08-03", "Tag B"),  # duplicate of d1's date
        _planned("d5", "2026-08-02", "Tag C"),  # dated BEFORE the day above
    ]
    plan = module.analyze_day_calendar(days)
    assert [entry["day_id"] for entry in plan["empty_days"]] == ["d2", "d3"]
    # d1 already sits right, so it produces no operation at all.
    assert {entry["day_id"]: entry["new_date"] for entry in plan["redated_days"]} == {
        "d4": "2026-08-04",
        "d5": "2026-08-05",
    }
    assert plan["start_date"] == "2026-08-03"
    assert plan["end_date"] == "2026-08-05"
    assert plan["day_count_after"] == 3

    order_before = [day["id"] for day in days]
    module.analyze_day_calendar(days)
    assert [day["id"] for day in days] == order_before, "analysis must not reorder"


def verify_a_healthy_calendar_proposes_nothing() -> None:
    days = [
        _planned("d1", "2026-08-03", "Tag A"),
        _planned("d2", "2026-08-04", "Tag B"),
        _planned("d3", "2026-08-05", "Tag C"),
    ]
    plan = module.analyze_day_calendar(days)
    assert plan["needs_repair"] is False
    assert plan["operation_count"] == 0
    assert module.build_repair_operations(plan) == []


def verify_the_operations_are_valid_changeset_operations() -> None:
    days = [
        _planned("d1", "2026-08-03", "Tag A"),
        _day("d2", "2026-08-04"),
        _planned("d3", "2026-08-09", "Tag B"),
    ]
    plan = module.analyze_day_calendar(days)
    operations = module.build_repair_operations(
        plan, trip_start_date="2026-08-03", trip_end_date="2026-08-09"
    )
    assert [operation["op"] for operation in operations] == [
        "remove_day",
        "update_day",
        "update_trip",
    ], "removals first, so the re-dating talks about the days that remain"
    removal = operations[0]
    assert removal["day_id"] == "d2"
    assert removal["remove_stops"] is False, "never widen a removal silently"
    assert operations[1]["patch"] == {"date": "2026-08-04"}
    # The trip span follows the repaired days - the end moved forward by
    # the removed placeholder.
    assert operations[2]["patch"] == {"end_date": "2026-08-04"}
    assert all(operation.get("reason") for operation in operations), (
        "every proposal must say why - the reason is all the reviewer has"
    )
    assert len({operation["operation_id"] for operation in operations}) == len(operations)


def verify_the_trip_span_is_never_rewritten_on_its_own() -> None:
    """Retitling the trip's dates is only ever a side effect of a repair."""
    days = [_planned("d1", "2026-08-03", "Tag A")]
    plan = module.analyze_day_calendar(days)
    operations = module.build_repair_operations(
        plan, trip_start_date="2020-01-01", trip_end_date="2020-01-01"
    )
    assert operations == []


def verify_an_absurd_repair_is_refused_instead_of_truncated() -> None:
    days = [_planned(f"d{i}", "2026-08-03", f"Tag {i}") for i in range(500)]
    plan = module.analyze_day_calendar(days)
    try:
        module.build_repair_operations(plan)
    except ValueError as err:
        assert "Datenfehler" in str(err)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a 500-operation repair must not be proposed")


def verify_a_calendar_without_any_date_is_left_alone() -> None:
    days = [
        {"id": "d1", "title": "Tag A", "date": "", "stops": [{"id": "s"}]},
        {"id": "d2", "title": "Tag B", "date": "", "stops": [{"id": "s"}]},
    ]
    plan = module.analyze_day_calendar(days)
    assert plan["redated_days"] == []
    assert plan["start_date"] is None
    assert plan["needs_repair"] is False


def verify_days_counted_without_an_expanded_stop_list_still_count() -> None:
    """The panel payload can carry stop_count instead of the stops."""
    day = {"id": "d1", "date": "2026-08-04", "title": "Tag 2026-08-04", "stop_count": 3}
    assert module.is_empty_day(day) is False


def verify_the_proposal_survives_the_real_changeset_validation() -> None:
    """The operations must pass the SAME validation an import goes through.

    A proposal that only looks right and is then rejected on ingest is
    worse than no repair at all - it fails at the moment it is needed.
    """
    days = [
        _planned("day-1", "2026-07-17", "Start"),
        _day("day-2", "2026-08-06"),
        _planned("day-3", "2026-08-20", "Trelleborg → Rostock (TT-Line Fähre)"),
    ]
    plan = module.analyze_day_calendar(days)
    operations = module.build_repair_operations(
        plan, trip_start_date="2026-07-17", trip_end_date="2026-08-20"
    )
    normalized = changeset_module.normalize_changeset(
        {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": "changeset_repairtest",
            "trip_id": "trip-1",
            "base_revision": 385,
            "created_at": "2026-08-05T18:00:00Z",
            "title": "Reisetage aufräumen",
            "summary": "Test",
            "apply_mode": "review",
            "operations": operations,
            "open_questions": [],
            "assumptions": [],
            "research_notes": [],
            "metadata": {},
        }
    )
    assert [item["op"] for item in normalized["operations"]] == [
        "remove_day",
        "update_day",
        "update_trip",
    ]
    # Removing a day must keep the changeset out of every automatic path.
    assert normalized["destructive"] is True
    assert normalized["automatic_eligible"] is False
    assert all(item.get("reason") for item in normalized["operations"]), (
        "the reason must survive normalization - it is what the review shows"
    )


verify_placeholder_days_are_the_only_ones_proposed_for_removal()
verify_the_proposal_survives_the_real_changeset_validation()
verify_the_remaining_days_are_redated_along_the_planned_order()
verify_a_healthy_calendar_proposes_nothing()
verify_the_operations_are_valid_changeset_operations()
verify_the_trip_span_is_never_rewritten_on_its_own()
verify_an_absurd_repair_is_refused_instead_of_truncated()
verify_a_calendar_without_any_date_is_left_alone()
verify_days_counted_without_an_expanded_stop_list_still_count()
print("Travel calendar repair tests passed.")
