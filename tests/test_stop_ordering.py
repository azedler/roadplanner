"""Regression tests for the canonical Roadplanner stop order."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path("custom_components/roadplanner_mcp/stop_ordering.py")
spec = spec_from_file_location("roadplanner_stop_ordering", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)

explicit = [
    {"id": "c", "position": 3},
    {"id": "a", "position": 1},
    {"id": "b", "position": 2},
]
assert [item["id"] for item in module.canonical_order_stops(explicit)] == ["a", "b", "c"]

chronological = [
    {"id": "overnight", "type": "wildcamp"},
    {"id": "late", "type": "attraction", "arrival_time": "16:30"},
    {"id": "start", "type": "start"},
    {"id": "early", "type": "sightseeing", "arrival_time": "09:45"},
    {"id": "untimed", "type": "shopping"},
]
assert [item["id"] for item in module.canonical_order_stops(chronological)] == [
    "start", "early", "late", "untimed", "overnight"
]

stable = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
assert [item["id"] for item in module.canonical_order_stops(stable)] == ["x", "y", "z"]
assert module.canonical_position_map(explicit) == {"a": 1, "b": 2, "c": 3}

mutable = [{"id": "one"}, {"id": "two"}]
module.reindex_explicit_positions(mutable)
assert [item["position"] for item in mutable] == [1, 2]

print("Canonical stop ordering tests passed.")
