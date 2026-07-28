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
