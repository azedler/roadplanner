"""Regression test: a stop referenced under the wrong day no longer crashes.

Real bug report: preparing an assistant review failed outright with
"Änderungsentwurf konnte nicht erstellt werden / Bestehende Stopp-ID ist
nicht im aktuellen Reisetag vorhanden: stop-7b498d4f8a06". The stop
existed - just not under the day_id the operation carried, either because
the stop moved days after the draft was written (a handoff applied in
between) or because the model paired a correctly-referenced stop with the
wrong day. Stop IDs are globally unique, so a single match under another
day identifies the real day deterministically - the sanitizer now corrects
the day reference instead of rejecting the whole pending change (the same
stale-day-ID fallback the panel's _find_stop has always had). A stop found
NOWHERE stays a hard error: it was deleted or invented, and silently
guessing would write to the wrong place.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stale_stop_day_test"

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
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError

CONTEXT = {
    "trip": {"id": "trip-1"},
    "selected_trip_id": "trip-1",
    "id_catalog": {
        "day_ids": ["day-1", "day-2"],
        "stop_ids_by_day": {
            "day-1": ["stop-7b498d4f8a06"],
            "day-2": ["stop-other"],
        },
        "preference_ids": [],
    },
    "days": [],
}


def _operation(day_id: str, entity_id: str) -> dict:
    return {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": entity_id,
        "day_id": day_id,
        "changes": {"notes": "Aktualisiert."},
        "reason": "Test",
    }


def verify_wrong_day_reference_is_corrected_to_the_real_day() -> None:
    result = sanitizer._sanitize_operation(
        _operation("day-2", "stop-7b498d4f8a06"),
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
    )
    assert result["day_id"] == "day-1", (
        "a stop that exists under exactly one other day must have its day "
        "reference corrected instead of the whole change being rejected"
    )
    assert result["entity_id"] == "stop-7b498d4f8a06"


def verify_correct_day_reference_stays_untouched() -> None:
    result = sanitizer._sanitize_operation(
        _operation("day-1", "stop-7b498d4f8a06"),
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
    )
    assert result["day_id"] == "day-1"


def verify_stop_that_exists_nowhere_still_raises() -> None:
    try:
        sanitizer._sanitize_operation(
            _operation("day-1", "stop-deleted"),
            index=0,
            context=CONTEXT,
            new_day_refs=set(),
        )
    except ValidationError as err:
        assert "stop-deleted" in str(err)
    else:
        raise AssertionError(
            "a stop id that exists on no day must still be rejected - "
            "silently guessing would write to the wrong place"
        )


def verify_position_bookkeeping_uses_the_corrected_day() -> None:
    position_state: dict = {}
    sanitizer._sanitize_operation(
        _operation("day-2", "stop-7b498d4f8a06"),
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
        position_state=position_state,
    )
    assert "day-1" in position_state, (
        "stop-ordering bookkeeping must track the corrected day, not the "
        "stale one"
    )


if __name__ == "__main__":
    verify_wrong_day_reference_is_corrected_to_the_real_day()
    verify_correct_day_reference_stays_untouched()
    verify_stop_that_exists_nowhere_still_raises()
    verify_position_bookkeeping_uses_the_corrected_day()
    print("Stale stop day reference recovery tests passed.")
