"""Regression test: a link buried in notes must still reach place_query.

Real bug report: a stop compiled from "Heute Nachmittag machen wir bei dem
Link vorbei <Google Maps link>" ended up with the link only inside
changes.notes ("Stopp am Nachmittag laut Maps-Link https://maps.app.goo.gl/
...") and no place_query at all - even though the prompt explicitly asks the
model to always put such a link in the dedicated place_query field. Since
GeocodingAssistantPlugin.async_enrich_operations only ever inspects
place_query (never notes/reason), that stop's automatic enrichment never
even attempted to run - the stop was stuck on "Ort fehlt" until the user
manually tapped "Stopp anreichern", instead of getting resolved right away
as part of the same change like new-stop creation is supposed to.

Fix: when a stop operation has no place_query after all the existing
alias-lifts, scan changes.notes and the operation's own reason text for a
link and lift the first one found into place_query - without removing it
from notes/reason, which stays as human-readable context.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_stop_link_notes_test"

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


def verify_maps_link_in_notes_is_lifted_to_place_query() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "day_id": "day-1",
        "changes": {
            "name": "Zwischenstopp am Nachmittag",
            "notes": (
                "Stopp am Nachmittag laut Maps-Link "
                "https://maps.app.goo.gl/pQ7FjhamJquM7WXN6?g_st=ic"
            ),
        },
        "reason": "Nutzer hat einen Google-Maps-Link geschickt.",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["place_query"] == "https://maps.app.goo.gl/pQ7FjhamJquM7WXN6?g_st=ic"
    # The link stays in notes too - it's still useful human-readable context.
    assert "maps.app.goo.gl" in result["changes"]["notes"]


def verify_link_in_reason_is_lifted_when_notes_has_none() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "day_id": "day-1",
        "changes": {"name": "Restaurant"},
        "reason": "Laut Nutzer: https://www.booking.com/Share-oxWIOW",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["place_query"] == "https://www.booking.com/Share-oxWIOW"


def verify_existing_place_query_is_never_overwritten() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "day_id": "day-1",
        "place_query": "Arktikum Rovaniemi",
        "changes": {
            "name": "Zwischenstopp",
            "notes": "Siehe https://maps.app.goo.gl/other-link",
        },
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert result["place_query"] == "Arktikum Rovaniemi"


def verify_non_stop_entity_is_unaffected() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"notes": "Siehe https://maps.app.goo.gl/some-link"},
        "reason": "Test",
    }
    result = compile_module._normalize_compiled_operation_aliases(raw, index=0)
    assert "place_query" not in result


verify_maps_link_in_notes_is_lifted_to_place_query()
verify_link_in_reason_is_lifted_when_notes_has_none()
verify_existing_place_query_is_never_overwritten()
verify_non_stop_entity_is_unaffected()

print("Stop link lifted from notes/reason tests passed.")
