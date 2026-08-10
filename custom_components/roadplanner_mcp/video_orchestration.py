"""What to analyse, what it would cost, and what came back.

The decisions of the video pipeline, with nothing that can call anything.
Every rule here is arithmetic over stored records, so the same code
answers "what would this cost?" before a button is pressed and "what do
we have?" after it - and a test can read all of it without a provider, a
browser or a file.

The service beside this module does the parts that touch the world: cut a
proxy, call Gemini, write the store. It asks this module what to do and
what to keep.

Three rules that are not obvious and are load-bearing:

**The cache is asked before the wallet.** A video whose content, model and
schema have not changed has already been answered. Rendering a film must
never produce a paid call - the film reads stored segments, and if there
are none the film has no clips, which is a normal state and not an error.

**A refusal is better than a repair.** A segment that runs past the end of
its recording is not a range that needs clamping; it is evidence the
answer is about something else. Repairing it would produce something that
looks exactly like a good answer.

**A video is a medium like a photograph.** Segments do not get their own
budget. They compete for the day's places, and a clip that wins takes a
photograph's slot rather than being added beside it.
"""

from __future__ import annotations

from typing import Any

from .video_analysis import (
    CLIP_ROLES,
    MAX_SEGMENT_SECONDS,
    MIN_SEGMENT_SECONDS,
    ROLE_AMBIENT,
    ROLE_HERO,
    TOKENS_PER_VIDEO_SECOND,
    clip_score,
)
from .video_asset import cache_key

# What one analysed second costs, in euro, at low media resolution.
#
# Video is billed per token and a second of video at the cheap resolution
# is roughly a hundred of them. The number below is deliberately a
# constant with a name rather than a literal in a message: it is an
# ESTIMATE shown to a person before they spend money, and when the
# provider's pricing moves this is the one place that has to move.
#
# Derived from the published per-million-token price for the analysis
# model, rounded UP, because an estimate that turns out too low is the
# one that damages trust.
EUR_PER_MILLION_TOKENS = 0.30
TOKENS_PER_SECOND_LOW = 100

# How many videos one press may pay for. A three-week trip holds ten to
# fifteen recordings; this is a guard against a folder nobody expected,
# not a working limit.
MAX_ANALYSES_PER_RUN = 30

# How many clips a day may show, by what the story says the day was.
# Movement is an ADDITION to a day in the sense that it is another kind
# of medium - never an addition to its budget.
CLIPS_BY_IMPORTANCE = {
    "transition": 1,
    "normal": 2,
    "highlight": 3,
    "major_highlight": 3,
}

# What a clip has to be worth to take a photograph's place.
#
# `clip_score` weighs story value and visual quality the same way the
# photo scorer does. A clip below this is not bad - it is simply not
# better than the picture it would displace, and displacing a good
# photograph with a mediocre clip is how "we added video" makes a film
# worse.
MIN_CLIP_SCORE = 6.0

STATE_CACHED = "cached"
STATE_NEW = "new"
STATE_SKIPPED = "skipped"


def _seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):
        return 0.0
    return max(0.0, number)


def analysis_plan(
    candidates: list[dict[str, Any]],
    stored: dict[str, Any],
    *,
    model: str,
    schema_version: int,
    force: bool = False,
    limit: int = MAX_ANALYSES_PER_RUN,
) -> dict[str, Any]:
    """Which windows need a paid look, and which are already answered.

    `stored` is keyed by the same cache key the service writes, so this
    cannot disagree with what was saved: the key is computed here by the
    production function rather than reconstructed.

    `force` is the explicit "analyse again" - it plans every candidate as
    new without deleting anything, so a failed re-analysis leaves the old
    answer intact.
    """
    fresh: list[dict[str, Any]] = []
    cached: list[dict[str, Any]] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        key = cache_key(candidate, model=model, schema_version=schema_version)
        entry = {**candidate, "cache_key": key}
        if not force and isinstance(stored.get(key), dict):
            cached.append(entry)
        else:
            fresh.append(entry)

    capped = fresh[: max(0, int(limit))]
    dropped = len(fresh) - len(capped)
    seconds = sum(
        _seconds(entry.get("segment_end")) - _seconds(entry.get("segment_start"))
        for entry in capped
    )
    tokens = int(round(seconds * TOKENS_PER_SECOND_LOW))
    return {
        "new": capped,
        "cached": cached,
        "over_limit": dropped,
        "new_count": len(capped),
        "cached_count": len(cached),
        "analysis_seconds": round(seconds, 1),
        "estimated_tokens": tokens,
        # Rounded to cents and never down. A person deciding whether to
        # spend money is owed the pessimistic number.
        "estimated_eur": round(
            (tokens / 1_000_000) * EUR_PER_MILLION_TOKENS + 0.004, 2
        ),
        "model": model,
        "schema_version": schema_version,
    }


def normalise_segment(
    parsed: dict[str, Any],
    *,
    window_start: float,
    duration_seconds: float,
) -> dict[str, Any] | None:
    """One accepted answer, placed back on the recording's own timeline.

    The model is asked about a window and answers in the window's own
    seconds. A segment that stays in those coordinates is unplayable: the
    film cuts from the RECORDING, so 2.5 seconds into window three is not
    2.5 seconds into the file.

    Returns None when the result cannot be placed - which is a refusal,
    not a repair.
    """
    if not isinstance(parsed, dict):
        return None
    start = _seconds(parsed.get("start_seconds")) + _seconds(window_start)
    end = _seconds(parsed.get("end_seconds")) + _seconds(window_start)
    if end <= start:
        return None
    length = end - start
    if length < MIN_SEGMENT_SECONDS or length > MAX_SEGMENT_SECONDS:
        return None
    # The recording's real length is the only authority on whether a
    # segment exists. A duration of zero means nobody measured it, and a
    # bound that is not known cannot be enforced - so it is not.
    total = _seconds(duration_seconds)
    if total and end > total + 0.25:
        return None
    role = str(parsed.get("role") or "")
    return {
        **parsed,
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "duration_seconds": round(length, 3),
        "role": role if role in CLIP_ROLES else ROLE_AMBIENT,
    }


def merge_overlapping(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One moment, once - even when three windows all noticed it.

    Windows overlap on purpose, so the same four seconds can be proposed
    by two of them. Keeping both would put the same clip in the film
    twice, which is the photograph duplication bug in a different
    medium. The stronger proposal wins the ground.
    """
    ordered = sorted(
        (segment for segment in segments or [] if isinstance(segment, dict)),
        key=lambda segment: (-clip_score(segment), _seconds(segment.get("start_seconds"))),
    )
    kept: list[dict[str, Any]] = []
    for segment in ordered:
        start = _seconds(segment.get("start_seconds"))
        end = _seconds(segment.get("end_seconds"))
        clash = False
        for chosen in kept:
            if start < _seconds(chosen.get("end_seconds")) and end > _seconds(
                chosen.get("start_seconds")
            ):
                clash = True
                break
        if not clash:
            kept.append(segment)
    return sorted(kept, key=lambda segment: _seconds(segment.get("start_seconds")))


def clips_for_day(
    segments: list[dict[str, Any]],
    *,
    importance: str = "normal",
    min_score: float = MIN_CLIP_SCORE,
) -> list[dict[str, Any]]:
    """The clips one day shows, strongest first and never all of them.

    Twelve recordings can legitimately produce four clips. A day that
    used everything it had would be a camera roll rather than a film, and
    the cap by importance is the same idea as the photo cap: what the
    story says the day was decides how much room it gets.
    """
    scored = [
        segment
        for segment in segments or []
        if isinstance(segment, dict) and clip_score(segment) >= min_score
    ]
    scored.sort(key=lambda segment: -clip_score(segment))
    cap = CLIPS_BY_IMPORTANCE.get(str(importance or ""), CLIPS_BY_IMPORTANCE["normal"])
    chosen = scored[:cap]
    # At most one hero per day: two "the moment of the day" clips is no
    # moment of the day. The runner-up keeps its place, as an ordinary one.
    seen_hero = False
    result: list[dict[str, Any]] = []
    for segment in chosen:
        entry = dict(segment)
        if entry.get("role") == ROLE_HERO:
            if seen_hero:
                entry["role"] = ROLE_AMBIENT
            seen_hero = True
        result.append(entry)
    return result


def photo_places_taken(clips: list[dict[str, Any]]) -> int:
    """How many photographs the clips displace. One each, and no more.

    This is the whole of "a video is a medium like a photograph": the
    day's places are a single budget, so a clip that joins the film costs
    a picture rather than lengthening the day. Without it, adding video
    would inflate every day that has any - which is the failure the photo
    allocation was just rebuilt to remove.
    """
    return len([clip for clip in clips or [] if isinstance(clip, dict)])


__all__ = [
    "CLIPS_BY_IMPORTANCE",
    "EUR_PER_MILLION_TOKENS",
    "MAX_ANALYSES_PER_RUN",
    "MIN_CLIP_SCORE",
    "TOKENS_PER_SECOND_LOW",
    "analysis_plan",
    "clips_for_day",
    "merge_overlapping",
    "normalise_segment",
    "photo_places_taken",
]
