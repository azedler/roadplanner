"""Static map snapshot rendering for the trip video export's chapter openers.

These are plain, non-interactive images ("where we are right now"), not the
live interactive map used elsewhere in the panel. Two backends:

- ``openstreetmap``: no API key needed. A handful of raster tiles are
  stitched together with Pillow and a mandatory "(c) OpenStreetMap
  contributors" attribution is burned into the image, per OSM's tile usage
  policy.
- ``google_static_maps``: reuses the already-configured Google Places API
  key, passed as a plain query parameter (the same idiom Google Places'
  photo-media endpoint uses in google_places.py, not the request-header
  style used for Places Text Search).

Both backends are best-effort and defensive: any HTTP error, timeout, or
malformed/undecodable response returns ``None`` rather than raising, the
same contract trip_export_photos.py already uses for photo downloads - a
missing map snapshot just means that chapter opens without one.
"""

from __future__ import annotations

import io
import logging
import math
from typing import Any

from aiohttp import ClientError, ClientTimeout

_LOGGER = logging.getLogger(__name__)

MAX_SNAPSHOT_BYTES = 3 * 1024 * 1024
SNAPSHOT_FETCH_TIMEOUT = ClientTimeout(total=8)

_OSM_TILE_SIZE = 256
_OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
_OSM_USER_AGENT = "Roadplanner-HomeAssistant/1.0 (+https://github.com/azedler/roadplanner)"
_OSM_ATTRIBUTION = "© OpenStreetMap contributors"
_OSM_MAX_ZOOM = 19

_GOOGLE_STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
# Google's classic (non-premium) Static Maps size ceiling per side.
_GOOGLE_MAX_SIZE_PX = 640


def _lonlat_to_global_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Standard Web Mercator projection: lat/lon -> global pixel coordinates."""
    lat = max(-85.05112878, min(85.05112878, lat))
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n * _OSM_TILE_SIZE
    y = (
        (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi)
        / 2.0
        * n
        * _OSM_TILE_SIZE
    )
    return x, y


def fit_center_zoom(
    points: list[tuple[float, float]],
    *,
    width_px: int,
    height_px: int,
    max_zoom: int = 12,
    padding_px: int = 48,
) -> tuple[float, float, int]:
    """Return (center_lat, center_lon, zoom) covering all ``points``.

    Used by the PDF route page: a schematic zigzag says nothing about where
    a trip actually went (live report "Karte macht keinen Sinn"), so the
    real map has to frame the whole route by itself.
    """
    latitudes = [point[0] for point in points]
    longitudes = [point[1] for point in points]
    center_lat = (max(latitudes) + min(latitudes)) / 2
    center_lon = (max(longitudes) + min(longitudes)) / 2
    usable_w = max(64, width_px - padding_px)
    usable_h = max(64, height_px - padding_px)
    for zoom in range(max_zoom, 0, -1):
        corners = [_lonlat_to_global_pixel(lat, lon, zoom) for lat, lon in points]
        span_x = max(x for x, _ in corners) - min(x for x, _ in corners)
        span_y = max(y for _, y in corners) - min(y for _, y in corners)
        if span_x <= usable_w and span_y <= usable_h:
            return center_lat, center_lon, zoom
    return center_lat, center_lon, 1


async def async_fetch_snapshot(
    session: Any,
    provider: str,
    api_key: str | None,
    *,
    center_lat: float,
    center_lon: float,
    markers: list[tuple[float, float]] | None = None,
    zoom: int = 9,
    width_px: int = 1280,
    height_px: int = 720,
) -> bytes | None:
    """Return a static map snapshot as PNG/JPEG bytes, or ``None`` on failure."""
    try:
        if provider == "google_static_maps" and api_key:
            return await _async_fetch_google_static_map(
                session,
                api_key,
                center_lat=center_lat,
                center_lon=center_lon,
                markers=markers or [],
                zoom=zoom,
                width_px=width_px,
                height_px=height_px,
            )
        return await _async_fetch_osm_snapshot(
            session,
            center_lat=center_lat,
            center_lon=center_lon,
            markers=markers or [],
            zoom=zoom,
            width_px=width_px,
            height_px=height_px,
        )
    except Exception as err:  # noqa: BLE001 - a map snapshot must never abort the export
        _LOGGER.debug("Map snapshot fetch failed: %s", type(err).__name__)
        return None


async def _async_fetch_google_static_map(
    session: Any,
    api_key: str,
    *,
    center_lat: float,
    center_lon: float,
    markers: list[tuple[float, float]],
    zoom: int,
    width_px: int,
    height_px: int,
) -> bytes | None:
    size_w = min(width_px, _GOOGLE_MAX_SIZE_PX)
    size_h = min(height_px, _GOOGLE_MAX_SIZE_PX)
    params = {
        "key": api_key,
        "center": f"{center_lat},{center_lon}",
        "zoom": str(zoom),
        "size": f"{size_w}x{size_h}",
        "maptype": "roadmap",
    }
    if markers:
        params["markers"] = "|".join(f"{lat},{lon}" for lat, lon in markers[:10])
    try:
        async with session.get(
            _GOOGLE_STATIC_MAPS_URL, params=params, timeout=SNAPSHOT_FETCH_TIMEOUT
        ) as response:
            if response.status != 200:
                return None
            if (
                response.content_length is not None
                and response.content_length > MAX_SNAPSHOT_BYTES
            ):
                return None
            body = await response.content.read(MAX_SNAPSHOT_BYTES + 1)
            if len(body) > MAX_SNAPSHOT_BYTES:
                return None
            return body
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("Google Static Maps fetch failed: %s", type(err).__name__)
        return None


async def _async_fetch_osm_snapshot(
    session: Any,
    *,
    center_lat: float,
    center_lon: float,
    markers: list[tuple[float, float]],
    zoom: int,
    width_px: int,
    height_px: int,
) -> bytes | None:
    from PIL import Image, ImageDraw, ImageFont

    zoom = max(0, min(zoom, _OSM_MAX_ZOOM))
    center_px_x, center_px_y = _lonlat_to_global_pixel(center_lat, center_lon, zoom)
    origin_x = center_px_x - width_px / 2
    origin_y = center_px_y - height_px / 2

    first_tile_x = int(origin_x // _OSM_TILE_SIZE)
    first_tile_y = int(origin_y // _OSM_TILE_SIZE)
    last_tile_x = int((origin_x + width_px) // _OSM_TILE_SIZE)
    last_tile_y = int((origin_y + height_px) // _OSM_TILE_SIZE)

    canvas = Image.new("RGB", (width_px, height_px), color=(230, 230, 230))
    tile_count = (last_tile_x - first_tile_x + 1) * (last_tile_y - first_tile_y + 1)
    if tile_count > 64:
        # A sane upper bound - this should never happen at the zoom levels
        # this module is expected to be called with, but a corrupt/extreme
        # zoom value must not turn into an unbounded download burst.
        return None

    n = 2**zoom
    any_tile_fetched = False
    for tile_x in range(first_tile_x, last_tile_x + 1):
        for tile_y in range(first_tile_y, last_tile_y + 1):
            if tile_y < 0 or tile_y >= n:
                continue
            wrapped_x = tile_x % n
            tile_bytes = await _async_fetch_osm_tile(session, zoom, wrapped_x, tile_y)
            if tile_bytes is None:
                continue
            try:
                tile_image = Image.open(io.BytesIO(tile_bytes)).convert("RGB")
                tile_image.load()
            except Exception:  # noqa: BLE001 - a corrupt tile must not abort the snapshot
                continue
            any_tile_fetched = True
            paste_x = int(tile_x * _OSM_TILE_SIZE - origin_x)
            paste_y = int(tile_y * _OSM_TILE_SIZE - origin_y)
            canvas.paste(tile_image, (paste_x, paste_y))

    if not any_tile_fetched:
        return None

    draw = ImageDraw.Draw(canvas)
    for lat, lon in markers[:10]:
        marker_px_x, marker_px_y = _lonlat_to_global_pixel(lat, lon, zoom)
        x = marker_px_x - origin_x
        y = marker_px_y - origin_y
        radius = 7
        draw.ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(224, 122, 63),
            outline=(255, 255, 255),
            width=2,
        )

    attribution_font = ImageFont.load_default()
    text_bbox = draw.textbbox((0, 0), _OSM_ATTRIBUTION, font=attribution_font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    pad = 4
    bar_y = height_px - text_h - pad * 2
    draw.rectangle(
        (0, bar_y, text_w + pad * 2, height_px), fill=(0, 0, 0, 160)
    )
    draw.text((pad, bar_y + pad // 2), _OSM_ATTRIBUTION, fill=(255, 255, 255), font=attribution_font)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue()


async def _async_fetch_osm_tile(session: Any, zoom: int, x: int, y: int) -> bytes | None:
    url = _OSM_TILE_URL.format(z=zoom, x=x, y=y)
    try:
        async with session.get(
            url,
            timeout=SNAPSHOT_FETCH_TIMEOUT,
            headers={"User-Agent": _OSM_USER_AGENT},
        ) as response:
            if response.status != 200:
                return None
            body = await response.content.read(1024 * 1024)
            return body
    except (ClientError, TimeoutError) as err:
        _LOGGER.debug("OpenStreetMap tile fetch failed: %s", type(err).__name__)
        return None
