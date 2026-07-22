"""Regression tests for persisted Roadbook stop positions."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_sequence_normalization_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for name in ("stop_ordering", "canonical_day", "roadplanner"):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

roadplanner = sys.modules[f"{PACKAGE_NAME}.roadplanner"]


def normalize(stops):
    return roadplanner.normalize_day_document(
        {
            "schema_version": 1,
            "day": {
                "id": "day-1",
                "date": "2026-07-22",
                "title": "Tallinn und Fähre",
            },
            "stops": stops,
        },
        fallback_id="day-1",
        fallback_timestamp="2026-07-22T08:00:00Z",
    )["stops"]


# A timed ferry must not jump ahead of untimed stops. Legacy storage order is
# normalized into an explicit, gap-free sequence.
legacy = normalize(
    [
        {"id": "parking", "name": "Parkplatz", "type": "parking"},
        {"id": "pharmacy", "name": "Apotheke", "type": "service"},
        {
            "id": "ferry",
            "name": "Fährterminal",
            "type": "ferry",
            "arrival_time": "19:30",
        },
    ]
)
assert [stop["id"] for stop in legacy] == ["parking", "pharmacy", "ferry"]
assert [stop["position"] for stop in legacy] == [1, 2, 3]

# A complete explicit position set remains authoritative and is serialized in
# canonical list order.
positioned = normalize(
    [
        {"id": "ferry", "name": "Fährterminal", "type": "ferry", "position": 3},
        {"id": "parking", "name": "Parkplatz", "type": "parking", "position": 1},
        {"id": "pharmacy", "name": "Apotheke", "type": "service", "position": 2},
    ]
)
assert [stop["id"] for stop in positioned] == ["parking", "pharmacy", "ferry"]
assert [stop["position"] for stop in positioned] == [1, 2, 3]

print("Roadbook stop sequence normalization tests passed.")
