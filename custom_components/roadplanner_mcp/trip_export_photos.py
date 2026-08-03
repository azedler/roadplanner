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
import logging
from typing import Any

from aiohttp import ClientError, ClientTimeout

from .roadplanner import RoadplannerError

_LOGGER = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 6 * 1024 * 1024
PHOTO_FETCH_TIMEOUT = ClientTimeout(total=8)


async def async_fetch_day_photos(
    session: Any,
    experience: Any,
    trip_id: str,
    stops: list[dict[str, Any]],
    media_by_stop: dict[str, list[dict[str, Any]]],
    destination_galleries: dict[str, Any],
    *,
    max_photos: int,
) -> list[bytes]:
    """Best-effort download of up to ``max_photos`` photos, one per stop.

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
            session, experience, trip_id, str(stop.get("id") or ""), media_by_stop
        )
        if photo is None:
            photo = await async_fetch_stock_stop_photo(
                session, str(stop.get("id") or ""), destination_galleries
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
            session, experience, trip_id, media_item
        )
        if photo:
            return photo
    return None


async def async_fetch_media_photo(
    session: Any,
    experience: Any,
    trip_id: str,
    media_item: dict[str, Any],
) -> bytes | None:
    """Download ONE personal media item, original first, thumbnail fallback."""
    media_id = str(media_item.get("id") or "")
    if not media_id:
        return None
    for kind in ("original", "thumbnail"):
        try:
            url = await experience.async_media_redirect_url(trip_id, media_id, kind)
        except (RoadplannerError, KeyError) as err:
            _LOGGER.info(
                "Trip export could not resolve a personal photo (%s/%s): %s",
                media_id,
                kind,
                type(err).__name__,
            )
            continue
        if not str(url or "").casefold().startswith("https://"):
            continue
        photo = await async_download_photo(session, url)
        if photo:
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


async def async_download_photo(session: Any, url: str) -> bytes | None:
    try:
        async with session.get(
            url, timeout=PHOTO_FETCH_TIMEOUT, allow_redirects=True
        ) as response:
            if response.status != 200:
                return None
            if (
                response.content_length is not None
                and response.content_length > MAX_PHOTO_BYTES
            ):
                return None
            body = await response.content.read(MAX_PHOTO_BYTES + 1)
            if len(body) > MAX_PHOTO_BYTES:
                return None
            return body
    except (ClientError, TimeoutError, asyncio.TimeoutError) as err:
        _LOGGER.debug(
            "Trip export could not fetch a destination photo: %s", type(err).__name__
        )
        return None
