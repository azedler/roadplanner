"""Regression test: trip-only fields echoed on a stop no longer kill the draft.

Live report: "Änderungsentwurf konnte nicht erstellt werden / Nicht
erlaubte Felder für stop: travelers (Anfrage prepare-57f96ddd4dc5)".
Since the ferry-details fix the response schema legitimately allows
travelers/vehicle/preferences inside changes (for the TRIP) - so the model
sometimes echoes them on a stop or day too. The strict per-entity field
check then rejected the whole multi-operation draft over a redundant echo.
Such strays are now salvaged into notes as compact text, the same
approach used for stray category/text; the canonical trip-level crew and
vehicle data stays untouched.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stray_trip_fields_test"

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
compile_module = sys.modules[f"{PACKAGE_NAME}.assistant_compile"]

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


def verify_reported_case_no_longer_rejects_the_draft() -> None:
    result = sanitizer._sanitize_operation(
        {
            "operation_id": "op-1",
            "action": "update",
            "entity_type": "stop",
            "entity_id": "stop-1",
            "day_id": "day-1",
            "changes": {
                "notes": "Fährtasche vorbereiten.",
                "travelers": [
                    {"name": "Aron"}, {"name": "Michaela"}, "Levi",
                ],
            },
            "reason": "Test",
        },
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
    )
    assert "travelers" not in result["changes"], "the stray field is removed"
    notes = result["changes"]["notes"]
    assert "Fährtasche vorbereiten." in notes, "real notes survive"
    assert "Reisende: Aron, Michaela, Levi" in notes, (
        "the echoed crew is preserved as text instead of killing the draft"
    )


def verify_vehicle_and_preferences_strays_are_salvaged_too() -> None:
    for field, value, expected in (
        ("vehicle", {"model": "Ford Nugget"}, "Fahrzeug: model: Ford Nugget"),
        ("preferences", {"angeln": "wichtig"}, "Präferenzen: angeln: wichtig"),
    ):
        result = sanitizer._sanitize_operation(
            {
                "operation_id": "op-1",
                "action": "update",
                "entity_type": "stop",
                "entity_id": "stop-1",
                "day_id": "day-1",
                "changes": {field: value},
                "reason": "Test",
            },
            index=0,
            context=CONTEXT,
            new_day_refs=set(),
        )
        assert field not in result["changes"]
        assert expected in result["changes"]["notes"], result["changes"]


def verify_trip_updates_keep_travelers_untouched() -> None:
    raw = compile_module._normalize_compiled_operation_aliases(
        {
            "operation_id": "op-1",
            "action": "update",
            "entity_type": "trip",
            "changes": {"travelers": [{"name": "Aron"}]},
        },
        index=0,
    )
    assert raw["changes"]["travelers"] == [{"name": "Aron"}], (
        "the canonical trip-level crew data must never be rewritten to text"
    )


def verify_empty_stray_is_simply_dropped() -> None:
    result = sanitizer._sanitize_operation(
        {
            "operation_id": "op-1",
            "action": "update",
            "entity_type": "stop",
            "entity_id": "stop-1",
            "day_id": "day-1",
            "changes": {"notes": "Bleibt.", "travelers": []},
            "reason": "Test",
        },
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
    )
    assert result["changes"]["notes"] == "Bleibt.", "no noise note for an empty echo"


if __name__ == "__main__":
    verify_reported_case_no_longer_rejects_the_draft()
    verify_vehicle_and_preferences_strays_are_salvaged_too()
    verify_trip_updates_keep_travelers_untouched()
    verify_empty_stray_is_simply_dropped()
    print("Stray trip-field salvage tests passed.")
