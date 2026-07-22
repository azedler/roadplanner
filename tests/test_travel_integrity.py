"""Contract tests for the Roadplanner trip-integrity report."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_travel_integrity_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for name in ("stop_ordering", "canonical_day", "travel_integrity"):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

module = sys.modules[f"{PACKAGE_NAME}.travel_integrity"]

parking = {
    "id": "parking",
    "name": "Parkplatz",
    "position": 1,
    "type": "parking",
    "location": {"latitude": 59.4, "longitude": 24.7},
}
pharmacy = {
    "id": "pharmacy",
    "name": "Apotheke",
    "position": 2,
    "type": "service",
    "location": {},
}
ferry = {
    "id": "ferry",
    "name": "Fährterminal",
    "position": 3,
    "type": "ferry",
    "arrival_time": "19:30",
    "location": {"latitude": 59.45, "longitude": 24.76},
}
camp = {
    "id": "camp",
    "name": "Campingplatz",
    "position": 1,
    "type": "campsite",
    "location": {"latitude": 60.0, "longitude": 25.0},
    "details": {"geocoding": {"status": "coordinates_unverified"}},
}

days = [
    {
        "id": "day-1",
        "date": "2026-07-22",
        "title": "Tallinn und Fähre",
        "stops": [parking, pharmacy, ferry],
        "canonical": {
            "stops": [parking, pharmacy, ferry],
            "route_nodes": [parking, pharmacy, ferry],
            "coordinate_count": 2,
            "missing_coordinate_count": 1,
        },
        "routing": {"status": "partial"},
    },
    {
        "id": "day-2",
        "date": "2026-07-23",
        "title": "Helsinki",
        "stops": [camp],
        "canonical": {
            "stops": [camp],
            "route_nodes": [camp],
            "coordinate_count": 1,
            "missing_coordinate_count": 0,
        },
        "routing": {"status": "not_required"},
    },
]

report = module.build_travel_integrity(
    days,
    destination_galleries={
        "parking": {"images": [{"id": "img-1"}]},
        "ferry": {"images": [{"id": "img-2"}]},
    },
    media_by_stop={"camp": ["media-1", "media-2"]},
    route_metrics={"status": "partial"},
)

assert report["version"] == 1
assert report["status"] in {"attention", "incomplete"}
assert report["stats"]["day_count"] == 2
assert report["stats"]["stop_count"] == 4
assert report["stats"]["missing_location_count"] == 1
assert report["stats"]["unverified_location_count"] == 1
assert report["stats"]["repairable_location_count"] == 2
assert report["stats"]["route_issue_count"] >= 1
assert report["stats"]["visualized_stop_count"] == 3
assert report["stats"]["own_photo_stop_count"] == 1
assert report["stats"]["visual_missing_count"] == 1
assert report["stats"]["schedule_hint_count"] == 3
assert report["dimensions"]["sequence"] == 100
assert report["dimensions"]["locations"] < 100
assert report["dimensions"]["visuals"] == 75
assert any(item["code"] == "location_missing" and item["stop_id"] == "pharmacy" for item in report["issues"])
assert any(item["code"] == "location_unverified" and item["stop_id"] == "camp" for item in report["issues"])
assert any(item["code"] == "visual_missing" and item["stop_id"] == "pharmacy" for item in report["issues"])
# Missing schedule times remain informational and do not create integrity issues.
assert not any(item["category"] == "schedule" for item in report["issues"])

empty_report = module.build_travel_integrity([])
assert empty_report["status"] == "incomplete"
assert empty_report["score"] == 0
assert empty_report["blocking_issue_count"] == 1
assert empty_report["issues"][0]["code"] == "trip_without_days"

no_stop_report = module.build_travel_integrity(
    [{"id": "empty-day", "date": "2026-07-24", "title": "Noch ungeplant", "stops": []}]
)
assert no_stop_report["status"] == "incomplete"
assert no_stop_report["score"] == 0
assert no_stop_report["dimensions"] == {"sequence": 0, "locations": 0, "routes": 0, "visuals": 0}
assert any(item["code"] == "trip_without_stops" for item in no_stop_report["issues"])

print("Travel integrity report tests passed.")
