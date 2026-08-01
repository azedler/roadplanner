"""Source-level contract for lite-model routing of bounded extraction tasks.

Receipt/document analysis (async_analyze_binary) and photo curation
(async_analyze_images) are pure schema extraction - no search, no long
context - so they run on the cheap lite tier first, with the configured
primary model as the in-call backup. The advisory chat, compile and
research paths must NOT be routed to the lite tier: they need the full
Flash tier's long-context instruction following.
"""
from pathlib import Path

PROVIDER = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")
CONST = Path("custom_components/roadplanner_mcp/const.py").read_text(encoding="utf-8")
INIT = Path("custom_components/roadplanner_mcp/__init__.py").read_text(encoding="utf-8")

# The default models stay pinned versions: the client builds requests
# differently per model generation, so a floating alias like
# "gemini-flash-latest" would silently degrade the pipeline.
assert 'DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"' in CONST
assert 'DEFAULT_GEMINI_FALLBACK_MODEL = "gemini-3.5-flash"' in CONST
assert 'DEFAULT_GEMINI_LITE_MODEL = "gemini-3.5-flash-lite"' in CONST
assert "latest" not in CONST.split("DEFAULT_GEMINI_MODEL")[1].split("\n")[0]

# The lite chain is wired into exactly the two bounded extraction methods.
assert "def _lite_model_chain" in PROVIDER
assert PROVIDER.count("models_override=self._lite_model_chain()") == 2
analyze_binary = PROVIDER.split("async def async_analyze_binary")[1].split("async def ")[0]
analyze_images = PROVIDER.split("async def async_analyze_images")[1].split("async def ")[0]
assert "models_override=self._lite_model_chain()" in analyze_binary
assert "models_override=self._lite_model_chain()" in analyze_images

# The advisory/compile/research paths keep the primary/fallback pair.
generate_text = PROVIDER.split("async def async_generate_text")[1].split("async def ")[0]
generate_json_result = PROVIDER.split("async def async_generate_json_result")[1].split("async def ")[0]
assert "_lite_model_chain" not in generate_text
assert "_lite_model_chain" not in generate_json_result

# The integration passes the lite default through, and health reports it.
assert "lite_model=DEFAULT_GEMINI_LITE_MODEL" in INIT
assert '"lite_model": self._lite_model' in PROVIDER

print("Gemini lite routing contract tests passed.")
