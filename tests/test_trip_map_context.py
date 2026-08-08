"""Where a trip happened, and what the film is allowed to draw from it.

The map is the first thing in this project that turns stored geography
into a picture, so the properties worth pinning down are the ones about
truth rather than about looks:

- a ferry is drawn as a ferry because the roadbook says it was one, and
  never because a heuristic guessed;
- a deliberate gap stays a gap - no line is invented over water;
- a straight line between stops is marked as the guess it is;
- the story manifest still carries no coordinates, and the map is built
  beside it rather than inside it.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")

package = types.ModuleType("map_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["map_pkg"] = package


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"map_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


maps = load("trip_map_context")
plan = load("trip_film_plan")
bounded = load("bounded_json")


def _line(points: list[tuple[float, float]]) -> dict:
    return {"type": "LineString", "coordinates": [[lon, lat] for lon, lat in points]}


def _day(segments: list[dict]) -> dict:
    return {"id": "day-1", "details": {"routing": {"segments": segments}}}


def _stop(lon: float, lat: float, stop_id: str = "s1") -> dict:
    return {"id": stop_id, "name": "Ort", "location": {"lon": lon, "lat": lat}}


def verify_a_ferry_is_a_fact_and_not_a_guess() -> None:
    """The router records the mode. Nothing here re-derives it.

    This is the whole reason the film may draw a crossing differently: had
    the mode not been stored, the honest answer would have been to draw no
    distinction at all rather than to guess from the shape of a line.
    """
    day = _day(
        [
            {"mode": "driving", "geometry": _line([(10.0, 54.0), (10.5, 54.4)])},
            {"mode": "ferry", "geometry": _line([(10.5, 54.4), (13.0, 55.4)])},
        ]
    )
    result = maps.chapter_map(day, [])
    assert result is not None
    assert [segment["mode"] for segment in result["segments"]] == ["driving", "ferry"]
    assert result["has_ferry"] is True
    assert result["estimated"] is False

    # A long straight leg with no mode recorded stays "driving". It is not
    # promoted to a ferry because it happens to look like one.
    straight = _day([{"geometry": _line([(10.0, 54.0), (18.0, 59.0)])}])
    assert maps.chapter_map(straight, [])["has_ferry"] is False


def verify_a_deliberate_gap_is_drawn_as_nothing() -> None:
    """`break` means the trip does not connect. A line would say it does."""
    day = _day(
        [
            {"mode": "driving", "geometry": _line([(10.0, 54.0), (10.4, 54.2)])},
            {"mode": "break", "geometry": _line([(10.4, 54.2), (25.0, 60.0)])},
            {"mode": "driving", "geometry": _line([(25.0, 60.0), (25.4, 60.2)])},
        ]
    )
    result = maps.chapter_map(day, [])
    assert len(result["segments"]) == 2
    flat = [point for segment in result["segments"] for point in segment["points"]]
    assert [25.0, 60.0] in flat and [10.4, 54.2] in flat
    # No segment spans the gap.
    for segment in result["segments"]:
        lons = [point[0] for point in segment["points"]]
        assert max(lons) - min(lons) < 1.0


def verify_an_uncalculated_day_says_so() -> None:
    """Without a route, the stops are joined - and marked as a guess."""
    day = {"id": "day-1", "details": {}}
    result = maps.chapter_map(day, [_stop(10.0, 54.0, "a"), _stop(11.0, 55.0, "b")])
    assert result["estimated"] is True
    assert [segment["mode"] for segment in result["segments"]] == [maps.MODE_DIRECT]

    # One coordinate is not a line, and nothing is invented to make it one.
    assert maps.chapter_map(day, [_stop(10.0, 54.0)]) is None
    # No coordinate at all: the day simply has no map.
    assert maps.chapter_map(day, [{"id": "x", "name": "Ort"}]) is None


def verify_the_shape_survives_the_budget() -> None:
    """Thinning keeps the ends and the corners, not every third point."""
    # A square-ish detour: the corner is the only point that carries the
    # shape, and a stride-based reduction would be free to drop it.
    points = (
        [(10.0, 54.0 + i * 0.001) for i in range(400)]
        + [(10.0 + i * 0.001, 54.4) for i in range(400)]
    )
    day = _day([{"mode": "driving", "geometry": _line(points)}])
    result = maps.chapter_map(day, [])
    kept = result["segments"][0]["points"]
    assert len(kept) <= maps.MAX_POINTS_PER_SEGMENT
    assert kept[0] == [10.0, 54.0]
    assert kept[-1] == [10.399, 54.4]
    # The corner is still there, within rounding.
    assert any(abs(point[0] - 10.0) < 0.01 and abs(point[1] - 54.4) < 0.01 for point in kept)


def verify_the_whole_trip_stays_within_its_budget() -> None:
    long_day = _day(
        [{"mode": "driving", "geometry": _line([(10.0 + i * 0.01, 54.0 + i * 0.01) for i in range(500)])}]
    )
    chapters = [
        {"chapter_id": f"day-{index}", "index": index, "map": maps.chapter_map(long_day, [])}
        for index in range(45)
    ]
    context = maps.build_map_context(chapters)
    assert context["point_count"] <= maps.MAX_TRIP_POINTS
    # Thinned everywhere rather than truncated: a trip missing its middle
    # would be a worse lie than a slightly coarser one.
    assert context["chapter_count"] == 45
    maps.validate_map_context(context)


def verify_the_validator_agrees_with_the_builder() -> None:
    day = _day([{"mode": "driving", "geometry": _line([(10.0, 54.0), (10.5, 54.4)])}])
    context = maps.build_map_context(
        [{"chapter_id": "day-1", "index": 0, "map": maps.chapter_map(day, [])}]
    )
    assert maps.validate_map_context(context) is context

    for broken in (
        None,
        {},
        {"map_context_version": 99, "chapters": []},
        {"map_context_version": 1, "chapters": []},
        {
            "map_context_version": 1,
            "chapters": [{"segments": [{"mode": "flying", "points": [[1, 1], [2, 2]]}]}],
        },
        {
            "map_context_version": 1,
            "chapters": [{"segments": [{"mode": "driving", "points": [[500, 1], [2, 2]]}]}],
        },
    ):
        try:
            maps.validate_map_context(broken)
        except maps.MapContextError:
            continue
        raise AssertionError(f"accepted a broken context: {broken!r}")


def verify_the_same_trip_produces_the_same_bytes() -> None:
    """A re-render has to be the same film, so the map has no clock in it."""
    day = _day([{"mode": "driving", "geometry": _line([(10.0, 54.0), (10.5, 54.4), (11.0, 55.0)])}])
    first = maps.build_map_context(
        [{"chapter_id": "day-1", "index": 0, "map": maps.chapter_map(day, [])}]
    )
    second = maps.build_map_context(
        [{"chapter_id": "day-1", "index": 0, "map": maps.chapter_map(day, [])}]
    )
    assert first == second


def verify_the_map_is_optional_for_the_plan() -> None:
    """Geography is a fact and a style is a wish.

    With map data, a `map_focus` day gets its map scene. Without it, the
    same day still has to become a watchable chapter - so it falls back
    instead of rendering an empty frame.
    """
    chapters = [
        {
            "chapter_id": "day-1",
            "index": 0,
            "title": "Tag",
            "story": "Eine Zeile.",
            "importance": "normal",
            "story_role": "journey",
            "visual_style": "map_focus",
            "images": [{"path": "photos/c00-1.jpg"}, {"path": "photos/c00-2.jpg"}],
        }
    ]
    without = plan.build_scene_plan(trip={}, chapters=chapters)
    assert not [scene for scene in without["scenes"] if scene["type"].startswith("map")]
    assert "hero" in {scene["type"] for scene in without["scenes"]}

    context = {
        "map_context_version": 1,
        "chapters": [{"chapter_id": "day-1", "index": 0}],
    }
    with_map = plan.build_scene_plan(trip={}, chapters=chapters, map_context=context)
    types_ = [scene["type"] for scene in with_map["scenes"]]
    assert types_[0] == "intro" and types_[1] == "map_start"
    # The whole route is shown once, at the end and only there - before
    # the closing card, so the film ends on its own words.
    assert "map_leg" in types_
    assert types_.index("map_full") == types_.index("outro") - 1


def verify_the_map_takes_time_rather_than_adding_it() -> None:
    """The brief is explicit: the film must not simply grow.

    Most of a map's length is taken back out of the same day. Not all of
    it - a day keeps recognisable pictures - so a mapped day is a little
    longer, and that residue is the price of having a map at all.

    The first version of this failed the brief badly and the check did
    not notice: the reclaim only reached plain photo scenes, so a day
    whose style was a hero or a collage paid nothing back. Measured on
    the 25-day test film, the whole thing grew by 36 %. Every style is
    therefore checked here, not just the one that happened to work.
    """
    context = {
        "map_context_version": 1,
        "chapters": [{"chapter_id": "day-1", "index": 0}],
    }
    for style in ("normal", "hero", "collage", "compact", "map_focus"):
        for importance in ("transition", "normal", "highlight", "major_highlight"):
            for photos in (0, 1, 4):
                chapter = {
                    "chapter_id": "day-1",
                    "index": 0,
                    "title": "Tag",
                    "story": "Eine Zeile.",
                    "importance": importance,
                    "story_role": "journey",
                    "visual_style": style,
                    "images": [
                        {"path": f"photos/c00-{position}.jpg"}
                        for position in range(1, photos + 1)
                    ],
                }
                without = plan.build_scene_plan(trip={}, chapters=[chapter])
                with_map = plan.build_scene_plan(
                    trip={}, chapters=[chapter], map_context=context
                )
                day_without = sum(
                    scene["frames"]
                    for scene in without["scenes"]
                    if scene["chapter_id"] == "day-1"
                )
                day_with = sum(
                    scene["frames"]
                    for scene in with_map["scenes"]
                    if scene["chapter_id"] == "day-1"
                )
                # map_focus is the one style that says the map IS the day,
                # so it is allowed to be more than the introduction.
                limit = 1.4 if style == "map_focus" else 1.25
                assert day_with <= day_without * limit, (
                    style,
                    importance,
                    photos,
                    day_without,
                    day_with,
                )
                if photos:
                    # A picture always survives: an atlas is not a travel film.
                    assert any(
                        scene["type"] in {"photo", "hero", "collage"}
                        for scene in with_map["scenes"]
                    )


def verify_the_map_reads_the_projection_that_still_has_coordinates() -> None:
    """The bug a real trip found, in the shape that produced it.

    A day carries its route twice, and the two are not interchangeable.
    Both pass through the same sanitiser, but the copy inside ``details``
    starts two levels deeper - and the depth limit lands exactly on the
    coordinate pairs, so every ``[lon, lat]`` in there is the string
    "<gekürzt: maximale Verschachtelung>".

    Reading ``details`` made a 23-day trip with routes on every day
    report "Karte: keine". The earlier test did not catch it because it
    handed ``chapter_map`` a day of its own making, in exactly the shape
    the code expected. This one runs the real sanitiser first.
    """
    line = [[10.0 + step * 0.01, 54.0 + step * 0.01] for step in range(60)]
    segments = [{"mode": "driving", "geometry": {"type": "LineString", "coordinates": line}}]

    # What `_compact_day` actually produces: the summary beside the day,
    # and the same route inside `details`, both sanitised.
    day = {
        "id": "day-1",
        "routing": {"segments": bounded._bounded_json_value(segments, max_items=200, max_string=500)},
        "details": bounded._bounded_json_value(
            {"routing": {"segments": segments}}, max_items=100, max_string=4_000
        ),
    }
    # The trap itself, asserted so nobody has to rediscover it.
    buried = day["details"]["routing"]["segments"][0]["geometry"]["coordinates"]
    assert all(isinstance(point, str) for point in buried), (
        "wenn das je wieder Koordinaten sind, darf diese Regel neu bewertet werden"
    )

    result = maps.chapter_map(day, [])
    assert result is not None, "die Karte muss die brauchbare Kopie finden"
    assert result["point_count"] >= 2
    assert result["estimated"] is False


def verify_a_stop_coordinate_is_read_under_its_real_name() -> None:
    """Roadplanner stores latitude/longitude. Only lat/lon was read.

    Every stop of a real trip had a coordinate and the map reported
    having none - the second half of the same failure.
    """
    day = {"id": "day-1", "details": {}}
    stops = [
        {"id": "a", "name": "Ort", "location": {"latitude": 54.0, "longitude": 10.0}},
        {"id": "b", "name": "Ort", "location": {"latitude": 55.0, "longitude": 11.0}},
    ]
    result = maps.chapter_map(day, stops)
    assert result is not None, "latitude/longitude muss gelesen werden"
    assert result["estimated"] is True
    assert result["segments"][0]["points"] == [[10.0, 54.0], [11.0, 55.0]]

    # The short names keep working, and so does lng.
    short = maps.chapter_map(
        day,
        [
            {"id": "a", "location": {"lat": 54.0, "lon": 10.0}},
            {"id": "b", "location": {"lat": 55.0, "lng": 11.0}},
        ],
    )
    assert short is not None


def verify_a_route_without_segments_is_still_a_route() -> None:
    """Routes calculated before segments existed have one line for the day.

    Skipping them would have left older trips mapless for a reason that
    has nothing to do with where they went.
    """
    line = [[10.0, 54.0], [10.4, 54.3], [10.9, 54.9]]
    day = {
        "id": "day-1",
        "routing": {"geometry": {"type": "LineString", "coordinates": line}},
    }
    result = maps.chapter_map(day, [])
    assert result is not None
    assert [segment["mode"] for segment in result["segments"]] == [maps.MODE_DRIVING]
    # It is a real road route, so it is not marked as a guess - it simply
    # cannot say where a ferry was, and nothing here pretends otherwise.
    assert result["estimated"] is False
    assert result["has_ferry"] is False


verify_a_ferry_is_a_fact_and_not_a_guess()
verify_the_map_reads_the_projection_that_still_has_coordinates()
verify_a_stop_coordinate_is_read_under_its_real_name()
verify_a_route_without_segments_is_still_a_route()
verify_a_deliberate_gap_is_drawn_as_nothing()
verify_an_uncalculated_day_says_so()
verify_the_shape_survives_the_budget()
verify_the_whole_trip_stays_within_its_budget()
verify_the_validator_agrees_with_the_builder()
verify_the_same_trip_produces_the_same_bytes()
verify_the_map_is_optional_for_the_plan()
verify_the_map_takes_time_rather_than_adding_it()
print("Trip map context tests passed.")
