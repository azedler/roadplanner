"""Contract and behavior tests for the enrichment link lookup.

Live request: "Ich würde gern die Möglichkeit haben beim Anreichern eines
Ortes einen Link anzugeben. Z.B. zu Google Maps oder P4n oder sowas".

Design: the new panel action ``place_link_lookup`` takes one https URL.
Google-Maps links resolve deterministically (no AI); Park4Night links use
the specialized page reader; every other page goes through the generalized
AI page lookup. Results are ONLY a prefill for the manual-confirmation
form - the trust model of the p4n reader applies unchanged.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import re
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_place_link_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


lookup_module = load("park4night_lookup")


class _RecordingProvider:
    configured = True

    def __init__(self):
        self.calls = []

    async def async_generate_json_result(self, **kwargs):
        self.calls.append(kwargs)
        value = {
            "found": True,
            "latitude": 63.07,
            "longitude": 18.35,
            "name": "Camping Testplatz",
            "city": "Docksta",
            "country_code": "se",
            "price_text": "250 SEK",
            "rating_text": None,
            "summary": "Strom und Duschen laut Seite.",
        }
        return types.SimpleNamespace(value=value)


def _run(coro):
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def verify_generic_page_lookup_reads_any_https_url() -> None:
    provider = _RecordingProvider()
    service = lookup_module.Park4NightLookupService(provider)
    result = _run(service.async_lookup_page("https://www.booking.com/hotel/se/x.html"))
    assert result is not None
    assert result["provider"] == "place_page_ai"
    assert result["latitude"] == 63.07 and result["country_code"] == "SE"
    system_instruction = provider.calls[0]["system_instruction"]
    assert "Schätze niemals Koordinaten" in system_instruction, (
        "the generic reader must carry the same never-guess rule"
    )


def verify_generic_lookup_rejects_non_https() -> None:
    service = lookup_module.Park4NightLookupService(_RecordingProvider())
    assert _run(service.async_lookup_page("http://unsicher.example/ort")) is None
    assert _run(service.async_lookup_page("kein-link")) is None


def verify_p4n_lookup_stays_restricted_to_p4n() -> None:
    provider = _RecordingProvider()
    service = lookup_module.Park4NightLookupService(provider)
    assert _run(service.async_lookup("https://www.booking.com/hotel")) is None
    assert provider.calls == [], "the p4n reader must never fetch foreign pages"


def verify_panel_action_routing_contract() -> None:
    source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert 'if action == "place_link_lookup":' in source
    google_at = source.index("_google_maps_host(host)")
    ai_at = source.index("async_lookup_page(url", google_at)
    assert google_at < ai_at, (
        "Google-Maps links must resolve deterministically BEFORE any AI read"
    )
    p4n_at = source.index("_PARK4NIGHT_ID_RE.search(url)", google_at)
    assert p4n_at < ai_at, "p4n links keep the specialized reader"
    for action_list_marker in ('"place_link_lookup",',):
        assert source.count(action_list_marker) >= 3, (
            "the action must be registered (actions, assistant gate, "
            "provider-call shield)"
        )


def verify_frontend_wiring_contract() -> None:
    enrichment = (PACKAGE_ROOT / "frontend/features/place-enrichment.js").read_text(
        encoding="utf-8"
    )
    assert "data-place-link-input" in enrichment
    assert '_runPlaceLinkLookup' in enrichment
    assert '"__manual__"' in enrichment, (
        "coordinates from a link select the manual-confirmation path - the "
        "user's explicit save stays the confirmation step"
    )
    panel_js = (PACKAGE_ROOT / "frontend/roadplanner-panel.js").read_text(
        encoding="utf-8"
    )
    assert 'action === "place-link-lookup"' in panel_js


if __name__ == "__main__":
    verify_generic_page_lookup_reads_any_https_url()
    verify_generic_lookup_rejects_non_https()
    verify_p4n_lookup_stays_restricted_to_p4n()
    verify_panel_action_routing_contract()
    verify_frontend_wiring_contract()
    print("Place link lookup tests passed.")
