"""Trip-export photo fetch: fallbacks instead of empty videos.

Live report: a trip with 254 memories produced "no photos found". One
failing OneDrive download URL must never sink a stop (the large thumbnail
is the fallback, and further candidates are tried), and a Google-primary
gallery must fall through to its next non-Google image instead of giving
up.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_export_photos_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)


class RoadplannerError(RuntimeError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.RoadplannerError = RoadplannerError
sys.modules[roadplanner.__name__] = roadplanner

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.trip_export_photos", PACKAGE_ROOT / "trip_export_photos.py"
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.content_length = len(body)
        self.content = self

    async def read(self, _limit: int) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requested.append(url)
        body = self.responses.get(url)
        return FakeResponse(200 if body else 404, body or b"")


class FakeExperience:
    def __init__(self, urls: dict[tuple[str, str], str]) -> None:
        self.urls = urls

    async def async_media_redirect_url(self, trip_id: str, media_id: str, kind: str) -> str:
        key = (media_id, kind)
        if key not in self.urls:
            raise RoadplannerError("nicht auflösbar")
        return self.urls[key]


def verify_personal_photo_falls_back_to_thumbnail_and_next_candidate() -> None:
    # Candidate 1 (cover): original URL dead, thumbnail dead too.
    # Candidate 2: original unresolvable, thumbnail delivers.
    session = FakeSession({"https://cdn/thumb-2.jpg": b"JPEG-2"})
    experience = FakeExperience(
        {
            ("m1", "original"): "https://cdn/dead-original.jpg",
            ("m1", "thumbnail"): "https://cdn/dead-thumb.jpg",
            ("m2", "thumbnail"): "https://cdn/thumb-2.jpg",
        }
    )
    media_by_stop = {"stop-1": [{"id": "m2"}, {"id": "m1", "is_cover": True}]}
    photo = asyncio.run(
        module.async_fetch_personal_stop_photo(
            session, experience, "trip-1", "stop-1", media_by_stop
        )
    )
    assert photo == b"JPEG-2", photo
    assert session.requested[0] == "https://cdn/dead-original.jpg", (
        "the cover candidate and the original size are still tried first"
    )
    # Nothing resolvable at all -> None, never an exception.
    assert asyncio.run(
        module.async_fetch_personal_stop_photo(
            FakeSession({}), FakeExperience({}), "trip-1", "stop-1", media_by_stop
        )
    ) is None


def verify_stock_gallery_skips_google_primary_to_next_image() -> None:
    session = FakeSession({"https://commons/photo.jpg": b"COMMONS"})
    galleries = {
        "stop-1": {
            "primary_image_id": "g1",
            "images": [
                {"id": "g1", "provider": "google_places", "image_url": "https://g/x"},
                {"id": "c1", "provider": "wikimedia_commons", "image_url": "https://commons/photo.jpg"},
            ],
        }
    }
    photo = asyncio.run(
        module.async_fetch_stock_stop_photo(session, "stop-1", galleries)
    )
    assert photo == b"COMMONS", (
        "a Google-primary gallery must fall through to the next non-Google image"
    )
    assert "https://g/x" not in session.requested


if __name__ == "__main__":
    verify_personal_photo_falls_back_to_thumbnail_and_next_candidate()
    verify_stock_gallery_skips_google_primary_to_next_image()
    print("Trip export photo fallback tests passed.")
