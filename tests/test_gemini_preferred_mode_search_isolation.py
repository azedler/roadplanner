"""Regression test: a plain call must not poison a model's search-mode cache.

Real bug found live: a Booking.com link update kept failing to resolve a
place even after url_context/search support landed, with diagnostics showing
search_requested=true but search_used=false and request_mode=json_mime_only.

Root cause: GeminiClient._post() memoizes which request shape ("preferred
request mode") last succeeded for a given model, keyed only by the model
name, and sorts that shape to the front of the next call's variant list to
skip straight to whatever is known to work. Once an ordinary, non-search
call (for example the basket-item chat extraction) succeeded via the
schema-less "json_mime_only" fallback for a model, every later call to that
same model - including one that explicitly requested google_search/
url_context tools - reused that cached preference and tried the tool-less
mode first. Since json_mime_only almost always succeeds, the search-capable
"schema_search" variant was never attempted again, silently disabling
research indefinitely for that model.

Fix: key the memoized preference by (model, bool(search_requested)) instead
of by model alone, so a plain call's cached shape can never short-circuit a
call that actually needs the search/url_context tools, and vice versa.
"""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")

assert "self._preferred_request_modes: dict[tuple[str, bool], str] = {}" in SOURCE
assert "preferred_key = (model, bool(search_requested))" in SOURCE
assert "self._preferred_request_modes.get(preferred_key)" in SOURCE
assert "self._preferred_request_modes[preferred_key] = request_mode" in SOURCE
# The old model-only cache key must be fully gone, not just supplemented.
assert "self._preferred_request_modes.get(model)" not in SOURCE
assert "self._preferred_request_modes[model] = request_mode" not in SOURCE

print("Gemini preferred-request-mode search isolation tests passed.")
