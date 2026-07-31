"""Per-option detour routes for the Stellplatz planning map.

For one day, every overnight candidate (the active stop plus each
non-rejected option with GPS) gets a real road route through the day's
anchor corridor: last GPS stop before the overnight -> candidate -> first
own GPS stop of the NEXT day. The direct corridor route (without any
overnight) is the baseline, so each candidate shows its true detour cost
("+12 min · +8,4 km").

Results are display-only: nothing is written to the roadbook. Routes are
cached in memory by the same input hash the day routing uses, so repeated
opens of the Stellplätze tab cost no provider calls until a coordinate
actually changes.
"""

from __future__ import annotations

from functools import partial
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .assistant_shared import OVERNIGHT_STOP_TYPES
from .roadplanner import ValidationError
from .routing import RoutingError, route_input_hash

_LOGGER = logging.getLogger(__name__)

_MAX_CACHE_ENTRIES = 300
_MAX_GEOMETRY_POINTS = 400


def corridor_anchors(
    day_points: list[dict[str, Any]],
    next_day_points: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Derive origin/destination anchors and the active overnight point.

    ``day_points``/``next_day_points`` are routing-plan day points (GPS only,
    inherited overnight-continuity start included). The overnight candidate
    itself is excluded from the corridor: candidates are inserted between
    origin and destination.
    """
    overnight_index = None
    for index in range(len(day_points) - 1, -1, -1):
        point = day_points[index]
        if point.get("inherited"):
            continue
        if str(point.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES:
            overnight_index = index
            break
    active_point = day_points[overnight_index] if overnight_index is not None else None
    before_overnight = (
        day_points[:overnight_index] if overnight_index is not None else list(day_points)
    )
    origin = before_overnight[-1] if before_overnight else None
    destination = None
    for point in next_day_points or []:
        if not point.get("inherited"):
            destination = point
            break
    return {"origin": origin, "destination": destination, "active": active_point}


def _route_point(source: dict[str, Any], *, stop_id: str, name: str) -> dict[str, Any]:
    return {
        "day_id": source.get("day_id"),
        "stop_id": stop_id,
        "name": name,
        "latitude": float(source["latitude"]),
        "longitude": float(source["longitude"]),
        "inherited": False,
        "mode_to_next": "driving",
    }


def _metrics(result: dict[str, Any]) -> dict[str, Any]:
    duration_minutes = round(float(result.get("duration_s") or 0) / 60)
    distance_km = round(float(result.get("distance_m") or 0) / 1000, 1)
    geometry = result.get("geometry") if isinstance(result.get("geometry"), dict) else {}
    coordinates = geometry.get("coordinates") or []
    if len(coordinates) > _MAX_GEOMETRY_POINTS:
        step = max(1, len(coordinates) // _MAX_GEOMETRY_POINTS)
        coordinates = coordinates[::step] + [coordinates[-1]]
    return {
        "duration_minutes": duration_minutes,
        "distance_km": distance_km,
        "geometry": coordinates,
    }


class PitchRouteService:
    """Compute and cache per-candidate detour routes for one day."""

    def __init__(self, hass: HomeAssistant, manager: Any) -> None:
        self._hass = hass
        self._manager = manager
        self._cache: dict[str, dict[str, Any]] = {}

    async def _async_route(self, points: list[dict[str, Any]]) -> dict[str, Any]:
        input_hash = route_input_hash(points, self._manager.router.profile)
        cached = self._cache.get(input_hash)
        if cached is not None:
            return cached
        result = _metrics(
            await self._manager.router.async_calculate(points, input_hash=input_hash)
        )
        if len(self._cache) >= _MAX_CACHE_ENTRIES:
            # Drop the oldest half - a simple bound beats an LRU here.
            for key in list(self._cache)[: _MAX_CACHE_ENTRIES // 2]:
                self._cache.pop(key, None)
        self._cache[input_hash] = result
        return result

    async def async_overview(self, day_id: str) -> dict[str, Any]:
        if not self._manager.router.configured:
            raise ValidationError(
                "Die Straßenroutenberechnung ist nicht aktiviert. "
                "Aktiviere sie in den Roadplanner-Optionen."
            )
        plan = await self._hass.async_add_executor_job(
            partial(self._manager.store.get_routing_plan, trip_id=None, day_ids=None)
        )
        days = plan.get("days") or []
        day_index = next(
            (index for index, day in enumerate(days) if day.get("day_id") == day_id),
            None,
        )
        if day_index is None:
            raise ValidationError(f"Reisetag nicht gefunden: {day_id}")
        day = days[day_index]
        next_day = days[day_index + 1] if day_index + 1 < len(days) else None
        anchors = corridor_anchors(
            day.get("points") or [],
            (next_day or {}).get("points"),
        )
        origin = anchors["origin"]
        destination = anchors["destination"]
        if origin is None:
            raise ValidationError(
                "Für diesen Tag gibt es vor der Übernachtung noch keinen Stopp "
                "mit GPS - ohne Startpunkt lässt sich kein Umweg berechnen."
            )

        payload = await self._manager.async_get_assistant_payload(None)
        stored_days = payload.get("days") if isinstance(payload.get("days"), dict) else {}
        stored_day = next(
            (
                item
                for item in stored_days.get("days") or []
                if isinstance(item, dict) and str(item.get("id")) == day_id
            ),
            {},
        )
        details = stored_day.get("details") if isinstance(stored_day.get("details"), dict) else {}
        overnight_plan = details.get("overnight_plan") if isinstance(details.get("overnight_plan"), dict) else {}

        candidates: list[dict[str, Any]] = []
        skipped: list[str] = []
        if anchors["active"] is not None:
            candidates.append(
                {
                    "kind": "active",
                    "option_id": "",
                    "name": str(anchors["active"].get("name") or "Aktiver Platz"),
                    "point": anchors["active"],
                }
            )
        for option in overnight_plan.get("options") or []:
            if not isinstance(option, dict) or option.get("status") == "rejected":
                continue
            location = option.get("location") if isinstance(option.get("location"), dict) else {}
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
                skipped.append(str(option.get("name") or option.get("id") or "Option"))
                continue
            candidates.append(
                {
                    "kind": "option",
                    "option_id": str(option.get("id") or ""),
                    "name": str(option.get("name") or "Option"),
                    "point": {
                        "day_id": day_id,
                        "latitude": float(latitude),
                        "longitude": float(longitude),
                    },
                }
            )
        if not candidates:
            raise ValidationError(
                "Weder der Übernachtungsstopp noch eine Option dieses Tages "
                "hat GPS-Daten - es gibt nichts zu berechnen."
            )

        origin_point = _route_point(
            origin, stop_id=str(origin.get("stop_id") or "origin"),
            name=str(origin.get("name") or ""),
        )
        destination_point = (
            _route_point(
                destination,
                stop_id=str(destination.get("stop_id") or "destination"),
                name=str(destination.get("name") or ""),
            )
            if destination is not None
            else None
        )

        baseline = None
        if destination_point is not None:
            try:
                direct = await self._async_route([origin_point, destination_point])
                baseline = {
                    "duration_minutes": direct["duration_minutes"],
                    "distance_km": direct["distance_km"],
                }
            except (RoutingError, ValidationError) as err:
                _LOGGER.debug("Pitch baseline route failed: %s", err)

        results: list[dict[str, Any]] = []
        errors: list[str] = []
        for candidate in candidates:
            leg_points = [
                origin_point,
                _route_point(
                    {**candidate["point"], "day_id": day_id},
                    stop_id=candidate["option_id"] or "active-overnight",
                    name=candidate["name"],
                ),
            ]
            if destination_point is not None:
                leg_points.append(destination_point)
            try:
                metrics = await self._async_route(leg_points)
            except (RoutingError, ValidationError) as err:
                errors.append(f"{candidate['name']}: {str(err)[:200]}")
                continue
            entry = {
                "kind": candidate["kind"],
                "option_id": candidate["option_id"],
                "name": candidate["name"],
                **metrics,
            }
            if baseline is not None:
                entry["extra_duration_minutes"] = max(
                    0, metrics["duration_minutes"] - baseline["duration_minutes"]
                )
                entry["extra_distance_km"] = max(
                    0.0,
                    round(metrics["distance_km"] - baseline["distance_km"], 1),
                )
            results.append(entry)

        return {
            "day_id": day_id,
            "strategy": str(overnight_plan.get("strategy") or ""),
            "origin": {"name": origin_point["name"]},
            "destination": (
                {"name": destination_point["name"]} if destination_point else None
            ),
            "baseline": baseline,
            "candidates": results,
            "skipped": skipped,
            "errors": errors,
        }
