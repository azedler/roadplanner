"""Find and repair a drifted travel calendar - pure, no side effects.

Live report: "Die Tage sind verwurschtelt" - a trip whose day list showed
three untitled placeholder days ("Tag 2026-08-06", "Tag 2026-08-07",
"Tag 2026-08-08") wedged between the planned days, which pushed every
real day behind them and made "Heute" land on the wrong one.

Two independent defects produce that picture and this module names them
separately:

* **Leftover placeholder days** - a day with no stops, no notes, no
  distance and nothing but the auto-generated ``Tag <Datum>`` title. It
  carries no plan at all; it only occupies a number.
* **Dates that disagree with the order** - the day list is the plan, but
  the stored dates run backwards or repeat, so the weekday shown on a
  card is wrong and the trip appears to last longer than it does.

The order of the days is NEVER changed here. The sequence is the user's
plan and the only thing in this data that cannot be re-derived; the dates
are what follow from it. Everything this module produces is a PROPOSAL -
removing a day is destructive, so the caller parks it for review instead
of applying it (same contract as the Park4Night autofill).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# A trip cannot sensibly be renumbered beyond this - a guard against a
# corrupt payload turning into thousands of operations.
MAX_REPAIR_OPERATIONS = 400


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def _stop_count(day: dict[str, Any]) -> int:
    stops = day.get("stops")
    if isinstance(stops, list):
        return sum(1 for stop in stops if isinstance(stop, dict))
    count = day.get("stop_count")
    return int(count) if isinstance(count, int) and not isinstance(count, bool) else 0


def _is_placeholder_title(day: dict[str, Any]) -> bool:
    """True only for a title nobody typed.

    ``add_day`` without a title stores ``Tag <ISO-Datum>``; anything else
    - even a short one - is a title the user or the assistant chose and
    must never be treated as disposable.
    """
    title = _text(day.get("title"))
    if not title:
        return True
    stored_date = _text(day.get("date"))
    return bool(stored_date) and title == f"Tag {stored_date}"


def is_empty_day(day: dict[str, Any]) -> bool:
    """True when a day carries no plan whatsoever.

    Deliberately strict: one stop, one note, one kilometre or one typed
    title is enough to keep a day. A false positive here deletes work.
    """
    if not isinstance(day, dict):
        return False
    if _stop_count(day) > 0:
        return False
    if not _is_placeholder_title(day):
        return False
    if _text(day.get("notes")) or _text(day.get("start")) or _text(day.get("end")):
        return False
    for field in ("distance_km", "drive_minutes"):
        value = day.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return False
    status = _text(day.get("status")).casefold()
    if status in {"confirmed", "completed"}:
        # A day somebody confirmed is a statement, even an empty one.
        return False
    return True


def analyze_day_calendar(
    days: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Describe what is wrong with the calendar and what would repair it.

    Returns a plan the caller can show BEFORE anything is changed:
    ``empty_days`` (proposed for removal), ``redated_days`` (day id, old
    and new date) and the resulting day count / end date, so the plan can
    be checked against what the trip is actually meant to be.
    """
    valid = [day for day in days if isinstance(day, dict)]
    empty_days = [day for day in valid if is_empty_day(day)]
    empty_ids = {id(day) for day in empty_days}
    kept = [day for day in valid if id(day) not in empty_ids]

    anchor = next(
        (parsed for day in kept if (parsed := _parse_date(day.get("date")))), None
    )
    redated: list[dict[str, Any]] = []
    if anchor is not None:
        for offset, day in enumerate(kept):
            target = anchor + timedelta(days=offset)
            current = _parse_date(day.get("date"))
            if current == target:
                continue
            redated.append(
                {
                    "day_id": _text(day.get("id")),
                    "title": _text(day.get("title")),
                    "sequence": offset + 1,
                    "old_date": _text(day.get("date")) or None,
                    "new_date": target.isoformat(),
                }
            )

    start_date = anchor.isoformat() if anchor else None
    end_date = (
        (anchor + timedelta(days=len(kept) - 1)).isoformat()
        if anchor and kept
        else None
    )
    return {
        "version": 1,
        "day_count_before": len(valid),
        "day_count_after": len(kept),
        "empty_days": [
            {
                "day_id": _text(day.get("id")),
                "title": _text(day.get("title")),
                "date": _text(day.get("date")) or None,
            }
            for day in empty_days
        ],
        "redated_days": redated,
        "start_date": start_date,
        "end_date": end_date,
        "last_day_title": _text(kept[-1].get("title")) if kept else None,
        "operation_count": len(empty_days) + len(redated),
        "needs_repair": bool(empty_days or redated),
    }


def build_repair_operations(
    plan: dict[str, Any],
    *,
    trip_start_date: str = "",
    trip_end_date: str = "",
) -> list[dict[str, Any]]:
    """Turn an analysis into ChangeSet operations - removals first.

    Removals come first so the positions the re-dating talks about are the
    ones that exist afterwards. Every operation carries the reason it was
    generated, because this lands under "Übergaben" where the reason is
    the only thing the reviewer has to judge it by.

    Raises ValueError past ``MAX_REPAIR_OPERATIONS``: a half-applied
    re-dating would leave duplicate dates behind, which is worse than the
    drift it was meant to fix, so this never truncates silently.
    """
    operations: list[dict[str, Any]] = []
    for entry in plan.get("empty_days") or []:
        day_id = _text(entry.get("day_id"))
        if not day_id:
            continue
        label = _text(entry.get("title")) or _text(entry.get("date")) or day_id
        operations.append(
            {
                "op": "remove_day",
                "operation_id": f"repair-remove-{day_id}",
                "day_id": day_id,
                # No stops to carry: the day was only kept because it was
                # empty, and remove_stops would be a silent widening of
                # what this proposal claims to do.
                "remove_stops": False,
                "reason": (
                    f"„{label}“ enthält keinen Stopp, keine Notiz und keinen "
                    "eigenen Titel - ein übrig gebliebener Platzhalter."
                ),
            }
        )
    for entry in plan.get("redated_days") or []:
        day_id = _text(entry.get("day_id"))
        new_date = _text(entry.get("new_date"))
        if not day_id or not new_date:
            continue
        old_date = _text(entry.get("old_date")) or "ohne Datum"
        operations.append(
            {
                "op": "update_day",
                "operation_id": f"repair-date-{day_id}",
                "day_id": day_id,
                "patch": {"date": new_date},
                "reason": (
                    f"Tag {entry.get('sequence')} der Reihenfolge nach: "
                    f"{old_date} → {new_date}."
                ),
            }
        )

    patch: dict[str, Any] = {}
    if plan.get("start_date") and _text(trip_start_date) != _text(plan["start_date"]):
        patch["start_date"] = plan["start_date"]
    if plan.get("end_date") and _text(trip_end_date) != _text(plan["end_date"]):
        patch["end_date"] = plan["end_date"]
    if patch and operations:
        # Only alongside real day changes: rewriting the trip span on its
        # own is not a repair, it is an opinion about the trip.
        operations.append(
            {
                "op": "update_trip",
                "operation_id": "repair-trip-span",
                "patch": patch,
                "reason": (
                    "Reisezeitraum an die bereinigten Reisetage angeglichen "
                    f"({plan.get('start_date')} bis {plan.get('end_date')})."
                ),
            }
        )
    if len(operations) > MAX_REPAIR_OPERATIONS:
        raise ValueError(
            f"Die Reparatur würde {len(operations)} Änderungen umfassen - das "
            "sieht nach einem Datenfehler aus und wird nicht vorgeschlagen."
        )
    return operations
