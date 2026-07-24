"""Contract tests for optional AI place-text cleanup."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_place_cleanup_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package
provider_module = types.ModuleType(f"{PACKAGE_NAME}.assistant_provider")
provider_module.AssistantProvider = object
sys.modules[provider_module.__name__] = provider_module

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.place_cleanup",
    PACKAGE_ROOT / "place_cleanup.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class Result:
    def __init__(self, value, model_version="test-model-v1"):
        self.value = value
        self.model_version = model_version


class FakeProvider:
    configured = True
    name = "gemini"
    model = "gemini-test"

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.kwargs = None

    async def async_generate_json_result(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return Result(self.value)


input_item = {
    "stop_id": "stop-1",
    "name": "Tallin",
    "stop_type": "city",
    "day_date": "2026-07-24",
    "day_title": "Estland",
    "notes": "Altstadt",
    "address": {"city": "Tallin", "country": "Estland"},
    "latitude": 59.4,
    "longitude": 24.7,
    "location": {"latitude": 59.4, "longitude": 24.7},
}


async def main() -> None:
    provider = FakeProvider(
        {
            "items": [
                {
                    "stop_id": "stop-1",
                    "name": "Tallinn",
                    "address": {"city": "Tallinn", "country": "Estland", "country_code": "EE"},
                    "place_kind": "place",
                    "search_terms": ["Tallinn city", "Tallinn city", "Estonia capital"],
                    "confidence": 0.96,
                    "reason": "Offensichtliche Schreibweise vereinheitlicht.",
                }
            ]
        }
    )
    service = module.PlaceCleanupService(provider)
    suggestions, diagnostics = await service.async_suggest_many([input_item])
    assert diagnostics["suggested_count"] == 1
    suggestion = suggestions["stop-1"]
    assert suggestion["name"] == "Tallinn"
    assert suggestion["address"]["city"] == "Tallinn"
    assert suggestion["address"]["country_code"] == "EE"
    assert suggestion["place_kind"] == "place"
    assert suggestion["search_terms"] == ["Tallinn city", "Estonia capital"]
    assert "place_kind" in suggestion["changed_fields"]
    assert "search_terms" in suggestion["changed_fields"]
    assert "name" in suggestion["changed_fields"]
    assert suggestion["coordinate_policy"] == "not_provided_not_accepted"

    assert provider.kwargs is not None
    assert provider.kwargs["enable_search"] is False
    payload = json.loads(provider.kwargs["messages"][0]["content"])
    supplied_stop = payload["stops"][0]
    assert "latitude" not in supplied_stop
    assert "longitude" not in supplied_stop
    assert "location" not in supplied_stop
    assert "notes" not in supplied_stop
    assert "gps" not in json.dumps(payload, ensure_ascii=False).casefold()

    coordinate_provider = FakeProvider(
        {
            "items": [
                {
                    "stop_id": "stop-1",
                    "name": "Tallinn",
                    "address": {"city": "Tallinn"},
                    "latitude": 59.4,
                    "longitude": 24.7,
                }
            ]
        }
    )
    rejected, rejected_diagnostics = await module.PlaceCleanupService(
        coordinate_provider
    ).async_suggest_many([input_item])
    assert rejected == {}
    assert "Koordinatenfelder" in rejected_diagnostics["error"]

    failing_provider = FakeProvider(error=RuntimeError("provider unavailable"))
    fallback, fallback_diagnostics = await module.PlaceCleanupService(
        failing_provider
    ).async_suggest_many([input_item])
    assert fallback == {}
    assert fallback_diagnostics["error"] == "KI-Ortsbereinigung war nicht verfügbar"


asyncio.run(main())
print("AI place cleanup tests passed.")
