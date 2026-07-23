"""Conservative OpenStreetMap Nominatim geocoding for confirmed stop proposals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import re
import time
from typing import Any
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import INTEGRATION_VERSION
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]+", re.UNICODE)
_TOKEN_TRANSLATION = str.maketrans(
    {
        "Ł": "L",
        "ł": "l",
        "Ø": "O",
        "ø": "o",
        "Đ": "D",
        "đ": "d",
        "Þ": "Th",
        "þ": "th",
        "Ð": "D",
        "ð": "d",
        "ß": "ss",
    }
)

# Generic POI/category words are useful for the provider query, but they must
# not dilute the lexical confidence score for the actual place name. Country
# words are evaluated separately against the returned country code.
_GENERIC_QUERY_TOKENS = frozenset(
    {
        "amenity",
        "attraction",
        "camp",
        "camping",
        "campingplatz",
        "caravan",
        "carpark",
        "fahre",
        "faehre",
        "ferry",
        "hotel",
        "ladepunkt",
        "laden",
        "parking",
        "parkplatz",
        "parkplatze",
        "parkplaetze",
        "restaurant",
        "service",
        "stellplatz",
        "tankstelle",
        "unterkunft",
        "ubernachtung",
        "uebernachtung",
        "wohnmobilstellplatz",
    }
)

_COUNTRY_ALIASES: dict[str, frozenset[str]] = {
    "DE": frozenset({"de", "deutschland", "germany", "allemagne"}),
    "DK": frozenset({"dk", "danemark", "danmark", "denmark"}),
    "EE": frozenset({"ee", "eesti", "estland", "estonia"}),
    "FI": frozenset({"fi", "finland", "finnland", "suomi"}),
    "LT": frozenset({"lt", "lietuva", "litauen", "lithuania"}),
    "LV": frozenset({"latvia", "lettland", "lv", "latvija"}),
    "NO": frozenset({"no", "norge", "norway", "norwegen"}),
    "PL": frozenset({"pl", "poland", "polen", "polska"}),
    "SE": frozenset({"se", "schweden", "sverige", "sweden"}),
}
_COUNTRY_QUERY_TOKENS = frozenset(
    token for aliases in _COUNTRY_ALIASES.values() for token in aliases
)

_PROVIDER_WORD_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?iu)\bparkpl(?:a|ä)tz(?:e)?\b"), "parking"),
    (re.compile(r"(?iu)\bwohnmobilstellplatz\b"), "motorhome parking"),
    (re.compile(r"(?iu)\bstellplatz\b"), "motorhome parking"),
    (re.compile(r"(?iu)\bcampingplatz\b"), "camp site"),
    (re.compile(r"(?iu)\bf(?:ä|ae)hre\b"), "ferry terminal"),
    (re.compile(r"(?iu)\btankstelle\b"), "fuel station"),
    (re.compile(r"(?iu)\bladepunkt\b"), "charging station"),
    (re.compile(r"(?iu)\bpolen\b"), "Poland"),
    (re.compile(r"(?iu)\bfinnland\b"), "Finland"),
    (re.compile(r"(?iu)\bschweden\b"), "Sweden"),
    (re.compile(r"(?iu)\bnorwegen\b"), "Norway"),
    (re.compile(r"(?iu)\bd(?:ä|ae)nemark\b"), "Denmark"),
    (re.compile(r"(?iu)\bestland\b"), "Estonia"),
    (re.compile(r"(?iu)\blettland\b"), "Latvia"),
    (re.compile(r"(?iu)\blitauen\b"), "Lithuania"),
    (re.compile(r"(?iu)\bdeutschland\b"), "Germany"),
)

_CATEGORY_QUERY_TOKENS: dict[str, frozenset[str]] = {
    "parking": frozenset(
        {
            "carpark",
            "parking",
            "parkplatz",
            "parkplatze",
            "parkplaetze",
            "stellplatz",
            "wohnmobilstellplatz",
        }
    ),
    "camping": frozenset({"camp", "camping", "campingplatz", "caravan"}),
    "restaurant": frozenset({"restaurant"}),
    "ferry": frozenset({"fahre", "faehre", "ferry"}),
    "fuel": frozenset({"fuel", "tankstelle"}),
    "charging": frozenset({"charging", "ladepunkt"}),
    "accommodation": frozenset(
        {"accommodation", "hotel", "unterkunft", "ubernachtung", "uebernachtung"}
    ),
}
_CATEGORY_RESULT_TOKENS: dict[str, frozenset[str]] = {
    "parking": frozenset(
        {
            "car_park",
            "parking",
            "parking_entrance",
            "parking_space",
        }
    ),
    "camping": frozenset({"camp_site", "camping", "caravan_site"}),
    "restaurant": frozenset({"restaurant"}),
    "ferry": frozenset({"ferry", "ferry_terminal", "terminal"}),
    "fuel": frozenset({"fuel"}),
    "charging": frozenset({"charging_station"}),
    "accommodation": frozenset(
        {"alpine_hut", "guest_house", "hostel", "hotel", "motel", "shelter"}
    ),
}
_COORDINATE_LABEL_RE = re.compile(
    r"(?ix)\b(?:lat(?:itude)?|breitengrad|lon(?:gitude)?|lng|längengrad)\s*[:=]\s*"
)
_COORDINATE_DOT_PATTERNS = (
    re.compile(
        r"^\s*([+-]?\d{1,3}(?:\.\d+)?)\s*[,;]\s*([+-]?\d{1,3}(?:\.\d+)?)\s*$"
    ),
    re.compile(
        r"^\s*([+-]?\d{1,3}(?:\.\d+)?)\s+([+-]?\d{1,3}(?:\.\d+)?)\s*$"
    ),
)
_COORDINATE_COMMA_PATTERNS = (
    re.compile(
        r"^\s*([+-]?\d{1,3},\d+)\s*;\s*([+-]?\d{1,3},\d+)\s*$"
    ),
    re.compile(
        r"^\s*([+-]?\d{1,3},\d+)\s+([+-]?\d{1,3},\d+)\s*$"
    ),
)
_MAX_REVERSE_DISTANCE_METERS = 5_000.0


def _normalized_text(value: Any) -> str:
    """Return a comparison-friendly, accent-insensitive text value."""
    translated = str(value or "").translate(_TOKEN_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()


def _normalized_tokens(value: Any) -> set[str]:
    """Return normalized tokens for place and address comparisons."""
    return {
        token
        for token in _TOKEN_RE.findall(_normalized_text(value))
        if len(token) >= 2
    }


def _country_codes_for_query(query: str) -> set[str]:
    """Return country codes explicitly named in a user-facing query."""
    tokens = _normalized_tokens(query)
    return {
        code
        for code, aliases in _COUNTRY_ALIASES.items()
        if tokens & aliases
    }


def _category_intent_for_query(query: str) -> str | None:
    """Return the explicit POI category requested by a place query."""
    tokens = _normalized_tokens(query)
    for category, aliases in _CATEGORY_QUERY_TOKENS.items():
        if tokens & aliases:
            return category
    return None


def _core_query_tokens(query: str) -> set[str]:
    """Return the place-name tokens after generic category/country words."""
    tokens = _normalized_tokens(query)
    core = tokens - _GENERIC_QUERY_TOKENS - _COUNTRY_QUERY_TOKENS
    return core or tokens


def _provider_query(query: str) -> str:
    """Translate common German POI terms for a provider-friendly query.

    Proper names remain untouched. This is intentionally narrow: it improves
    queries such as ``Parkplatz Rąbka, Łeba, Polen`` without rewriting the
    actual destination name.
    """
    result = " ".join(str(query or "").split())
    for pattern, replacement in _PROVIDER_WORD_REPLACEMENTS:
        result = pattern.sub(replacement, result)
    return " ".join(result.split())


def _candidate_text(
    display_name: str,
    address: dict[str, Any],
    namedetails: dict[str, Any],
) -> str:
    """Build the searchable candidate text from provider response fields."""
    values: list[str] = [display_name]
    values.extend(str(value) for value in address.values() if value not in (None, ""))
    values.extend(
        str(value) for value in namedetails.values() if value not in (None, "")
    )
    return " ".join(values)


def _category_matches(
    intent: str | None,
    *,
    category: str,
    result_type: str,
    extratags: dict[str, Any],
) -> bool | None:
    """Return whether provider metadata satisfies an explicit POI intent."""
    if intent is None:
        return None
    values = [category, result_type]
    values.extend(
        str(value) for value in extratags.values() if value not in (None, "")
    )
    tokens = _normalized_tokens(" ".join(values))
    return bool(tokens & _CATEGORY_RESULT_TOKENS.get(intent, frozenset()))


class GeocodingError(RoadplannerError):
    """Raised for a sanitized geocoder failure."""


class GeocodingUrlValidationError(ValueError):
    """Raised when a configured geocoding endpoint is unsafe or malformed."""


def normalize_geocoding_url(value: str) -> str:
    """Return a normalized HTTPS Nominatim-compatible search endpoint."""
    raw = str(value or "").strip()
    if not raw or any(character.isspace() or ord(character) < 32 for character in raw):
        raise GeocodingUrlValidationError("Die Geocoding-URL enthält ungültige Zeichen")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as err:
        raise GeocodingUrlValidationError("Ungültiger Port in der Geocoding-URL") from err
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise GeocodingUrlValidationError("Die Geocoding-URL muss eine HTTPS-URL sein")
    if parsed.username or parsed.password:
        raise GeocodingUrlValidationError("Zugangsdaten sind in der Geocoding-URL nicht erlaubt")
    if parsed.query or parsed.fragment:
        raise GeocodingUrlValidationError("Query und Fragment sind in der Geocoding-URL nicht erlaubt")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError as err:
        raise GeocodingUrlValidationError("Ungültiger Hostname in der Geocoding-URL") from err
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit(("https", netloc, path, "", ""))


def derive_reverse_geocoding_url(search_url: str) -> str:
    """Derive the sibling Nominatim reverse endpoint from a search endpoint."""
    normalized = normalize_geocoding_url(search_url)
    parsed = urlsplit(normalized)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[-1].casefold() in {"search", "search.php"}:
        segments[-1] = "reverse"
    elif segments and segments[-1].casefold() in {"reverse", "reverse.php"}:
        segments[-1] = "reverse"
    else:
        segments.append("reverse")
    path = "/" + "/".join(segments)
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def parse_coordinate_pair(value: str) -> tuple[float, float] | None:
    """Parse a decimal latitude/longitude pair or return ``None``.

    Supported examples include ``52.9348, 14.8570``, ``52.9348 14.8570`` and
    decimal-comma forms separated by a semicolon or whitespace. Coordinates are
    interpreted in WGS84 latitude/longitude order.
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = _COORDINATE_LABEL_RE.sub("", raw.replace("°", ""))
    cleaned = cleaned.strip().strip("()[]{}")

    match: re.Match[str] | None = None
    decimal_comma = False
    for pattern in _COORDINATE_DOT_PATTERNS:
        match = pattern.fullmatch(cleaned)
        if match:
            break
    if match is None:
        for pattern in _COORDINATE_COMMA_PATTERNS:
            match = pattern.fullmatch(cleaned)
            if match:
                decimal_comma = True
                break
    if match is None:
        return None

    latitude_text, longitude_text = match.groups()
    if decimal_comma:
        latitude_text = latitude_text.replace(",", ".")
        longitude_text = longitude_text.replace(",", ".")
    try:
        latitude = float(latitude_text)
        longitude = float(longitude_text)
    except ValueError:
        return None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValidationError("GPS-Koordinaten müssen endliche Zahlen sein")
    if not -90 <= latitude <= 90:
        raise ValidationError(
            f"Breitengrad liegt außerhalb des gültigen Bereichs: {latitude}"
        )
    if not -180 <= longitude <= 180:
        raise ValidationError(
            f"Längengrad liegt außerhalb des gültigen Bereichs: {longitude}"
        )
    return latitude, longitude


def _distance_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the great-circle distance between two WGS84 points."""
    radius = 6_371_008.8
    phi_a = math.radians(latitude_a)
    phi_b = math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    haversine = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    return radius * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


@dataclass(slots=True)
class GeocodingCandidate:
    """Normalized geocoding result used by the assistant compiler."""

    display_name: str
    latitude: float
    longitude: float
    importance: float
    score: float
    osm_type: str
    osm_id: int | None
    category: str
    result_type: str
    address: dict[str, Any]
    namedetails: dict[str, Any]
    extratags: dict[str, Any]
    boundingbox: tuple[float, float, float, float] | None = None
    core_token_match: float = 0.0
    country_match: bool | None = None
    category_match: bool | None = None
    category_intent: str | None = None
    resolution_mode: str = "search"
    input_latitude: float | None = None
    input_longitude: float | None = None
    matched_latitude: float | None = None
    matched_longitude: float | None = None
    distance_meters: float | None = None

    @property
    def preferred_name(self) -> str:
        """Return the most useful provider name without replacing user labels."""
        for key in ("name:de", "name:en", "name", "official_name", "short_name"):
            value = str(self.namedetails.get(key) or "").strip()
            if value:
                return value[:500]
        return self.display_name.split(",", 1)[0].strip()[:500]

    @property
    def source_url(self) -> str:
        if self.osm_type in {"node", "way", "relation"} and self.osm_id:
            return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"
        return "https://www.openstreetmap.org/"

    def as_location(self) -> dict[str, Any]:
        address = self.address
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
            or ""
        )
        return {
            "label": self.display_name,
            "address": self.display_name,
            "city": str(city),
            "country_code": str(address.get("country_code") or "").upper(),
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    def as_provenance(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": "nominatim",
            "resolution_mode": self.resolution_mode,
            "display_name": self.display_name,
            "score": round(self.score, 4),
            "importance": round(self.importance, 4),
            "osm_type": self.osm_type,
            "osm_id": self.osm_id,
            "category": self.category,
            "result_type": self.result_type,
            "source_url": self.source_url,
            "attribution": "© OpenStreetMap contributors",
            "core_token_match": round(self.core_token_match, 4),
            "preferred_name": self.preferred_name,
        }
        contact = {
            "website": str(
                self.extratags.get("contact:website")
                or self.extratags.get("website")
                or self.extratags.get("url")
                or ""
            ).strip()[:1_000],
            "phone": str(
                self.extratags.get("contact:phone")
                or self.extratags.get("phone")
                or self.extratags.get("telephone")
                or ""
            ).strip()[:300],
            "email": str(
                self.extratags.get("contact:email")
                or self.extratags.get("email")
                or ""
            ).strip()[:500],
            "opening_hours": str(self.extratags.get("opening_hours") or "").strip()[:1_000],
            "wikidata": str(self.extratags.get("wikidata") or "").strip()[:100],
            "wikipedia": str(self.extratags.get("wikipedia") or "").strip()[:500],
        }
        contact = {key: value for key, value in contact.items() if value}
        if contact:
            result["contact"] = contact
        if self.boundingbox is not None:
            result["boundingbox"] = list(self.boundingbox)
        if self.country_match is not None:
            result["country_match"] = self.country_match
        if self.category_match is not None:
            result["category_match"] = self.category_match
        if self.category_intent:
            result["category_intent"] = self.category_intent
        if self.input_latitude is not None and self.input_longitude is not None:
            result["input_coordinates"] = {
                "latitude": self.input_latitude,
                "longitude": self.input_longitude,
            }
        if self.matched_latitude is not None and self.matched_longitude is not None:
            result["matched_coordinates"] = {
                "latitude": self.matched_latitude,
                "longitude": self.matched_longitude,
            }
        if self.distance_meters is not None:
            result["match_distance_m"] = round(self.distance_meters, 1)
        return result


class NominatimGeocoder:
    """Rate-limited, cached Nominatim search and reverse-geocoding client.

    It is called only after an explicit user action such as "Orte
    vervollständigen" or a review preparation. No background bulk geocoding is
    performed.
    """

    def __init__(
        self,
        hass: Any,
        *,
        enabled: bool = True,
        base_url: str = "https://nominatim.openstreetmap.org/search",
        min_interval_seconds: float = 1.05,
    ) -> None:
        self.enabled = bool(enabled)
        self._base_url = normalize_geocoding_url(base_url)
        self._reverse_url = derive_reverse_geocoding_url(self._base_url)
        self._session: ClientSession = async_get_clientsession(hass)
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._min_interval = max(1.0, float(min_interval_seconds))
        self._cache: dict[tuple[str, str], list[GeocodingCandidate]] = {}
        self._reverse_cache: dict[
            tuple[float, float, str], GeocodingCandidate | None
        ] = {}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return _normalized_tokens(value)

    @classmethod
    def _score(
        cls,
        query: str,
        candidate_text: str,
        importance: float,
        *,
        country_code: str,
        category: str,
        result_type: str,
        extratags: dict[str, Any],
    ) -> tuple[float, float, bool | None, bool | None, str | None]:
        core_tokens = _core_query_tokens(query)
        result_tokens = cls._tokens(candidate_text)
        core_match = (
            len(core_tokens & result_tokens) / len(core_tokens)
            if core_tokens
            else 0.0
        )

        expected_countries = _country_codes_for_query(query)
        normalized_country_code = str(country_code or "").strip().upper()
        country_match: bool | None = None
        if expected_countries:
            country_match = normalized_country_code in expected_countries

        category_intent = _category_intent_for_query(query)
        category_match = _category_matches(
            category_intent,
            category=category,
            result_type=result_type,
            extratags=extratags,
        )

        bounded_importance = max(0.0, min(importance, 1.0))
        score = core_match * 0.72 + bounded_importance * 0.08
        if core_match >= 0.999:
            score += 0.05
        if country_match is True:
            score += 0.10
        elif country_match is False:
            score -= 0.25
        if category_match is True:
            score += 0.10
        elif category_match is False:
            score -= 0.12
        return (
            max(0.0, min(1.0, score)),
            core_match,
            country_match,
            category_match,
            category_intent,
        )

    @staticmethod
    def _osm_id(item: dict[str, Any]) -> int | None:
        raw = item.get("osm_id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _boundingbox(value: Any) -> tuple[float, float, float, float] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            south, north, west, east = (float(item) for item in value)
        except (TypeError, ValueError):
            return None
        if not (-90 <= south <= 90 and -90 <= north <= 90):
            return None
        if not (-180 <= west <= 180 and -180 <= east <= 180):
            return None
        return south, north, west, east

    @classmethod
    def _candidate_from_item(
        cls,
        item: dict[str, Any],
        *,
        query: str,
        resolution_mode: str = "search",
        input_coordinates: tuple[float, float] | None = None,
    ) -> GeocodingCandidate | None:
        if not isinstance(item, dict) or item.get("error"):
            return None
        display_name = str(item.get("display_name") or "").strip()
        if not display_name:
            return None
        try:
            matched_latitude = float(item.get("lat"))
            matched_longitude = float(item.get("lon"))
            importance = float(item.get("importance") or 0.0)
        except (TypeError, ValueError):
            return None
        if not -90 <= matched_latitude <= 90 or not -180 <= matched_longitude <= 180:
            return None
        address = item.get("address") if isinstance(item.get("address"), dict) else {}
        namedetails = (
            item.get("namedetails")
            if isinstance(item.get("namedetails"), dict)
            else {}
        )
        extratags = (
            item.get("extratags")
            if isinstance(item.get("extratags"), dict)
            else {}
        )
        category = str(item.get("category") or item.get("class") or "")
        result_type = str(item.get("type") or "")

        input_latitude: float | None = None
        input_longitude: float | None = None
        distance: float | None = None
        latitude = matched_latitude
        longitude = matched_longitude
        (
            score,
            core_token_match,
            country_match,
            category_match,
            category_intent,
        ) = cls._score(
            query,
            _candidate_text(display_name, address, namedetails),
            importance,
            country_code=str(address.get("country_code") or ""),
            category=category,
            result_type=result_type,
            extratags=extratags,
        )
        if input_coordinates is not None:
            input_latitude, input_longitude = input_coordinates
            latitude = input_latitude
            longitude = input_longitude
            distance = _distance_meters(
                input_latitude,
                input_longitude,
                matched_latitude,
                matched_longitude,
            )
            score = 1.0
            core_token_match = 1.0
            country_match = None
            category_match = None
            category_intent = None

        return GeocodingCandidate(
            display_name=display_name[:1_000],
            latitude=latitude,
            longitude=longitude,
            importance=importance,
            score=score,
            osm_type=str(item.get("osm_type") or ""),
            osm_id=cls._osm_id(item),
            category=category,
            result_type=result_type,
            address=dict(address),
            namedetails=dict(namedetails),
            extratags=dict(extratags),
            boundingbox=cls._boundingbox(item.get("boundingbox")),
            core_token_match=core_token_match,
            country_match=country_match,
            category_match=category_match,
            category_intent=category_intent,
            resolution_mode=resolution_mode,
            input_latitude=input_latitude,
            input_longitude=input_longitude,
            matched_latitude=matched_latitude,
            matched_longitude=matched_longitude,
            distance_meters=distance,
        )

    async def _async_get_json_locked(
        self,
        url: str,
        *,
        params: dict[str, str],
        not_found_is_empty: bool = False,
    ) -> Any:
        wait_for = self._min_interval - (time.monotonic() - self._last_request)
        if wait_for > 0:
            await asyncio.sleep(wait_for)
        headers = {
            "User-Agent": f"HomeAssistant-Roadplanner/{INTEGRATION_VERSION} (user-triggered geocoding)",
            "Accept": "application/json",
        }
        try:
            async with asyncio.timeout(25):
                response = await self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    allow_redirects=False,
                )
                async with response:
                    self._last_request = time.monotonic()
                    if response.status == 429:
                        raise GeocodingError(
                            "Die Ortssuche ist momentan ausgelastet. Bitte später erneut versuchen."
                        )
                    if response.status == 404 and not_found_is_empty:
                        return None
                    if response.status < 200 or response.status >= 300:
                        raise GeocodingError(
                            f"Die Ortssuche ist mit HTTP {response.status} fehlgeschlagen."
                        )
                    return await response.json(content_type=None)
        except TimeoutError as err:
            raise GeocodingError("Die Ortssuche hat nicht rechtzeitig geantwortet.") from err
        except GeocodingError:
            raise
        except (ClientError, TypeError, ValueError) as err:
            _LOGGER.debug("Nominatim failure: %s", type(err).__name__)
            raise GeocodingError("Die Verbindung zur Ortssuche ist fehlgeschlagen.") from err

    async def async_search(
        self,
        query: str,
        *,
        language: str = "de",
        limit: int = 3,
    ) -> list[GeocodingCandidate]:
        if not self.enabled:
            return []
        query = " ".join(str(query or "").split())
        if len(query) < 3:
            raise ValidationError("Für die Ortssuche fehlt eine eindeutige Suchanfrage")
        limit = max(1, min(int(limit), 5))
        cache_key = (query.casefold(), language.casefold())
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached[:limit]

        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached[:limit]
            provider_query = _provider_query(query)
            expected_countries = sorted(_country_codes_for_query(query))
            params = {
                "q": provider_query,
                "format": "jsonv2",
                "addressdetails": "1",
                "namedetails": "1",
                "extratags": "1",
                "dedupe": "1",
                # Request a few additional candidates for reliable ranking,
                # while still returning only the caller-requested amount.
                "limit": str(max(limit, 5)),
                "accept-language": language,
            }
            if expected_countries:
                params["countrycodes"] = ",".join(
                    code.casefold() for code in expected_countries
                )
            raw = await self._async_get_json_locked(
                self._base_url,
                params=params,
            )

        if not isinstance(raw, list):
            raise GeocodingError("Die Ortssuche hat ein ungültiges Ergebnis geliefert.")
        candidates: list[GeocodingCandidate] = []
        for item in raw:
            candidate = self._candidate_from_item(
                item,
                query=query,
                resolution_mode="search",
            )
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda candidate: (candidate.score, candidate.importance), reverse=True)
        self._cache[cache_key] = candidates[:5]
        if len(self._cache) > 250:
            oldest_key = next(iter(self._cache))
            self._cache.pop(oldest_key, None)
        return candidates[:limit]

    async def async_reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str = "de",
    ) -> GeocodingCandidate | None:
        """Validate and enrich a supplied coordinate with reverse geocoding."""
        if not self.enabled:
            return None
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValidationError("GPS-Koordinaten liegen außerhalb des gültigen Bereichs")
        cache_key = (round(latitude, 7), round(longitude, 7), language.casefold())
        if cache_key in self._reverse_cache:
            return self._reverse_cache[cache_key]

        async with self._lock:
            if cache_key in self._reverse_cache:
                return self._reverse_cache[cache_key]
            raw = await self._async_get_json_locked(
                self._reverse_url,
                params={
                    "lat": f"{latitude:.8f}",
                    "lon": f"{longitude:.8f}",
                    "format": "jsonv2",
                    "addressdetails": "1",
                    "namedetails": "1",
                    "extratags": "1",
                    "zoom": "18",
                    "accept-language": language,
                },
                not_found_is_empty=True,
            )

        if raw is None or not isinstance(raw, dict) or raw.get("error"):
            candidate = None
        else:
            candidate = self._candidate_from_item(
                raw,
                query=f"{latitude:.8f}, {longitude:.8f}",
                resolution_mode="reverse",
                input_coordinates=(latitude, longitude),
            )
            if (
                candidate is not None
                and candidate.distance_meters is not None
                and candidate.distance_meters > _MAX_REVERSE_DISTANCE_METERS
            ):
                _LOGGER.debug(
                    "Reverse geocoding result rejected because it is %.0f m away",
                    candidate.distance_meters,
                )
                candidate = None
        self._reverse_cache[cache_key] = candidate
        if len(self._reverse_cache) > 250:
            oldest_key = next(iter(self._reverse_cache))
            self._reverse_cache.pop(oldest_key, None)
        return candidate

    async def async_resolve(
        self,
        query: str,
        *,
        language: str = "de",
    ) -> tuple[GeocodingCandidate | None, list[GeocodingCandidate]]:
        coordinates = parse_coordinate_pair(query)
        if coordinates is not None:
            best = await self.async_reverse(
                coordinates[0],
                coordinates[1],
                language=language,
            )
            return (best, [best]) if best is not None else (None, [])

        candidates = await self.async_search(query, language=language, limit=3)
        if not candidates:
            return None, []
        best = candidates[0]
        category_intent = _category_intent_for_query(query)

        # A query that explicitly asks for a POI category (for example a
        # parking area) must not silently resolve to only the surrounding town
        # or road. Provider-language translation and address-aware scoring make
        # the intended POI rank first, while this guard preserves the existing
        # conservative safety behaviour when no matching POI exists.
        if category_intent and best.category_match is not True:
            category_candidates = [
                candidate
                for candidate in candidates
                if candidate.category_match is True
            ]
            if not category_candidates:
                return None, candidates
            category_candidates.sort(
                key=lambda candidate: (candidate.score, candidate.importance),
                reverse=True,
            )
            best = category_candidates[0]

        if best.country_match is False:
            return None, candidates
        # Prefer a false negative over a confidently wrong map point. A low
        # score or a near tie is returned to the review UI as an open question
        # instead of silently selecting a coordinate.
        if best.score < 0.50 or best.core_token_match < 0.50:
            return None, candidates
        if len(candidates) > 1:
            ranked = [candidate for candidate in candidates if candidate is not best]
            second = ranked[0] if ranked else None
        else:
            second = None
        if second is not None:
            score_gap = best.score - second.score
            distance = _distance_meters(
                best.latitude,
                best.longitude,
                second.latitude,
                second.longitude,
            )
            far_apart = distance > 1_500
            if far_apart and score_gap < 0.08 and best.score < 0.82:
                return None, candidates
        return best, candidates
