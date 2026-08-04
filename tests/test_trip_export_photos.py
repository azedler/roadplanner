"""Trip-export photo fetch: fallbacks instead of empty videos.

Live report: a trip with 254 memories produced "no photos found". One
failing OneDrive download URL must never sink a stop (the large thumbnail
is the fallback, and further candidates are tried), and a Google-primary
gallery must fall through to its next non-Google image instead of giving
up.
"""
from __future__ import annotations

import asyncio
import io
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

cache_spec = spec_from_file_location(
    f"{PACKAGE_NAME}.media_cache", PACKAGE_ROOT / "media_cache.py"
)
assert cache_spec and cache_spec.loader
cache_module = module_from_spec(cache_spec)
sys.modules[cache_spec.name] = cache_module
cache_spec.loader.exec_module(cache_module)

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

    async def iter_chunked(self, _size: int):
        # aiohttp streams in chunks; a single read() returns only the first
        # one, which used to truncate every photo silently.
        for start in range(0, len(self._body), 512):
            yield self._body[start:start + 512]

    async def read(self, _limit: int = -1) -> bytes:
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


def jpeg(marker: bytes) -> bytes:
    """Bytes that actually look like a JPEG.

    The exporters now refuse a download whose format no renderer can open
    (an iPhone HEIC original), so a fixture must carry a real JPEG header.
    """
    return b"\xff\xd8\xff\xe0" + marker.ljust(16, b"\x00")


def verify_personal_photo_falls_back_to_thumbnail_and_next_candidate() -> None:
    # THE live cause of photo-less exports: iPhone originals are HEIC and
    # Pillow cannot decode them, so the rendered JPEG preview must be tried
    # FIRST; the original is only the last resort.
    session = FakeSession({"https://cdn/thumb-2.jpg": jpeg(b"JPEG-2")})
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
    assert photo == jpeg(b"JPEG-2"), photo
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


class RecordingCache:
    """Minimal stand-in for MediaCache with real key semantics."""

    enabled = True

    def __init__(self, seed: dict[str, bytes] | None = None) -> None:
        self.store: dict[str, bytes] = dict(seed or {})

    def read(self, key: str) -> bytes | None:
        return self.store.get(key)

    def write(self, key: str, data: bytes) -> None:
        self.store[key] = data


class ImmediateHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def verify_a_cached_heic_can_never_poison_the_thumbnail_request() -> None:
    """The live cause of exports that stayed empty AFTER the HEIC fix.

    An iPhone original cached earlier under size "large" was served to the
    rendered-thumbnail request, which uses the same size name - so the
    export got HEIC bytes again and every frame failed to decode ("Für
    diese Reise wurden keine Fotos für das Video gefunden", 261 memories).
    """
    heic = b"\x00\x00\x00\x18ftypheic" + b"x" * 512
    poisoned = RecordingCache(
        {cache_module.cache_key("m1", "item-1", "large"): heic}
    )
    session = FakeSession({"https://cdn/thumb.jpg": jpeg(b"GOOD")})
    experience = FakeExperience({("m1", "thumbnail"): "https://cdn/thumb.jpg"})
    photo = asyncio.run(
        module.async_fetch_media_photo(
            session,
            experience,
            "trip-1",
            {"id": "m1", "provider_item_id": "item-1"},
            cache=poisoned,
            hass=ImmediateHass(),
        )
    )
    assert photo == jpeg(b"GOOD"), photo

    # A HEIC that arrives fresh is skipped rather than handed on.
    only_heic = FakeSession({"https://cdn/thumb.jpg": heic, "https://cdn/orig": heic})
    heic_everywhere = FakeExperience(
        {("m1", "thumbnail"): "https://cdn/thumb.jpg", ("m1", "original"): "https://cdn/orig"}
    )
    assert (
        asyncio.run(
            module.async_fetch_media_photo(
                only_heic,
                heic_everywhere,
                "trip-1",
                {"id": "m1", "provider_item_id": "item-1"},
                cache=RecordingCache(),
                hass=ImmediateHass(),
            )
        )
        is None
    )
    assert "HEIC" in module.LAST_PHOTO_ERROR.get("reason", "")


def verify_stock_gallery_skips_google_primary_to_next_image() -> None:
    session = FakeSession({"https://commons/photo.jpg": jpeg(b"COMMONS")})
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
    assert photo == jpeg(b"COMMONS"), (
        "a Google-primary gallery must fall through to the next non-Google image"
    )
    assert "https://g/x" not in session.requested


def verify_every_source_is_checked_not_just_personal_media() -> None:
    """Gallery images go through the same format gate as personal photos.

    Live report: "8 Fotos und 0 Kartenbilder wurden geladen, aber keines
    davon ließ sich als Bild öffnen." Those eight came from planning
    galleries, which downloaded straight past the format check - they
    counted as loaded photos while no renderer could open one of them.
    """
    heic = b"\x00\x00\x00\x18ftypheic" + b"x" * 512
    session = FakeSession({"https://cdn/gallery.heic": heic})
    galleries = {
        "stop-1": {"images": [{"id": "g1", "image_url": "https://cdn/gallery.heic"}]}
    }
    assert (
        asyncio.run(module.async_fetch_stock_stop_photo(session, "stop-1", galleries))
        is None
    ), "an unopenable gallery image must not be reported as a loaded photo"
    assert "HEIC" in module.LAST_PHOTO_ERROR.get("reason", "")

    # The format is named, so the message points at the actual problem.
    assert module._format_name(b"<!DOCTYPE html><html>" + b"x" * 200) == (
        "HTML-Seite statt Bild"
    )
    assert module._format_name(b"\x00\x00\x00\x18ftypavif" + b"x" * 20) == "AVIF"
    assert module._format_name(jpeg(b"X")) == "JPEG"


def verify_day_linked_photos_fill_the_remaining_slots() -> None:
    # THE live cause of "Für diese Reise wurden keine Fotos für das Video
    # gefunden" on a trip with 255 memories: photos assigned to a DAY but
    # to no stop were invisible to both exporters.
    session = FakeSession(
        {"https://cdn/day-1.jpg": jpeg(b"DAY1"), "https://cdn/day-2.jpg": jpeg(b"DAY2")}
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
    assert photos == [jpeg(b"DAY1"), jpeg(b"DAY2")], photos

    # A photo already used through its stop is not repeated as day media.
    session = FakeSession({"https://cdn/stop.jpg": jpeg(b"STOP"), "https://cdn/day-2.jpg": jpeg(b"DAY2")})
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
    assert photos == [jpeg(b"STOP"), jpeg(b"DAY2")], photos


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


def verify_crop_photo_cuts_the_face_region() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (10, 20, 30)).save(buffer, "JPEG")
    original = buffer.getvalue()
    cropped = module.crop_photo(original, {"x": 0.25, "y": 0.5, "w": 0.5, "h": 0.5})
    assert cropped != original
    assert Image.open(io.BytesIO(cropped)).size == (200, 150)
    # Fail-open: no crop, junk crop, or a degenerate box keeps the photo.
    assert module.crop_photo(original, None) is original
    assert module.crop_photo(original, {"x": 0, "y": 0, "w": 0.001, "h": 0.5}) is original
    assert module.crop_photo(original, {"x": "a", "y": 0, "w": 1, "h": 1}) is original
    assert module.crop_photo(b"kein bild", {"x": 0, "y": 0, "w": 1, "h": 1}) == b"kein bild"


def verify_crew_picker_pagination_and_crop_ui() -> None:
    crew_ui = Path("custom_components/roadplanner_mcp/frontend/features/crew.js").read_text(encoding="utf-8")
    # Live request: not all photos visible, zoom, and a crop for group shots.
    assert "CREW_PHOTO_PAGE_SIZE" in crew_ui
    assert "_showCrewPhotoPage" in crew_ui and "data-crew-photo-pager-label" in crew_ui
    assert "_applyCrewCropTap" in crew_ui and "data-crew-crop-frame" in crew_ui
    assert 'name="reference_crop"' in crew_ui
    panel_ui = Path("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js").read_text(encoding="utf-8")
    for action in ("crew-photo-prev", "crew-photo-next", "crew-crop-reset"):
        assert f'"{action}"' in panel_ui, action
    assert "crew-crop-size" in panel_ui
    # The crop must be MOVABLE, not only resizable (live request): pointer
    # events drag it across the photo.
    assert 'addEventListener("pointerdown"' in panel_ui
    assert 'addEventListener("pointermove"' in panel_ui
    assert "this._crewCropFrame" in panel_ui
    styles = Path("custom_components/roadplanner_mcp/frontend/lib/styles.js").read_text(encoding="utf-8")
    crop_frame_css = styles.split(".crew-crop-frame {")[1].split("}")[0]
    assert "touch-action: none" in crop_frame_css, "dragging must not scroll the dialog"
    crop_img_css = styles.split(".crew-crop-frame img {")[1].split("}")[0]
    assert "object-fit" not in crop_img_css, (
        "a letterboxed image would put the crop box next to the pixels it "
        "claims to select - the frame must map 1:1 onto the photo"
    )
    export_source = Path("custom_components/roadplanner_mcp/trip_pdf_export.py").read_text(encoding="utf-8")
    assert 'crop_photo(member.photo, reference.get("crop"))' in export_source, (
        "the chosen face region must be applied to the portrait"
    )


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
    verify_a_cached_heic_can_never_poison_the_thumbnail_request()
    verify_stock_gallery_skips_google_primary_to_next_image()
    verify_every_source_is_checked_not_just_personal_media()
    verify_day_linked_photos_fill_the_remaining_slots()
    verify_heic_is_named_as_the_undecodable_format()
    verify_crop_photo_cuts_the_face_region()
    verify_crew_picker_pagination_and_crop_ui()
    verify_crew_reference_picker_ui_contract()
    print("Trip export photo fallback tests passed.")
