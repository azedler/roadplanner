"""Read-only query surface for the active (or a named) Roadplanner trip.

No file is ever modified here - every method loads state through the
injected ``TripRepository`` and returns a bounded, JSON-safe projection.
"""

from __future__ import annotations

from datetime import date
import json
from typing import Any

from .identifiers import _ID_PATTERN, validate_identifier
from .json_io import RoadplannerError, TripNotFoundError, ValidationError
from .routing_helpers import (
    _effective_routing_stops,
    _routing_leg_mode,
    ferry_crossing_minutes,
    _routing_summary,
    _stop_coordinate,
    _trip_route_metrics,
)
from .stop_ordering import canonical_order_stops
from .trip_documents import _ensure_optional_date
from .trip_projections import _compact_day, _compact_stop, _compact_trip, _first_trip_media
from .trip_repository import TripRepository

MAX_SUMMARY_DAYS = 60
MAX_SUMMARY_STOPS = 20
MAX_SEARCH_RESULTS = 50


class TripQueries:
    """Read-only projections over the canonical trip/day/stop documents."""

    def __init__(self, repository: TripRepository) -> None:
        self._repository = repository

    def load_trip(self) -> dict[str, Any]:
        """Return the complete active trip without modifying any file."""
        return self._repository._load_state().combined_export()

    def load_coordinator_payload(self) -> dict[str, Any]:
        """Return bounded entity data without side effects."""
        return self._repository._load_state().coordinator_payload()

    def get_trip_summary(
        self,
        *,
        trip_id: str | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        state = self._repository._load_state(trip_id=trip_id)
        ordered = state.ordered_days()
        current_date = today or date.today()
        next_day_document = next(
            (
                document
                for document in ordered
                if document["day"].get("date")
                and date.fromisoformat(document["day"]["date"]) >= current_date
            ),
            None,
        )
        if next_day_document is None:
            next_day_document = next(
                (
                    document
                    for document in ordered
                    if not document["day"].get("date")
                ),
                None,
            )
        summary_days = [
            _compact_day(
                document,
                sequence=sequence,
                include_details=False,
            )
            for sequence, document in enumerate(
                ordered[:MAX_SUMMARY_DAYS],
                start=1,
            )
        ]
        stop_count = sum(len(document["stops"]) for document in ordered)
        route_metrics = _trip_route_metrics(state)
        trip = _compact_trip(
            state.trip_document["trip"],
            include_details=True,
        )
        next_day = None
        if next_day_document is not None:
            sequence = ordered.index(next_day_document) + 1
            next_day = _compact_day(
                next_day_document,
                sequence=sequence,
                include_details=False,
            )
        return {
            "trip": trip,
            "revision": state.revision,
            "day_count": len(ordered),
            "stop_count": stop_count,
            "total_distance_km": route_metrics["total_distance_km"],
            "total_drive_minutes": route_metrics["total_drive_minutes"],
            "route_metrics": route_metrics,
            "next_day": next_day,
            "days": summary_days,
            "days_truncated": len(ordered) > MAX_SUMMARY_DAYS,
            "pending_unmanaged_day_files": state.unmanaged_day_files[:100],
            "summary": (
                f"{trip['title']}: {len(ordered)} Reisetage, "
                f"{stop_count} Stopps, Revision {state.revision}."
            ),
        }

    def get_days(
        self,
        *,
        trip_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
        include_stops: bool = False,
    ) -> dict[str, Any]:
        state = self._repository._load_state(trip_id=trip_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("'offset' muss eine nicht-negative Ganzzahl sein")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 60
        ):
            raise ValidationError("'limit' muss zwischen 1 und 60 liegen")
        ordered = state.ordered_days()
        selected = ordered[offset : offset + limit]
        result_days: list[dict[str, Any]] = []
        for sequence, document in enumerate(selected, start=offset + 1):
            day = _compact_day(
                document,
                sequence=sequence,
                include_details=True,
            )
            if include_stops:
                day["stops"] = [
                    _compact_stop(stop, include_details=True)
                    for stop in canonical_order_stops(document["stops"])[:MAX_SUMMARY_STOPS]
                ]
                day["stops_truncated"] = (
                    len(document["stops"]) > MAX_SUMMARY_STOPS
                )
            result_days.append(day)
        return {
            "revision": state.revision,
            "offset": offset,
            "limit": limit,
            "total": len(ordered),
            "days": result_days,
            "has_more": offset + len(selected) < len(ordered),
        }

    def get_day(
        self,
        *,
        day_id: str,
        trip_id: str | None = None,
        stop_offset: int = 0,
        stop_limit: int = 50,
    ) -> dict[str, Any]:
        state = self._repository._load_state(trip_id=trip_id)
        day_id = validate_identifier(day_id, "day_id")
        document = state.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if (
            isinstance(stop_offset, bool)
            or not isinstance(stop_offset, int)
            or stop_offset < 0
        ):
            raise ValidationError("'stop_offset' muss nicht-negativ sein")
        if (
            isinstance(stop_limit, bool)
            or not isinstance(stop_limit, int)
            or not 1 <= stop_limit <= 100
        ):
            raise ValidationError("'stop_limit' muss zwischen 1 und 100 liegen")
        stops = canonical_order_stops(document["stops"])
        selected = stops[stop_offset : stop_offset + stop_limit]
        sequence = next(
            index
            for index, ref in enumerate(state.trip_document["days"], start=1)
            if ref["id"] == day_id
        )
        return {
            "revision": state.revision,
            "day": _compact_day(
                document,
                sequence=sequence,
                include_details=True,
            ),
            "stops": [
                _compact_stop(stop, include_details=True)
                for stop in selected
            ],
            "stop_offset": stop_offset,
            "stop_limit": stop_limit,
            "stop_total": len(stops),
            "has_more_stops": stop_offset + len(selected) < len(stops),
        }

    def search_stops(
        self,
        *,
        query: str | None = None,
        trip_id: str | None = None,
        stop_type: str | None = None,
        day_id: str | None = None,
        day_date: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        state = self._repository._load_state(trip_id=trip_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_RESULTS
        ):
            raise ValidationError(
                f"'limit' muss zwischen 1 und {MAX_SEARCH_RESULTS} liegen"
            )
        query_text = (query or "").casefold().strip()
        if day_id is not None:
            day_id = validate_identifier(day_id, "day_id")
        normalized_date = _ensure_optional_date(day_date, "day_date")
        results: list[dict[str, Any]] = []
        total_matches = 0
        for day_sequence, document in enumerate(state.ordered_days(), start=1):
            day = document["day"]
            if day_id is not None and day["id"] != day_id:
                continue
            if normalized_date is not None and day.get("date") != normalized_date:
                continue
            for stop_sequence, stop in enumerate(canonical_order_stops(document["stops"]), start=1):
                if stop_type is not None and stop["type"] != stop_type:
                    continue
                searchable = json.dumps(
                    {
                        "name": stop["name"],
                        "notes": stop["notes"],
                        "location": stop["location"],
                        "details": stop["details"],
                    },
                    ensure_ascii=False,
                    default=str,
                ).casefold()
                if query_text and query_text not in searchable:
                    continue
                total_matches += 1
                if len(results) < limit:
                    result = _compact_stop(stop, include_details=True)
                    result.update(
                        {
                            "day_id": day["id"],
                            "day_date": day.get("date"),
                            "day_title": day["title"],
                            "day_sequence": day_sequence,
                            "stop_sequence": stop_sequence,
                        }
                    )
                    results.append(result)
        return {
            "revision": state.revision,
            "count": len(results),
            "total_matches": total_matches,
            "truncated": total_matches > len(results),
            "stops": results,
        }

    def list_trips(self) -> dict[str, Any]:
        """List all valid trip folders with bounded card metadata."""
        pointer = self._repository._load_pointer()
        trips: list[dict[str, Any]] = []
        if not self._repository.trips_dir.exists():
            return {"active_trip": pointer["active_trip"], "trips": []}
        for path in sorted(self._repository.trips_dir.iterdir()):
            if not path.is_dir() or not _ID_PATTERN.fullmatch(path.name):
                continue
            trip_path = path / "trip.json"
            if not trip_path.exists():
                continue
            try:
                state = self._repository._load_state(
                    trip_id=path.name,
                    validate_hash=True,
                )
            except RoadplannerError as err:
                trips.append(
                    {
                        "id": path.name,
                        "valid": False,
                        "active": path.name == pointer["active_trip"],
                        "error": str(err)[:500],
                    }
                )
                continue
            ordered = state.ordered_days()
            stop_count = sum(len(document["stops"]) for document in ordered)
            route_metrics = _trip_route_metrics(state)
            trip = state.trip_document["trip"]
            cover_image = _first_trip_media(state)
            trips.append(
                {
                    "id": path.name,
                    "title": trip["title"],
                    "status": trip.get("status"),
                    "start_date": trip.get("start_date"),
                    "end_date": trip.get("end_date"),
                    "revision": state.revision,
                    "day_count": len(ordered),
                    "stop_count": stop_count,
                    "total_distance_km": route_metrics["total_distance_km"],
                    "total_drive_minutes": route_metrics["total_drive_minutes"],
                    "route_metrics": route_metrics,
                    "cover_image": cover_image,
                    "active": path.name == pointer["active_trip"],
                    "valid": True,
                }
            )
        return {"active_trip": pointer["active_trip"], "trips": trips}

    def get_routing_plan(
        self,
        *,
        trip_id: str | None = None,
        day_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a revision-consistent, provider-neutral routing work plan.

        Route metrics are canonical writes and therefore follow the same
        active-trip rule as all other mutations. Reject a non-active trip
        before any external routing requests are started.
        """
        pointer = self._repository._load_pointer()
        selected_trip_id = trip_id or pointer["active_trip"]
        if selected_trip_id != pointer["active_trip"]:
            raise ValidationError(
                "Straßenrouten können nur für die aktive Reise berechnet werden"
            )
        state = self._repository._load_state(trip_id=selected_trip_id)
        requested: set[str] | None = None
        if day_ids is not None:
            requested = {
                validate_identifier(day_id, "day_id")
                for day_id in day_ids
            }
            missing = requested - set(state.day_documents)
            if missing:
                raise TripNotFoundError(
                    "Reisetag nicht gefunden: " + ", ".join(sorted(missing))
                )
        ordered = state.ordered_days()
        days: list[dict[str, Any]] = []
        for index, document in enumerate(ordered):
            day_id = document["day"]["id"]
            if requested is not None and day_id not in requested:
                continue
            effective = _effective_routing_stops(ordered, index)
            points: list[dict[str, Any]] = []
            missing_stops: list[dict[str, Any]] = []
            effective_entries: list[dict[str, Any]] = []
            for effective_index, item in enumerate(effective):
                stop = item["stop"]
                coordinate = _stop_coordinate(stop)
                reference = {
                    "day_id": day_id,
                    "source_day_id": item["source_day_id"],
                    "stop_id": stop.get("id"),
                    "name": stop.get("name"),
                    "type": stop.get("type"),
                    "inherited": bool(item["inherited"]),
                }
                effective_entries.append(
                    {
                        "effective_index": effective_index,
                        "stop": stop,
                        "coordinate": coordinate,
                        "reference": reference,
                    }
                )
                if coordinate is None:
                    missing_stops.append(reference)
                    continue
                points.append(
                    {
                        **reference,
                        "latitude": coordinate[0],
                        "longitude": coordinate[1],
                        "_effective_index": effective_index,
                    }
                )

            route_warnings: list[str] = []
            for point_index in range(max(0, len(points) - 1)):
                source_point = points[point_index]
                target_point = points[point_index + 1]
                source_index = int(source_point["_effective_index"])
                target_index = int(target_point["_effective_index"])
                if target_index != source_index + 1:
                    mode = "break"
                    reason = "Mindestens ein dazwischenliegender Stopp besitzt noch keine GPS-Daten."
                else:
                    source_stop = effective_entries[source_index]["stop"]
                    target_stop = effective_entries[target_index]["stop"]
                    mode, reason = _routing_leg_mode(source_stop, target_stop)
                    if mode == "ferry":
                        # Both terminal stops are in hand exactly here, and
                        # their times are the only real source for how long
                        # the crossing takes.
                        crossing = ferry_crossing_minutes(source_stop, target_stop)
                        if crossing is not None:
                            source_point["ferry_minutes"] = crossing
                source_point["mode_to_next"] = mode
                if reason:
                    source_point["mode_reason"] = reason
                    if reason not in route_warnings:
                        route_warnings.append(reason)
            for point in points:
                point.pop("_effective_index", None)

            days.append(
                {
                    "day_id": day_id,
                    "sequence": index + 1,
                    "date": document["day"].get("date"),
                    "title": document["day"].get("title"),
                    "effective_stop_count": len(effective),
                    "point_count": len(points),
                    "points": points,
                    "missing_stops": missing_stops,
                    "route_warnings": route_warnings,
                    "existing_routing": _routing_summary(document),
                }
            )
        return {
            "trip_id": state.trip_id,
            "revision": state.revision,
            "days": days,
            "route_metrics": _trip_route_metrics(state),
        }

    def export_trip(self) -> dict[str, Any]:
        state = self._repository._load_state()
        return {
            "trip_id": state.trip_id,
            "revision": state.revision,
            "trip_json": json.dumps(
                state.combined_export(),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
        }
