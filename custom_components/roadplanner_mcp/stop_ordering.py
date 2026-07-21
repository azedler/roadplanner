"""Canonical stop ordering shared by all Roadplanner derived views.

Roadbook v1 stores stop order primarily through the list order and optionally
through the explicit ``position`` field.  Legacy data often lacks explicit
positions, so this module derives a deterministic temporal order without
mutating the canonical documents.
"""

from __future__ import annotations

from typing import Any, Iterable

_OVERNIGHT_TYPES = frozenset(
    {
        "overnight",
        "campsite",
        "camping",
        "stellplatz",
        "wildcamp",
        "accommodation",
    }
)
_START_TYPES = frozenset({"start", "origin", "home"})


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
    """Return the best confirmed chronology value for one stop."""
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
    return all(position is not None for position in positions) and len(set(positions)) == len(values)


def canonical_order_stops(stops: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stop references in the one canonical derived order.

    Ordering rules follow ADR-004:

    1. A complete, unique explicit ``position`` set wins.
    2. Otherwise confirmed arrival/departure chronology is used.
    3. Untimed start-like stops stay before timed stops; untimed overnight stops
       stay after ordinary stops.
    4. Original list order is the deterministic final fallback.
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

    timed_count = sum(stop_time_minutes(stop) is not None for stop in values)
    stop_types = [str(stop.get("type") or "").strip().casefold() for stop in values]
    needs_type_anchors = any(stop_type in _OVERNIGHT_TYPES | _START_TYPES for stop_type in stop_types)
    if timed_count == 0 and not needs_type_anchors:
        return values

    def sort_key(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        original_index, stop = item
        stop_type = str(stop.get("type") or "").strip().casefold()
        minutes = stop_time_minutes(stop)
        if stop_type in _START_TYPES and minutes is None:
            return (0, 0, original_index)
        if minutes is not None:
            return (1, minutes, original_index)
        if stop_type in _OVERNIGHT_TYPES:
            return (3, 0, original_index)
        return (2, 0, original_index)

    indexed.sort(key=sort_key)
    return [stop for _, stop in indexed]


def canonical_position_map(stops: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Return stop ID to one-based canonical sequence."""
    result: dict[str, int] = {}
    for sequence, stop in enumerate(canonical_order_stops(stops), start=1):
        stop_id = str(stop.get("id") or "").strip()
        if stop_id:
            result[stop_id] = sequence
    return result


def reindex_explicit_positions(stops: list[dict[str, Any]]) -> None:
    """Persist the current list order as complete explicit positions."""
    for position, stop in enumerate(stops, start=1):
        stop["position"] = position
