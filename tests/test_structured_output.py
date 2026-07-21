"""Regression tests for tolerant Gemini structured-output normalization."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path("custom_components/roadplanner_mcp/structured_output.py")
spec = spec_from_file_location("roadplanner_structured_output", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)

schema = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "operations": {"type": "array", "items": {"type": "object"}},
    },
}

value, mode = module.parse_structured_object('{"summary":"ok","operations":[]}', schema)
assert value["summary"] == "ok"
assert mode == "object"

value, mode = module.parse_structured_object(
    '```json\n{"summary":"fenced","operations":[]}\n```',
    schema,
)
assert value["summary"] == "fenced"
assert mode == "object"

value, mode = module.parse_structured_object(
    'Hier ist das Ergebnis:\n{"summary":"wrapped","operations":[]}\nDanke.',
    schema,
)
assert value["summary"] == "wrapped"

value, mode = module.parse_structured_object(
    '[{"action":"add"},{"action":"remove"}]',
    schema,
)
assert len(value["operations"]) == 2
assert mode == "list_wrapped_operations"

value, mode = module.parse_structured_object(
    '"{\\"summary\\":\\"nested\\",\\"operations\\":[]}"',
    schema,
)
assert value["summary"] == "nested"
assert mode.startswith("string_")

single_schema = {"type": "object", "properties": {"title": {"type": "string"}}}
value, mode = module.parse_structured_object('[{"title":"single"}]', single_schema)
assert value["title"] == "single"
assert mode == "single_object_list"

try:
    module.parse_structured_object('[1,2,3]', single_schema)
except module.StructuredOutputError:
    pass
else:
    raise AssertionError("An unrelated scalar list must not be coerced into an object")

print("Structured output normalization tests passed.")
