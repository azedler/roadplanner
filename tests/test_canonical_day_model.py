"""Contract tests for the shared Roadplanner 3.0 canonical day model."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_canonical_day_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for name in ("stop_ordering", "canonical_day"):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

module = sys.modules[f"{PACKAGE_NAME}.canonical_day"]


def stop(stop_id, name, position, stop_type="waypoint", *, lat=None, lon=None, arrival=None):
    value = {
        "id": stop_id,
        "name": name,
        "position": position,
        "type": stop_type,
    }
    if lat is not None and lon is not None:
        value["location"] = {"latitude": lat, "longitude": lon}
    if arrival:
        value["arrival_time"] = arrival
    return value


days = [
    {
        "id": "day-1",
        "sequence": 1,
        "title": "Vortag",
        "date": "2026-07-20",
        "stops": [
            stop("visit", "Besichtigung", 1, lat=56.0, lon=23.0),
            stop("camp", "Camping am See", 2, "campsite", lat=56.2, lon=23.2),
        ],
    },
    {
        "id": "day-2",
        "sequence": 2,
        "title": "Küste",
        "date": "2026-07-21",
        "start": "Veralteter Start",
        "end": "Riga",
        "stops": [
            stop("third", "Restaurant", 3, "restaurant", lat=57.3, lon=24.3),
            stop("first", "Berg der Kreuze", 1, "sightseeing", lat=56.0, lon=23.4),
            stop("second", "Weiße Düne", 2, "sightseeing", lat=57.2, lon=24.4),
        ],
    },
]

model = module.canonical_day_model(days, 1)
assert [item["id"] for item in model["stops"]] == ["first", "second", "third"]
assert [item["display_sequence"] for item in model["stops"]] == [1, 2, 3]
assert [item["id"] for item in model["route_nodes"]] == ["camp", "first", "second", "third"]
assert model["route_nodes"][0]["marker_label"] == "S"
assert model["route_nodes"][0]["_inherited"] is True
assert model["start_label"] == "Camping am See"
assert model["end_label"] == "Restaurant"
assert "Riga" not in [item["name"] for item in model["route_nodes"]]
assert model["legacy_route_nodes"] == []
assert any(item["code"] == "legacy_end_context" for item in model["warnings"])
assert model["map_stop_ids"] == ["camp", "first", "second", "third"]

payload = {"days": days}
module.decorate_canonical_days(payload)
assert payload["days"][1]["stop_count"] == 3
assert [item["id"] for item in payload["days"][1]["stops"]] == ["first", "second", "third"]
assert module.canonical_day_stops(payload["days"][1])[0]["id"] == "camp"
assert module.canonical_roadbook_stops(payload["days"][1])[0]["id"] == "first"

# A duplicated physical overnight at the start of the next day must not create
# another inherited route node.
duplicate_days = [
    days[0],
    {
        "id": "day-duplicate",
        "date": "2026-07-21",
        "stops": [
            stop("camp", "Camping am See", 1, "campsite", lat=56.2, lon=23.2),
            stop("next", "Nächstes Ziel", 2, lat=57.0, lon=24.0),
        ],
    },
]
duplicate_model = module.canonical_day_model(duplicate_days, 1)
assert duplicate_model["inherited_start"] is False
assert [item["id"] for item in duplicate_model["route_nodes"]] == ["camp", "next"]

# Legacy labels remain available only when the day has no real Roadbook stops.
legacy_days = [{"id": "legacy", "start": "A", "end": "B", "stops": []}]
legacy_model = module.canonical_day_model(legacy_days, 0)
assert legacy_model["route_nodes"] == []
assert [item["name"] for item in legacy_model["legacy_route_nodes"]] == ["A", "B"]

print("Canonical day model tests passed.")
