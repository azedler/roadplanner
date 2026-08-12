"""What actually happens in the film, said in terms music can use.

Between the scene plan and any decision about music there was nothing.
The planner cut the film into equal shares by duration and never looked
at what was in them - so a section boundary could land in the middle of
a map drive, and the "Aufbruch" could be four minutes of somebody's
seventh day.

A cue sheet is that missing layer. It reads the plan the film is
actually made of and describes it in the only terms a composer needs:
when something starts, how long it lasts, whether it moves, whether
there is text to read under it, whether a day begins there.

Three properties, and each is a rule this project has paid to learn:

**Provider-neutral.** Nothing here knows what Gemini or Lyria are. It is
arithmetic over a plan, so it can be checked without a network, without
a key, and without spending anything.

**Deterministic.** The same plan yields the same cue sheet, byte for
byte. It is part of the cache key for music that costs money, so a sheet
that wobbled would buy a second soundtrack for a film nobody changed.

**It describes, it does not decide.** Where a musical section begins is
the plan's business, not this module's. This one says what is there.
"""

from __future__ import annotations

from typing import Any

from .trip_film_plan import (
    FILM_FPS,
    SCENE_CHAPTER_CARD,
    SCENE_CLIP,
    SCENE_COLLAGE,
    SCENE_CREW,
    SCENE_HERO,
    SCENE_INTRO,
    SCENE_MAP_FULL,
    SCENE_MAP_LEG,
    SCENE_MAP_START,
    SCENE_OUTRO,
    SCENE_OUTRO_COLLAGE,
    SCENE_PHOTO,
    SCENE_TEXT,
)

CUE_SHEET_VERSION = 1

# Scenes that carry a moving picture, which is what music has to breathe
# with. A map leg drives; a photograph does not.
_MOVING = frozenset({SCENE_MAP_LEG, SCENE_CLIP})
_MAP = frozenset({SCENE_MAP_START, SCENE_MAP_FULL, SCENE_MAP_LEG})
# Scenes somebody reads. Music under text wants to stay out of the way.
_TEXT = frozenset({SCENE_INTRO, SCENE_OUTRO, SCENE_TEXT, SCENE_CHAPTER_CARD})
_STILL = frozenset({SCENE_PHOTO, SCENE_HERO, SCENE_COLLAGE, SCENE_OUTRO_COLLAGE})

# How busy a cue is, on a scale a prompt can use. Deliberately three
# words rather than a number: "0.62 energy" is a figure nobody can check
# and a model cannot hear.
ENERGY_CALM = "ruhig"
ENERGY_STEADY = "gleichmaessig"
ENERGY_LIVELY = "bewegt"

# What a cue is FOR, in the film's own terms.
FUNCTION_OPENING = "eroeffnung"
FUNCTION_DAY = "reisetag"
FUNCTION_TRAVEL = "fahrt"
FUNCTION_MOMENT = "moment"
FUNCTION_CLOSING = "abschluss"


class CueSheetError(ValueError):
    """A plan this cannot describe. Said rather than approximated."""


def _energy(scenes: list[dict[str, Any]]) -> str:
    """How much of this cue moves."""
    frames = sum(max(0, int(scene.get("frames") or 0)) for scene in scenes)
    if frames <= 0:
        return ENERGY_CALM
    moving = sum(
        max(0, int(scene.get("frames") or 0))
        for scene in scenes
        if str(scene.get("type") or "") in _MOVING
    )
    share = moving / frames
    # Measured against real day shapes rather than chosen: across a
    # transition day, an ordinary day, a day with a clip, a highlight
    # with two, a photo day with no map, and a long drive, the moving
    # share runs from 0.00 to 0.58 and clusters near 0.3-0.45.
    #
    # The first bands were 0.15 and 0.50, and at 0.50 nothing real ever
    # reached "bewegt" - a word in the vocabulary that no film could
    # produce, which is an absent answer wearing a name. These split the
    # measured shapes the way somebody watching them would.
    if share >= 0.40:
        return ENERGY_LIVELY
    if share >= 0.20:
        return ENERGY_STEADY
    return ENERGY_CALM


def _function(scenes: list[dict[str, Any]], *, first: bool, last: bool) -> str:
    kinds = {str(scene.get("type") or "") for scene in scenes}
    if first or kinds & {SCENE_INTRO, SCENE_CREW}:
        return FUNCTION_OPENING
    if last or kinds & {SCENE_OUTRO, SCENE_OUTRO_COLLAGE}:
        return FUNCTION_CLOSING
    if SCENE_CHAPTER_CARD in kinds:
        return FUNCTION_DAY
    if kinds & _MAP:
        return FUNCTION_TRAVEL
    return FUNCTION_MOMENT


def build_cue_sheet(
    plan: dict[str, Any],
    *,
    chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The film's own course, one cue per chapter plus its framing.

    A cue is a stretch of film that belongs together musically: the
    opening before the first day, then each day, then the closing. That
    is the grain music works at - §29's "no micro-scoring" is not a
    preference here, it is the unit this file is built in.
    """
    scenes = list(plan.get("scenes") or [])
    if not scenes:
        raise CueSheetError("Ein Film ohne Szenen hat keinen Ablauf")
    fps = int(plan.get("fps") or FILM_FPS)
    by_chapter = {
        str(entry.get("chapter_id") or ""): entry for entry in (chapters or [])
    }

    # Grouped by the film's own chapters, in the order they play. A cue
    # boundary that fell inside a day would ask the music to change in
    # the middle of one, which is exactly what §41 forbids.
    groups: list[tuple[str, list[dict[str, Any]], int]] = []
    cursor = 0
    for scene in scenes:
        frames = max(0, int(scene.get("frames") or 0))
        if frames <= 0:
            continue
        name = str(scene.get("chapter_id") or "")
        if groups and groups[-1][0] == name:
            groups[-1][1].append(scene)
        else:
            groups.append((name, [scene], cursor))
        cursor += frames

    cues: list[dict[str, Any]] = []
    for index, (chapter_id, members, start_frame) in enumerate(groups):
        frames = sum(max(0, int(scene.get("frames") or 0)) for scene in members)
        kinds = {str(scene.get("type") or "") for scene in members}
        chapter = by_chapter.get(chapter_id) or {}
        cues.append(
            {
                "index": index,
                "start_seconds": round(start_frame / fps, 2),
                "end_seconds": round((start_frame + frames) / fps, 2),
                "seconds": round(frames / fps, 2),
                "chapter_id": chapter_id,
                # From the chapter when there is one, and simply absent
                # when there is not - the framing scenes belong to no day
                # and inventing a story role for them would be a fact
                # made up for a prompt.
                "story_role": str(chapter.get("story_role") or ""),
                "importance": str(chapter.get("importance") or ""),
                "day_number": chapter.get("day_number"),
                "scene_types": sorted(kinds),
                "has_video": SCENE_CLIP in kinds,
                "has_map": bool(kinds & _MAP),
                "has_text": bool(kinds & _TEXT),
                "has_stills": bool(kinds & _STILL),
                "is_intro": SCENE_INTRO in kinds,
                "is_outro": bool(kinds & {SCENE_OUTRO, SCENE_OUTRO_COLLAGE}),
                "energy_hint": _energy(members),
                "narrative_function": _function(
                    members, first=index == 0, last=index == len(groups) - 1
                ),
            }
        )

    total = sum(max(0, int(scene.get("frames") or 0)) for scene in scenes)
    return {
        "cue_sheet_version": CUE_SHEET_VERSION,
        "fps": fps,
        "film_seconds": round(total / fps, 2),
        "cue_count": len(cues),
        "cues": cues,
    }


# A cue sheet for a single stretch of film needs a finer grain than one
# per chapter: a seventy-five-second excerpt is often one chapter, and
# "one cue" describes nothing. But finer must not become per-scene -
# music that comments on every cut is the micro-scoring this whole file
# is built to avoid. So the grain is the RUN: consecutive scenes that
# behave the same way.
CHARACTER_TRAVEL = "fahrt"
CHARACTER_READING = "lesen"
CHARACTER_MOTION = "bewegtbild"
CHARACTER_STILLS = "bilder"

# Under this a run is not a musical movement, it is a moment; it gets
# folded into its neighbour instead of becoming a segment of its own.
MIN_SEGMENT_SECONDS = 8.0
# And this many segments is already more than a minute of music can act
# on. The cap is what keeps §11 honest when a busy excerpt would
# otherwise produce nine of them.
MAX_WINDOW_CUES = 5


def _character(scene_type: str) -> str:
    if scene_type in _MAP:
        return CHARACTER_TRAVEL
    if scene_type == SCENE_CLIP:
        return CHARACTER_MOTION
    if scene_type in _TEXT:
        return CHARACTER_READING
    return CHARACTER_STILLS


def build_window_cue_sheet(
    plan: dict[str, Any],
    *,
    start_frame: int,
    end_frame: int,
    chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The larger movements inside one stretch of film.

    Times come out in two forms and both are named: where the segment
    sits in the whole film, and where it sits inside the excerpt. A
    planner that only saw one of them would either place music against
    the wrong clock or have to work the other one out - and working out
    a time is the one thing a language model must not be doing here.

    Frames in, seconds out. The excerpt is chosen in frames because a
    frame is where a scene actually begins; rounding that to seconds
    first and cutting there is how a photograph ends up half faded in.
    """
    scenes = list(plan.get("scenes") or [])
    if not scenes:
        raise CueSheetError("Ein Film ohne Szenen hat keinen Ablauf")
    fps = int(plan.get("fps") or FILM_FPS)
    begin = max(0, int(start_frame))
    stop = int(end_frame)
    if stop <= begin:
        raise CueSheetError("Der Ausschnitt hat keine Länge")
    by_chapter = {
        str(entry.get("chapter_id") or ""): entry for entry in (chapters or [])
    }

    # Every scene that the window touches, clipped to it.
    inside: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for scene in scenes:
        frames = max(0, int(scene.get("frames") or 0))
        if frames <= 0:
            continue
        scene_start, scene_end = cursor, cursor + frames
        cursor = scene_end
        if scene_end <= begin or scene_start >= stop:
            continue
        inside.append((max(scene_start, begin), min(scene_end, stop), scene))
    if not inside:
        raise CueSheetError("In diesem Ausschnitt liegt keine Szene")

    # Runs of like-behaving scenes.
    runs: list[dict[str, Any]] = []
    for scene_start, scene_end, scene in inside:
        character = _character(str(scene.get("type") or ""))
        if runs and runs[-1]["character"] == character:
            runs[-1]["end"] = scene_end
            runs[-1]["scenes"].append(scene)
            continue
        runs.append(
            {
                "character": character,
                "start": scene_start,
                "end": scene_end,
                "scenes": [scene],
            }
        )

    # Anything too short to be a movement joins its predecessor - or its
    # successor when it is the first. Repeated until nothing is short,
    # because folding two shorts together can still leave a short.
    least = MIN_SEGMENT_SECONDS * fps
    merged = True
    while merged and len(runs) > 1:
        merged = False
        for index, run in enumerate(runs):
            if run["end"] - run["start"] >= least:
                continue
            target = index - 1 if index > 0 else 1
            keep, drop = (target, index) if index > 0 else (index, target)
            runs[keep]["end"] = max(runs[keep]["end"], runs[drop]["end"])
            runs[keep]["start"] = min(runs[keep]["start"], runs[drop]["start"])
            runs[keep]["scenes"].extend(runs[drop]["scenes"])
            del runs[drop]
            merged = True
            break

    # Still too many? Fold the shortest into its neighbour until the cap
    # holds. Shortest first, so the segments that survive are the ones
    # that actually last long enough to be scored.
    while len(runs) > MAX_WINDOW_CUES:
        index = min(
            range(len(runs)), key=lambda position: runs[position]["end"] - runs[position]["start"]
        )
        target = index - 1 if index > 0 else 1
        keep, drop = (target, index) if index > 0 else (index, target)
        runs[keep]["end"] = max(runs[keep]["end"], runs[drop]["end"])
        runs[keep]["start"] = min(runs[keep]["start"], runs[drop]["start"])
        runs[keep]["scenes"].extend(runs[drop]["scenes"])
        del runs[drop]

    cues: list[dict[str, Any]] = []
    for index, run in enumerate(runs):
        members = run["scenes"]
        kinds = {str(scene.get("type") or "") for scene in members}
        names: list[str] = []
        for scene in members:
            name = str(scene.get("chapter_id") or "")
            if name and name not in names:
                names.append(name)
        chapter = by_chapter.get(names[0]) if names else None
        cues.append(
            {
                "index": index,
                "character": run["character"],
                "start_seconds": round(run["start"] / fps, 2),
                "end_seconds": round(run["end"] / fps, 2),
                "seconds": round((run["end"] - run["start"]) / fps, 2),
                # The same moment on the excerpt's own clock, so nothing
                # downstream has to subtract anything to place music.
                "window_start_seconds": round((run["start"] - begin) / fps, 2),
                "window_end_seconds": round((run["end"] - begin) / fps, 2),
                "chapter_ids": names,
                "story_role": str((chapter or {}).get("story_role") or ""),
                "importance": str((chapter or {}).get("importance") or ""),
                "scene_types": sorted(kinds),
                "has_map": bool(kinds & _MAP),
                "has_text": bool(kinds & _TEXT),
                "has_photo": bool(kinds & _STILL),
                "has_video": SCENE_CLIP in kinds,
                "energy_hint": _energy(members),
                "narrative_function": _function(
                    members, first=False, last=False
                ),
                # Whether the film changes day here. A chapter boundary
                # is the one transition worth telling a composer about;
                # every other cut is a cut.
                "transition_hint": (
                    "kapitelwechsel"
                    if index > 0 and names and names != cues[index - 1]["chapter_ids"]
                    else "weiter"
                ),
            }
        )

    return {
        "cue_sheet_version": CUE_SHEET_VERSION,
        "fps": fps,
        "start_seconds": round(begin / fps, 2),
        "end_seconds": round(stop / fps, 2),
        "window_seconds": round((stop - begin) / fps, 2),
        "cue_count": len(cues),
        "cues": cues,
    }


def summarise(sheet: dict[str, Any]) -> dict[str, Any]:
    """The whole film in the few numbers a plan is argued from.

    What a model gets instead of the full list when the full list would
    be longer than the thinking it supports.
    """
    cues = list(sheet.get("cues") or [])
    return {
        "film_seconds": sheet.get("film_seconds"),
        "cue_count": len(cues),
        "with_video": sum(1 for cue in cues if cue.get("has_video")),
        "with_map": sum(1 for cue in cues if cue.get("has_map")),
        "lively": sum(1 for cue in cues if cue.get("energy_hint") == ENERGY_LIVELY),
        "calm": sum(1 for cue in cues if cue.get("energy_hint") == ENERGY_CALM),
        "days": sum(
            1 for cue in cues if cue.get("narrative_function") == FUNCTION_DAY
        ),
    }


__all__ = [
    "CHARACTER_MOTION",
    "CHARACTER_READING",
    "CHARACTER_STILLS",
    "CHARACTER_TRAVEL",
    "CUE_SHEET_VERSION",
    "MAX_WINDOW_CUES",
    "MIN_SEGMENT_SECONDS",
    "build_window_cue_sheet",
    "ENERGY_CALM",
    "ENERGY_LIVELY",
    "ENERGY_STEADY",
    "FUNCTION_CLOSING",
    "FUNCTION_DAY",
    "FUNCTION_MOMENT",
    "FUNCTION_OPENING",
    "FUNCTION_TRAVEL",
    "CueSheetError",
    "build_cue_sheet",
    "summarise",
]
