"""Conservative OpenStreetMap Nominatim geocoding for confirmed stop proposals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import math
import re
import time
from typing import Any, Protocol
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
        "fahrterminal",
        "faehrterminal",
        "ferry",
        "terminal",
        "passenger",
        "port",
        "hiking",
        "trail",
        "wanderung",
        "wanderroute",
        "naturzentrum",
        "naturzentrum",
        "visitor",
        "center",
        "centre",
        "tourist",
        "attraction",
        "shopping",
        "shop",
        "store",
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
    (re.compile(r"(?iu)\bf(?:ä|ae)hrterminal\b"), "ferry terminal"),
    (re.compile(r"(?iu)\bf(?:ä|ae)hrhafen\b"), "ferry port"),
    (re.compile(r"(?iu)\bnaturzentrum\b"), "nature center"),
    (re.compile(r"(?iu)\bbesucherzentrum\b"), "visitor center"),
    (re.compile(r"(?iu)\bwanderrunde\b"), "hiking trail"),
    (re.compile(r"(?iu)\bwander(?:ung|route)\b"), "hiking trail"),
    (re.compile(r"(?iu)\bsehensw(?:ü|ue)rdigkeit\b"), "tourist attraction"),
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
    "ferry": frozenset(
        {"fahre", "faehre", "fahrterminal", "faehrterminal", "ferry", "passenger"}
    ),
    "transport": frozenset(
        {"airport", "bahnhof", "busbahnhof", "station", "terminal"}
    ),
    "hiking": frozenset(
        {"hike", "hiking", "kierros", "rundweg", "trail", "wanderroute", "wanderung"}
    ),
    "nature_center": frozenset(
        {"besucherzentrum", "luontokeskus", "naturzentrum", "naturzentrum", "visitor"}
    ),
    "attraction": frozenset(
        {"attraction", "museum", "sehenswuerdigkeit", "sehenswurdigkeit", "viewpoint"}
    ),
    "retail": frozenset(
        {"einkauf", "mall", "shop", "shopping", "store", "supermarket", "supermarkt"}
    ),
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
    "ferry": frozenset({"ferry", "ferry_terminal", "harbour", "port", "terminal"}),
    "transport": frozenset(
        {"aerodrome", "airport", "bus_station", "railway", "station", "terminal"}
    ),
    "hiking": frozenset(
        {"footway", "hiking", "nature_reserve", "path", "route", "trail", "walking"}
    ),
    "nature_center": frozenset(
        {"education", "information", "museum", "nature_reserve", "visitor_centre", "visitor_center"}
    ),
    "attraction": frozenset(
        {"archaeological_site", "attraction", "castle", "historic", "memorial", "monument", "museum", "tourism", "viewpoint"}
    ),
    "retail": frozenset(
        {"department_store", "mall", "shop", "sports", "store", "supermarket"}
    ),
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

_MAX_SEARCH_VARIANTS = 3
_POSTAL_CODE_RE = re.compile(
    r"(?iu)(?<![\w-])(?:[A-Z]{1,2}[-\s]?)?(?P<postal>\d{4,6})(?![\w-])"
)
_POSTAL_LOCALITY_RE = re.compile(
    r"(?iu)^\s*(?:[A-Z]{1,2}[-\s]?)?(?P<postal>\d{4,6})\s+(?P<locality>.+?)\s*$"
)
_STREET_HOUSE_RE = re.compile(
    r"(?iu)^(?P<street>.+?)[\s,]+(?P<number>\d{1,5}[A-Z]?(?:\s*[-/]\s*\d{1,5}[A-Z]?)?)$"
)
_DISTRICT_LABEL_RE = re.compile(
    r"(?iu)\b(?:ortsteil|ot|district|borough|stadtteil)\s*[:\-]?\s*(?P<district>[^,;\n]+)"
)
_ADDRESS_LINE_SPLIT_RE = re.compile(r"[\r\n;]+")
_ADDRESS_SPACE_RE = re.compile(r"\s+")
_STATE_ALIASES = frozenset(
    {
        "baden wurttemberg",
        "bayern",
        "berlin",
        "brandenburg",
        "bremen",
        "hamburg",
        "hessen",
        "mecklenburg vorpommern",
        "niedersachsen",
        "nordrhein westfalen",
        "rheinland pfalz",
        "saarland",
        "sachsen",
        "sachsen anhalt",
        "schleswig holstein",
        "thuringen",
        "thueringen",
    }
)
_ADDRESS_ROAD_KEYS = (
    "road",
    "pedestrian",
    "residential",
    "footway",
    "path",
    "cycleway",
)
_ADDRESS_CITY_KEYS = (
    "city",
    "town",
    "village",
    "municipality",
    "hamlet",
)
_ADDRESS_DISTRICT_KEYS = (
    "city_district",
    "suburb",
    "quarter",
    "neighbourhood",
    "borough",
)
_MATCH_RANK = {
    "reverse": 70,
    "house_exact": 60,
    "house_interpolated": 55,
    "poi": 45,
    "street": 35,
    "locality": 20,
    "generic": 10,
    "mismatch": 0,
}


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
    # A translated category suffix often followed a hyphen in imported stop
    # names (for example "Haukkankierros-Wanderung"). Providers understand a
    # normal token boundary more reliably than the retained punctuation.
    result = re.sub(
        r"\s*[-–—]\s*(?=(?:hiking trail|tourist attraction|nature center|visitor center)\b)",
        " ",
        result,
        flags=re.IGNORECASE,
    )
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



def _component(value: Any, maximum: int = 300) -> str:
    """Return one whitespace-normalized address component."""
    return _ADDRESS_SPACE_RE.sub(" ", str(value or "").strip())[:maximum]


def _component_key(value: Any) -> str:
    return " ".join(_normalized_text(value).replace("-", " ").split())


def _first_address_value(address: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _component(address.get(key))
        if value:
            return value
    return ""


def _component_similarity(expected: str, actual: str) -> float:
    expected_key = _component_key(expected)
    actual_key = _component_key(actual)
    if not expected_key or not actual_key:
        return 0.0
    if expected_key == actual_key:
        return 1.0
    expected_tokens = set(expected_key.split())
    actual_tokens = set(actual_key.split())
    if not expected_tokens or not actual_tokens:
        return 0.0
    overlap = len(expected_tokens & actual_tokens) / len(expected_tokens)
    if expected_key in actual_key or actual_key in expected_key:
        overlap = max(overlap, 0.85)
    return max(0.0, min(1.0, overlap))


def _normalized_house_number(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", _normalized_text(value))


def _country_name_for_code(code: str) -> str:
    mapping = {
        "DE": "Deutschland",
        "DK": "Danmark",
        "EE": "Eesti",
        "FI": "Suomi",
        "LT": "Lietuva",
        "LV": "Latvija",
        "NO": "Norge",
        "PL": "Polska",
        "SE": "Sverige",
    }
    return mapping.get(str(code or "").upper(), "")


@dataclass(slots=True)
class StructuredAddress:
    """Bounded address components used for provider-specific searches.

    The structure contains only text supplied by the Roadbook or by an
    explicitly requested cleanup suggestion. It never contains coordinates.
    """

    street: str = ""
    house_number: str = ""
    postal_code: str = ""
    city: str = ""
    district: str = ""
    state: str = ""
    country: str = ""
    country_code: str = ""
    name: str = ""
    raw: str = ""

    @property
    def street_line(self) -> str:
        return " ".join(
            value for value in (self.street, self.house_number) if value
        ).strip()

    @property
    def has_address_detail(self) -> bool:
        return bool(self.street or self.postal_code or self.city or self.district)

    def as_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "street": self.street,
                "house_number": self.house_number,
                "postal_code": self.postal_code,
                "city": self.city,
                "district": self.district,
                "state": self.state,
                "country": self.country,
                "country_code": self.country_code,
                "name": self.name,
            }.items()
            if value
        }

    def merged(self, values: dict[str, Any] | None) -> "StructuredAddress":
        allowed = {
            "street",
            "house_number",
            "postal_code",
            "city",
            "district",
            "state",
            "country",
            "country_code",
            "name",
        }
        current = self.as_dict()
        for key, raw in (values or {}).items():
            if key not in allowed:
                continue
            value = _component(raw, 500)
            if key == "country_code":
                value = value.upper()[:2] if len(value) == 2 else ""
            if value:
                current[key] = value
        return StructuredAddress(raw=self.raw, **current)

    def full_query(self, fallback: str = "") -> str:
        parts: list[str] = []
        if self.name:
            parts.append(self.name)
        if self.street_line:
            parts.append(self.street_line)
        locality = ", ".join(
            value for value in (self.district, self.city) if value
        )
        if locality:
            parts.append(locality)
        if self.postal_code:
            parts.append(self.postal_code)
        if self.state:
            parts.append(self.state)
        if self.country:
            parts.append(self.country)
        elif self.country_code:
            parts.append(self.country_code)
        query = ", ".join(dict.fromkeys(value for value in parts if value))
        return query[:1_000] or _component(fallback, 1_000)


def parse_structured_address(
    *,
    address: Any = "",
    city: Any = "",
    district: Any = "",
    state: Any = "",
    country: Any = "",
    country_code: Any = "",
    label: Any = "",
    query: Any = "",
    name: Any = "",
) -> StructuredAddress:
    """Extract conservative structured components from Roadbook text.

    Explicit fields win. Heuristics are used only to split already supplied
    text; they do not add external facts. In particular, a locality repeated
    before a street name is treated as a district rather than as part of the
    road name.
    """

    explicit_city = _component(city)
    explicit_district = _component(district)
    explicit_state = _component(state)
    explicit_country = _component(country)
    code = _component(country_code, 10).upper()
    if len(code) != 2 or not code.isalpha():
        code = ""

    source_values: list[str] = []
    address_like_values: list[str] = []
    for source_name, raw in (("address", address), ("query", query), ("label", label)):
        value = str(raw or "").strip()
        if not value:
            continue
        if value.casefold() not in {item.casefold() for item in source_values}:
            source_values.append(value)
        # A single free-text POI name such as "Fährterminal Tallinn" is not a
        # city. Explicit address/label fields always participate. A generic
        # aggregate query participates only when it contains real address
        # evidence (postal code, labelled district, or a street/house fragment),
        # not merely because UI fields were joined with commas.
        query_parts = [
            _component(item, 1_000)
            for item in re.split(r"[,;\r\n]+", value)
            if _component(item, 1_000)
        ]
        query_is_address_like = bool(
            _POSTAL_CODE_RE.search(value)
            or _DISTRICT_LABEL_RE.search(value)
            or any(_STREET_HOUSE_RE.fullmatch(item) for item in query_parts)
        )
        if source_name in {"address", "label"} or query_is_address_like:
            if value.casefold() not in {
                item.casefold() for item in address_like_values
            }:
                address_like_values.append(value)
    raw_text = "\n".join(source_values)[:3_000]
    address_like_text = "\n".join(address_like_values)[:3_000]
    name_value = _component(name, 500)

    def split_groups(value: str) -> tuple[list[list[str]], list[str]]:
        groups: list[list[str]] = []
        values: list[str] = []
        for raw_line in _ADDRESS_LINE_SPLIT_RE.split(value):
            clean_line = _component(raw_line, 1_000)
            if not clean_line:
                continue
            group = [_component(item) for item in clean_line.split(",")]
            group = [item for item in group if item]
            if group:
                groups.append(group)
                values.extend(group)
        return groups, values

    line_groups, parts = split_groups(raw_text)
    address_line_groups, address_parts = split_groups(address_like_text)

    postal_code = ""
    for value in [raw_text, *parts]:
        match = _POSTAL_CODE_RE.search(value)
        if match:
            postal_code = _component(match.group("postal"), 20)
            break

    if not code:
        query_tokens = _normalized_tokens(raw_text)
        matching_codes = [
            candidate_code
            for candidate_code, aliases in _COUNTRY_ALIASES.items()
            if query_tokens & aliases
        ]
        if len(matching_codes) == 1:
            code = matching_codes[0]
    if not explicit_country:
        for value in reversed(parts):
            key_tokens = _normalized_tokens(value)
            matched = [
                candidate_code
                for candidate_code, aliases in _COUNTRY_ALIASES.items()
                if key_tokens & aliases
            ]
            if matched:
                explicit_country = value
                if not code and len(set(matched)) == 1:
                    code = matched[0]
                break
    if not explicit_country and code:
        explicit_country = _country_name_for_code(code)

    district_match = _DISTRICT_LABEL_RE.search(raw_text)
    if not explicit_district and district_match:
        explicit_district = _component(district_match.group("district"))

    # Postal fragments such as "01844 Neustadt in Sachsen" are much stronger
    # locality evidence than a trailing category or labelled district. Keep the
    # postal code separate and use only the remaining text as the city.
    if not explicit_city:
        for value in address_parts:
            match = _POSTAL_LOCALITY_RE.fullmatch(value)
            if not match:
                continue
            locality = _component(match.group("locality"), 500)
            if not locality:
                continue
            if _DISTRICT_LABEL_RE.fullmatch(locality):
                continue
            if _component_key(locality) in _STATE_ALIASES:
                continue
            if _country_codes_for_query(locality):
                continue
            explicit_city = locality
            break

    if not explicit_state:
        for value in reversed(parts):
            if _component_key(value) in _STATE_ALIASES:
                explicit_state = value
                break

    # A two-part address line commonly contains "district, city".  Free-text
    # POI names are deliberately excluded from this inference.
    if not explicit_city or not explicit_district:
        for group in address_line_groups:
            if len(group) < 2:
                continue
            first, second = group[0], group[1]
            if _POSTAL_CODE_RE.search(first) or _POSTAL_CODE_RE.search(second):
                continue
            if _component_key(second) in _STATE_ALIASES:
                continue
            if _country_codes_for_query(f"{first} {second}"):
                continue
            if _STREET_HOUSE_RE.fullmatch(" ".join(group)):
                continue
            if not explicit_district:
                explicit_district = first
            if not explicit_city:
                explicit_city = second
            if explicit_city and explicit_district:
                break

    street = ""
    house_number = ""
    street_source = ""
    street_candidates = [name_value, *address_parts]
    for group in address_line_groups:
        if len(group) == 1:
            street_candidates.append(group[0])
    for raw_candidate in street_candidates:
        match = _STREET_HOUSE_RE.fullmatch(_component(raw_candidate, 1_000))
        if not match:
            continue
        candidate_number = _component(match.group("number"), 30)
        # Do not confuse a postal-code-only fragment with a house number.
        if postal_code and _normalized_house_number(candidate_number) == _normalized_house_number(postal_code):
            continue
        street_source = _component(match.group("street"), 500)
        house_number = candidate_number
        break

    if street_source:
        locality_values = [explicit_district, explicit_city]
        locality_values.extend(parts)
        unique_localities = sorted(
            {value for value in locality_values if value},
            key=len,
            reverse=True,
        )
        street = street_source
        street_key = _component_key(street)
        for locality in unique_localities:
            locality_key = _component_key(locality)
            if not locality_key or locality_key == street_key:
                continue
            prefix = locality_key + " "
            if street_key.startswith(prefix):
                original_prefix_length = len(locality.split())
                words = street.split()
                if len(words) > original_prefix_length:
                    removed = " ".join(words[:original_prefix_length])
                    street = " ".join(words[original_prefix_length:])
                    if not explicit_district:
                        explicit_district = removed
                    break
        street = _component(street, 500)

    # If only one plausible address locality remains, use it as the city.
    # Never reinterpret a standalone POI name as a locality.
    if not explicit_city:
        for value in address_parts:
            if not value or value in {street_source, street, explicit_state, explicit_country}:
                continue
            if postal_code and postal_code in value:
                continue
            if _DISTRICT_LABEL_RE.search(value):
                continue
            if _country_codes_for_query(value):
                continue
            if _component_key(value) in _STATE_ALIASES:
                continue
            if _STREET_HOUSE_RE.fullmatch(value):
                continue
            explicit_city = value
            break

    return StructuredAddress(
        street=street,
        house_number=house_number,
        postal_code=postal_code,
        city=explicit_city,
        district=explicit_district,
        state=explicit_state,
        country=explicit_country,
        country_code=code,
        name=_component(name, 500),
        raw=raw_text,
    )


def _structured_match_details(
    request: StructuredAddress | None,
    *,
    address: dict[str, Any],
    result_type: str,
    extratags: dict[str, Any],
    category_intent: str | None,
) -> dict[str, Any]:
    if request is None or not request.has_address_detail:
        return {
            "score_adjustment": 0.0,
            "match_type": "poi" if category_intent else "generic",
            "match_label": "POI" if category_intent else "Ort",
            "auto_selectable": False,
            "street_match": None,
            "house_number_match": None,
            "postal_code_match": None,
            "city_match": None,
            "district_match": None,
            "mismatches": (),
        }

    candidate_street = _first_address_value(address, _ADDRESS_ROAD_KEYS)
    candidate_house = _component(address.get("house_number"), 50)
    candidate_postal = _component(address.get("postcode"), 50)
    candidate_city = _first_address_value(address, _ADDRESS_CITY_KEYS)
    candidate_district = _first_address_value(address, _ADDRESS_DISTRICT_KEYS)

    street_similarity = (
        _component_similarity(request.street, candidate_street)
        if request.street
        else 0.0
    )
    street_match: bool | None = None
    if request.street:
        street_match = street_similarity >= 0.8

    house_match: bool | None = None
    if request.house_number:
        house_match = bool(candidate_house) and (
            _normalized_house_number(request.house_number)
            == _normalized_house_number(candidate_house)
        )

    postal_match: bool | None = None
    if request.postal_code:
        postal_match = bool(candidate_postal) and (
            _component_key(request.postal_code) == _component_key(candidate_postal)
        )

    city_similarity = max(
        _component_similarity(request.city, candidate_city),
        _component_similarity(request.city, candidate_district),
    ) if request.city else 0.0
    city_match: bool | None = city_similarity >= 0.75 if request.city else None

    district_similarity = max(
        _component_similarity(request.district, candidate_district),
        _component_similarity(request.district, candidate_city),
    ) if request.district else 0.0
    district_match: bool | None = (
        district_similarity >= 0.75 if request.district else None
    )

    mismatches: list[str] = []
    adjustment = 0.0
    if street_match is True:
        adjustment += 0.18
    elif street_match is False and candidate_street:
        adjustment -= 0.24
        mismatches.append("street")
    if house_match is True:
        adjustment += 0.24
    elif house_match is False and candidate_house:
        adjustment -= 0.35
        mismatches.append("house_number")
    if postal_match is True:
        adjustment += 0.12
    elif postal_match is False and candidate_postal:
        adjustment -= 0.20
        mismatches.append("postal_code")
    if city_match is True:
        adjustment += 0.08
    elif city_match is False and candidate_city:
        adjustment -= 0.10
        mismatches.append("city")
    if district_match is True:
        adjustment += 0.06

    interpolation = bool(
        extratags.get("addr:interpolation")
        or "interpolation" in _normalized_text(result_type)
    )
    if request.house_number:
        if house_match is True and street_match is True:
            match_type = "house_interpolated" if interpolation else "house_exact"
            match_label = (
                "Hausnummer interpoliert" if interpolation else "Hausnummer exakt"
            )
        elif street_match is True:
            match_type = "street"
            match_label = "Straße ohne bestätigte Hausnummer"
        elif city_match is True or district_match is True:
            match_type = "locality"
            match_label = "Nur Ort oder Ortsteil"
        else:
            match_type = "mismatch"
            match_label = "Adressabweichung"
    elif request.street:
        if street_match is True:
            match_type = "street"
            match_label = "Straße"
        elif city_match is True or district_match is True:
            match_type = "locality"
            match_label = "Nur Ort oder Ortsteil"
        else:
            match_type = "mismatch"
            match_label = "Adressabweichung"
    elif category_intent:
        match_type = "poi"
        match_label = "POI"
    else:
        match_type = "locality" if city_match is True or district_match is True else "generic"
        match_label = "Ort/Ortsteil" if match_type == "locality" else "Ort"

    auto_selectable = False
    if match_type in {"house_exact", "house_interpolated"}:
        auto_selectable = postal_match is not False and city_match is not False
    elif match_type == "street" and not request.house_number:
        auto_selectable = postal_match is not False and city_match is not False

    return {
        "score_adjustment": adjustment,
        "match_type": match_type,
        "match_label": match_label,
        "auto_selectable": auto_selectable,
        "street_match": street_match,
        "house_number_match": house_match,
        "postal_code_match": postal_match,
        "city_match": city_match,
        "district_match": district_match,
        "mismatches": tuple(mismatches),
    }


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
    search_variant: str = "free_text"
    match_type: str = "generic"
    match_label: str = "Ort"
    auto_selectable: bool = False
    street_match: bool | None = None
    house_number_match: bool | None = None
    postal_code_match: bool | None = None
    city_match: bool | None = None
    district_match: bool | None = None
    address_mismatches: tuple[str, ...] = ()

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
            "search_variant": self.search_variant,
            "match_type": self.match_type,
            "match_label": self.match_label,
            "auto_selectable": self.auto_selectable,
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
        address_matches = {
            "street": self.street_match,
            "house_number": self.house_number_match,
            "postal_code": self.postal_code_match,
            "city": self.city_match,
            "district": self.district_match,
        }
        address_matches = {
            key: value for key, value in address_matches.items() if value is not None
        }
        if address_matches:
            result["address_matches"] = address_matches
        if self.address_mismatches:
            result["address_mismatches"] = list(self.address_mismatches)
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


class GeocodingProvider(Protocol):
    """Provider-neutral contract for reviewed place resolution."""

    enabled: bool

    async def async_resolve(
        self,
        query: str,
        *,
        structured_address: StructuredAddress | None = None,
        language: str = "de",
    ) -> tuple[GeocodingCandidate | None, list[GeocodingCandidate]]:
        """Return an optional safe default and all reviewable candidates."""
        ...

    async def async_reverse(
        self,
        latitude: float,
        longitude: float,
        *,
        language: str = "de",
    ) -> GeocodingCandidate | None:
        """Validate a user-supplied coordinate without changing it."""
        ...


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
        self._cache: dict[tuple[str, ...], list[GeocodingCandidate]] = {}
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

    @staticmethod
    def _candidate_sort_key(candidate: GeocodingCandidate) -> tuple[int, int, float, float]:
        return (
            _MATCH_RANK.get(candidate.match_type, 0),
            int(candidate.auto_selectable),
            candidate.score,
            candidate.importance,
        )

    @classmethod
    def _candidate_from_item(
        cls,
        item: dict[str, Any],
        *,
        query: str,
        structured_address: StructuredAddress | None = None,
        search_variant: str = "free_text",
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
        match_details = _structured_match_details(
            structured_address,
            address=address,
            result_type=result_type,
            extratags=extratags,
            category_intent=category_intent,
        )
        score = max(
            0.0,
            min(1.0, score + float(match_details["score_adjustment"])),
        )
        match_type = str(match_details["match_type"])
        match_label = str(match_details["match_label"])
        auto_selectable = bool(match_details["auto_selectable"])

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
            match_type = "reverse"
            match_label = "Bestätigte Koordinate"
            auto_selectable = True

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
            search_variant=search_variant,
            match_type=match_type,
            match_label=match_label,
            auto_selectable=auto_selectable,
            street_match=match_details["street_match"],
            house_number_match=match_details["house_number_match"],
            postal_code_match=match_details["postal_code_match"],
            city_match=match_details["city_match"],
            district_match=match_details["district_match"],
            address_mismatches=tuple(match_details["mismatches"]),
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

    @staticmethod
    def _search_variants(
        query: str,
        structured_address: StructuredAddress | None,
        *,
        language: str,
        limit: int,
    ) -> list[tuple[str, dict[str, str]]]:
        common = {
            "format": "jsonv2",
            "addressdetails": "1",
            "namedetails": "1",
            "extratags": "1",
            "dedupe": "1",
            "limit": str(max(limit, 5)),
            "accept-language": language,
        }
        expected_countries = sorted(
            set(_country_codes_for_query(query))
            | ({structured_address.country_code} if structured_address and structured_address.country_code else set())
        )
        if expected_countries:
            common["countrycodes"] = ",".join(
                code.casefold() for code in expected_countries
            )

        variants: list[tuple[str, dict[str, str]]] = []
        if structured_address is not None and structured_address.has_address_detail:
            structured_params = dict(common)
            if structured_address.street_line:
                structured_params["street"] = _provider_query(
                    structured_address.street_line
                )
            structured_city = structured_address.city or structured_address.district
            if structured_city:
                structured_params["city"] = structured_city
            if structured_address.state:
                structured_params["state"] = structured_address.state
            if structured_address.country:
                structured_params["country"] = structured_address.country
            if structured_address.postal_code:
                structured_params["postalcode"] = structured_address.postal_code
            if any(
                key in structured_params
                for key in ("street", "city", "state", "country", "postalcode")
            ):
                variants.append(("structured", structured_params))

            district_query = structured_address.full_query(query)
            if district_query:
                district_params = dict(common)
                district_params["q"] = _provider_query(district_query)
                variants.append(("district_text", district_params))

        free_text_params = dict(common)
        free_text_params["q"] = _provider_query(query)
        variants.append(("free_text", free_text_params))

        unique: list[tuple[str, dict[str, str]]] = []
        fingerprints: set[tuple[tuple[str, str], ...]] = set()
        for name, params in variants:
            fingerprint = tuple(sorted(params.items()))
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            unique.append((name, params))
            if len(unique) >= _MAX_SEARCH_VARIANTS:
                break
        return unique

    async def async_search(
        self,
        query: str,
        *,
        structured_address: StructuredAddress | None = None,
        language: str = "de",
        limit: int = 3,
    ) -> list[GeocodingCandidate]:
        if not self.enabled:
            return []
        query = " ".join(str(query or "").split())
        if len(query) < 3:
            raise ValidationError("Für die Ortssuche fehlt eine eindeutige Suchanfrage")
        limit = max(1, min(int(limit), 5))
        structured_key = tuple(
            str(value or "").casefold()
            for value in (
                structured_address.street if structured_address else "",
                structured_address.house_number if structured_address else "",
                structured_address.postal_code if structured_address else "",
                structured_address.city if structured_address else "",
                structured_address.district if structured_address else "",
                structured_address.state if structured_address else "",
                structured_address.country if structured_address else "",
                structured_address.country_code if structured_address else "",
                structured_address.name if structured_address else "",
            )
        )
        cache_key = (query.casefold(), language.casefold(), *structured_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached[:limit]

        variants = self._search_variants(
            query,
            structured_address,
            language=language,
            limit=limit,
        )
        candidates_by_key: dict[tuple[Any, ...], GeocodingCandidate] = {}
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached[:limit]
            for search_variant, params in variants:
                raw = await self._async_get_json_locked(
                    self._base_url,
                    params=params,
                )
                if not isinstance(raw, list):
                    raise GeocodingError(
                        "Die Ortssuche hat ein ungültiges Ergebnis geliefert."
                    )
                score_query = (
                    structured_address.full_query(query)
                    if structured_address is not None
                    else query
                )
                for item in raw:
                    candidate = self._candidate_from_item(
                        item,
                        query=score_query,
                        structured_address=structured_address,
                        search_variant=search_variant,
                        resolution_mode="search",
                    )
                    if candidate is None:
                        continue
                    candidate_key: tuple[Any, ...]
                    if candidate.osm_type and candidate.osm_id is not None:
                        candidate_key = (candidate.osm_type, candidate.osm_id)
                    else:
                        candidate_key = (
                            round(candidate.latitude, 6),
                            round(candidate.longitude, 6),
                            candidate.display_name.casefold(),
                        )
                    previous = candidates_by_key.get(candidate_key)
                    if previous is None or self._candidate_sort_key(
                        candidate
                    ) > self._candidate_sort_key(previous):
                        candidates_by_key[candidate_key] = candidate

                if any(
                    candidate.match_type == "house_exact"
                    and candidate.auto_selectable
                    and candidate.score >= 0.58
                    for candidate in candidates_by_key.values()
                ):
                    break

        candidates = list(candidates_by_key.values())
        candidates.sort(key=self._candidate_sort_key, reverse=True)
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
                search_variant="reverse",
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
        structured_address: StructuredAddress | None = None,
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

        candidates = await self.async_search(
            query,
            structured_address=structured_address,
            language=language,
            limit=5,
        )
        if not candidates:
            return None, []
        best = candidates[0]
        category_intent = _category_intent_for_query(query)

        # Explicit POI intent remains conservative: a surrounding locality must
        # never be accepted as the requested parking area, ferry terminal, etc.
        if category_intent and best.category_match is not True:
            category_candidates = [
                candidate
                for candidate in candidates
                if candidate.category_match is True
            ]
            if not category_candidates:
                return None, candidates
            category_candidates.sort(key=self._candidate_sort_key, reverse=True)
            best = category_candidates[0]

        if best.country_match is False:
            return None, candidates

        structured = structured_address
        if structured is not None and structured.has_address_detail:
            # With a requested house number only an exact or explicitly marked
            # interpolated house result may be preselected. Street and locality
            # fallbacks stay visible for deliberate review.
            if structured.house_number:
                if (
                    best.match_type not in {"house_exact", "house_interpolated"}
                    or not best.auto_selectable
                    or best.score < 0.58
                ):
                    return None, candidates
            elif structured.street:
                if (
                    best.match_type not in {
                        "street",
                        "house_exact",
                        "house_interpolated",
                    }
                    or not best.auto_selectable
                    or best.score < 0.52
                ):
                    return None, candidates
            elif not best.auto_selectable:
                return None, candidates
        else:
            # Preserve the pre-3.5 conservative free-text behaviour.
            if best.score < 0.50 or best.core_token_match < 0.50:
                return None, candidates

        second = next(
            (candidate for candidate in candidates if candidate is not best),
            None,
        )
        if second is not None:
            same_rank = _MATCH_RANK.get(second.match_type, 0) == _MATCH_RANK.get(
                best.match_type, 0
            )
            score_gap = best.score - second.score
            distance = _distance_meters(
                best.latitude,
                best.longitude,
                second.latitude,
                second.longitude,
            )
            far_apart = distance > 1_500
            if far_apart and same_rank and score_gap < 0.08 and best.score < 0.88:
                return None, candidates
        return best, candidates
