"""Pure contract tests for local-first Vision selection."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_media_vision_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

roadplanner = types.ModuleType(f"{PACKAGE_NAME}.roadplanner")
class ValidationError(RuntimeError):
    pass
roadplanner.ValidationError = ValidationError
sys.modules[roadplanner.__name__] = roadplanner

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.media_vision",
    PACKAGE_ROOT / "media_vision.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

candidates = [
    {"id": "local-1", "file_hash": "a", "selection_score": 99, "width": 4000, "height": 3000},
    {"id": "local-2", "file_hash": "b", "selection_score": 90, "width": 3000, "height": 2000},
    {"id": "local-3", "file_hash": "c", "selection_score": 80, "width": 2000, "height": 3000},
]
context = {"stop_id": "stop-1", "stop_name": "Matsi Beach", "place": "Estonia"}
fingerprint_a = module.selection_fingerprint(kind="travel", context=context, candidates=candidates, model="gemini")
fingerprint_b = module.selection_fingerprint(kind="travel", context=context, candidates=candidates, model="gemini")
assert fingerprint_a == fingerprint_b
assert fingerprint_a != module.selection_fingerprint(kind="travel", context=context, candidates=candidates[:2], model="gemini")

value = {
    "cover_image_id": "local-2",
    "highlight_image_ids": ["local-2", "invented", "local-3"],
    "selections": [
        {"image_id": "local-2", "role": "cover", "reason": "repräsentativ"},
        {"image_id": "invented", "role": "highlight", "reason": "must be ignored"},
    ],
    "summary": "semantic selection",
}
selection = module.normalize_vision_selection(
    value,
    allowed_ids=[item["id"] for item in candidates],
    local_order=[item["id"] for item in candidates],
    max_highlights=3,
)
assert selection["cover_id"] == "local-2"
assert selection["highlight_ids"] == ["local-2", "local-3", "local-1"]
assert "invented" not in selection["highlight_ids"]

manual = module.normalize_vision_selection(
    value,
    allowed_ids=[item["id"] for item in candidates],
    local_order=[item["id"] for item in candidates],
    max_highlights=3,
    manual_cover_id="local-1",
)
assert manual["cover_id"] == "local-1"
assert manual["highlight_ids"][0] == "local-1"

system, prompt = module.build_vision_prompt(
    kind="travel",
    context=context,
    candidate_ids=[item["id"] for item in candidates],
    max_highlights=3,
)
assert "lokal" in system.casefold()
assert "identifiziere keine personen" in system.casefold()
assert "local-1" in prompt

print("Media Vision selection tests passed.")
