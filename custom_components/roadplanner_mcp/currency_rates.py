"""Daily ECB reference exchange rates for the trip-cost overview.

The travel archive keeps expense totals strictly separated by currency -
original amounts are never rewritten. This module only supplies the daily
European Central Bank reference rates (no API key, published once per
working day) so the panel can ADDITIONALLY show an approximate EUR total,
clearly labeled with the rate date. Rates are cached in memory; on fetch
failure the last known rates keep serving (stale is better than nothing
for an explicitly approximate number).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
import xml.etree.ElementTree as ET

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

ECB_DAILY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_CACHE_SECONDS = 6 * 3600
_FETCH_TIMEOUT_SECONDS = 20
_MAX_RESPONSE_BYTES = 512 * 1024


def parse_ecb_daily_rates(payload: bytes | str) -> dict[str, Any] | None:
    """Parse the ECB daily reference XML into {"date": ..., "rates": {...}}.

    Returns None for anything that does not contain at least one plausible
    rate - a partial document must never yield a misleading EUR total.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    date = ""
    rates: dict[str, float] = {}
    for cube in root.iter():
        tag = cube.tag.rsplit("}", 1)[-1]
        if tag != "Cube":
            continue
        if cube.get("time"):
            date = str(cube.get("time"))
        currency = str(cube.get("currency") or "").upper()
        raw_rate = cube.get("rate")
        if len(currency) == 3 and currency.isalpha() and raw_rate is not None:
            try:
                rate = float(raw_rate)
            except ValueError:
                continue
            if rate > 0:
                rates[currency] = rate
    if not date or not rates:
        return None
    return {"base": "EUR", "date": date, "rates": rates}


def eur_total(totals_by_currency: dict[str, Any], rates: dict[str, float]) -> dict[str, Any]:
    """Convert per-currency totals into one approximate EUR sum.

    Returns {"total": float, "converted": [...], "unconvertible": [...]}.
    A currency without a rate is reported instead of silently dropped -
    a partial sum without that marker would be actively misleading.
    """
    total = 0.0
    converted: list[str] = []
    unconvertible: list[str] = []
    for currency, amount in (totals_by_currency or {}).items():
        code = str(currency or "").upper()
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            continue
        if code == "EUR":
            total += float(amount)
            converted.append(code)
            continue
        rate = rates.get(code)
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > 0:
            total += float(amount) / float(rate)
            converted.append(code)
        else:
            unconvertible.append(code)
    return {
        "total": round(total, 2),
        "converted": sorted(converted),
        "unconvertible": sorted(unconvertible),
    }


class EcbRateService:
    """Cached fetcher for the ECB daily reference rates."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._cache: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def async_get_rates(self) -> dict[str, Any] | None:
        async with self._lock:
            if self._cache is not None and time.monotonic() - self._fetched_at < _CACHE_SECONDS:
                return self._cache
            session = async_get_clientsession(self._hass)
            try:
                async with session.get(
                    ECB_DAILY_RATES_URL,
                    timeout=ClientTimeout(total=_FETCH_TIMEOUT_SECONDS),
                ) as response:
                    if response.status != 200:
                        raise ClientError(f"HTTP {response.status}")
                    payload = await response.content.read(_MAX_RESPONSE_BYTES)
            except (ClientError, asyncio.TimeoutError, OSError) as err:
                _LOGGER.debug("ECB rate fetch failed: %s", err)
                return self._cache
            parsed = parse_ecb_daily_rates(payload)
            if parsed is None:
                _LOGGER.debug("ECB rate payload could not be parsed")
                return self._cache
            self._cache = parsed
            self._fetched_at = time.monotonic()
            return parsed
