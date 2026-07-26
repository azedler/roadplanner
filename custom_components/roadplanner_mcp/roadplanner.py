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

from dataclasses import dataclass, field
from datetime import date
import logging
from pathlib import Path
from typing import Any

from .bounded_json import _bounded_json_value
from .identifiers import _stable_id, validate_identifier
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
from .stop_ordering import canonical_order_stops
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
from .trip_mutations import TripMutations
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
    _mutations: TripMutations = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._repository = TripRepository(
            roadbook_dir=self.roadbook_dir,
            backup_dir=self.backup_dir,
            handoff_dir=self.handoff_dir,
            backup_count=self.backup_count,
        )
        self._queries = TripQueries(self._repository)
        self._mutations = TripMutations(
            self._repository,
            write_context_best_effort=self._write_context_best_effort,
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
        return self._mutations.apply_routing_results(
            results=results,
            actor=actor,
            expected_revision=expected_revision,
            expected_trip_id=expected_trip_id,
        )

    def set_active_trip(
        self,
        *,
        trip_id: str,
        expected_active_trip: str | None = None,
    ) -> dict[str, Any]:
        return self._mutations.set_active_trip(
            trip_id=trip_id,
            expected_active_trip=expected_active_trip,
        )

    def update_trip(
        self,
        *,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        return self._mutations.update_trip(
            patch=patch,
            actor=actor,
            expected_revision=expected_revision,
            expected_trip_id=expected_trip_id,
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
        return self._mutations.add_day(
            actor=actor,
            expected_revision=expected_revision,
            day_date=day_date,
            title=title,
            start=start,
            end=end,
            distance_km=distance_km,
            drive_minutes=drive_minutes,
            status=status,
            notes=notes,
            details=details,
            position=position,
            expected_trip_id=expected_trip_id,
        )

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
        return self._mutations.update_day(
            day_id=day_id,
            patch=patch,
            actor=actor,
            expected_revision=expected_revision,
            position=position,
            expected_trip_id=expected_trip_id,
        )

    def remove_day(
        self,
        *,
        day_id: str,
        actor: str,
        expected_revision: int,
        remove_stops: bool = False,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        return self._mutations.remove_day(
            day_id=day_id,
            actor=actor,
            expected_revision=expected_revision,
            remove_stops=remove_stops,
            expected_trip_id=expected_trip_id,
        )

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
        return self._mutations.add_stop(
            day_id=day_id,
            name=name,
            actor=actor,
            expected_revision=expected_revision,
            stop_type=stop_type,
            arrival_time=arrival_time,
            departure_time=departure_time,
            location=location,
            notes=notes,
            details=details,
            position=position,
            expected_trip_id=expected_trip_id,
        )

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
        return self._mutations.update_stop(
            day_id=day_id,
            stop_id=stop_id,
            patch=patch,
            actor=actor,
            expected_revision=expected_revision,
            position=position,
            expected_trip_id=expected_trip_id,
        )

    def remove_stop(
        self,
        *,
        day_id: str,
        stop_id: str,
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        return self._mutations.remove_stop(
            day_id=day_id,
            stop_id=stop_id,
            actor=actor,
            expected_revision=expected_revision,
            expected_trip_id=expected_trip_id,
        )

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

