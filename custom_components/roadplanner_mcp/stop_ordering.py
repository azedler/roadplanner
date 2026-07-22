"""Canonical stop ordering shared by all Roadplanner derived views.

Roadbook v1 stores the user-confirmed order as the list order and, once a day
has been touched by Roadplanner 3.1 or newer, as a complete one-based
``position`` sequence.  Arrival and departure times describe the schedule; they
must never silently reorder the travel plan.
"""

from __future__ import annotations

from typing import Any, Iterable


def _positive_position(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _time_minutes(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return hour * 60 + minute


def stop_time_minutes(stop: dict[str, Any]) -> int | None:
    """Return the best schedule time for display and diagnostics only."""
    arrival = _time_minutes(stop.get("arrival_time"))
    departure = _time_minutes(stop.get("departure_time"))
    if arrival is not None:
        return arrival
    return departure


def has_complete_explicit_positions(stops: Iterable[dict[str, Any]]) -> bool:
    """Return whether every stop has one unique explicit positive position."""
    values = list(stops)
    if not values:
        return False
    positions = [_positive_position(stop.get("position")) for stop in values]
    return (
        all(position is not None for position in positions)
        and len(set(positions)) == len(values)
    )


def canonical_order_stops(stops: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stop references in the one canonical travel-plan order.

    Ordering rules:

    1. A complete, unique explicit ``position`` set wins.
    2. Otherwise the stored Roadbook list order is authoritative.

    Time fields deliberately do *not* participate in this function.  A ferry at
    19:30 must remain after untimed parking or pharmacy stops when that is the
    user-confirmed list order.
    """
    values = [stop for stop in stops if isinstance(stop, dict)]
    if len(values) < 2:
        return values

    indexed = list(enumerate(values))
    if has_complete_explicit_positions(values):
        indexed.sort(
            key=lambda item: (
                _positive_position(item[1].get("position")) or 2_147_483_647,
                item[0],
            )
        )
        return [stop for _, stop in indexed]
    return values


def canonical_position_map(stops: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Return stop ID to one-based canonical sequence."""
    result: dict[str, int] = {}
    for sequence, stop in enumerate(canonical_order_stops(stops), start=1):
        stop_id = str(stop.get("id") or "").strip()
        if stop_id:
            result[stop_id] = sequence
    return result


def reindex_explicit_positions(stops: list[dict[str, Any]]) -> None:
    """Persist the current list order as a complete one-based position set."""
    for position, stop in enumerate(stops, start=1):
        stop["position"] = position


def normalize_stop_sequence(stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by a complete position set and persist a gap-free sequence.

    The list itself is mutated and returned for convenient use by normalizers
    and mutation paths.
    """
    ordered = canonical_order_stops(stops)
    if ordered is not stops:
        stops[:] = ordered
    reindex_explicit_positions(stops)
    return stops
