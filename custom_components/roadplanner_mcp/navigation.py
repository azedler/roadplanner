"""Safe, API-key-free Google Maps URL helpers for panel payloads."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from .canonical_day import canonical_day_model, canonical_day_stops
from .routing import coordinate_from_location

_GOOGLE_MAPS_SEARCH = "https://www.google.com/maps/search/"
_GOOGLE_MAPS_DIRECTIONS = "https://www.google.com/maps/dir/"
_MAX_MOBILE_WAYPOINTS = 3


def _coordinate_text(latitude: float, longitude: float) -> str:
    return f"{latitude:.7f},{longitude:.7f}"


def effective_day_stops(days: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    """Return the canonical shared stop sequence for one day."""
    model = canonical_day_model(days, index)
    return [stop for stop in model.get("route_nodes", []) if isinstance(stop, dict)]


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
        for stop in canonical_day_stops(day):
            if isinstance(stop, dict):
                decorate_stop_navigation(stop)
    for index, day in enumerate(days):
        effective = effective_day_stops(days, index)
        day["navigation"] = build_day_navigation(effective)
