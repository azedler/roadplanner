"""Contract tests for reviewable Roadplanner place enrichment."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_place_enrichment_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

canonical = types.ModuleType(f"{PACKAGE_NAME}.canonical_day")
canonical.canonical_day_stops = lambda day: list(day.get("stops") or [])
canonical.location_status = lambda stop: (
    "resolved"
    if isinstance(stop.get("location"), dict)
    and isinstance(stop["location"].get("latitude"), (int, float))
    and isinstance(stop["location"].get("longitude"), (int, float))
    else "missing"
)
sys.modules[canonical.__name__] = canonical


class ValidationError(RuntimeError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner


class GeocodingError(RuntimeError):
    pass


@dataclass
class GeocodingCandidate:
    display_name: str
    latitude: float
    longitude: float
    score: float = 0.95
    category: str = "amenity"
    result_type: str = "ferry_terminal"
    address: dict = field(default_factory=lambda: {
        "road": "Lootsi",
        "house_number": "13",
        "city": "Tallinn",
        "country_code": "ee",
    })
    namedetails: dict = field(default_factory=lambda: {"name": "Tallinn Terminal D"})
    extratags: dict = field(default_factory=lambda: {
        "website": "https://www.ts.ee/en/old-city-harbour/",
        "phone": "+372 631 8550",
        "email": "info@ts.ee",
        "opening_hours": "24/7",
        "wikidata": "Q123",
    })
    boundingbox: list = field(default_factory=lambda: ["59.44", "59.45", "24.75", "24.77"])
    osm_type: str = "node"
    osm_id: int = 12345
    source_url: str = "https://www.openstreetmap.org/node/12345"
    resolution_mode: str = "search"
    distance_meters: float | None = None

    @property
    def preferred_name(self) -> str:
        return str(self.namedetails.get("name") or self.display_name.split(",", 1)[0])

    def as_location(self) -> dict:
        return {
            "label": self.preferred_name,
            "address": self.display_name,
            "city": "Tallinn",
            "country_code": "EE",
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    def as_provenance(self) -> dict:
        return {
            "provider": "nominatim",
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "source_url": self.source_url,
            "score": self.score,
            "namedetails": dict(self.namedetails),
            "extratags": dict(self.extratags),
        }


geocoding = types.ModuleType(f"{PACKAGE_NAME}.geocoding")
geocoding.GeocodingCandidate = GeocodingCandidate
geocoding.GeocodingError = GeocodingError
geocoding.NominatimGeocoder = object
sys.modules[geocoding.__name__] = geocoding

images = types.ModuleType(f"{PACKAGE_NAME}.destination_images")
images.DestinationImageProvider = object
sys.modules[images.__name__] = images

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.place_enrichment",
    PACKAGE_ROOT / "place_enrichment.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeGeocoder:
    async def async_resolve(self, query, *, language):
        assert "Fährterminal Tallinn" in query
        assert language == "de"
        candidate = GeocodingCandidate(
            display_name="Tallinn Terminal D, Lootsi 13, Tallinn, Estonia",
            latitude=59.44327,
            longitude=24.76154,
        )
        return candidate, [candidate]

    async def async_reverse(self, latitude, longitude, *, language):
        return GeocodingCandidate(
            display_name="Tallinn Terminal D, Lootsi 13, Tallinn, Estonia",
            latitude=latitude,
            longitude=longitude,
            resolution_mode="reverse",
            distance_meters=15.0,
        )


class FakeImages:
    async def async_search(self, query, *, limit, latitude, longitude):
        assert "Tallinn" in query
        assert limit == 6
        assert latitude == 59.44327
        assert longitude == 24.76154
        return {
            "results": [
                {
                    "id": "img-1",
                    "provider": "wikimedia_commons",
                    "title": "Tallinn Old City Harbour",
                    "thumbnail_url": "https://example.org/thumb-1.jpg",
                    "image_url": "https://example.org/image-1.jpg",
                    "source_url": "https://commons.wikimedia.org/wiki/File:Terminal_D.jpg",
                    "author": "Example",
                    "license": "CC BY-SA 4.0",
                    "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                    "selection_score": 42.0,
                    "selection_reason": "nahe am Kartenpunkt",
                },
                {
                    "id": "img-2",
                    "provider": "openverse",
                    "title": "Terminal D Tallinn",
                    "thumbnail_url": "https://example.org/thumb-2.jpg",
                    "image_url": "https://example.org/image-2.jpg",
                    "source_url": "https://example.org/photo/2",
                    "author": "Open Author",
                    "license": "BY 4.0",
                    "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "selection_score": 38.0,
                    "selection_reason": "repräsentativer Blick",
                },
            ],
            "provider_errors": {},
        }


async def main() -> None:
    service = module.PlaceEnrichmentService(FakeGeocoder(), FakeImages())
    day = {
        "id": "day-6",
        "date": "2026-07-22",
        "title": "Tallinn und Fähre",
        "stops": [
            {
                "id": "stop-terminal",
                "name": "Fährterminal Tallinn",
                "type": "ferry",
                "position": 3,
                "location": {},
                "notes": "Tallink-Abfahrt um 19:30 Uhr",
            }
        ],
    }
    preview = await service.async_prepare(
        user_id="user-1",
        trip_id="trip-1",
        days=[day],
    )
    assert preview["stats"]["item_count"] == 1
    item = preview["items"][0]
    assert item["status"] == "resolved"
    assert item["selected_candidate_id"]
    candidate = item["candidates"][0]
    assert candidate["name"] == "Tallinn Terminal D"
    assert candidate["address"].startswith("Tallinn Terminal D")
    assert candidate["category"] == "Fährterminal"
    assert candidate["location"]["latitude"] == 59.44327
    assert candidate["website"].startswith("https://")
    assert candidate["phone"] == "+372 631 8550"
    assert candidate["opening_hours"] == "24/7"
    assert candidate["map_url"].startswith("https://www.google.com/maps/search/")
    assert candidate["source_url"].startswith("https://www.openstreetmap.org/")
    assert candidate["confidence"] >= 90
    assert len(candidate["images"]) == 2

    operations, galleries = await service.resolve_selections(
        user_id="user-1",
        trip_id="trip-1",
        preview_id=preview["id"],
        selections={"stop-terminal": candidate["id"]},
    )
    assert len(operations) == 1
    operation = operations[0]
    assert operation["action"] == "update"
    assert operation["entity_type"] == "stop"
    assert operation["entity_id"] == "stop-terminal"
    assert operation["day_id"] == "day-6"
    assert operation["changes"]["location"]["latitude"] == 59.44327
    assert operation["changes"]["location"]["longitude"] == 24.76154
    details = operation["changes"]["details"]
    assert details["place_profile"]["name"] == "Tallinn Terminal D"
    assert details["place_profile"]["website"].startswith("https://")
    assert details["place_profile"]["email"] == "info@ts.ee"
    assert details["place_profile"]["confirmed_at"]
    assert details["geocoding"]["selected_candidate_id"] == candidate["id"]
    assert len(galleries) == 1
    assert galleries[0]["status"] == "ready"
    assert galleries[0]["primary_image_id"] == "img-1"
    assert len(galleries[0]["query_fingerprint"]) == 64

    try:
        await service.resolve_selections(
            user_id="another-user",
            trip_id="trip-1",
            preview_id=preview["id"],
            selections={"stop-terminal": candidate["id"]},
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Preview ownership must be enforced")


asyncio.run(main())
print("Place enrichment tests passed.")
