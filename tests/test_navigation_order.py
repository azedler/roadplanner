"""Regression tests for canonical stop order in navigation payloads."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_navigation_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for name in ("stop_ordering", "canonical_day"):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

routing = types.ModuleType(f"{PACKAGE_NAME}.routing")


def coordinate_from_location(location):
    if not isinstance(location, dict):
        return None
    lat = location.get("latitude")
    lon = location.get("longitude")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        return float(lat), float(lon)
    return None


routing.coordinate_from_location = coordinate_from_location
sys.modules[routing.__name__] = routing

spec = spec_from_file_location(f"{PACKAGE_NAME}.navigation", PACKAGE_ROOT / "navigation.py")
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def stop(stop_id, position, stop_type="waypoint", lat=50.0, lon=10.0):
    return {
        "id": stop_id,
        "name": stop_id,
        "position": position,
        "type": stop_type,
        "location": {"latitude": lat, "longitude": lon},
    }


days = [
    {"id": "day-1", "stops": [stop("camp", 1, "wildcamp", 49.0, 9.0)]},
    {
        "id": "day-2",
        "stops": [
            stop("third", 3, lat=53.0, lon=13.0),
            stop("first", 1, lat=51.0, lon=11.0),
            stop("second", 2, lat=52.0, lon=12.0),
        ],
    },
]

effective = module.effective_day_stops(days, 1)
assert [item["id"] for item in effective] == ["camp", "first", "second", "third"]
route = module.build_day_navigation(effective)
assert route["included_stop_ids"] == ["camp", "first", "second", "third"]

payload = {"days": days}
module.decorate_panel_navigation(payload)
assert payload["days"][1]["navigation"]["included_stop_ids"] == ["camp", "first", "second", "third"]

# When routing derives a nearest drivable access point, navigation uses it but
# the stop's canonical target coordinates remain unchanged for maps/media.
access_point = {
    "stop_id": "first",
    "navigation_latitude": 51.1001,
    "navigation_longitude": 11.1001,
    "target_latitude": 51.0,
    "target_longitude": 11.0,
    "distance_m": 950.0,
    "derived": True,
}
module.decorate_stop_navigation(days[1]["stops"][1], access_point)
assert days[1]["stops"][1]["navigation"]["uses_access_point"] is True
assert "51.1001000%2C11.1001000" in days[1]["stops"][1]["navigation"]["google_maps_navigation_url"]
assert days[1]["stops"][1]["location"] == {"latitude": 51.0, "longitude": 11.0}

print("Canonical navigation order tests passed.")
