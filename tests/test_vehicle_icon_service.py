"""Behavioral tests for the vehicle icon generation service."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_vehicle_icon_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


class ValidationError(RuntimeError):
    pass


roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


assistant_provider = load("assistant_provider")
vehicle_icon_service = load("vehicle_icon_service")


class FakeProvider:
    def __init__(self, *, configured: bool = True):
        self.configured = configured
        self.prompts: list[str] = []

    async def async_generate_image(self, *, prompt: str):
        self.prompts.append(prompt)
        return assistant_provider.AssistantImageResult(
            data=b"\x89PNG\r\n\x1a\nfake",
            mime_type="image/png",
            model_version="gemini-2.5-flash-image",
            usage={"totalTokenCount": 42},
        )


async def verify_generate_vehicle_icon() -> None:
    provider = FakeProvider()
    result = await vehicle_icon_service.async_generate_vehicle_icon(
        provider, vehicle_description="cremefarbener Kastenwagen mit orangem Streifen"
    )
    assert result.mime_type == "image/png"
    assert result.data.startswith(b"\x89PNG")
    assert len(provider.prompts) == 1
    prompt = provider.prompts[0]
    assert "cremefarbener Kastenwagen mit orangem Streifen" in prompt
    assert "Kein Text" in prompt

    # Missing/blank description is rejected before any provider call.
    try:
        await vehicle_icon_service.async_generate_vehicle_icon(
            provider, vehicle_description="   "
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError for a blank description")
    assert len(provider.prompts) == 1  # no extra call was made

    # An overlong description is truncated rather than rejected or sent raw.
    long_description = "Kastenwagen " * 100
    await vehicle_icon_service.async_generate_vehicle_icon(
        provider, vehicle_description=long_description
    )
    assert len(provider.prompts) == 2
    assert len(provider.prompts[1]) < len(vehicle_icon_service._PROMPT_TEMPLATE) + len(
        long_description
    )

    # An unconfigured provider is rejected up front, without a call attempt.
    unconfigured = FakeProvider(configured=False)
    try:
        await vehicle_icon_service.async_generate_vehicle_icon(
            unconfigured, vehicle_description="Camper"
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("expected ValidationError for an unconfigured provider")
    assert unconfigured.prompts == []


asyncio.run(verify_generate_vehicle_icon())

print("Vehicle icon service tests passed.")
