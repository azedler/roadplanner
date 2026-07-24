"""Deterministic destination intent and bounded search planning.

The module turns incomplete Roadbook stop text into a small, reviewable search
plan.  It does not call external providers, invent coordinates, or mutate the
Roadbook.  Provider results remain subject to the existing place-enrichment
review flow.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable
import unicodedata
from urllib.parse import urlparse

_MAX_GEOCODING_QUERY_LENGTH = 240
_MAX_IMAGE_QUERY_LENGTH = 180
_MAX_QUERY_VARIANTS = 3
_URL_RE = re.compile(r"https://[^\s<>\]\[\)\(\"']+", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[\wÀ-ÖØ-öø-ÿ]+", re.UNICODE)

_COUNTRY_NAMES = {
    "DE": "Deutschland",
    "DK": "Danmark",
    "EE": "Estland",
    "FI": "Finnland",
    "LT": "Litauen",
    "LV": "Lettland",
    "NO": "Norwegen",
    "PL": "Polen",
    "SE": "Schweden",
}

_KIND_LABELS = {
    "address": "Adresse",
    "ferry_terminal": "Fährterminal",
    "transport_terminal": "Verkehrsterminal",
    "hike": "Wanderung",
    "nature_center": "Natur- oder Besucherzentrum",
    "attraction": "Sehenswürdigkeit",
    "retail": "Einkauf",
    "restaurant": "Gastronomie",
    "camping": "Camping- oder Übernachtungsplatz",
    "accommodation": "Unterkunft",
    "parking": "Parkplatz",
    "fuel": "Tankstelle",
    "charging": "Ladepunkt",
    "place": "Ort oder POI",
}

# English provider terms are deliberately narrow.  They are search hints, not
# facts that are written into the Roadbook.
_KIND_PROVIDER_TERMS = {
    "ferry_terminal": ("ferry terminal", "passenger terminal", "port"),
    "transport_terminal": ("transport terminal", "station"),
    "hike": ("hiking trail", "nature trail", "walking route"),
    "nature_center": ("nature center", "visitor center", "information center"),
    "attraction": ("tourist attraction", "historic site", "museum"),
    "retail": ("shop", "shopping", "store"),
    "restaurant": ("restaurant", "cafe"),
    "camping": ("camp site", "motorhome parking", "camping"),
    "accommodation": ("hotel", "guest house", "accommodation"),
    "parking": ("parking", "car park"),
    "fuel": ("fuel station",),
    "charging": ("charging station",),
    "place": (),
    "address": (),
}

_KIND_ALIASES = {
    "ferry_terminal": (
        "fährterminal",
        "faehrterminal",
        "ferry terminal",
        "passenger terminal",
        "fährhafen",
        "faehrhafen",
        "ferry port",
        "ferry",
        "fähre",
        "faehre",
        "hafen terminal",
        "harbour terminal",
        "harbor terminal",
    ),
    "transport_terminal": (
        "bahnhof",
        "train station",
        "busbahnhof",
        "bus station",
        "flughafen",
        "airport",
        "terminal",
    ),
    "hike": (
        "wanderung",
        "wanderrunde",
        "wanderroute",
        "rundweg",
        "hiking",
        "hike",
        "trail",
        "walking route",
        "nature trail",
        "luontopolku",
        "kierros",
    ),
    "nature_center": (
        "naturzentrum",
        "naturzentrum",
        "nature center",
        "nature centre",
        "besucherzentrum",
        "visitor center",
        "visitor centre",
        "luontokeskus",
    ),
    "attraction": (
        "sehenswürdigkeit",
        "sehenswuerdigkeit",
        "museum",
        "denkmal",
        "monument",
        "burg",
        "schloss",
        "bunker",
        "ruine",
        "viewpoint",
        "aussichtspunkt",
        "historic site",
        "attraction",
        "sightseeing",
        "stadtbesichtigung",
        "city tour",
    ),
    "retail": (
        "einkauf",
        "einkaufen",
        "shop",
        "store",
        "supermarkt",
        "supermarket",
        "kaufhaus",
        "shopping",
        "mall",
    ),
    "restaurant": (
        "restaurant",
        "gastronomie",
        "cafe",
        "café",
        "essen",
        "lunch",
        "dinner",
    ),
    "camping": (
        "wildcamp",
        "wild camping",
        "camping",
        "campingplatz",
        "camp site",
        "campsite",
        "stellplatz",
        "wohnmobilstellplatz",
        "motorhome parking",
        "park4night",
    ),
    "accommodation": (
        "übernachtung",
        "uebernachtung",
        "hotel",
        "hostel",
        "ferienwohnung",
        "apartment",
        "guest house",
        "unterkunft",
    ),
    "parking": ("parkplatz", "parking", "car park"),
    "fuel": ("tankstelle", "fuel station", "petrol station"),
    "charging": ("ladepunkt", "ladesäule", "ladesaule", "charging station"),
}

_ALLOWED_AI_KINDS = frozenset(_KIND_LABELS)


def _clean(value: Any, maximum: int = 1_000) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())[:maximum]

def _host_matches(host: str, domain: str) -> bool:
    host = host.casefold()
    domain = domain.casefold()
    return host == domain or host.endswith("." + domain)


def _normalized(value: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_normalized(value))
        if len(token) >= 2
    }


def _bounded_words(value: Any, maximum: int) -> str:
    text = _clean(value, maximum * 3)
    if len(text) <= maximum:
        return text
    cut = text[: maximum + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-")[:maximum]


def _unique_text(values: Iterable[Any], *, maximum: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _bounded_words(raw, maximum)
        key = _normalized(value)
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _join_query(values: Iterable[Any], *, maximum: int, separator: str = ", ") -> str:
    result = ""
    seen: set[str] = set()
    for raw in values:
        value = _clean(raw, maximum)
        key = _normalized(value)
        if not value or key in seen:
            continue
        candidate = value if not result else f"{result}{separator}{value}"
        if len(candidate) > maximum:
            remaining = maximum - len(result) - (len(separator) if result else 0)
            if remaining < 3:
                break
            value = _bounded_words(value, remaining)
            if not value:
                break
            candidate = value if not result else f"{result}{separator}{value}"
        result = candidate
        seen.add(key)
        if len(result) >= maximum:
            break
    return result[:maximum]


def _string_values(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 3:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _string_values(nested, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for nested in value[:20]:
            yield from _string_values(nested, depth=depth + 1)


def _source_hints(stop: dict[str, Any]) -> tuple[dict[str, str], ...]:
    values = [stop.get("notes"), stop.get("details"), stop.get("location")]
    hints: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for text in _string_values(values):
        for raw_url in _URL_RE.findall(text):
            url = raw_url.rstrip(".,;:")
            try:
                parsed = urlparse(url)
            except ValueError:
                continue
            host = (parsed.hostname or "").casefold()
            path = parsed.path or ""
            kind = "link"
            identifier = ""
            if _host_matches(host, "park4night.com"):
                kind = "park4night"
                match = re.search(r"/(?:lieu|place)/(\d+)", path, re.IGNORECASE)
                identifier = match.group(1) if match else ""
            elif _host_matches(host, "openstreetmap.org"):
                kind = "openstreetmap"
                match = re.search(r"/(node|way|relation)/(\d+)", path, re.IGNORECASE)
                identifier = "/".join(match.groups()) if match else ""
            elif _host_matches(host, "wikipedia.org"):
                kind = "wikidata"
                match = re.search(r"/(Q\d+)", path, re.IGNORECASE)
                identifier = match.group(1).upper() if match else ""
            elif _host_matches(host, "wikipedia.org"):
                kind = "wikipedia"
                identifier = path.rsplit("/", 1)[-1]
            elif "google." in host or "goo.gl" in host:
                kind = "google_maps"
            fingerprint = (kind, identifier or url.casefold())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            hint = {"provider": kind, "url": url[:2_000]}
            if identifier:
                hint["id"] = identifier[:200]
            hints.append(hint)
            if len(hints) >= 6:
                return tuple(hints)
    return tuple(hints)


def _attribute(value: Any, name: str) -> str:
    return _clean(getattr(value, name, ""), 500) if value is not None else ""


def _address_query(structured: Any, *, include_name: bool = False) -> str:
    street = " ".join(
        value
        for value in (
            _attribute(structured, "street"),
            _attribute(structured, "house_number"),
        )
        if value
    )
    locality = " ".join(
        value
        for value in (
            _attribute(structured, "postal_code"),
            _attribute(structured, "district"),
            _attribute(structured, "city"),
        )
        if value
    )
    country = _attribute(structured, "country") or _attribute(
        structured, "country_code"
    )
    values: list[str] = []
    if include_name:
        values.append(_attribute(structured, "name"))
    values.extend((street, locality, _attribute(structured, "state"), country))
    return _join_query(values, maximum=_MAX_GEOCODING_QUERY_LENGTH)


def _matches_alias(text: str, aliases: Iterable[str]) -> bool:
    normalized = _normalized(text)
    return any(_normalized(alias) in normalized for alias in aliases)


def _classify(
    *,
    name: str,
    stop_type: str,
    notes: str,
    structured: Any,
    source_hints: tuple[dict[str, str], ...],
    ai_kind: str,
) -> tuple[str, float, str]:
    if _attribute(structured, "street") or _attribute(structured, "house_number"):
        return "address", 0.99, "structured_address"
    if ai_kind in _ALLOWED_AI_KINDS:
        return ai_kind, 0.9, "ai_text_classification"
    if any(hint.get("provider") == "park4night" for hint in source_hints):
        return "camping", 0.96, "park4night_link"

    # Imported stop categories are useful hints, but the destination name is
    # often more specific and may correct a broad or wrong legacy category
    # (for example a nature centre imported as a restaurant because it has a
    # cafe). Prefer an explicit type phrase in the name over ``stop_type``.
    fields = (
        (name, 5.0, "name"),
        (stop_type, 3.0, "stop_type"),
        (notes[:500], 1.0, "notes"),
    )
    scores: dict[str, float] = {kind: 0.0 for kind in _KIND_ALIASES}
    reasons: dict[str, str] = {}
    for text, weight, source in fields:
        if not text:
            continue
        for kind, aliases in _KIND_ALIASES.items():
            if _matches_alias(text, aliases):
                scores[kind] += weight
                reasons.setdefault(kind, source)
    best_kind = max(scores, key=scores.get)
    best_score = scores[best_kind]
    if best_score <= 0:
        return "place", 0.35, "generic_place"
    confidence = min(0.95, 0.48 + best_score * 0.09)
    return best_kind, confidence, reasons.get(best_kind, "text")


def _strip_kind_terms(name: str, kind: str) -> str:
    result = name
    for alias in sorted(_KIND_ALIASES.get(kind, ()), key=len, reverse=True):
        result = re.sub(
            rf"(?iu)(?<!\w){re.escape(alias)}(?!\w)",
            " ",
            result,
        )
    return _clean(result.strip(" -–—,;:/"), 500)


def _looks_generic_name(name: str, kind: str, locality: str) -> bool:
    residue = _strip_kind_terms(name, kind)
    if not residue:
        return True
    if locality and _normalized(residue) == _normalized(locality):
        return True
    return False


def _locality_from_name(name: str, kind: str) -> str:
    if kind not in {
        "ferry_terminal",
        "transport_terminal",
        "parking",
        "fuel",
        "charging",
    }:
        return ""
    residue = _strip_kind_terms(name, kind)
    words = residue.split()
    if 1 <= len(words) <= 4 and not any(character.isdigit() for character in residue):
        return residue
    return ""


@dataclass(frozen=True, slots=True)
class DestinationIntent:
    """One bounded, provider-neutral destination search plan."""

    kind: str
    label: str
    strategy: str
    confidence: float
    reason: str
    name: str
    locality: str
    country: str
    country_code: str
    query_variants: tuple[str, ...]
    source_hints: tuple[dict[str, str], ...]

    @property
    def primary_query(self) -> str:
        return self.query_variants[0] if self.query_variants else ""

    @property
    def is_specific_poi(self) -> bool:
        return self.kind not in {"address", "place"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "strategy": self.strategy,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "name": self.name,
            "locality": self.locality,
            "country": self.country,
            "country_code": self.country_code,
            "query_variants": list(self.query_variants),
            "source_hints": [dict(value) for value in self.source_hints],
        }


def analyze_destination(
    day: dict[str, Any],
    stop: dict[str, Any],
    *,
    structured_address: Any = None,
    cleanup_suggestion: dict[str, Any] | None = None,
) -> DestinationIntent:
    """Return a deterministic destination type and a bounded query plan."""

    cleanup = cleanup_suggestion if isinstance(cleanup_suggestion, dict) else {}
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    name = _clean(cleanup.get("name") or stop.get("name"), 500)
    stop_type = _clean(stop.get("type"), 200)
    notes = _clean(stop.get("notes"), 1_000)
    source_hints = _source_hints(stop)
    ai_kind = _clean(cleanup.get("place_kind"), 100).casefold()
    if ai_kind not in _ALLOWED_AI_KINDS:
        ai_kind = ""

    kind, confidence, reason = _classify(
        name=name,
        stop_type=stop_type,
        notes=notes,
        structured=structured_address,
        source_hints=source_hints,
        ai_kind=ai_kind,
    )
    city = (
        _attribute(structured_address, "city")
        or _clean(location.get("city"), 300)
    )
    district = (
        _attribute(structured_address, "district")
        or _clean(location.get("district") or location.get("suburb"), 300)
    )
    locality = city or district or _locality_from_name(name, kind)
    country_code = (
        _attribute(structured_address, "country_code")
        or _clean(location.get("country_code"), 10)
    ).upper()
    if len(country_code) != 2 or not country_code.isalpha():
        country_code = ""
    country = (
        _attribute(structured_address, "country")
        or _clean(location.get("country"), 300)
        or _COUNTRY_NAMES.get(country_code, "")
    )

    variants: list[str] = []
    if kind == "address":
        variants.extend(
            (
                _address_query(structured_address),
                _address_query(structured_address, include_name=True),
            )
        )
    else:
        primary = _join_query(
            (name, locality if locality and _normalized(locality) not in _normalized(name) else "", country or country_code),
            maximum=_MAX_GEOCODING_QUERY_LENGTH,
        )
        variants.append(primary)
        specific_name = _strip_kind_terms(name, kind)
        generic_name = _looks_generic_name(name, kind, locality)
        ai_terms = cleanup.get("search_terms")
        safe_ai_terms = (
            _unique_text(ai_terms[:3], maximum=120)
            if isinstance(ai_terms, list)
            else []
        )
        provider_terms = [*safe_ai_terms, *_KIND_PROVIDER_TERMS.get(kind, ())]
        for term in provider_terms:
            subject = specific_name if specific_name and not generic_name else term
            values = (
                subject,
                term if subject != term else "",
                locality if locality and _normalized(locality) not in _normalized(subject) else "",
                country or country_code,
            )
            variants.append(
                _join_query(values, maximum=_MAX_GEOCODING_QUERY_LENGTH)
            )
            if len(_unique_text(variants, maximum=_MAX_GEOCODING_QUERY_LENGTH)) >= _MAX_QUERY_VARIANTS:
                break

    query_variants = tuple(
        _unique_text(variants, maximum=_MAX_GEOCODING_QUERY_LENGTH)[
            :_MAX_QUERY_VARIANTS
        ]
    )
    strategy = "structured_address" if kind == "address" else "typed_poi"
    if any(hint.get("provider") == "park4night" for hint in source_hints):
        strategy = "source_hint_then_typed_poi"
    return DestinationIntent(
        kind=kind,
        label=_KIND_LABELS.get(kind, _KIND_LABELS["place"]),
        strategy=strategy,
        confidence=confidence,
        reason=reason,
        name=name,
        locality=locality,
        country=country,
        country_code=country_code,
        query_variants=query_variants,
        source_hints=source_hints,
    )


def destination_image_query(
    day: dict[str, Any],
    stop: dict[str, Any],
    *,
    intent: DestinationIntent | None = None,
    candidate: Any = None,
) -> str:
    """Return a concise image query tied to a resolved place identity.

    Coordinates are passed separately to image providers.  Notes and day titles
    are intentionally excluded so internal text can never exceed provider query
    limits or dilute the actual destination name.
    """

    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    profile = details.get("place_profile") if isinstance(details.get("place_profile"), dict) else {}

    def candidate_value(name: str) -> Any:
        if candidate is None:
            return None
        if isinstance(candidate, dict):
            return candidate.get(name)
        return getattr(candidate, name, None)

    candidate_location = candidate_value("location")
    if not isinstance(candidate_location, dict):
        as_location = getattr(candidate, "as_location", None) if candidate is not None else None
        candidate_location = as_location() if callable(as_location) else {}
    preferred_name = candidate_value("preferred_name")
    if not preferred_name and candidate is not None and not isinstance(candidate, dict):
        preferred_name = getattr(candidate, "preferred_name", None)
    name = _clean(
        preferred_name
        or candidate_value("name")
        or profile.get("name")
        or profile.get("display_name")
        or stop.get("name"),
        500,
    )
    # Provider display names can be full postal addresses; only their leading
    # identity is useful for image retrieval.
    if "," in name:
        name = _clean(name.split(",", 1)[0], 500)
    city = _clean(
        candidate_location.get("city")
        or location.get("city")
        or profile.get("city"),
        300,
    )
    country_code = _clean(
        candidate_location.get("country_code")
        or location.get("country_code")
        or profile.get("country_code"),
        10,
    ).upper()
    country = _clean(profile.get("country"), 300) or _COUNTRY_NAMES.get(
        country_code, country_code
    )
    category = _clean(
        profile.get("category")
        or (intent.label if intent is not None else "")
        or candidate_value("category")
        or stop.get("type"),
        200,
    )
    query = _join_query(
        (name, city if _normalized(city) not in _normalized(name) else "", country, category),
        maximum=_MAX_IMAGE_QUERY_LENGTH,
        separator=" ",
    )
    return query or _bounded_words(stop.get("name"), _MAX_IMAGE_QUERY_LENGTH)


__all__ = [
    "DestinationIntent",
    "analyze_destination",
    "destination_image_query",
]
