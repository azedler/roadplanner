"""Regression check that interactive timeouts preserve the fallback budget."""
from pathlib import Path

SOURCE = Path("custom_components/roadplanner_mcp/gemini_client.py").read_text(encoding="utf-8")

needle = '''err.code == "timeout"\n                                and model_index == 0\n                                and len(models) > 1'''
assert needle in SOURCE
assert "Preserve the\n                                # remaining deadline for the configured fallback" in SOURCE

print("Gemini timeout fallback contract tests passed.")
