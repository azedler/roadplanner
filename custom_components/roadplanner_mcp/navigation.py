"""Safe, API-key-free Google Maps URL helpers for panel payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlencode

from .routing import coordinate_from_location

_GOOGLE_MAPS_SEARCH = "https://www.google.com/maps/search/"
_GOOGLE_MAPS_DIRECTIONS = "https://www.google.com/maps/dir/"
_OVERNIGHT_TYPES = {
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
}
_MAX_MOBILE_WAYPOINTS = 3


def _coordinate_text(latitude: float, longitude: float) -> str:
    return f"{latitude:.7f},{longitude:.7f}"


def _is_overnight(stop: Any) -> bool:
    return isinstance(stop, dict) and str(stop.get("type") or "").casefold() in _OVERNIGHT_TYPES


def _same_place(first: Any, second: Any) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    if first.get("id") and first.get("id") == second.get("id"):
        return True
    first_coordinate = coordinate_from_location(first.get("location"))
    second_coordinate = coordinate_from_location(second.get("location"))
    if first_coordinate and second_coordinate:
        return (
            abs(first_coordinate[0] - second_coordinate[0]) < 0.00005
            and abs(first_coordinate[1] - second_coordinate[1]) < 0.00005
        )
    first_name = str(first.get("name") or "").casefold().strip()
    second_name = str(second.get("name") or "").casefold().strip()
    return bool(first_name and first_name == second_name)


def effective_day_stops(days: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Return canonical stops plus the prior overnight start when applicable."""
    canonical = list(days[index].get("stops") or [])
    if index <= 0:
        return canonical
    previous = list(days[index - 1].get("stops") or [])
    overnight = previous[-1] if previous else None
    if not _is_overnight(overnight):
        return canonical
    if canonical and _same_place(overnight, canonical[0]):
        return canonical
    inherited = deepcopy(overnight)
    inherited["_inherited"] = True
    inherited["_source_day_id"] = days[index - 1].get("id")
    return [inherited, *canonical]


def google_maps_search_url(stop: dict[str, Any]) -> str | None:
    coordinate = coordinate_from_location(stop.get("location"))
    if coordinate:
        query = _coordinate_text(*coordinate)
    else:
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        query = ", ".join(
            value
            for value in (
                str(stop.get("name") or "").strip(),
                str(location.get("address") or "").strip(),
                str(location.get("city") or "").strip(),
                str(location.get("country_code") or "").strip(),
            )
            if value
        )
    if not query:
        return None
    return f"{_GOOGLE_MAPS_SEARCH}?{urlencode({'api': 1, 'query': query})}"


def google_maps_navigation_url(stop: dict[str, Any]) -> str | None:
    coordinate = coordinate_from_location(stop.get("location"))
    if not coordinate:
        return None
    return f"{_GOOGLE_MAPS_DIRECTIONS}?{urlencode({
        'api': 1,
        'destination': _coordinate_text(*coordinate),
        'travelmode': 'driving',
        'dir_action': 'navigate',
    })}"


def decorate_stop_navigation(stop: dict[str, Any]) -> None:
    search_url = google_maps_search_url(stop)
    navigation_url = google_maps_navigation_url(stop)
    stop["navigation"] = {
        "google_maps_search_url": search_url,
        "google_maps_navigation_url": navigation_url,
        "has_coordinates": bool(coordinate_from_location(stop.get("location"))),
    }


def _selected_day_points(stops: list[dict[str, Any]]) -> list[tuple[dict[str, Any], tuple[float, float]]]:
    result: list[tuple[dict[str, Any], tuple[float, float]]] = []
    for stop in stops:
        coordinate = coordinate_from_location(stop.get("location"))
        if coordinate:
            result.append((stop, coordinate))
    return result


def build_day_navigation(stops: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a mobile-safe Google Maps directions URL for one day."""
    points = _selected_day_points(stops)
    if len(points) < 2:
        return {
            "google_maps_directions_url": None,
            "point_count": len(points),
            "included_point_count": len(points),
            "omitted_point_count": 0,
            "mobile_waypoint_limit": _MAX_MOBILE_WAYPOINTS,
        }
    origin = points[0]
    destination = points[-1]
    intermediates = points[1:-1]
    included_intermediates = intermediates[:_MAX_MOBILE_WAYPOINTS]
    params: dict[str, Any] = {
        "api": 1,
        "origin": _coordinate_text(*origin[1]),
        "destination": _coordinate_text(*destination[1]),
        "travelmode": "driving",
    }
    if included_intermediates:
        params["waypoints"] = "|".join(
            _coordinate_text(*coordinate)
            for _stop, coordinate in included_intermediates
        )
    return {
        "google_maps_directions_url": f"{_GOOGLE_MAPS_DIRECTIONS}?{urlencode(params)}",
        "point_count": len(points),
        "included_point_count": 2 + len(included_intermediates),
        "omitted_point_count": max(0, len(intermediates) - len(included_intermediates)),
        "mobile_waypoint_limit": _MAX_MOBILE_WAYPOINTS,
        "included_stop_ids": [
            str(stop.get("id") or "")
            for stop, _coordinate in [origin, *included_intermediates, destination]
        ],
    }


def decorate_panel_navigation(days_payload: dict[str, Any]) -> None:
    """Decorate a panel day page in-place without touching canonical files."""
    days = days_payload.get("days")
    if not isinstance(days, list):
        return
    for day in days:
        for stop in day.get("stops") or []:
            if isinstance(stop, dict):
                decorate_stop_navigation(stop)
    for index, day in enumerate(days):
        effective = effective_day_stops(days, index)
        day["navigation"] = build_day_navigation(effective)
