"""Shared personal-photo-first, stock-photo-fallback fetch logic.

Both the trip PDF export (trip_pdf_export.py) and the trip video export
(trip_video_export.py) need the exact same photo-selection priority: a
stop's own real photo (OneDrive-synced or manually uploaded, the same
personal media shown in "Erinnerungen") always wins over the stock
destination-gallery image, which is only a fallback for a stop with no
personal photo of its own. Keeping this logic in one place means the two
exporters can never silently drift apart on this behavior.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

from aiohttp import ClientError, ClientTimeout

from .media_cache import cache_key, is_decodable_image
from .media_intelligence import media_quality_score
from .roadplanner import RoadplannerError

_LOGGER = logging.getLogger(__name__)

# Modern phone photos routinely exceed 6 MB - the old cap silently dropped
# them, which is why a trip with 255 memories produced photo-less exports.
MAX_PHOTO_BYTES = 30 * 1024 * 1024
PHOTO_FETCH_TIMEOUT = ClientTimeout(total=25)

# Last concrete download failure, surfaced in the export's error message so
# a photo-less export names its cause instead of just "keine Fotos".
LAST_PHOTO_ERROR: dict[str, str] = {}


def _record_photo_error(reason: str) -> None:
    LAST_PHOTO_ERROR["reason"] = reason[:300]
    _LOGGER.warning("Trip export photo download failed: %s", reason[:300])


async def async_fetch_day_photos(
    session: Any,
    experience: Any,
    trip_id: str,
    stops: list[dict[str, Any]],
    media_by_stop: dict[str, list[dict[str, Any]]],
    destination_galleries: dict[str, Any],
    *,
    max_photos: int,
    day_media: list[dict[str, Any]] | None = None,
    cache: Any = None,
    hass: Any = None,
) -> list[bytes]:
    """Best-effort download of up to ``max_photos`` photos for one day.

    Priority per stop: the stop's own personal photo, else its planning
    gallery image. Photos that are only linked to the DAY (not to any
    stop) are used to fill the remaining slots - live report: a trip with
    255 memories produced "keine Fotos gefunden" because every exporter
    looked exclusively at stop-linked media.

    Only a plain, directly fetchable HTTPS image URL is ever downloaded
    (Wikimedia Commons/Openverse for stock images; a temporary Microsoft
    Graph download URL for personal media). Google Places photos resolve to
    a redirect meant for the browser session, not a server-side background
    job, so they are deliberately skipped. A missing or failed photo is
    simply omitted, never an error.
    """
    photos: list[bytes] = []
    for stop in stops:
        if len(photos) >= max_photos:
            break
        photo = await async_fetch_personal_stop_photo(
            session,
            experience,
            trip_id,
            str(stop.get("id") or ""),
            media_by_stop,
            cache=cache,
            hass=hass,
        )
        if photo is None:
            photo = await async_fetch_stock_stop_photo(
                session, str(stop.get("id") or ""), destination_galleries
            )
        if photo:
            photos.append(photo)
    if len(photos) < max_photos and day_media:
        used_stop_media = {
            str(item.get("id") or "")
            for stop in stops
            for item in media_by_stop.get(str(stop.get("id") or ""), [])
        }
        remaining = [
            item
            for item in day_media
            if str(item.get("id") or "") not in used_stop_media
        ]
        remaining.sort(key=media_quality_score, reverse=True)
        for item in remaining:
            if len(photos) >= max_photos:
                break
            photo = await async_fetch_media_photo(
                session, experience, trip_id, item, cache=cache, hass=hass
            )
            if photo:
                photos.append(photo)
    return photos


async def async_fetch_personal_stop_photo(
    session: Any,
    experience: Any,
    trip_id: str,
    stop_id: str,
    media_by_stop: dict[str, list[dict[str, Any]]],
    *,
    cache: Any = None,
    hass: Any = None,
) -> bytes | None:
    """Try several personal candidates, each as original THEN thumbnail.

    Live report: a trip with 254 memories produced "no photos found" - one
    failing download URL must never sink a whole stop when the large
    thumbnail (the exact image the panel shows reliably) is available.
    """
    candidates = media_by_stop.get(stop_id) or []
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda item: not item.get("is_cover"))
    for media_item in ordered[:3]:
        photo = await async_fetch_media_photo(
            session, experience, trip_id, media_item, cache=cache, hass=hass
        )
        if photo:
            return photo
    return None


async def async_fetch_media_photo(
    session: Any,
    experience: Any,
    trip_id: str,
    media_item: dict[str, Any],
    *,
    cache: Any = None,
    hass: Any = None,
) -> bytes | None:
    """Download ONE personal media item, rendered preview first.

    A locally cached copy short-circuits the whole Graph round trip - the
    same photos are needed by every PDF, every video and every crew
    portrait (live question: "da ist ein permanentes Runterladen
    eigentlich übertrieben").
    """
    media_id = str(media_item.get("id") or "")
    if not media_id:
        return None
    provider_item_id = str(media_item.get("provider_item_id") or "")
    cache_usable = cache is not None and hass is not None and cache.enabled
    # Rendered JPEG previews FIRST: iPhone photos are HEIC, which Pillow
    # cannot decode - the original downloaded fine and was then silently
    # discarded, which is why PDF and video stayed empty (live report).
    # Graph renders every thumbnail size as JPEG, whatever the source is.
    for kind, size in (
        ("thumbnail", "c1920x1440"),
        ("thumbnail", "large"),
        ("original", "large"),
    ):
        key = (
            cache_key(media_id, provider_item_id, size, kind) if cache_usable else ""
        )
        if key:
            cached = await hass.async_add_executor_job(cache.read, key)
            if cached:
                return cached
        try:
            url = await experience.async_media_redirect_url(
                trip_id, media_id, kind, size=size
            )
        except (RoadplannerError, KeyError, TypeError) as err:
            _record_photo_error(
                f"OneDrive-Link für Foto {media_id} ({kind}/{size}) nicht "
                f"auflösbar: {err}"
            )
            continue
        if not str(url or "").casefold().startswith("https://"):
            continue
        photo = await async_download_photo(session, url)
        if not photo:
            continue
        if not is_decodable_image(photo):
            # A downloaded iPhone original is HEIC: no renderer in Home
            # Assistant can open it, so returning it would only move the
            # failure one step further. Keep trying the other variants and
            # say precisely why this one was useless.
            _record_photo_error(
                f"Foto {media_id} ({kind}/{size}) kam in einem Format an, das "
                "Home Assistant nicht dekodieren kann (vermutlich HEIC)"
            )
            continue
        if key:
            # Only the user's OWN photo is stored locally - provider
            # stock imagery stays a reference (see module docstring).
            await hass.async_add_executor_job(cache.write, key, photo)
        return photo
    return None


async def async_fetch_stock_stop_photo(
    session: Any,
    stop_id: str,
    destination_galleries: dict[str, Any],
) -> bytes | None:
    gallery = destination_galleries.get(stop_id)
    if not isinstance(gallery, dict):
        return None
    images = [
        image for image in gallery.get("images") or [] if isinstance(image, dict)
    ]
    if not images:
        return None
    primary_id = str(gallery.get("primary_image_id") or "")
    ordered = sorted(
        images, key=lambda item: str(item.get("id") or "") != primary_id
    )
    for image in ordered:
        # Google Places photo URLs are browser-session redirects, unusable
        # for a server-side download - try the NEXT image instead of
        # giving up on the whole gallery.
        if str(image.get("provider") or "").casefold() == "google_places":
            continue
        url = str(image.get("image_url") or "")
        if not url.casefold().startswith("https://"):
            continue
        photo = await async_download_photo(session, url)
        if photo:
            return photo
    return None


def crop_photo(photo: bytes | None, crop: Any) -> bytes | None:
    """Cut a normalized (0..1) region out of a JPEG - fail-open.

    Used for the crew reference photo: on a group picture only the chosen
    face region identifies the person (live request).
    """
    if not photo or not isinstance(crop, dict):
        return photo
    try:
        from PIL import Image

        box = {key: float(crop[key]) for key in ("x", "y", "w", "h")}
    except (KeyError, TypeError, ValueError, ImportError):
        return photo
    if box["w"] <= 0.02 or box["h"] <= 0.02:
        return photo
    try:
        image = Image.open(io.BytesIO(photo))
        image.load()
        width, height = image.size
        left = int(max(0, min(box["x"], 1)) * width)
        top = int(max(0, min(box["y"], 1)) * height)
        right = min(width, left + max(1, int(box["w"] * width)))
        bottom = min(height, top + max(1, int(box["h"] * height)))
        if right - left < 16 or bottom - top < 16:
            return photo
        buffer = io.BytesIO()
        image.convert("RGB").crop((left, top, right, bottom)).save(
            buffer, format="JPEG", quality=90
        )
        return buffer.getvalue()
    except Exception as err:  # noqa: BLE001 - a failed crop must keep the photo
        _LOGGER.debug("Crew reference crop failed: %s", type(err).__name__)
        return photo


async def async_download_photo(session: Any, url: str) -> bytes | None:
    try:
        async with session.get(
            url, timeout=PHOTO_FETCH_TIMEOUT, allow_redirects=True
        ) as response:
            if response.status != 200:
                _record_photo_error(f"HTTP {response.status} beim Bild-Download")
                return None
            if (
                response.content_length is not None
                and response.content_length > MAX_PHOTO_BYTES
            ):
                _record_photo_error(
                    f"Bild zu groß ({response.content_length // 1_000_000} MB)"
                )
                return None
            body = await response.content.read(MAX_PHOTO_BYTES + 1)
            if len(body) > MAX_PHOTO_BYTES:
                _record_photo_error("Bild überschreitet das Größenlimit")
                return None
            return body
    except (ClientError, TimeoutError, asyncio.TimeoutError) as err:
        _record_photo_error(f"Netzwerkfehler beim Bild-Download ({type(err).__name__})")
        return None
