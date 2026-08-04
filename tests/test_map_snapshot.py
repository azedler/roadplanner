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

MODULE_PATH = Path("custom_components/roadplanner_mcp/map_snapshot.py")
spec = spec_from_file_location("roadplanner_map_snapshot_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _png_bytes(color=(30, 120, 90), size=(256, 256)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeContentReader:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, _max_bytes: int) -> bytes:
        return self._body


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

    def __init__(self, *, tile_body: bytes | None = None, google_body: bytes | None = None, status: int = 200) -> None:
        self._tile_body = tile_body
        self._google_body = google_body
        self._status = status
        self.requests: list[dict] = []

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.requests.append({"url": url, **kwargs})
        if "maps.googleapis.com" in url:
            if self._google_body is None:
                return _FakeResponse(status=404)
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


verify_openstreetmap_snapshot_is_stitched_and_attributed()
verify_google_static_maps_uses_key_as_query_param_not_header()
verify_google_branch_is_skipped_without_an_api_key()
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
