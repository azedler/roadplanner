"""Roadplanner storage, validation, migration, and domain logic.

The module intentionally has no Home Assistant imports. The canonical route is
split into a small trip index plus one JSON document per travel day:

    roadbook/active_trip.json
    roadbook/trips/<trip_id>/trip.json
    roadbook/trips/<trip_id>/days/<day_id>.json

Only these canonical files are managed. Other files in an existing roadbook are
left untouched.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
import json
import logging
from pathlib import Path
from typing import Any

from .bounded_json import _bounded_json_value
from .identifiers import _ID_PATTERN, _new_id, _stable_id, validate_identifier
from .json_io import (
    RevisionConflictError,
    RoadplannerError,
    StorageError,
    TripNotFoundError,
    ValidationError,
    _json_bytes,
    _read_json,
    _write_json_atomic,
    _write_text_atomic,
    utc_now_iso,
)
from .json_tree_validation import _validate_json_tree
from .routing_helpers import (
    _ROUTING_DETAIL_KEY,
    _effective_routing_stops,
    _routing_leg_mode,
    _routing_summary,
    _stop_coordinate,
    _trip_route_metrics,
)
from .stop_ordering import canonical_order_stops, reindex_explicit_positions
from .trip_documents import (
    DAY_SCHEMA_VERSION,
    HANDOFF_CONTEXT_SCHEMA_VERSION,
    MAX_DAYS,
    MAX_STOPS_PER_DAY,
    _ensure_optional_date,
    _without_audit_fields,
    normalize_day_document,
    normalize_stop,
    normalize_trip_document,
)
from .trip_projections import _compact_day, _compact_stop, _compact_trip, _first_trip_media
from .trip_repository import TripRepository
from .trip_state import TripState

MAX_SUMMARY_DAYS = 60
MAX_SUMMARY_STOPS = 20
MAX_SEARCH_RESULTS = 50
MAX_CONTEXT_DAYS = 180
MAX_CONTEXT_STOPS_PER_DAY = 40
MAX_CONTEXT_JSON_BYTES = 3 * 1024 * 1024
MAX_CONTEXT_MARKDOWN_CHARS = 300_000

_LOGGER = logging.getLogger(__name__)



@dataclass(slots=True)
class RoadplannerStore:
    """Facade composing the trip repository with queries/mutations/changesets/context export."""

    roadbook_dir: Path
    backup_dir: Path
    handoff_dir: Path
    backup_count: int = 20
    _repository: TripRepository = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._repository = TripRepository(
            roadbook_dir=self.roadbook_dir,
            backup_dir=self.backup_dir,
            handoff_dir=self.handoff_dir,
            backup_count=self.backup_count,
        )

    @property
    def pointer_path(self) -> Path:
        return self._repository.pointer_path

    @property
    def trips_dir(self) -> Path:
        return self._repository.trips_dir

    def initialize(self, *, create_if_missing: bool = True) -> dict[str, Any]:
        """Initialize canonical split files without changing the active pointer."""
        final_state = self._repository.initialize_documents(
            create_if_missing=create_if_missing
        )
        self._write_context_best_effort(final_state)
        return final_state.coordinator_payload()

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
        if not self.trips_dir.exists():
            return {"active_trip": pointer["active_trip"], "trips": []}
        for path in sorted(self.trips_dir.iterdir()):
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

    def apply_routing_results(
        self,
        *,
        results: list[dict[str, Any]],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one or more derived routes atomically in one revision."""
        if not isinstance(results, list) or not results:
            raise ValidationError("Es wurden keine Routenberechnungen übergeben")
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        candidate = previous.clone()
        applied: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = utc_now_iso()
        for item in results:
            if not isinstance(item, dict):
                raise ValidationError("Routing-Ergebnis muss ein JSON-Objekt sein")
            day_id = validate_identifier(item.get("day_id"), "day_id")
            if day_id in seen:
                raise ValidationError(f"Doppeltes Routing-Ergebnis für {day_id}")
            seen.add(day_id)
            document = candidate.day_documents.get(day_id)
            if document is None:
                raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
            routing = item.get("routing")
            if not isinstance(routing, dict):
                raise ValidationError(f"Routing-Ergebnis für {day_id} fehlt")
            distance_m = routing.get("distance_m")
            duration_s = routing.get("duration_s")
            if (
                isinstance(distance_m, bool)
                or isinstance(duration_s, bool)
                or not isinstance(distance_m, (int, float))
                or not isinstance(duration_s, (int, float))
                or distance_m < 0
                or duration_s < 0
            ):
                raise ValidationError(f"Ungültige Routing-Metrik für {day_id}")
            missing_stops = item.get("missing_stops", [])
            if not isinstance(missing_stops, list):
                raise ValidationError("missing_stops muss eine Liste sein")
            normalized_routing = deepcopy(routing)
            requested_status = str(normalized_routing.get("status") or "calculated")
            if requested_status not in {"calculated", "partial"}:
                requested_status = "calculated"
            if missing_stops or int(normalized_routing.get("gap_count") or 0) > 0:
                requested_status = "partial"
            normalized_routing.update(
                {
                    "schema_version": max(1, int(normalized_routing.get("schema_version") or 1)),
                    "status": requested_status,
                    "missing_stop_count": len(missing_stops),
                    "missing_stops": _bounded_json_value(
                        missing_stops,
                        max_items=500,
                        max_string=500,
                    ),
                    "managed_metrics": True,
                    "geometry_stale": False,
                }
            )
            normalized_routing.pop("invalidated_at", None)
            normalized_routing.pop("invalidated_reason", None)
            details = document["day"].get("details")
            if not isinstance(details, dict):
                details = {}
            details[_ROUTING_DETAIL_KEY] = normalized_routing
            document["day"]["details"] = details
            document["day"]["distance_km"] = round(float(distance_m) / 1000.0, 1)
            document["day"]["drive_minutes"] = max(0, int(round(float(duration_s) / 60.0)))
            document["day"]["updated_at"] = now
            applied.append(
                {
                    "day_id": day_id,
                    "status": normalized_routing["status"],
                    "distance_km": document["day"]["distance_km"],
                    "drive_minutes": document["day"]["drive_minutes"],
                    "point_count": normalized_routing.get("point_count"),
                    "missing_stop_count": len(missing_stops),
                    "ferry_distance_km": round(float(normalized_routing.get("ferry_distance_m") or 0.0) / 1000.0, 1),
                    "gap_count": int(normalized_routing.get("gap_count") or 0),
                }
            )
        result, verified_commit = self._repository._commit(
            previous,
            candidate,
            actor=actor,
            operation="calculate_routes",
            removed_files=[],
        )
        if verified_commit is not None:
            self._write_context_best_effort(verified_commit)
        verified = self._repository._load_state()
        result["routing_results"] = applied
        result["route_metrics"] = _trip_route_metrics(verified)
        return result

    def set_active_trip(
        self,
        *,
        trip_id: str,
        expected_active_trip: str | None = None,
    ) -> dict[str, Any]:
        trip_id = validate_identifier(trip_id, "trip_id")
        pointer = self._repository._load_pointer()
        if (
            expected_active_trip is not None
            and pointer["active_trip"] != expected_active_trip
        ):
            raise ValidationError(
                "Die aktive Reise wurde zwischenzeitlich gewechselt: "
                f"{pointer['active_trip']}"
            )
        target = self._repository._load_state(trip_id=trip_id, validate_hash=True)
        if pointer["active_trip"] == trip_id:
            return {
                "changed": False,
                "active_trip": trip_id,
                "trip": target.coordinator_payload(),
            }
        snapshot = self._repository._create_snapshot(pointer["active_trip"], "before-trip-switch")
        new_pointer = deepcopy(pointer)
        new_pointer["active_trip"] = trip_id
        new_pointer["last_opened"] = utc_now_iso()
        try:
            _write_json_atomic(self.pointer_path, new_pointer)
            verified = self._repository._load_state(trip_id=trip_id, validate_hash=True)
        except Exception:
            self._repository._restore_snapshot(snapshot)
            raise
        self._write_context_best_effort(verified)
        return {
            "changed": True,
            "active_trip": trip_id,
            "trip": verified.coordinator_payload(),
        }

    def _commit_and_notify(
        self,
        previous: TripState,
        candidate: TripState,
        *,
        actor: str,
        operation: str,
        removed_files: list[str],
    ) -> dict[str, Any]:
        result, verified = self._repository._commit(
            previous,
            candidate,
            actor=actor,
            operation=operation,
            removed_files=removed_files,
        )
        if verified is not None:
            self._write_context_best_effort(verified)
        return result

    def update_trip(
        self,
        *,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
            "title",
            "status",
            "start_date",
            "end_date",
            "travelers",
            "vehicle",
            "preferences",
            "notes",
            "details",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Reisefelder: " + ", ".join(sorted(unknown))
            )
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        candidate = previous.clone()
        candidate.trip_document["trip"].update(deepcopy(patch))
        candidate.trip_document = normalize_trip_document(
            candidate.trip_document,
            expected_trip_id=previous.trip_id,
            fallback_timestamp=previous.trip_document["metadata"]["created_at"],
        )
        return self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="update_trip",
            removed_files=[],
        )

    def add_day(
        self,
        *,
        actor: str,
        expected_revision: int,
        day_date: str | None = None,
        title: str | None = None,
        start: str = "",
        end: str = "",
        distance_km: int | float | None = None,
        drive_minutes: int | None = None,
        status: str = "planned",
        notes: str = "",
        details: dict[str, Any] | None = None,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        candidate = previous.clone()
        if len(candidate.trip_document["days"]) >= MAX_DAYS:
            raise ValidationError(f"Maximal {MAX_DAYS} Reisetage werden unterstützt")
        now = utc_now_iso()
        day_id = _new_id("day")
        raw_document = {
            "schema_version": DAY_SCHEMA_VERSION,
            "day": {
                "id": day_id,
                "date": day_date,
                "title": title or (f"Tag {day_date}" if day_date else "Neuer Reisetag"),
                "start": start,
                "end": end,
                "distance_km": distance_km,
                "drive_minutes": drive_minutes,
                "status": status,
                "notes": notes,
                "details": details or {},
                "created_at": now,
                "updated_at": now,
            },
            "stops": [],
        }
        document = normalize_day_document(
            raw_document,
            fallback_id=day_id,
            fallback_timestamp=now,
        )
        candidate.day_documents[day_id] = document
        ref = {"id": day_id, "file": f"days/{day_id}.json"}
        refs = candidate.trip_document["days"]
        insert_at = self._repository._insert_index(position, len(refs))
        refs.insert(insert_at, ref)
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="add_day",
            removed_files=[],
        )
        result["day"] = deepcopy(document["day"])
        result["position"] = insert_at + 1
        return result

    def update_day(
        self,
        *,
        day_id: str,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
            "date",
            "title",
            "start",
            "end",
            "distance_km",
            "drive_minutes",
            "status",
            "notes",
            "details",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Tagesfelder: " + ", ".join(sorted(unknown))
            )
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        if day_id not in previous.day_documents:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        candidate = previous.clone()
        document = candidate.day_documents[day_id]
        before = _without_audit_fields(document["day"])
        document["day"].update(deepcopy(patch))
        normalized = normalize_day_document(
            document,
            fallback_id=day_id,
            fallback_timestamp=document["day"]["created_at"],
        )
        if _without_audit_fields(normalized["day"]) != before:
            normalized["day"]["updated_at"] = utc_now_iso()
        candidate.day_documents[day_id] = normalized
        if position is not None:
            refs = candidate.trip_document["days"]
            old_index = next(i for i, ref in enumerate(refs) if ref["id"] == day_id)
            ref = refs.pop(old_index)
            refs.insert(self._repository._insert_index(position, len(refs)), ref)
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="update_day",
            removed_files=[],
        )
        result["day"] = deepcopy(normalized["day"])
        return result

    def remove_day(
        self,
        *,
        day_id: str,
        actor: str,
        expected_revision: int,
        remove_stops: bool = False,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if document["stops"] and not remove_stops:
            raise ValidationError(
                "Der Reisetag enthält Stopps. Zum Löschen 'remove_stops=true' setzen."
            )
        candidate = previous.clone()
        ref = next(
            ref for ref in candidate.trip_document["days"] if ref["id"] == day_id
        )
        candidate.trip_document["days"] = [
            item for item in candidate.trip_document["days"] if item["id"] != day_id
        ]
        removed = candidate.day_documents.pop(day_id)
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="remove_day",
            removed_files=[ref["file"]],
        )
        result["removed_day"] = deepcopy(removed["day"])
        result["removed_stop_count"] = len(removed["stops"])
        return result

    def add_stop(
        self,
        *,
        day_id: str,
        name: str,
        actor: str,
        expected_revision: int,
        stop_type: str = "waypoint",
        arrival_time: str | None = None,
        departure_time: str | None = None,
        location: dict[str, Any] | None = None,
        notes: str = "",
        details: dict[str, Any] | None = None,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if len(document["stops"]) >= MAX_STOPS_PER_DAY:
            raise ValidationError(
                f"Ein Reisetag darf maximal {MAX_STOPS_PER_DAY} Stopps enthalten"
            )
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        now = utc_now_iso()
        stop_id = _new_id("stop")
        stop = normalize_stop(
            {
                "id": stop_id,
                "name": name,
                "type": stop_type,
                "arrival_time": arrival_time,
                "departure_time": departure_time,
                "location": location or {},
                "notes": notes,
                "details": details or {},
                "created_at": now,
                "updated_at": now,
            },
            index=len(target["stops"]),
            fallback_timestamp=now,
        )
        insert_at = self._repository._insert_index(position, len(target["stops"]))
        target["stops"].insert(insert_at, stop)
        reindex_explicit_positions(target["stops"])
        target["day"]["updated_at"] = now
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="add_stop",
            removed_files=[],
        )
        result["stop"] = deepcopy(stop)
        result["day_id"] = day_id
        result["position"] = insert_at + 1
        return result

    def update_stop(
        self,
        *,
        day_id: str,
        stop_id: str,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        stop_id = validate_identifier(stop_id, "stop_id")
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
            "name",
            "type",
            "arrival_time",
            "departure_time",
            "location",
            "notes",
            "details",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Stoppfelder: " + ", ".join(sorted(unknown))
            )
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        old_index = next(
            (i for i, stop in enumerate(document["stops"]) if stop["id"] == stop_id),
            None,
        )
        if old_index is None:
            raise TripNotFoundError(f"Stopp nicht gefunden: {stop_id}")
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        raw_stop = deepcopy(target["stops"][old_index])
        before = _without_audit_fields(raw_stop)
        raw_stop.update(deepcopy(patch))
        raw_stop["id"] = stop_id
        normalized = normalize_stop(
            raw_stop,
            index=old_index,
            fallback_timestamp=raw_stop["created_at"],
        )
        changed_fields = _without_audit_fields(normalized) != before
        if changed_fields:
            normalized["updated_at"] = utc_now_iso()
        target["stops"][old_index] = normalized
        if position is not None:
            moved = target["stops"].pop(old_index)
            target["stops"].insert(
                self._repository._insert_index(position, len(target["stops"])),
                moved,
            )
        # Every stop mutation leaves a complete explicit sequence behind.
        reindex_explicit_positions(target["stops"])
        if changed_fields or position is not None:
            target["day"]["updated_at"] = utc_now_iso()
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="update_stop",
            removed_files=[],
        )
        result["stop"] = deepcopy(
            next(item for item in target["stops"] if item["id"] == stop_id)
        )
        result["day_id"] = day_id
        return result

    def remove_stop(
        self,
        *,
        day_id: str,
        stop_id: str,
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        stop_id = validate_identifier(stop_id, "stop_id")
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        old_index = next(
            (i for i, stop in enumerate(document["stops"]) if stop["id"] == stop_id),
            None,
        )
        if old_index is None:
            raise TripNotFoundError(f"Stopp nicht gefunden: {stop_id}")
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        removed = target["stops"].pop(old_index)
        reindex_explicit_positions(target["stops"])
        target["day"]["updated_at"] = utc_now_iso()
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="remove_stop",
            removed_files=[],
        )
        result["removed_stop"] = deepcopy(removed)
        result["day_id"] = day_id
        return result

    def create_backup(self, reason: str = "manual") -> dict[str, Any]:
        return self._repository.create_backup(reason)

    def adopt_external_changes(
        self,
        *,
        actor: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        state = self._repository._load_state(validate_hash=False)
        self._repository._check_revision(state, expected_revision)
        actual_hash = state.content_hash()
        if state.trip_document["metadata"].get("content_hash") == actual_hash:
            return {
                "changed": False,
                "revision": state.revision,
                "trip": state.coordinator_payload(),
            }
        snapshot = self._repository._create_snapshot(state.trip_id, "adopt-external-changes")
        now = utc_now_iso()
        state.trip_document["metadata"].update(
            {
                "revision": state.revision + 1,
                "updated_at": now,
                "updated_by": (actor or "unknown")[:200],
                "last_operation": "adopt_external_changes",
                "content_hash": actual_hash,
            }
        )
        self._repository._write_state_transaction(
            state,
            snapshot=snapshot,
            operation="adopt_external_changes",
            removed_files=[],
        )
        verified = self._repository._load_state()
        self._write_context_best_effort(verified)
        return {
            "changed": True,
            "revision": verified.revision,
            "trip": verified.coordinator_payload(),
        }

    def preview_changeset(self, changeset: dict[str, Any]) -> dict[str, Any]:
        """Validate a ChangeSet against the active trip without writing files."""
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        current = self._repository._load_state()
        summary = changeset_summary(normalized)
        response: dict[str, Any] = {
            **summary,
            "current_trip_id": current.trip_id,
            "current_revision": current.revision,
            "applicable": False,
            "would_change": False,
        }
        if normalized["trip_id"] != current.trip_id:
            response.update(
                {
                    "status": "wrong_trip",
                    "reason": (
                        "ChangeSet gehört zur Reise "
                        f"{normalized['trip_id']}, aktiv ist {current.trip_id}."
                    ),
                }
            )
            return response
        if normalized["base_revision"] != current.revision:
            response.update(
                {
                    "status": "revision_conflict",
                    "reason": (
                        "ChangeSet basiert auf Revision "
                        f"{normalized['base_revision']}, aktuell ist "
                        f"{current.revision}."
                    ),
                }
            )
            return response

        execution = execute_changeset(current, normalized)
        would_change = (
            current.business_value() != execution.candidate.business_value()
        )
        response.update(
            {
                "status": "ready",
                "applicable": True,
                "would_change": would_change,
                "target_revision": current.revision + (1 if would_change else 0),
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        return response

    def inspect_changeset_for_import(
        self,
        changeset: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate an external ChangeSet deeply, including stale revisions.

        A stale base revision remains a review conflict, but all referenced
        entities and operation payloads are still checked against the current
        active trip before the ChangeSet is admitted to the inbox.
        """
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        current = self._repository._load_state()
        summary = changeset_summary(normalized)
        response: dict[str, Any] = {
            **summary,
            "current_trip_id": current.trip_id,
            "current_revision": current.revision,
            "applicable": False,
            "would_change": False,
        }
        if normalized["trip_id"] != current.trip_id:
            response.update(
                {
                    "status": "wrong_trip",
                    "reason": (
                        "ChangeSet gehört zur Reise "
                        f"{normalized['trip_id']}, aktiv ist {current.trip_id}."
                    ),
                }
            )
            return response

        execution = execute_changeset(current, normalized)
        would_change = (
            current.business_value() != execution.candidate.business_value()
        )
        response.update(
            {
                "would_change": would_change,
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        if normalized["base_revision"] != current.revision:
            response.update(
                {
                    "status": "revision_conflict",
                    "reason": (
                        "ChangeSet basiert auf Revision "
                        f"{normalized['base_revision']}, aktuell ist "
                        f"{current.revision}."
                    ),
                }
            )
            return response

        response.update(
            {
                "status": "ready",
                "applicable": True,
                "target_revision": current.revision + (1 if would_change else 0),
            }
        )
        return response

    def apply_changeset(
        self,
        *,
        changeset: dict[str, Any],
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply all ChangeSet operations atomically in one route revision."""
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        previous = self._repository._load_state()
        if normalized["trip_id"] != previous.trip_id:
            raise ValidationError(
                "ChangeSet gehört zur Reise "
                f"'{normalized['trip_id']}', aktiv ist '{previous.trip_id}'"
            )
        if expected_revision is not None:
            self._repository._check_revision(previous, expected_revision)
            if expected_revision != normalized["base_revision"]:
                raise ValidationError(
                    "expected_revision stimmt nicht mit base_revision des "
                    "ChangeSets überein"
                )
        self._repository._check_revision(previous, normalized["base_revision"])
        execution = execute_changeset(previous, normalized)
        result = self._commit_and_notify(
            previous,
            execution.candidate,
            actor=actor,
            operation="apply_changeset",
            removed_files=execution.removed_files,
        )
        result.update(
            {
                **changeset_summary(normalized),
                "revision_before": previous.revision,
                "revision_after": result["revision"],
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        return result

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

    def _context_stop(self, stop: dict[str, Any]) -> dict[str, Any]:
        """Return a deliberately small stop projection for external planning."""
        details = stop.get("details", {})
        result = {
            "id": stop["id"],
            "name": _bounded_json_value(stop["name"], max_string=500),
            "type": _bounded_json_value(stop["type"], max_string=100),
            "arrival_time": stop.get("arrival_time"),
            "departure_time": stop.get("departure_time"),
            "location": _bounded_json_value(
                stop.get("location", {}),
                max_depth=4,
                max_items=25,
                max_string=500,
            ),
            "notes": _bounded_json_value(stop.get("notes", ""), max_string=1_500),
        }
        if isinstance(details, dict) and details:
            result["detail_sections"] = sorted(str(key) for key in details)[:50]
        return result

    def _context_payload(self, current: TripState) -> dict[str, Any]:
        """Build a bounded, read-only route context for external assistants."""
        ordered = current.ordered_days()
        total_stops = sum(len(document["stops"]) for document in ordered)
        context: dict[str, Any] = {
            "schema_version": HANDOFF_CONTEXT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "trip_id": current.trip_id,
            "base_revision": current.revision,
            "route": {
                "trip": _compact_trip(
                    current.trip_document["trip"],
                    include_details=True,
                ),
                "day_count": len(ordered),
                "stop_count": total_stops,
                "days": [],
                "days_truncated": False,
                "stops_truncated": False,
            },
            "instructions": {
                "purpose": "Read-only planning context for external assistants",
                "changeset_kind": "roadplanner_changeset",
                "do_not_edit_canonical_files": True,
                "include_trip_id_and_base_revision_in_changeset": True,
            },
        }
        route = context["route"]
        used_bytes = len(_json_bytes(context))
        represented_stops = 0

        for sequence, document in enumerate(ordered, start=1):
            if len(route["days"]) >= MAX_CONTEXT_DAYS:
                route["days_truncated"] = True
                break
            day = _compact_day(
                document,
                sequence=sequence,
                include_details=False,
            )
            day["stops"] = []
            day["stops_truncated"] = False
            day_bytes = len(_json_bytes(day)) + 64
            if used_bytes + day_bytes > MAX_CONTEXT_JSON_BYTES:
                route["days_truncated"] = True
                break
            route["days"].append(day)
            used_bytes += day_bytes

            for stop in canonical_order_stops(document["stops"]):
                if len(day["stops"]) >= MAX_CONTEXT_STOPS_PER_DAY:
                    day["stops_truncated"] = True
                    route["stops_truncated"] = True
                    break
                compact_stop = self._context_stop(stop)
                stop_bytes = len(_json_bytes(compact_stop)) + 32
                if used_bytes + stop_bytes > MAX_CONTEXT_JSON_BYTES:
                    day["stops_truncated"] = True
                    route["stops_truncated"] = True
                    route["days_truncated"] = sequence < len(ordered)
                    break
                day["stops"].append(compact_stop)
                represented_stops += 1
                used_bytes += stop_bytes
            if used_bytes >= MAX_CONTEXT_JSON_BYTES:
                break

        route["represented_day_count"] = len(route["days"])
        route["represented_stop_count"] = represented_stops
        if len(route["days"]) < len(ordered):
            route["days_truncated"] = True
        if represented_stops < total_stops:
            route["stops_truncated"] = True
        return context

    def _context_markdown(self, current: TripState) -> str:
        """Build a bounded human-readable context companion."""
        trip = current.trip_document["trip"]
        lines = [
            f"# {trip['title']}",
            "",
            f"Trip-ID: `{current.trip_id}`  ",
            f"Basis-Revision: `{current.revision}`  ",
            "",
        ]
        if trip.get("start_date") or trip.get("end_date"):
            lines.extend(
                [
                    f"Zeitraum: {trip.get('start_date') or '?'} bis "
                    f"{trip.get('end_date') or '?'}",
                    "",
                ]
            )

        truncated = False
        represented_stops = 0
        ordered = current.ordered_days()
        for sequence, document in enumerate(
            ordered[:MAX_CONTEXT_DAYS],
            start=1,
        ):
            day = document["day"]
            candidate = [
                f"## {sequence}. {day['title']} "
                f"({day.get('date') or 'ohne Datum'})",
            ]
            if day.get("start") or day.get("end"):
                candidate.append(
                    f"{day.get('start') or '?'} → {day.get('end') or '?'}"
                )
            if day.get("notes"):
                note = str(day["notes"])
                candidate.append(note[:1_500] + ("…" if len(note) > 1_500 else ""))
            details = day.get("details", {})
            if isinstance(details, dict):
                preferences = details.get("planning_preferences", [])
                if isinstance(preferences, list):
                    for preference in preferences[:20]:
                        if not isinstance(preference, dict):
                            continue
                        text = str(preference.get("text") or "").strip()
                        if text:
                            candidate.append(
                                "- Präferenz "
                                f"[`{preference.get('id', '?')}`]: {text[:1_000]}"
                            )
            for stop in canonical_order_stops(document["stops"])[:MAX_CONTEXT_STOPS_PER_DAY]:
                candidate.append(
                    f"- {stop['name']} [{stop['type']}] (`{stop['id']}`)"
                )
                represented_stops += 1
            if len(document["stops"]) > MAX_CONTEXT_STOPS_PER_DAY:
                candidate.append("- _Weitere Stopps nicht dargestellt._")
                truncated = True
            candidate.append("")
            candidate_text = "\n".join(candidate)
            existing_length = sum(len(line) + 1 for line in lines)
            if existing_length + len(candidate_text) > MAX_CONTEXT_MARKDOWN_CHARS:
                truncated = True
                break
            lines.extend(candidate)

        if len(ordered) > MAX_CONTEXT_DAYS:
            truncated = True
        if truncated:
            lines.extend(
                [
                    "_Der Kontext wurde für mobile Nutzung gekürzt. "
                    "Home Assistant enthält die vollständige Route._",
                    "",
                ]
            )
        lines.extend(
            [
                "---",
                "Erstelle Änderungen als roadplanner_changeset mit Trip-ID, "
                "Basis-Revision und gezielten Operationen. Diese Datei ist nur "
                "Lesekontext.",
                f"Dargestellte Stopps: {represented_stops}.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    def get_context_payload(self) -> dict[str, Any]:
        """Return bounded JSON context for an authenticated external bridge."""
        return self._context_payload(self._repository._load_state())

    def get_context_markdown(self) -> dict[str, Any]:
        """Return bounded Markdown context for an authenticated external bridge."""
        current = self._repository._load_state()
        context = self._context_payload(current)
        return {
            "trip_id": current.trip_id,
            "revision": current.revision,
            "content": self._context_markdown(current),
            "days_truncated": context["route"]["days_truncated"],
            "stops_truncated": context["route"]["stops_truncated"],
        }

    def _write_context_best_effort(self, state: TripState) -> None:
        """Refresh derived context without making a canonical mutation fail."""
        try:
            self.write_context(state)
        except Exception as err:  # Derived export must never roll back canonical data.
            _LOGGER.warning("Roadplanner context export failed: %s", err)

    def write_context(self, state: TripState | None = None) -> dict[str, Any]:
        """Write bounded derived context files for Drive or OneDrive sync."""
        current = state or self._repository._load_state()
        outbox = self.handoff_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        context = self._context_payload(current)
        json_path = outbox / "roadplanner_context.json"
        _write_json_atomic(json_path, context)
        markdown_path = outbox / "roadplanner_context.md"
        _write_text_atomic(markdown_path, self._context_markdown(current))
        return {
            "trip_id": current.trip_id,
            "revision": current.revision,
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "days_truncated": context["route"]["days_truncated"],
            "stops_truncated": context["route"]["stops_truncated"],
        }

