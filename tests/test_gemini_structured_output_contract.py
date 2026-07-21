"""Source-level contract tests for Gemini structured-output repair."""
from pathlib import Path

source = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")
assert "parse_structured_object" in source
assert "async def _repair_structured_output" in source
assert '"structured_output_repaired"' in source
assert '"structured_output_normalization"' in source
assert "Repariere die bereitgestellte Modellantwort" in source
assert "Erfinde keine fachlichen Daten" in source
assert "_merge_usage" in source

print("Gemini structured-output repair contract tests passed.")
