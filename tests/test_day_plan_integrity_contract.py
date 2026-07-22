"""Source-level contracts for the canonical day mutation and assistant pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "roadplanner_mcp"
roadplanner = (ROOT / "roadplanner.py").read_text(encoding="utf-8")
changeset = (ROOT / "changeset.py").read_text(encoding="utf-8")
assistant = (ROOT / "assistant.py").read_text(encoding="utf-8")
prompt = (ROOT / "assistant_prompt.py").read_text(encoding="utf-8")
panel = (ROOT / "panel.py").read_text(encoding="utf-8")
frontend = (ROOT / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")

# Every canonical mutation path must leave a complete one-based position set.
assert "normalize_stop_sequence(stops)" in roadplanner
assert roadplanner.count("reindex_explicit_positions(target[\"stops\"])") >= 3
assert changeset.count("reindex_explicit_positions(document[\"stops\"])") >= 3

# The assistant plans a day sequence explicitly and never treats times as order.
assert "position_state" in assistant
assert 'operation["position"] = insert_at + 1' in assistant
assert "Zeiten beschreiben nur den Tagesablauf und dürfen niemals zur Sortierung" in prompt
assert "Jede neue Stoppoperation enthält eine positive position" in prompt

# GPS completion remains review-only and uses existing geocoding enrichment.
assert "async_add_location_drafts" in assistant
assert '"action": "update"' in assistant
assert '"place_query": place_query' in assistant
assert 'stop.get("_source_day_id") if stop.get("_inherited") else day_id' in assistant
assert '"assistant_prepare_locations"' in panel
assert 'data-action="complete-day-locations"' in frontend
assert "GPS prüfen/ergänzen" in frontend

print("Canonical day plan integrity contract tests passed.")
