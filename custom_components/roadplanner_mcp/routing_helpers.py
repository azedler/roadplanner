"""Derive, invalidate and reconcile Roadplanner's per-day routing metrics.

Also owns the ferry/transport-mode stop metadata helpers the routing metrics
depend on. ``TripState`` is referenced only as a quoted (never resolved at
runtime, thanks to ``from __future__ import annotations``) type hint to avoid
a circular import - these functions only rely on its duck-typed shape
(``ordered_days()``, ``trip_document``, ``day_documents``).
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from .bounded_json import _bounded_json_value
from .canonical_day import canonical_day_model
from .json_io import utc_now_iso
from .stop_ordering import canonical_order_stops

if TYPE_CHECKING:
    from .roadplanner import TripState

_ROUTING_DETAIL_KEY = "routing"
_FERRY_STOP_TYPES = frozenset({"ferry", "ferry_terminal", "terminal"})
_TRANSPORT_MODES = frozenset({"driving", "ferry", "break"})

_OVERNIGHT_STOP_TYPES = frozenset({
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
})


def _stop_coordinate(stop: Any) -> tuple[float, float] | None:
    """Return a validated ``(latitude, longitude)`` pair for one stop."""
    if not isinstance(stop, dict):
        return None
    location = stop.get("location")
    if not isinstance(location, dict):
        return None
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _is_overnight_stop(stop: Any) -> bool:
    return (
        isinstance(stop, dict)
        and str(stop.get("type") or "").casefold() in _OVERNIGHT_STOP_TYPES
    )


def _stop_transport(stop: Any) -> dict[str, Any]:
    if not isinstance(stop, dict):
        return {}
    details = stop.get("details")
    if not isinstance(details, dict):
        return {}
    transport = details.get("transport")
    return transport if isinstance(transport, dict) else {}


def _is_ferry_stop(stop: Any) -> bool:
    """Return whether a canonical stop explicitly represents a ferry terminal.

    Free text such as "Ziel hinter der Fähre" is deliberately insufficient:
    misclassifying it would reconnect a ferry leg as a straight or road segment.
    The stop type or structured transport metadata must identify the terminal.
    """
    if not isinstance(stop, dict):
        return False
    stop_type = str(stop.get("type") or "").casefold()
    if stop_type in _FERRY_STOP_TYPES:
        return True
    transport = _stop_transport(stop)
    return (
        str(transport.get("mode") or "").casefold() == "ferry"
        or str(transport.get("ferry_role") or "").casefold() in {"departure", "arrival"}
    )


def _ferry_role(stop: Any) -> str:
    transport = _stop_transport(stop)
    role = str(transport.get("ferry_role") or "").casefold().strip()
    if role in {"departure", "arrival"}:
        return role
    text = " ".join(
        str(value or "")
        for value in (stop.get("name"), stop.get("notes"))
    ).casefold() if isinstance(stop, dict) else ""
    if any(token in text for token in ("ankunft", "arrival", "ankunftsterminal")):
        return "arrival"
    if any(token in text for token in ("abfahrt", "departure", "abfahrtsterminal", "check-in")):
        return "departure"
    return ""


def _routing_leg_mode(source: Any, target: Any) -> tuple[str, str | None]:
    source_transport = _stop_transport(source)
    target_transport = _stop_transport(target)
    explicit = str(source_transport.get("mode_to_next") or "").casefold().strip()
    if explicit in _TRANSPORT_MODES:
        return explicit, None
    explicit_from = str(target_transport.get("mode_from_previous") or "").casefold().strip()
    if explicit_from in _TRANSPORT_MODES:
        return explicit_from, None

    source_ferry = _is_ferry_stop(source)
    target_ferry = _is_ferry_stop(target)
    if source_ferry and target_ferry:
        return "ferry", None
    if source_ferry:
        if _ferry_role(source) == "arrival":
            return "driving", None
        return (
            "break",
            "Fährabfahrt erkannt, aber ein eigener Ankunftsterminal-Stopp mit GPS fehlt.",
        )
    if target_ferry:
        # A road leg ending at a departure terminal is valid. If the target was
        # explicitly marked as an arrival terminal, the departure is missing.
        if _ferry_role(target) == "arrival":
            return (
                "break",
                "Fährankunft erkannt, aber ein eigener Abfahrtsterminal-Stopp mit GPS fehlt.",
            )
        return "driving", None
    return "driving", None


def _effective_routing_stops(
    ordered: list[dict[str, Any]],
    index: int,
) -> list[dict[str, Any]]:
    """Return route wrappers from the shared canonical day model."""
    day_views = [
        {**deepcopy(document["day"]), "stops": deepcopy(document["stops"])}
        for document in ordered
    ]
    model = canonical_day_model(day_views, index)
    current_day_id = str(ordered[index]["day"]["id"])
    return [
        {
            "stop": stop,
            "inherited": bool(stop.get("_inherited")),
            "source_day_id": str(stop.get("_source_day_id") or current_day_id),
        }
        for stop in model.get("route_nodes", [])
        if isinstance(stop, dict)
    ]

def _routing_summary(document: dict[str, Any]) -> dict[str, Any] | None:
    details = document["day"].get("details")
    if not isinstance(details, dict):
        return None
    routing = details.get(_ROUTING_DETAIL_KEY)
    if not isinstance(routing, dict):
        return None
    result: dict[str, Any] = {}
    for key in (
        "schema_version",
        "status",
        "provider",
        "road_provider",
        "profile",
        "endpoint_host",
        "calculated_at",
        "invalidated_at",
        "invalidated_reason",
        "input_hash",
        "point_count",
        "missing_stop_count",
        "distance_m",
        "duration_s",
        "ferry_distance_m",
        "ferry_duration_s",
        "total_movement_m",
        "gap_count",
        "ferry_segment_count",
        "managed_metrics",
    ):
        if key in routing:
            result[key] = deepcopy(routing[key])
    geometry = routing.get("geometry")
    if (
        isinstance(geometry, dict)
        and geometry.get("type") == "LineString"
        and isinstance(geometry.get("coordinates"), list)
        and len(geometry["coordinates"]) <= 5_000
    ):
        result["geometry"] = deepcopy(geometry)
    if isinstance(routing.get("legs"), list):
        result["legs"] = _bounded_json_value(
            routing["legs"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("missing_stops"), list):
        result["missing_stops"] = _bounded_json_value(
            routing["missing_stops"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("stop_refs"), list):
        result["stop_refs"] = _bounded_json_value(
            routing["stop_refs"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("segments"), list):
        result["segments"] = _bounded_json_value(
            routing["segments"],
            max_items=200,
            max_string=500,
        )
    if isinstance(routing.get("warnings"), list):
        result["warnings"] = _bounded_json_value(
            routing["warnings"],
            max_items=100,
            max_string=500,
        )
    return result


def _invalidate_day_routing(document: dict[str, Any], reason: str) -> bool:
    """Mark one stored route stale and clear only provider-managed metrics."""
    day = document["day"]
    details = day.get("details")
    if not isinstance(details, dict):
        return False
    routing = details.get(_ROUTING_DETAIL_KEY)
    if not isinstance(routing, dict):
        return False
    changed = False
    now = utc_now_iso()
    if routing.get("managed_metrics"):
        if day.get("distance_km") is not None:
            routing.setdefault("previous_distance_km", day.get("distance_km"))
            day["distance_km"] = None
            changed = True
        if day.get("drive_minutes") is not None:
            routing.setdefault("previous_drive_minutes", day.get("drive_minutes"))
            day["drive_minutes"] = None
            changed = True
    desired_status = "stale" if routing.get("managed_metrics") else "manual_override"
    for key, value in (
        ("status", desired_status),
        ("invalidated_at", now),
        ("invalidated_reason", str(reason)[:500]),
        ("geometry_stale", True),
    ):
        if routing.get(key) != value:
            routing[key] = value
            changed = True
    if changed:
        details[_ROUTING_DETAIL_KEY] = routing
        day["details"] = details
        day["updated_at"] = now
    return changed


def _mark_manual_route_metrics(document: dict[str, Any]) -> None:
    """Document that manually edited metrics override prior provider results."""
    day = document["day"]
    details = day.get("details")
    if not isinstance(details, dict):
        details = {}
    previous = details.get(_ROUTING_DETAIL_KEY)
    routing = deepcopy(previous) if isinstance(previous, dict) else {"schema_version": 1}
    routing.update(
        {
            "status": "manual_override",
            "managed_metrics": False,
            "manual_override_at": utc_now_iso(),
            "distance_km": day.get("distance_km"),
            "drive_minutes": day.get("drive_minutes"),
            "geometry_stale": True,
        }
    )
    details[_ROUTING_DETAIL_KEY] = routing
    day["details"] = details


def _invalidate_all_routing(state: "TripState", reason: str) -> None:
    for document in state.ordered_days():
        _invalidate_day_routing(document, reason)


def _invalidate_day_and_next(
    state: "TripState",
    day_id: str,
    reason: str,
) -> None:
    refs = state.trip_document["days"]
    index = next((i for i, ref in enumerate(refs) if ref["id"] == day_id), None)
    if index is None:
        return
    _invalidate_day_routing(state.day_documents[day_id], reason)
    if index + 1 < len(refs):
        _invalidate_day_routing(
            state.day_documents[refs[index + 1]["id"]],
            f"previous_day_{reason}",
        )


def _route_stop_signature(document: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (
            stop.get("id"),
            str(stop.get("type") or "").casefold(),
            _stop_coordinate(stop),
            str(_stop_transport(stop).get("mode_to_next") or ""),
            str(_stop_transport(stop).get("mode_from_previous") or ""),
            str(_stop_transport(stop).get("ferry_role") or ""),
        )
        for stop in canonical_order_stops(document["stops"])
    ]


def _reconcile_routing_after_change(
    previous: "TripState",
    candidate: "TripState",
    operation: str,
) -> None:
    """Invalidate derived routes for all canonical mutation paths.

    Detection is deliberately separated from mutation. Invalidating one day can
    clear provider-managed metrics on the following day; a single-pass loop
    would then mistake those derived changes for a user-entered manual override.
    """
    if operation == "calculate_routes":
        return
    previous_ids = [ref["id"] for ref in previous.trip_document["days"]]
    candidate_ids = [ref["id"] for ref in candidate.trip_document["days"]]
    if previous_ids != candidate_ids:
        _invalidate_all_routing(candidate, "day_structure_changed")
        return

    manual_metric_day_ids: list[str] = []
    route_changed_day_ids: list[str] = []
    for day_id in candidate_ids:
        old_document = previous.day_documents[day_id]
        new_document = candidate.day_documents[day_id]
        old_day = old_document["day"]
        new_day = new_document["day"]
        if (
            old_day.get("distance_km") != new_day.get("distance_km")
            or old_day.get("drive_minutes") != new_day.get("drive_minutes")
        ):
            manual_metric_day_ids.append(day_id)
        if _route_stop_signature(old_document) != _route_stop_signature(new_document):
            route_changed_day_ids.append(day_id)

    for day_id in manual_metric_day_ids:
        _mark_manual_route_metrics(candidate.day_documents[day_id])
    for day_id in route_changed_day_ids:
        _invalidate_day_and_next(candidate, day_id, "route_stops_changed")


def _trip_route_metrics(state: "TripState") -> dict[str, Any]:
    ordered = state.ordered_days()
    total_distance = 0.0
    total_minutes = 0
    total_ferry_distance = 0.0
    total_ferry_minutes = 0
    ferry_segment_count = 0
    routing_gap_count = 0
    days_with_distance = 0
    days_with_duration = 0
    calculated_days = 0
    partial_days = 0
    stale_days = 0
    manual_days = 0
    candidate_days = 0
    missing_coordinate_days: list[str] = []
    unrouted_day_ids: list[str] = []
    for index, document in enumerate(ordered):
        day = document["day"]
        distance = day.get("distance_km")
        duration = day.get("drive_minutes")
        if isinstance(distance, (int, float)) and not isinstance(distance, bool):
            total_distance += float(distance)
            days_with_distance += 1
        if isinstance(duration, int) and not isinstance(duration, bool):
            total_minutes += int(duration)
            days_with_duration += 1
        routing = _routing_summary(document) or {}
        ferry_distance_m = routing.get("ferry_distance_m")
        ferry_duration_s = routing.get("ferry_duration_s")
        if isinstance(ferry_distance_m, (int, float)) and not isinstance(ferry_distance_m, bool):
            total_ferry_distance += float(ferry_distance_m) / 1000.0
        if isinstance(ferry_duration_s, (int, float)) and not isinstance(ferry_duration_s, bool):
            total_ferry_minutes += max(0, int(round(float(ferry_duration_s) / 60.0)))
        ferry_segment_count += int(routing.get("ferry_segment_count") or 0)
        routing_gap_count += int(routing.get("gap_count") or 0)
        status = str(routing.get("status") or "")
        if status == "calculated":
            calculated_days += 1
        elif status == "partial":
            partial_days += 1
        elif status == "stale":
            stale_days += 1
        elif status == "manual_override":
            manual_days += 1
        effective = _effective_routing_stops(ordered, index)
        if len(effective) >= 2:
            candidate_days += 1
            coordinate_count = sum(
                1 for item in effective if _stop_coordinate(item["stop"])
            )
            if coordinate_count < 2:
                missing_coordinate_days.append(day["id"])
            if distance is None or duration is None:
                unrouted_day_ids.append(day["id"])
    if candidate_days == 0:
        status = "not_required"
    elif not days_with_distance and not days_with_duration:
        status = "not_calculated"
    elif (
        not unrouted_day_ids
        and not stale_days
        and not partial_days
        and not missing_coordinate_days
    ):
        status = "complete"
    else:
        status = "partial"
    return {
        "status": status,
        "total_distance_km": round(total_distance, 1) if days_with_distance else None,
        "total_drive_minutes": total_minutes if days_with_duration else None,
        "total_ferry_distance_km": round(total_ferry_distance, 1) if total_ferry_distance else None,
        "total_ferry_minutes": total_ferry_minutes if total_ferry_minutes else None,
        "total_movement_km": round(total_distance + total_ferry_distance, 1) if (days_with_distance or total_ferry_distance) else None,
        "ferry_segment_count": ferry_segment_count,
        "routing_gap_count": routing_gap_count,
        "day_count": len(ordered),
        "route_candidate_day_count": candidate_days,
        "days_with_distance": days_with_distance,
        "days_with_drive_time": days_with_duration,
        "calculated_day_count": calculated_days,
        "partial_day_count": partial_days,
        "stale_day_count": stale_days,
        "manual_day_count": manual_days,
        "unrouted_day_count": len(unrouted_day_ids),
        "unrouted_day_ids": unrouted_day_ids[:180],
        "missing_coordinate_day_count": len(missing_coordinate_days),
        "missing_coordinate_day_ids": missing_coordinate_days[:180],
    }
