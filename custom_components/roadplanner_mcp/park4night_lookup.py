"""Read a stop's Park4Night page - directly first, via Gemini as fallback.

Park4Night has no public API, but the place page itself states the GPS
position in its HTML ("63.1353, 18.5161 (lat, lng)", embedded JSON, meta
tags). The page is therefore fetched DIRECTLY and parsed deterministically
- no AI involved, so the lookup keeps working when the Gemini quota is
exhausted (live report: the adoption always failed with "Die Seite konnte
nicht gelesen werden" while the provider was out of credit). Only when the
direct fetch cannot produce coordinates does the Gemini url_context reader
run as a fallback; generic non-p4n pages still use Gemini exclusively.

Trust model: page-derived coordinates are never written to the roadbook
directly. The lookup result appears in the enrichment review dialog as a
clearly labeled suggestion that only PREFILLS the existing
manual-confirmation path - the user confirms it like any hand-typed
coordinate, and it is stored as manually confirmed, not provider-verified.
"""

from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import re
from typing import Any

from .assistant_provider import AssistantProvider

_LOGGER = logging.getLogger(__name__)

_LOOKUP_TIMEOUT_SECONDS = 35
_DIRECT_FETCH_TIMEOUT_SECONDS = 20
_MAX_HTML_BYTES = 2_000_000
_DIRECT_FETCH_USER_AGENT = (
    "Mozilla/5.0 (compatible; Roadplanner-HomeAssistant; "
    "+https://github.com/azedler/roadplanner)"
)

# Coordinate patterns seen on park4night place pages, most specific first:
# embedded JSON ("lat":"63.1353","lng":"18.5161"), geo meta tags, and the
# visible "63.1353, 18.5161 (lat, lng)" text block.
_COORD_PATTERNS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(r'"lat(?:itude)?"\s*:\s*"?(-?\d{1,2}(?:\.\d+)?)"?'),
        re.compile(r'"(?:lng|lon(?:gitude)?)"\s*:\s*"?(-?\d{1,3}(?:\.\d+)?)"?'),
    ),
    (
        re.compile(
            r'(?:place|og|geo)[:.]?(?:location:|position:)?latitude"?\s*(?:content=)?"?'
            r"(-?\d{1,2}(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        re.compile(
            r'(?:place|og|geo)[:.]?(?:location:|position:)?longitude"?\s*(?:content=)?"?'
            r"(-?\d{1,3}(?:\.\d+)?)",
            re.IGNORECASE,
        ),
    ),
)
_COORD_TEXT_PATTERN = re.compile(
    r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})\s*\(lat,?\s*ln?g\)",
    re.IGNORECASE,
)
_TITLE_PATTERN = re.compile(
    r'og:title"\s+content="([^"]{1,300})"|<title>([^<]{1,300})</title>',
    re.IGNORECASE,
)
_RATING_PATTERN = re.compile(r"(\d[.,]\d{1,2})\s*/\s*5")


def _valid_coordinate(latitude: float, longitude: float) -> bool:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return False
    return not (latitude == 0 and longitude == 0)


def parse_park4night_page(page_html: str, *, url: str) -> dict[str, Any] | None:
    """Deterministically extract place facts from a p4n page's HTML."""
    if not page_html:
        return None
    latitude = longitude = None
    text_match = _COORD_TEXT_PATTERN.search(page_html)
    if text_match is not None:
        latitude = float(text_match.group(1))
        longitude = float(text_match.group(2))
    if latitude is None:
        for lat_pattern, lon_pattern in _COORD_PATTERNS:
            lat_match = lat_pattern.search(page_html)
            lon_match = lon_pattern.search(page_html)
            if lat_match is None or lon_match is None:
                continue
            latitude = float(lat_match.group(1))
            longitude = float(lon_match.group(1))
            break
    if (
        latitude is None
        or longitude is None
        or not _valid_coordinate(latitude, longitude)
    ):
        return None
    name = ""
    title_match = _TITLE_PATTERN.search(page_html)
    if title_match is not None:
        raw_title = title_match.group(1) or title_match.group(2) or ""
        name = _text(html_module.unescape(raw_title), 200)
        name = re.sub(
            r"\s*[-|·]\s*park4night.*$", "", name, flags=re.IGNORECASE
        ).strip()
    rating_match = _RATING_PATTERN.search(page_html)
    return {
        "provider": "park4night_page",
        "url": url,
        "latitude": latitude,
        "longitude": longitude,
        "name": name,
        "city": "",
        "country_code": "",
        "price_text": "",
        "rating_text": (
            f"{rating_match.group(1)}/5" if rating_match is not None else ""
        ),
        "summary": "",
    }

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


_PAGE_SYSTEM_INSTRUCTION = """Du liest genau eine Ortsseite (Stellplatz-, Campingplatz-, Unterkunfts-, Buchungs- oder sonstige Seite zu einem konkreten Ort) über den url_context-Abruf und gibst ausschließlich Fakten zurück, die auf dieser Seite stehen.

Regeln:
- Öffne ausschließlich die angegebene URL. Keine anderen Quellen und kein eigenes Wissen.
- latitude/longitude: exakt die auf der Seite angegebene GPS-Position des Ortes. Wenn die Seite nicht erreichbar ist oder keine GPS-Angabe enthält, setze found=false und alle anderen Felder auf null. Schätze niemals Koordinaten - auch nicht aus einer Adresse.
- name: der Titel des Ortes auf der Seite, ohne IDs oder Werbezusätze.
- city/country_code: nur falls auf der Seite genannt (country_code als ISO-2).
- price_text/rating_text: kurz und wörtlich von der Seite (zum Beispiel "80 SEK / 24h", "4,67/5"), sonst null.
- summary: höchstens zwei kurze Sätze mit Ausstattung/Beschreibung laut Seite, sonst null.
- Antworte ausschließlich im vorgegebenen JSON-Schema."""


def _text(value: Any, maximum: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


class Park4NightLookupService:
    """One bounded read of a single place page: direct fetch, AI fallback."""

    def __init__(self, provider: AssistantProvider | None, session: Any = None) -> None:
        self._provider = provider
        self._session = session

    @property
    def available(self) -> bool:
        return bool(
            self._session is not None
            or (self._provider is not None and self._provider.configured)
        )

    async def _async_direct_p4n_facts(self, url: str) -> dict[str, Any] | None:
        """Fetch and parse the p4n page without any AI involvement."""
        if self._session is None:
            return None
        try:
            async with asyncio.timeout(_DIRECT_FETCH_TIMEOUT_SECONDS):
                async with self._session.get(
                    url,
                    headers={
                        "User-Agent": _DIRECT_FETCH_USER_AGENT,
                        "Accept-Language": "de,en;q=0.8",
                    },
                ) as response:
                    if response.status != 200:
                        return None
                    raw = await response.content.read(_MAX_HTML_BYTES)
        except Exception as err:  # noqa: BLE001 - fall back to the AI reader
            _LOGGER.debug("Direct Park4Night fetch failed: %s", type(err).__name__)
            return None
        return parse_park4night_page(
            raw.decode("utf-8", errors="replace"), url=url
        )

    async def async_lookup(self, url: str, *, hint_name: str = "") -> dict[str, Any] | None:
        """Return reviewable page facts, or None when nothing usable came back."""
        url = _text(url, 500)
        if not url.startswith("https://park4night.com/"):
            return None
        direct = await self._async_direct_p4n_facts(url)
        if direct is not None:
            return direct
        return await self._async_lookup(
            url,
            hint_name=hint_name,
            system_instruction=_SYSTEM_INSTRUCTION,
            provider_name="park4night_ai",
        )

    async def async_lookup_page(
        self, url: str, *, hint_name: str = ""
    ) -> dict[str, Any] | None:
        """Read ANY https place page (Booking, campsite homepage, ...).

        Same trust model as the Park4Night reader: the result is only ever a
        prefill for the user's manual confirmation, never written to the
        roadbook on its own.
        """
        url = _text(url, 500)
        if not url.startswith("https://"):
            return None
        return await self._async_lookup(
            url,
            hint_name=hint_name,
            system_instruction=_PAGE_SYSTEM_INSTRUCTION,
            provider_name="place_page_ai",
        )

    async def _async_lookup(
        self,
        url: str,
        *,
        hint_name: str,
        system_instruction: str,
        provider_name: str,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        assert self._provider is not None
        try:
            async with asyncio.timeout(_LOOKUP_TIMEOUT_SECONDS):
                result = await self._provider.async_generate_json_result(
                    system_instruction=system_instruction,
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
            _LOGGER.warning("Place page lookup failed: %s", type(err).__name__)
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
            "provider": provider_name,
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
