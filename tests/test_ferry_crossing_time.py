"""A ferry crossing has a duration, not just a distance.

Live report: "Die Fahrzeit scheint nicht die Fährzeit zu berücksichtigen?"
It did not - and not because a number got lost in a sum: the ferry segment
returned a literal ``None`` for its duration, so a six-hour crossing was
never a number anywhere in the system.

The one honest source is the schedule the user already entered on the two
terminal stops. Nothing is estimated from the straight-line distance: a
crossing time invented that way would read as a fact.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_NAME = "roadplanner_ferry_test_pkg"
PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *a, **k: None
sys.modules.setdefault("aiohttp", aiohttp_stub)

for name, attrs in (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.aiohttp_client", {"async_get_clientsession": lambda *a, **k: None}),
    ("homeassistant.util", {}),
):
    stub = types.ModuleType(name)
    stub.__path__ = []
    for key, value in attrs.items():
        setattr(stub, key, value)
    sys.modules.setdefault(name, stub)

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


helpers = load("routing_helpers")
routing = load("routing")


def verify_the_crossing_time_comes_from_the_terminal_times() -> None:
    # Trelleborg 07:30 -> Rostock 13:30 is the live case: six hours at sea.
    minutes = helpers.ferry_crossing_minutes(
        {"departure_time": "07:30"}, {"arrival_time": "13:30"}
    )
    assert minutes == 360, minutes


def verify_an_overnight_crossing_is_not_negative() -> None:
    """A night ferry arrives "before" it left on a wall clock."""
    assert helpers.ferry_crossing_minutes(
        {"departure_time": "22:00"}, {"arrival_time": "06:15"}
    ) == 495


def verify_missing_or_absurd_times_yield_nothing_rather_than_a_guess() -> None:
    for source, target in (
        ({}, {"arrival_time": "13:30"}),
        ({"departure_time": "07:30"}, {}),
        ({"departure_time": ""}, {"arrival_time": "13:30"}),
        ({"departure_time": "7:30"}, {"arrival_time": "13:30"}),
        ({"departure_time": "25:00"}, {"arrival_time": "13:30"}),
        ({"departure_time": "07:30"}, {"arrival_time": "07:30"}),
        (None, None),
    ):
        assert helpers.ferry_crossing_minutes(source, target) is None, (source, target)


def verify_the_segment_reports_the_duration_instead_of_none() -> None:
    points = [
        {
            "latitude": 55.375,
            "longitude": 13.15,
            "stop_id": "s1",
            "day_id": "d1",
            "ferry_minutes": 360,
        },
        {"latitude": 54.15, "longitude": 12.10, "stop_id": "s2", "day_id": "d1"},
    ]
    segment = routing.ferry_route_segment(points)
    assert segment["mode"] == "ferry"
    assert segment["duration_s"] == 360 * 60, segment["duration_s"]
    assert segment["distance_m"] > 0, "the distance still comes from the geometry"

    # Without a known schedule it stays None - never estimated from the
    # distance, which would look like a measured fact.
    without = routing.ferry_route_segment(
        [dict(points[0], ferry_minutes=None), points[1]]
    )
    assert without["duration_s"] is None
    assert without["distance_m"] > 0


def verify_the_schedule_is_part_of_the_route_identity() -> None:
    """Change the ferry times and the stored route must count as stale."""
    base = [
        {"latitude": 55.375, "longitude": 13.15, "stop_id": "s1", "mode_to_next": "ferry"},
        {"latitude": 54.15, "longitude": 12.10, "stop_id": "s2"},
    ]
    same = routing.route_input_hash(base, "car")
    changed = routing.route_input_hash(
        [dict(base[0], ferry_minutes=360), base[1]], "car"
    )
    assert same != changed, "a crossing time must change the route hash"

    source = (PACKAGE_ROOT / "routing_helpers.py").read_text(encoding="utf-8")
    signature = source.split("def _route_stop_signature", 1)[1].split("def ", 1)[0]
    assert "departure_time" in signature and "arrival_time" in signature, (
        "editing a terminal's times must invalidate the stored route"
    )


def verify_the_panel_shows_the_crossing_separately() -> None:
    """Road time stays road time - the crossing gets its own row.

    ``distance_km``/``drive_minutes`` are deliberately road-only, mirrored
    by ``total_movement_km`` for the combined figure. Silently inflating
    the drive time would break that split.
    """
    day_view = (
        PACKAGE_ROOT / "frontend/features/trip-day-stop.js"
    ).read_text(encoding="utf-8")
    assert "ferry_duration_s" in day_view
    assert "Fährzeit" in day_view
    assert "Unterwegs gesamt" in day_view
    # And when the crossing time is unknown, the page says what to do.
    assert "Abfahrtszeit" in day_view and "Ankunftszeit" in day_view

    trip_view = (PACKAGE_ROOT / "frontend/features/route-map.js").read_text(encoding="utf-8")
    assert "total_ferry_minutes" in trip_view


verify_the_crossing_time_comes_from_the_terminal_times()
verify_an_overnight_crossing_is_not_negative()
verify_missing_or_absurd_times_yield_nothing_rather_than_a_guess()
verify_the_segment_reports_the_duration_instead_of_none()
verify_the_schedule_is_part_of_the_route_identity()
verify_the_panel_shows_the_crossing_separately()
print("Ferry crossing time tests passed.")
