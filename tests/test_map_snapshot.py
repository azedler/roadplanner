"""Behavioral tests for the trip video export's map snapshot providers.

Both backends (OpenStreetMap tile-stitching, Google Static Maps) are
exercised against a real Pillow-generated fake tile/image and a fake
aiohttp session - no real network access, no real Home Assistant.
"""
from __future__ import annotations

import asyncio
import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_map_snapshot_test"
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


load("http_read")
module = load("map_snapshot")


def _png_bytes(color=(30, 120, 90), size=(256, 256)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeContentReader:
    """Streams the body in chunks, exactly like aiohttp does.

    The old fake returned the whole body from a single read() call, which
    hid the bug this fake now reproduces: aiohttp's read(n) returns only
    what is buffered, so a body arriving in several chunks was silently
    truncated.
    """

    def __init__(self, body: bytes, *, chunk_size: int = 512) -> None:
        self._body = body
        self._chunk_size = chunk_size

    async def iter_chunked(self, _size: int):
        for start in range(0, len(self._body), self._chunk_size):
            yield self._body[start:start + self._chunk_size]

    async def read(self, max_bytes: int = -1) -> bytes:
        # aiohttp semantics: at most ONE buffered chunk, never the rest.
        return self._body[: self._chunk_size if max_bytes < 0 else min(max_bytes, self._chunk_size)]


class _FakeResponse:
    def __init__(self, *, status: int = 200, body: bytes = b"", content_length: int | None = None) -> None:
        self.status = status
        self.content_length = content_length if content_length is not None else len(body)
        self.content = _FakeContentReader(body)

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _FakeSession:
    """Fake aiohttp session recording every request's url/params/headers."""

    def __init__(
        self,
        *,
        tile_body: bytes | None = None,
        google_body: bytes | None = None,
        status: int = 200,
        error_body: bytes = b"",
    ) -> None:
        self._tile_body = tile_body
        self._google_body = google_body
        self._status = status
        self._error_body = error_body
        self.requests: list[dict] = []

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        if "maps.googleapis.com" in url:
            if self._google_body is None:
                return _FakeResponse(status=404, body=self._error_body)
            return _FakeResponse(status=self._status, body=self._google_body)
        if self._tile_body is None:
            return _FakeResponse(status=404)
        return _FakeResponse(status=self._status, body=self._tile_body)


def verify_openstreetmap_snapshot_is_stitched_and_attributed() -> None:
    async def scenario() -> None:
        session = _FakeSession(tile_body=_png_bytes())
        result = await module.async_fetch_snapshot(
            session,
            "openstreetmap",
            None,
            center_lat=61.759,
            center_lon=28.878,
            markers=[(61.759, 28.878)],
            zoom=8,
            width_px=640,
            height_px=360,
        )
        assert result is not None
        assert result.startswith(b"\x89PNG")
        assert session.requests, "expected at least one OSM tile request"
        for request in session.requests:
            assert "tile.openstreetmap.org" in request["url"]
            assert request["headers"]["User-Agent"]

    asyncio.run(scenario())


def verify_google_static_maps_uses_key_as_query_param_not_header() -> None:
    async def scenario() -> None:
        session = _FakeSession(google_body=_png_bytes(size=(640, 360)))
        result = await module.async_fetch_snapshot(
            session,
            "google_static_maps",
            "test-api-key",
            center_lat=61.759,
            center_lon=28.878,
            markers=[(61.759, 28.878)],
            zoom=8,
            width_px=640,
            height_px=360,
        )
        assert result is not None
        assert result.startswith(b"\x89PNG")
        assert len(session.requests) == 1
        request = session.requests[0]
        assert "maps.googleapis.com/maps/api/staticmap" in request["url"]
        assert request["params"]["key"] == "test-api-key"
        assert "headers" not in request or "key" not in (request.get("headers") or {})

    asyncio.run(scenario())


def verify_google_branch_is_skipped_without_an_api_key() -> None:
    async def scenario() -> None:
        session = _FakeSession(tile_body=_png_bytes(), google_body=_png_bytes())
        result = await module.async_fetch_snapshot(
            session,
            "google_static_maps",
            None,
            center_lat=61.759,
            center_lon=28.878,
            zoom=8,
            width_px=640,
            height_px=360,
        )
        assert result is not None
        assert all("maps.googleapis.com" not in r["url"] for r in session.requests), (
            "without an API key the OSM fallback path must be used, never a keyless Google call"
        )

    asyncio.run(scenario())


def verify_http_error_returns_none_gracefully() -> None:
    async def scenario() -> None:
        session = _FakeSession(tile_body=_png_bytes(), status=500)
        result = await module.async_fetch_snapshot(
            session,
            "openstreetmap",
            None,
            center_lat=61.759,
            center_lon=28.878,
            zoom=8,
            width_px=640,
            height_px=360,
        )
        assert result is None

    asyncio.run(scenario())


def verify_malformed_tile_bytes_return_none_gracefully() -> None:
    async def scenario() -> None:
        session = _FakeSession(tile_body=b"not-a-real-tile")
        result = await module.async_fetch_snapshot(
            session,
            "openstreetmap",
            None,
            center_lat=61.759,
            center_lon=28.878,
            zoom=8,
            width_px=640,
            height_px=360,
        )
        assert result is None

    asyncio.run(scenario())


def verify_tile_math_is_within_valid_bounds_at_a_known_coordinate() -> None:
    # Rovaniemi, Finland (Arctic Circle) at zoom 8 - a known, stable reference
    # point; regression guard against a sign/axis error in the Mercator math.
    x, y = module._lonlat_to_global_pixel(66.5039, 25.7294, 8)
    tile_size = module._OSM_TILE_SIZE
    max_pixel = tile_size * (2**8)
    assert 0 <= x <= max_pixel
    assert 0 <= y <= max_pixel


def verify_a_rejected_google_request_reports_googles_own_reason() -> None:
    """Live Systemcheck: "kein Kartenbild erhalten" was the whole diagnosis.

    Google states the actual cause in the response body, in plain language.
    Reporting only "no image" threw away the one sentence that says what to
    do about it.
    """
    async def scenario() -> None:
        module.LAST_SNAPSHOT_ERROR.clear()
        session = _FakeSession(
            tile_body=_png_bytes(),
            google_body=None,
            error_body=(
                b"<html><body><h1>Error</h1><p>The Google Maps Platform server "
                b"rejected your request. This API project is not authorized to "
                b"use this API.</p></body></html>"
            ),
        )
        result = await module.async_fetch_snapshot(
            session, "google_static_maps", "key-123",
            center_lat=59.3, center_lon=15.2, zoom=6, width_px=320, height_px=240,
        )
        assert result is not None, "a rejected Google key must fall back to OSM"
        reason = module.LAST_SNAPSHOT_ERROR.get("reason", "")
        assert "404" in reason, reason
        assert "not authorized to use this API" in reason, reason
        assert "<" not in reason, "the markup must be stripped, the sentence kept"

    asyncio.run(scenario())


def verify_a_200_that_is_not_an_image_is_not_accepted_as_a_map() -> None:
    """A status code is not proof of a map."""
    async def scenario() -> None:
        module.LAST_SNAPSHOT_ERROR.clear()
        session = _FakeSession(
            google_body=b"<html><body>Sorry, the API key is invalid.</body></html>"
        )
        # No tiles either, so the fallback cannot mask the rejection here.
        result = await module.async_fetch_snapshot(
            session, "google_static_maps", "key-123",
            center_lat=59.3, center_lon=15.2, zoom=6, width_px=320, height_px=240,
        )
        assert result is None, "an HTML error page must never pass as a map image"
        assert "API key is invalid" in module.LAST_SNAPSHOT_ERROR.get("reason", "")

    asyncio.run(scenario())


def verify_a_rejected_google_key_falls_back_to_openstreetmap() -> None:
    """Live Systemcheck: Google said "This API is not activated on your API
    project" while the OpenStreetMap probe was green - and the PDF's route
    page came out without any map. A working free tile server is right
    there; an unactivated API is a Google Cloud setting nobody can fix from
    inside Home Assistant.
    """
    async def scenario() -> None:
        module.LAST_SNAPSHOT_ERROR.clear()
        session = _FakeSession(tile_body=_png_bytes(), google_body=None, error_body=b"nope")
        result = await module.async_fetch_snapshot(
            session, "google_static_maps", "key-123",
            center_lat=59.3, center_lon=15.2, zoom=6, width_px=320, height_px=240,
        )
        assert result is not None and result.startswith(b"\x89PNG")
        assert any("tile.openstreetmap.org" in r["url"] for r in session.requests)
        assert any("maps.googleapis.com" in r["url"] for r in session.requests), (
            "the configured provider must still be tried FIRST"
        )

    asyncio.run(scenario())


def verify_both_backends_failing_names_both_causes() -> None:
    async def scenario() -> None:
        module.LAST_SNAPSHOT_ERROR.clear()
        session = _FakeSession(tile_body=None, google_body=None, error_body=b"not authorized")
        result = await module.async_fetch_snapshot(
            session, "google_static_maps", "key-123",
            center_lat=59.3, center_lon=15.2, zoom=6, width_px=320, height_px=240,
        )
        assert result is None
        reason = module.LAST_SNAPSHOT_ERROR.get("reason", "")
        assert "not authorized" in reason, reason
        assert "OpenStreetMap" in reason, reason

    asyncio.run(scenario())


verify_openstreetmap_snapshot_is_stitched_and_attributed()
verify_google_static_maps_uses_key_as_query_param_not_header()
verify_google_branch_is_skipped_without_an_api_key()
verify_a_rejected_google_request_reports_googles_own_reason()
verify_a_200_that_is_not_an_image_is_not_accepted_as_a_map()
verify_a_rejected_google_key_falls_back_to_openstreetmap()
verify_both_backends_failing_names_both_causes()
verify_http_error_returns_none_gracefully()
verify_malformed_tile_bytes_return_none_gracefully()
verify_tile_math_is_within_valid_bounds_at_a_known_coordinate()

print("Map snapshot tests passed.")


def verify_fit_center_zoom_frames_all_points() -> None:
    # The PDF route page must frame the WHOLE trip - a schematic zigzag says
    # nothing about where it went (live report "Karte macht keinen Sinn").
    nordic_trip = [(51.05, 14.08), (63.10, 18.50), (60.17, 24.94), (54.69, 25.28)]
    lat, lon, zoom = module.fit_center_zoom(
        nordic_trip, width_px=1000, height_px=720
    )
    assert 51.0 < lat < 64.0 and 14.0 < lon < 26.0, (lat, lon)
    assert 1 <= zoom <= 6, zoom
    # Every point must fall inside the rendered frame at that zoom.
    pixels = [module._lonlat_to_global_pixel(a, b, zoom) for a, b in nordic_trip]
    assert max(x for x, _ in pixels) - min(x for x, _ in pixels) <= 1000
    assert max(y for _, y in pixels) - min(y for _, y in pixels) <= 720
    # A tight cluster gets a much closer zoom than a continent-wide trip.
    _, _, close = module.fit_center_zoom(
        [(60.10, 24.90), (60.12, 24.95)], width_px=1000, height_px=720
    )
    assert close > zoom


verify_fit_center_zoom_frames_all_points()
print("Map snapshot fit tests passed.")
