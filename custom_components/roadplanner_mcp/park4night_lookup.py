"""Read a stop's Park4Night page via Gemini's url_context fetch.

Park4Night has no public API, so a p4n reference on a stop could never be
turned into coordinates deterministically - geocoding a generic name like
"Parkplatz am Angelteich" is hopeless, and the stop stayed at "Ort fehlt".
The assistant chat already fetches p4n pages during plan handover (see
gemini_client.py's url_context tool); this service brings the same
capability into the place-enrichment flow.

Trust model: coordinates produced by a language model are never written to
the roadbook directly. The lookup result appears in the enrichment review
dialog as a clearly labeled suggestion that only PREFILLS the existing
manual-confirmation path - the user confirms it like any hand-typed
coordinate, and it is stored as manually confirmed, not provider-verified.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .assistant_provider import AssistantProvider

_LOGGER = logging.getLogger(__name__)

_LOOKUP_TIMEOUT_SECONDS = 35

_LOOKUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "latitude": {"type": ["number", "null"]},
        "longitude": {"type": ["number", "null"]},
        "name": {"type": ["string", "null"]},
        "city": {"type": ["string", "null"]},
        "country_code": {"type": ["string", "null"]},
        "price_text": {"type": ["string", "null"]},
        "rating_text": {"type": ["string", "null"]},
        "summary": {"type": ["string", "null"]},
    },
    "required": ["found"],
}

_SYSTEM_INSTRUCTION = """Du liest genau eine Park4Night-Ortsseite über den url_context-Abruf und gibst ausschließlich Fakten zurück, die auf dieser Seite stehen.

Regeln:
- Öffne ausschließlich die angegebene URL. Keine anderen Quellen und kein eigenes Wissen.
- latitude/longitude: exakt die auf der Seite angegebene GPS-Position des Platzes. Wenn die Seite nicht erreichbar ist oder keine GPS-Angabe enthält, setze found=false und alle anderen Felder auf null. Schätze niemals Koordinaten.
- name: der Titel des Platzes auf der Seite, ohne ID.
- city/country_code: nur falls auf der Seite genannt (country_code als ISO-2).
- price_text/rating_text: kurz und wörtlich von der Seite (zum Beispiel "80 SEK / 24h", "4,67/5"), sonst null.
- summary: höchstens zwei kurze Sätze mit Ausstattung laut Seite, sonst null.
- Antworte ausschließlich im vorgegebenen JSON-Schema."""


def _text(value: Any, maximum: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


class Park4NightLookupService:
    """One bounded, url_context-backed read of a single Park4Night page."""

    def __init__(self, provider: AssistantProvider | None) -> None:
        self._provider = provider

    @property
    def available(self) -> bool:
        return bool(self._provider is not None and self._provider.configured)

    async def async_lookup(self, url: str, *, hint_name: str = "") -> dict[str, Any] | None:
        """Return reviewable page facts, or None when nothing usable came back."""
        url = _text(url, 500)
        if not url.startswith("https://park4night.com/") or not self.available:
            return None
        assert self._provider is not None
        try:
            async with asyncio.timeout(_LOOKUP_TIMEOUT_SECONDS):
                result = await self._provider.async_generate_json_result(
                    system_instruction=_SYSTEM_INSTRUCTION,
                    messages=[
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"url": url, "erwarteter_name": _text(hint_name, 200)},
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        }
                    ],
                    schema=_LOOKUP_SCHEMA,
                    enable_search=True,
                    max_output_tokens=1024,
                    temperature=0.0,
                )
        except Exception as err:  # An optional lookup must never break enrichment.
            _LOGGER.warning("Park4Night lookup failed: %s", type(err).__name__)
            return None
        value = result.value if isinstance(result.value, dict) else {}
        if value.get("found") is not True:
            return None
        latitude = value.get("latitude")
        longitude = value.get("longitude")
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or not isinstance(longitude, (int, float))
            or not (-90 <= latitude <= 90)
            or not (-180 <= longitude <= 180)
            or (latitude == 0 and longitude == 0)
        ):
            return None
        country_code = _text(value.get("country_code"), 10).upper()
        if len(country_code) != 2 or not country_code.isalpha():
            country_code = ""
        return {
            "provider": "park4night_ai",
            "url": url,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "name": _text(value.get("name"), 200),
            "city": _text(value.get("city"), 200),
            "country_code": country_code,
            "price_text": _text(value.get("price_text"), 100),
            "rating_text": _text(value.get("rating_text"), 100),
            "summary": _text(value.get("summary"), 400),
        }
