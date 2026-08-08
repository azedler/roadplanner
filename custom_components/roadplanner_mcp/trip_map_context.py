"""Where the trip happened, in the smallest form a film can draw.

The story manifest answers *what we tell*. It deliberately carries no
coordinates - that decision was made when it was built and it still
holds. This module answers the other question, *where it happened and
how the journey moves*, and it answers it from the canonical routing
data rather than from a second geography of its own.

What it reads
-------------

Roadplanner already stores, per day, the route it calculated: a list of
segments, each with a mode and a GeoJSON LineString. The mode is
recorded by the router, not guessed here:

- ``driving`` - a real road route from the routing provider;
- ``ferry`` - a crossing, stored as a straight line between the two
  terminals because no road exists to route along;
- ``break`` - an intentional gap, where the trip does not connect.

That is why the film can draw a ferry differently without inventing a
heuristic: the distinction is a fact in the roadbook.

Where a day has no calculated route at all, the fallback is a straight
line between the stops that do have coordinates, marked ``direct``. It
is drawn as what it is - an unverified connection - and never as a road.
A day with no usable coordinates simply has no map, and the film shows
the day without one.

What it deliberately does not do
--------------------------------

It writes nothing. It fetches nothing. It contains no clock and no
random source, so the same roadbook produces the same map context byte
for byte - which is what lets a film be re-rendered and be the same
film.

And it minimises, hard. A trip's raw geometry is tens of thousands of
coordinates; a map on a 1280-pixel canvas cannot show that and a render
package has no business carrying it. Douglas-Peucker reduces each
segment to the shape that survives at film scale, and a budget caps the
whole trip. The same reasoning as the photos: what travels to the
renderer is the minimum that still tells the truth.
"""

from __future__ import annotations

from typing import Any

MAP_CONTEXT_VERSION = 1

MODE_DRIVING = "driving"
MODE_FERRY = "ferry"
MODE_BREAK = "break"
# Not a mode the router knows: the film's own marker for "these two
# places are connected in the roadbook, but nobody calculated how".
MODE_DIRECT = "direct"
SEGMENT_MODES = (MODE_DRIVING, MODE_FERRY, MODE_BREAK, MODE_DIRECT)

# Coordinates are rounded to five decimals - about a metre. A film that
# needs more than that is not a film.
COORDINATE_DIGITS = 5
# Per segment and for the whole trip. A 1280-pixel map cannot show more,
# and the package should not carry what cannot be seen.
MAX_POINTS_PER_SEGMENT = 120
MAX_POINTS_PER_CHAPTER = 400
MAX_TRIP_POINTS = 6000
MAX_SEGMENTS_PER_CHAPTER = 24


class MapContextError(ValueError):
    """The map context cannot be built from this trip."""


def _coordinate(value: Any) -> list[float] | None:
    """One [lon, lat] pair, or nothing. Order is GeoJSON's, not a map's."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        lon = float(value[0])
        lat = float(value[1])
    except (TypeError, ValueError):
        return None
    if isinstance(value[0], bool) or isinstance(value[1], bool):
        return None
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        return None
    return [round(lon, COORDINATE_DIGITS), round(lat, COORDINATE_DIGITS)]


def _location(stop: dict[str, Any]) -> list[float] | None:
    location = stop.get("location")
    if not isinstance(location, dict):
        return None
    return _coordinate([location.get("lon"), location.get("lat")])


def _perpendicular_distance(
    point: list[float], start: list[float], end: list[float]
) -> float:
    """Distance from a point to the line through start and end.

    Plain planar maths on degrees. At the scale a film map is drawn -
    a whole country in a few hundred pixels - the error from ignoring
    the Earth's curvature is far below one pixel, and using it here
    keeps the simplification deterministic and dependency-free.
    """
    if start == end:
        return ((point[0] - start[0]) ** 2 + (point[1] - start[1]) ** 2) ** 0.5
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / (
        (dx * dx + dy * dy) ** 0.5
    )


def simplify(points: list[list[float]], *, epsilon: float) -> list[list[float]]:
    """Douglas-Peucker, iteratively so a long route cannot blow the stack.

    Stride-based thinning would have been three lines, and it cuts
    corners: on a winding coast road it deletes exactly the points that
    make the shape recognisable. This keeps the points that carry the
    form and drops the ones that repeat it.
    """
    if len(points) <= 2:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst = 0.0
        index = first
        for position in range(first + 1, last):
            distance = _perpendicular_distance(points[position], points[first], points[last])
            if distance > worst:
                worst = distance
                index = position
        if worst > epsilon:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [point for point, kept in zip(points, keep, strict=True) if kept]


def _thin(points: list[list[float]], *, limit: int) -> list[list[float]]:
    """Reduce a line to at most ``limit`` points, shape first.

    The epsilon is raised until the result fits rather than guessed from
    the data: a route across Finland and one across a car park need very
    different tolerances, and the only thing both need is to fit.
    """
    if len(points) <= limit:
        return points
    epsilon = 0.0005
    reduced = points
    for _ in range(24):
        reduced = simplify(points, epsilon=epsilon)
        if len(reduced) <= limit:
            return reduced
        epsilon *= 1.8
    # Still too many after twenty-four doublings: keep the ends and take
    # an even stride. Deterministic, and it cannot loop forever.
    stride = max(1, len(points) // (limit - 1))
    thinned = points[::stride][: limit - 1]
    thinned.append(points[-1])
    return thinned


def _segment_points(segment: dict[str, Any]) -> list[list[float]]:
    geometry = segment.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return []
    raw = geometry.get("coordinates")
    if not isinstance(raw, list):
        return []
    points = [point for point in (_coordinate(item) for item in raw) if point]
    # Consecutive duplicates say nothing and cost a coordinate each.
    deduped: list[list[float]] = []
    for point in points:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    return deduped


def _fallback_segments(stops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Straight lines between the stops that have coordinates.

    Used only when a day has no calculated route. Marked ``direct`` so
    the film can draw it as the unverified connection it is - the one
    thing that must not happen is a made-up line that looks like a road.
    """
    located = [point for point in (_location(stop) for stop in stops) if point]
    deduped: list[list[float]] = []
    for point in located:
        if not deduped or deduped[-1] != point:
            deduped.append(point)
    if len(deduped) < 2:
        return []
    return [{"mode": MODE_DIRECT, "points": deduped}]


def chapter_map(
    day: dict[str, Any], stops: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The geography of one day, or None when there is none to show."""
    details = day.get("details") if isinstance(day.get("details"), dict) else {}
    routing = details.get("routing") if isinstance(details.get("routing"), dict) else {}
    raw_segments = routing.get("segments")
    segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for segment in raw_segments[:MAX_SEGMENTS_PER_CHAPTER]:
            if not isinstance(segment, dict):
                continue
            mode = str(segment.get("mode") or MODE_DRIVING)
            if mode not in SEGMENT_MODES:
                mode = MODE_DRIVING
            if mode == MODE_BREAK:
                # A deliberate gap. Carried as knowledge, drawn as
                # nothing: the trip does not connect here and a line
                # would say it does.
                continue
            points = _segment_points(segment)
            if len(points) < 2:
                continue
            segments.append({"mode": mode, "points": points})
    if not segments:
        segments = _fallback_segments(stops)
    if not segments:
        return None

    # Share the chapter's point budget across its segments, so one long
    # motorway cannot starve the ferry that follows it.
    per_segment = max(8, min(MAX_POINTS_PER_SEGMENT, MAX_POINTS_PER_CHAPTER // len(segments)))
    trimmed = [
        {"mode": segment["mode"], "points": _thin(segment["points"], limit=per_segment)}
        for segment in segments
    ]
    every = [point for segment in trimmed for point in segment["points"]]
    return {
        "segments": trimmed,
        "start": every[0],
        "end": every[-1],
        "bbox": _bbox(every),
        "point_count": len(every),
        "has_ferry": any(segment["mode"] == MODE_FERRY for segment in trimmed),
        # True when nothing was calculated and the line is a straight
        # guess. The film draws it differently; the report names it.
        "estimated": all(segment["mode"] == MODE_DIRECT for segment in trimmed),
    }


def _bbox(points: list[list[float]]) -> list[float]:
    lons = [point[0] for point in points]
    lats = [point[1] for point in points]
    return [min(lons), min(lats), max(lons), max(lats)]


def build_map_context(chapters: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The whole trip's geography, chapter by chapter, in travel order.

    ``chapters`` is a list of ``{"chapter_id", "index", "map"}`` where
    ``map`` is what :func:`chapter_map` returned - possibly None. The
    order is the film's order, which is what makes progress meaningful:
    chapter N draws everything up to and including N, and nothing after.
    """
    entries = [chapter for chapter in chapters if chapter.get("map")]
    if not entries:
        return None
    total = sum(int(chapter["map"]["point_count"]) for chapter in entries)
    if total > MAX_TRIP_POINTS:
        # Thin every chapter by the same share rather than dropping days:
        # a trip missing its middle is worse than one drawn slightly
        # coarser throughout.
        chapter_budget = max(8, MAX_TRIP_POINTS // len(entries))
        for chapter in entries:
            segments = chapter["map"]["segments"]
            per_segment = max(4, chapter_budget // max(1, len(segments)))
            chapter["map"]["segments"] = [
                {"mode": segment["mode"], "points": _thin(segment["points"], limit=per_segment)}
                for segment in segments
            ]
            chapter["map"]["point_count"] = sum(
                len(segment["points"]) for segment in chapter["map"]["segments"]
            )

    every = [
        point
        for chapter in entries
        for segment in chapter["map"]["segments"]
        for point in segment["points"]
    ]
    return {
        "map_context_version": MAP_CONTEXT_VERSION,
        "bbox": _bbox(every),
        "point_count": len(every),
        "chapter_count": len(entries),
        "has_ferry": any(chapter["map"]["has_ferry"] for chapter in entries),
        "estimated_chapters": sum(1 for chapter in entries if chapter["map"]["estimated"]),
        "start": entries[0]["map"]["start"],
        "end": entries[-1]["map"]["end"],
        "chapters": [
            {
                "chapter_id": chapter["chapter_id"],
                "index": chapter["index"],
                **chapter["map"],
            }
            for chapter in entries
        ],
    }


def validate_map_context(payload: Any) -> dict[str, Any]:
    """Read a map context with the rules that produced it."""
    if not isinstance(payload, dict):
        raise MapContextError("Kartenkontext ist kein Objekt")
    if payload.get("map_context_version") != MAP_CONTEXT_VERSION:
        raise MapContextError("Nicht unterstützte Version des Kartenkontexts")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise MapContextError("Kartenkontext ohne Kapitel")
    total = 0
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise MapContextError("Kartenkapitel ist kein Objekt")
        segments = chapter.get("segments")
        if not isinstance(segments, list) or not segments:
            raise MapContextError("Kartenkapitel ohne Streckenabschnitte")
        for segment in segments:
            if not isinstance(segment, dict) or segment.get("mode") not in SEGMENT_MODES:
                raise MapContextError("Streckenabschnitt mit unbekannter Art")
            points = segment.get("points")
            if not isinstance(points, list) or len(points) < 2:
                raise MapContextError("Streckenabschnitt ohne Punkte")
            for point in points:
                if _coordinate(point) is None:
                    raise MapContextError("Ungültige Koordinate im Kartenkontext")
            total += len(points)
    if total > MAX_TRIP_POINTS:
        raise MapContextError("Kartenkontext enthält zu viele Punkte")
    return payload


__all__ = [
    "MAP_CONTEXT_VERSION",
    "MAX_TRIP_POINTS",
    "MODE_BREAK",
    "MODE_DIRECT",
    "MODE_DRIVING",
    "MODE_FERRY",
    "MapContextError",
    "build_map_context",
    "chapter_map",
    "simplify",
    "validate_map_context",
]
