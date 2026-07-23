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

# Place completion remains review-only, keeps existing IDs and submits concrete
# selected profiles through the normal handoff/ChangeSet path.
experience = (ROOT / "experience_manager.py").read_text(encoding="utf-8")
place_enrichment = (ROOT / "place_enrichment.py").read_text(encoding="utf-8")
assert "async_prepare_place_enrichment" in experience
assert "async_submit_place_enrichment" in experience
assert "async_ingest_external_changeset" in experience
assert '"action": "update"' in place_enrichment
assert '"entity_type": "stop"' in place_enrichment
assert '"location": deepcopy(candidate.get("location") or {})' in place_enrichment
assert '"place_profile"' in place_enrichment
assert 'stop.get("_source_day_id") if stop.get("_inherited") else day.get("id")' in place_enrichment
assert '"prepare_place_enrichment"' in panel
assert '"submit_place_enrichment"' in panel
assert 'data-action="complete-day-locations"' in frontend
assert "Orte vervollständigen" in frontend
assert 'data-action="integrity-prepare-locations"' in frontend
assert 'data-action="complete-stop-place"' in frontend

print("Canonical day plan integrity contract tests passed.")
