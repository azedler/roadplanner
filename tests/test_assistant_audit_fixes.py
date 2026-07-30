"""Regression tests for the seven confirmed assistant-robustness audit findings.

[5] The compile response schema forbade changes.details/travelers/vehicle/
    preferences/reason that the prompt explicitly mandates (ferry terminals
    MUST set changes.details.transport) - schema-constrained decoding could
    not emit them, so ferry roles were silently dropped.
[6] An echoed non-empty changes object on remove/move hard-rejected the
    whole draft; the echo carries no user data and is now dropped.
[7] A top-level position as digit-string or integral float (MIME-only JSON
    fallback) was silently discarded, misplacing new stops and hard-
    rejecting position-only reorders.
[8] changes.location carrying user-supplied coordinates ({"lat":..,"lng":..}
    or [lat, lon]) was destroyed instead of salvaged into place_query,
    then the add was rejected for lacking place_query.
[1] remove-then-reference of the same stop/day in one draft passed
    sanitization and blew up the whole draft at changeset ingestion with a
    cryptic error; now a clear sanitize-time error names the contradiction.
[4] update/move/remove referencing a stop ADDED earlier in the same draft
    was rejected as an unknown stop ID, discarding the whole draft - the
    ChangeSet executor fully supports same-batch references.
[3] The past-overnight dedup ("wir haben hier geschlafen") only searched the
    bounded context window, so a target day outside the window got a
    DUPLICATE overnight stop instead of an update of the existing one.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_assistant_audit_test"

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
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError

FULL_DAYS = [
    {
        "id": "day-1",
        "stops": [
            {"id": "stop-a", "type": "start"},
            {"id": "stop-b", "type": "campsite"},
        ],
    },
    {
        "id": "day-2",
        "stops": [{"id": "stop-c", "type": "start"}],
    },
]

CONTEXT = {
    "trip": {"id": "trip-1"},
    "selected_trip_id": "trip-1",
    "id_catalog": {
        "day_ids": ["day-1", "day-2"],
        "stop_ids_by_day": {
            "day-1": ["stop-a", "stop-b"],
            "day-2": ["stop-c"],
        },
        "preference_ids": [],
    },
    # Bounded compile window: only day-2 is detailed; day-1 is outside.
    "days": [FULL_DAYS[1]],
}


def _sanitize(raw, *, batch_refs=None, position_state=None, full_days=None):
    return sanitizer._sanitize_operation(
        raw,
        index=0,
        context=CONTEXT,
        new_day_refs=set(),
        position_state=position_state,
        batch_refs=batch_refs,
        full_days=full_days,
    )


# --- [5] schema allows the prompt-mandated structured fields -------------

def verify_schema_allows_prompt_mandated_fields() -> None:
    schema = compile_module.OPERATION_CHANGES_SCHEMA["properties"]
    for field in ("details", "vehicle", "preferences", "travelers", "reason"):
        assert field in schema, (
            f"the response schema must allow changes.{field} - the prompt "
            "mandates it (ferry terminals set changes.details.transport)"
        )
    allowed = compile_module._ALLOWED_CHANGE_FIELDS
    for field in schema:
        assert field in allowed or field == "location", (
            f"schema field {field!r} must also survive the sanitizer"
        )


# --- [6] echoed changes on remove/move are dropped, not rejected ---------

def verify_echoed_changes_on_remove_are_dropped() -> None:
    result = _sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "stop",
            "entity_id": "stop-c",
            "day_id": "day-2",
            "changes": {"name": "Camping Seeblick"},
            "reason": "Test",
        }
    )
    assert result["action"] == "remove"
    assert result["changes"] == {}, "the identifying echo is dropped, not fatal"


# --- [7] string/float positions are coerced ------------------------------

def verify_string_and_float_positions_survive() -> None:
    for raw_position in ("2", 2.0):
        result = _sanitize(
            {
                "operation_id": "op-1",
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-2",
                "place_query": "Aussichtspunkt X",
                "position": raw_position,
                "changes": {"name": "Aussichtspunkt X", "type": "viewpoint"},
                "reason": "Test",
            },
            position_state={"day-2": [{"id": "stop-c", "type": "start"}]},
        )
        assert result["position"] == 2, (
            f"position {raw_position!r} must be coerced, not silently dropped"
        )


# --- [8] coordinate dicts/lists salvage into place_query -----------------

def verify_coordinate_location_is_salvaged() -> None:
    for stray in (
        {"lat": 47.26, "lng": 11.39},
        {"latitude": 47.26, "longitude": 11.39},
        [47.26, 11.39],
    ):
        raw = compile_module._normalize_compiled_operation_aliases(
            {
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-2",
                "changes": {"name": "Wildcamp", "type": "wildcamp", "location": stray},
            },
            index=0,
        )
        assert raw.get("place_query") == "47.26, 11.39", (
            f"user-supplied GPS {stray!r} must survive as place_query"
        )
        assert "location" not in raw["changes"]


# --- [1] remove-then-reference fails clearly at sanitize time ------------

def verify_remove_then_reference_is_a_clear_sanitize_error() -> None:
    batch_refs: dict = {}
    _sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "stop",
            "entity_id": "stop-c",
            "day_id": "day-2",
            "changes": {},
            "reason": "Test",
        },
        batch_refs=batch_refs,
    )
    try:
        _sanitize(
            {
                "operation_id": "op-2",
                "action": "update",
                "entity_type": "stop",
                "entity_id": "stop-c",
                "day_id": "day-2",
                "changes": {"notes": "Doch behalten?"},
                "reason": "Test",
            },
            batch_refs=batch_refs,
        )
    except ValidationError as err:
        assert "im selben Entwurf" in str(err), (
            "the contradiction must be named clearly at sanitize time"
        )
    else:
        raise AssertionError("referencing a stop removed in the same draft must fail")


def verify_stop_add_on_day_removed_in_same_draft_fails_clearly() -> None:
    batch_refs: dict = {}
    _sanitize(
        {
            "operation_id": "op-1",
            "action": "remove",
            "entity_type": "day",
            "entity_id": "day-2",
            "changes": {},
            "reason": "Test",
        },
        batch_refs=batch_refs,
    )
    try:
        _sanitize(
            {
                "operation_id": "op-2",
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-2",
                "place_query": "Camping X",
                "changes": {"name": "Camping X", "type": "campsite"},
                "reason": "Test",
            },
            batch_refs=batch_refs,
        )
    except ValidationError as err:
        assert "im selben Entwurf entfernt" in str(err)
    else:
        raise AssertionError("adding a stop to a day removed in the same draft must fail")


# --- [4] same-batch add-then-update/move is legal ------------------------

def verify_reference_to_stop_added_in_same_batch_is_accepted() -> None:
    batch_refs: dict = {}
    position_state = {"day-2": [{"id": "stop-c", "type": "start"}]}
    added = _sanitize(
        {
            "operation_id": "op-1",
            "action": "add",
            "entity_type": "stop",
            "day_id": "day-2",
            "place_query": "Aquarium",
            "changes": {"name": "Aquarium", "type": "attraction"},
            "reason": "Test",
        },
        batch_refs=batch_refs,
        position_state=position_state,
    )
    new_id = added["entity_id"]
    updated = _sanitize(
        {
            "operation_id": "op-2",
            "action": "update",
            "entity_type": "stop",
            "entity_id": new_id,
            "day_id": "day-2",
            "changes": {"notes": "Tickets online kaufen."},
            "reason": "Test",
        },
        batch_refs=batch_refs,
        position_state=position_state,
    )
    assert updated["entity_id"] == new_id, (
        "an update of a stop added in the same draft must pass, not reject "
        "the whole draft as an unknown stop ID"
    )


# --- [3] past-overnight dedup sees days outside the window ---------------

def verify_past_overnight_dedup_works_outside_the_window() -> None:
    raw = {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "day_id": "day-1",  # outside the detailed window (only day-2 is in)
        "place_query": "Stellplatz Hafen",
        "changes": {"name": "Stellplatz Hafen", "type": "overnight"},
        "reason": "Wir haben letzte Nacht hier geschlafen.",
    }
    result = _sanitize(dict(raw), full_days=FULL_DAYS)
    assert result["action"] == "update", (
        "the existing overnight stop on the out-of-window day must be "
        "updated, not duplicated"
    )
    assert result["entity_id"] == "stop-b"
    without_full_days = _sanitize(dict(raw))
    assert without_full_days["action"] == "add", (
        "sanity check: without the full day list the old duplicate behavior "
        "is what the fallback prevents"
    )


if __name__ == "__main__":
    verify_schema_allows_prompt_mandated_fields()
    verify_echoed_changes_on_remove_are_dropped()
    verify_string_and_float_positions_survive()
    verify_coordinate_location_is_salvaged()
    verify_remove_then_reference_is_a_clear_sanitize_error()
    verify_stop_add_on_day_removed_in_same_draft_fails_clearly()
    verify_reference_to_stop_added_in_same_batch_is_accepted()
    verify_past_overnight_dedup_works_outside_the_window()
    print("Assistant robustness audit fix tests passed.")
