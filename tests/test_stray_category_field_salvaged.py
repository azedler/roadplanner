"""Regression test for a real crash: a stray changes.category breaks review prep.

Real bug report: preparing the review for a stop failed with "Änderungsentwurf
konnte nicht erstellt werden / Nicht erlaubte Felder für stop: category",
because Gemini classified a stop (a natural word choice for "what kind of
place is this") under `category` - a field that only ever belongs to
entity_type=preference. `assistant_operation_sanitizer.py`'s per-entity
allowed-field check rejects the whole ChangeSet outright on any such
mismatch, so a misplaced but genuinely useful classification blocked the
entire pending change instead of just that one field being dropped - the
exact same failure mode as the earlier changes.text bug.

Fix: for any entity_type other than preference, a stray changes.category is
salvaged into changes.notes (appended if notes already has content, prefixed
"Kategorie: ") before the strict per-entity field check ever runs.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stray_category_test"

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


def verify_stray_category_is_salvaged_into_notes_for_stop() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {"category": "Camping"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "category" not in result["changes"]
    assert result["changes"]["notes"] == "Kategorie: Camping"


def verify_stray_category_is_appended_to_existing_notes() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {
            "notes": "80 SEK / 24h.",
            "category": "Camping",
        },
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "category" not in result["changes"]
    assert result["changes"]["notes"] == "80 SEK / 24h.\nKategorie: Camping"


def verify_stray_category_is_salvaged_for_day_and_trip_too() -> None:
    day_raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"category": "Anreisetag"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(day_raw, index=0)
    assert "category" not in result["changes"]
    assert result["changes"]["notes"] == "Kategorie: Anreisetag"

    trip_raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "trip",
        "changes": {"category": "Roadtrip"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(trip_raw, index=0)
    assert "category" not in result["changes"]
    assert result["changes"]["notes"] == "Kategorie: Roadtrip"


def verify_preference_entity_keeps_its_own_category_field() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "preference",
        "entity_id": "pref-1",
        "changes": {"category": "Ernährung"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["changes"]["category"] == "Ernährung"
    assert "notes" not in result["changes"]


verify_stray_category_is_salvaged_into_notes_for_stop()
verify_stray_category_is_appended_to_existing_notes()
verify_stray_category_is_salvaged_for_day_and_trip_too()
verify_preference_entity_keeps_its_own_category_field()

print("Stray category field salvage tests passed.")
