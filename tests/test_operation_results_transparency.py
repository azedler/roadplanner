"""Regression tests: operation_results must show what actually changed.

Real gap: the handoff preview dialog renders each operation_results entry
verbatim (JSON.stringify) so the user can see what a pending change will do
before clicking "Übernehmen" - but execute_changeset only ever put bare
metadata (index/op/day_id/stop_id/position/annotations) into each result,
never the requested patch or new entity content. A user reviewing a
`update_stop` preview saw literally nothing about what would change.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE = "roadplanner_operation_results_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

for name in (
    "bounded_json",
    "json_io",
    "identifiers",
    "json_tree_validation",
    "stop_ordering",
    "canonical_day",
    "trip_documents",
    "trip_state",
    "changeset",
):
    spec = spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

changeset = sys.modules[f"{PACKAGE}.changeset"]
trip_documents = sys.modules[f"{PACKAGE}.trip_documents"]
trip_state_module = sys.modules[f"{PACKAGE}.trip_state"]

FALLBACK_TS = "2026-01-01T00:00:00Z"


def _trip_state():
    trip_document = trip_documents.normalize_trip_document(
        {
            "schema_version": 3,
            "trip": {"id": "trip-1", "title": "Testreise"},
            "days": [{"id": "day-1", "file": "days/day-1.json"}],
            "metadata": {
                "revision": 5,
                "created_at": FALLBACK_TS,
                "updated_at": FALLBACK_TS,
            },
        },
        expected_trip_id="trip-1",
        fallback_timestamp=FALLBACK_TS,
    )
    day_document = trip_documents.normalize_day_document(
        {
            "schema_version": 1,
            "day": {"id": "day-1", "date": "2026-01-02", "title": "Tag 1"},
            "stops": [
                {
                    "id": "stop-1",
                    "name": "Altes Camp",
                    "type": "camping",
                    "notes": "alt",
                }
            ],
        },
        fallback_id="day-1",
        fallback_timestamp=FALLBACK_TS,
    )
    return trip_state_module.TripState(
        pointer={"active_trip": "trip-1"},
        trip_document=trip_document,
        day_documents={"day-1": day_document},
        unmanaged_day_files=[],
    )


def _changeset(operations):
    return changeset.normalize_changeset(
        {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": "changeset-transparency-001",
            "trip_id": "trip-1",
            "base_revision": 5,
            "created_at": FALLBACK_TS,
            "title": "Test",
            "summary": "Test",
            "apply_mode": "review",
            "operations": operations,
        }
    )


def verify_update_stop_result_shows_the_patch() -> None:
    previous = _trip_state()
    normalized = _changeset(
        [
            {
                "operation_id": "op-1",
                "action": "update",
                "entity_type": "stop",
                "entity_id": "stop-1",
                "day_id": "day-1",
                "changes": {"name": "Campingplatz Rovaniemi"},
            }
        ]
    )
    execution = changeset.execute_changeset(previous, normalized)
    result = execution.operation_results[0]
    assert result["op"] == "update_stop"
    assert result["patch"] == {"name": "Campingplatz Rovaniemi"}


def verify_add_stop_result_shows_the_new_stop() -> None:
    previous = _trip_state()
    normalized = _changeset(
        [
            {
                "operation_id": "op-1",
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-1",
                "changes": {"name": "Neuer Stopp", "type": "sightseeing"},
            }
        ]
    )
    execution = changeset.execute_changeset(previous, normalized)
    result = execution.operation_results[0]
    assert result["op"] == "add_stop"
    assert result["stop"]["name"] == "Neuer Stopp"
    assert result["stop"]["type"] == "sightseeing"


def verify_update_day_result_shows_the_patch() -> None:
    previous = _trip_state()
    normalized = _changeset(
        [
            {
                "operation_id": "op-1",
                "action": "update",
                "entity_type": "day",
                "entity_id": "day-1",
                "changes": {"title": "Neuer Titel"},
            }
        ]
    )
    execution = changeset.execute_changeset(previous, normalized)
    result = execution.operation_results[0]
    assert result["op"] == "update_day"
    assert result["patch"] == {"title": "Neuer Titel"}


def verify_update_trip_result_shows_the_patch() -> None:
    previous = _trip_state()
    normalized = _changeset(
        [
            {
                "operation_id": "op-1",
                "action": "update",
                "entity_type": "trip",
                "changes": {"title": "Neue Reise"},
            }
        ]
    )
    execution = changeset.execute_changeset(previous, normalized)
    result = execution.operation_results[0]
    assert result["op"] == "update_trip"
    assert result["patch"] == {"title": "Neue Reise"}


def verify_remove_stop_result_still_has_no_content_to_show() -> None:
    previous = _trip_state()
    normalized = _changeset(
        [
            {
                "operation_id": "op-1",
                "action": "remove",
                "entity_type": "stop",
                "entity_id": "stop-1",
                "day_id": "day-1",
            }
        ]
    )
    execution = changeset.execute_changeset(previous, normalized)
    result = execution.operation_results[0]
    assert result["op"] == "remove_stop"
    assert "patch" not in result and "stop" not in result


verify_update_stop_result_shows_the_patch()
verify_add_stop_result_shows_the_new_stop()
verify_update_day_result_shows_the_patch()
verify_update_trip_result_shows_the_patch()
verify_remove_stop_result_still_has_no_content_to_show()

print("Operation results transparency tests passed.")
