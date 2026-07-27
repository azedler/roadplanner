"""Source-level contract for icon-style Gemini image generation."""
from pathlib import Path

PROVIDER = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")
INTERFACE = Path("custom_components/roadplanner_mcp/assistant_provider.py").read_text(encoding="utf-8")
SERVICE = Path("custom_components/roadplanner_mcp/vehicle_icon_service.py").read_text(encoding="utf-8")

assert "class AssistantImageResult" in INTERFACE
assert "async def async_generate_image" in INTERFACE

assert "async def async_generate_image" in PROVIDER
assert '"responseModalities": ["IMAGE"]' in PROVIDER
# The image model is a dedicated, never-fallback model: it must not reuse
# the configured text/vision model or its fallback for image requests.
assert "models_override=[self._image_model]" in PROVIDER
assert "image_model: str = \"gemini-2.5-flash-image\"" in PROVIDER
assert '"inlineData"' in PROVIDER

assert "async def async_generate_vehicle_icon" in SERVICE
assert "provider.async_generate_image" in SERVICE
assert "Kein Text, keine Buchstaben, kein Wasserzeichen im Bild." in SERVICE

print("Gemini image generation contract tests passed.")
