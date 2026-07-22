"""Source contracts for bounded automatic planning-image enrichment."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manager = (ROOT / "custom_components" / "roadplanner_mcp" / "experience_manager.py").read_text(encoding="utf-8")
panel = (ROOT / "custom_components" / "roadplanner_mcp" / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")

assert "async_call_later" in manager
assert "_DESTINATION_BACKGROUND_INTERVAL_MINUTES" in manager
assert "_DESTINATION_BACKGROUND_BATCH" in manager
assert "_reschedule_destination_enrichment" in manager
assert "_async_periodic_destination_enrichment" in manager
assert '"source": "destination_image_enrichment"' in manager
assert "own_media_stop_ids" in manager
assert "stop_id in own_media_stop_ids" in manager
assert "include_experience: bool = True" in manager
assert 'limit=_DESTINATION_BACKGROUND_BATCH' in manager

# The panel starts one small best-effort batch; it no longer loops through the
# entire trip every time the user opens the dashboard.
method = panel.split("async _maybeAutoPopulateDestinationGalleries(payload)", 1)[1].split("_setBusy(value)", 1)[0]
assert "for (let batch" not in method
assert 'limit: 4' in method
assert 'destination_enrichment?.state === "running"' in method

print("Background visual enrichment contract tests passed.")
