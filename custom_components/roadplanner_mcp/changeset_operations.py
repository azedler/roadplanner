"""Store-side ChangeSet glue: preview, deep-inspect, apply, adopt external edits.

``changeset.py`` no longer imports anything from ``roadplanner.py`` (it pulls
``TripState``/document normalization/etc. directly from the leaf modules that
now own them), which resolves the one real circular-dependency risk in the
``roadplanner.py`` decomposition - this module can therefore import
``changeset.py`` normally at the top of the file instead of the
method-body-local workaround the facade used before.
"""

from __future__ import annotations

from typing import Any, Callable

from .changeset import changeset_summary, execute_changeset, normalize_changeset
from .json_io import ValidationError, utc_now_iso
from .trip_repository import TripRepository
from .trip_state import TripState


class ChangesetOperations:
    """Preview, deeply inspect, apply, and adopt external ChangeSets/edits."""

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
