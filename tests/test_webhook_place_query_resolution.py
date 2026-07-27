"""Behavioral test: external handoff ChangeSets get the same Google Maps
link resolution and geocoding the in-panel assistant chat already applies to
its own compiled operations, instead of reaching normalize_changeset raw."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_webhook_test"

# --- Minimal runtime stubs so the real modules can be imported outside HA ---
aiohttp = types.ModuleType("aiohttp")


class ClientError(Exception):
    pass


class ClientSession:
    pass


aiohttp.ClientError = ClientError
aiohttp.ClientSession = ClientSession
aiohttp_web = types.ModuleType("aiohttp.web")


class Request:
    pass


class Response:
    pass


def _json_response(*args, **kwargs):
    return Response()


aiohttp_web.Request = Request
aiohttp_web.Response = Response
aiohttp_web.json_response = _json_response
aiohttp.web = aiohttp_web
sys.modules.setdefault("aiohttp", aiohttp)
sys.modules["aiohttp.web"] = aiohttp_web

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

ha_core = types.ModuleType("homeassistant.core")


class HomeAssistant:
    pass


ha_core.HomeAssistant = HomeAssistant
sys.modules["homeassistant.core"] = ha_core

ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules["homeassistant.util"] = ha_util
ha_util_dt = types.ModuleType("homeassistant.util.dt")
ha_util_dt.now = lambda: None
sys.modules["homeassistant.util.dt"] = ha_util_dt

ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules["homeassistant.helpers"] = ha_helpers
ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp_client.async_get_clientsession = lambda hass: None
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client
ha_network = types.ModuleType("homeassistant.helpers.network")
ha_network.get_url = lambda hass, **kwargs: "http://example.invalid"
sys.modules["homeassistant.helpers.network"] = ha_network

ha_components = types.ModuleType("homeassistant.components")
ha_components.__path__ = []
sys.modules["homeassistant.components"] = ha_components
ha_webhook = types.ModuleType("homeassistant.components.webhook")
ha_webhook.async_register = lambda *args, **kwargs: None
ha_webhook.async_unregister = lambda *args, **kwargs: None
sys.modules["homeassistant.components.webhook"] = ha_webhook

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


# Load in dependency order so relative imports resolve inside the fake package.
load("const")
load("roadplanner")
load("changeset")
load("handoff")
load("canonical_day")
load("navigation")
load("routing")
load("manager")
load("geocoding")
load("destination_intelligence")
load("assistant_plugins")
load("google_maps_link")
webhook = load("webhook")


class FakeCandidate:
    def __init__(self, *, display_name: str, latitude: float, longitude: float):
        self.display_name = display_name
        self.latitude = latitude
        self.longitude = longitude
        self.score = 1.0
        self.importance = 1.0
        self.category = "amenity"
        self.result_type = "poi"
        self.source_url = "https://nominatim.example/lookup"
        self.resolution_mode = "reverse_coordinates"

    def as_location(self):
        return {
            "label": self.display_name,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    def as_provenance(self):
        return {"provider": "fake", "source_url": self.source_url}


class FakeGeocoder:
    enabled = True

    def __init__(self):
        self.queries: list[str] = []

    async def async_resolve(self, query: str, *, language: str = "de"):
        self.queries.append(query)
        latitude, longitude = (float(part) for part in query.split(","))
        return FakeCandidate(
            display_name="Schöner Stellplatz", latitude=latitude, longitude=longitude
        ), []


MAPS_URL = (
    "https://www.google.com/maps/place/Sch%C3%B6ner+Stellplatz/@52.5200,13.4050,15z"
)


async def verify_resolves_maps_link_on_stop_add() -> None:
    geocoder = FakeGeocoder()
    raw_changeset = {
        "operations": [
            {
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-1",
                "changes": {"name": "Übernachtung"},
                "place_query": MAPS_URL,
            }
        ]
    }
    resolved = await webhook._async_resolve_external_place_queries(
        HomeAssistant(), geocoder, raw_changeset
    )
    operation = resolved["operations"][0]
    assert "place_query" not in operation
    assert operation["changes"]["location"]["latitude"] == 52.5200
    assert operation["changes"]["location"]["longitude"] == 13.4050
    assert geocoder.queries == ["52.5200,13.4050"]


async def verify_leaves_non_stop_and_query_free_operations_untouched() -> None:
    geocoder = FakeGeocoder()
    raw_changeset = {
        "operations": [
            {"action": "remove", "entity_type": "stop", "day_id": "day-1", "entity_id": "stop-1"},
            {"action": "update", "entity_type": "trip", "changes": {"title": "Neuer Titel"}},
        ]
    }
    resolved = await webhook._async_resolve_external_place_queries(
        HomeAssistant(), geocoder, raw_changeset
    )
    assert resolved["operations"] == raw_changeset["operations"]
    assert geocoder.queries == []


async def verify_handles_missing_geocoder_without_crashing() -> None:
    raw_changeset = {
        "operations": [
            {
                "action": "add",
                "entity_type": "stop",
                "day_id": "day-1",
                "changes": {"name": "Übernachtung"},
                "place_query": MAPS_URL,
            }
        ]
    }
    resolved = await webhook._async_resolve_external_place_queries(
        HomeAssistant(), None, raw_changeset
    )
    operation = resolved["operations"][0]
    # No geocoder: the Maps link is still canonicalized, just not verified.
    assert operation["place_query"] == "52.5200,13.4050"


async def verify_non_dict_changeset_passes_through() -> None:
    resolved = await webhook._async_resolve_external_place_queries(
        HomeAssistant(), FakeGeocoder(), None
    )
    assert resolved is None


asyncio.run(verify_resolves_maps_link_on_stop_add())
asyncio.run(verify_leaves_non_stop_and_query_free_operations_untouched())
asyncio.run(verify_handles_missing_geocoder_without_crashing())
asyncio.run(verify_non_dict_changeset_passes_through())

print("Webhook place_query resolution tests passed.")
