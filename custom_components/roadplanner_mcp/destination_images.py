"""Optional destination image search backed by Wikimedia Commons."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from html import unescape
import logging
import re
from time import monotonic
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INTEGRATION_VERSION
from .roadplanner import ValidationError

_LOGGER = logging.getLogger(__name__)

_COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_CACHE_SECONDS = 6 * 60 * 60
_MAX_QUERY_LENGTH = 200
_MAX_RESULTS = 12
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")


def _plain_text(value: Any, *, max_length: int = 500) -> str:
    """Convert Wikimedia HTML metadata into short plain text."""
    if not isinstance(value, str):
        return ""
    text = unescape(_TAG_PATTERN.sub(" ", value))
    text = _SPACE_PATTERN.sub(" ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text


def _https_url(value: Any) -> str | None:
    """Return a safe HTTPS URL or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return value


def _metadata_value(metadata: dict[str, Any], key: str) -> str:
    raw = metadata.get(key, {})
    if not isinstance(raw, dict):
        return ""
    return _plain_text(raw.get("value", ""))


def _parse_commons_response(payload: Any, *, limit: int) -> list[dict[str, Any]]:
    """Convert a Commons API response to a bounded frontend contract."""
    if not isinstance(payload, dict):
        return []
    query = payload.get("query")
    if not isinstance(query, dict):
        return []
    pages = query.get("pages", [])
    if isinstance(pages, dict):
        page_items = pages.values()
    elif isinstance(pages, list):
        page_items = pages
    else:
        return []

    results: list[dict[str, Any]] = []
    for page in page_items:
        if not isinstance(page, dict):
            continue
        image_info = page.get("imageinfo")
        if not isinstance(image_info, list) or not image_info:
            continue
        info = image_info[0]
        if not isinstance(info, dict):
            continue
        mime = str(info.get("mime") or "")
        if mime and not mime.startswith("image/"):
            continue
        image_url = _https_url(info.get("thumburl") or info.get("url"))
        source_url = _https_url(info.get("descriptionurl"))
        if image_url is None or source_url is None:
            continue
        metadata = info.get("extmetadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        title = _plain_text(page.get("title", ""), max_length=300)
        if title.casefold().startswith("file:"):
            title = title[5:].strip()
        artist = _metadata_value(metadata, "Artist")
        license_name = _metadata_value(metadata, "LicenseShortName")
        credit = _metadata_value(metadata, "Credit")
        description = (
            _metadata_value(metadata, "ImageDescription")
            or _metadata_value(metadata, "ObjectName")
            or title
        )
        attribution_parts = [part for part in (artist, license_name) if part]
        attribution = " · ".join(attribution_parts) or credit or "Wikimedia Commons"
        results.append(
            {
                "id": str(page.get("pageid") or len(results) + 1),
                "title": title or description or "Wikimedia Commons",
                "image_url": image_url,
                "source_url": source_url,
                "alt": description or title,
                "attribution": attribution,
                "provider": "wikimedia_commons",
            }
        )
        if len(results) >= limit:
            break
    return results


@dataclass(slots=True)
class _CacheEntry:
    created_at: float
    results: list[dict[str, Any]]


class DestinationImageProvider:
    """Search destination images only after an explicit panel request."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: dict[tuple[str, int], _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def async_search(
        self,
        query: str,
        *,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Search Wikimedia Commons for reusable destination imagery."""
        if not isinstance(query, str):
            raise ValidationError("Bildsuche benötigt einen Suchtext")
        query = _SPACE_PATTERN.sub(" ", query).strip()
        if not query:
            raise ValidationError("Bildsuche benötigt einen Suchtext")
        if len(query) > _MAX_QUERY_LENGTH:
            raise ValidationError(
                f"Der Suchtext darf maximal {_MAX_QUERY_LENGTH} Zeichen enthalten"
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValidationError("'limit' muss eine Ganzzahl sein")
        limit = max(1, min(limit, _MAX_RESULTS))
        cache_key = (query.casefold(), limit)

        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and monotonic() - cached.created_at < _CACHE_SECONDS:
                return {
                    "query": query,
                    "provider": "wikimedia_commons",
                    "cached": True,
                    "count": len(cached.results),
                    "results": cached.results,
                }

            session = async_get_clientsession(self.hass)
            params = {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrnamespace": "6",
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata",
                "iiurlwidth": "1280",
            }
            headers = {
                "User-Agent": (
                    "HomeAssistant-Roadplanner/"
                    f"{INTEGRATION_VERSION} (destination image search)"
                )
            }
            try:
                async with session.get(
                    _COMMONS_API,
                    params=params,
                    headers=headers,
                    timeout=ClientTimeout(total=15),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                _LOGGER.warning("Wikimedia image search failed: %s", err)
                raise ValidationError(
                    "Die Bildsuche bei Wikimedia Commons ist fehlgeschlagen"
                ) from err

            results = _parse_commons_response(payload, limit=limit)
            self._cache[cache_key] = _CacheEntry(monotonic(), results)
            return {
                "query": query,
                "provider": "wikimedia_commons",
                "cached": False,
                "count": len(results),
                "results": results,
            }
