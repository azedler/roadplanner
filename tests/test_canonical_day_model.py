"""Contract tests for the shared Roadplanner canonical day model."""
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


def stop(stop_id, name, position, stop_type="waypoint", *, lat=None, lon=None, arrival=None, geocoding_status=None):
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
    if geocoding_status:
        value["details"] = {"geocoding": {"status": geocoding_status, "query": name}}
    elif lat is not None and lon is not None:
        value["details"] = {"place_profile": {"confirmed_at": "2026-07-20T08:00:00Z"}}
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
assert model["version"] == 3
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
assert model["location_complete"] is True
assert model["route_complete"] is True
assert model["missing_coordinate_count"] == 0
assert model["data_quality"] == {"sequence": "complete", "locations": "complete", "score": 100}

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

# Missing coordinates remain part of the canonical route order, are omitted
# only from map nodes, and make the route explicitly partial.
partial_days = [
    {
        "id": "day-partial",
        "date": "2026-07-22",
        "stops": [
            stop("parking", "Parkplatz", 1, "parking", lat=59.43, lon=24.74),
            stop("pharmacy", "Apotheke", 2, "service"),
            stop("ferry", "Fährterminal", 3, "ferry", lat=59.45, lon=24.76, arrival="19:30"),
        ],
    }
]
partial_model = module.canonical_day_model(partial_days, 0)
assert [item["id"] for item in partial_model["route_nodes"]] == ["parking", "pharmacy", "ferry"]
assert [item["id"] for item in partial_model["map_nodes"]] == ["parking", "ferry"]
assert partial_model["map_stop_ids"] == ["parking", "ferry"]
assert partial_model["coordinate_count"] == 2
assert partial_model["missing_coordinate_count"] == 1
assert partial_model["location_counts"] == {"resolved": 2, "unverified": 0, "ambiguous": 0, "missing": 1}
assert partial_model["missing_location_nodes"] == [
    {
        "id": "pharmacy",
        "name": "Apotheke",
        "display_sequence": 2,
        "marker_label": "2",
        "inherited": False,
        "status": "missing",
        "place_profile_status": "unreviewed",
        "message": "Kartenpunkt und Ortsprofil fehlen",
        "query": "Apotheke",
    }
]
assert partial_model["location_attention_nodes"] == partial_model["missing_location_nodes"]
assert partial_model["location_complete"] is False
assert partial_model["route_complete"] is False
assert partial_model["data_quality"] == {"sequence": "complete", "locations": "partial", "score": 67}
assert partial_model["stops"][1]["location_status"] == "missing"
assert partial_model["stops"][1]["location_requires_attention"] is True
assert partial_model["stops"][1]["location_message"] == "Kartenpunkt und Ortsprofil fehlen"

# A routable coordinate without a confirmed place profile remains visible as
# an unreviewed place, while the physical route can still use it.
coordinate_only = {
    "id": "coordinate-only",
    "name": "Nur GPS",
    "position": 1,
    "type": "waypoint",
    "location": {"latitude": 59.4, "longitude": 24.7},
}
coordinate_only_model = module.canonical_day_model(
    [{"id": "coordinate-only-day", "stops": [coordinate_only]}],
    0,
)
assert coordinate_only_model["stops"][0]["location_status"] == "resolved"
assert coordinate_only_model["stops"][0]["place_profile_status"] == "unreviewed"
assert coordinate_only_model["stops"][0]["location_requires_attention"] is True
assert coordinate_only_model["stops"][0]["location_message"] == "GPS vorhanden, Ortsprofil noch nicht bestätigt"
assert coordinate_only_model["route_complete"] is True
assert coordinate_only_model["location_complete"] is False

# Coordinates with unresolved provider provenance remain routable but visible as
# unverified location data.
unverified_stop = stop(
    "unverified",
    "Manuell gesetzter Punkt",
    1,
    lat=59.4,
    lon=24.7,
    geocoding_status="coordinates_unverified",
)
unverified_model = module.canonical_day_model([{"id": "unverified-day", "stops": [unverified_stop]}], 0)
assert unverified_model["stops"][0]["location_status"] == "unverified"
assert unverified_model["coordinate_count"] == 1
assert unverified_model["missing_location_nodes"] == []
assert unverified_model["location_attention_nodes"][0]["id"] == "unverified"
assert unverified_model["location_complete"] is False
assert unverified_model["route_complete"] is True
assert unverified_model["data_quality"] == {"sequence": "complete", "locations": "review", "score": 0}

# Legacy labels remain available only when the day has no real Roadbook stops.
legacy_days = [{"id": "legacy", "start": "A", "end": "B", "stops": []}]
legacy_model = module.canonical_day_model(legacy_days, 0)
assert legacy_model["route_nodes"] == []
assert [item["name"] for item in legacy_model["legacy_route_nodes"]] == ["A", "B"]

print("Canonical day model tests passed.")
