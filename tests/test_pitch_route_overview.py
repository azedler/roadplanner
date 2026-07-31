"""Behavioral tests for the per-option Stellplatz detour routes.

Deferred sprint item, now built: every overnight candidate of a day (the
active stop plus each non-rejected option with GPS) gets a real road route
through the corridor "last GPS stop before the overnight -> candidate ->
first own GPS stop of the next day". The direct corridor route is the
baseline, so each candidate shows its true detour ("+12 min · +8,4 km").
Display-only, hash-cached, nothing is written to the roadbook.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_pitch_route_test"

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
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
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


pitch_routing = load("pitch_routing")
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError


DAY_POINTS = [
    {"day_id": "day-2", "stop_id": "s-prev", "name": "Übernachtung gestern", "type": "campsite", "inherited": True, "latitude": 63.0, "longitude": 18.0},
    {"day_id": "day-2", "stop_id": "s-1", "name": "Wanderung", "type": "activity", "inherited": False, "latitude": 63.1, "longitude": 18.2},
    {"day_id": "day-2", "stop_id": "s-2", "name": "Camping Abend", "type": "campsite", "inherited": False, "latitude": 63.2, "longitude": 18.4},
]
NEXT_DAY_POINTS = [
    {"day_id": "day-3", "stop_id": "s-2", "name": "Camping Abend", "type": "campsite", "inherited": True, "latitude": 63.2, "longitude": 18.4},
    {"day_id": "day-3", "stop_id": "s-3", "name": "Museum morgen", "type": "attraction", "inherited": False, "latitude": 63.4, "longitude": 18.9},
]


def verify_corridor_anchors() -> None:
    anchors = pitch_routing.corridor_anchors(DAY_POINTS, NEXT_DAY_POINTS)
    assert anchors["origin"]["stop_id"] == "s-1", (
        "origin is the last GPS stop BEFORE the overnight, not the overnight"
    )
    assert anchors["active"]["stop_id"] == "s-2", "the day's own overnight is the active candidate"
    assert anchors["destination"]["stop_id"] == "s-3", (
        "destination is the next day's first OWN stop, skipping the inherited "
        "overnight-continuity start"
    )


def verify_corridor_without_next_day() -> None:
    anchors = pitch_routing.corridor_anchors(DAY_POINTS, None)
    assert anchors["destination"] is None
    anchors = pitch_routing.corridor_anchors([DAY_POINTS[0]], NEXT_DAY_POINTS)
    assert anchors["active"] is None, "an inherited overnight is never the active candidate"
    assert anchors["origin"]["stop_id"] == "s-prev"


class _FakeRouter:
    configured = True
    profile = "driving"

    def __init__(self):
        self.calls = []

    async def async_calculate(self, points, *, input_hash):
        self.calls.append([p["stop_id"] for p in points])
        # Direct corridor: 60 min / 50 km. Any 3-point detour: 75 min / 58 km.
        if len(points) == 2:
            return {"duration_s": 3600, "distance_m": 50_000, "geometry": {"type": "LineString", "coordinates": [[18.2, 63.1], [18.9, 63.4]]}}
        return {"duration_s": 4500, "distance_m": 58_000, "geometry": {"type": "LineString", "coordinates": [[18.2, 63.1], [18.4, 63.2], [18.9, 63.4]]}}


class _FakeStore:
    def get_routing_plan(self, *, trip_id=None, day_ids=None):
        return {"days": [
            {"day_id": "day-2", "points": DAY_POINTS},
            {"day_id": "day-3", "points": NEXT_DAY_POINTS},
        ]}


class _FakeManager:
    def __init__(self):
        self.router = _FakeRouter()
        self.store = _FakeStore()

    async def async_get_assistant_payload(self, trip_id=None):
        return {"days": {"days": [{
            "id": "day-2",
            "details": {"overnight_plan": {"strategy": "route_optimal", "options": [
                {"id": "opt-1", "name": "Wiese am See", "status": "backup", "location": {"latitude": 63.25, "longitude": 18.5}},
                {"id": "opt-2", "name": "Ohne GPS", "status": "backup", "location": {}},
                {"id": "opt-3", "name": "Verworfen", "status": "rejected", "location": {"latitude": 63.3, "longitude": 18.6}},
            ]}},
        }]}}


class _FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def verify_overview_computes_detours_and_caches() -> None:
    manager = _FakeManager()
    service = pitch_routing.PitchRouteService(_FakeHass(), manager)
    result = _run(service.async_overview("day-2"))
    assert result["baseline"] == {"duration_minutes": 60, "distance_km": 50.0}
    by_name = {entry["name"]: entry for entry in result["candidates"]}
    assert by_name["Camping Abend"]["kind"] == "active"
    assert by_name["Camping Abend"]["extra_duration_minutes"] == 15
    assert by_name["Wiese am See"]["extra_duration_minutes"] == 15
    assert by_name["Wiese am See"]["extra_distance_km"] == 8.0
    assert by_name["Wiese am See"]["geometry"], "each candidate carries map geometry"
    assert result["skipped"] == ["Ohne GPS"], "a GPS-less option is named, not silently dropped"
    assert all("Verworfen" != entry["name"] for entry in result["candidates"]), (
        "rejected options are not routed"
    )
    calls_before = len(manager.router.calls)
    _run(service.async_overview("day-2"))
    assert len(manager.router.calls) == calls_before, (
        "a second overview with unchanged coordinates must be fully served "
        "from the hash cache - zero provider calls"
    )


def verify_unconfigured_router_gives_clear_error() -> None:
    manager = _FakeManager()
    manager.router.configured = False
    service = pitch_routing.PitchRouteService(_FakeHass(), manager)
    try:
        _run(service.async_overview("day-2"))
    except ValidationError as err:
        assert "nicht aktiviert" in str(err)
    else:
        raise AssertionError("without routing the overview must fail clearly")


def verify_wiring_contract() -> None:
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert 'if action == "pitch_route_overview":' in panel_source
    pitches_js = (PACKAGE_ROOT / "frontend/features/pitches.js").read_text(encoding="utf-8")
    assert "_loadPitchRoutes" in pitches_js
    assert 'plan.strategy === "route_optimal"' in pitches_js, (
        "route_optimal must rank options by computed detour"
    )
    assert "routePaths" in pitches_js and "_renderMap(`pitch-map-${day.id}`, context.points, day.title, routePaths" in pitches_js, (
        "the computed geometries must reach the map as path overlays"
    )
    panel_js = (PACKAGE_ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
    assert 'action === "pitch-routes"' in panel_js


if __name__ == "__main__":
    verify_corridor_anchors()
    verify_corridor_without_next_day()
    verify_overview_computes_detours_and_caches()
    verify_unconfigured_router_gives_clear_error()
    verify_wiring_contract()
    print("Pitch route overview tests passed.")
