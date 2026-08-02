"""Regression test: a user-shared Google-Maps pin counts as confirmed GPS.

Live report: the user shared a maps.app.goo.gl link for tonight's
overnight spot, the stop was created at exactly the link's coordinates -
and still showed "Ort noch prüfen - GPS vorhanden, aber noch nicht
bestätigt" forever. A pin the user shared themselves is their explicit
choice of map point (like a manually confirmed coordinate), so when
reverse geocoding cannot attach an address the status is now
"manual_confirmed" instead of "coordinates_unverified*". The origin flag
is server-set AFTER sanitizing (a server-controlled operation field), so
a model can never mark its own coordinates as user-confirmed.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_maps_pin_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
ha = types.ModuleType("homeassistant")
ha.__path__ = []
sys.modules.setdefault("homeassistant", ha)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


load("geocoding")
plugins = load("assistant_plugins")


class DisabledGeocoder:
    enabled = False


PLUGIN = plugins.GeocodingAssistantPlugin(DisabledGeocoder())


def enrich(operation: dict) -> tuple[dict, list[str]]:
    result = asyncio.run(
        PLUGIN.async_enrich_operations(
            operations=[operation],
            open_questions=[],
            context={},
        )
    )
    return result.operations[0], result.open_questions


def stop_operation(**extra) -> dict:
    return {
        "operation_id": "op-1",
        "action": "add",
        "entity_type": "stop",
        "entity_id": "new-stop-1",
        "day_id": "day-1",
        "changes": {"name": "Übernachtungsplatz (Google Maps Link)", "type": "overnight"},
        "place_query": "60.6750132,17.1467019",
        **extra,
    }


def verify_user_pin_counts_as_confirmed() -> None:
    value, questions = enrich(
        stop_operation(place_query_origin="user_google_maps_link")
    )
    geocoding = value["changes"]["details"]["geocoding"]
    assert geocoding["status"] == "manual_confirmed", geocoding
    assert geocoding["provider"] == "google_maps_link"
    assert geocoding["provider_verified"] is False
    assert geocoding["requires_confirmation"] is False
    assert value["changes"]["location"]["latitude"] == 60.6750132
    assert questions == [], "a user-shared pin needs no verification question"
    assert "place_query_origin" not in value, "the flag never reaches the ChangeSet"


def verify_plain_coordinates_stay_unverified() -> None:
    value, questions = enrich(stop_operation())
    geocoding = value["changes"]["details"]["geocoding"]
    assert geocoding["status"] == "coordinates_unverified_geocoding_disabled"
    assert geocoding["requires_confirmation"] is True
    assert len(questions) == 1, "coordinates without user-link provenance keep the review question"


def verify_origin_flag_never_leaks_from_non_stop_operations() -> None:
    value, _ = enrich(
        {
            "operation_id": "op-1",
            "action": "update",
            "entity_type": "trip",
            "changes": {"notes": "x"},
            "place_query_origin": "user_google_maps_link",
        }
    )
    assert "place_query_origin" not in value


def verify_model_cannot_set_the_origin_itself() -> None:
    compile_source = (PACKAGE_ROOT / "assistant_compile.py").read_text(encoding="utf-8")
    assert '"place_query_origin",' in compile_source.split(
        "_SERVER_CONTROLLED_OPERATION_FIELDS"
    )[1].split("}")[0], "the origin must be a server-controlled operation field"
    assistant_source = (PACKAGE_ROOT / "assistant.py").read_text(encoding="utf-8")
    assert 'sanitized["place_query_origin"] = "user_google_maps_link"' in assistant_source, (
        "the origin is set server-side AFTER sanitizing"
    )


if __name__ == "__main__":
    verify_user_pin_counts_as_confirmed()
    verify_plain_coordinates_stay_unverified()
    verify_origin_flag_never_leaks_from_non_stop_operations()
    verify_model_cannot_set_the_origin_itself()
    print("User-pinned Google-Maps coordinate tests passed.")
