"""Regression test for a real crash: a stray changes.text breaks review prep.

Real bug report: preparing the review for a stop update crashed with
"Nicht erlaubte Felder für stop: text", after Gemini's JSON-mode fallback
put descriptive content under changes.text - a field that only ever
belongs to entity_type=preference (its free-text content) - instead of
changes.notes. `assistant_operation_sanitizer.py`'s per-entity allowed-field
check rejects the whole ChangeSet outright on any such mismatch, so a
misplaced but genuinely useful field (the model correctly extracted content,
just under the wrong key) blocked the entire pending change instead of just
that content being dropped.

Fix: for any entity_type other than preference, a stray changes.text is
salvaged into changes.notes (appended if notes already has content) before
the strict per-entity field check ever runs - the same "salvage real content,
never invent, never crash" approach used for the earlier changes.location
fix.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stray_text_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules["homeassistant.util"] = ha_util
ha_util_dt = types.ModuleType("homeassistant.util.dt")
ha_util_dt.now = lambda: None
sys.modules["homeassistant.util.dt"] = ha_util_dt


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for module_name in (
    "bounded_json",
    "json_io",
    "identifiers",
    "json_tree_validation",
    "stop_ordering",
    "canonical_day",
    "routing_helpers",
    "trip_documents",
    "trip_projections",
    "trip_state",
    "trip_repository",
    "trip_mutations",
    "trip_queries",
    "changeset",
    "changeset_operations",
    "context_export",
    "roadplanner",
    "destination_intelligence",
    "assistant_shared",
    "structured_output",
    "assistant_basket",
    "assistant_prompt",
):
    load(module_name)

compile_module = load("assistant_compile")


def verify_stray_text_is_salvaged_into_notes_for_stop() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {"text": "Laut Booking.com: 4 Sterne, am Fluss."},
        "reason": "Nutzer hat Booking.com-Link geschickt.",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "text" not in result["changes"]
    assert result["changes"]["notes"] == "Laut Booking.com: 4 Sterne, am Fluss."


def verify_stray_text_is_appended_to_existing_notes() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {
            "notes": "Aufenthalt vom 27.07. bis 29.07.2026.",
            "text": "Laut Booking.com: 4 Sterne, am Fluss.",
        },
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "text" not in result["changes"]
    assert result["changes"]["notes"] == (
        "Aufenthalt vom 27.07. bis 29.07.2026.\nLaut Booking.com: 4 Sterne, am Fluss."
    )


def verify_stray_text_is_salvaged_for_day_and_trip_too() -> None:
    day_raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"text": "Wetterumschwung erwartet."},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(day_raw, index=0)
    assert "text" not in result["changes"]
    assert result["changes"]["notes"] == "Wetterumschwung erwartet."

    trip_raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "trip",
        "changes": {"text": "Reisepass läuft im September ab."},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(trip_raw, index=0)
    assert "text" not in result["changes"]
    assert result["changes"]["notes"] == "Reisepass läuft im September ab."


def verify_preference_entity_keeps_its_own_text_field() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "preference",
        "entity_id": "pref-1",
        "changes": {"text": "Immer vegetarisch essen."},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["changes"]["text"] == "Immer vegetarisch essen."
    assert "notes" not in result["changes"]


verify_stray_text_is_salvaged_into_notes_for_stop()
verify_stray_text_is_appended_to_existing_notes()
verify_stray_text_is_salvaged_for_day_and_trip_too()
verify_preference_entity_keeps_its_own_text_field()

print("Stray changes.text salvage tests passed.")
