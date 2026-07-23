"""Regression tests for existing day IDs returned in ``day_ref``."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

SOURCE_PATH = Path("custom_components/roadplanner_mcp/assistant.py")
source = SOURCE_PATH.read_text(encoding="utf-8")
tree = ast.parse(source)
function_node = next(
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "_normalize_compiled_day_reference"
)
compiled = compile(
    ast.fix_missing_locations(ast.Module(body=[function_node], type_ignores=[])),
    str(SOURCE_PATH),
    "exec",
)


class ValidationError(RuntimeError):
    pass


def _clean_text(value: Any, maximum: int = 2_000) -> str:
    return " ".join(str(value or "").strip().split())[:maximum]


namespace = {
    "Any": Any,
    "ValidationError": ValidationError,
    "_clean_text": _clean_text,
}
exec(compiled, namespace)
normalize = namespace["_normalize_compiled_day_reference"]

existing_id = "day-e6c19b335d42"
operation = {"day_ref": existing_id}
day_id, day_ref = normalize(
    operation,
    day_ids={existing_id},
    new_day_refs=set(),
)
assert day_id == existing_id
assert day_ref == ""
assert operation == {"day_id": existing_id}

new_id = "new-day-nordkap"
operation = {"day_id": new_id}
day_id, day_ref = normalize(
    operation,
    day_ids={existing_id},
    new_day_refs={new_id},
)
assert day_id == ""
assert day_ref == new_id
assert operation == {"day_ref": new_id}

operation = {"day_id": existing_id, "day_ref": existing_id}
day_id, day_ref = normalize(
    operation,
    day_ids={existing_id},
    new_day_refs=set(),
)
assert (day_id, day_ref) == (existing_id, "")
assert operation == {"day_id": existing_id}

unknown = {"day_ref": "day-unknown"}
assert normalize(unknown, day_ids={existing_id}, new_day_refs=set()) == (
    "",
    "day-unknown",
)
assert unknown == {"day_ref": "day-unknown"}

try:
    normalize(
        {"day_id": existing_id, "day_ref": new_id},
        day_ids={existing_id},
        new_day_refs={new_id},
    )
except ValidationError as err:
    assert "Widersprüchliche Tagesreferenzen" in str(err)
else:
    raise AssertionError("Conflicting day_id/day_ref values must be rejected")

assert source.count("_normalize_compiled_day_reference(") >= 3
assert "day_ref verweist nicht auf einen neuen Tag" in source
print("Assistant day-reference normalization tests passed.")
