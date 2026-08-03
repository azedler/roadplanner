"""Extract candidate photos from a user-shared place page.

When a stop carries a link the user shared (Park4Night, naturkartan.se, a
campsite website ...), that page usually shows the best photos of the
place. This module reads them DETERMINISTICALLY - og:image/twitter:image
metadata, JSON-LD image entries and plain <img> tags - the same data any
link preview uses. No AI is involved, nothing is invented, and every
extracted image keeps the page as its source/attribution so the origin
stays reviewable. Downloads never happen here: like every other image
provider, only references and attribution metadata are returned, the
files remain with the linked website.

Everything fails open: any network error, oversized page, or unparseable
HTML yields an empty list, never an exception into the gallery flow.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INTEGRATION_VERSION

_LOGGER = logging.getLogger(__name__)

_MAX_PAGE_BYTES = 900_000
_PAGE_TIMEOUT_SECONDS = 10.0
_MAX_IMAGES_PER_PAGE = 8

_META_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)="(?:og:image(?::secure_url)?|twitter:image(?::src)?)"'
    r'[^>]+content="([^"]{1,2000})"'
    r'|<meta[^>]+content="([^"]{1,2000})"[^>]+(?:property|name)='
    r'"(?:og:image(?::secure_url)?|twitter:image(?::src)?)"',
    re.IGNORECASE,
)
_JSON_LD_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_ATTRIBUTE_RE = re.compile(r'([a-zA-Z0-9_:-]+)\s*=\s*"([^"]*)"')

# Obvious non-photo assets, matched against the URL path (casefolded).
_NEGATIVE_URL_MARKERS = (
    "logo",
    "icon",
    "sprite",
    "avatar",
    "placeholder",
    "pixel",
    "badge",
    "banner",
    "favicon",
    "flag",
    "spinner",
    "loading",
    "blank",
    "tracking",
    "/map",
    "karte",
    "marker",
)
_ALLOWED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".avif")
_MIN_INLINE_DIMENSION = 200


def _clean_image_url(raw: str, *, base_url: str) -> str | None:
    value = str(raw or "").replace("&amp;", "&").strip()
    if not value or value.startswith("data:"):
        return None
    absolute = urljoin(base_url, value)
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    path = (parsed.path or "").casefold()
    if any(marker in path for marker in _NEGATIVE_URL_MARKERS):
        return None
    # Query-parameterized CDN URLs often carry no suffix - accept those,
    # reject only paths that clearly end in a non-photo format.
    if "." in path.rsplit("/", 1)[-1] and not path.endswith(_ALLOWED_SUFFIXES):
        return None
    return absolute[:2_000]


def _json_ld_image_urls(page_html: str) -> list[str]:
    urls: list[str] = []
    for match in _JSON_LD_RE.finditer(page_html):
        try:
            payload = json.loads(match.group(1).strip())
        except (ValueError, TypeError):
            continue
        stack: list[Any] = [payload]
        while stack and len(urls) < _MAX_IMAGES_PER_PAGE:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                image = node.get("image")
                if isinstance(image, str):
                    urls.append(image)
                elif isinstance(image, list):
                    for item in image:
                        if isinstance(item, str):
                            urls.append(item)
                        elif isinstance(item, dict) and isinstance(
                            item.get("url"), str
                        ):
                            urls.append(item["url"])
                elif isinstance(image, dict) and isinstance(image.get("url"), str):
                    urls.append(image["url"])
                stack.extend(node.values())
    return urls


def _img_tag_candidates(page_html: str) -> list[tuple[str, str]]:
    """Return (url, alt) pairs from plain <img> tags, obvious junk removed."""
    candidates: list[tuple[str, str]] = []
    for tag_match in _IMG_TAG_RE.finditer(page_html):
        attributes = dict(_ATTRIBUTE_RE.findall(tag_match.group(0)))
        source = (
            attributes.get("src")
            or attributes.get("data-src")
            or attributes.get("data-lazy-src")
            or ""
        )
        if not source:
            continue
        skip = False
        for dimension_key in ("width", "height"):
            raw_dimension = str(attributes.get(dimension_key) or "").strip()
            if raw_dimension.isdigit() and int(raw_dimension) < _MIN_INLINE_DIMENSION:
                skip = True
        if skip:
            continue
        candidates.append((source, str(attributes.get("alt") or "")))
    return candidates


def extract_page_images(
    page_html: str, *, page_url: str, limit: int = _MAX_IMAGES_PER_PAGE
) -> list[dict[str, Any]]:
    """Pure extraction of image references from one page's HTML."""
    if not page_html:
        return []
    host = (urlparse(page_url).hostname or "").casefold()
    ordered: list[tuple[str, str]] = []
    for meta_match in _META_IMAGE_RE.finditer(page_html):
        url = next((group for group in meta_match.groups() if group), "")
        ordered.append((url, ""))
    for url in _json_ld_image_urls(page_html):
        ordered.append((url, ""))
    ordered.extend(_img_tag_candidates(page_html))
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_url, alt in ordered:
        cleaned = _clean_image_url(raw_url, base_url=page_url)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        images.append(
            {
                "id": "shared-link-"
                + hashlib.sha256(cleaned.encode()).hexdigest()[:16],
                "provider": "shared_link",
                "title": alt[:500] or host or "Geteilter Link",
                "image_url": cleaned,
                "thumbnail_url": cleaned,
                "original_url": cleaned,
                "source_url": page_url[:2_000],
                "alt": alt[:1_000] or host,
                "attribution": host or "Geteilter Link",
                "author": "",
                "license": "",
                "license_url": None,
                "width": None,
                "height": None,
                "proximity_match": True,
            }
        )
        if len(images) >= limit:
            break
    return images


# Hint providers whose pages are not worth fetching for photos: maps pages
# sit behind consent walls, and the wiki/OSM ecosystems are already covered
# by the Commons image search.
_SKIP_HINT_PROVIDERS = {"google_maps", "wikipedia", "wikidata", "openstreetmap"}


async def async_images_from_source_hints(
    hass: HomeAssistant,
    source_hints: Any,
    *,
    limit: int = _MAX_IMAGES_PER_PAGE,
    max_pages: int = 2,
) -> list[dict[str, Any]]:
    """Fetch photo references from the stop's user-shared content pages."""
    images: list[dict[str, Any]] = []
    seen: set[str] = set()
    pages = 0
    for hint in list(source_hints or []):
        if not isinstance(hint, dict):
            continue
        if str(hint.get("provider") or "") in _SKIP_HINT_PROVIDERS:
            continue
        url = str(hint.get("url") or "")
        if not url:
            continue
        pages += 1
        found = await async_fetch_page_images(hass, url, limit=limit - len(images))
        for image in found:
            key = str(image.get("image_url") or "")
            if key and key not in seen:
                seen.add(key)
                images.append(image)
        if len(images) >= limit or pages >= max_pages:
            break
    if images:
        _LOGGER.debug(
            "Extracted %d photo references from %d shared page(s)",
            len(images),
            pages,
        )
    return images


async def async_fetch_page_images(
    hass: HomeAssistant, url: str, *, limit: int = _MAX_IMAGES_PER_PAGE
) -> list[dict[str, Any]]:
    """Bounded fetch of one shared page, returning image references."""
    if hass is None:
        return []
    parsed = urlparse(str(url or ""))
    if parsed.scheme != "https" or not parsed.hostname:
        return []
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(_PAGE_TIMEOUT_SECONDS):
            async with session.get(
                url,
                headers={
                    "Accept-Language": "de,en;q=0.8",
                    "User-Agent": (
                        "HomeAssistant-Roadplanner/"
                        f"{INTEGRATION_VERSION} (shared link preview)"
                    ),
                },
            ) as response:
                if response.status != 200:
                    return []
                raw = await response.content.read(_MAX_PAGE_BYTES)
    except (ClientError, TimeoutError):
        return []
    return extract_page_images(
        raw.decode("utf-8", errors="replace"), page_url=url, limit=limit
    )
