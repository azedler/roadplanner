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
        self.calls: list[tuple[str, str, str]] = []

    async def async_media_redirect_url(
        self, trip_id: str, media_id: str, kind: str, *, size: str = "large"
    ) -> str:
        self.calls.append((media_id, kind, size))
        key = (media_id, kind)
        if key not in self.urls:
            raise RoadplannerError("nicht auflösbar")
        return self.urls[key]


def verify_personal_photo_falls_back_to_thumbnail_and_next_candidate() -> None:
    # THE live cause of photo-less exports: iPhone originals are HEIC and
    # Pillow cannot decode them, so the rendered JPEG preview must be tried
    # FIRST; the original is only the last resort.
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
    assert experience.calls[0] == ("m1", "thumbnail", "c1920x1440"), (
        f"{experience.calls[0]}: the cover candidate's rendered JPEG preview "
        "comes first - the HEIC original last"
    )
    assert ("m1", "original", "large") in experience.calls, (
        "the original stays as the last resort"
    )
    assert experience.calls.index(("m1", "thumbnail", "large")) < experience.calls.index(
        ("m1", "original", "large")
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


def verify_day_linked_photos_fill_the_remaining_slots() -> None:
    # THE live cause of "Für diese Reise wurden keine Fotos für das Video
    # gefunden" on a trip with 255 memories: photos assigned to a DAY but
    # to no stop were invisible to both exporters.
    session = FakeSession(
        {"https://cdn/day-1.jpg": b"DAY1", "https://cdn/day-2.jpg": b"DAY2"}
    )
    experience = FakeExperience(
        {
            ("d1", "original"): "https://cdn/day-1.jpg",
            ("d2", "original"): "https://cdn/day-2.jpg",
        }
    )
    stops = [{"id": "stop-1"}]
    photos = asyncio.run(
        module.async_fetch_day_photos(
            session,
            experience,
            "trip-1",
            stops,
            {},  # no stop-linked media at all
            {},  # no planning galleries
            max_photos=2,
            day_media=[{"id": "d1", "is_cover": True}, {"id": "d2"}],
        )
    )
    assert photos == [b"DAY1", b"DAY2"], photos

    # A photo already used through its stop is not repeated as day media.
    session = FakeSession({"https://cdn/stop.jpg": b"STOP", "https://cdn/day-2.jpg": b"DAY2"})
    experience = FakeExperience(
        {
            ("s1", "original"): "https://cdn/stop.jpg",
            ("d2", "original"): "https://cdn/day-2.jpg",
        }
    )
    photos = asyncio.run(
        module.async_fetch_day_photos(
            session,
            experience,
            "trip-1",
            [{"id": "stop-1"}],
            {"stop-1": [{"id": "s1"}]},
            {},
            max_photos=3,
            day_media=[{"id": "s1"}, {"id": "d2"}],
        )
    )
    assert photos == [b"STOP", b"DAY2"], photos


def verify_heic_is_named_as_the_undecodable_format() -> None:
    # iPhone originals are HEIC; Pillow has no HEIC support, so the photo
    # downloaded fine and was then silently dropped. The hint must name it.
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "trip_pdf_hint", Path("custom_components/roadplanner_mcp/trip_pdf.py")
    )
    pdf = importlib.util.module_from_spec(spec)
    sys.modules["trip_pdf_hint"] = pdf
    spec.loader.exec_module(pdf)
    heic = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00"
    assert "HEIC" in pdf._image_format_hint(heic)
    assert pdf._image_format_hint(b"\xff\xd8\xff\xe0abcdefghij") == "JPEG"
    assert pdf._image_format_hint(b"\x89PNG\r\n\x1a\nabcdefgh") == "PNG"
    assert pdf._decode_photo(heic) is None, "an undecodable photo never raises"


def verify_crew_reference_picker_ui_contract() -> None:
    # Live report: the picker grid rendered as ragged full-height columns
    # and the selection was not recognizable.
    styles = Path("custom_components/roadplanner_mcp/frontend/lib/styles.js").read_text(encoding="utf-8")
    assert "aspect-ratio" not in styles.split(".crew-photo-choice {")[1].split("}")[0], (
        "Safari/WebView ignores aspect-ratio on <button> - the tile needs a "
        "fixed height instead"
    )
    assert ".crew-photo-choice.selected::after" in styles, "the selection needs a visible badge"
    crew_ui = Path("custom_components/roadplanner_mcp/frontend/features/crew.js").read_text(encoding="utf-8")
    assert "_setCrewReferencePhoto" in crew_ui
    assert "data-crew-photo-current" in crew_ui, "the current selection is shown as a preview"
    assert 'data-thumb-url=' in crew_ui


if __name__ == "__main__":
    verify_personal_photo_falls_back_to_thumbnail_and_next_candidate()
    verify_stock_gallery_skips_google_primary_to_next_image()
    verify_day_linked_photos_fill_the_remaining_slots()
    verify_heic_is_named_as_the_undecodable_format()
    verify_crew_reference_picker_ui_contract()
    print("Trip export photo fallback tests passed.")
