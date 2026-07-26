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
import logging
from pathlib import Path
from typing import Any

from .bounded_json import _bounded_json_value
from .identifiers import _new_id, _stable_id, validate_identifier
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
from .routing_helpers import _ROUTING_DETAIL_KEY, _trip_route_metrics
from .stop_ordering import canonical_order_stops, reindex_explicit_positions
from .trip_documents import (
    DAY_SCHEMA_VERSION,
    HANDOFF_CONTEXT_SCHEMA_VERSION,
    MAX_DAYS,
    MAX_STOPS_PER_DAY,
    _without_audit_fields,
    normalize_day_document,
    normalize_stop,
    normalize_trip_document,
)
from .trip_projections import _compact_day, _compact_trip
from .trip_queries import TripQueries
from .trip_repository import TripRepository
from .trip_state import TripState

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
    _queries: TripQueries = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._repository = TripRepository(
            roadbook_dir=self.roadbook_dir,
            backup_dir=self.backup_dir,
            handoff_dir=self.handoff_dir,
            backup_count=self.backup_count,
        )
        self._queries = TripQueries(self._repository)

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
        return self._queries.load_trip()

    def load_coordinator_payload(self) -> dict[str, Any]:
        """Return bounded entity data without side effects."""
        return self._queries.load_coordinator_payload()

    def get_trip_summary(
        self,
        *,
        trip_id: str | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        return self._queries.get_trip_summary(trip_id=trip_id, today=today)

    def get_days(
        self,
        *,
        trip_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
        include_stops: bool = False,
    ) -> dict[str, Any]:
        return self._queries.get_days(
            trip_id=trip_id,
            offset=offset,
            limit=limit,
            include_stops=include_stops,
        )

    def get_day(
        self,
        *,
        day_id: str,
        trip_id: str | None = None,
        stop_offset: int = 0,
        stop_limit: int = 50,
    ) -> dict[str, Any]:
        return self._queries.get_day(
            day_id=day_id,
            trip_id=trip_id,
            stop_offset=stop_offset,
            stop_limit=stop_limit,
        )

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
        return self._queries.search_stops(
            query=query,
            trip_id=trip_id,
            stop_type=stop_type,
            day_id=day_id,
            day_date=day_date,
            limit=limit,
        )

    def list_trips(self) -> dict[str, Any]:
        """List all valid trip folders with bounded card metadata."""
        return self._queries.list_trips()

    def get_routing_plan(
        self,
        *,
        trip_id: str | None = None,
        day_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._queries.get_routing_plan(trip_id=trip_id, day_ids=day_ids)

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
        return self._queries.export_trip()

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

