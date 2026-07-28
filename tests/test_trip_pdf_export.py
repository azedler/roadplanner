"""Behavioral + contract tests for trip PDF export orchestration.

The ticket lifecycle (create/resolve/expire/limited reuse) is pure asyncio
with no Home Assistant or network dependency, so it's exercised directly
against the real TripPdfExporter methods. The data-gathering/photo-fetch
path (async_generate) needs aiohttp/Home Assistant, so that part is only
covered by source-level contract checks below.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_pdf_export_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules["aiohttp"] = aiohttp_stub

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules["homeassistant.core"] = ha_core
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules["homeassistant.helpers"] = ha_helpers
ha_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp_client.async_get_clientsession = lambda *a, **k: None
sys.modules["homeassistant.helpers.aiohttp_client"] = ha_aiohttp_client


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


for module_name in ("bounded_json", "json_io", "identifiers", "json_tree_validation"):
    try:
        load(module_name)
    except FileNotFoundError:
        pass

load("canonical_day")
load("trip_pdf")
export_module = load("trip_pdf_export")


def _exporter():
    return export_module.TripPdfExporter(hass=None, manager=None, experience=None)


def verify_ticket_round_trip() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-1234", user_id="user-1")
        resolved = await exporter.async_resolve_ticket(token)
        assert resolved == b"%PDF-1234"

    asyncio.run(scenario())


def verify_ticket_is_single_use_limited() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-abc", user_id="user-1")
        for _ in range(export_module._MAX_TICKET_USES):
            await exporter.async_resolve_ticket(token)
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected the exhausted ticket to be rejected")

    asyncio.run(scenario())


def verify_unknown_token_is_rejected() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        try:
            await exporter.async_resolve_ticket("does-not-exist")
        except export_module.ValidationError:
            return
        raise AssertionError("expected an unknown token to be rejected")

    asyncio.run(scenario())


def verify_expired_ticket_is_purged() -> None:
    async def scenario() -> None:
        exporter = _exporter()
        token = await exporter.async_create_ticket(b"%PDF-old", user_id="user-1")
        exporter._tickets[token].expires_monotonic = 0.0
        try:
            await exporter.async_resolve_ticket(token)
        except export_module.ValidationError:
            return
        raise AssertionError("expected an expired ticket to be purged and rejected")

    asyncio.run(scenario())


verify_ticket_round_trip()
verify_ticket_is_single_use_limited()
verify_unknown_token_is_rejected()
verify_expired_ticket_is_purged()

# Source-level contract checks for the parts that need real aiohttp/Home
# Assistant network access to exercise behaviorally.
SOURCE = (PACKAGE_ROOT / "trip_pdf_export.py").read_text(encoding="utf-8")
assert 'casefold() == "google_places"' in SOURCE, (
    "Google Places photos resolve to an internal, session-authenticated "
    "redirect - a server-side export job must not try to fetch them directly"
)
assert 'startswith("https://")' in SOURCE, (
    "only a plain, directly fetchable HTTPS image URL may be downloaded"
)
assert "_MAX_PHOTO_BYTES" in SOURCE and "content_length" in SOURCE, (
    "a downloaded photo must be bounded in size"
)

print("Trip PDF export tests passed.")
