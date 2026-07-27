"""Optional Google Places (New) fallback for Roadplanner destination search.

The place-resolution client deliberately requests only the identity, address,
type and location fields required for reviewed place enrichment. It never
exposes the API key to the browser and never logs it. Photo search
(`async_search_photos`) is a separate, off-by-default opt-in path with its own
quota - see docs/product/EXTERNAL_SERVICES_AND_PRIVACY.md for the data-flow
and attribution requirements that come with it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
import logging
import math
import time
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .geocoding import (
    GeocodingCandidate,
    GeocodingError,
    StructuredAddress,
    _category_intent_for_query,
    _category_matches,
    _core_query_tokens,
    _country_codes_for_query,
    _normalized_tokens,
    _structured_match_details,
)
from .roadplanner import ValidationError

_LOGGER = logging.getLogger(__name__)

_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.shortFormattedAddress",
        "places.location",
        "places.addressComponents",
        "places.primaryType",
        "places.primaryTypeDisplayName",
        "places.types",
        "places.googleMapsUri",
        "places.businessStatus",
    )
)
# Deliberately separate from _FIELD_MASK: photo data is a different, more
# expensive part of the place record and must only be requested by the
# opt-in destination-photo search path, never by the geocoding search above.
_PHOTOS_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.googleMapsUri",
        "places.photos",
    )
)
_MAX_CACHE = 250
_MAX_RESULTS = 5
_CACHE_TTL_SECONDS = 5 * 60
_MAX_PHOTOS_PER_PLACE = 3
_PHOTO_MEDIA_MAX_WIDTH_PX = 1600

_GOOGLE_TYPE_FILTERS = {
    "parking": "parking",
    "camping": "campground",
    "restaurant": "restaurant",
    "ferry": "ferry_terminal",
    "transport": "transit_station",
    "hiking": "hiking_area",
    "nature_center": "visitor_center",
    "attraction": "tourist_attraction",
    "retail": "store",
    "fuel": "gas_station",
    "charging": "electric_vehicle_charging_station",
    "accommodation": "lodging",
}

_GOOGLE_CATEGORY_BY_TYPE = {
    "campground": "tourism",
    "rv_park": "tourism",
    "ferry_terminal": "amenity",
    "transit_station": "public_transport",
    "train_station": "railway",
    "bus_station": "amenity",
    "airport": "aeroway",
    "hiking_area": "route",
    "visitor_center": "tourism",
    "tourist_attraction": "tourism",
    "historical_landmark": "historic",
    "museum": "tourism",
    "restaurant": "amenity",
    "cafe": "amenity",
    "store": "shop",
    "shopping_mall": "shop",
    "supermarket": "shop",
    "sporting_goods_store": "shop",
    "parking": "amenity",
    "gas_station": "amenity",
    "electric_vehicle_charging_station": "amenity",
    "lodging": "tourism",
    "hotel": "tourism",
}

_ADDRESS_COMPONENT_KEYS = {
    "street_number": "house_number",
    "route": "road",
    "postal_code": "postcode",
    "locality": "city",
    "postal_town": "town",
    "administrative_area_level_1": "state",
    "administrative_area_level_2": "county",
    "administrative_area_level_3": "municipality",
    "sublocality": "suburb",
    "sublocality_level_1": "suburb",
    "neighborhood": "neighbourhood",
    "country": "country",
}


def _https_url_or_none(value: Any) -> str | None:
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


@dataclass(slots=True)
class GooglePlacesStatus:
    """Sanitized runtime status suitable for diagnostics."""

    enabled: bool
    configured: bool
    state: str
    requests_today: int
    daily_limit: int
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "state": self.state,
            "requests_today": self.requests_today,
            "daily_limit": self.daily_limit,
            "last_error": self.last_error,
        }


class GooglePlacesClient:
    """Rate-limited client for Google Places Text Search (New)."""

    def __init__(
        self,
        hass: Any,
        *,
        api_key: str,
        enabled: bool = True,
        daily_limit: int = 50,
        request_timeout: int = 25,
        min_interval_seconds: float = 0.2,
        photos_enabled: bool = False,
        photos_daily_limit: int = 10,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self.enabled = bool(enabled and self._api_key)
        self.configured = bool(self._api_key)
        self._daily_limit = max(0, min(int(daily_limit), 1_000))
        self._request_timeout = max(5, min(int(request_timeout), 60))
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._session: ClientSession = async_get_clientsession(hass)
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._counter_date = date.today()
        self._requests_today = 0
        self._last_error: str | None = None
        self._cache: dict[
            tuple[str, str, str, str, str],
            tuple[
                float,
                tuple[GeocodingCandidate | None, list[GeocodingCandidate]],
            ],
        ] = {}
        # Photos are a separately billed Google SKU (Places Photo media), so
        # they get their own opt-in flag and quota counter rather than
        # sharing the text-search budget above.
        self.photos_enabled = bool(photos_enabled and self._api_key)
        self._photos_daily_limit = max(0, min(int(photos_daily_limit), 200))
        self._photos_requests_today = 0
        self._photos_last_error: str | None = None

    @property
    def status(self) -> GooglePlacesStatus:
        self._roll_counter()
        if not self.configured:
            state = "not_configured"
        elif not self.enabled:
            state = "disabled"
        elif self._daily_limit == 0:
            state = "limit_disabled"
        elif self._requests_today >= self._daily_limit:
            state = "daily_limit_reached"
        elif self._last_error:
            state = "degraded"
        else:
            state = "ready"
        return GooglePlacesStatus(
            enabled=self.enabled,
            configured=self.configured,
            state=state,
            requests_today=self._requests_today,
            daily_limit=self._daily_limit,
            last_error=self._last_error,
        )

    def _roll_counter(self) -> None:
        today = date.today()
        if today != self._counter_date:
            self._counter_date = today
            self._requests_today = 0
            self._last_error = None
            self._photos_requests_today = 0
            self._photos_last_error = None

    def _reserve_request(self) -> None:
        self._roll_counter()
        if self._daily_limit <= 0:
            raise GeocodingError(
                "Google Places ist durch das Roadplanner-Tageslimit deaktiviert."
            )
        if self._requests_today >= self._daily_limit:
            raise GeocodingError(
                "Das Roadplanner-Tageslimit für Google Places ist erreicht."
            )
        self._requests_today += 1

    @property
    def photos_status(self) -> dict[str, Any]:
        self._roll_counter()
        if not self.configured:
            state = "not_configured"
        elif not self.photos_enabled:
            state = "disabled"
        elif self._photos_daily_limit == 0:
            state = "limit_disabled"
        elif self._photos_requests_today >= self._photos_daily_limit:
            state = "daily_limit_reached"
        elif self._photos_last_error:
            state = "degraded"
        else:
            state = "ready"
        return {
            "enabled": self.photos_enabled,
            "configured": self.configured,
            "state": state,
            "requests_today": self._photos_requests_today,
            "daily_limit": self._photos_daily_limit,
            "last_error": self._photos_last_error,
        }

    def _reserve_photo_request(self) -> None:
        self._roll_counter()
        if self._photos_daily_limit <= 0:
            raise GeocodingError(
                "Google-Fotos sind durch das Roadplanner-Tageslimit deaktiviert."
            )
        if self._photos_requests_today >= self._photos_daily_limit:
            raise GeocodingError(
                "Das Roadplanner-Tageslimit für Google-Fotos ist erreicht."
            )
        self._photos_requests_today += 1

    @staticmethod
    def _display_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("text") or "").strip()
        return str(value or "").strip()

    @classmethod
    def _address(cls, place: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for component in place.get("addressComponents") or []:
            if not isinstance(component, dict):
                continue
            long_text = str(component.get("longText") or "").strip()
            short_text = str(component.get("shortText") or "").strip()
            for component_type in component.get("types") or []:
                key = _ADDRESS_COMPONENT_KEYS.get(str(component_type))
                if not key or not long_text:
                    continue
                result.setdefault(key, long_text)
                if component_type == "country" and short_text:
                    result.setdefault("country_code", short_text.upper()[:2])
        return result

    @staticmethod
    def _location(place: dict[str, Any]) -> tuple[float, float] | None:
        location = place.get("location")
        if not isinstance(location, dict):
            return None
        latitude = location.get("latitude")
        longitude = location.get("longitude")
        if isinstance(latitude, bool) or isinstance(longitude, bool):
            return None
        if not isinstance(latitude, (int, float)) or not isinstance(
            longitude, (int, float)
        ):
            return None
        latitude = float(latitude)
        longitude = float(longitude)
        if not math.isfinite(latitude) or not math.isfinite(longitude):
            return None
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return None
        return latitude, longitude

    @staticmethod
    def _score(
        query: str,
        *,
        display_name: str,
        preferred_name: str,
        address: dict[str, Any],
        result_type: str,
        types: list[str],
        structured_address: StructuredAddress | None,
    ) -> tuple[
        float,
        float,
        bool | None,
        bool | None,
        str | None,
        dict[str, Any],
    ]:
        core_tokens = _core_query_tokens(query)
        result_tokens = _normalized_tokens(
            " ".join(
                (
                    preferred_name,
                    display_name,
                    " ".join(types),
                )
            )
        )
        core_match = (
            len(core_tokens & result_tokens) / len(core_tokens)
            if core_tokens
            else 0.0
        )
        query_tokens = _normalized_tokens(query)
        name_tokens = _normalized_tokens(preferred_name)
        exact_name_bonus = 0.08 if name_tokens and name_tokens <= query_tokens else 0.0

        expected_countries = _country_codes_for_query(query)
        country_code = str(address.get("country_code") or "").upper()
        country_match: bool | None = None
        if expected_countries:
            country_match = country_code in expected_countries

        category_intent = _category_intent_for_query(query)
        category = _GOOGLE_CATEGORY_BY_TYPE.get(result_type, "place")
        extratags = {"google_types": types}
        category_match = _category_matches(
            category_intent,
            category=category,
            result_type=result_type,
            extratags=extratags,
        )

        details = _structured_match_details(
            structured_address,
            address=address,
            result_type=result_type,
            extratags=extratags,
            category_intent=category_intent,
        )
        score = 0.12 + core_match * 0.62 + exact_name_bonus
        if country_match is True:
            score += 0.10
        elif country_match is False:
            score -= 0.30
        if category_match is True:
            score += 0.12
        elif category_match is False:
            score -= 0.14
        score += float(details.get("score_adjustment") or 0.0)
        return (
            max(0.0, min(1.0, score)),
            core_match,
            country_match,
            category_match,
            category_intent,
            details,
        )

    @classmethod
    def _candidate(
        cls,
        place: dict[str, Any],
        *,
        query: str,
        structured_address: StructuredAddress | None,
    ) -> GeocodingCandidate | None:
        location = cls._location(place)
        if location is None:
            return None
        place_id = str(place.get("id") or "").strip()
        preferred_name = cls._display_text(place.get("displayName"))
        display_name = str(
            place.get("formattedAddress")
            or place.get("shortFormattedAddress")
            or preferred_name
        ).strip()
        if not place_id or not display_name:
            return None
        address = cls._address(place)
        primary_type = str(place.get("primaryType") or "place").strip().casefold()
        types = [
            str(value).strip().casefold()
            for value in place.get("types") or []
            if str(value).strip()
        ]
        if primary_type and primary_type not in types:
            types.insert(0, primary_type)
        (
            score,
            core_match,
            country_match,
            category_match,
            category_intent,
            match_details,
        ) = cls._score(
            query,
            display_name=display_name,
            preferred_name=preferred_name,
            address=address,
            result_type=primary_type,
            types=types,
            structured_address=structured_address,
        )
        match_type = str(match_details.get("match_type") or "poi")
        auto_selectable = bool(match_details.get("auto_selectable"))
        if structured_address is None or not structured_address.has_address_detail:
            match_type = "poi" if category_match is True or primary_type != "place" else "generic"
            auto_selectable = (
                score >= 0.70
                and core_match >= 0.45
                and country_match is not False
                and category_match is not False
            )
        category = _GOOGLE_CATEGORY_BY_TYPE.get(primary_type, "place")
        namedetails = {"name": preferred_name}
        extratags: dict[str, Any] = {
            "google_types": types,
            "business_status": str(place.get("businessStatus") or ""),
        }
        return GeocodingCandidate(
            display_name=display_name[:1_000],
            latitude=location[0],
            longitude=location[1],
            importance=0.5,
            score=score,
            osm_type="",
            osm_id=None,
            category=category,
            result_type=primary_type or "place",
            address=address,
            namedetails=namedetails,
            extratags=extratags,
            core_token_match=core_match,
            country_match=country_match,
            category_match=category_match,
            category_intent=category_intent,
            resolution_mode="search",
            search_variant="google_text_search",
            match_type=match_type,
            match_label=str(match_details.get("match_label") or "Google Places POI"),
            auto_selectable=auto_selectable,
            street_match=match_details.get("street_match"),
            house_number_match=match_details.get("house_number_match"),
            postal_code_match=match_details.get("postal_code_match"),
            city_match=match_details.get("city_match"),
            district_match=match_details.get("district_match"),
            address_mismatches=tuple(match_details.get("mismatches") or ()),
            provider="google_places",
            provider_id=place_id,
            provider_source_url=str(place.get("googleMapsUri") or "").strip()
            or f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}",
            provider_attribution="Google Maps",
        )

    async def _request(
        self,
        body: dict[str, Any],
        *,
        field_mask: str = _FIELD_MASK,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {}
        async with self._lock:
            wait_for = self._min_interval - (time.monotonic() - self._last_request)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._reserve_request()
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self._api_key,
                "X-Goog-FieldMask": field_mask,
            }
            try:
                async with asyncio.timeout(self._request_timeout):
                    response = await self._session.post(
                        _TEXT_SEARCH_URL,
                        json=body,
                        headers=headers,
                        allow_redirects=False,
                    )
                    async with response:
                        self._last_request = time.monotonic()
                        if response.status == 429:
                            raise GeocodingError(
                                "Google Places hat das Anfragekontingent vorübergehend begrenzt."
                            )
                        if response.status in {401, 403}:
                            raise GeocodingError(
                                "Google Places hat den API-Schlüssel oder dessen Berechtigungen abgelehnt."
                            )
                        if response.status < 200 or response.status >= 300:
                            raise GeocodingError(
                                f"Google Places ist mit HTTP {response.status} fehlgeschlagen."
                            )
                        payload = await response.json(content_type=None)
                        if not isinstance(payload, dict):
                            raise GeocodingError(
                                "Google Places hat ein ungültiges Ergebnis geliefert."
                            )
                        self._last_error = None
                        return payload
            except TimeoutError as err:
                self._last_error = "timeout"
                raise GeocodingError(
                    "Google Places hat nicht rechtzeitig geantwortet."
                ) from err
            except GeocodingError as err:
                self._last_error = str(err)[:300]
                raise
            except (ClientError, TypeError, ValueError) as err:
                self._last_error = type(err).__name__
                _LOGGER.debug("Google Places failure: %s", type(err).__name__)
                raise GeocodingError(
                    "Die Verbindung zu Google Places ist fehlgeschlagen."
                ) from err

    async def async_resolve(
        self,
        query: str,
        *,
        structured_address: StructuredAddress | None = None,
        language: str = "de",
        location_bias: tuple[float, float] | None = None,
    ) -> tuple[GeocodingCandidate | None, list[GeocodingCandidate]]:
        """Resolve one reviewed text query with Google Places Text Search."""
        if not self.enabled:
            return None, []
        query = " ".join(str(query or "").split())[:240]
        if len(query) < 3:
            raise ValidationError("Für Google Places fehlt eine eindeutige Suchanfrage")
        region_code = ""
        if structured_address is not None:
            region_code = str(structured_address.country_code or "").upper()
        if not region_code:
            countries = sorted(_country_codes_for_query(query))
            if len(countries) == 1:
                region_code = countries[0]
        category_intent = _category_intent_for_query(query)
        included_type = _GOOGLE_TYPE_FILTERS.get(category_intent or "", "")
        normalized_bias: tuple[float, float] | None = None
        if location_bias is not None:
            latitude, longitude = location_bias
            if (
                not isinstance(latitude, bool)
                and not isinstance(longitude, bool)
                and isinstance(latitude, (int, float))
                and isinstance(longitude, (int, float))
                and math.isfinite(float(latitude))
                and math.isfinite(float(longitude))
                and -90 <= float(latitude) <= 90
                and -180 <= float(longitude) <= 180
            ):
                normalized_bias = (round(float(latitude), 4), round(float(longitude), 4))
        cache_key = (
            query.casefold(),
            language.casefold(),
            region_code,
            included_type,
            str(normalized_bias or ""),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            expires_at, cached_result = cached
            if time.monotonic() < expires_at:
                return cached_result
            self._cache.pop(cache_key, None)

        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": _MAX_RESULTS,
            "languageCode": language or "de",
        }
        if region_code:
            body["regionCode"] = region_code
        if included_type:
            body["includedType"] = included_type
            # A named place must remain discoverable even if Google classifies
            # it under a neighbouring type.  The returned type is still scored
            # and reviewed, therefore strict filtering is intentionally false.
            body["strictTypeFiltering"] = False
        if normalized_bias is not None:
            body["locationBias"] = {
                "circle": {
                    "center": {
                        "latitude": normalized_bias[0],
                        "longitude": normalized_bias[1],
                    },
                    "radius": 50_000.0,
                }
            }

        payload = await self._request(body)
        candidates = [
            candidate
            for place in payload.get("places") or []
            if isinstance(place, dict)
            for candidate in [
                self._candidate(
                    place,
                    query=query,
                    structured_address=structured_address,
                )
            ]
            if candidate is not None
        ]
        candidates.sort(
            key=lambda candidate: (
                int(candidate.auto_selectable),
                candidate.score,
                candidate.core_token_match,
            ),
            reverse=True,
        )
        best = candidates[0] if candidates else None
        if best is not None:
            if best.country_match is False:
                best = None
            elif structured_address is not None and structured_address.has_address_detail:
                if structured_address.house_number:
                    if best.match_type not in {"house_exact", "house_interpolated"}:
                        best = None
                elif structured_address.street and best.match_type not in {
                    "street",
                    "house_exact",
                    "house_interpolated",
                }:
                    best = None
            elif not best.auto_selectable:
                best = None

        result = (best, candidates[:_MAX_RESULTS])
        self._cache[cache_key] = (
            time.monotonic() + _CACHE_TTL_SECONDS,
            result,
        )
        if len(self._cache) > _MAX_CACHE:
            self._cache.pop(next(iter(self._cache)), None)
        return result

    async def async_reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str = "de",
    ) -> GeocodingCandidate | None:
        """Google is not used for reverse geocoding in this integration."""
        return None

    async def async_resolve_photo_url(self, photo_name: str) -> str | None:
        """Resolve a durable photo resource name to a fresh, directly linkable URI.

        Public entry point for callers that only hold the durable
        ``photo_name`` (e.g. a saved destination gallery) and need a live
        URL to redirect a browser to - as opposed to `async_search_photos`,
        which discovers photo names via a fresh text search. Each call
        still spends one entry of the separate photos daily quota.
        """
        return await self._resolve_photo_media(photo_name)

    async def _resolve_photo_media(self, photo_name: str) -> str | None:
        """Resolve a photo resource name to a short-lived, directly linkable URI.

        Each call spends one entry of the separate photos daily quota - the
        media endpoint is billed independently from the text search above.
        """
        if not self.photos_enabled:
            return None
        try:
            async with self._lock:
                self._reserve_photo_request()
        except GeocodingError as err:
            self._photos_last_error = str(err)[:300]
            return None
        try:
            async with asyncio.timeout(self._request_timeout):
                response = await self._session.get(
                    f"https://places.googleapis.com/v1/{photo_name}/media",
                    params={
                        "key": self._api_key,
                        "maxWidthPx": str(_PHOTO_MEDIA_MAX_WIDTH_PX),
                        "skipHttpRedirect": "true",
                    },
                    allow_redirects=False,
                )
                async with response:
                    if response.status < 200 or response.status >= 300:
                        self._photos_last_error = f"HTTP {response.status}"
                        return None
                    payload = await response.json(content_type=None)
                    if not isinstance(payload, dict):
                        return None
                    self._photos_last_error = None
                    return _https_url_or_none(payload.get("photoUri"))
        except TimeoutError:
            self._photos_last_error = "timeout"
            return None
        except (ClientError, TypeError, ValueError) as err:
            self._photos_last_error = type(err).__name__
            _LOGGER.debug("Google Places photo media failure: %s", type(err).__name__)
            return None

    async def async_search_photos(
        self,
        query: str,
        *,
        limit: int = 6,
        language: str = "de",
    ) -> list[dict[str, Any]]:
        """Return up to `limit` Google-sourced photos for a free-text query.

        This is an opt-in test feature (CONF_GOOGLE_PHOTOS_ENABLED). Returned
        entries carry the resource name (`photo_name`) and a freshly resolved,
        short-lived direct image URI (`photo_uri`) plus mandatory attribution
        text - Google does not guarantee `photo_uri` stays valid long-term,
        so callers must not assume it survives beyond the current request.
        """
        if not self.photos_enabled or not self.enabled:
            return []
        query = " ".join(str(query or "").split())[:240]
        if len(query) < 3:
            return []
        limit = max(1, min(int(limit), _MAX_RESULTS * _MAX_PHOTOS_PER_PLACE))
        try:
            payload = await self._request(
                {
                    "textQuery": query,
                    "pageSize": _MAX_RESULTS,
                    "languageCode": language or "de",
                },
                field_mask=_PHOTOS_FIELD_MASK,
            )
        except GeocodingError as err:
            _LOGGER.debug("Google Places photo search failed: %s", err)
            return []

        candidates: list[dict[str, Any]] = []
        for place in payload.get("places") or []:
            if not isinstance(place, dict):
                continue
            place_id = str(place.get("id") or "").strip()
            place_title = self._display_text(place.get("displayName"))
            maps_uri = str(place.get("googleMapsUri") or "").strip()
            photos = place.get("photos")
            if not place_id or not isinstance(photos, list):
                continue
            for photo in photos[:_MAX_PHOTOS_PER_PLACE]:
                if not isinstance(photo, dict):
                    continue
                photo_name = str(photo.get("name") or "").strip()
                if not photo_name:
                    continue
                author = ""
                attributions = photo.get("authorAttributions")
                if isinstance(attributions, list) and attributions:
                    first = attributions[0]
                    if isinstance(first, dict):
                        author = str(first.get("displayName") or "").strip()
                candidates.append(
                    {
                        "place_id": place_id,
                        "place_title": place_title,
                        "source_url": maps_uri
                        or f"https://www.google.com/maps/search/?api=1&query_place_id={place_id}",
                        "photo_name": photo_name,
                        "width": photo.get("widthPx"),
                        "height": photo.get("heightPx"),
                        "author": author,
                    }
                )
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

        results: list[dict[str, Any]] = []
        for candidate in candidates:
            photo_uri = await self._resolve_photo_media(candidate["photo_name"])
            if not photo_uri:
                continue
            results.append({**candidate, "photo_uri": photo_uri})
        return results


__all__ = ["GooglePlacesClient", "GooglePlacesStatus"]
