"""Regression test for a real crash: a stray changes.location breaks review prep.

Real bug report: pasting a Booking.com share link for an *existing* stop
("Hier schlafen wir heute und morgen: https://www.booking.com/Share-...")
made the assistant chat accept the message into the change basket, but
clicking "Als Entscheidungsvorlage"/preparing the review then failed with
"ChangeSet-Operation 1 ('update_stop') ist ungueltig: 'stops[1].location'
muss ein JSON-Objekt sein".

Root cause: assistant_prompt.py's COMPILE_SYSTEM_PROMPT listed ``location``
as an allowed changes field for stops, contradicting both the compile
schema (OPERATION_CHANGES_SCHEMA has no ``location`` property at all) and
the design that a stop's location may only ever be set server-side by the
geocoding plugin from a confirmed place_query. Gemini's JSON-mode fallback
(used when the schema-constrained attempt fails, which url_context/search
tool use makes more likely) ignores the response schema and, following the
prompt's incorrect claim, can put a raw string or hand-built object
straight into changes.location - which then reaches the ChangeSet
untouched and fails deep inside execute_changeset's normalize_stop call.

Fix: strip any changes.location the model set directly, salvaging text
content (a place name/address) into place_query if none is already set, so
the normal geocoding path still gets a chance to resolve it, instead of an
untyped value reaching the ChangeSet.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stray_location_test"

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


def verify_string_location_is_salvaged_into_place_query() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {
            "location": "Lapland Hotels Ounasvaara Chalets, Rovaniemi",
            "notes": "Laut Booking.com: 4 Sterne, am Fluss.",
        },
        "reason": "Nutzer hat Booking.com-Link geschickt.",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "location" not in result["changes"]
    assert result["place_query"] == "Lapland Hotels Ounasvaara Chalets, Rovaniemi"


def verify_object_location_label_is_salvaged() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "changes": {
            "location": {"label": "Campingplatz Rovaniemi", "latitude": 66.5, "longitude": 25.7},
        },
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "location" not in result["changes"]
    assert result["place_query"] == "Campingplatz Rovaniemi"


def verify_existing_place_query_is_not_overwritten() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "stop",
        "entity_id": "stop-1",
        "day_id": "day-1",
        "place_query": "https://www.booking.com/Share-oBEUZI",
        "changes": {"location": "some other guessed place"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "location" not in result["changes"]
    assert result["place_query"] == "https://www.booking.com/Share-oBEUZI"


def verify_non_stop_entity_is_unaffected() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "trip",
        "changes": {"title": "Neue Reise", "location": "irrelevant"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["changes"].get("location") == "irrelevant"


verify_string_location_is_salvaged_into_place_query()
verify_object_location_label_is_salvaged()
verify_existing_place_query_is_not_overwritten()
verify_non_stop_entity_is_unaffected()

print("Stray changes.location stripping tests passed.")
