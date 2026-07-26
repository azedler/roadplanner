"""Resolve a Google Maps link into a deterministic place_query.

Only the URL structure is read. Short links (``maps.app.goo.gl``, ``goo.gl``)
are resolved by following the HTTP redirect and reading the ``Location``
header - never by fetching or parsing Google's rendered page content, which
would be scraping. If the canonical URL carries an ``@lat,lng`` segment, that
takes priority (an exact coordinate pair, verified through the normal
GPS-Prüfung reverse-geocoding path); otherwise the place name in the URL path
is used as a text query. This never invents data and fails open (returns
``None``) on any error, network condition, or unrecognized link shape so a
stop can always still be completed manually.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import unquote, urlparse

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .destination_intelligence import _URL_RE, _google_maps_host, _host_matches

_LOGGER = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 4
_REDIRECT_TIMEOUT_SECONDS = 6.0

_COORDINATE_IN_PATH_RE = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
_PLACE_NAME_IN_PATH_RE = re.compile(r"/maps/place/([^/@]+)", re.IGNORECASE)


def _is_short_link_host(host: str) -> bool:
    return _host_matches(host, "maps.app.goo.gl") or _host_matches(host, "goo.gl")


def _extract_place_query_from_url(url: str) -> str | None:
    """Return a place_query derived deterministically from a Maps URL's own
    structure - a coordinate pair if present, otherwise a place name."""
    parsed = urlparse(url)
    coordinate_match = _COORDINATE_IN_PATH_RE.search(parsed.path)
    if coordinate_match:
        latitude, longitude = coordinate_match.groups()
        return f"{latitude},{longitude}"
    name_match = _PLACE_NAME_IN_PATH_RE.search(parsed.path)
    if name_match:
        name = unquote(name_match.group(1)).replace("+", " ").strip()
        if name:
            return name[:500]
    return None


async def _async_follow_redirects(hass: HomeAssistant, url: str) -> str | None:
    """Follow HTTP redirects from a Google short link to its canonical URL.

    Reads only response headers (never the response body) and only ever
    follows redirects that stay on a Google-controlled host.
    """
    session = async_get_clientsession(hass)
    current = url
    for _ in range(_MAX_REDIRECT_HOPS):
        try:
            async with asyncio.timeout(_REDIRECT_TIMEOUT_SECONDS):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status not in (301, 302, 303, 307, 308):
                        return current if response.status < 400 else None
                    location = response.headers.get("Location")
        except (ClientError, TimeoutError):
            return None
        if not location:
            return None
        if not location.startswith("http"):
            return None
        parsed = urlparse(location)
        redirect_host = parsed.hostname or ""
        stays_on_google = _host_matches(redirect_host, "google.com") or _google_maps_host(
            redirect_host
        )
        if parsed.scheme != "https" or not stays_on_google:
            return None
        current = location
    return None


async def async_resolve_google_maps_place_query(
    hass: HomeAssistant, text: str
) -> str | None:
    """Find a Google Maps link in free text and resolve it to a place_query.

    Returns None if no Google Maps link is present, or if it cannot be
    resolved into a coordinate pair or place name - the caller should then
    fall back to whatever place_query it already had.
    """
    for raw_url in _URL_RE.findall(str(text or "")):
        url = raw_url.rstrip(".,;:")
        try:
            parsed = urlparse(url)
        except ValueError:
            continue
        host = (parsed.hostname or "").casefold()
        if not _google_maps_host(host):
            continue
        canonical_url = url
        if _is_short_link_host(host):
            resolved = await _async_follow_redirects(hass, url)
            if not resolved:
                _LOGGER.debug("Could not resolve Google Maps short link redirect")
                return None
            canonical_url = resolved
        place_query = _extract_place_query_from_url(canonical_url)
        if place_query:
            return place_query
        return None
    return None
