"""Regression tests for tolerant assistant operation changes normalization."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path("custom_components/roadplanner_mcp/structured_output.py")
spec = spec_from_file_location("roadplanner_structured_output_changes", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)

normalize = module.normalize_changes_mapping

value, mode = normalize({"name": "Dino Zoo", "type": "attraction"})
assert value == {"name": "Dino Zoo", "type": "attraction"}
assert mode == "object"

value, mode = normalize([
    {"name": "Dino Zoo"},
    {"type": "attraction"},
    {"notes": "Besuch am Nachmittag"},
])
assert value == {
    "name": "Dino Zoo",
    "type": "attraction",
    "notes": "Besuch am Nachmittag",
}
assert mode.startswith("list_merged_")

value, mode = normalize([
    {"field": "name", "value": "Apotheke"},
    {"key": "type", "value": "service"},
])
assert value == {"name": "Apotheke", "type": "service"}
assert "field_value_object" in mode

value, mode = normalize([
    {"op": "replace", "path": "/departure_time", "value": "18:15"},
    {"op": "add", "path": "/notes", "value": "Später losfahren"},
])
assert value == {
    "departure_time": "18:15",
    "notes": "Später losfahren",
}

value, mode = normalize(
    '[{"name":"RMK Matsi Beach"},{"type":"overnight"}]'
)
assert value == {"name": "RMK Matsi Beach", "type": "overnight"}
assert mode.startswith("string_list_merged_")

value, mode = normalize({
    "action": "update",
    "entity_type": "stop",
    "day_id": "day-1",
    "changes": [{"name": "Fährterminal"}, {"type": "ferry"}],
})
assert value == {
    "name": "Fährterminal",
    "type": "ferry",
    "day_id": "day-1",
}
assert mode.startswith("wrapped_")

value, mode = normalize(None, allow_scalar_empty=True)
assert value == {}
assert mode == "null_empty"

value, mode = normalize([], allow_scalar_empty=True)
assert value == {}
assert mode == "empty_list"

value, mode = normalize(
    "Keine fachlichen Änderungen; nur verschieben.",
    allow_scalar_empty=True,
)
assert value == {}
assert mode == "discarded_explanatory_string"

try:
    normalize([{"name": "A"}, {"name": "B"}])
except module.StructuredOutputError as err:
    assert "conflicting values" in str(err)
else:
    raise AssertionError("Conflicting change fragments must be rejected")

try:
    normalize([
        {"action": "update", "changes": {"name": "A"}},
        {"action": "update", "changes": {"name": "B"}},
    ])
except module.StructuredOutputError as err:
    assert "multiple operations" in str(err)
else:
    raise AssertionError("Multiple nested operations must not be merged")

assistant_source = Path(
    "custom_components/roadplanner_mcp/assistant.py"
).read_text(encoding="utf-8")
prompt_source = Path(
    "custom_components/roadplanner_mcp/assistant_prompt.py"
).read_text(encoding="utf-8")

assert "normalize_changes_mapping" in assistant_source
assert 'allow_scalar_empty=action in {"remove", "move"}' in assistant_source
assert "changes ist bei jeder Operation immer genau ein JSON-Objekt" in prompt_source
assert "Bei remove und move lautet changes exakt {}" in prompt_source

print("Assistant changes normalization tests passed.")
