"""How many pictures a day gets, and which ones.

The old rule was one number per importance: a transition day got three
pictures, a normal day six, a highlight ten. That is a decision about the
ITINERARY, made before anybody looked at what was actually photographed,
and it fails in both directions at once. A major highlight with seven
mediocre pictures was padded to fourteen; a normal day with twelve strong
ones was cut to six. On a real 23-day trip it left 109 of 260 places in
the film unused while 122 curated pictures waited for one.

So the question is turned around:

    Importance sets the CEILING.
    Quality sets the DEMAND.
    Redundancy caps a series.
    One global maximum stops the film growing without end.

A day asks for as many pictures as it has good, non-redundant ones, and
its importance only says how many it may have at most. An important stop
photographed badly is not inflated; an ordinary day photographed well is
not cut.

Nothing here calls anything. Every number it reads - the per-picture
score, the series grouping, the motifs - was already paid for when the
day was curated, and is read from the stored record. This module is pure
arithmetic over that, so the same code answers "what would the film
carry?" in a simulation and "what does it carry?" in a render.

What this module deliberately does NOT do
-----------------------------------------

It does not touch story importance. A day with thirty-nine photographs
is not thereby an important day - narrative weight and visual richness
are different axes, and deriving one from the other would let a camera
roll rewrite the story. A transition day with strong material simply
gets more of its six than it used to get of its three.
"""

from __future__ import annotations

from typing import Any

from .media_curation import motif_matches, semantic_score

# What a day may show at most, by what the story says it was.
#
# CAPS, not targets - the name matters, because the previous name
# ("weights") described the thing it was used as: a fixed allocation. A
# day reaches its cap only by having that many pictures worth showing.
PHOTO_CAPS_BY_IMPORTANCE = {
    "transition": 6,
    "normal": 10,
    "highlight": 14,
    "major_highlight": 18,
}
DEFAULT_IMPORTANCE = "normal"

# The bar a picture clears to be worth a place on its own.
#
# `semantic_score` runs 0 to 20: story value (0-5) counts double, image
# quality (0-5) once, emotion and uniqueness (0-5 each) at half.
#
# Read off a real 23-day trip, 419 analysed pictures:
#
#     min 6.0 · p25 12.0 · median 13.0 · p75 15.5 · p90 16.0 · max 20.0
#
# Two things follow, and the second is the more important one.
#
# The scores are COMPRESSED: nothing scores below 6, and more than a
# third of everything lands in 12-14. So a bar has far less leverage
# here than the arithmetic suggests - between 8.0 and 11.0 the whole
# trip moves by nine pictures (220 to 211), because on 15 of 23 days it
# is the day's own ceiling that binds, not the quality of its material.
# A bar below the first quartile is not a filter, it is decoration.
#
# 12.0 is the first value that removes anything: the weakest quarter,
# which is exactly what "not good enough to hold the screen alone"
# should mean. Above it the bar starts cutting into the middle of the
# distribution - at 14.0 the film falls to 140 pictures, BELOW what it
# carries today, and a day appears that only keeps its own subject
# through a coverage exception. That is the signal for "too high".
GOOD_IMAGE_THRESHOLD = 12.0

# How many pictures one burst may contribute.
#
# Six attempts at a moose are one moment. Which two of the six is a
# question the analysis already answered, so they are taken by score.
MAX_PER_SERIES = 2

# The film's own ceiling. Not a target: if only 194 pictures earn a
# place, the film has 194.
MAX_FILM_PHOTOS = 260

REASON_PINNED = "pinned"
REASON_EARNED = "earned"
REASON_COVERAGE = "coverage_exception"


def _importance(value: Any) -> str:
    text = str(value or "").strip()
    return text if text in PHOTO_CAPS_BY_IMPORTANCE else DEFAULT_IMPORTANCE


def cap_for(importance: Any) -> int:
    return PHOTO_CAPS_BY_IMPORTANCE[_importance(importance)]


def score_of(analyses: dict[str, Any], media_id: str) -> float:
    """The stored score for one picture, via the production scorer."""
    analysis = (analyses or {}).get(media_id)
    return semantic_score(analysis) if isinstance(analysis, dict) else 0.0


def _covers(analysis: Any, motif: str, alternatives: list[str] | None) -> bool:
    """Whether this picture answers this requirement.

    Uses the curation's own matcher against the curation's own fields. A
    second implementation beside the real one describes a system nobody
    is running - this project has shipped that bug before.
    """
    if not isinstance(analysis, dict):
        return False
    seen: list[str] = []
    for key in ("motifs", "shows"):
        value = analysis.get(key)
        if isinstance(value, list):
            seen.extend(str(entry) for entry in value)
        elif isinstance(value, str):
            seen.append(value)
    for token in list(alternatives or []) or [motif]:
        if motif_matches(token, seen):
            return True
    return False


def earned_for_day(
    *,
    curated: list[str],
    analyses: dict[str, Any],
    series_by_media: dict[str, str] | None = None,
    pinned: list[str] | None = None,
    excluded: list[str] | None = None,
    must_cover: list[str] | None = None,
    alternatives: dict[str, list[str]] | None = None,
    importance: Any = DEFAULT_IMPORTANCE,
    threshold: float = GOOD_IMAGE_THRESHOLD,
    max_per_series: int = MAX_PER_SERIES,
) -> dict[str, Any]:
    """What one day has earned, in the order of section 2 of the brief.

    Excludes out, pins protected, quality gate, series cap, coverage
    exceptions, then the day's own ceiling. The result carries a reason
    per picture so a person can see why anything is in or out.
    """
    pinned_set = {str(value) for value in (pinned or [])}
    excluded_set = {str(value) for value in (excluded or [])}
    series = series_by_media or {}
    analyses = analyses or {}

    # 1-3: the day's curated order, minus what the user threw out. A pin
    # is protected from every rule below, including the day's cap.
    candidates = [
        media_id
        for media_id in (curated or [])
        if str(media_id) and str(media_id) not in excluded_set
    ]
    reasons: dict[str, str] = {}
    chosen: list[str] = []
    per_series: dict[str, int] = {}

    for media_id in candidates:
        if media_id in pinned_set:
            chosen.append(media_id)
            reasons[media_id] = REASON_PINNED
            # A pin does NOT spend the series budget. Counting it here
            # made a hand-picked third frame of a burst take the place of
            # an automatic one, so asking for one more picture silently
            # cost another - which is the opposite of what pinning means.
            # A series may exceed its cap precisely when somebody said so.

    # 4-5: the quality gate, then the burst cap - strongest first, so a
    # series contributes its best rather than its earliest.
    automatic = [
        media_id
        for media_id in candidates
        if media_id not in pinned_set and score_of(analyses, media_id) >= threshold
    ]
    automatic.sort(key=lambda media_id: -score_of(analyses, media_id))
    for media_id in automatic:
        group = series.get(media_id)
        if group and per_series.get(group, 0) >= max_per_series:
            continue
        chosen.append(media_id)
        reasons[media_id] = REASON_EARNED
        if group:
            per_series[group] = per_series.get(group, 0) + 1

    # 6: a picture below the bar may still be the only evidence of a
    # subject the day is about. One per unmet requirement, and only when
    # nothing already chosen answers it - not a general softening.
    exceptions: list[str] = []
    for motif in list(must_cover or []):
        options = list((alternatives or {}).get(motif) or [])
        if any(_covers(analyses.get(media_id), motif, options) for media_id in chosen):
            continue
        rescue = [
            media_id
            for media_id in candidates
            if media_id not in reasons
            and _covers(analyses.get(media_id), motif, options)
        ]
        if not rescue:
            continue
        best = max(rescue, key=lambda media_id: score_of(analyses, media_id))
        chosen.append(best)
        reasons[best] = REASON_COVERAGE
        exceptions.append(best)

    # 7-8: the demand, then the day's ceiling. Pins survive the cap; the
    # automatic picks give way instead, weakest first.
    demand = list(chosen)
    cap = cap_for(importance)
    if len(demand) > cap:
        keep_first = [media_id for media_id in demand if reasons[media_id] != REASON_EARNED]
        earned = [media_id for media_id in demand if reasons[media_id] == REASON_EARNED]
        earned.sort(key=lambda media_id: -score_of(analyses, media_id))
        room = max(0, cap - len(keep_first))
        demand = keep_first + earned[:room]

    # Back into the curation's own order, which is the order a day reads
    # in: the selection decides WHICH pictures, not in which sequence.
    order = {media_id: index for index, media_id in enumerate(candidates)}
    demand.sort(key=lambda media_id: order.get(media_id, 0))
    return {
        "media_ids": demand,
        "reasons": {media_id: reasons[media_id] for media_id in demand},
        "cap": cap,
        "importance": _importance(importance),
        "curated_count": len(curated or []),
        "above_threshold": len(automatic),
        "after_series_cap": sum(
            1 for media_id in chosen if reasons.get(media_id) == REASON_EARNED
        ),
        "coverage_exceptions": [
            media_id for media_id in demand if reasons.get(media_id) == REASON_COVERAGE
        ],
        "pinned_kept": [
            media_id for media_id in demand if reasons.get(media_id) == REASON_PINNED
        ],
    }


def allocate_trip(
    days: list[dict[str, Any]],
    *,
    threshold: float = GOOD_IMAGE_THRESHOLD,
    max_per_series: int = MAX_PER_SERIES,
    global_cap: int = MAX_FILM_PHOTOS,
) -> dict[str, Any]:
    """Every day's earned selection, then the film's own ceiling.

    Under the ceiling nothing is padded: free places stay free. Over it,
    the reduction is GLOBAL and by merit - falling back to per-day quotas
    here would reinstate the fixed allocation this module replaces.
    """
    per_day: dict[str, dict[str, Any]] = {}
    for day in days or []:
        if not isinstance(day, dict):
            continue
        day_id = str(day.get("chapter_id") or day.get("day_id") or "")
        if not day_id:
            continue
        per_day[day_id] = earned_for_day(
            curated=list(day.get("curated") or []),
            analyses=day.get("analyses") or {},
            series_by_media=day.get("series_by_media") or {},
            pinned=list(day.get("pinned") or []),
            excluded=list(day.get("excluded") or []),
            must_cover=list(day.get("must_cover") or []),
            alternatives=day.get("alternatives") or {},
            importance=day.get("importance"),
            threshold=threshold,
            max_per_series=max_per_series,
        )

    demanded = sum(len(entry["media_ids"]) for entry in per_day.values())
    removed: list[str] = []
    if demanded > global_cap:
        # Rank every automatic pick across the whole trip and drop the
        # weakest. Pins and coverage exceptions are never candidates;
        # importance breaks ties between similarly strong pictures, as a
        # protection rather than as a quota.
        rank = {
            "transition": 0,
            "normal": 1,
            "highlight": 2,
            "major_highlight": 3,
        }
        droppable: list[tuple[float, int, str, str]] = []
        for day_id, entry in per_day.items():
            day = next(
                (
                    item
                    for item in days
                    if str(item.get("chapter_id") or item.get("day_id") or "") == day_id
                ),
                {},
            )
            analyses = day.get("analyses") or {}
            for media_id in entry["media_ids"]:
                if entry["reasons"].get(media_id) != REASON_EARNED:
                    continue
                droppable.append(
                    (
                        score_of(analyses, media_id),
                        rank.get(entry["importance"], 1),
                        day_id,
                        media_id,
                    )
                )
        droppable.sort()
        for _score, _rank, day_id, media_id in droppable:
            if demanded <= global_cap:
                break
            entry = per_day[day_id]
            entry["media_ids"] = [
                value for value in entry["media_ids"] if value != media_id
            ]
            entry["reasons"].pop(media_id, None)
            removed.append(media_id)
            demanded -= 1

    total = sum(len(entry["media_ids"]) for entry in per_day.values())
    return {
        "days": per_day,
        "total": total,
        "global_cap": global_cap,
        "globally_removed": removed,
        "unused_budget": max(0, global_cap - total),
        "threshold": threshold,
        "max_per_series": max_per_series,
    }


def visual_richness(entry: dict[str, Any]) -> str:
    """A label for the panel, derived from the demand. Never an input.

    Deliberately one-way: story importance is what the day WAS, and a
    number of photographs must not be allowed to argue with it.
    """
    earned = int(entry.get("above_threshold") or 0)
    if earned >= 12:
        return "high"
    if earned >= 5:
        return "medium"
    return "low"


__all__ = [
    "GOOD_IMAGE_THRESHOLD",
    "MAX_FILM_PHOTOS",
    "MAX_PER_SERIES",
    "PHOTO_CAPS_BY_IMPORTANCE",
    "REASON_COVERAGE",
    "REASON_EARNED",
    "REASON_PINNED",
    "allocate_trip",
    "cap_for",
    "earned_for_day",
    "score_of",
    "visual_richness",
]


def score_distribution(scores: list[float]) -> dict[str, Any]:
    """The shape of what the analyses actually said.

    A threshold picked without this is a guess wearing a decimal point.
    `semantic_score` runs 0-20, and where the mass of a real trip sits on
    that range is the only thing that can justify a bar.
    """
    values = sorted(float(value) for value in scores)
    if not values:
        return {"count": 0}

    def _percentile(fraction: float) -> float:
        if len(values) == 1:
            return round(values[0], 2)
        position = fraction * (len(values) - 1)
        low = int(position)
        high = min(low + 1, len(values) - 1)
        weight = position - low
        return round(values[low] * (1 - weight) + values[high] * weight, 2)

    buckets: dict[str, int] = {}
    for value in values:
        edge = min(int(value // 2) * 2, 18)
        buckets[f"{edge}-{edge + 2}"] = buckets.get(f"{edge}-{edge + 2}", 0) + 1
    return {
        "count": len(values),
        "min": round(values[0], 2),
        "max": round(values[-1], 2),
        "p25": _percentile(0.25),
        "p50": _percentile(0.50),
        "median": _percentile(0.50),
        "p75": _percentile(0.75),
        "p90": _percentile(0.90),
        "mean": round(sum(values) / len(values), 2),
        "buckets": dict(sorted(buckets.items(), key=lambda item: int(item[0].split("-")[0]))),
    }
