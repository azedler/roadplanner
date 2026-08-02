"""Resolve a Google Maps link into a deterministic place_query.

The URL structure is read first. Short links (``maps.app.goo.gl``,
``goo.gl``) are resolved by following the HTTP redirect and reading the
``Location`` header. If the canonical URL carries a precise ``!3d/!4d``
marker, an ``@lat,lng`` segment, a place/search name or a ``q=`` parameter,
that is used directly. Only when the URL itself is missing something (the
coordinates of cid-only POI shares, or the NAME of marker-only shares)
does a bounded LINK-PREVIEW fetch run: it reads
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
from typing import Any
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
# The page's canonical/og:url metadata carries the FULL maps URL including
# the data blob with the precise !3d/!4d marker - often the only place the
# position survives when the share URL itself is opaque.
_META_CANONICAL_URL_RE = re.compile(
    r'<link[^>]+rel="canonical"[^>]+href="([^"]{1,2000})"'
    r'|<link[^>]+href="([^"]{1,2000})"[^>]+rel="canonical"'
    r'|<meta[^>]+property="og:url"[^>]+content="([^"]{1,2000})"'
    r'|<meta[^>]+content="([^"]{1,2000})"[^>]+property="og:url"',
    re.IGNORECASE,
)


def _extract_place_preview(page_html: str) -> dict[str, Any]:
    """Read only link-preview metadata: static-map position and og:title."""
    preview: dict[str, Any] = {}
    if not page_html:
        return preview
    for canonical_match in _META_CANONICAL_URL_RE.finditer(page_html):
        canonical = next(
            (group for group in canonical_match.groups() if group), ""
        ).replace("&amp;", "&")
        url_info = _extract_place_info_from_url(canonical)
        if "latitude" in url_info:
            preview["latitude"] = float(url_info["latitude"])
            preview["longitude"] = float(url_info["longitude"])
        if "name" in url_info:
            preview.setdefault("name", url_info["name"])
        if "latitude" in preview:
            break
    if "latitude" not in preview:
        center_match = _META_STATICMAP_CENTER_RE.search(page_html)
        if center_match and _valid_pair(*center_match.groups()):
            latitude, longitude = center_match.groups()
            preview["latitude"] = float(latitude)
            preview["longitude"] = float(longitude)
    title_match = _META_OG_TITLE_RE.search(page_html)
    if title_match:
        raw_title = title_match.group(1) or title_match.group(2) or ""
        name = re.sub(
            r"\s*[·\-–|]\s*Google\s*Maps\s*$", "", raw_title, flags=re.IGNORECASE
        ).strip()
        if name and name.casefold() != "google maps":
            preview["name"] = name[:500]
    return preview


def _extract_place_query_from_preview_html(page_html: str) -> str | None:
    preview = _extract_place_preview(page_html)
    if "latitude" in preview:
        return f"{preview['latitude']},{preview['longitude']}"
    return preview.get("name")


async def _async_link_preview(hass: HomeAssistant, url: str) -> dict[str, Any]:
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
                    return {}
                raw = await response.content.read(_MAX_PREVIEW_BYTES)
    except (ClientError, TimeoutError):
        return {}
    return _extract_place_preview(raw.decode("utf-8", errors="replace"))


def _valid_pair(latitude: str, longitude: str) -> bool:
    try:
        lat, lon = float(latitude), float(longitude)
    except ValueError:
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _is_short_link_host(host: str) -> bool:
    return _host_matches(host, "maps.app.goo.gl") or _host_matches(host, "goo.gl")


def _extract_place_info_from_url(url: str) -> dict[str, Any]:
    """Read coordinates AND a place name from a Maps URL's own structure.

    Coordinates: the precise !3d/!4d marker position from the data blob
    beats the @lat,lng viewport center, which beats a coordinate-shaped
    q=/query= parameter. Name: the /maps/place|search/<name> path segment,
    otherwise a textual q=/query= parameter. Both are collected
    independently, so a shared POI link yields its NAME alongside the pin
    (live report: a shared restaurant was adopted only as generic "Essen").
    """
    info: dict[str, Any] = {}
    parsed = urlparse(url)
    marker_match = _MARKER_IN_DATA_RE.search(unquote(url))
    if marker_match and _valid_pair(*marker_match.groups()):
        info["latitude"], info["longitude"] = marker_match.groups()
    if "latitude" not in info:
        coordinate_match = _COORDINATE_IN_PATH_RE.search(parsed.path)
        if coordinate_match and _valid_pair(*coordinate_match.groups()):
            info["latitude"], info["longitude"] = coordinate_match.groups()
    name_match = _PLACE_NAME_IN_PATH_RE.search(parsed.path)
    if name_match:
        name = unquote(name_match.group(1)).replace("+", " ").strip()
        if name:
            info["name"] = name[:500]
    query_params = parse_qs(parsed.query)
    for key in ("q", "query", "destination"):
        for raw_value in query_params.get(key, []):
            value = unquote(str(raw_value)).replace("+", " ").strip()
            if not value:
                continue
            pair_match = _COORDINATE_PAIR_RE.match(value)
            if pair_match:
                if "latitude" not in info and _valid_pair(*pair_match.groups()):
                    info["latitude"], info["longitude"] = pair_match.groups()
                continue
            if "name" not in info:
                info["name"] = value[:500]
    return info


def _extract_place_query_from_url(url: str) -> str | None:
    """Return a place_query derived deterministically from a Maps URL's own
    structure - a coordinate pair if present, otherwise a place name.
    """
    info = _extract_place_info_from_url(url)
    if "latitude" in info:
        return f"{info['latitude']},{info['longitude']}"
    return info.get("name")


def _continue_url_from_consent(location: str) -> str | None:
    """Extract the real maps URL a Google consent interstitial wraps.

    In the EU, Google may redirect to consent.google.com with the actual
    destination in the ``continue`` parameter - following that redirect
    would land on a consent page carrying no place data at all (live
    report: a shared restaurant link failed to resolve). The wrapped URL
    is only accepted when it is itself an https Google URL.
    """
    parsed = urlparse(location)
    host = parsed.hostname or ""
    if not _host_matches(host, "consent.google.com"):
        return None
    for candidate in parse_qs(parsed.query).get("continue", []):
        target = unquote(str(candidate))
        target_parsed = urlparse(target)
        target_host = target_parsed.hostname or ""
        if target_parsed.scheme == "https" and (
            _host_matches(target_host, "google.com")
            or _google_maps_host(target_host)
        ):
            return target
    return None


async def _async_follow_redirects(hass: HomeAssistant, url: str) -> str | None:
    """Follow HTTP redirects from a Google short link to its canonical URL.

    Reads only response headers (never the response body) and only ever
    follows redirects that stay on a Google-controlled host. A consent
    interstitial is never followed - its ``continue`` parameter IS the
    canonical URL.
    """
    session = async_get_clientsession(hass)
    current = url
    for _ in range(_MAX_REDIRECT_HOPS):
        try:
            async with asyncio.timeout(_REDIRECT_TIMEOUT_SECONDS):
                async with session.get(
                    current,
                    allow_redirects=False,
                    headers={
                        "Accept-Language": "de,en;q=0.8",
                        # Best effort to skip the EU consent interstitial.
                        "Cookie": "CONSENT=YES+cb",
                    },
                ) as response:
                    if response.status not in (301, 302, 303, 307, 308):
                        return current if response.status < 400 else None
                    location = response.headers.get("Location")
        except (ClientError, TimeoutError):
            return None
        if not location:
            return None
        if not location.startswith("http"):
            return None
        continue_url = _continue_url_from_consent(location)
        if continue_url:
            return continue_url
        parsed = urlparse(location)
        redirect_host = parsed.hostname or ""
        stays_on_google = _host_matches(redirect_host, "google.com") or _google_maps_host(
            redirect_host
        )
        if parsed.scheme != "https" or not stays_on_google:
            return None
        current = location
    return None


async def async_resolve_google_maps_place(
    hass: HomeAssistant, text: str
) -> dict[str, Any] | None:
    """Find a Google Maps link in free text and resolve it into place data.

    Returns a dict with ``place_query`` (a coordinate pair if known,
    otherwise the place name) plus, when readable, ``name`` and
    ``latitude``/``longitude`` - so callers can adopt the POI's NAME
    alongside its pin. Returns None if no Google Maps link is present or
    nothing readable could be derived - the caller should then fall back
    to whatever it already had.
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
        info = _extract_place_info_from_url(canonical_url)
        if "latitude" not in info or "name" not in info:
            # cid-only POI shares carry nothing readable in the URL itself,
            # and marker-only shares carry no NAME - the link-preview
            # metadata of the canonical page fills the gaps (live reports:
            # a shared POI link was silently ignored; a shared restaurant
            # was adopted only as generic "Essen").
            preview = await _async_link_preview(hass, canonical_url)
            if "latitude" not in info and "latitude" in preview:
                info["latitude"] = preview["latitude"]
                info["longitude"] = preview["longitude"]
            if "name" not in info and "name" in preview:
                info["name"] = preview["name"]
        if "latitude" in info:
            # The place_query keeps the URL's original digits (incl.
            # trailing zeros); the numeric fields are for form prefills.
            info["place_query"] = f"{info['latitude']},{info['longitude']}"
            info["latitude"] = float(info["latitude"])
            info["longitude"] = float(info["longitude"])
        elif info.get("name"):
            info["place_query"] = info["name"]
        else:
            _LOGGER.debug(
                "Maps link carried no readable place data: %s", canonical_url
            )
            return None
        return info
    return None


async def async_resolve_google_maps_place_query(
    hass: HomeAssistant, text: str
) -> str | None:
    """Find a Google Maps link in free text and resolve it to a place_query.

    Returns None if no Google Maps link is present, or if it cannot be
    resolved into a coordinate pair or place name - the caller should then
    fall back to whatever place_query it already had.
    """
    info = await async_resolve_google_maps_place(hass, text)
    if info is None:
        return None
    return str(info["place_query"])
