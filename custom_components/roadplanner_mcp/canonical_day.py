"""Canonical day view-model shared by every Roadplanner consumer.

The split Roadbook remains the source of truth.  This module derives one
bounded, immutable view-model for maps, stop cards, route flows, navigation,
decisions, assistant context and exports.  Legacy ``day.start`` / ``day.end``
values are retained as context but never become synthetic stops while real
Roadbook stops exist.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
from typing import Any, Iterable

from .stop_ordering import canonical_order_stops

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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _day_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def is_overnight_stop(stop: Any) -> bool:
    """Return whether a stop closes a travel day."""
    return isinstance(stop, dict) and _text(stop.get("type")).casefold() in _OVERNIGHT_TYPES


def is_start_stop(stop: Any) -> bool:
    """Return whether a stop explicitly represents a trip/day origin."""
    return isinstance(stop, dict) and _text(stop.get("type")).casefold() in _START_TYPES


def _coordinate(stop: Any) -> tuple[float, float] | None:
    if not isinstance(stop, dict):
        return None
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _geocoding_details(stop: Any) -> dict[str, Any]:
    if not isinstance(stop, dict):
        return {}
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    geocoding = details.get("geocoding") if isinstance(details.get("geocoding"), dict) else {}
    return geocoding


def location_status(stop: Any) -> str:
    """Return one stable canonical location state for a stop.

    Values are ``resolved``, ``unverified``, ``ambiguous`` or ``missing``.
    Coordinates are never inferred here; this is a pure derived status.
    """
    coordinate = _coordinate(stop)
    status = _text(_geocoding_details(stop).get("status")).casefold()
    if coordinate is not None:
        if not status or status == "resolved":
            return "resolved"
        return "unverified"
    if "ambiguous" in status:
        return "ambiguous"
    return "missing"


def _location_query(stop: Any) -> str:
    if not isinstance(stop, dict):
        return ""
    geocoding = _geocoding_details(stop)
    query = _text(geocoding.get("query"))
    if query:
        return query
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    context = [
        _text(stop.get("name")),
        _text(location.get("label")),
        _text(location.get("city")),
        _text(location.get("country_code")),
    ]
    seen: set[str] = set()
    values: list[str] = []
    for value in context:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return ", ".join(values)[:500]


def _decorate_location_state(stop: dict[str, Any]) -> dict[str, Any]:
    status = location_status(stop)
    stop["location_status"] = status
    stop["has_coordinates"] = _coordinate(stop) is not None
    stop["location_query"] = _location_query(stop)
    stop["location_requires_attention"] = status != "resolved"
    stop["location_message"] = {
        "resolved": "GPS vorhanden",
        "unverified": "GPS vorhanden, aber noch nicht bestätigt",
        "ambiguous": "GPS-Zuordnung ist mehrdeutig",
        "missing": "GPS-Koordinaten fehlen",
    }[status]
    return stop


def same_place(first: Any, second: Any) -> bool:
    """Return whether two stop objects represent the same physical place."""
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    first_id = _text(first.get("id"))
    second_id = _text(second.get("id"))
    if first_id and first_id == second_id:
        return True
    first_coordinate = _coordinate(first)
    second_coordinate = _coordinate(second)
    if first_coordinate and second_coordinate:
        return (
            abs(first_coordinate[0] - second_coordinate[0]) < 0.00005
            and abs(first_coordinate[1] - second_coordinate[1]) < 0.00005
        )
    first_name = _text(first.get("name")).casefold()
    second_name = _text(second.get("name")).casefold()
    return bool(first_name and first_name == second_name)


def _roadbook_stop(stop: dict[str, Any], *, sequence: int) -> dict[str, Any]:
    result = deepcopy(stop)
    result["display_sequence"] = sequence
    result["route_sequence"] = sequence
    result["marker_label"] = str(sequence)
    result["_inherited"] = False
    result["_route_node_kind"] = "stop"
    result["_is_roadbook_stop"] = True
    return _decorate_location_state(result)


def _inherited_stop(
    stop: dict[str, Any],
    *,
    source_day_id: str | None,
    source_day_title: str | None,
) -> dict[str, Any]:
    result = deepcopy(stop)
    result["display_sequence"] = None
    result["route_sequence"] = 0
    result["marker_label"] = "S"
    result["_inherited"] = True
    result["_source_day_id"] = source_day_id
    result["_source_day_title"] = source_day_title
    result["_route_node_kind"] = "inherited_start"
    result["_is_roadbook_stop"] = False
    return _decorate_location_state(result)


def _legacy_node(label: str, *, kind: str) -> dict[str, Any]:
    return {
        "id": None,
        "name": label,
        "type": "start" if kind == "legacy_start" else "destination",
        "display_sequence": None,
        "route_sequence": 0 if kind == "legacy_start" else 1,
        "marker_label": None,
        "_inherited": False,
        "_route_node_kind": kind,
        "_is_roadbook_stop": False,
        "_legacy_context": True,
        "location": {},
    }


def _route_fingerprint(day_id: str, route_nodes: list[dict[str, Any]]) -> str:
    payload = {
        "day_id": day_id,
        "nodes": [
            {
                "id": node.get("id"),
                "kind": node.get("_route_node_kind"),
                "sequence": node.get("display_sequence"),
                "name": node.get("name"),
                "type": node.get("type"),
                "arrival_time": node.get("arrival_time"),
                "departure_time": node.get("departure_time"),
                "coordinate": _coordinate(node),
            }
            for node in route_nodes
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _phase(day: dict[str, Any], today: date | None) -> str:
    if today is None:
        return "planned"
    parsed = _day_date(day.get("date"))
    if parsed is None:
        return "planned"
    if parsed < today:
        return "past"
    if parsed > today:
        return "future"
    return "today"


def canonical_day_model(
    days: list[dict[str, Any]],
    index: int,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the one canonical derived model for ``days[index]``.

    Roadbook-owned stops keep one-based numbering independent of the inherited
    overnight start.  The inherited start uses the explicit ``S`` marker so a
    day map, flow diagram and stop cards can all show the same physical route
    without renumbering canonical stops.
    """
    day = days[index]
    ordered = canonical_order_stops(day.get("stops") or [])
    roadbook_stops = [
        _roadbook_stop(stop, sequence=position)
        for position, stop in enumerate(ordered, start=1)
    ]

    inherited: dict[str, Any] | None = None
    if index > 0:
        previous = days[index - 1]
        previous_ordered = canonical_order_stops(previous.get("stops") or [])
        previous_overnight = previous_ordered[-1] if previous_ordered else None
        if is_overnight_stop(previous_overnight) and not (
            ordered and same_place(previous_overnight, ordered[0])
        ):
            inherited = _inherited_stop(
                previous_overnight,
                source_day_id=_text(previous.get("id")) or None,
                source_day_title=_text(previous.get("title")) or None,
            )

    route_nodes = ([inherited] if inherited else []) + roadbook_stops
    legacy_start = _text(day.get("start"))
    legacy_end = _text(day.get("end"))
    legacy_route_nodes: list[dict[str, Any]] = []
    if not route_nodes:
        if legacy_start:
            legacy_route_nodes.append(_legacy_node(legacy_start, kind="legacy_start"))
        if legacy_end and legacy_end.casefold() != legacy_start.casefold():
            legacy_route_nodes.append(_legacy_node(legacy_end, kind="legacy_end"))

    overnight = next(
        (stop for stop in reversed(roadbook_stops) if is_overnight_stop(stop)),
        None,
    )
    start_stop = route_nodes[0] if route_nodes else None
    end_stop = route_nodes[-1] if route_nodes else None
    warnings: list[dict[str, Any]] = []
    if route_nodes and legacy_start and start_stop and not same_place(
        {"name": legacy_start}, start_stop
    ):
        warnings.append(
            {
                "code": "legacy_start_context",
                "message": (
                    f"Das alte Startfeld '{legacy_start}' wird nur noch als Kontext "
                    "geführt; die Tagesroute beginnt am ersten echten Stopp."
                ),
            }
        )
    if route_nodes and legacy_end and end_stop and not same_place(
        {"name": legacy_end}, end_stop
    ):
        warnings.append(
            {
                "code": "legacy_end_context",
                "message": (
                    f"Das alte Zielfeld '{legacy_end}' wird nicht als Stopp dargestellt. "
                    "Das Tagesziel ergibt sich aus dem letzten echten Stopp."
                ),
            }
        )

    map_nodes = [node for node in route_nodes if _coordinate(node) is not None]
    location_counts = {
        "resolved": 0,
        "unverified": 0,
        "ambiguous": 0,
        "missing": 0,
    }
    for node in route_nodes:
        status = _text(node.get("location_status")) or location_status(node)
        if status not in location_counts:
            status = "missing"
        location_counts[status] += 1

    def location_node_payload(node: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": _text(node.get("id")) or None,
            "name": _text(node.get("name")) or "Unbenannter Stopp",
            "display_sequence": node.get("display_sequence"),
            "marker_label": node.get("marker_label"),
            "inherited": bool(node.get("_inherited")),
            "status": _text(node.get("location_status")) or location_status(node),
            "query": _text(node.get("location_query")) or _location_query(node),
        }

    missing_location_nodes = [
        location_node_payload(node)
        for node in route_nodes
        if _coordinate(node) is None
    ]
    location_attention_nodes = [
        location_node_payload(node)
        for node in route_nodes
        if (
            _text(node.get("location_status")) or location_status(node)
        ) != "resolved"
    ]
    stop_ids = [
        _text(stop.get("id"))
        for stop in roadbook_stops
        if _text(stop.get("id"))
    ]
    route_stop_ids = [
        _text(stop.get("id"))
        for stop in route_nodes
        if _text(stop.get("id"))
    ]

    model = {
        "version": 3,
        "day_id": _text(day.get("id")),
        "day_sequence": day.get("sequence"),
        "phase": _phase(day, today),
        "has_real_stops": bool(roadbook_stops),
        "stops": roadbook_stops,
        "route_nodes": route_nodes,
        "map_nodes": map_nodes,
        "legacy_route_nodes": legacy_route_nodes,
        "stop_ids": stop_ids,
        "route_stop_ids": route_stop_ids,
        "map_stop_ids": [
            _text(stop.get("id"))
            for stop in map_nodes
            if _text(stop.get("id"))
        ],
        "sequence_by_stop_id": {
            _text(stop.get("id")): int(stop.get("display_sequence") or 0)
            for stop in roadbook_stops
            if _text(stop.get("id"))
        },
        "start_stop_id": _text(start_stop.get("id")) if start_stop else None,
        "end_stop_id": _text(end_stop.get("id")) if end_stop else None,
        "overnight_stop_id": _text(overnight.get("id")) if overnight else None,
        "inherited_start": inherited is not None,
        "inherited_from_day_id": inherited.get("_source_day_id") if inherited else None,
        "start_label": _text(start_stop.get("name")) if start_stop else legacy_start,
        "end_label": _text(end_stop.get("name")) if end_stop else legacy_end,
        "legacy_start": legacy_start or None,
        "legacy_end": legacy_end or None,
        "legacy_labels_are_context_only": bool(route_nodes),
        "real_stop_count": len(roadbook_stops),
        "route_node_count": len(route_nodes),
        "coordinate_count": len(map_nodes),
        "missing_coordinate_count": len(missing_location_nodes),
        "location_counts": location_counts,
        "missing_location_nodes": missing_location_nodes,
        "location_attention_nodes": location_attention_nodes,
        "location_complete": not location_attention_nodes,
        "route_complete": bool(route_nodes) and not missing_location_nodes,
        "data_quality": {
            "sequence": "complete",
            "locations": (
                "partial"
                if missing_location_nodes
                else "review"
                if location_attention_nodes
                else "complete"
            ),
            "score": (
                100
                if not route_nodes
                else round(100 * location_counts["resolved"] / len(route_nodes))
            ),
        },
        "warnings": warnings,
    }
    model["route_fingerprint"] = _route_fingerprint(model["day_id"], route_nodes)
    return model


def decorate_canonical_days(
    days_payload: dict[str, Any],
    *,
    today: date | None = None,
) -> None:
    """Attach canonical day models to a bounded Roadbook day payload."""
    days = days_payload.get("days")
    if not isinstance(days, list):
        return
    valid_days = [day for day in days if isinstance(day, dict)]
    for index, day in enumerate(valid_days):
        model = canonical_day_model(valid_days, index, today=today)
        day["canonical"] = model
        # Keep the existing payload convenient for older callers while using
        # the canonical order as the only serialized stop order.
        day["stops"] = deepcopy(model["stops"])
        day["stop_count"] = len(model["stops"])


def canonical_day_stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    """Return effective route nodes, falling back for non-panel callers."""
    canonical = day.get("canonical") if isinstance(day.get("canonical"), dict) else None
    stops = canonical.get("route_nodes") if canonical else None
    if isinstance(stops, list):
        return [stop for stop in stops if isinstance(stop, dict)]
    return canonical_order_stops(day.get("stops") or [])


def canonical_roadbook_stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only stops canonically owned by the selected day."""
    canonical = day.get("canonical") if isinstance(day.get("canonical"), dict) else None
    stops = canonical.get("stops") if canonical else None
    if isinstance(stops, list):
        return [stop for stop in stops if isinstance(stop, dict)]
    return canonical_order_stops(day.get("stops") or [])


def canonical_sequence(stop: dict[str, Any], fallback: int) -> int:
    """Return the displayed sequence supplied by the canonical day model."""
    raw = stop.get("display_sequence")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return fallback


def iter_canonical_days(
    days_payload: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    """Yield ``(day, canonical_model)`` pairs from a decorated payload."""
    for day in days_payload.get("days") or []:
        if not isinstance(day, dict):
            continue
        model = day.get("canonical")
        if isinstance(model, dict):
            yield day, model
