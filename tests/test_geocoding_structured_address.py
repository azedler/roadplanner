"""Regression tests for structured, reviewable place resolution."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

# The contract test runs outside Home Assistant. Stub the small aiohttp
# surface imported by geocoding.py instead of requiring HA runtime packages.
aiohttp = types.ModuleType("aiohttp")


class ClientError(Exception):
    pass


class ClientSession:
    pass


aiohttp.ClientError = ClientError
aiohttp.ClientSession = ClientSession
sys.modules.setdefault("aiohttp", aiohttp)

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_geocoding_structured_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

const = types.ModuleType(f"{PACKAGE_NAME}.const")
const.INTEGRATION_VERSION = "3.5.0"
sys.modules[const.__name__] = const


class RoadplannerError(RuntimeError):
    pass


class ValidationError(RoadplannerError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.RoadplannerError = RoadplannerError
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
helpers = types.ModuleType("homeassistant.helpers")
helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", helpers)
aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
aiohttp_client.async_get_clientsession = lambda hass: None
sys.modules["homeassistant.helpers.aiohttp_client"] = aiohttp_client

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.geocoding",
    PACKAGE_ROOT / "geocoding.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

raw_address = """Krumhermsdorf Neuhäuser 40
Krumhermsdorf, Neustadt in Sachsen
Sächsische Schweiz-Osterzgebirge, Sachsen
01844, Deutschland"""
structured = module.parse_structured_address(address=raw_address)
assert structured.street == "Neuhäuser"
assert structured.house_number == "40"
assert structured.postal_code == "01844"
assert structured.city == "Neustadt in Sachsen"
assert structured.district == "Krumhermsdorf"
assert structured.state == "Sachsen"
assert structured.country == "Deutschland"
assert structured.country_code == "DE"

# The exact live examples must remain structured correctly. Aggregate POI
# search text may contain commas, but category tokens are never fake cities.
poi_structured = module.parse_structured_address(
    query="Fährterminal Tallinn, ferry",
    name="Fährterminal Tallinn",
)
assert poi_structured.city == ""
assert poi_structured.district == ""
assert poi_structured.name == "Fährterminal Tallinn"
assert module._provider_query("Fährterminal Tallinn") == "ferry terminal Tallinn"
assert module._category_intent_for_query("Fährterminal Tallinn") == "ferry"
assert module._core_query_tokens("Fährterminal Tallinn") == {"tallinn"}
assert (
    module._provider_query("Haukkankierros-Wanderung")
    == "Haukkankierros hiking trail"
)
assert module._category_intent_for_query("Haukkankierros-Wanderung") == "hiking"

single_line = module.parse_structured_address(
    query=(
        "Krumhermsdorf Neuhäuser 40, 01844 Neustadt in Sachsen, "
        "Ortsteil Krumhermsdorf"
    ),
    name="Krumhermsdorf Neuhäuser 40",
)
assert single_line.street == "Neuhäuser"
assert single_line.house_number == "40"
assert single_line.postal_code == "01844"
assert single_line.city == "Neustadt in Sachsen"
assert single_line.district == "Krumhermsdorf"


def item(address: dict, *, result_type: str = "house", osm_id: int = 1) -> dict:
    return {
        "display_name": "Neuhäuser 40, Krumhermsdorf, Neustadt in Sachsen, Deutschland",
        "lat": "50.9500",
        "lon": "14.2000",
        "importance": 0.5,
        "osm_type": "node",
        "osm_id": osm_id,
        "category": "place",
        "type": result_type,
        "address": address,
        "namedetails": {},
        "extratags": {},
    }


score_query = structured.full_query(raw_address)
exact = module.NominatimGeocoder._candidate_from_item(
    item(
        {
            "road": "Neuhäuser",
            "house_number": "40",
            "postcode": "01844",
            "town": "Neustadt in Sachsen",
            "suburb": "Krumhermsdorf",
            "state": "Sachsen",
            "country": "Deutschland",
            "country_code": "de",
        }
    ),
    query=score_query,
    structured_address=structured,
    search_variant="structured",
)
assert exact is not None
assert exact.match_type == "house_exact"
assert exact.match_label == "Hausnummer exakt"
assert exact.auto_selectable is True
assert exact.house_number_match is True
assert exact.street_match is True
assert exact.postal_code_match is True

street = module.NominatimGeocoder._candidate_from_item(
    item(
        {
            "road": "Neuhäuser",
            "postcode": "01844",
            "town": "Neustadt in Sachsen",
            "suburb": "Krumhermsdorf",
            "country_code": "de",
        },
        result_type="residential",
        osm_id=2,
    ),
    query=score_query,
    structured_address=structured,
    search_variant="district_text",
)
assert street is not None
assert street.match_type == "street"
assert street.auto_selectable is False
assert street.house_number_match is False

locality = module.NominatimGeocoder._candidate_from_item(
    item(
        {
            "town": "Neustadt in Sachsen",
            "suburb": "Krumhermsdorf",
            "country_code": "de",
        },
        result_type="town",
        osm_id=3,
    ),
    query=score_query,
    structured_address=structured,
    search_variant="free_text",
)
assert locality is not None
assert locality.match_type == "locality"
assert locality.auto_selectable is False

variants = module.NominatimGeocoder._search_variants(
    score_query,
    structured,
    language="de",
    limit=5,
)
assert 1 <= len(variants) <= 3
assert variants[0][0] == "structured"
assert variants[0][1]["street"] == "Neuhäuser 40"
assert variants[0][1]["postalcode"] == "01844"
assert variants[0][1]["countrycodes"] == "de"
assert all(not ({"q", "street"} <= set(params)) for _, params in variants)


class FakeResolver(module.NominatimGeocoder):
    def __init__(self, candidates):
        self._fake_candidates = candidates

    async def async_search(
        self,
        query,
        *,
        structured_address=None,
        language="de",
        limit=3,
    ):
        assert structured_address is structured
        assert language == "de"
        assert limit == 5
        return list(self._fake_candidates)


async def verify_selection_policy() -> None:
    selected, candidates = await FakeResolver([exact]).async_resolve(
        score_query,
        structured_address=structured,
        language="de",
    )
    assert selected is exact
    assert candidates == [exact]

    selected, candidates = await FakeResolver([street]).async_resolve(
        score_query,
        structured_address=structured,
        language="de",
    )
    assert selected is None
    assert candidates == [street]

    selected, candidates = await FakeResolver([locality]).async_resolve(
        score_query,
        structured_address=structured,
        language="de",
    )
    assert selected is None
    assert candidates == [locality]


async def verify_controlled_provider_calls() -> None:
    client = object.__new__(module.NominatimGeocoder)
    client.enabled = True
    client._base_url = "https://nominatim.openstreetmap.org/search"
    client._lock = asyncio.Lock()
    client._cache = {}
    calls = []

    async def fake_request(url, *, params, not_found_is_empty=False):
        assert url == client._base_url
        assert not not_found_is_empty
        calls.append(dict(params))
        if len(calls) == 1:
            return [
                item(
                    {
                        "road": "Neuhäuser",
                        "postcode": "01844",
                        "town": "Neustadt in Sachsen",
                        "suburb": "Krumhermsdorf",
                        "country_code": "de",
                    },
                    result_type="residential",
                    osm_id=20,
                )
            ]
        return [
            item(
                {
                    "road": "Neuhäuser",
                    "house_number": "40",
                    "postcode": "01844",
                    "town": "Neustadt in Sachsen",
                    "suburb": "Krumhermsdorf",
                    "country_code": "de",
                },
                osm_id=21,
            )
        ]

    client._async_get_json_locked = fake_request
    candidates = await client.async_search(
        "Adresse Neuhäuser 40 in Krumhermsdorf",
        structured_address=structured,
        language="de",
        limit=5,
    )
    assert len(calls) == 2
    assert "street" in calls[0] and "q" not in calls[0]
    assert "q" in calls[1]
    assert candidates[0].match_type == "house_exact"
    assert candidates[0].auto_selectable is True


asyncio.run(verify_selection_policy())
asyncio.run(verify_controlled_provider_calls())
print("Structured geocoding regression tests passed.")
