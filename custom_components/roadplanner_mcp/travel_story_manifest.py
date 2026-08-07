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

MANIFEST_VERSION = 1
SCHEMA_ID = "roadplanner.travel_story"

# The register the composed sentences aim for. Recorded in the manifest so
# a later renderer - or a later, different composer - can tell what the
# text was written to sound like.
TONE = "warm-personal-playful"

STORY_FROM_OVERRIDE = "override"
STORY_FROM_STORED = "stored"
STORY_FROM_COMPOSED = "composed"
STORY_SOURCES = (STORY_FROM_OVERRIDE, STORY_FROM_STORED, STORY_FROM_COMPOSED)

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
MAX_MEDIA_PER_CHAPTER = 12
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
) -> dict[str, Any]:
    """Pick the story text and record where it came from.

    The order is a statement of trust: what a person wrote beats what the
    summary service generated, and both beat what this module assembled.
    """
    typed_override = clean_story(override)
    if typed_override:
        return {"text": typed_override, "source": STORY_FROM_OVERRIDE, "tone": TONE}
    stored = clean_story(stored_summary)
    if stored:
        return {"text": stored, "source": STORY_FROM_STORED, "tone": TONE}
    return {
        "text": compose_story(facts, index=index, title=title, stop_names=stop_names),
        "source": STORY_FROM_COMPOSED,
        "tone": TONE,
    }


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
) -> dict[str, Any]:
    """One day, described. Ids stay ids; nothing is resolved or fetched."""
    chapter_id = clean_line(day_id, limit=200)
    if not chapter_id:
        raise StoryManifestError("Ein Kapitel ohne Tages-ID kann nicht gebaut werden")
    override = overrides or {}

    clean_stops = [
        {
            "stop_id": clean_line(stop.get("stop_id"), limit=200),
            "name": clean_line(stop.get("name"), limit=MAX_TITLE_LENGTH),
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

    title_override = clean_line(override.get("title"), limit=MAX_TITLE_LENGTH)
    resolved_title = title_override or clean_line(title, limit=MAX_TITLE_LENGTH)

    return {
        "chapter_id": chapter_id,
        "index": index,
        "date": clean_line(date, limit=40),
        "title": resolved_title,
        "title_overridden": bool(title_override),
        "story": build_story(
            facts=resolved_facts,
            index=index,
            title=resolved_title,
            stop_names=[stop["name"] for stop in clean_stops],
            stored_summary=stored_summary,
            override=str(override.get("story") or ""),
        ),
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
        "chapters": ordered,
    }
    manifest["content_hash"] = content_hash(manifest)
    return manifest


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
_UNHASHED_KEYS = frozenset({"content_hash", "source_revision"})


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
    "STORY_FROM_OVERRIDE",
    "STORY_FROM_STORED",
    "STORY_SOURCES",
    "TONE",
    "StoryManifestError",
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
