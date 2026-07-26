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
from pathlib import Path
from typing import Any

from .changeset_operations import ChangesetOperations
from .context_export import ContextExport
from .identifiers import validate_identifier
from .json_io import (
    RevisionConflictError,
    RoadplannerError,
    StorageError,
    ValidationError,
    _read_json,
    _write_json_atomic,
    utc_now_iso,
)
from .json_tree_validation import _validate_json_tree
from .trip_mutations import TripMutations
from .trip_queries import TripQueries
from .trip_repository import TripRepository
from .trip_state import TripState



@dataclass(slots=True)
class RoadplannerStore:
    """Facade composing the trip repository with queries/mutations/changesets/context export."""

    roadbook_dir: Path
    backup_dir: Path
    handoff_dir: Path
    backup_count: int = 20
    _repository: TripRepository = field(init=False, repr=False, compare=False)
    _context: ContextExport = field(init=False, repr=False, compare=False)
    _queries: TripQueries = field(init=False, repr=False, compare=False)
    _mutations: TripMutations = field(init=False, repr=False, compare=False)
    _changesets: ChangesetOperations = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._repository = TripRepository(
            roadbook_dir=self.roadbook_dir,
            backup_dir=self.backup_dir,
            handoff_dir=self.handoff_dir,
            backup_count=self.backup_count,
        )
        self._context = ContextExport(self._repository)
        self._queries = TripQueries(self._repository)
        self._mutations = TripMutations(
            self._repository,
            write_context_best_effort=self._context.write_context_best_effort,
        )
        self._changesets = ChangesetOperations(
            self._repository,
            write_context_best_effort=self._context.write_context_best_effort,
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
        self._context.write_context_best_effort(final_state)
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
        return self._changesets.adopt_external_changes(
            actor=actor,
            expected_revision=expected_revision,
        )

    def preview_changeset(self, changeset: dict[str, Any]) -> dict[str, Any]:
        """Validate a ChangeSet against the active trip without writing files."""
        return self._changesets.preview_changeset(changeset)

    def inspect_changeset_for_import(
        self,
        changeset: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate an external ChangeSet deeply, including stale revisions.

        A stale base revision remains a review conflict, but all referenced
        entities and operation payloads are still checked against the current
        active trip before the ChangeSet is admitted to the inbox.
        """
        return self._changesets.inspect_changeset_for_import(changeset)

    def apply_changeset(
        self,
        *,
        changeset: dict[str, Any],
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply all ChangeSet operations atomically in one route revision."""
        return self._changesets.apply_changeset(
            changeset=changeset,
            actor=actor,
            expected_revision=expected_revision,
        )

    def export_trip(self) -> dict[str, Any]:
        return self._queries.export_trip()

    def get_context_payload(self) -> dict[str, Any]:
        """Return bounded JSON context for an authenticated external bridge."""
        return self._context.get_context_payload()

    def get_context_markdown(self) -> dict[str, Any]:
        """Return bounded Markdown context for an authenticated external bridge."""
        return self._context.get_context_markdown()

    def write_context(self, state: TripState | None = None) -> dict[str, Any]:
        """Write bounded derived context files for Drive or OneDrive sync."""
        return self._context.write_context(state)
