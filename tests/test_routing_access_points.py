"""Regression tests for route snapping without losing the real destination."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_routing_access_test"

# Minimal runtime stubs for loading routing.py outside Home Assistant.
aiohttp = types.ModuleType("aiohttp")


class ClientError(Exception):
    pass


class ClientSession:
    pass


aiohttp.ClientError = ClientError
aiohttp.ClientSession = ClientSession
sys.modules.setdefault("aiohttp", aiohttp)

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", helpers)
aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
aiohttp_client.async_get_clientsession = lambda hass: None
sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package
const = types.ModuleType(f"{PACKAGE_NAME}.const")
const.INTEGRATION_VERSION = "3.6.0"
sys.modules[const.__name__] = const


class RoadplannerError(RuntimeError):
    pass


class ValidationError(RoadplannerError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.RoadplannerError = RoadplannerError
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner

spec = spec_from_file_location(f"{PACKAGE_NAME}.routing", PACKAGE_ROOT / "routing.py")
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

points = [
    {
        "day_id": "day-1",
        "stop_id": "dunes",
        "name": "Wanderdünen Leba",
        "latitude": 54.76677,
        "longitude": 17.54254,
        "inherited": False,
    },
    {
        "day_id": "day-1",
        "stop_id": "hotel",
        "name": "Hotel",
        "latitude": 54.753,
        "longitude": 17.55,
        "inherited": False,
    },
]
payload = {
    "code": "Ok",
    "routes": [
        {
            "distance": 8_000.0,
            "duration": 900.0,
            "geometry": {
                "type": "LineString",
                "coordinates": [[17.53000, 54.76000], [17.55000, 54.75300]],
            },
            "legs": [{"distance": 8_000.0, "duration": 900.0}],
        }
    ],
    "waypoints": [
        {"location": [17.53000, 54.76000], "distance": 1_250.0},
        {"location": [17.55000, 54.75300], "distance": 0.0},
    ],
}
route = module.parse_osrm_response(
    payload,
    points=points,
    provider="osrm",
    profile="driving",
    endpoint_host="router.example",
    input_hash="hash",
)
assert route["point_count"] == 2
assert len(route["access_points"]) == 1
access = route["access_points"][0]
assert access["stop_id"] == "dunes"
assert access["target_latitude"] == 54.76677
assert access["target_longitude"] == 17.54254
assert access["navigation_latitude"] == 54.76
assert access["navigation_longitude"] == 17.53
assert access["distance_m"] == 1_250.0
assert access["derived"] is True
# The real target remains untouched in the canonical points and is not replaced
# by OSRM's nearest drivable waypoint.
assert points[0]["latitude"] == 54.76677
assert points[0]["longitude"] == 17.54254

combined = module.combine_route_segments(
    points=points,
    segment_results=[{"mode": "driving", **route}],
    provider="osrm",
    profile="driving",
    endpoint_host="router.example",
    input_hash="hash",
)
assert combined["access_points"][0]["stop_id"] == "dunes"
assert any("nächstgelegenen erreichbaren Zugang" in warning for warning in combined["warnings"])

source = Path("custom_components/roadplanner_mcp/routing.py").read_text(encoding="utf-8")
assert '"radiuses"' in source
assert "_MAX_SNAP_RADIUS_M" in source
assert source.count('"access_points": access_points') == 2  # segment + combined result
print("Routing access-point tests passed.")
