"""Contract tests for optional Google Places destination resolution."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_google_places_test"

# Minimal runtime stubs for loading the provider outside Home Assistant.
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


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


geocoding = load("geocoding")
google_places = load("google_places")
place_providers = load("place_providers")
google_photo_token_service = load("google_photo_token_service")


class FakeGoogle(google_places.GooglePlacesClient):
    def __init__(self, payload):
        self.enabled = True
        self.configured = True
        self._cache = {}
        self.payload = payload
        self.last_body = None
        self.request_count = 0

    async def _request(self, body):
        self.request_count += 1
        self.last_body = body
        return self.payload


PAYLOAD = {
    "places": [
        {
            "id": "ChIJ-roadplanner-test",
            "displayName": {"text": "RMK Matsiranna telkimisala"},
            "formattedAddress": "Matsi küla, 88219 Pärnumaa, Estonia",
            "shortFormattedAddress": "Matsi, Estonia",
            "location": {"latitude": 58.34231, "longitude": 23.73091},
            "addressComponents": [
                {
                    "longText": "Matsi küla",
                    "shortText": "Matsi",
                    "types": ["locality"],
                },
                {
                    "longText": "88219",
                    "shortText": "88219",
                    "types": ["postal_code"],
                },
                {
                    "longText": "Estonia",
                    "shortText": "EE",
                    "types": ["country"],
                },
            ],
            "primaryType": "campground",
            "types": ["campground", "point_of_interest"],
            "googleMapsUri": "https://maps.google.com/?cid=123",
            "businessStatus": "OPERATIONAL",
        }
    ]
}


async def verify_google_candidate() -> None:
    client = FakeGoogle(PAYLOAD)
    best, candidates = await client.async_resolve(
        "RMK Matsiranna telkimisala camping Estonia",
        language="de",
        location_bias=(58.34, 23.73),
    )
    assert best is not None
    assert candidates and candidates[0] is best
    assert best.provider == "google_places"
    assert best.canonical_provider_id == "ChIJ-roadplanner-test"
    assert best.provider_attribution == "Google Maps"
    assert best.source_url == "https://maps.google.com/?cid=123"
    assert best.address["country_code"] == "EE"
    assert best.address["city"] == "Matsi küla"
    assert best.category_match is True
    assert best.auto_selectable is True
    assert client.last_body["includedType"] == "campground"
    assert client.last_body["regionCode"] == "EE"
    assert client.last_body["locationBias"]["circle"]["center"] == {
        "latitude": 58.34,
        "longitude": 23.73,
    }
    assert client.last_body["locationBias"]["circle"]["radius"] == 50_000.0
    assert "api" not in str(client.last_body).casefold()
    assert "photos" not in google_places._FIELD_MASK

    cached_best, cached_candidates = await client.async_resolve(
        "RMK Matsiranna telkimisala camping Estonia",
        language="de",
        location_bias=(58.34, 23.73),
    )
    assert cached_best is best
    assert cached_candidates[0] is best
    assert client.request_count == 1
    cache_key = next(iter(client._cache))
    _expires_at, result = client._cache[cache_key]
    client._cache[cache_key] = (0.0, result)
    await client.async_resolve(
        "RMK Matsiranna telkimisala camping Estonia",
        language="de",
        location_bias=(58.34, 23.73),
    )
    assert client.request_count == 2, "expired Google content must be refreshed"


class FakeProvider:
    def __init__(self, *, best=None, alternatives=None):
        self.enabled = True
        self.best = best
        self.alternatives = list(alternatives or ([] if best is None else [best]))
        self.calls = []
        self.reverse_calls = []

    async def async_resolve(
        self,
        query,
        *,
        structured_address=None,
        language="de",
        location_bias=None,
    ):
        self.calls.append((query, structured_address, language, location_bias))
        return self.best, list(self.alternatives)

    async def async_reverse(self, latitude, longitude, *, language="de"):
        self.reverse_calls.append((latitude, longitude, language))
        return self.best


def candidate(provider: str, identifier: str, score: float, *, auto: bool):
    return geocoding.GeocodingCandidate(
        display_name=f"{identifier}, Estonia",
        latitude=58.34,
        longitude=23.73,
        importance=0.5,
        score=score,
        osm_type="node" if provider == "nominatim" else "",
        osm_id=42 if provider == "nominatim" else None,
        category="tourism",
        result_type="campground",
        address={"country_code": "ee"},
        namedetails={"name": identifier},
        extratags={},
        core_token_match=0.9,
        country_match=True,
        category_match=True,
        category_intent="camping",
        match_type="poi",
        match_label="Campingplatz",
        auto_selectable=auto,
        provider=provider,
        provider_id=identifier if provider != "nominatim" else None,
        provider_attribution=(
            "Google Maps" if provider == "google_places" else "© OpenStreetMap contributors"
        ),
    )


async def verify_composite_fallback() -> None:
    weak = candidate("nominatim", "weak-osm", 0.45, auto=False)
    google = candidate("google_places", "strong-google", 0.93, auto=True)
    primary = FakeProvider(best=None, alternatives=[weak])
    fallback = FakeProvider(best=google)
    composite = place_providers.CompositePlaceProvider(primary, fallback, mode="fallback")
    best, alternatives = await composite.async_resolve(
        "RMK Matsiranna telkimisala Estonia",
        location_bias=(58.34, 23.73),
    )
    assert best is google
    assert primary.calls and fallback.calls
    assert fallback.calls[0][3] == (58.34, 23.73)
    assert alternatives[0] is google
    assert weak in alternatives

    strong_osm = candidate("nominatim", "strong-osm", 0.91, auto=True)
    primary = FakeProvider(best=strong_osm)
    fallback = FakeProvider(best=google)
    composite = place_providers.CompositePlaceProvider(primary, fallback, mode="fallback")
    best, alternatives = await composite.async_resolve("Known place")
    assert best is strong_osm
    assert primary.calls
    assert not fallback.calls, "a safe primary result must avoid a billable fallback request"
    assert alternatives == [strong_osm]

    reverse = await composite.async_reverse(58.34, 23.73, language="de")
    assert reverse is strong_osm
    assert primary.reverse_calls == [(58.34, 23.73, "de")]
    assert fallback.reverse_calls == [], "Google must not provide durable reverse data"


class FakeGooglePhotos(google_places.GooglePlacesClient):
    """Overrides network calls to verify the opt-in photo search contract."""

    def __init__(self, *, photos_enabled: bool, photos_daily_limit: int = 10):
        self._api_key = "test-key"
        self.enabled = True
        self.configured = True
        self.photos_enabled = bool(photos_enabled)
        self._photos_daily_limit = photos_daily_limit
        self._photos_requests_today = 0
        self._photos_last_error = None
        self._request_timeout = 25
        self.text_search_calls = 0
        self.media_calls: list[str] = []

    async def _request(self, body, *, field_mask=None):
        self.text_search_calls += 1
        assert field_mask == google_places._PHOTOS_FIELD_MASK, (
            "photo search must use the photos field mask, never the "
            "identity/address mask used for place resolution"
        )
        return {
            "places": [
                {
                    "id": "ChIJ-photo-test",
                    "displayName": {"text": "RMK Matsiranna telkimisala"},
                    "googleMapsUri": "https://maps.google.com/?cid=456",
                    "photos": [
                        {
                            "name": "places/ChIJ-photo-test/photos/ref-1",
                            "widthPx": 4032,
                            "heightPx": 3024,
                            "authorAttributions": [{"displayName": "Jane Doe"}],
                        }
                    ],
                }
            ]
        }

    async def _resolve_photo_media(self, photo_name):
        self.media_calls.append(photo_name)
        return f"https://lh3.googleusercontent.com/places/{photo_name}"


async def verify_photos_opt_in() -> None:
    # Off by default, and async_search_photos must fail closed without
    # making any network call when the opt-in flag is not set.
    disabled = FakeGooglePhotos(photos_enabled=False)
    assert disabled.photos_enabled is False
    results = await disabled.async_search_photos("RMK Matsiranna Estonia")
    assert results == []
    assert disabled.text_search_calls == 0

    enabled = FakeGooglePhotos(photos_enabled=True)
    photos = await enabled.async_search_photos("RMK Matsiranna Estonia", limit=6)
    assert enabled.text_search_calls == 1
    assert len(photos) == 1
    photo = photos[0]
    assert photo["photo_name"] == "places/ChIJ-photo-test/photos/ref-1"
    assert photo["photo_uri"].startswith("https://lh3.googleusercontent.com/")
    assert photo["author"] == "Jane Doe"
    assert photo["place_title"] == "RMK Matsiranna telkimisala"
    assert photo["source_url"] == "https://maps.google.com/?cid=456"
    assert enabled.media_calls == ["places/ChIJ-photo-test/photos/ref-1"]


class FakeGooglePhotoResolver:
    """Stands in for GooglePlacesClient.async_resolve_photo_url."""

    def __init__(self, url_by_photo_name: dict[str, str]):
        self._urls = url_by_photo_name
        self.resolve_calls: list[str] = []

    async def async_resolve_photo_url(self, photo_name: str):
        self.resolve_calls.append(photo_name)
        return self._urls.get(photo_name)


async def verify_google_photo_token_service() -> None:
    photo_name = "places/ChIJ-photo-test/photos/ref-1"
    resolver = FakeGooglePhotoResolver({photo_name: "https://lh3.googleusercontent.com/fresh-1"})
    tokens = google_photo_token_service.GooglePhotoTokenService(resolver)

    token = tokens.token(photo_name)
    assert tokens.validate_token(photo_name, token) is True, "a freshly minted token must validate"
    assert tokens.validate_token("places/other/photos/x", token) is False, (
        "a token must be bound to its exact photo_name - it must not validate for a different one"
    )
    assert tokens.validate_token(photo_name, "garbage") is False
    assert tokens.validate_token(photo_name, "9999999999.deadbeef") is False, (
        "a well-formed but wrong signature must not validate"
    )

    url = await tokens.async_redirect_url(photo_name)
    assert url == "https://lh3.googleusercontent.com/fresh-1"
    assert resolver.resolve_calls == [photo_name], (
        "each redirect must re-resolve live against the provider, never cache/reuse a stale URL"
    )

    missing_resolver = FakeGooglePhotoResolver({})
    missing_tokens = google_photo_token_service.GooglePhotoTokenService(missing_resolver)
    try:
        await missing_tokens.async_redirect_url("places/gone/photos/x")
    except Exception as err:  # noqa: BLE001 - asserting the specific contract below
        assert "nicht mehr verfügbar" in str(err)
    else:
        raise AssertionError("a photo_name the provider can no longer resolve must raise, not return None silently")


asyncio.run(verify_google_candidate())
asyncio.run(verify_composite_fallback())
asyncio.run(verify_photos_opt_in())
asyncio.run(verify_google_photo_token_service())

source = Path("custom_components/roadplanner_mcp/google_places.py").read_text(encoding="utf-8")
assert "X-Goog-Api-Key" in source
# Photo data must never be requested by the identity/address search used for
# reviewed place enrichment - only by the separate, off-by-default photo path.
assert "places.photos" not in google_places._FIELD_MASK
assert "places.photos" in google_places._PHOTOS_FIELD_MASK
assert "_api_key" not in " ".join(google_places.GooglePlacesStatus.__annotations__)
print("Google Places provider tests passed.")
