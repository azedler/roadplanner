"""Optional, review-only AI cleanup for Roadplanner place text.

The cleanup service never receives coordinates and never returns coordinates.
It can normalize names and user-supplied address components before the normal
geocoding provider is queried. Every proposed rename remains opt-in in the
place-enrichment review dialog.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
from typing import Any, Iterable

from .assistant_provider import AssistantProvider

_LOGGER = logging.getLogger(__name__)

_MAX_ITEMS = 20
_ALLOWED_ADDRESS_FIELDS = frozenset(
    {
        "street",
        "house_number",
        "postal_code",
        "city",
        "district",
        "state",
        "country",
        "country_code",
    }
)
_ALLOWED_PLACE_KINDS = frozenset(
    {
        "address",
        "ferry_terminal",
        "transport_terminal",
        "hike",
        "nature_center",
        "attraction",
        "retail",
        "restaurant",
        "camping",
        "accommodation",
        "parking",
        "fuel",
        "charging",
        "place",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "lat",
        "latitude",
        "lon",
        "lng",
        "longitude",
        "coordinates",
        "location",
        "gps",
    }
)

_CLEANUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "maxItems": _MAX_ITEMS,
            "items": {
                "type": "object",
                "properties": {
                    "stop_id": {"type": "string"},
                    "name": {"type": ["string", "null"]},
                    "address": {
                        "type": "object",
                        "properties": {
                            "street": {"type": ["string", "null"]},
                            "house_number": {"type": ["string", "null"]},
                            "postal_code": {"type": ["string", "null"]},
                            "city": {"type": ["string", "null"]},
                            "district": {"type": ["string", "null"]},
                            "state": {"type": ["string", "null"]},
                            "country": {"type": ["string", "null"]},
                            "country_code": {"type": ["string", "null"]},
                        },
                    },
                    "place_kind": {"type": ["string", "null"]},
                    "search_terms": {
                        "type": "array",
                        "maxItems": 3,
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": ["number", "null"]},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["stop_id", "address"],
            },
        }
    },
    "required": ["items"],
}

_SYSTEM_INSTRUCTION = """Du bereinigst ausschließlich bereits vorhandene Ortsnamen und Adresstexte für einen Reiseplan.

Regeln:
- Erfinde keine Koordinaten und gib niemals latitude, longitude, GPS, location oder coordinates aus.
- Verwende ausschließlich Informationen aus dem gelieferten Stopptext. Keine Websuche und kein externes Wissen.
- Korrigiere nur offensichtliche Schreibweisen, trenne Straße/Hausnummer/PLZ/Ort/Ortsteil/Bundesland/Land und vereinheitliche Ländernamen.
- Ordne den Stopp optional genau einem place_kind zu: address, ferry_terminal, transport_terminal, hike, nature_center, attraction, retail, restaurant, camping, accommodation, parking, fuel, charging oder place.
- search_terms dürfen höchstens drei kurze, aus dem Eingabetext ableitbare Provider-Suchbegriffe enthalten, zum Beispiel eine englische Kategorienübersetzung. Keine konkrete Filiale oder Adresse erfinden.
- Bei Unsicherheit lasse Felder leer oder unverändert. Ein Marken- oder POI-Name darf nicht in eine konkrete Filiale umgedeutet werden.
- stop_id muss exakt aus der Eingabe übernommen werden.
- Antworte ausschließlich im vorgegebenen JSON-Schema.
"""


def _text(value: Any, maximum: int = 500) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                return True
            if _contains_forbidden_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _source_text_fields(item: dict[str, Any]) -> dict[str, str]:
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    return {
        "name": _text(item.get("name")),
        **{key: _text(address.get(key)) for key in _ALLOWED_ADDRESS_FIELDS},
    }


class PlaceCleanupService:
    """Run one bounded provider call and return reviewable text suggestions."""

    def __init__(
        self,
        provider: AssistantProvider | None,
        *,
        language: str = "de",
    ) -> None:
        self._provider = provider
        self._language = _text(language, 20) or "de"

    @property
    def available(self) -> bool:
        return bool(self._provider is not None and self._provider.configured)

    async def async_suggest_many(
        self,
        items: Iterable[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        """Return suggestions keyed by stop ID and sanitized diagnostics.

        Provider failures are intentionally converted to diagnostics so the
        ordinary geocoder remains usable.
        """

        bounded: list[dict[str, Any]] = []
        source_by_id: dict[str, dict[str, str]] = {}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            stop_id = _text(raw.get("stop_id"), 200)
            if not stop_id or stop_id in source_by_id:
                continue
            safe_address = raw.get("address") if isinstance(raw.get("address"), dict) else {}
            safe = {
                "stop_id": stop_id,
                "name": _text(raw.get("name")),
                "stop_type": _text(raw.get("stop_type"), 100),
                "day_date": _text(raw.get("day_date"), 30),
                "day_title": _text(raw.get("day_title")),
                "address": {
                    key: _text(safe_address.get(key))
                    for key in _ALLOWED_ADDRESS_FIELDS
                    if _text(safe_address.get(key))
                },
            }
            # Coordinates are deliberately never copied from the caller.
            bounded.append(safe)
            source_by_id[stop_id] = _source_text_fields(safe)
            if len(bounded) >= _MAX_ITEMS:
                break

        diagnostics: dict[str, Any] = {
            "requested": bool(bounded),
            "available": self.available,
            "item_count": len(bounded),
            "suggested_count": 0,
            "error": None,
        }
        if not bounded or not self.available or self._provider is None:
            if bounded and not self.available:
                diagnostics["error"] = "KI-Ortsbereinigung ist nicht konfiguriert"
            return {}, diagnostics

        try:
            async with asyncio.timeout(30):
                result = await self._provider.async_generate_json_result(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    messages=[
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "language": self._language,
                                    "stops": bounded,
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                    schema=_CLEANUP_SCHEMA,
                    enable_search=False,
                    max_output_tokens=4096,
                    temperature=0.0,
                )
        except Exception as err:  # Optional enhancement must never block geocoding.
            _LOGGER.warning("AI place cleanup failed: %s", type(err).__name__)
            diagnostics["error"] = "KI-Ortsbereinigung war nicht verfügbar"
            return {}, diagnostics

        value = result.value if isinstance(result.value, dict) else {}
        if _contains_forbidden_key(value):
            diagnostics["error"] = "KI-Antwort enthielt unzulässige Koordinatenfelder"
            return {}, diagnostics

        suggestions: dict[str, dict[str, Any]] = {}
        for raw in value.get("items", []):
            if not isinstance(raw, dict):
                continue
            stop_id = _text(raw.get("stop_id"), 200)
            source = source_by_id.get(stop_id)
            if source is None or stop_id in suggestions:
                continue
            address_raw = raw.get("address") if isinstance(raw.get("address"), dict) else {}
            address: dict[str, str] = {}
            for key in _ALLOWED_ADDRESS_FIELDS:
                cleaned = _text(address_raw.get(key))
                if key == "country_code":
                    cleaned = cleaned.upper()
                    if len(cleaned) != 2 or not cleaned.isalpha():
                        cleaned = ""
                if cleaned:
                    address[key] = cleaned
            name = _text(raw.get("name"))
            place_kind = _text(raw.get("place_kind"), 100).casefold()
            if place_kind not in _ALLOWED_PLACE_KINDS:
                place_kind = ""
            search_terms: list[str] = []
            seen_terms: set[str] = set()
            for term in raw.get("search_terms", []) if isinstance(raw.get("search_terms"), list) else []:
                cleaned_term = _text(term, 120)
                key = cleaned_term.casefold()
                if cleaned_term and key not in seen_terms:
                    seen_terms.add(key)
                    search_terms.append(cleaned_term)
                if len(search_terms) >= 3:
                    break
            changed_fields: list[str] = []
            if name and name.casefold() != source.get("name", "").casefold():
                changed_fields.append("name")
            for key, cleaned in address.items():
                if cleaned.casefold() != source.get(key, "").casefold():
                    changed_fields.append(key)
            if place_kind:
                changed_fields.append("place_kind")
            if search_terms:
                changed_fields.append("search_terms")
            if not changed_fields:
                continue
            confidence_raw = raw.get("confidence")
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            suggestions[stop_id] = {
                "stop_id": stop_id,
                "name": name or source.get("name", ""),
                "address": deepcopy(address),
                "place_kind": place_kind or None,
                "search_terms": search_terms,
                "confidence": round(confidence, 3),
                "reason": _text(raw.get("reason"), 1_000),
                "changed_fields": changed_fields,
                "provider": _text(getattr(self._provider, "name", "ai"), 100) or "ai",
                "model": _text(result.model_version or getattr(self._provider, "model", ""), 200),
                "coordinate_policy": "not_provided_not_accepted",
            }

        diagnostics.update(
            {
                "suggested_count": len(suggestions),
                "provider": _text(getattr(self._provider, "name", "ai"), 100),
                "model": _text(result.model_version or getattr(self._provider, "model", ""), 200),
            }
        )
        return suggestions, diagnostics
