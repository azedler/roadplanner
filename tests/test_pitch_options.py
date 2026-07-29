"""Behavioral tests for the per-day overnight plan (Stellplatz-Optionen).

Pure-logic tests: option validation, the day-anchored plan storage, and the
activation swap. The swap is the heart of the feature - the previous active
place must always be demoted into the options list (never silently lost),
and activating a former backup after "Platz voll" must restore its full
data, including on a round-trip back to the original place.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_pitch_options_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


load("json_io")
load("identifiers")
load("assistant_shared")
pitch = load("pitch_options")

ValidationError = sys.modules[f"{PACKAGE_NAME}.json_io"].ValidationError

NOW = "2026-07-29T10:00:00+00:00"


def _day_document(stops=None, details=None):
    return {
        "day": {
            "id": "day-1",
            "title": "Tag 1",
            "details": details if details is not None else {},
        },
        "stops": stops if stops is not None else [],
    }


def _raise(callable_, *args, **kwargs):
    try:
        callable_(*args, **kwargs)
    except ValidationError as err:
        return str(err)
    raise AssertionError("expected ValidationError")


def verify_option_requires_a_name() -> None:
    message = _raise(pitch.validate_option_input, {"name": "  "}, now=NOW)
    assert "name" in message


def verify_save_option_generates_id_and_enforces_the_limit() -> None:
    document = _day_document()
    saved = pitch.apply_save_option(document, {"name": "Hafen"}, now=NOW)
    assert saved["id"].startswith("opt")
    assert saved["status"] == "backup"
    plan = pitch.get_overnight_plan(document["day"])
    assert [item["name"] for item in plan["options"]] == ["Hafen"]

    for index in range(pitch.MAX_OVERNIGHT_OPTIONS - 1):
        pitch.apply_save_option(document, {"name": f"Platz {index}"}, now=NOW)
    message = _raise(pitch.apply_save_option, document, {"name": "Zuviel"}, now=NOW)
    assert "maximal" in message


def verify_update_by_id_preserves_created_at() -> None:
    document = _day_document()
    saved = pitch.apply_save_option(document, {"name": "Hafen"}, now="2026-07-01T00:00:00+00:00")
    updated = pitch.apply_save_option(
        document,
        {"id": saved["id"], "name": "Hafen Nord", "price": {"amount": 12}},
        now=NOW,
    )
    assert updated["id"] == saved["id"]
    assert updated["created_at"] == "2026-07-01T00:00:00+00:00"
    assert updated["updated_at"] == NOW
    assert updated["price"] == {"amount": 12.0, "currency": "EUR"}
    plan = pitch.get_overnight_plan(document["day"])
    assert len(plan["options"]) == 1


def verify_delete_and_unknown_id() -> None:
    document = _day_document()
    saved = pitch.apply_save_option(document, {"name": "Hafen"}, now=NOW)
    pitch.apply_delete_option(document, saved["id"])
    assert pitch.get_overnight_plan(document["day"])["options"] == []
    message = _raise(pitch.apply_delete_option, document, "opt-missing")
    assert "nicht gefunden" in message


def verify_reject_and_restore_status() -> None:
    document = _day_document()
    saved = pitch.apply_save_option(document, {"name": "Hafen"}, now=NOW)
    rejected = pitch.apply_set_option_status(document, saved["id"], "rejected", now=NOW)
    assert rejected["status"] == "rejected"
    restored = pitch.apply_set_option_status(document, saved["id"], "backup", now=NOW)
    assert restored["status"] == "backup"
    message = _raise(pitch.apply_set_option_status, document, saved["id"], "gone", now=NOW)
    assert "Optionsstatus" in message


def verify_strategy_is_validated_and_stored() -> None:
    document = _day_document()
    assert pitch.apply_set_strategy(document, "best_first") == "best_first"
    assert pitch.get_overnight_plan(document["day"])["strategy"] == "best_first"
    message = _raise(pitch.apply_set_strategy, document, "teleport")
    assert "Strategie" in message


def verify_activation_swaps_stop_and_demotes_previous_place() -> None:
    stop = {
        "id": "stop-1",
        "name": "Stellplatz am Hafen",
        "type": "stellplatz",
        "location": {"latitude": 54.09, "longitude": 12.13},
        "notes": "Reserviert für Fr.",
        "details": {},
    }
    document = _day_document(stops=[{"id": "stop-0", "name": "Bäcker", "type": "waypoint"}, stop])
    option = pitch.apply_save_option(
        document,
        {"name": "Waldparkplatz am See", "location": {"latitude": 54.2, "longitude": 12.4}, "notes": "kostenlos"},
        now=NOW,
    )
    result = pitch.apply_activate_option(document, option["id"], now=NOW, actor="user:test")

    assert result["stop_id"] == "stop-1", "the existing overnight stop must be patched, not replaced"
    assert result["materialized"] is False
    assert stop["name"] == "Waldparkplatz am See"
    assert stop["location"] == {"latitude": 54.2, "longitude": 12.4}
    assert stop["notes"] == "kostenlos"
    assert stop["details"]["overnight_option_id"] == option["id"]
    assert stop["details"]["overnight_selection"]["selected_by"] == "user:test"

    plan = pitch.get_overnight_plan(document["day"])
    assert [item["name"] for item in plan["options"]] == ["Stellplatz am Hafen"], (
        "the previous place must be demoted into the options list, never lost"
    )
    demoted = plan["options"][0]
    assert demoted["status"] == "backup"
    assert demoted["source"]["type"] == "roadbook"
    assert demoted["notes"] == "Reserviert für Fr."


def verify_activation_round_trip_restores_the_original_place() -> None:
    stop = {
        "id": "stop-1",
        "name": "Stellplatz am Hafen",
        "type": "overnight",
        "location": {"latitude": 54.09, "longitude": 12.13},
        "notes": "Strom inklusive",
        "details": {},
    }
    document = _day_document(stops=[stop])
    plan_b = pitch.apply_save_option(document, {"name": "Waldparkplatz"}, now=NOW)
    pitch.apply_activate_option(document, plan_b["id"], now=NOW, actor="user:test")
    demoted_id = pitch.get_overnight_plan(document["day"])["options"][0]["id"]
    pitch.apply_activate_option(document, demoted_id, now=NOW, actor="user:test")

    assert stop["name"] == "Stellplatz am Hafen"
    assert stop["location"] == {"latitude": 54.09, "longitude": 12.13}
    assert stop["notes"] == "Strom inklusive"
    plan = pitch.get_overnight_plan(document["day"])
    assert [item["name"] for item in plan["options"]] == ["Waldparkplatz"], (
        "Plan B must come back as a backup after switching back to Plan A"
    )


def verify_activation_materializes_a_stop_when_none_exists() -> None:
    document = _day_document(stops=[{"id": "stop-0", "name": "Bäcker", "type": "waypoint"}])
    option = pitch.apply_save_option(document, {"name": "Wiese am Fluss"}, now=NOW)
    result = pitch.apply_activate_option(document, option["id"], now=NOW, actor="user:test")
    assert result["materialized"] is True
    assert result["demoted_option"] is None
    new_stop = document["stops"][-1]
    assert new_stop["type"] == "overnight"
    assert new_stop["name"] == "Wiese am Fluss"
    assert pitch.get_overnight_plan(document["day"])["options"] == []


def verify_rejected_option_cannot_be_activated_directly() -> None:
    document = _day_document(stops=[])
    option = pitch.apply_save_option(document, {"name": "Laut an der Straße"}, now=NOW)
    pitch.apply_set_option_status(document, option["id"], "rejected", now=NOW)
    message = _raise(pitch.apply_activate_option, document, option["id"], now=NOW, actor="user:test")
    assert "verworfene" in message


def verify_preferences_validation_and_lenient_read() -> None:
    validated = pitch.validate_preferences_input(
        {
            "required": {"dog_ok": True},
            "preferred": {"quiet": 5, "power": 1},
            "limits": {"max_price_per_night": 25, "max_detour_minutes": 20},
            "avoid": ["busy_road"],
            "vehicle": {"height_m": 2.85},
            "free_text": "Ruhig und naturnah.",
        }
    )
    assert validated["schema_version"] == pitch.PREFERENCES_SCHEMA_VERSION
    assert validated["preferred"]["quiet"] == 5
    assert validated["limits"]["max_price_per_night"] == 25.0
    assert validated["vehicle"]["height_m"] == 2.85

    message = _raise(pitch.validate_preferences_input, {"preferred": {"quiet": 9}})
    assert "0 bis 5" in message

    assert pitch.get_pitch_preferences({"details": {"pitch_preferences": "kaputt"}})["required"] == {}
    assert pitch.get_pitch_preferences({})["free_text"] == ""


def verify_lenient_plan_read_drops_garbage() -> None:
    day = {
        "details": {
            "overnight_plan": {
                "strategy": "warp",
                "options": [
                    {"id": "opt-1", "name": "Gültig"},
                    {"name": "ohne id"},
                    "kein objekt",
                ],
            }
        }
    }
    plan = pitch.get_overnight_plan(day)
    assert plan["strategy"] == pitch.DEFAULT_OVERNIGHT_STRATEGY
    assert [item["name"] for item in plan["options"]] == ["Gültig"]


if __name__ == "__main__":
    verify_option_requires_a_name()
    verify_save_option_generates_id_and_enforces_the_limit()
    verify_update_by_id_preserves_created_at()
    verify_delete_and_unknown_id()
    verify_reject_and_restore_status()
    verify_strategy_is_validated_and_stored()
    verify_activation_swaps_stop_and_demotes_previous_place()
    verify_activation_round_trip_restores_the_original_place()
    verify_activation_materializes_a_stop_when_none_exists()
    verify_rejected_option_cannot_be_activated_directly()
    verify_preferences_validation_and_lenient_read()
    verify_lenient_plan_read_drops_garbage()
    print("Pitch options (overnight plan) tests passed.")
