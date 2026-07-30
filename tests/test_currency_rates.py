"""Behavioral tests for the ECB rate service and the approximate EUR total.

Live request (screenshot of "Reisekosten 3.491,63 SEK · 2.659,06 € ·
614,23 PLN"): "Vielleicht wollen wir uns mal was überlegen für die
Währungen". Design: per-currency totals stay untouched and authoritative;
ADDITIONALLY the panel shows one approximate EUR total based on the daily
ECB reference rates (no API key), labeled with the rate date. A currency
without a rate is reported, never silently dropped - a partial sum without
that marker would be actively misleading.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_currency_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


currency = load("currency_rates")

ECB_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-07-30">
      <Cube currency="USD" rate="1.0842"/>
      <Cube currency="SEK" rate="11.213"/>
      <Cube currency="PLN" rate="4.2515"/>
      <Cube currency="BAD" rate="-1"/>
      <Cube currency="XX" rate="2.0"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def verify_ecb_xml_parses_to_dated_rates() -> None:
    parsed = currency.parse_ecb_daily_rates(ECB_SAMPLE)
    assert parsed is not None
    assert parsed["base"] == "EUR" and parsed["date"] == "2026-07-30"
    assert parsed["rates"]["SEK"] == 11.213 and parsed["rates"]["PLN"] == 4.2515
    assert "BAD" not in parsed["rates"], "non-positive rates are rejected"
    assert "XX" not in parsed["rates"], "malformed currency codes are rejected"


def verify_garbage_payloads_yield_none() -> None:
    assert currency.parse_ecb_daily_rates("kein xml") is None
    assert currency.parse_ecb_daily_rates("<Envelope/>") is None, (
        "a document without rates must never produce a misleading total"
    )


def verify_eur_total_matches_screenshot_scenario() -> None:
    rates = currency.parse_ecb_daily_rates(ECB_SAMPLE)["rates"]
    result = currency.eur_total(
        {"SEK": 3491.63, "EUR": 2659.06, "PLN": 614.23}, rates
    )
    expected = round(3491.63 / 11.213 + 2659.06 + 614.23 / 4.2515, 2)
    assert result["total"] == expected
    assert result["converted"] == ["EUR", "PLN", "SEK"]
    assert result["unconvertible"] == []


def verify_missing_rate_is_reported_not_dropped() -> None:
    result = currency.eur_total({"EUR": 100.0, "NOK": 500.0}, {"SEK": 11.2})
    assert result["total"] == 100.0
    assert result["unconvertible"] == ["NOK"], (
        "a currency without a rate must be named, never silently omitted"
    )


def verify_wiring_contract() -> None:
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert 'if action == "get_exchange_rates":' in panel_source
    archive_js = (PACKAGE_ROOT / "frontend/features/archive.js").read_text(
        encoding="utf-8"
    )
    assert "get_exchange_rates" in archive_js
    assert "EZB-Kurse vom" in archive_js, (
        "the approximate total must always name its rate date"
    )
    assert "kein Kurs" in archive_js, (
        "currencies without a rate are surfaced in the label"
    )


if __name__ == "__main__":
    verify_ecb_xml_parses_to_dated_rates()
    verify_garbage_payloads_yield_none()
    verify_eur_total_matches_screenshot_scenario()
    verify_missing_rate_is_reported_not_dropped()
    verify_wiring_contract()
    print("Currency rate tests passed.")
