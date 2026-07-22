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

# Time fields describe the schedule but must never reorder a user-confirmed
# list. In particular, a late ferry remains after untimed parking/pharmacy
# stops when the stored order says so.
stored_order = [
    {"id": "overnight", "type": "wildcamp"},
    {"id": "parking", "type": "parking"},
    {"id": "pharmacy", "type": "service"},
    {"id": "ferry", "type": "ferry", "arrival_time": "19:30"},
]
assert [item["id"] for item in module.canonical_order_stops(stored_order)] == [
    "overnight", "parking", "pharmacy", "ferry"
]

# Incomplete or conflicting legacy positions do not override the stored order.
partial_positions = [
    {"id": "first", "position": 1, "arrival_time": "18:00"},
    {"id": "second"},
    {"id": "third", "position": 1, "arrival_time": "08:00"},
]
assert [item["id"] for item in module.canonical_order_stops(partial_positions)] == [
    "first", "second", "third"
]

stable = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
assert [item["id"] for item in module.canonical_order_stops(stable)] == ["x", "y", "z"]
assert module.canonical_position_map(explicit) == {"a": 1, "b": 2, "c": 3}

mutable = [{"id": "one"}, {"id": "two"}]
module.reindex_explicit_positions(mutable)
assert [item["position"] for item in mutable] == [1, 2]

legacy = [
    {"id": "two", "position": 2},
    {"id": "one", "position": 1},
    {"id": "three", "position": 3},
]
returned = module.normalize_stop_sequence(legacy)
assert returned is legacy
assert [item["id"] for item in legacy] == ["one", "two", "three"]
assert [item["position"] for item in legacy] == [1, 2, 3]

print("Canonical stop ordering tests passed.")
