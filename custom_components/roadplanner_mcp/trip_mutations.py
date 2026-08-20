"""Revision-checked CRUD writes for the active Roadplanner trip.

Every mutation loads the current ``TripState`` through the injected
``TripRepository``, builds a candidate, and commits it via the shared
``_commit`` primitive. ``write_context_best_effort`` is injected the same
way ``experience_manager.py``'s collaborators take ``get_panel_payload`` -
this keeps the concrete dependency on context export inside the facade,
one directional.
"""

from __future__ import annotations

from copy import deepcopy
import shutil
from typing import Any, Callable

from .bounded_json import _bounded_json_value
from .identifiers import _new_id, validate_identifier
from .json_io import TripNotFoundError, ValidationError, _write_json_atomic, utc_now_iso
from .pitch_options import (
    apply_activate_option,
    apply_delete_option,
    apply_save_option,
    apply_set_option_status,
    apply_set_strategy,
    validate_preferences_input,
)
from .routing_helpers import _ROUTING_DETAIL_KEY, _trip_route_metrics
from .stop_ordering import reindex_explicit_positions
from .trip_documents import (
    DAY_SCHEMA_VERSION,
    MAX_DAYS,
    MAX_STOPS_PER_DAY,
    _without_audit_fields,
    normalize_day_document,
    normalize_stop,
    normalize_trip_document,
)
from .trip_repository import TripRepository
from .trip_state import TripState


class TripMutations:
    """Revision-checked, atomically-committed writes to the active trip."""

    def __init__(
        self,
        repository: TripRepository,
        *,
        write_context_best_effort: Callable[[TripState], None],
    ) -> None:
        self._repository = repository
        self._write_context_best_effort = write_context_best_effort

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
            _write_json_atomic(self._repository.pointer_path, new_pointer)
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

    def create_trip(
        self,
        *,
        title: str,
        actor: str,
        status: str = "planning",
        start_date: str | None = None,
        end_date: str | None = None,
        notes: str = "",
        activate: bool = False,
        expected_active_trip: str | None = None,
    ) -> dict[str, Any]:
        """Create a new trip; optionally make it the active one.

        Activation goes through the production ``set_active_trip`` - the
        pointer switch, its snapshot and its rollback live in exactly one
        place. If that switch fails, the just-created (still empty) trip
        directory is removed again so the system is exactly as before.
        """
        document = self._repository.create_trip_documents(
            title=title,
            actor=actor,
            status=status,
            start_date=start_date,
            end_date=end_date,
            notes=notes,
        )
        trip_id = document["trip"]["id"]
        activated = False
        if activate:
            try:
                self.set_active_trip(
                    trip_id=trip_id,
                    expected_active_trip=expected_active_trip,
                )
            except Exception:
                shutil.rmtree(
                    self._repository.trips_dir / trip_id, ignore_errors=True
                )
                raise
            activated = True
        return {
            "trip_id": trip_id,
            "title": document["trip"]["title"],
            "activated": activated,
            "revision": document["metadata"]["revision"],
        }

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

    def mutate_overnight_plan(
        self,
        *,
        day_id: str,
        operation: str,
        payload: dict[str, Any],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply one overnight-plan operation as a single atomic commit.

        The activate operation touches both the day's details and its stops
        list; running it through one commit (instead of update_day plus
        update_stop) is what makes "Plan B aktivieren" atomic.
        """
        day_id = validate_identifier(day_id, "day_id")
        if operation not in {
            "save_option",
            "delete_option",
            "set_option_status",
            "set_strategy",
            "activate_option",
        }:
            raise ValidationError(f"Unbekannte Stellplatz-Operation: {operation}")
        if not isinstance(payload, dict):
            raise ValidationError("'payload' muss ein JSON-Objekt sein")
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        now = utc_now_iso()
        extra: dict[str, Any] = {}
        if operation == "save_option":
            extra["option"] = apply_save_option(target, payload.get("option"), now=now)
        elif operation == "delete_option":
            apply_delete_option(target, validate_identifier(payload.get("option_id"), "option_id"))
        elif operation == "set_option_status":
            extra["option"] = apply_set_option_status(
                target,
                validate_identifier(payload.get("option_id"), "option_id"),
                str(payload.get("status") or ""),
                now=now,
            )
        elif operation == "set_strategy":
            extra["strategy"] = apply_set_strategy(target, str(payload.get("strategy") or ""))
        else:
            activation = apply_activate_option(
                target,
                validate_identifier(payload.get("option_id"), "option_id"),
                now=now,
                actor=actor,
            )
            stop_index = next(
                i for i, stop in enumerate(target["stops"]) if stop["id"] == activation["stop_id"]
            )
            target["stops"][stop_index] = normalize_stop(
                target["stops"][stop_index],
                index=stop_index,
                fallback_timestamp=now,
            )
            reindex_explicit_positions(target["stops"])
            extra.update(activation)
        target["day"]["updated_at"] = now
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation=f"pitch_{operation}",
            removed_files=[],
        )
        result.update(deepcopy(extra))
        result["day_id"] = day_id
        return result

    def update_pitch_preferences(
        self,
        *,
        preferences: dict[str, Any],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Replace the trip's pitch preferences, preserving other trip details."""
        validated = validate_preferences_input(preferences)
        previous = self._repository._load_state()
        self._repository._check_expected_trip(previous, expected_trip_id)
        self._repository._check_revision(previous, expected_revision)
        candidate = previous.clone()
        trip = candidate.trip_document["trip"]
        details = deepcopy(trip.get("details")) if isinstance(trip.get("details"), dict) else {}
        details["pitch_preferences"] = validated
        trip["details"] = details
        candidate.trip_document = normalize_trip_document(
            candidate.trip_document,
            expected_trip_id=previous.trip_id,
            fallback_timestamp=previous.trip_document["metadata"]["created_at"],
        )
        result = self._commit_and_notify(
            previous,
            candidate,
            actor=actor,
            operation="pitch_update_preferences",
            removed_files=[],
        )
        result["pitch_preferences"] = validated
        return result
