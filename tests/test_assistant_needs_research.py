"""Behavioral tests: which basket items trigger Gemini search/url_context.

Regression coverage for a real bug: a basket item that *updates* an existing
stop (because the chat step already matched it to a prior placeholder stop)
never enabled search/url_context, since the old check only looked at
action == "add". A pasted booking link on an update therefore got no fetch at
all - the model had to guess, producing a generic name and no place_query.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_needs_research_test"

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


# Dependency order for the pure (non-HA) roadplanner.py store decomposition,
# plus the assistant modules assistant_operation_sanitizer.py needs.
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


def _stop_item(**overrides):
    item = {"entity_type": "stop", "action": "add", "place_query": ""}
    item.update(overrides)
    return item


def verify_add_without_place_query_needs_research():
    assert sanitizer._needs_research([_stop_item(action="add", place_query="")])


def verify_update_without_place_query_needs_research():
    # The regression: an update (e.g. the chat step matched an existing
    # placeholder stop) must trigger research/url_context exactly like add.
    assert sanitizer._needs_research([_stop_item(action="update", place_query="")])


def verify_resolved_place_query_does_not_need_research():
    assert not sanitizer._needs_research(
        [_stop_item(action="update", place_query="52.5,13.4")]
    )


def verify_google_maps_link_does_not_need_research():
    item = _stop_item(
        action="update",
        place_query="https://maps.app.goo.gl/abc123",
    )
    assert not sanitizer._needs_research([item])


def verify_non_google_maps_link_in_reason_needs_research():
    item = {
        "entity_type": "stop",
        "action": "update",
        "place_query": "",
        "reason": "Benutzer hat https://www.booking.com/Share-l6WFn7 geschickt.",
    }
    assert sanitizer._needs_research([item])


def verify_plain_stop_update_without_any_link_still_needs_research():
    # Even with no link at all, an unresolved place_query on an add/update
    # must still trigger research (matches the pre-existing "add" behavior).
    item = {"entity_type": "stop", "action": "update", "place_query": ""}
    assert sanitizer._needs_research([item])


def verify_non_stop_items_are_unaffected():
    item = {"entity_type": "trip", "action": "update", "place_query": ""}
    assert not sanitizer._needs_research([item])


verify_add_without_place_query_needs_research()
verify_update_without_place_query_needs_research()
verify_resolved_place_query_does_not_need_research()
verify_google_maps_link_does_not_need_research()
verify_non_google_maps_link_in_reason_needs_research()
verify_plain_stop_update_without_any_link_still_needs_research()
verify_non_stop_items_are_unaffected()

print("Assistant needs-research trigger tests passed.")
