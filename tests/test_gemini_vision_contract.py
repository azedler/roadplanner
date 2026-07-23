"""Source-level contract for bounded Gemini multi-image analysis."""
from pathlib import Path

PROVIDER = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")
MANAGER = Path("custom_components/roadplanner_mcp/experience_manager.py").read_text(encoding="utf-8")
CONFIG = Path("custom_components/roadplanner_mcp/const.py").read_text(encoding="utf-8")
PANEL = Path("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js").read_text(encoding="utf-8")

assert "async def async_analyze_images" in PROVIDER
assert '"inlineData"' in PROVIDER
assert 'if len(images) > 15' in PROVIDER
assert 'total_bytes > 10_000_000' in PROVIDER
assert 'responseJsonSchema' in PROVIDER

assert "select_media_highlights" in MANAGER
assert "_async_semantic_curation" in MANAGER
assert "reserve_vision_call" in MANAGER
assert "media_vision_daily_limit" in MANAGER
assert "local_value" in MANAGER
assert "local_fallback" in MANAGER
assert "async_auto_curate_media" in MANAGER
assert "async_curate_stop_media" in MANAGER
assert '"mode": "manual"' in MANAGER
assert 'in {"hybrid_vision", "manual"}' in MANAGER

assert 'DEFAULT_MEDIA_CURATION_MODE = "local"' in CONFIG
assert 'MEDIA_CURATION_MODES = ("local", "hybrid")' in CONFIG
assert 'data-action="media-curate-stop"' in PANEL
assert "Lokal vorgefiltert · KI kuratiert" in PANEL

print("Gemini Vision local-first contract tests passed.")
