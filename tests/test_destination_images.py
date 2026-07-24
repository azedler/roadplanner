"""Regression tests for multi-provider destination image search."""
from __future__ import annotations

import asyncio
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_destination_image_test"

# The pure contract test runs outside Home Assistant. Stub the tiny aiohttp
# surface used by destination_images.py instead of requiring the full HA stack.
aiohttp = types.ModuleType("aiohttp")


class ClientError(Exception):
    pass


class ClientTimeout:
    def __init__(self, **kwargs):
        self.options = kwargs


aiohttp.ClientError = ClientError
aiohttp.ClientTimeout = ClientTimeout
sys.modules["aiohttp"] = aiohttp

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

const_module = types.ModuleType(f"{PACKAGE_NAME}.const")
manifest = json.loads(
    (PACKAGE_ROOT / "manifest.json").read_text(encoding="utf-8")
)
const_module.INTEGRATION_VERSION = str(manifest["version"])
sys.modules[const_module.__name__] = const_module

roadplanner_module = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")


class ValidationError(RuntimeError):
    pass


roadplanner_module.ValidationError = ValidationError
sys.modules[roadplanner_module.__name__] = roadplanner_module

homeassistant = types.ModuleType("homeassistant")
homeassistant_core = types.ModuleType("homeassistant.core")
homeassistant_helpers = types.ModuleType("homeassistant.helpers")
homeassistant_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")


class HomeAssistant:
    pass


homeassistant_core.HomeAssistant = HomeAssistant
homeassistant_aiohttp.async_get_clientsession = lambda _hass: None
sys.modules["homeassistant"] = homeassistant
sys.modules["homeassistant.core"] = homeassistant_core
sys.modules["homeassistant.helpers"] = homeassistant_helpers
sys.modules["homeassistant.helpers.aiohttp_client"] = homeassistant_aiohttp

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.destination_images",
    PACKAGE_ROOT / "destination_images.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

commons_payload = {
    "query": {
        "pages": [
            {
                "pageid": 42,
                "title": "File:Hill of Crosses.jpg",
                "imageinfo": [
                    {
                        "mime": "image/jpeg",
                        "thumburl": "https://upload.wikimedia.org/thumb.jpg",
                        "url": "https://upload.wikimedia.org/original.jpg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Hill_of_Crosses.jpg",
                        "width": 2400,
                        "height": 1600,
                        "extmetadata": {
                            "Artist": {"value": "Example Author"},
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0/"},
                            "ImageDescription": {"value": "Hill of Crosses near Domantai, Lithuania"},
                        },
                    }
                ],
            }
        ]
    }
}
commons = module._parse_commons_response(commons_payload, limit=3, proximity=True)
assert len(commons) == 1
assert commons[0]["provider"] == "wikimedia_commons"
assert commons[0]["proximity_match"] is True
assert commons[0]["license"] == "CC BY-SA 4.0"
assert commons[0]["width"] == 2400

openverse_payload = {
    "results": [
        {
            "id": "open-1",
            "title": "Hill of Crosses Lithuania",
            "thumbnail": "https://images.openverse.org/thumb.jpg",
            "url": "https://images.openverse.org/original.jpg",
            "foreign_landing_url": "https://example.org/photo/open-1",
            "creator": "Open Author",
            "license": "by",
            "license_version": "4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "width": 1800,
            "height": 1200,
            "mature": False,
        },
        {
            "id": "mature",
            "title": "Should not be used",
            "thumbnail": "https://images.openverse.org/mature.jpg",
            "foreign_landing_url": "https://example.org/photo/mature",
            "mature": True,
        },
    ]
}
openverse = module._parse_openverse_response(openverse_payload, limit=3)
assert len(openverse) == 1
assert openverse[0]["provider"] == "openverse"
assert openverse[0]["license"] == "BY 4.0"

ranked = module._deduplicate_and_rank(
    [commons[0], openverse[0], {**openverse[0], "id": "duplicate"}],
    query="Berg der Kreuze Domantai Litauen",
    limit=3,
)
assert len(ranked) == 2
assert ranked[0]["provider"] == "wikimedia_commons", "coordinate matches must rank first"
assert all("score" in item for item in ranked)
assert all("selection_score" in item for item in ranked)
assert all("quality_score" in item for item in ranked)
assert all("relevance_score" in item for item in ranked)
assert all("selection_reason" in item for item in ranked)
assert [item["rank"] for item in ranked] == [1, 2]

# Representative landscape photos must outrank logos and near-identical results.
logo = {
    **openverse[0],
    "id": "logo",
    "title": "Hill of Crosses logo map poster",
    "source_url": "https://example.org/photo/logo",
    "original_url": "https://images.openverse.org/logo.jpg",
    "width": 400,
    "height": 400,
}
similar = {
    **commons[0],
    "id": "commons-similar",
    "title": "Hill of Crosses near Domantai Lithuania panorama",
    "source_url": "https://commons.wikimedia.org/wiki/File:Hill_of_Crosses_panorama.jpg",
    "original_url": "https://upload.wikimedia.org/panorama.jpg",
}
representative = module._deduplicate_and_rank(
    [logo, commons[0], similar, openverse[0]],
    query="Berg der Kreuze Domantai Litauen",
    limit=4,
)
assert representative[0]["provider"] == "wikimedia_commons"
assert representative[0]["selection_score"] > next(
    item["selection_score"] for item in representative if item["id"] == "logo"
)
assert len(representative) == 4


async def test_fail_open() -> None:
    provider = module.DestinationImageProvider(HomeAssistant())

    async def broken_commons(*_args, **_kwargs):
        raise ValidationError("Commons unavailable")

    async def working_openverse(*_args, **_kwargs):
        return openverse

    provider._search_commons = broken_commons
    provider._search_openverse = working_openverse
    result = await provider.async_search("Berg der Kreuze Litauen", limit=3)
    assert result["count"] == 1
    assert result["results"][0]["provider"] == "openverse"
    assert "wikimedia_commons" in result["provider_errors"]


async def test_overlong_internal_query_is_bounded() -> None:
    provider = module.DestinationImageProvider(HomeAssistant())
    observed: list[str] = []

    async def capture_commons(query, *, latitude, longitude, limit):
        assert latitude is None
        assert longitude is None
        assert limit >= 8
        observed.append(query)
        return []

    async def capture_openverse(query, *, limit):
        assert limit >= 8
        observed.append(query)
        return []

    provider._search_commons = capture_commons
    provider._search_openverse = capture_openverse
    result = await provider.async_search(("Tallinn Terminal D " * 80).strip(), limit=3)
    assert observed
    assert len(set(observed)) == 1
    assert all(len(value) <= module._MAX_QUERY_LENGTH for value in observed)
    assert len(result["query"]) <= module._MAX_QUERY_LENGTH
    assert result["query"].endswith("D")


asyncio.run(test_fail_open())
asyncio.run(test_overlong_internal_query_is_bounded())
print("Destination image provider tests passed.")
