"""Resolve a Google Maps link into a deterministic place_query.

The URL structure is read first. Short links (``maps.app.goo.gl``,
``goo.gl``) are resolved by following the HTTP redirect and reading the
``Location`` header. If the canonical URL carries a precise ``!3d/!4d``
marker, an ``@lat,lng`` segment, a place/search name or a ``q=`` parameter,
that is used directly. Only when the URL itself carries nothing readable
(e.g. cid-only POI shares) does a bounded LINK-PREVIEW fetch run: it reads
exclusively the page's meta tags (og:title and the static-map preview
image, which encodes the place position) - the same data any messenger
shows for a pasted link, never the rendered maps application content.
This never invents data and fails open (returns ``None``) on any error,
network condition, or unrecognized link shape so a stop can always still
be completed manually.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .destination_intelligence import _URL_RE, _google_maps_host, _host_matches

_LOGGER = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 4
_REDIRECT_TIMEOUT_SECONDS = 6.0

_COORDINATE_IN_PATH_RE = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
_PLACE_NAME_IN_PATH_RE = re.compile(r"/maps/(?:place|search)/([^/@]+)", re.IGNORECASE)
# The data blob of a shared POI carries the PRECISE marker position as
# !3d<lat>!4d<lon> - unlike @lat,lng, which is only the viewport center.
# Mobile share links (maps.app.goo.gl, "?g_st=ic") often resolve to URLs
# without any @segment or place name, so without this the whole link was
# silently ignored (live report).
_MARKER_IN_DATA_RE = re.compile(r"!3d(-?\d{1,3}\.\d+)!4d(-?\d{1,3}\.\d+)")
_COORDINATE_PAIR_RE = re.compile(r"^(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)$")


_MAX_PREVIEW_BYTES = 512_000
_PREVIEW_TIMEOUT_SECONDS = 8.0
# The og:image of a maps place page is a static-map URL whose center (or
# markers parameter) encodes the PLACE position.
_META_STATICMAP_CENTER_RE = re.compile(
    r"(?:center|markers)=(?:[^\"&]*?%7C)?(-?\d{1,3}\.\d+)(?:%2C|,)(-?\d{1,3}\.\d+)",
    re.IGNORECASE,
)
_META_OG_TITLE_RE = re.compile(
    r'<meta[^>]+property="og:title"[^>]+content="([^"]{1,300})"'
    r'|<meta[^>]+content="([^"]{1,300})"[^>]+property="og:title"',
    re.IGNORECASE,
)


def _extract_place_query_from_preview_html(page_html: str) -> str | None:
    """Read only link-preview metadata: static-map position, then og:title."""
    if not page_html:
        return None
    center_match = _META_STATICMAP_CENTER_RE.search(page_html)
    if center_match and _valid_pair(*center_match.groups()):
        latitude, longitude = center_match.groups()
        return f"{latitude},{longitude}"
    title_match = _META_OG_TITLE_RE.search(page_html)
    if title_match:
        raw_title = title_match.group(1) or title_match.group(2) or ""
        name = re.sub(
            r"\s*[·\-–|]\s*Google\s*Maps\s*$", "", raw_title, flags=re.IGNORECASE
        ).strip()
        if name and name.casefold() != "google maps":
            return name[:500]
    return None


async def _async_link_preview_query(hass: HomeAssistant, url: str) -> str | None:
    """Bounded meta-tag fetch for maps links whose URL carries nothing."""
    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(_PREVIEW_TIMEOUT_SECONDS):
            async with session.get(
                url,
                headers={
                    "Accept-Language": "de,en;q=0.8",
                    # Best effort to skip the EU consent interstitial, which
                    # carries no place metadata. Failing is fine: fail open.
                    "Cookie": "CONSENT=YES+cb",
                },
            ) as response:
                if response.status != 200:
                    return None
                raw = await response.content.read(_MAX_PREVIEW_BYTES)
    except (ClientError, TimeoutError):
        return None
    return _extract_place_query_from_preview_html(
        raw.decode("utf-8", errors="replace")
    )


def _valid_pair(latitude: str, longitude: str) -> bool:
    try:
        lat, lon = float(latitude), float(longitude)
    except ValueError:
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _is_short_link_host(host: str) -> bool:
    return _host_matches(host, "maps.app.goo.gl") or _host_matches(host, "goo.gl")


def _extract_place_query_from_url(url: str) -> str | None:
    """Return a place_query derived deterministically from a Maps URL's own
    structure - a coordinate pair if present, otherwise a place name.

    Priority: the precise !3d/!4d marker position from the data blob, then
    the @lat,lng viewport center, then a /maps/place|search/<name> path
    segment, then a q=/query= parameter (coordinates or text).
    """
    parsed = urlparse(url)
    marker_match = _MARKER_IN_DATA_RE.search(unquote(url))
    if marker_match and _valid_pair(*marker_match.groups()):
        latitude, longitude = marker_match.groups()
        return f"{latitude},{longitude}"
    coordinate_match = _COORDINATE_IN_PATH_RE.search(parsed.path)
    if coordinate_match and _valid_pair(*coordinate_match.groups()):
        latitude, longitude = coordinate_match.groups()
        return f"{latitude},{longitude}"
    name_match = _PLACE_NAME_IN_PATH_RE.search(parsed.path)
    if name_match:
        name = unquote(name_match.group(1)).replace("+", " ").strip()
        if name:
            return name[:500]
    query_params = parse_qs(parsed.query)
    for key in ("q", "query", "destination"):
        for raw_value in query_params.get(key, []):
            value = unquote(str(raw_value)).replace("+", " ").strip()
            if not value:
                continue
            pair_match = _COORDINATE_PAIR_RE.match(value)
            if pair_match:
                if _valid_pair(*pair_match.groups()):
                    latitude, longitude = pair_match.groups()
                    return f"{latitude},{longitude}"
                continue
            return value[:500]
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
        # cid-only POI shares carry nothing readable in the URL itself -
        # last resort is the link-preview metadata of the canonical page
        # (live report: such a shared POI link was silently ignored).
        preview_query = await _async_link_preview_query(hass, canonical_url)
        if preview_query:
            _LOGGER.debug("Resolved Maps link via link-preview metadata")
            return preview_query
        _LOGGER.debug("Maps link carried no readable place data: %s", canonical_url)
        return None
    return None
