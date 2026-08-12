"""Which minute of the film gets rendered at full size for the check.

A full film at 1440p is projected at around an hour and a half. Nobody
looks at a cut that costs that much, which is why a quality check has to
be a *piece* of the film rather than the film.

The piece is cut from the plan the film already has. Not a second plan,
not a re-planning with different settings: the same scene ids, the same
media, the same seconds, the same map. Only where it starts, where it
stops, and how many pixels it is drawn with. Anything else and the check
would be about a film nobody is going to ship.

Two rules the selection follows, and both are about honesty rather than
cleverness:

**It never splits a scene.** A window runs from one scene boundary to
another, so what is seen is whole scenes in the order the film has them.
Cutting mid-scene would show a photograph fading in and never landing,
and somebody would report that as a bug in the film.

**It only scores what the plan actually carries.** The plan has no
orientation of any photograph in it - so this does not pretend to balance
portrait against landscape, it says that it could not. Reading a field
off an object that never had it is this project's most repeated mistake;
a wish list in a brief is not a reason to start.
"""

from __future__ import annotations

from typing import Any

from .trip_film_plan import (
    FILM_FPS,
    SCENE_CHAPTER_CARD,
    SCENE_CLIP,
    SCENE_COLLAGE,
    SCENE_HERO,
    SCENE_INTRO,
    SCENE_MAP_FULL,
    SCENE_MAP_LEG,
    SCENE_MAP_START,
    SCENE_OUTRO,
    SCENE_OUTRO_COLLAGE,
    SCENE_TEXT,
)

# What a quality check is worth looking at. Under a minute and there is
# not enough film to judge a rhythm; over ninety seconds and the render
# stops being cheap, which was the whole point.
QA_MIN_SECONDS = 60.0
QA_MAX_SECONDS = 90.0

# What makes a window worth choosing, and what each is worth.
#
# A map leg is the most expensive thing in the film to get right - it
# moves, it carries labels, and it is where scaling shows first - so it
# counts for more than a still. A chapter card is what a transition looks
# like. Deliberately a small, readable table rather than a formula.
_INGREDIENTS: dict[str, tuple[str, ...]] = {
    "chapter_transition": (SCENE_CHAPTER_CARD,),
    "map": (SCENE_MAP_START, SCENE_MAP_FULL, SCENE_MAP_LEG),
    "map_drive": (SCENE_MAP_LEG,),
    "text": (SCENE_TEXT, SCENE_INTRO, SCENE_OUTRO),
    "hero": (SCENE_HERO,),
    "collage": (SCENE_COLLAGE, SCENE_OUTRO_COLLAGE),
    "clip": (SCENE_CLIP,),
}
_WEIGHTS: dict[str, int] = {
    "map_drive": 4,
    "clip": 4,
    "hero": 3,
    "collage": 3,
    "chapter_transition": 2,
    "text": 2,
    "map": 1,
}

# The plan carries no orientation, so nothing here can weigh it.
UNSCORED = ("portrait_landscape_mix",)


class QaExcerptError(ValueError):
    """The plan cannot yield a window. Said plainly rather than guessed at."""


def _offsets(plan: dict[str, Any]) -> list[tuple[int, int, dict[str, Any]]]:
    """Every scene with the frame it starts on and the frame after it."""
    found: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for scene in plan.get("scenes") or []:
        frames = max(0, int(scene.get("frames") or 0))
        if frames <= 0:
            continue
        found.append((cursor, cursor + frames, scene))
        cursor += frames
    return found


def _contains(scenes: list[dict[str, Any]]) -> dict[str, bool]:
    kinds = {str(scene.get("type") or "") for scene in scenes}
    return {
        name: bool(kinds & set(types)) for name, types in _INGREDIENTS.items()
    }


def _score(contains: dict[str, bool]) -> int:
    return sum(_WEIGHTS[name] for name, present in contains.items() if present)


def excerpt_range(
    plan: dict[str, Any],
    *,
    chapter_id: str = "",
    start_seconds: float | None = None,
    min_seconds: float = QA_MIN_SECONDS,
    max_seconds: float = QA_MAX_SECONDS,
) -> dict[str, Any]:
    """The frames the QA clip shows, and why those.

    With neither `chapter_id` nor `start_seconds`, every window in the
    film is scored and the richest one wins - earliest on a tie, so the
    same plan always yields the same clip. With either of them, the
    window starts where it was asked to and simply grows to length.
    """
    fps = int(plan.get("fps") or FILM_FPS)
    entries = _offsets(plan)
    if not entries:
        raise QaExcerptError("Dieser Film hat keine Szenen, aus denen ein Ausschnitt käme")

    total_frames = entries[-1][1]
    least = int(round(max(1.0, float(min_seconds)) * fps))
    most = int(round(max(float(min_seconds), float(max_seconds)) * fps))

    # A film shorter than the window is its own excerpt. Refusing here
    # would make the check impossible for exactly the short test films
    # this is developed against.
    if total_frames <= least:
        chosen = [scene for _start, _end, scene in entries]
        return _describe(0, total_frames, chosen, fps, "ganzer Film", total_frames)

    starts = list(range(len(entries)))
    if chapter_id:
        wanted = str(chapter_id)
        starts = [
            index
            for index, (_start, _end, scene) in enumerate(entries)
            if str(scene.get("chapter_id") or "") == wanted
        ]
        if not starts:
            raise QaExcerptError(f"Zu diesem Tag gibt es keine Szenen: {chapter_id}")
        # From the beginning of that day, so the clip opens the way the
        # day opens rather than in the middle of it.
        starts = [starts[0]]
        reason = "gewählter Tag"
    elif start_seconds is not None:
        mark = max(0, int(round(float(start_seconds) * fps)))
        # Clamped to the last start that still yields a full window, not
        # silently reset to the beginning. A time past the end of the film
        # is a plausible thing to ask for - the film got shorter, or the
        # number came from an older render - and answering it with "minute
        # zero" would look like the request was honoured.
        latest = max(0, total_frames - least)
        mark = min(mark, latest)
        starts = [
            next(
                (
                    index
                    for index, (start, end, _scene) in enumerate(entries)
                    if start <= mark < end
                ),
                len(entries) - 1,
            )
        ]
        reason = "gewählte Startzeit"
    else:
        reason = "automatisch gewählt"

    # How far from the film's middle a window sits, as the tie-break.
    #
    # Score alone is not enough, and a real film showed why. On the trip
    # this was first run against, the opening minute contained all seven
    # ingredients and therefore scored the maximum - as did many later
    # windows - and "earliest wins" handed back minute zero every time.
    # That is the one minute §22 warned against and the least
    # representative part of the film: intro, crew and the starting map
    # occur exactly once, while the body of the film is days.
    #
    # A synthetic plan hid it. Its opening did not happen to contain
    # everything, so the check that the excerpt is not minute zero passed
    # while the real answer was minute zero - a test agreeing with an
    # assumption instead of with the data.
    middle = total_frames // 2

    def _distance(begin: int, end: int) -> int:
        return abs((begin + end) // 2 - middle)

    best: tuple[int, int, int, list[dict[str, Any]]] | None = None
    for index in starts:
        begin = entries[index][0]
        window: list[dict[str, Any]] = []
        for start, end, scene in entries[index:]:
            window.append(scene)
            length = end - begin
            if length < least:
                continue
            if length > most and len(window) > 1:
                # Adding this scene overshot; the window without it was
                # already long enough and is the one to judge.
                break
            candidate = (
                _score(_contains(window)),
                -_distance(begin, end),
                begin,
                end,
                list(window),
            )
            if best is None or candidate[:3] > best[:3]:
                best = candidate
            break
        else:
            # The film ended before the window was full. What is left is
            # still a legitimate ending to look at.
            if window and (entries[-1][1] - begin) >= least // 2:
                candidate = (
                    _score(_contains(window)),
                    -_distance(begin, entries[-1][1]),
                    begin,
                    entries[-1][1],
                    list(window),
                )
                if best is None or candidate[:3] > best[:3]:
                    best = candidate

    if best is None:
        raise QaExcerptError("Es liess sich kein zusammenhängender Ausschnitt bilden")
    # `begin` is in the key after the distance, so two windows equally
    # far from the middle still resolve the same way on every run.
    score, _negative_distance, start_frame, end_frame, window = best
    return _describe(start_frame, end_frame, window, fps, reason, total_frames, score)


def _describe(
    start_frame: int,
    end_frame: int,
    window: list[dict[str, Any]],
    fps: int,
    reason: str,
    total_frames: int,
    score: int | None = None,
) -> dict[str, Any]:
    contains = _contains(window)
    chapters: list[str] = []
    for scene in window:
        name = str(scene.get("chapter_id") or "")
        if name and name not in chapters:
            chapters.append(name)
    frames = max(1, end_frame - start_frame)
    return {
        "start_frame": start_frame,
        # Inclusive, because that is how the renderer counts a range.
        "end_frame": max(start_frame, end_frame - 1),
        "frames": frames,
        "start_seconds": round(start_frame / fps, 2),
        "seconds": round(frames / fps, 2),
        "film_seconds": round(total_frames / fps, 2),
        "scene_count": len(window),
        "chapter_ids": chapters,
        "contains": contains,
        "missing": sorted(name for name, present in contains.items() if not present),
        # Named rather than silently absent: the plan carries no
        # orientation, so nothing here balanced portrait against
        # landscape, and saying so is better than letting somebody
        # assume it was considered.
        "unscored": list(UNSCORED),
        "reason": reason,
        "score": score,
    }


__all__ = [
    "QA_MAX_SECONDS",
    "QA_MIN_SECONDS",
    "QaExcerptError",
    "UNSCORED",
    "excerpt_range",
]
