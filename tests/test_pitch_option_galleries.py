"""Behavioral tests for planning-image galleries on pitch backup options.

"Wie immer": before a place is visited its images come from internet
sources, afterwards from personal OneDrive photos. The ACTIVE overnight
place is a normal stop and already gets both behaviors. Backup options are
not stops - they plug into the same gallery machinery through synthetic
"option:<id>" keys resolved by _find_stop, so query building, image
search, save and refresh all work unchanged.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_pitch_gallery_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
for name, attributes in (
    ("homeassistant.core", {"HomeAssistant": object}),
    ("homeassistant.util", {}),
    ("homeassistant.util.dt", {"now": None, "utcnow": None, "DEFAULT_TIME_ZONE": None}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda *a, **k: None}),
    ("homeassistant.helpers.storage", {"Store": object}),
):
    stub = types.ModuleType(name)
    stub.__path__ = []
    for key, value in attributes.items():
        setattr(stub, key, value)
    sys.modules.setdefault(name, stub)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


helpers = load("experience_helpers")
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError

DAYS = [
    {
        "id": "day-1",
        "title": "Rostock",
        "details": {
            "overnight_plan": {
                "schema_version": 1,
                "strategy": "route_optimal",
                "options": [
                    {
                        "id": "opt-1",
                        "name": "Waldparkplatz am See",
                        "location": {"latitude": 54.2, "longitude": 12.4},
                        "notes": "kostenlos",
                        "status": "backup",
                    }
                ],
            }
        },
        "stops": [{"id": "stop-1", "name": "Hafen", "type": "overnight"}],
    },
    {"id": "day-2", "title": "Stralsund", "details": {}, "stops": []},
]


def verify_option_key_resolves_to_a_synthetic_overnight_stop() -> None:
    day, stop = helpers._find_stop(DAYS, "day-1", "option:opt-1")
    assert day["id"] == "day-1"
    assert stop["id"] == "option:opt-1"
    assert stop["name"] == "Waldparkplatz am See"
    assert stop["type"] == "overnight"
    assert stop["location"] == {"latitude": 54.2, "longitude": 12.4}
    assert stop["notes"] == "kostenlos"


def verify_option_key_resolves_even_with_a_stale_day_id() -> None:
    day, stop = helpers._find_stop(DAYS, "day-2", "option:opt-1")
    assert day["id"] == "day-1", "a unique option must resolve even from a stale day reference"
    assert stop["id"] == "option:opt-1"


def verify_unknown_option_raises_the_dedicated_error() -> None:
    try:
        helpers._find_stop(DAYS, "day-1", "option:opt-missing")
    except ValidationError as err:
        assert "Stellplatz-Option" in str(err)
    else:
        raise AssertionError("an unknown option key must raise")


def verify_real_stop_resolution_is_unchanged() -> None:
    day, stop = helpers._find_stop(DAYS, "day-1", "stop-1")
    assert stop["name"] == "Hafen"
    try:
        helpers._find_stop(DAYS, "day-1", "stop-missing")
    except ValidationError as err:
        assert "existiert nicht mehr" in str(err)
    else:
        raise AssertionError("unknown real stops must still raise")


if __name__ == "__main__":
    verify_option_key_resolves_to_a_synthetic_overnight_stop()
    verify_option_key_resolves_even_with_a_stale_day_id()
    verify_unknown_option_raises_the_dedicated_error()
    verify_real_stop_resolution_is_unchanged()
    print("Pitch option gallery resolution tests passed.")
