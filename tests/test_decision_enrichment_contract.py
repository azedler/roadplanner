"""Regression checks for decision enrichment latency and failure isolation."""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/experience_manager.py").read_text(encoding="utf-8")

assert "_DECISION_GEOCODE_TIMEOUT_SECONDS" in SOURCE
assert "_DECISION_IMAGE_TIMEOUT_SECONDS" in SOURCE
assert "_DECISION_ROUTE_TIMEOUT_SECONDS" in SOURCE
assert "await asyncio.gather(" in SOURCE
assert "return_exceptions=True" in SOURCE
assert "a missing image or route must never discard" in SOURCE
assert "enrichment_duration_ms" in SOURCE

print("Decision enrichment concurrency contract tests passed.")
