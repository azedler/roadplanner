"""Source-level contract for bounded Gemini multi-image analysis."""
from pathlib import Path

PROVIDER = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")
MANAGER = (
    Path("custom_components/roadplanner_mcp/experience_manager.py").read_text(encoding="utf-8")
    + Path("custom_components/roadplanner_mcp/media_vision_curation.py").read_text(encoding="utf-8")
    + Path("custom_components/roadplanner_mcp/media_curation_manager.py").read_text(encoding="utf-8")
    + Path("custom_components/roadplanner_mcp/destination_gallery_manager.py").read_text(encoding="utf-8")
)
CONFIG = Path("custom_components/roadplanner_mcp/const.py").read_text(encoding="utf-8")
PANEL = Path("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js").read_text(encoding="utf-8") + Path(
    "custom_components/roadplanner_mcp/frontend/features/trip-day-stop.js"
).read_text(encoding="utf-8")

assert "async def async_analyze_images" in PROVIDER
assert '"inlineData"' in PROVIDER
# A ceiling on how many images one call may carry still has to exist -
# that is what this line guards. It is no longer the literal 15, because
# the two callers legitimately want different numbers: the stop curation
# compares one stop's best few (its own option tops out at 15, which
# stays the default here), while the day curation batches wider on
# purpose and passes its own. Pinning the literal made the DAY path
# unfixable without failing this test, and a hard 15 silently left every
# day with 16 to 24 photographs unanalysed on the real trip. The
# agreement between the two numbers is asserted where it can be checked
# against both modules at once - test_day_curation_service.py's
# `verify_the_batch_size_and_the_provider_limit_agree`.
assert 'max_images: int = 15' in PROVIDER
assert 'if len(images) > ceiling' in PROVIDER
assert 'total_bytes > 10_000_000' in PROVIDER
assert 'responseJsonSchema' in PROVIDER

assert "select_media_highlights" in MANAGER
assert "async_curate" in MANAGER
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
# The per-stop curation has to be offered; how it is drawn is not this
# test's business. It used to be a hand-written button and is now rendered
# through the shared action helper, which is what puts a price on it -
# pinning the markup pinned the spelling rather than the feature.
assert '"media-curate-stop"' in PANEL
assert "Lokal vorgefiltert · KI kuratiert" in PANEL

print("Gemini Vision local-first contract tests passed.")
