"""Regression guard: an HTTP 400 compatibility fallback must log its cause.

Real diagnosis gap: when Gemini rejects a request_mode with HTTP 400 (invalid
argument), GeminiClient._post() silently moves to the next, more compatible
request shape - and if a later shape eventually succeeds, the whole call
reports success with only aggregate counters (compatibility_fallback_count),
never the actual provider error text. That made a live report of persistent
schema_search/schema_no_search rejections across several current models
impossible to diagnose without adding a log line and reproducing again.
Every HTTP 400 compatibility fallback must now emit a debug log with the
request_mode, model, and the provider's own error detail.
"""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")

needle = (
    '_LOGGER.debug(\n'
    '                                    "Gemini rejected request_mode=%s for model=%s "\n'
    '                                    "with HTTP 400: %s",\n'
    '                                    request_mode,\n'
    '                                    model,\n'
    '                                    err.provider_detail or str(err),\n'
    '                                )'
)
assert needle in SOURCE

print("Gemini invalid-request debug log tests passed.")
