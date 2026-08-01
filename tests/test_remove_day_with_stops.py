"""Regression test: removing a day that still has stops no longer kills the draft.

Live report: "Änderungsentwurf konnte nicht erstellt werden /
ChangeSet-Operation 1 ('remove_day') ist ungültig: Der Reisetag enthält
Stopps. Zum Löschen muss remove_stops=true gesetzt sein. (Anfrage
prepare-aa6fd4485785)". The executor's remove_stops gate protects
scripted callers - for a reviewed assistant draft the user's request to
delete the day obviously includes its stops, and the loud destructive
confirmation at apply time stays. The sanitizer now sets the flag
whenever the day still carries stops; a model-provided flag survives
the remove-echo cleanup; the schema layer accepts the field.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_remove_day_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", ha_util)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = None
ha_dt.utcnow = None
sys.modules.setdefault("homeassistant.util.dt", ha_dt)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


sanitizer = load("assistant_operation_sanitizer")

CONTEXT = {
    "trip": {"id": "trip-1"},
    "selected_trip_id": "trip-1",
    "id_catalog": {
        "day_ids": ["day-1", "day-2"],
        "stop_ids_by_day": {"day-1": ["stop-1", "stop-2"], "day-2": []},
        "preference_ids": [],
    },
    "days": [],
}


def sanitize(operation: dict, batch_refs: dict | None = None) -> dict:
    return sanitizer._sanitize_operation(
        operation,
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
        batch_refs=batch_refs,
    )


def verify_reported_case_gets_remove_stops() -> None:
    result = sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "day",
            "entity_id": "day-1",
            "changes": {},
            "reason": "Tag streichen",
        }
    )
    assert result["changes"] == {"remove_stops": True}, result["changes"]


def verify_model_supplied_flag_survives_echo_cleanup() -> None:
    result = sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "day",
            "entity_id": "day-1",
            "changes": {"remove_stops": True, "title": "echoed title"},
            "reason": "Tag streichen",
        }
    )
    assert result["changes"] == {"remove_stops": True}, (
        "remove_stops must survive while echoed fields are dropped"
    )


def verify_empty_day_needs_no_flag() -> None:
    result = sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "day",
            "entity_id": "day-2",
            "changes": {},
            "reason": "Leeren Tag streichen",
        }
    )
    assert result["changes"] == {}, result["changes"]


def verify_stop_removal_keeps_dropping_all_changes() -> None:
    result = sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "stop",
            "entity_id": "stop-1",
            "day_id": "day-1",
            "changes": {"name": "echo"},
            "reason": "Stopp streichen",
        },
        batch_refs={},
    )
    assert result["changes"] == {}, "stop removals never carry changes"


if __name__ == "__main__":
    verify_reported_case_gets_remove_stops()
    verify_model_supplied_flag_survives_echo_cleanup()
    verify_empty_day_needs_no_flag()
    verify_stop_removal_keeps_dropping_all_changes()
    print("Remove-day-with-stops tests passed.")
