"""An assistant update that changes nothing no longer kills the whole draft.

Live report: "Änderungsentwurf konnte nicht erstellt werden / Eine
Aktualisierung benötigt Änderungen, eine Position oder place_query". Gemini
had echoed an entity it decided NOT to touch (all fields null) next to
perfectly usable operations - and the hard rejection threw those away too.
The empty operation is now dropped, the rest of the draft survives, and the
omission is recorded as a visible assumption.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_empty_update_test"

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
        "day_ids": ["day-1"],
        "stop_ids_by_day": {"day-1": ["stop-1"]},
        "preference_ids": [],
    },
    "days": [],
}


def _stop_update(changes: dict) -> dict:
    return {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": changes,
        "reason": "Nichts zu tun",
    }


def verify_empty_update_raises_the_skip_signal_not_a_validation_error() -> None:
    # An object full of nulls survives schema validation and then collapses
    # to {} - the exact shape the live failure carried.
    for changes in ({}, {"notes": None}, {"name": None, "notes": None}):
        try:
            sanitizer._sanitize_operation(
                _stop_update(changes),
                index=0,
                context=CONTEXT,
                new_day_refs=set(),
            )
        except sanitizer.NoOperationChange as skipped:
            assert skipped.entity_type == "stop"
            assert skipped.entity_id == "stop-1"
            assert "stop-1" in skipped.note and "Übersprungen" in skipped.note
        else:
            raise AssertionError(f"empty changes must signal a skip: {changes}")


def verify_updates_that_do_carry_something_still_pass() -> None:
    for operation in (
        _stop_update({"notes": "Neuer Hinweis"}),
        {**_stop_update({}), "position": 2},
        {**_stop_update({}), "place_query": "Angelsee, Schweden"},
    ):
        result = sanitizer._sanitize_operation(
            operation,
            index=0,
            context=CONTEXT,
            new_day_refs=set(),
        )
        assert result["action"] == "update"


def verify_the_caller_drops_only_the_empty_operation() -> None:
    source = (PACKAGE_ROOT / "assistant.py").read_text(encoding="utf-8")
    assert "except NoOperationChange as empty:" in source
    assert "skipped_no_ops.append(empty.note)" in source, (
        "the omission must stay visible in the draft, not vanish silently"
    )
    assert "[compiled.get(\"assumptions\"), skipped_no_ops]" in source
    assert "nur Aktualisierungen ohne konkrete" in source, (
        "a draft that consisted ONLY of empty updates needs a message that "
        "tells the user what to do instead of naming an internal field"
    )


if __name__ == "__main__":
    verify_empty_update_raises_the_skip_signal_not_a_validation_error()
    verify_updates_that_do_carry_something_still_pass()
    verify_the_caller_drops_only_the_empty_operation()
    print("Empty update operation skip tests passed.")
