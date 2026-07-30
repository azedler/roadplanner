"""Regression test: position bookkeeping must cover the FULL trip.

Audit finding (confirmed by two independent reviewers): the compile
position_state was seeded only from context["days"] - a bounded detail
window (basket-target days plus neighbours, capped at 30 and trimmed
further under size pressure) - while ID validation accepts any day of the
whole trip via the id_catalog. An operation targeting a day outside the
window (e.g. "fuege am letzten Reisetag einen Stellplatz hinzu" mid-trip,
or a date resolved through the 180-day trip_index, or the stale-day
fallback redirecting to such a day) then saw an EMPTY shadow list for a
day that really has stops: a new stop was forced to position 1 (in front
of the day's start and everything else, instead of before the overnight
stop / at the end), and "verschiebe auf Position 5" was silently clamped
to position 1. The changeset applied cleanly, so the corrupted order was
only visible later in the roadbook.

The state is now seeded via seed_position_state() from the full stored
day list (payload["days"]["days"]), never from the context window.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_position_window_test"

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

# A 3-stop day the way it exists in the stored trip - far outside any
# compile detail window in the live bug.
FULL_DAYS = [
    {
        "id": "day-window",
        "stops": [{"id": "stop-w1", "type": "start"}],
    },
    {
        "id": "day-far",
        "stops": [
            {"id": "stop-f1", "type": "start"},
            {"id": "stop-f2", "type": "restaurant"},
            {"id": "stop-f3", "type": "campsite"},
        ],
    },
]

CONTEXT = {
    "trip": {"id": "trip-1"},
    "selected_trip_id": "trip-1",
    "id_catalog": {
        "day_ids": ["day-window", "day-far"],
        "stop_ids_by_day": {
            "day-window": ["stop-w1"],
            "day-far": ["stop-f1", "stop-f2", "stop-f3"],
        },
        "preference_ids": [],
    },
    # The bounded compile window: only day-window is detailed.
    "days": [FULL_DAYS[0]],
}


def _seeded_state():
    return sanitizer.seed_position_state(FULL_DAYS)


def verify_seed_covers_days_outside_the_context_window() -> None:
    state = _seeded_state()
    assert [item["id"] for item in state["day-far"]] == [
        "stop-f1",
        "stop-f2",
        "stop-f3",
    ], "seeding must reflect the real stops of every trip day, not just the window"


def verify_add_on_out_of_window_day_lands_before_overnight_stop() -> None:
    state = _seeded_state()
    operation = {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "entity_id": "new-stop-1",
        "day_id": "day-far",
        "place_query": "Restaurant X, Umeå",
        "changes": {"name": "Restaurant X", "type": "restaurant"},
        "reason": "Test",
    }
    result = sanitizer._sanitize_operation(
        operation, index=0, context=CONTEXT, new_day_refs=set(),
        position_state=state,
    )
    assert result["position"] == 3, (
        "a new day-time stop on an out-of-window day must slot in before the "
        f"overnight stop (position 3 of 4), not be forced to position "
        f"{result['position']}"
    )


def verify_move_on_out_of_window_day_keeps_requested_position() -> None:
    state = _seeded_state()
    operation = {
        "operation_id": "op-1",
        "action": "move",
        "entity_type": "stop",
        "entity_id": "stop-f1",
        "day_id": "day-far",
        "position": 2,
        "changes": {},
        "reason": "Test",
    }
    result = sanitizer._sanitize_operation(
        operation, index=0, context=CONTEXT, new_day_refs=set(),
        position_state=state,
    )
    assert result["position"] == 2, (
        "a requested move position on an out-of-window day must survive, "
        f"not be clamped to {result['position']}"
    )


def verify_assistant_seeds_from_stored_days_not_context_window() -> None:
    source = (PACKAGE_ROOT / "assistant.py").read_text(encoding="utf-8")
    assert "seed_position_state(" in source
    assert 'payload.get("days")' in source, (
        "position_state must be seeded from the full stored day list "
        '(payload["days"]["days"]), never from the bounded context window'
    )


def verify_garbage_input_yields_empty_state() -> None:
    assert sanitizer.seed_position_state(None) == {}
    assert sanitizer.seed_position_state("kaputt") == {}
    assert sanitizer.seed_position_state([{"stops": []}, "kein dict"]) == {}


if __name__ == "__main__":
    verify_seed_covers_days_outside_the_context_window()
    verify_add_on_out_of_window_day_lands_before_overnight_stop()
    verify_move_on_out_of_window_day_keeps_requested_position()
    verify_assistant_seeds_from_stored_days_not_context_window()
    verify_garbage_input_yields_empty_state()
    print("Full-trip position state tests passed.")
