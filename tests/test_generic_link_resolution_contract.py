"""Source-level contract for resolving non-Google-Maps links via Gemini's
own url_context tool, instead of one bespoke parser per booking provider."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "roadplanner_mcp"
GEMINI = (ROOT / "gemini_client.py").read_text(encoding="utf-8")
PROMPT = (ROOT / "assistant_prompt.py").read_text(encoding="utf-8")

# The compiled-operations JSON call must offer both google_search (general
# grounding) and url_context (fetching one specific link the user gave, e.g.
# a Booking.com/Park4Night share link that general search cannot find) in the
# same request, gated behind the same enable_search/structured-tools check
# that already exists for Google Search grounding.
assert '"tools": [{"google_search": {}}, {"url_context": {}}]' in GEMINI

# Google Maps keeps its cheap, deterministic, no-fetch fast path.
assert "Google-Maps-Link" in PROMPT
assert "Link unverändert und vollständig als place_query ausgeben" in PROMPT

# Every other link goes through the model reading the page itself, still
# only ever producing text (place_query / notes) that the server verifies -
# never trusting model-reported coordinates for these.
assert "über das" in PROMPT and "URL-Werkzeug ab" in PROMPT
assert "niemals Koordinaten aus dem Seiteninhalt übernehmen" in PROMPT
assert "Ist die Seite" in PROMPT and "nicht abrufbar" in PROMPT

print("Generic link resolution contract tests passed.")
