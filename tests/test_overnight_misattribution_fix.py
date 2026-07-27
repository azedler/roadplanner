"""Regression tests for a real basket misattribution bug.

`_operation_context_text`'s "exactly one stop item in the basket -> assume
it's the match" fallback is a reasonable last resort for inferring which
*day* an operation belongs to (low-stakes, reviewable). But the same
fallback also fed `_is_actual_past_overnight`, which silently converts a
brand-new stop `add` into an `update` of an *existing* overnight stop. With
a basket holding exactly one differently-themed stop item mentioning a past
overnight ("gestern Nacht hier übernachtet"), a completely unrelated new
stop (e.g. a fuel stop the model forgot to attach place_query/name to) would
get misattributed to that lone item's overnight text and silently overwrite
the previous night's real campsite/overnight entry.

Fix: `_is_actual_past_overnight` now calls `_operation_context_text` with
`strict=True`, which disables the lone-item fallback (but keeps real
place_query/name matches) - day inference keeps the lenient fallback since
guessing the wrong day is low-stakes and reviewable, unlike silently
rewriting an unrelated existing stop.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_overnight_misattribution_test"

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
    "assistant_compile",
):
    load(module_name)

sanitizer = load("assistant_operation_sanitizer")

LONE_OVERNIGHT_ITEM = {
    "entity_type": "stop",
    "action": "update",
    "target_id": "stop-lookout",
    "place_query": "Camping Look-Out",
    "summary": "Camping Look-Out war laut, aber toller Blick, gestern Nacht hier übernachtet.",
    "reason": "Gestern Nacht hier übernachtet.",
    "values": {"name": "Camping Look-Out", "notes": "Laut, aber toller Blick."},
}

UNRELATED_ADD_FUEL_STOP = {
    "action": "add",
    "changes": {"name": "Tankstelle Shell", "type": "fuel"},
    "reason": "Vom Benutzer für heute Morgen erbeten.",
}


def verify_strict_mode_ignores_lone_item_fallback() -> None:
    lenient_text = sanitizer._operation_context_text(
        UNRELATED_ADD_FUEL_STOP, basket=[LONE_OVERNIGHT_ITEM]
    )
    assert "gestern" in lenient_text or "camping" in lenient_text.casefold(), (
        "the lenient (day-inference) mode must still use the lone-item fallback"
    )

    strict_text = sanitizer._operation_context_text(
        UNRELATED_ADD_FUEL_STOP, basket=[LONE_OVERNIGHT_ITEM], strict=True
    )
    assert "camping" not in strict_text
    assert "übernachtet" not in strict_text


def verify_unrelated_add_is_not_misdetected_as_past_overnight() -> None:
    # The regression: previously this returned True purely because the
    # basket held exactly one (unrelated) stop item mentioning an overnight.
    assert not sanitizer._is_actual_past_overnight(
        UNRELATED_ADD_FUEL_STOP, basket=[LONE_OVERNIGHT_ITEM]
    )


def verify_operations_own_text_still_detected() -> None:
    own_overnight_add = {
        "action": "add",
        "changes": {"name": "Camping Look-Out", "type": "camping"},
        "reason": "Gestern Nacht hier übernachtet.",
    }
    assert sanitizer._is_actual_past_overnight(own_overnight_add, basket=[])


def verify_actual_basket_match_still_detected_in_strict_mode() -> None:
    matching_add = {
        "action": "add",
        "place_query": "Camping Look-Out",
        "changes": {"name": "Camping Look-Out", "type": "camping"},
    }
    assert sanitizer._is_actual_past_overnight(
        matching_add, basket=[LONE_OVERNIGHT_ITEM]
    )


verify_strict_mode_ignores_lone_item_fallback()
verify_unrelated_add_is_not_misdetected_as_past_overnight()
verify_operations_own_text_still_detected()
verify_actual_basket_match_still_detected_in_strict_mode()

print("Overnight misattribution fix tests passed.")
