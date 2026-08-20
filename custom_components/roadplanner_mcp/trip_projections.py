"""Bounded, read-only projections of trips/days/stops for summaries and payloads.

``TripState`` is referenced only as a ``TYPE_CHECKING``-guarded, quoted type
hint (never resolved at runtime, thanks to ``from __future__ import
annotations``) to avoid a circular import - ``_first_trip_media`` only relies
on its duck-typed shape (``trip_document``, ``ordered_days()``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .bounded_json import _bounded_json_value
from .routing_helpers import _routing_summary
from .stop_ordering import canonical_order_stops

if TYPE_CHECKING:
    from .roadplanner import TripState


def _compact_trip(
    trip: dict[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    result = {
        "id": trip["id"],
        "title": trip["title"],
        "status": trip["status"],
        "start_date": trip.get("start_date"),
        "end_date": trip.get("end_date"),
        "travelers": _bounded_json_value(
            trip.get("travelers", []),
            max_items=50,
            max_string=1_000,
        ),
        "vehicle": _bounded_json_value(
            trip.get("vehicle", {}),
            max_items=50,
            max_string=1_000,
        ),
        "preferences": _bounded_json_value(
            trip.get("preferences", {}),
            max_items=100,
            max_string=1_000,
        ),
        "notes": _bounded_json_value(trip.get("notes", ""), max_string=4_000),
    }
    details = trip.get("details", {})
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _compact_day(
    document: dict[str, Any],
    *,
    sequence: int,
    include_details: bool,
) -> dict[str, Any]:
    raw = document["day"]
    result = {
        "id": raw["id"],
        "sequence": sequence,
        "date": raw.get("date"),
        "title": raw["title"],
        "start": raw.get("start"),
        "end": raw.get("end"),
        "distance_km": raw.get("distance_km"),
        "drive_minutes": raw.get("drive_minutes"),
        "status": raw.get("status"),
        "notes": _bounded_json_value(raw.get("notes", ""), max_string=4_000),
        "stop_count": len(document["stops"]),
        "routing": _routing_summary(document),
    }
    details = raw.get("details", {})
    if isinstance(details, dict):
        planning_preferences = details.get("planning_preferences")
        if isinstance(planning_preferences, list) and planning_preferences:
            result["planning_preferences"] = _bounded_json_value(
                planning_preferences,
                max_items=20,
                max_string=2_000,
            )
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _compact_stop(
    stop: dict[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    result = {
        "id": stop["id"],
        "name": stop["name"],
        "type": stop["type"],
        "arrival_time": stop.get("arrival_time"),
        "departure_time": stop.get("departure_time"),
        "position": stop.get("position"),
        "location": _bounded_json_value(
            stop.get("location", {}),
            max_items=50,
            max_string=2_000,
        ),
        "notes": _bounded_json_value(stop.get("notes", ""), max_string=4_000),
    }
    details = stop.get("details", {})
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _media_from_details(details: Any) -> dict[str, str] | None:
    """Extract the optional media object used by the frontend.

    The ``url`` alias is honoured ONLY inside an explicit ``media``
    block. A bare details object also carries ``url`` fields - and there
    they are source LINKS (a provider's place page), never images. The
    live symptom was a trip card whose cover ``<img>`` pointed at a
    campsite's web page: the stop had a stored lookup link and no photo,
    and this function turned the one into the other.
    """
    if not isinstance(details, dict):
        return None
    media = details.get("media")
    if isinstance(media, dict):
        image_url = media.get("image_url") or media.get("url")
    else:
        media = details
        image_url = media.get("image_url")
    if not isinstance(image_url, str) or not image_url.strip():
        return None
    result = {"image_url": image_url.strip()}
    for target, source in (
        ("alt", "alt"),
        ("attribution", "attribution"),
        ("source_url", "source_url"),
        ("provider", "provider"),
    ):
        value = media.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value.strip()[:1_000]
    return result


def _first_trip_media(state: "TripState") -> dict[str, str] | None:
    """Return a cover image from trip, day, or stop details."""
    media = _media_from_details(state.trip_document["trip"].get("details"))
    if media is not None:
        return media
    for document in state.ordered_days():
        media = _media_from_details(document["day"].get("details"))
        if media is not None:
            return media
        for stop in canonical_order_stops(document["stops"]):
            media = _media_from_details(stop.get("details"))
            if media is not None:
                return media
    return None
