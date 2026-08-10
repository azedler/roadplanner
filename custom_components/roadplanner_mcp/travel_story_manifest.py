"""TravelStoryManifest v1 - one described trip, in a form both exporters can read.

Today the PDF export and the video export each dig through the roadbook on
their own and each decide separately what a day *is about*. They drift,
and they have drifted: a stop name with an internal id in it reached the
PDF, and photo selection had to be pulled into a shared module after the
two had already diverged. This is the structure that stops the same thing
happening to the narrative layer.

What the manifest is
--------------------

A **description**, not a rendering. It says what a trip and its days are,
in stable ids and facts, and it says nothing about pages, frames, fonts or
resolutions. A PDF and a video built from the same manifest tell the same
story; that is the whole point of having one.

Four properties carry it, and each is a rule the code enforces rather than
a hope:

**Only stable ids and real facts.** Every reference is an id that exists in
Roadplanner - a day id, a stop id, a media id. No URLs, no bytes, no
coordinates, no derived numbers that Roadplanner does not already hold. A
distance appears only if the roadbook already has one; nothing is looked up
or estimated to fill a field. A manifest that invented a number would be
worse than one missing it, because the number would be believed.

**Deterministic.** The same inputs produce byte-identical output. There is
no clock in here, no random choice, and every list has a defined order. That
is what makes ``content_hash`` meaningful and the whole thing cacheable: if
the hash is unchanged, nothing downstream needs to be rebuilt.

**Nothing is invented in the prose either.** A chapter's story text is
either a summary a human or the summary service already wrote, or an
override someone typed, or a sentence assembled from the facts listed in
that same chapter. ``story.source`` says which, always. There is no
generator here that can produce a fact the chapter does not contain - the
tone is in the phrasing, never in the content.

**Overrides are part of the model, not a patch on top.** Any chapter title
or story may be replaced by hand, and the manifest records that it was.
Where those overrides are stored is the caller's business; this module only
knows how to apply them.

What the manifest deliberately is not
-------------------------------------

Not a layout, not a shot list, not a script. It carries *hints* - which
media belong to a chapter, whether a map could be drawn, what a caption
would say - and leaves every decision about how to show them to whoever
renders it. Adding a frame count or a page size here would make the next
exporter fight the manifest instead of using it.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .film_photo_allocation import PHOTO_CAPS_BY_IMPORTANCE

MANIFEST_VERSION = 2
SCHEMA_ID = "roadplanner.travel_story"

# The register the composed sentences aim for. Recorded in the manifest so
# a later renderer - or a later, different composer - can tell what the
# text was written to sound like.
TONE = "warm-personal-playful"

STORY_FROM_OVERRIDE = "override"
# What the Gemini story director edited. Deliberately its own source and
# never written into the override fields: a reader has to be able to tell
# at a glance whether a sentence was written by a person or by a model,
# and a machine text stored where a human one belongs makes that question
# permanently unanswerable.
STORY_FROM_DIRECTED = "directed"
STORY_FROM_STORED = "stored"
STORY_FROM_COMPOSED = "composed"
STORY_SOURCES = (
    STORY_FROM_OVERRIDE,
    STORY_FROM_DIRECTED,
    STORY_FROM_STORED,
    STORY_FROM_COMPOSED,
)

# How much a day matters to the trip as a whole. Four steps because three
# could not separate "a good day" from "the reason we went".
IMPORTANCE_TRANSITION = "transition"
IMPORTANCE_NORMAL = "normal"
IMPORTANCE_HIGHLIGHT = "highlight"
IMPORTANCE_MAJOR_HIGHLIGHT = "major_highlight"
IMPORTANCE_LEVELS = (
    IMPORTANCE_TRANSITION,
    IMPORTANCE_NORMAL,
    IMPORTANCE_HIGHLIGHT,
    IMPORTANCE_MAJOR_HIGHLIGHT,
)
DEFAULT_IMPORTANCE = IMPORTANCE_NORMAL

# Where a day sits in the arc. Importance says how much it matters, this
# says what it does - and they are not the same: a quiet transfer can be
# the hinge of a trip.
ROLE_OPENING = "opening"
ROLE_JOURNEY = "journey"
ROLE_TRANSITION = "transition"
ROLE_HIGHLIGHT = "highlight"
ROLE_FINALE = "finale"
STORY_ROLES = (ROLE_OPENING, ROLE_JOURNEY, ROLE_TRANSITION, ROLE_HIGHLIGHT, ROLE_FINALE)
DEFAULT_STORY_ROLE = ROLE_JOURNEY

# A suggestion about weight, not a layout. A renderer that has no collage
# may read "collage" as "this day has a lot to show" and do its own thing;
# one that ignores the field entirely still produces a correct film.
VISUAL_COMPACT = "compact"
VISUAL_NORMAL = "normal"
VISUAL_HERO = "hero"
VISUAL_COLLAGE = "collage"
VISUAL_MAP_FOCUS = "map_focus"
VISUAL_STYLES = (VISUAL_COMPACT, VISUAL_NORMAL, VISUAL_HERO, VISUAL_COLLAGE, VISUAL_MAP_FOCUS)
DEFAULT_VISUAL_STYLE = VISUAL_NORMAL

MAX_VIDEO_CAPTION_LENGTH = 180
MAX_MOTIFS = 5
MAX_MOTIF_LENGTH = 80

MEDIA_ROLE_TRIP_COVER = "trip_cover"
MEDIA_ROLE_DAY_COVER = "day_cover"
MEDIA_ROLE_STOP_COVER = "stop_cover"
MEDIA_ROLE_HIGHLIGHT = "highlight"
MEDIA_ROLES = (
    MEDIA_ROLE_TRIP_COVER,
    MEDIA_ROLE_DAY_COVER,
    MEDIA_ROLE_STOP_COVER,
    MEDIA_ROLE_HIGHLIGHT,
)

MAX_TITLE_LENGTH = 120
MAX_STORY_LENGTH = 1200
MAX_CAPTION_LENGTH = 200
MAX_STOPS_PER_CHAPTER = 24
# The most pictures a chapter may carry. Derived rather than typed: the
# film's own ceiling for its most important day is 18, and a schema that
# said 16 would silently drop the last two of a major highlight - one
# number living in two files with only one of them raised is the mistake
# this project has now made four times. A test compares them.
MAX_MEDIA_PER_CHAPTER = max(PHOTO_CAPS_BY_IMPORTANCE.values())
MAX_CAPTIONS_PER_CHAPTER = 12

_WHITESPACE_RE = re.compile(r"\s+")
_SPACES_RE = re.compile(r"[^\S\n]+")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StoryManifestError(ValueError):
    """The manifest cannot be built or is not one we can read."""


# --- text ---------------------------------------------------------------


def clean_line(value: Any, *, limit: int) -> str:
    """One bounded line. Control characters become spaces, never boxes."""
    text = _WHITESPACE_RE.sub(" ", str(value or "").replace("\x00", " ")).strip()
    return text[:limit]


def clean_story(value: Any, *, limit: int = MAX_STORY_LENGTH) -> str:
    """Bounded prose that may have paragraphs.

    A title is one line; a story someone writes by hand is not. Runs of
    spaces still collapse and control characters still go, but a blank line
    between two paragraphs survives, because a person who typed one meant
    it. Three or more blank lines collapse to one - that is formatting by
    accident, not by intent.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        character if character == "\n" or character.isprintable() else " "
        for character in text
    )
    lines = [_SPACES_RE.sub(" ", line).strip() for line in text.split("\n")]
    collapsed: list[str] = []
    for line in lines:
        if not line and collapsed and not collapsed[-1]:
            continue
        collapsed.append(line)
    return "\n".join(collapsed).strip()[:limit]


def _optional_number(value: Any, *, digits: int = 1) -> float | None:
    """A number Roadplanner already has, or nothing.

    Zero is treated as absent on purpose: a day with ``distance_km = 0``
    means "not recorded", and printing "0 km" would state something the
    roadbook never claimed.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number, digits)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None


# --- the composed story -------------------------------------------------


def _format_duration(minutes: float) -> str:
    hours, rest = divmod(int(round(minutes)), 60)
    if not hours:
        return f"{rest} Minuten"
    if not rest:
        return "eine Stunde" if hours == 1 else f"{hours} Stunden"
    return f"{hours} Stunden {rest} Minuten"


def _join_names(names: list[str]) -> str:
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} und {names[-1]}"


# Openings vary so twenty-one chapters do not read like a table, and they
# vary by the chapter's own index so the variation is reproducible. Every
# one of them states only the day number and the title.
_OPENINGS = (
    "Tag {number} führt nach {title}.",
    "Tag {number}: {title}.",
    "An Tag {number} geht es nach {title}.",
    "{title} steht an Tag {number} auf dem Plan.",
)
_OPENINGS_WITHOUT_TITLE = (
    "Tag {number}.",
    "Tag {number} unterwegs.",
)


def compose_story(facts: dict[str, Any], *, index: int, title: str, stop_names: list[str]) -> str:
    """Assemble a short chapter text from the facts of that chapter.

    Every sentence is derived from a value that is present; a fact that is
    missing produces no sentence rather than a hedge. Nothing here can
    output a word about weather, mood or scenery, because none of those are
    inputs - the warmth has to come from the phrasing, and the phrasing is
    all this function is allowed to choose.
    """
    number = facts.get("day_number") or index + 1
    clean_title = clean_line(title, limit=MAX_TITLE_LENGTH)
    if clean_title:
        opening = _OPENINGS[index % len(_OPENINGS)].format(number=number, title=clean_title)
    else:
        opening = _OPENINGS_WITHOUT_TITLE[index % len(_OPENINGS_WITHOUT_TITLE)].format(
            number=number
        )
    sentences = [opening]

    distance = facts.get("distance_km")
    duration = facts.get("duration_minutes")
    if distance and duration:
        sentences.append(
            f"{round(distance)} Kilometer und {_format_duration(duration)} liegen dazwischen."
        )
    elif distance:
        sentences.append(f"{round(distance)} Kilometer liegen dazwischen.")
    elif duration:
        sentences.append(f"{_format_duration(duration)} Fahrt liegen dazwischen.")

    shown = [name for name in stop_names if name][:3]
    if shown:
        rest = len([name for name in stop_names if name]) - len(shown)
        listed = _join_names(shown)
        if rest > 0:
            sentences.append(f"Unterwegs: {listed} und {rest} weitere.")
        else:
            sentences.append(f"Unterwegs: {listed}.")

    photos = facts.get("photo_count")
    if photos:
        sentences.append(
            "Ein Foto ist an diesem Tag entstanden."
            if photos == 1
            else f"{photos} Fotos sind an diesem Tag entstanden."
        )

    return clean_line(" ".join(sentences), limit=MAX_STORY_LENGTH)


def build_story(
    *,
    facts: dict[str, Any],
    index: int,
    title: str,
    stop_names: list[str],
    stored_summary: str = "",
    override: str = "",
    directed: str = "",
) -> dict[str, Any]:
    """Pick the story text and record where it came from.

    The order is a statement of trust, and it is the whole reason this
    function exists rather than four ``if`` blocks scattered around the
    codebase: what a person wrote beats what the model edited, which beats
    what the summary service once generated, which beats what this module
    assembled out of numbers.

    The model sits below the person and above the machinery on purpose. It
    can write better prose than a template, and it must never quietly
    replace a sentence somebody chose.
    """
    typed_override = clean_story(override)
    if typed_override:
        return {"text": typed_override, "source": STORY_FROM_OVERRIDE, "tone": TONE}
    edited = clean_story(directed)
    if edited:
        return {"text": edited, "source": STORY_FROM_DIRECTED, "tone": TONE}
    stored = clean_story(stored_summary)
    if stored:
        return {"text": stored, "source": STORY_FROM_STORED, "tone": TONE}
    return {
        "text": compose_story(facts, index=index, title=title, stop_names=stop_names),
        "source": STORY_FROM_COMPOSED,
        "tone": TONE,
    }


def _one_of(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    """An enum value, or the fallback. Never something a caller invented."""
    text = clean_line(value, limit=40)
    return text if text in allowed else fallback


# --- chapters -----------------------------------------------------------


def build_chapter(
    *,
    day_id: str,
    index: int,
    date: str,
    title: str,
    stops: list[dict[str, Any]],
    media: list[dict[str, Any]],
    facts: dict[str, Any] | None = None,
    stored_summary: str = "",
    overrides: dict[str, Any] | None = None,
    captions: list[dict[str, Any]] | None = None,
    map_hint: dict[str, Any] | None = None,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One day, described. Ids stay ids; nothing is resolved or fetched."""
    chapter_id = clean_line(day_id, limit=200)
    if not chapter_id:
        raise StoryManifestError("Ein Kapitel ohne Tages-ID kann nicht gebaut werden")
    override = overrides or {}

    edit = direction or {}
    # A readable name for a stop whose canonical name is a full address or
    # a Park4Night code. It sits BESIDE the canonical name and never
    # replaces it: the roadbook keeps the name you can navigate to, the
    # story gets the name you would say out loud.
    story_names = {
        clean_line(key, limit=200): clean_line(value, limit=MAX_TITLE_LENGTH)
        for key, value in (edit.get("stop_story_names") or {}).items()
        if clean_line(key, limit=200) and clean_line(value, limit=MAX_TITLE_LENGTH)
    }
    clean_stops = [
        {
            "stop_id": clean_line(stop.get("stop_id"), limit=200),
            "name": clean_line(stop.get("name"), limit=MAX_TITLE_LENGTH),
            "story_name": story_names.get(clean_line(stop.get("stop_id"), limit=200)) or None,
            "kind": clean_line(stop.get("kind"), limit=40),
            "arrival_time": clean_line(stop.get("arrival_time"), limit=10),
        }
        for stop in stops[:MAX_STOPS_PER_CHAPTER]
        if isinstance(stop, dict) and clean_line(stop.get("stop_id"), limit=200)
    ]
    clean_media = [
        {
            "media_id": clean_line(item.get("media_id"), limit=200),
            "role": item.get("role") if item.get("role") in MEDIA_ROLES else MEDIA_ROLE_HIGHLIGHT,
            "stop_id": clean_line(item.get("stop_id"), limit=200) or None,
        }
        for item in media[:MAX_MEDIA_PER_CHAPTER]
        if isinstance(item, dict) and clean_line(item.get("media_id"), limit=200)
    ]

    given = facts or {}
    resolved_facts = {
        "day_number": _optional_int(given.get("day_number")) or index + 1,
        "distance_km": _optional_number(given.get("distance_km")),
        "duration_minutes": _optional_number(given.get("duration_minutes"), digits=0),
        "stop_count": len(clean_stops),
        # The number of photos the day HAS, which is not the number carried
        # in this chapter - a renderer needs both and they are different
        # facts.
        "photo_count": _optional_int(given.get("photo_count")) or 0,
    }

    base_title = clean_line(title, limit=MAX_TITLE_LENGTH)
    base_stored = clean_story(stored_summary)
    title_override = clean_line(override.get("title"), limit=MAX_TITLE_LENGTH)
    directed_title = clean_line(edit.get("title"), limit=MAX_TITLE_LENGTH)
    resolved_title = title_override or directed_title or base_title
    story = build_story(
        facts=resolved_facts,
        index=index,
        title=resolved_title,
        stop_names=[stop["story_name"] or stop["name"] for stop in clean_stops],
        stored_summary=stored_summary,
        override=str(override.get("story") or ""),
        directed=str(edit.get("story") or ""),
    )

    return {
        "chapter_id": chapter_id,
        "index": index,
        "date": clean_line(date, limit=40),
        "title": resolved_title,
        "title_overridden": bool(title_override),
        "title_source": STORY_FROM_OVERRIDE
        if title_override
        else STORY_FROM_DIRECTED
        if directed_title
        else STORY_FROM_COMPOSED,
        "story": story,
        # What the chapter is without anyone's edits. It is here so that
        # "reset to the automatic version" can be shown before it is
        # pressed, and - more importantly - so the hash that decides
        # whether the director has to run again can be computed from
        # material that an override does not move. Without it, typing a
        # title would invalidate the cache and cost money.
        "base": {"title": base_title, "stored_summary": base_stored},
        # The film's version of the story: one or two sentences that fit on
        # a card. A long text is right for an album and wrong on screen,
        # and shortening prose mechanically produces neither.
        "video_caption": clean_line(
            edit.get("video_caption"), limit=MAX_VIDEO_CAPTION_LENGTH
        ),
        "importance": _one_of(edit.get("importance"), IMPORTANCE_LEVELS, DEFAULT_IMPORTANCE),
        "story_role": _one_of(edit.get("story_role"), STORY_ROLES, DEFAULT_STORY_ROLE),
        "visual_style": _one_of(edit.get("visual_style"), VISUAL_STYLES, DEFAULT_VISUAL_STYLE),
        "facts": resolved_facts,
        "stops": clean_stops,
        "media": clean_media,
        "captions": [
            {
                "media_id": clean_line(caption.get("media_id"), limit=200),
                "text": clean_line(caption.get("text"), limit=MAX_CAPTION_LENGTH),
            }
            for caption in (captions or [])[:MAX_CAPTIONS_PER_CHAPTER]
            if isinstance(caption, dict)
            and clean_line(caption.get("media_id"), limit=200)
            and clean_line(caption.get("text"), limit=MAX_CAPTION_LENGTH)
        ],
        # A hint, not a map. It says a map COULD be drawn and from which
        # stops - drawing one is a later decision and a different module.
        "map_hint": None if map_hint is None else {
            "kind": clean_line(map_hint.get("kind"), limit=40) or "day_route",
            "stop_ids": [
                clean_line(stop_id, limit=200)
                for stop_id in (map_hint.get("stop_ids") or [])[:MAX_STOPS_PER_CHAPTER]
                if clean_line(stop_id, limit=200)
            ],
            "has_coordinates": bool(map_hint.get("has_coordinates")),
        },
    }


# --- manifest -----------------------------------------------------------


def build_manifest(
    *,
    trip_id: str,
    title: str,
    start_date: str = "",
    end_date: str = "",
    revision: Any = None,
    chapters: list[dict[str, Any]],
    trip_facts: dict[str, Any] | None = None,
    trip_cover_media_id: str = "",
    narrative: dict[str, Any] | None = None,
    crew: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the manifest and stamp it with the hash of its content.

    ``source_revision`` is what makes caching safe: it is the roadbook
    revision the description was taken from, so a cached manifest can be
    discarded the moment the trip moves on. The hash then says whether the
    description actually changed, which is the cheaper question.
    """
    identifier = clean_line(trip_id, limit=200)
    if not identifier:
        raise StoryManifestError("Ein Manifest ohne Reise-ID kann nicht gebaut werden")
    ordered = sorted(chapters, key=lambda chapter: (chapter.get("index", 0), chapter.get("chapter_id", "")))
    facts = trip_facts or {}
    manifest = {
        "schema": SCHEMA_ID,
        "manifest_version": MANIFEST_VERSION,
        "trip": {
            "trip_id": identifier,
            "title": clean_line(title, limit=MAX_TITLE_LENGTH),
            "start_date": clean_line(start_date, limit=40),
            "end_date": clean_line(end_date, limit=40),
            "cover_media_id": clean_line(trip_cover_media_id, limit=200) or None,
        },
        "source_revision": revision if isinstance(revision, int) and not isinstance(revision, bool) else None,
        "facts": {
            "chapter_count": len(ordered),
            "stop_count": sum(len(chapter.get("stops") or []) for chapter in ordered),
            "media_count": sum(len(chapter.get("media") or []) for chapter in ordered),
            "photo_count": sum(
                int((chapter.get("facts") or {}).get("photo_count") or 0) for chapter in ordered
            ),
            "distance_km": _optional_number(facts.get("distance_km")),
        },
        "story_sources": _story_source_counts(ordered),
        # The arc, when somebody has written one. Absent rather than empty
        # when nobody has: a renderer must be able to tell "no arc" from
        # "an arc that says nothing".
        "narrative": _narrative(narrative),
        # Who the trip was with, in the smallest form that makes a story
        # personal: display names and a vehicle name. No notes, no
        # portraits, no summaries, no ids that lead back to a person's
        # record - a story does not need them and a story context that
        # carries them is a person's file leaving the house.
        "crew": _crew(crew),
        "chapters": ordered,
    }
    manifest["content_hash"] = content_hash(manifest)
    # Deliberately computed AFTER the content hash and kept separate from
    # it. They answer different questions: the content hash asks "did this
    # description change?", the context hash asks "does the editor have to
    # run again?" - and the second must stay still while somebody types a
    # title, or every edit would cost a Gemini call.
    manifest["story_context_hash"] = story_context_hash(manifest)
    return manifest


def _narrative(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """The trip-level arc, bounded and normalised, or nothing."""
    if not isinstance(value, dict):
        return None
    motifs = [
        clean_line(motif, limit=MAX_MOTIF_LENGTH)
        for motif in (value.get("motifs") or [])[:MAX_MOTIFS]
        if clean_line(motif, limit=MAX_MOTIF_LENGTH)
    ]
    narrative = {
        "title_variant": clean_line(value.get("title_variant"), limit=MAX_TITLE_LENGTH),
        "subtitle": clean_line(value.get("subtitle"), limit=MAX_TITLE_LENGTH),
        "opening": clean_story(value.get("opening")),
        "closing": clean_story(value.get("closing")),
        "motifs": motifs,
        "source": STORY_FROM_DIRECTED,
    }
    if not any(
        (narrative["title_variant"], narrative["subtitle"], narrative["opening"],
         narrative["closing"], narrative["motifs"])
    ):
        return None
    return narrative


def _crew(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Names only. See the comment where this is called."""
    if not isinstance(value, dict):
        return None
    people = [
        clean_line(name, limit=MAX_TITLE_LENGTH)
        for name in (value.get("people") or [])[:12]
        if clean_line(name, limit=MAX_TITLE_LENGTH)
    ]
    vehicle = clean_line(value.get("vehicle"), limit=MAX_TITLE_LENGTH)
    if not people and not vehicle:
        return None
    return {"people": people, "vehicle": vehicle or None}


def _story_source_counts(chapters: list[dict[str, Any]]) -> dict[str, int]:
    """How much of this manifest is written, generated or assembled.

    Worth having at the top level: it answers "how much of this trip has
    somebody actually described?" without walking every chapter, and it is
    the number that will show whether a story editor is being used.
    """
    counts = {source: 0 for source in STORY_SOURCES}
    for chapter in chapters:
        source = (chapter.get("story") or {}).get("source")
        if source in counts:
            counts[source] += 1
    return counts


# Neither of these is part of the description. The hash is the answer, so
# it cannot be part of the question - and ``source_revision`` is
# provenance: it says which roadbook state the description was taken from,
# not what the description says. Including it would make the hash change
# on every unrelated trip edit, and the one question the hash exists to
# answer - "did the story actually change?" - would become unanswerable.
_UNHASHED_KEYS = frozenset({"content_hash", "source_revision", "story_context_hash"})


def story_context_hash(manifest: dict[str, Any]) -> str:
    """What the editor would be looking at, hashed.

    This is the cache key for the story director, and everything about it
    follows from one requirement: **typing must not cost money**. So it is
    computed from the material an editor works FROM - dates, canonical
    titles, stops, facts, which photos exist, what the summary service
    once wrote - and never from anything an editor produces. A human
    override does not move it. The director's own output does not move it,
    which is what stops the second run from invalidating the first.

    It is also not the content hash. The content hash changes whenever the
    description changes, including when somebody types one character;
    using it here would rebuild the whole trip's prose on every keystroke.
    Two hashes because there are genuinely two questions.
    """
    trip = manifest.get("trip") or {}
    projection = {
        "trip": {
            "title": trip.get("title") or "",
            "start_date": trip.get("start_date") or "",
            "end_date": trip.get("end_date") or "",
        },
        "facts": manifest.get("facts") or {},
        "crew": manifest.get("crew"),
        "chapters": [
            {
                "chapter_id": chapter.get("chapter_id") or "",
                "index": chapter.get("index"),
                "date": chapter.get("date") or "",
                # The canonical title and the stored summary, NOT the
                # resolved ones - those carry the edits.
                "title": (chapter.get("base") or {}).get("title") or "",
                "stored_summary": (chapter.get("base") or {}).get("stored_summary") or "",
                "facts": chapter.get("facts") or {},
                "stops": [
                    {
                        "stop_id": stop.get("stop_id") or "",
                        "name": stop.get("name") or "",
                        "kind": stop.get("kind") or "",
                    }
                    for stop in chapter.get("stops") or []
                ],
                "media": [item.get("media_id") or "" for item in chapter.get("media") or []],
            }
            for chapter in manifest.get("chapters") or []
        ],
    }
    payload = json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_json(payload: dict[str, Any]) -> str:
    """One spelling of a manifest, so two of them can be compared as text."""
    return json.dumps(
        {key: value for key, value in payload.items() if key not in _UNHASHED_KEYS},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def content_hash(payload: dict[str, Any]) -> str:
    """The hash of everything except the hash itself."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_manifest(payload: Any) -> dict[str, Any]:
    """Read a manifest with the rules that produced it.

    The hash is checked here, which is the point of carrying it: a manifest
    that was edited after it was built no longer describes the trip it
    claims to, and a renderer must not quietly use it.
    """
    if not isinstance(payload, dict):
        raise StoryManifestError("Manifest ist kein Objekt")
    if payload.get("schema") != SCHEMA_ID:
        raise StoryManifestError("Fremdes Schema")
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise StoryManifestError(
            f"Nicht unterstützte Manifestversion: {payload.get('manifest_version')!r}"
        )
    trip = payload.get("trip")
    if not isinstance(trip, dict) or not clean_line(trip.get("trip_id"), limit=200):
        raise StoryManifestError("Manifest ohne Reise-ID")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        raise StoryManifestError("Manifest ohne Kapitelliste")
    seen: set[str] = set()
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise StoryManifestError("Kapitel ist kein Objekt")
        chapter_id = clean_line(chapter.get("chapter_id"), limit=200)
        if not chapter_id:
            raise StoryManifestError("Kapitel ohne Kennung")
        if chapter_id in seen:
            raise StoryManifestError(f"Doppeltes Kapitel: {chapter_id}")
        seen.add(chapter_id)
        story = chapter.get("story")
        if not isinstance(story, dict) or story.get("source") not in STORY_SOURCES:
            raise StoryManifestError(f"Kapitel {chapter_id} ohne gültige Story-Herkunft")
    digest = str(payload.get("content_hash") or "")
    if not _SHA256_RE.match(digest):
        raise StoryManifestError("Manifest ohne gültigen Inhalts-Hash")
    if digest != content_hash(payload):
        raise StoryManifestError("Der Inhalts-Hash passt nicht zum Inhalt")
    return payload


__all__ = [
    "MANIFEST_VERSION",
    "MAX_STORY_LENGTH",
    "MEDIA_ROLES",
    "MEDIA_ROLE_DAY_COVER",
    "MEDIA_ROLE_HIGHLIGHT",
    "MEDIA_ROLE_STOP_COVER",
    "MEDIA_ROLE_TRIP_COVER",
    "SCHEMA_ID",
    "STORY_FROM_COMPOSED",
    "STORY_FROM_DIRECTED",
    "STORY_FROM_OVERRIDE",
    "STORY_FROM_STORED",
    "STORY_ROLES",
    "STORY_SOURCES",
    "IMPORTANCE_LEVELS",
    "VISUAL_STYLES",
    "MAX_VIDEO_CAPTION_LENGTH",
    "TONE",
    "StoryManifestError",
    "story_context_hash",
    "build_chapter",
    "build_manifest",
    "build_story",
    "canonical_json",
    "clean_line",
    "clean_story",
    "compose_story",
    "content_hash",
    "validate_manifest",
]
