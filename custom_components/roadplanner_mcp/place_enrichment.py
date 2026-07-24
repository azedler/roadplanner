"""Reviewable place enrichment for Roadplanner stops.

The service resolves a Roadbook stop to a concrete place profile before any
Roadbook mutation is proposed. A profile combines conservative geocoding with
representative planning images. Optional AI cleanup can normalize only the
place text used for the search; it never supplies coordinates and every rename
remains separately reviewable.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import secrets
import time
from typing import Any, Iterable
from urllib.parse import quote_plus, urlparse

from .canonical_day import canonical_day_stops, location_status
from .destination_images import DestinationImageProvider
from .destination_intelligence import (
    DestinationIntent,
    analyze_destination,
    destination_image_query,
)
from .geocoding import (
    GeocodingCandidate,
    GeocodingError,
    GeocodingProvider,
    StructuredAddress,
    parse_coordinate_pair,
    parse_structured_address,
)
from .place_cleanup import PlaceCleanupService
from .roadplanner import ValidationError

_PREVIEW_TTL_SECONDS = 30 * 60
_MAX_PREVIEWS = 50
_MAX_ITEMS = 20
_MAX_CANDIDATES = 3
_MAX_IMAGES = 3
_MANUAL_CANDIDATE_ID = "__manual__"
_MAX_INTENT_QUERIES = 2
_MAX_COORDINATE_POI_DISTANCE_METERS = 30_000.0
_LOCALITY_RESULT_TYPES = frozenset(
    {
        "administrative",
        "borough",
        "city",
        "city_district",
        "county",
        "hamlet",
        "locality",
        "municipality",
        "neighbourhood",
        "postcode",
        "quarter",
        "state",
        "suburb",
        "town",
        "village",
    }
)
_INTENT_CATEGORY_KEYS = {
    "ferry_terminal": "ferry",
    "transport_terminal": "transport",
    "hike": "hiking",
    "nature_center": "nature_center",
    "attraction": "attraction",
    "retail": "retail",
    "restaurant": "restaurant",
    "camping": "camping",
    "accommodation": "accommodation",
    "parking": "parking",
    "fuel": "fuel",
    "charging": "charging",
}
_INTENT_RESULT_MARKERS = {
    "ferry_terminal": ("ferry", "harbour", "port", "terminal"),
    "transport_terminal": ("airport", "bus", "railway", "station", "terminal"),
    "hike": ("footway", "hiking", "path", "route", "trail", "walking"),
    "nature_center": ("education", "information", "museum", "nature", "visitor"),
    "attraction": ("attraction", "castle", "historic", "memorial", "monument", "museum", "tourism", "viewpoint"),
    "retail": ("mall", "shop", "sports", "store", "supermarket"),
    "restaurant": ("cafe", "fast_food", "restaurant"),
    "camping": ("camp", "caravan", "motorhome"),
    "accommodation": ("guest_house", "hostel", "hotel", "motel", "shelter"),
    "parking": ("car_park", "parking"),
    "fuel": ("fuel",),
    "charging": ("charging",),
}


def _text(value: Any, maximum: int = 2_000) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _https_url(value: Any) -> str | None:
    text = _text(value, 2_000)
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return text


def _coordinate(stop: dict[str, Any]) -> tuple[float, float] | None:
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _category_label(candidate: GeocodingCandidate) -> str:
    key = f"{candidate.category}:{candidate.result_type}".casefold()
    mapping = (
        (("ferry", "terminal"), "Fährterminal"),
        (("pharmacy",), "Apotheke"),
        (("hospital", "clinic", "doctors"), "Medizinische Versorgung"),
        (("camp", "caravan", "motorhome"), "Übernachtungsplatz"),
        (("parking",), "Parkplatz"),
        (("restaurant", "cafe", "fast_food"), "Gastronomie"),
        (("museum", "attraction", "viewpoint", "monument"), "Sehenswürdigkeit"),
        (("supermarket", "convenience", "mall"), "Einkauf"),
    )
    for tokens, label in mapping:
        if any(token in key for token in tokens):
            return label
    return _text(candidate.result_type or candidate.category, 100) or "Ort"


def _confidence(candidate: GeocodingCandidate) -> tuple[int, str]:
    value = max(0.0, min(1.0, float(candidate.score)))
    percent = round(value * 100)
    if candidate.resolution_mode == "reverse" and candidate.distance_meters is not None:
        if candidate.distance_meters <= 80:
            percent = max(percent, 96)
        elif candidate.distance_meters <= 250:
            percent = max(percent, 88)
    if percent >= 90:
        return percent, "Sehr hoch"
    if percent >= 78:
        return percent, "Hoch"
    if percent >= 60:
        return percent, "Mittel"
    return percent, "Niedrig"


def _candidate_id(candidate: GeocodingCandidate) -> str:
    material = {
        "osm_type": candidate.osm_type,
        "osm_id": candidate.osm_id,
        "lat": round(candidate.latitude, 7),
        "lon": round(candidate.longitude, 7),
        "name": candidate.display_name,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:18]
    return f"place-{digest}"


def _query_for_stop(day: dict[str, Any], stop: dict[str, Any]) -> str:
    """Return bounded stop identity text without notes or day-title noise."""
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    geocoding = (
        details.get("geocoding") if isinstance(details.get("geocoding"), dict) else {}
    )
    values = [
        geocoding.get("query"),
        stop.get("name"),
        location.get("label"),
        location.get("address"),
        location.get("city"),
        location.get("country_code"),
        stop.get("type"),
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = _text(raw, 240)
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            unique.append(value)
    return ", ".join(unique)[:240]


def _structured_address_for_stop(
    stop: dict[str, Any],
    *,
    query: str,
) -> StructuredAddress:
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    geocoding = (
        details.get("geocoding") if isinstance(details.get("geocoding"), dict) else {}
    )
    return parse_structured_address(
        address=location.get("address"),
        city=location.get("city"),
        district=(
            location.get("district")
            or location.get("suburb")
            or location.get("city_district")
        ),
        state=location.get("state"),
        country=location.get("country"),
        country_code=location.get("country_code"),
        label=location.get("label"),
        # ``query`` is an aggregate UI search string and may contain the stop
        # type separated by commas. Feeding it back into the address parser can
        # turn values such as "ferry" into a fake city. Only a previously saved
        # geocoding query participates here; the stop name remains available
        # separately for street/house extraction.
        query=geocoding.get("query"),
        name=stop.get("name"),
    )


def _cleanup_input(
    day: dict[str, Any],
    stop: dict[str, Any],
    structured: StructuredAddress,
) -> dict[str, Any]:
    address = structured.as_dict()
    address.pop("name", None)
    return {
        "stop_id": _text(stop.get("id"), 200),
        "name": _text(stop.get("name"), 500),
        "stop_type": _text(stop.get("type"), 100),
        "day_date": _text(day.get("date"), 30),
        "day_title": _text(day.get("title"), 500),
        "notes": _text(stop.get("notes"), 600),
        "address": address,
    }


def _image_query(
    day: dict[str, Any],
    stop: dict[str, Any],
    candidate: GeocodingCandidate,
    *,
    intent: DestinationIntent | None = None,
) -> str:
    """Return a concise geodata-backed query for planning images."""
    return destination_image_query(day, stop, intent=intent, candidate=candidate)


def _contact(candidate: GeocodingCandidate) -> dict[str, str]:
    tags = candidate.extratags
    values = {
        "website": _https_url(
            tags.get("contact:website") or tags.get("website") or tags.get("url")
        ),
        "phone": _text(
            tags.get("contact:phone") or tags.get("phone") or tags.get("telephone"),
            300,
        ),
        "email": _text(tags.get("contact:email") or tags.get("email"), 500),
        "opening_hours": _text(tags.get("opening_hours"), 1_000),
        "wikidata": _text(tags.get("wikidata"), 100),
        "wikipedia": _text(tags.get("wikipedia"), 500),
    }
    return {key: value for key, value in values.items() if value}


def _map_url(latitude: float, longitude: float) -> str:
    query = quote_plus(f"{latitude:.7f},{longitude:.7f}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def _current_stop_payload(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
    return {
        "day_id": _text(
            stop.get("_source_day_id") if stop.get("_inherited") else day.get("id"),
            200,
        ),
        "day_date": _text(day.get("date"), 30),
        "day_title": _text(day.get("title"), 500),
        "stop_id": _text(stop.get("id"), 200),
        "stop_name": _text(stop.get("name"), 500),
        "stop_type": _text(stop.get("type"), 100),
        "location_status": location_status(stop),
        "location": deepcopy(
            stop.get("location") if isinstance(stop.get("location"), dict) else {}
        ),
        "details": deepcopy(
            stop.get("details") if isinstance(stop.get("details"), dict) else {}
        ),
        "inherited": bool(stop.get("_inherited")),
    }


def _country_code(value: Any) -> str:
    code = _text(value, 10).upper()
    if code and (len(code) != 2 or not code.isalpha()):
        raise ValidationError("Der Ländercode muss aus genau zwei Buchstaben bestehen")
    return code


def _manual_location(
    raw: dict[str, Any],
    *,
    current_name: str,
) -> tuple[dict[str, Any], str]:
    latitude_text = _text(raw.get("latitude"), 100)
    longitude_text = _text(raw.get("longitude"), 100)
    if not latitude_text or not longitude_text:
        raise ValidationError("Für einen manuellen Ort werden Breiten- und Längengrad benötigt")
    coordinates = parse_coordinate_pair(f"{latitude_text};{longitude_text}")
    if coordinates is None:
        raise ValidationError("Die manuellen GPS-Koordinaten konnten nicht gelesen werden")

    name = _text(raw.get("name"), 500) or _text(current_name, 500)
    address = _text(raw.get("address"), 1_000)
    city = _text(raw.get("city"), 300)
    country_code = _country_code(raw.get("country_code"))
    if not name and not address:
        raise ValidationError("Ein manueller Ort benötigt mindestens einen Namen oder eine Adresse")
    location = {
        "label": name or address,
        "address": address,
        "city": city,
        "country_code": country_code,
        "latitude": coordinates[0],
        "longitude": coordinates[1],
    }
    return {key: value for key, value in location.items() if value != ""}, name


def _candidate_supports_intent(
    candidate: GeocodingCandidate,
    intent: DestinationIntent,
) -> bool:
    """Return whether a candidate is more specific than a surrounding locality."""
    if intent.kind in {"address", "place"}:
        return True
    expected_category = _INTENT_CATEGORY_KEYS.get(intent.kind)
    if expected_category and getattr(candidate, "category_intent", None) == expected_category:
        if getattr(candidate, "category_match", None) is True:
            return True
        if getattr(candidate, "category_match", None) is False:
            return False
    result_type = _text(getattr(candidate, "result_type", ""), 100).casefold()
    category = _text(getattr(candidate, "category", ""), 100).casefold()
    if result_type in _LOCALITY_RESULT_TYPES or category in {"boundary", "place"}:
        return False
    searchable = f"{category} {result_type}".casefold()
    return any(
        marker in searchable
        for marker in _INTENT_RESULT_MARKERS.get(intent.kind, ())
    )


def _distance_meters(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
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
class _PreviewEntry:
    created_at: float
    user_id: str
    trip_id: str
    payload: dict[str, Any]


class PlaceEnrichmentService:
    """Prepare and validate full place profiles before Roadbook review."""

    def __init__(
        self,
        geocoder: GeocodingProvider,
        image_provider: DestinationImageProvider,
        *,
        cleanup_service: PlaceCleanupService | None = None,
        language: str = "de",
    ) -> None:
        self._geocoder = geocoder
        self._image_provider = image_provider
        self._cleanup_service = cleanup_service
        self._language = language or "de"
        self._previews: dict[str, _PreviewEntry] = {}
        self._lock = asyncio.Lock()

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, value in self._previews.items()
            if now - value.created_at > _PREVIEW_TTL_SECONDS
        ]
        for key in expired:
            self._previews.pop(key, None)
        while len(self._previews) > _MAX_PREVIEWS:
            self._previews.pop(next(iter(self._previews)), None)

    async def _resolve_candidates(
        self,
        stop: dict[str, Any],
        *,
        intent: DestinationIntent,
        structured_address: StructuredAddress,
    ) -> tuple[
        GeocodingCandidate | None,
        list[tuple[GeocodingCandidate, str]],
    ]:
        """Resolve a bounded typed query plan and keep all reviewable results."""
        coordinate = _coordinate(stop)
        records: dict[str, tuple[GeocodingCandidate, str, bool, float | None]] = {}
        best: GeocodingCandidate | None = None

        def add(candidate: GeocodingCandidate | None, query: str) -> None:
            if candidate is None:
                return
            distance: float | None = None
            if coordinate is not None:
                distance = _distance_meters(
                    coordinate[0],
                    coordinate[1],
                    candidate.latitude,
                    candidate.longitude,
                )
                if (
                    intent.is_specific_poi
                    and candidate.resolution_mode != "reverse"
                    and distance > _MAX_COORDINATE_POI_DISTANCE_METERS
                ):
                    return
            identity = _candidate_id(candidate)
            supports = _candidate_supports_intent(candidate, intent)
            previous = records.get(identity)
            if previous is None:
                records[identity] = (candidate, query, supports, distance)
                return
            previous_candidate = previous[0]
            previous_key = (
                int(previous[2]),
                int(getattr(previous_candidate, "auto_selectable", False)),
                float(getattr(previous_candidate, "score", 0.0)),
            )
            candidate_key = (
                int(supports),
                int(getattr(candidate, "auto_selectable", False)),
                float(getattr(candidate, "score", 0.0)),
            )
            if candidate_key > previous_key:
                records[identity] = (candidate, query, supports, distance)

        if coordinate is not None:
            reverse = await self._geocoder.async_reverse(
                coordinate[0],
                coordinate[1],
                language=self._language,
            )
            add(reverse, intent.primary_query or f"{coordinate[0]}, {coordinate[1]}")
            if reverse is not None and (
                not intent.is_specific_poi
                or _candidate_supports_intent(reverse, intent)
            ):
                best = reverse

        should_forward_search = coordinate is None or (
            intent.is_specific_poi and best is None
        )
        if should_forward_search:
            for index, query in enumerate(
                intent.query_variants[:_MAX_INTENT_QUERIES]
            ):
                if not query:
                    continue
                safe_default, alternatives = await self._geocoder.async_resolve(
                    query,
                    structured_address=(
                        structured_address if intent.kind == "address" else None
                    ),
                    language=self._language,
                )
                add(safe_default, query)
                for candidate in alternatives:
                    add(candidate, query)
                if safe_default is not None and (
                    not intent.is_specific_poi
                    or _candidate_supports_intent(safe_default, intent)
                ):
                    best = safe_default
                    break
                # A second query is a deliberate fallback for typed POIs.  A
                # concrete address already has provider-side structured and
                # free-text variants in one request.
                if intent.kind == "address" or index + 1 >= _MAX_INTENT_QUERIES:
                    break

        ordered_records = list(records.values())
        ordered_records.sort(
            key=lambda record: (
                int(best is not None and _candidate_id(record[0]) == _candidate_id(best)),
                int(record[2]),
                int(getattr(record[0], "auto_selectable", False)),
                float(getattr(record[0], "score", 0.0)),
                -(record[3] if record[3] is not None else 0.0),
            ),
            reverse=True,
        )
        return best, [
            (candidate, query)
            for candidate, query, _supports, _distance in ordered_records
        ]

    async def _profile_candidate(
        self,
        day: dict[str, Any],
        stop: dict[str, Any],
        candidate: GeocodingCandidate,
        *,
        query: str,
        intent: DestinationIntent,
    ) -> dict[str, Any]:
        image_query = _image_query(day, stop, candidate, intent=intent)
        image_result: dict[str, Any]
        try:
            async with asyncio.timeout(18):
                image_result = await self._image_provider.async_search(
                    image_query,
                    limit=6,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                )
        except (TimeoutError, ValidationError):
            image_result = {
                "results": [],
                "provider_errors": {"roadplanner": "Bildsuche nicht verfügbar"},
            }
        images = [
            deepcopy(item)
            for item in list(image_result.get("results") or [])[:_MAX_IMAGES]
            if isinstance(item, dict)
        ]
        percent, label = _confidence(candidate)
        location = candidate.as_location()
        contact = _contact(candidate)
        provenance = candidate.as_provenance()
        provenance["provider_verified"] = True
        address_matches = (
            provenance.get("address_matches")
            if isinstance(provenance.get("address_matches"), dict)
            else {}
        )
        return {
            "id": _candidate_id(candidate),
            "name": candidate.preferred_name,
            "display_name": candidate.display_name,
            "address": candidate.display_name,
            "location": location,
            "category": _category_label(candidate),
            "provider_category": _text(candidate.category, 100),
            "provider_type": _text(candidate.result_type, 100),
            "contact": contact,
            "website": contact.get("website"),
            "phone": contact.get("phone"),
            "opening_hours": contact.get("opening_hours"),
            "map_url": _map_url(candidate.latitude, candidate.longitude),
            "source_url": candidate.source_url,
            "provider_id": (
                f"{candidate.osm_type}/{candidate.osm_id}"
                if candidate.osm_type and candidate.osm_id is not None
                else None
            ),
            "osm_type": candidate.osm_type or None,
            "osm_id": candidate.osm_id,
            "wikidata": contact.get("wikidata"),
            "wikipedia": contact.get("wikipedia"),
            "destination_intent": intent.as_dict(),
            "attribution": "© OpenStreetMap contributors",
            "confidence": percent,
            "confidence_label": label,
            "score": round(candidate.score, 4),
            "images": images,
            "primary_image_id": _text(images[0].get("id"), 500) if images else None,
            "image_query": image_query,
            "image_provider_errors": dict(image_result.get("provider_errors") or {}),
            "provenance": provenance,
            "query": query,
            "match_type": _text(getattr(candidate, "match_type", "generic"), 100),
            "match_label": _text(getattr(candidate, "match_label", "Ort"), 200),
            "search_variant": _text(
                getattr(candidate, "search_variant", "free_text"), 100
            ),
            "auto_selectable": bool(getattr(candidate, "auto_selectable", False)),
            "address_matches": deepcopy(address_matches),
            "address_mismatches": list(
                getattr(candidate, "address_mismatches", ()) or ()
            ),
        }

    async def _prepare_item(
        self,
        day: dict[str, Any],
        stop: dict[str, Any],
        *,
        cleanup_suggestion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = _current_stop_payload(day, stop)
        original_query = _query_for_stop(day, stop)
        structured = _structured_address_for_stop(stop, query=original_query)
        search_address = structured
        if cleanup_suggestion:
            merged = dict(cleanup_suggestion.get("address") or {})
            if cleanup_suggestion.get("name"):
                merged["name"] = cleanup_suggestion.get("name")
            search_address = structured.merged(merged)
        intent = analyze_destination(
            day,
            stop,
            structured_address=search_address,
            cleanup_suggestion=cleanup_suggestion,
        )
        query = intent.primary_query or (
            search_address.full_query(original_query)
            if search_address.has_address_detail
            else original_query
        )
        if not query:
            return {
                "status": "not_found",
                "query": "",
                "query_variants": [],
                "destination_intent": intent.as_dict(),
                "current": current,
                "structured_address": structured.as_dict(),
                "ai_cleanup": deepcopy(cleanup_suggestion),
                "candidates": [],
                "selected_candidate_id": None,
                "message": "Für diesen Stopp fehlen Angaben für eine Ortssuche.",
                "manual_allowed": True,
            }
        try:
            best, candidate_records = await self._resolve_candidates(
                stop,
                intent=intent,
                structured_address=search_address,
            )
        except GeocodingError as err:
            return {
                "status": "error",
                "query": query,
                "query_variants": list(intent.query_variants),
                "destination_intent": intent.as_dict(),
                "current": current,
                "structured_address": search_address.as_dict(),
                "ai_cleanup": deepcopy(cleanup_suggestion),
                "candidates": [],
                "selected_candidate_id": None,
                "message": str(err)[:1_000],
                "manual_allowed": True,
            }

        profiles = (
            await asyncio.gather(
                *(
                    self._profile_candidate(
                        day,
                        stop,
                        candidate,
                        query=matched_query,
                        intent=intent,
                    )
                    for candidate, matched_query in candidate_records[
                        :_MAX_CANDIDATES
                    ]
                )
            )
            if candidate_records
            else []
        )
        selected = None
        if best is not None:
            best_id = _candidate_id(best)
            selected = next(
                (
                    profile.get("id")
                    for profile in profiles
                    if profile.get("id") == best_id
                ),
                None,
            )
        if selected:
            status = "resolved"
            message = (
                "Roadplanner hat einen hinreichend genauen, typgerechten Ort "
                "gefunden."
            )
        elif profiles:
            status = "ambiguous"
            message = (
                "Es wurden mögliche Treffer gefunden. Bitte den konkreten Ort "
                "prüfen; ein umliegender Ortsteil wird bei einem erwarteten POI "
                "nicht automatisch übernommen."
            )
        else:
            status = "not_found"
            message = (
                "Es wurde kein passender Provider-Treffer gefunden. Adresse und "
                "Kartenpunkt können bewusst manuell bestätigt werden."
            )
        return {
            "status": status,
            "query": query,
            "query_variants": list(intent.query_variants),
            "original_query": original_query,
            "destination_intent": intent.as_dict(),
            "current": current,
            "structured_address": search_address.as_dict(),
            "ai_cleanup": deepcopy(cleanup_suggestion),
            "candidates": profiles,
            "selected_candidate_id": selected,
            "message": message,
            "manual_allowed": True,
        }

    async def async_prepare(
        self,
        *,
        user_id: str,
        trip_id: str,
        days: Iterable[dict[str, Any]],
        day_id: str | None = None,
        stop_id: str | None = None,
        limit: int = _MAX_ITEMS,
        use_ai_cleanup: bool = False,
    ) -> dict[str, Any]:
        target_day = _text(day_id, 200)
        target_stop = _text(stop_id, 200)
        maximum = max(1, min(int(limit), _MAX_ITEMS))
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen: set[tuple[str, str]] = set()
        for day in days:
            if not isinstance(day, dict):
                continue
            if target_day and _text(day.get("id"), 200) != target_day:
                continue
            for stop in canonical_day_stops(day):
                if not isinstance(stop, dict):
                    continue
                if target_stop and _text(stop.get("id"), 200) != target_stop:
                    continue
                current = _current_stop_payload(day, stop)
                identity = (current["day_id"], current["stop_id"])
                if not all(identity) or identity in seen:
                    continue
                seen.add(identity)
                details = (
                    current.get("details")
                    if isinstance(current.get("details"), dict)
                    else {}
                )
                place_profile = (
                    details.get("place_profile")
                    if isinstance(details.get("place_profile"), dict)
                    else {}
                )
                if (
                    current["location_status"] == "resolved"
                    and place_profile.get("confirmed_at")
                ):
                    continue
                candidates.append((day, stop))
                if len(candidates) >= maximum:
                    break
            if len(candidates) >= maximum:
                break
        if not candidates:
            raise ValidationError(
                "Für die ausgewählten Stopps sind keine offenen Ortsprofile vorhanden"
            )

        cleanup_suggestions: dict[str, dict[str, Any]] = {}
        cleanup_diagnostics: dict[str, Any] = {
            "requested": bool(use_ai_cleanup),
            "available": bool(
                self._cleanup_service is not None
                and self._cleanup_service.available
            ),
            "item_count": len(candidates),
            "suggested_count": 0,
            "error": None,
        }
        if use_ai_cleanup and self._cleanup_service is not None:
            inputs = []
            for day, stop in candidates:
                original_query = _query_for_stop(day, stop)
                structured = _structured_address_for_stop(
                    stop,
                    query=original_query,
                )
                inputs.append(_cleanup_input(day, stop, structured))
            cleanup_suggestions, cleanup_diagnostics = (
                await self._cleanup_service.async_suggest_many(inputs)
            )
        elif use_ai_cleanup:
            cleanup_diagnostics["error"] = "KI-Ortsbereinigung ist nicht konfiguriert"

        semaphore = asyncio.Semaphore(2)

        async def prepare(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._prepare_item(
                    day,
                    stop,
                    cleanup_suggestion=cleanup_suggestions.get(
                        _text(stop.get("id"), 200)
                    ),
                )

        items = await asyncio.gather(
            *(prepare(day, stop) for day, stop in candidates)
        )
        preview_id = f"place-preview-{secrets.token_hex(8)}"
        payload = {
            "id": preview_id,
            "trip_id": trip_id,
            "created_at": _utc_now_iso(),
            "expires_in_seconds": _PREVIEW_TTL_SECONDS,
            "use_ai_cleanup": bool(use_ai_cleanup),
            "ai_cleanup": cleanup_diagnostics,
            "items": items,
            "stats": {
                "item_count": len(items),
                "resolved_count": sum(
                    1 for item in items if item.get("status") == "resolved"
                ),
                "ambiguous_count": sum(
                    1 for item in items if item.get("status") == "ambiguous"
                ),
                "not_found_count": sum(
                    1 for item in items if item.get("status") == "not_found"
                ),
                "error_count": sum(
                    1 for item in items if item.get("status") == "error"
                ),
                "ai_cleanup_suggested_count": int(
                    cleanup_diagnostics.get("suggested_count") or 0
                ),
            },
        }
        async with self._lock:
            self._purge()
            self._previews[preview_id] = _PreviewEntry(
                created_at=time.monotonic(),
                user_id=user_id,
                trip_id=trip_id,
                payload=deepcopy(payload),
            )
        return payload

    @staticmethod
    def _gallery_for_candidate(
        *,
        current: dict[str, Any],
        stop_id: str,
        item: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        images = [
            deepcopy(value)
            for value in list(candidate.get("images") or [])[:_MAX_IMAGES]
            if isinstance(value, dict)
        ]
        location = (
            candidate.get("location")
            if isinstance(candidate.get("location"), dict)
            else {}
        )
        fingerprint_material = {
            "day_id": current.get("day_id"),
            "stop_id": stop_id,
            "query": candidate.get("image_query") or item.get("query"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        }
        query_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "stop_id": stop_id,
            "day_id": current.get("day_id"),
            "query": candidate.get("image_query") or item.get("query"),
            "query_fingerprint": query_fingerprint,
            "status": "ready" if images else "empty",
            "images": images,
            "primary_image_id": candidate.get("primary_image_id"),
            "provider_errors": deepcopy(
                candidate.get("image_provider_errors") or {}
            ),
            "attempted_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }

    async def resolve_selections(
        self,
        *,
        user_id: str,
        trip_id: str,
        preview_id: str,
        selections: dict[str, str],
        manual_entries: dict[str, dict[str, Any]] | None = None,
        cleanup_confirmations: dict[str, bool] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with self._lock:
            self._purge()
            entry = self._previews.get(preview_id)
            if entry is None:
                raise ValidationError(
                    "Die Ortsvorschau ist abgelaufen. Bitte erneut suchen."
                )
            if entry.user_id != user_id or entry.trip_id != trip_id:
                raise ValidationError("Die Ortsvorschau gehört zu einer anderen Sitzung")
            payload = deepcopy(entry.payload)

        manual_entries = manual_entries or {}
        cleanup_confirmations = cleanup_confirmations or {}
        operations: list[dict[str, Any]] = []
        galleries: list[dict[str, Any]] = []
        for item in payload.get("items", []):
            if not isinstance(item, dict):
                continue
            current = (
                item.get("current") if isinstance(item.get("current"), dict) else {}
            )
            stop_id = _text(current.get("stop_id"), 200)
            candidate_id = _text(selections.get(stop_id), 200)
            if not stop_id or not candidate_id:
                continue

            details = deepcopy(
                current.get("details")
                if isinstance(current.get("details"), dict)
                else {}
            )
            confirmed_at = _utc_now_iso()
            changes: dict[str, Any]
            reason: str
            gallery: dict[str, Any] | None = None

            if candidate_id == _MANUAL_CANDIDATE_ID:
                raw_manual = manual_entries.get(stop_id)
                if not isinstance(raw_manual, dict):
                    raise ValidationError(
                        f"Manuelle Ortsdaten für {current.get('stop_name') or stop_id} fehlen"
                    )
                location, manual_name = _manual_location(
                    raw_manual,
                    current_name=_text(current.get("stop_name"), 500),
                )
                provenance = {
                    "provider": "manual",
                    "status": "manual_confirmed",
                    "provider_verified": False,
                    "query": item.get("query"),
                    "selected_candidate_id": candidate_id,
                    "confirmed_at": confirmed_at,
                    "confirmed_by": user_id,
                    "coordinate_system": "WGS84",
                }
                details["geocoding"] = provenance
                details["place_profile"] = {
                    "provider": "manual",
                    "provider_verified": False,
                    "name": manual_name or location.get("label"),
                    "display_name": location.get("address") or location.get("label"),
                    "category": "Manuell bestätigter Ort",
                    "map_url": _map_url(
                        float(location["latitude"]),
                        float(location["longitude"]),
                    ),
                    "confidence": None,
                    "confidence_label": "Manuell bestätigt",
                    "confirmed_at": confirmed_at,
                }
                changes = {
                    "location": location,
                    "details": details,
                }
                current_name = _text(current.get("stop_name"), 500)
                if manual_name and manual_name.casefold() != current_name.casefold():
                    changes["name"] = manual_name
                reason = (
                    "Der Benutzer hat Adresse und WGS84-Kartenpunkt bewusst manuell "
                    "bestätigt; die Werte sind nicht provider-verifiziert."
                )
            else:
                candidate = next(
                    (
                        value
                        for value in item.get("candidates", [])
                        if isinstance(value, dict)
                        and _text(value.get("id"), 200) == candidate_id
                    ),
                    None,
                )
                if candidate is None:
                    raise ValidationError(
                        f"Ausgewählter Ort für {current.get('stop_name') or stop_id} ist ungültig"
                    )
                provenance = deepcopy(
                    candidate.get("provenance")
                    if isinstance(candidate.get("provenance"), dict)
                    else {}
                )
                provenance.update(
                    {
                        "status": "resolved",
                        "provider_verified": True,
                        "query": item.get("query"),
                        "query_variants": deepcopy(item.get("query_variants") or []),
                        "destination_intent": deepcopy(
                            item.get("destination_intent") or {}
                        ),
                        "selected_candidate_id": candidate_id,
                        "confirmed_at": confirmed_at,
                        "confirmed_by": user_id,
                    }
                )
                details["geocoding"] = provenance
                contact = (
                    candidate.get("contact")
                    if isinstance(candidate.get("contact"), dict)
                    else {}
                )
                details["place_profile"] = {
                    "provider": _text(provenance.get("provider"), 100) or "provider",
                    "provider_verified": True,
                    "name": candidate.get("name"),
                    "display_name": candidate.get("display_name"),
                    "category": candidate.get("category"),
                    "provider_category": candidate.get("provider_category"),
                    "provider_type": candidate.get("provider_type"),
                    "website": candidate.get("website"),
                    "phone": candidate.get("phone"),
                    "email": contact.get("email"),
                    "opening_hours": candidate.get("opening_hours"),
                    "map_url": candidate.get("map_url"),
                    "source_url": candidate.get("source_url"),
                    "provider_id": candidate.get("provider_id"),
                    "osm_type": candidate.get("osm_type"),
                    "osm_id": candidate.get("osm_id"),
                    "wikidata": candidate.get("wikidata"),
                    "wikipedia": candidate.get("wikipedia"),
                    "kind": (
                        candidate.get("destination_intent") or {}
                    ).get("kind"),
                    "kind_label": (
                        candidate.get("destination_intent") or {}
                    ).get("label"),
                    "source_hints": deepcopy(
                        (candidate.get("destination_intent") or {}).get(
                            "source_hints"
                        )
                        or []
                    ),
                    "image_query": candidate.get("image_query"),
                    "city": (candidate.get("location") or {}).get("city"),
                    "country_code": (candidate.get("location") or {}).get(
                        "country_code"
                    ),
                    "confidence": candidate.get("confidence"),
                    "confidence_label": candidate.get("confidence_label"),
                    "match_type": candidate.get("match_type"),
                    "match_label": candidate.get("match_label"),
                    "confirmed_at": confirmed_at,
                }
                changes = {
                    "location": deepcopy(candidate.get("location") or {}),
                    "details": details,
                }
                cleanup = (
                    item.get("ai_cleanup")
                    if isinstance(item.get("ai_cleanup"), dict)
                    else None
                )
                if cleanup and bool(cleanup_confirmations.get(stop_id)):
                    suggested_name = _text(cleanup.get("name"), 500)
                    current_name = _text(current.get("stop_name"), 500)
                    if (
                        suggested_name
                        and suggested_name.casefold() != current_name.casefold()
                    ):
                        changes["name"] = suggested_name
                    details["place_cleanup"] = {
                        "status": "confirmed",
                        "provider": cleanup.get("provider"),
                        "model": cleanup.get("model"),
                        "changed_fields": deepcopy(
                            cleanup.get("changed_fields") or []
                        ),
                        "suggested_address": deepcopy(cleanup.get("address") or {}),
                        "confirmed_at": confirmed_at,
                        "confirmed_by": user_id,
                        "coordinate_policy": "not_provided_not_accepted",
                    }
                reason = (
                    "Der Benutzer hat den konkreten Kartenpunkt und das vollständige "
                    "Ortsprofil in der Roadplanner-Vorschau bestätigt."
                )
                gallery = self._gallery_for_candidate(
                    current=current,
                    stop_id=stop_id,
                    item=item,
                    candidate=candidate,
                )

            operation_material = (
                f"{current.get('day_id') or ''}:{stop_id}:{candidate_id}:"
                f"{json.dumps(changes, ensure_ascii=False, sort_keys=True, default=str)}"
            )
            operation_id = "place-enrich-" + hashlib.sha256(
                operation_material.encode("utf-8")
            ).hexdigest()[:16]
            operations.append(
                {
                    "operation_id": operation_id,
                    "action": "update",
                    "entity_type": "stop",
                    "entity_id": stop_id,
                    "day_id": current.get("day_id"),
                    "changes": changes,
                    "reason": reason,
                }
            )
            if gallery is not None:
                galleries.append(gallery)
        if not operations:
            raise ValidationError("Wähle mindestens einen konkreten Ort aus")
        return operations, galleries
