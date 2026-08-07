"""Turn a described trip into a shot list. Deterministic, and pure.

Film v0 rendered every day the same way, so a three-week journey came out
as twenty-three identical cards followed by a slideshow. The story
director then started deciding which days matter - importance,
story_role, visual_style - and the film ignored all three. This module is
where that judgement finally reaches the screen.

The division of labour is the whole design, and it is worth stating
because each part is bad at the other two:

- **Gemini decides what matters.** It is the only one that read the
  trip as a whole.
- **Roadplanner supplies facts and pictures.** It is the only one that
  knows what actually happened.
- **This module decides how that looks**, from fixed rules. No model is
  called here and none may be: the same manifest has to produce the same
  film, or a re-render is a lottery and nothing about the result can be
  reasoned about.

Why a scene plan at all
-----------------------

It could have been done inside the composition. It is done here because
a rule like "a transition day gets one photo and five seconds" is a
sentence about editing, not about React - and because a plan built in
Python can be tested without a browser, a bundler or a render. The plan
is a **rendering derivation**: it is computed from the manifest, travels
in the render package, and never goes back into the manifest. Frame
counts have no business in a description of a journey.

The finite scene library
------------------------

Six kinds of scene and nothing else. ``visual_style`` selects among them;
it cannot describe a layout. That boundary is deliberate: a model that
could invent scene shapes would eventually invent one that cannot be
rendered, and the failure would arrive as a broken film rather than as a
refused value.
"""

from __future__ import annotations

from typing import Any

FILM_FPS = 30
PLAN_VERSION = 1

# --- the library ---------------------------------------------------------

SCENE_INTRO = "intro"
SCENE_CHAPTER_CARD = "chapter_card"
SCENE_PHOTO = "photo"
SCENE_HERO = "hero"
SCENE_COLLAGE = "collage"
SCENE_TEXT = "text"
SCENE_OUTRO = "outro"
SCENE_OUTRO_COLLAGE = "outro_collage"
SCENE_TYPES = (
    SCENE_INTRO,
    SCENE_CHAPTER_CARD,
    SCENE_PHOTO,
    SCENE_HERO,
    SCENE_COLLAGE,
    SCENE_TEXT,
    SCENE_OUTRO,
    SCENE_OUTRO_COLLAGE,
)

IMPORTANCE_TRANSITION = "transition"
IMPORTANCE_NORMAL = "normal"
IMPORTANCE_HIGHLIGHT = "highlight"
IMPORTANCE_MAJOR = "major_highlight"
_IMPORTANCE_ORDER = (
    IMPORTANCE_TRANSITION,
    IMPORTANCE_NORMAL,
    IMPORTANCE_HIGHLIGHT,
    IMPORTANCE_MAJOR,
)

# How long each kind of scene runs, per importance. A table rather than a
# solver: every number here can be argued about on its own, and the total
# for a day follows from the scenes it actually got. A day with one photo
# stays short even when it is a highlight - padding a thin day is exactly
# the "artificially extended" the brief rules out.
_CARD_FRAMES = {
    IMPORTANCE_TRANSITION: 45,
    IMPORTANCE_NORMAL: 60,
    IMPORTANCE_HIGHLIGHT: 75,
    IMPORTANCE_MAJOR: 90,
}
_PHOTO_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 105,
    IMPORTANCE_HIGHLIGHT: 110,
    IMPORTANCE_MAJOR: 120,
}
_HERO_FRAMES = {
    IMPORTANCE_TRANSITION: 120,
    IMPORTANCE_NORMAL: 150,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 210,
}
_COLLAGE_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 135,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 195,
}
# A day without photographs is not an error message. It is a page of
# writing, and it needs long enough to be read.
_TEXT_FRAMES = {
    IMPORTANCE_TRANSITION: 105,
    IMPORTANCE_NORMAL: 135,
    IMPORTANCE_HIGHLIGHT: 165,
    IMPORTANCE_MAJOR: 180,
}

# How many pictures a day is worth. The budget is shared, so a highlight
# takes room from a transfer day rather than from nowhere.
PHOTO_WEIGHTS = {
    IMPORTANCE_TRANSITION: 1,
    IMPORTANCE_NORMAL: 2,
    IMPORTANCE_HIGHLIGHT: 3,
    IMPORTANCE_MAJOR: 4,
}

INTRO_FRAMES = 135
OUTRO_FRAMES = 135
OUTRO_COLLAGE_FRAMES = 120
OUTRO_COLLAGE_PHOTOS = 6

# story_role does one job and only one: how a scene arrives. Making it a
# second sizing system would put it in a fight with importance, and the
# brief asks for the three fields to complement each other.
_ENTER_BY_ROLE = {
    "opening": "rise",
    "journey": "fade",
    "transition": "cut",
    "highlight": "push",
    "finale": "settle",
}
DEFAULT_ENTER = "fade"


class FilmPlanError(ValueError):
    """The plan cannot be built from this manifest."""


# --- readable places -----------------------------------------------------

# What a stop is called in the roadbook is what you navigate to. What a
# stop is called in a film is what you would say out loud. These are the
# prefixes real Roadplanner data actually carries.
_PROVIDER_PREFIXES = ("park4night", "p4n", "campercontact", "stellplatz", "camping")
_MAX_PLACE_LENGTH = 42


def _strip_parentheses(text: str) -> str:
    out: list[str] = []
    depth = 0
    for character in text:
        if character in "([":
            depth += 1
        elif character in ")]":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(character)
    return " ".join("".join(out).split())


def readable_place(stop: dict[str, Any]) -> str:
    """A stop name a film can show.

    The real example this exists for is
    ``park4night - (595 50) Mjölby - 24 Vetagatan``, which reached a title
    card verbatim and read like a database export. The editor's
    ``story_name`` wins when there is one; otherwise the canonical name is
    reduced conservatively - provider prefix, postcode in brackets, the
    street segment - and if none of that applies the name is returned
    untouched, because most of them are already fine.
    """
    story_name = str(stop.get("story_name") or "").strip()
    if story_name:
        return story_name[:_MAX_PLACE_LENGTH]

    text = " ".join(str(stop.get("name") or "").split())
    if not text:
        return ""
    lowered = text.casefold()
    for prefix in _PROVIDER_PREFIXES:
        for separator in (" - ", ": ", " – "):
            marker = f"{prefix}{separator}"
            if lowered.startswith(marker):
                text = text[len(marker) :].strip()
                lowered = text.casefold()
                break
    text = _strip_parentheses(text)
    segments = [part.strip() for part in text.replace(" – ", " - ").split(" - ")]
    segments = [part for part in segments if part]
    if not segments:
        return ""
    if len(segments) > 1:
        # A segment without digits is a place; one with them is an address.
        without_digits = [part for part in segments if not any(c.isdigit() for c in part)]
        text = without_digits[0] if without_digits else segments[0]
    else:
        text = segments[0]
    # A trailing house number says nothing on screen.
    words = text.split()
    while len(words) > 1 and any(c.isdigit() for c in words[-1]):
        words.pop()
    return " ".join(words)[:_MAX_PLACE_LENGTH]


def readable_places(stops: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    """The day's route, as few readable names, in order and without repeats."""
    names: list[str] = []
    for stop in stops:
        name = readable_place(stop if isinstance(stop, dict) else {})
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


# --- photo budget --------------------------------------------------------


def allocate_photos(
    chapters: list[dict[str, Any]], *, total_budget: int, per_chapter_cap: int
) -> dict[str, int]:
    """How many pictures each day gets, weighted by what it is worth.

    Flat allocation is what made film v0 feel like a contact sheet: a
    transfer day and the reason for the whole trip got the same three
    pictures. The budget is finite, so weighting is redistribution rather
    than growth - and a day is never given more pictures than it has.
    """
    if not chapters:
        return {}
    wanted: dict[str, int] = {}
    for chapter in chapters:
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id:
            continue
        available = len(chapter.get("media") or [])
        weight = PHOTO_WEIGHTS.get(chapter.get("importance"), PHOTO_WEIGHTS[IMPORTANCE_NORMAL])
        wanted[chapter_id] = max(0, min(available, weight, per_chapter_cap))

    total = sum(wanted.values())
    if total <= total_budget:
        return wanted

    # Over budget: take from the least important days first, and never
    # take a day's last picture while a richer day still has spares.
    order = sorted(
        wanted,
        key=lambda chapter_id: (
            _IMPORTANCE_ORDER.index(
                _importance_of(chapters, chapter_id)
            ),
            chapter_id,
        ),
    )
    while total > total_budget:
        moved = False
        for chapter_id in order:
            if total <= total_budget:
                break
            if wanted[chapter_id] > 1:
                wanted[chapter_id] -= 1
                total -= 1
                moved = True
        if not moved:
            # Everybody is down to one picture and it is still too many.
            for chapter_id in order:
                if total <= total_budget:
                    break
                if wanted[chapter_id] > 0:
                    wanted[chapter_id] -= 1
                    total -= 1
            break
    return wanted


def _importance_of(chapters: list[dict[str, Any]], chapter_id: str) -> str:
    for chapter in chapters:
        if str(chapter.get("chapter_id") or "") == chapter_id:
            value = chapter.get("importance")
            return value if value in _IMPORTANCE_ORDER else IMPORTANCE_NORMAL
    return IMPORTANCE_NORMAL


# --- the plan ------------------------------------------------------------


def _importance(chapter: dict[str, Any]) -> str:
    value = chapter.get("importance")
    return value if value in _IMPORTANCE_ORDER else IMPORTANCE_NORMAL


def _enter(chapter: dict[str, Any]) -> str:
    return _ENTER_BY_ROLE.get(str(chapter.get("story_role") or ""), DEFAULT_ENTER)


def _caption(chapter: dict[str, Any]) -> str:
    """The film's text. The caption first, the story only as a fallback.

    A card is on screen for three seconds. The long text is right for an
    album and unreadable here, so it is used only when the editor never
    wrote a short one.
    """
    value = chapter.get("story")
    if isinstance(value, str):
        # A package chapter: the choice between caption and story was
        # already made when the package was built.
        return value.strip()
    caption = str(chapter.get("video_caption") or "").strip()
    if caption:
        return caption
    return str((value or {}).get("text") or "").strip()


def _chapter_scenes(
    chapter: dict[str, Any], *, photo_count: int, index: int
) -> list[dict[str, Any]]:
    """The shots for one day. Style picks the shape, importance the size."""
    importance = _importance(chapter)
    style = str(chapter.get("visual_style") or "normal")
    enter = _enter(chapter)
    chapter_id = str(chapter.get("chapter_id") or "")
    caption = _caption(chapter)

    scenes: list[dict[str, Any]] = [
        {
            "type": SCENE_CHAPTER_CARD,
            "chapter_id": chapter_id,
            "chapter_index": index,
            "frames": _CARD_FRAMES[importance],
            "enter": enter,
            "photos": [],
        }
    ]

    def photo_scene(kind: str, indices: list[int], frames: int) -> dict[str, Any]:
        return {
            "type": kind,
            "chapter_id": chapter_id,
            "chapter_index": index,
            "frames": frames,
            "enter": enter,
            "photos": indices,
        }

    if photo_count <= 0:
        # Not "keine Fotos vorhanden" on screen - that is a diagnostic and
        # belongs in a log. The day gets a written page instead, which is
        # also what makes a photo-less last day flow into the outro.
        scenes.append(photo_scene(SCENE_TEXT, [], _TEXT_FRAMES[importance]))
        return scenes

    remaining = list(range(photo_count))
    if style == "collage" and photo_count >= 2:
        scenes.append(photo_scene(SCENE_COLLAGE, remaining, _COLLAGE_FRAMES[importance]))
        return scenes
    # map_focus has no map yet. Rather than a placeholder nobody asked
    # for, it becomes the strongest single-image form available - and
    # says so in the plan, so the substitution is visible rather than
    # silently identical to "hero".
    if style in {"hero", "map_focus"} or (
        style == "collage" and photo_count == 1
    ):
        scenes.append(photo_scene(SCENE_HERO, [remaining[0]], _HERO_FRAMES[importance]))
        remaining = remaining[1:]
    if style == "compact":
        # One picture, one line, move on.
        if remaining:
            scenes.append(
                photo_scene(SCENE_PHOTO, [remaining[0]], _PHOTO_FRAMES[importance])
            )
        return scenes
    for position in remaining:
        scenes.append(photo_scene(SCENE_PHOTO, [position], _PHOTO_FRAMES[importance]))

    if not caption:
        # Nothing to read anywhere in this chapter: the card carries it.
        scenes[0]["frames"] += 15
    return scenes


def build_scene_plan(
    *,
    trip: dict[str, Any],
    chapters: list[dict[str, Any]],
    narrative: dict[str, Any] | None = None,
    outro_photos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The whole film as an ordered list of scenes with frame counts.

    ``chapters`` are the package's chapters - each already carrying the
    pictures that travelled with it - so the plan can address photos by
    position and the renderer never has to decide anything.
    """
    if not chapters:
        raise FilmPlanError("Ein Film ohne Kapitel kann nicht geplant werden")

    arc = narrative if isinstance(narrative, dict) else {}
    scenes: list[dict[str, Any]] = [
        {
            "type": SCENE_INTRO,
            "chapter_id": "",
            "chapter_index": -1,
            # An opening sentence needs longer than a title.
            "frames": INTRO_FRAMES + (30 if str(arc.get("opening") or "").strip() else 0),
            "enter": "rise",
            "photos": [],
        }
    ]
    for index, chapter in enumerate(chapters):
        scenes.extend(
            _chapter_scenes(
                chapter,
                photo_count=len(chapter.get("images") or []),
                index=index,
            )
        )

    scenes.append(
        {
            "type": SCENE_OUTRO,
            "chapter_id": "",
            "chapter_index": -1,
            "frames": OUTRO_FRAMES + (30 if str(arc.get("closing") or "").strip() else 0),
            "enter": "settle",
            "photos": [],
        }
    )
    final = list(outro_photos or [])[:OUTRO_COLLAGE_PHOTOS]
    if len(final) >= 2:
        # A film that stops after the last day has not ended, it has run
        # out. The closing collage is the difference.
        scenes.append(
            {
                "type": SCENE_OUTRO_COLLAGE,
                "chapter_id": "",
                "chapter_index": -1,
                "frames": OUTRO_COLLAGE_FRAMES,
                "enter": "fade",
                "photos": [],
                "paths": [str(item.get("path") or "") for item in final if item.get("path")],
            }
        )

    return {
        "plan_version": PLAN_VERSION,
        "fps": FILM_FPS,
        "total_frames": sum(int(scene["frames"]) for scene in scenes),
        "scenes": scenes,
    }


def plan_seconds(plan: dict[str, Any]) -> float:
    return round(int(plan.get("total_frames") or 0) / FILM_FPS, 2)


def validate_scene_plan(plan: Any) -> dict[str, Any]:
    """Read a plan with the rules that produced it."""
    if not isinstance(plan, dict):
        raise FilmPlanError("Szenenplan ist kein Objekt")
    if plan.get("plan_version") != PLAN_VERSION:
        raise FilmPlanError(f"Unbekannte Planversion: {plan.get('plan_version')!r}")
    if plan.get("fps") != FILM_FPS:
        raise FilmPlanError("Der Szenenplan hat eine fremde Bildrate")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise FilmPlanError("Szenenplan ohne Szenen")
    total = 0
    for scene in scenes:
        if not isinstance(scene, dict):
            raise FilmPlanError("Szene ist kein Objekt")
        if scene.get("type") not in SCENE_TYPES:
            raise FilmPlanError(f"Unbekannter Szenentyp: {scene.get('type')!r}")
        frames = scene.get("frames")
        if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
            raise FilmPlanError("Szene ohne gültige Länge")
        total += frames
    if total != plan.get("total_frames"):
        raise FilmPlanError("Die Gesamtlänge passt nicht zu den Szenen")
    return plan


__all__ = [
    "FILM_FPS",
    "OUTRO_COLLAGE_PHOTOS",
    "PLAN_VERSION",
    "PHOTO_WEIGHTS",
    "SCENE_TYPES",
    "FilmPlanError",
    "allocate_photos",
    "build_scene_plan",
    "plan_seconds",
    "readable_place",
    "readable_places",
    "validate_scene_plan",
]
