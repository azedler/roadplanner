"""Destination image provider chain for planned Roadplanner stops.

The provider returns references and attribution metadata only. Original files
remain with Wikimedia Commons, Openverse, or another configured source.
"""

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
_OPENVERSE_API = "https://api.openverse.org/v1/images/"
_CACHE_SECONDS = 12 * 60 * 60
_MAX_QUERY_LENGTH = 400
_MAX_RESULTS = 18
_PROVIDER_RESULT_LIMIT = 12
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SPACE_PATTERN = re.compile(r"\s+")
_WORD_PATTERN = re.compile(r"[\wÀ-ž]{3,}", re.UNICODE)
_NEGATIVE_IMAGE_MARKERS = (
    "logo",
    "flag",
    "karte",
    "map",
    "diagram",
    "diagramm",
    "coat of arms",
    "wappen",
    "poster",
    "ticket",
    "screenshot",
    "sign",
    "schild",
    "floor plan",
    "grundriss",
)


def _plain_text(value: Any, *, max_length: int = 500) -> str:
    if not isinstance(value, str):
        return ""
    text = unescape(_TAG_PATTERN.sub(" ", value))
    text = _SPACE_PATTERN.sub(" ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "…"
    return text


def _https_url(value: Any) -> str | None:
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
    return _plain_text(raw.get("value", ""), max_length=1_000)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _image_contract(
    *,
    identifier: str,
    provider: str,
    title: str,
    image_url: str,
    source_url: str,
    alt: str,
    attribution: str,
    author: str = "",
    license_name: str = "",
    license_url: str | None = None,
    original_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    proximity: bool = False,
) -> dict[str, Any]:
    return {
        "id": identifier[:500],
        "provider": provider,
        "title": title[:500] or alt[:500] or "Reiseziel",
        "image_url": image_url,
        "thumbnail_url": image_url,
        "original_url": original_url or image_url,
        "source_url": source_url,
        "alt": alt[:1_000] or title[:1_000] or "Reiseziel",
        "attribution": attribution[:1_000] or provider,
        "author": author[:500],
        "license": license_name[:200],
        "license_url": license_url,
        "width": width,
        "height": height,
        "proximity_match": proximity,
    }


def _parse_commons_response(
    payload: Any,
    *,
    limit: int,
    proximity: bool,
) -> list[dict[str, Any]]:
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
        original_url = _https_url(info.get("url"))
        source_url = _https_url(info.get("descriptionurl"))
        if image_url is None or source_url is None:
            continue
        metadata = info.get("extmetadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        title = _plain_text(page.get("title", ""), max_length=500)
        if title.casefold().startswith("file:"):
            title = title[5:].strip()
        author = _metadata_value(metadata, "Artist")
        license_name = _metadata_value(metadata, "LicenseShortName")
        license_url = _https_url(_metadata_value(metadata, "LicenseUrl"))
        credit = _metadata_value(metadata, "Credit")
        description = (
            _metadata_value(metadata, "ImageDescription")
            or _metadata_value(metadata, "ObjectName")
            or title
        )
        attribution_parts = [part for part in (author, license_name) if part]
        attribution = " · ".join(attribution_parts) or credit or "Wikimedia Commons"
        results.append(
            _image_contract(
                identifier=f"commons-{page.get('pageid') or len(results) + 1}",
                provider="wikimedia_commons",
                title=title,
                image_url=image_url,
                original_url=original_url,
                source_url=source_url,
                alt=description,
                attribution=attribution,
                author=author,
                license_name=license_name,
                license_url=license_url,
                width=_integer(info.get("width") or info.get("thumbwidth")),
                height=_integer(info.get("height") or info.get("thumbheight")),
                proximity=proximity,
            )
        )
        if len(results) >= limit:
            break
    return results


def _parse_openverse_response(payload: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, Any]] = []
    for raw in raw_results:
        if not isinstance(raw, dict) or raw.get("mature") is True:
            continue
        image_url = _https_url(raw.get("thumbnail") or raw.get("url"))
        source_url = _https_url(raw.get("foreign_landing_url") or raw.get("detail_url"))
        original_url = _https_url(raw.get("url"))
        if image_url is None or source_url is None:
            continue
        identifier = str(raw.get("id") or len(results) + 1)
        title = _plain_text(raw.get("title") or "Openverse", max_length=500)
        author = _plain_text(raw.get("creator") or "", max_length=500)
        license_name = " ".join(
            part for part in (
                _plain_text(raw.get("license") or "", max_length=100).upper(),
                _plain_text(raw.get("license_version") or "", max_length=50),
            ) if part
        )
        license_url = _https_url(raw.get("license_url"))
        attribution = _plain_text(raw.get("attribution") or "", max_length=1_000)
        if not attribution:
            attribution = " · ".join(part for part in (author, license_name) if part) or "Openverse"
        results.append(
            _image_contract(
                identifier=f"openverse-{identifier}",
                provider="openverse",
                title=title,
                image_url=image_url,
                original_url=original_url,
                source_url=source_url,
                alt=title,
                attribution=attribution,
                author=author,
                license_name=license_name,
                license_url=license_url,
                width=_integer(raw.get("width")),
                height=_integer(raw.get("height")),
            )
        )
        if len(results) >= limit:
            break
    return results


def _query_tokens(query: str) -> set[str]:
    return {token.casefold() for token in _WORD_PATTERN.findall(query)}


def _selection_scores(
    image: dict[str, Any],
    query_tokens: set[str],
) -> tuple[float, float, float, str]:
    """Return relevance, technical quality, total score and a short reason."""
    title = str(image.get("title") or "")
    alt = str(image.get("alt") or "")
    searchable = f"{title} {alt}".casefold()
    candidate_tokens = _query_tokens(f"{title} {alt}")
    matches = len(query_tokens & candidate_tokens)
    coverage = matches / max(1, len(query_tokens))
    relevance = min(matches, 8) * 2.5 + coverage * 8.0
    reasons: list[str] = []
    if matches:
        reasons.append(f"{matches} Suchbegriffe passen")
    if image.get("proximity_match"):
        relevance += 12.0
        reasons.append("nahe am Kartenpunkt")
    if image.get("provider") == "wikimedia_commons":
        relevance += 0.8
    if any(marker in searchable for marker in _NEGATIVE_IMAGE_MARKERS):
        relevance -= 18.0
        reasons.append("weniger repräsentatives Motiv")

    quality = 0.0
    width = _integer(image.get("width"))
    height = _integer(image.get("height"))
    if width and height:
        pixels = width * height
        if pixels >= 3_000_000:
            quality += 8.0
        elif pixels >= 1_000_000:
            quality += 5.0
        elif pixels >= 400_000:
            quality += 2.0
        else:
            quality -= 6.0
        ratio = width / height
        if 1.15 <= ratio <= 2.1:
            quality += 5.0
            reasons.append("gut als Titelbild geeignet")
        elif 0.75 <= ratio <= 2.8:
            quality += 2.0
        elif ratio >= 4.0 or ratio <= 0.25:
            quality -= 5.0
    else:
        quality -= 2.0
    if image.get("license") and image.get("source_url"):
        quality += 2.0
    if image.get("author"):
        quality += 0.5
    total = relevance + quality
    reason = "; ".join(reasons[:3]) or "repräsentatives Planungsbild"
    return relevance, quality, total, reason


def _image_similarity_key(image: dict[str, Any]) -> tuple[str, frozenset[str]]:
    provider = str(image.get("provider") or "").casefold()
    title_tokens = frozenset(_query_tokens(str(image.get("title") or image.get("alt") or "")))
    return provider, title_tokens


def _too_similar(
    candidate: dict[str, Any],
    selected: list[dict[str, Any]],
) -> bool:
    provider, tokens = _image_similarity_key(candidate)
    if not tokens:
        return False
    for existing in selected:
        existing_provider, existing_tokens = _image_similarity_key(existing)
        overlap = len(tokens & existing_tokens) / max(1, min(len(tokens), len(existing_tokens)))
        if overlap >= 0.82 and (provider == existing_provider or overlap >= 0.95):
            return True
    return False


def _deduplicate_and_rank(
    images: list[dict[str, Any]],
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    tokens = _query_tokens(query)
    seen: set[str] = set()
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for index, image in enumerate(images):
        identity = str(
            image.get("source_url")
            or image.get("original_url")
            or image.get("image_url")
            or ""
        ).casefold()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        candidate = dict(image)
        relevance, quality, total, reason = _selection_scores(candidate, tokens)
        candidate.update(
            {
                "relevance_score": round(relevance, 2),
                "quality_score": round(quality, 2),
                "selection_score": round(total, 2),
                "selection_reason": reason,
                # Keep the legacy score for older panel consumers.
                "score": round(total, 2),
            }
        )
        ranked.append((float(total), index, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for _score, _index, candidate in ranked:
        if _too_similar(candidate, selected):
            deferred.append(candidate)
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        selected.extend(deferred[: limit - len(selected)])
    for rank, candidate in enumerate(selected, start=1):
        candidate["rank"] = rank
    return selected[:limit]


@dataclass(slots=True)
class _CacheEntry:
    created_at: float
    result: dict[str, Any]


class DestinationImageProvider:
    """Search several safe image sources and return one normalized contract."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: dict[tuple[str, int, float | None, float | None], _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def _commons_request(self, params: dict[str, str]) -> Any:
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": (
                "HomeAssistant-Roadplanner/"
                f"{INTEGRATION_VERSION} (destination image search)"
            )
        }
        async with session.get(
            _COMMONS_API,
            params=params,
            headers=headers,
            timeout=ClientTimeout(total=12),
        ) as response:
            response.raise_for_status()
            return await response.json(content_type=None)

    async def _search_commons(
        self,
        query: str,
        *,
        latitude: float | None,
        longitude: float | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        requests: list[tuple[bool, Any]] = []
        if latitude is not None and longitude is not None:
            requests.append((
                True,
                self._commons_request({
                    "action": "query",
                    "format": "json",
                    "formatversion": "2",
                    "generator": "geosearch",
                    "ggsprimary": "all",
                    "ggsnamespace": "6",
                    "ggsradius": "10000",
                    "ggslimit": str(min(limit, _PROVIDER_RESULT_LIMIT)),
                    "ggscoord": f"{latitude:.7f}|{longitude:.7f}",
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata|size",
                    "iiurlwidth": "1280",
                }),
            ))
        requests.append((
            False,
            self._commons_request({
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "generator": "search",
                "gsrsearch": f"{query} filetype:bitmap",
                "gsrnamespace": "6",
                "gsrlimit": str(min(limit, _PROVIDER_RESULT_LIMIT)),
                "prop": "imageinfo",
                "iiprop": "url|mime|extmetadata|size",
                "iiurlwidth": "1280",
            }),
        ))
        responses = await asyncio.gather(
            *(request for _, request in requests),
            return_exceptions=True,
        )
        results: list[dict[str, Any]] = []
        failures = 0
        for (proximity, _), response in zip(requests, responses, strict=True):
            if isinstance(response, BaseException):
                failures += 1
                continue
            results.extend(
                _parse_commons_response(
                    response,
                    limit=limit,
                    proximity=proximity,
                )
            )
        if failures == len(responses):
            raise ValidationError("Wikimedia Commons ist vorübergehend nicht erreichbar")
        return results

    async def _search_openverse(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": (
                "HomeAssistant-Roadplanner/"
                f"{INTEGRATION_VERSION} (Openverse destination image search)"
            )
        }
        params = {
            "q": query[:200],
            "page_size": str(min(limit, _PROVIDER_RESULT_LIMIT)),
            "mature": "false",
            "category": "photograph",
            "size": "large,medium",
        }
        async with session.get(
            _OPENVERSE_API,
            params=params,
            headers=headers,
            timeout=ClientTimeout(total=12),
        ) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
        return _parse_openverse_response(payload, limit=limit)

    async def async_search(
        self,
        query: str,
        *,
        limit: int = 8,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> dict[str, Any]:
        """Search Wikimedia Commons and Openverse with fail-open semantics."""
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
        safe_latitude = (
            round(float(latitude), 5)
            if isinstance(latitude, (int, float))
            and not isinstance(latitude, bool)
            and -90 <= float(latitude) <= 90
            else None
        )
        safe_longitude = (
            round(float(longitude), 5)
            if isinstance(longitude, (int, float))
            and not isinstance(longitude, bool)
            and -180 <= float(longitude) <= 180
            else None
        )
        cache_key = (query.casefold(), limit, safe_latitude, safe_longitude)

        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and monotonic() - cached.created_at < _CACHE_SECONDS:
                return {**cached.result, "cached": True}

        provider_calls = {
            "wikimedia_commons": self._search_commons(
                query,
                latitude=safe_latitude,
                longitude=safe_longitude,
                limit=max(limit, 8),
            ),
            "openverse": self._search_openverse(query, limit=max(limit, 8)),
        }
        provider_names = list(provider_calls)
        responses = await asyncio.gather(
            *(provider_calls[name] for name in provider_names),
            return_exceptions=True,
        )
        images: list[dict[str, Any]] = []
        errors: dict[str, str] = {}
        provider_counts: dict[str, int] = {}
        for name, response in zip(provider_names, responses, strict=True):
            if isinstance(response, BaseException):
                if isinstance(response, asyncio.CancelledError):
                    raise response
                if isinstance(response, (ClientError, asyncio.TimeoutError, ValueError, ValidationError)):
                    errors[name] = str(response)[:500] or "Bildquelle nicht erreichbar"
                else:
                    _LOGGER.warning(
                        "Destination image provider %s failed: %s",
                        name,
                        type(response).__name__,
                    )
                    errors[name] = "Bildquelle ist vorübergehend fehlgeschlagen"
                continue
            provider_counts[name] = len(response)
            images.extend(response)

        results = _deduplicate_and_rank(images, query=query, limit=limit)
        result = {
            "query": query,
            "provider": "multi",
            "providers": provider_counts,
            "provider_errors": errors,
            "cached": False,
            "count": len(results),
            "results": results,
        }
        async with self._lock:
            self._cache[cache_key] = _CacheEntry(monotonic(), result)
        return result
