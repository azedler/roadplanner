"""The editor's desk: what Gemini is asked, and what is allowed back.

Film v0 proved the machinery and exposed the real limit. The manifest can
carry a whole trip and Remotion can render it - but the sentences read
like a database report, because that is what they are. "31 Fotos sind an
diesem Tag entstanden" is true, and nobody has ever said it about their
own holiday.

This module is the editing pass. It is pure: it builds the text that goes
to the model, declares the shape of the answer, and decides what of that
answer is allowed into the manifest. It calls nothing, stores nothing and
imports nothing from Home Assistant, so every rule below can be tested
without a network.

Three properties do the work.

**The model edits, it does not report.** Everything it is told comes from
the manifest, and the manifest only ever contains what Roadplanner
already knows. There is no search, no world knowledge, no second source.
A sentence about the weather cannot be grounded in anything it was given,
so the prompt forbids it and the merge below cannot repair it - which is
why the prompt has to be right rather than the filter clever.

**The trip is edited as a trip.** Twenty-three separate calls would
produce twenty-three separate texts, each convinced its day was the most
important one of the holiday. So the arc is decided first, in one pass
over the whole journey, and the day texts are written afterwards with
that arc as context. Weighting a day is a comparison, and a comparison
needs the others in view.

**Nothing it produces is a fact.** The output can rename a stop *for the
story*, weight a day, shorten a text for a screen. It cannot move a stop,
change a time, add a kilometre or touch the roadbook. The merge returns a
narrow dict of narrative fields, and the caller has no path from here to
a write.
"""

from __future__ import annotations

from typing import Any

from .travel_story_manifest import (
    DEFAULT_IMPORTANCE,
    DEFAULT_STORY_ROLE,
    DEFAULT_VISUAL_STYLE,
    IMPORTANCE_LEVELS,
    MAX_MOTIFS,
    MAX_STORY_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_VIDEO_CAPTION_LENGTH,
    STORY_ROLES,
    VISUAL_STYLES,
    clean_line,
    clean_story,
)

DIRECTION_VERSION = 1

# How many chapters travel in one editing call. Small enough that the
# model keeps every day of the batch in view and the JSON stays inside a
# sane output budget, large enough that a three-week trip is four calls
# and not twenty-three. Changing it changes the cost of a trip directly.
CHAPTERS_PER_CALL = 6

# What a day is worth telling the model about. A stored summary is the
# most valuable thing here - it is the only place a real experience is
# already written down - so it gets the most room.
MAX_BRIEF_SUMMARY = 600
MAX_BRIEF_STOPS = 8


class StoryDirectorError(ValueError):
    """The model's answer cannot be used."""


# --- what the model is told ---------------------------------------------


_FACT_RULES = """
Faktenregeln, die über allem stehen:
- Du erfindest nichts. Keine Erlebnisse, Gespräche, Gefühle, Wetterlagen,
  Mahlzeiten, Probleme, Bewertungen, Aktivitäten oder Eigenschaften eines
  Ortes, die nicht in den gelieferten Daten stehen.
- Du kennst nur, was in diesem Auftrag steht. Es gibt keine weitere Quelle.
- Wo wenig Material vorliegt, schreibst du kurz. Ein knapper wahrer Satz
  ist besser als ein schöner erfundener.
- Zahlen übernimmst du nur, wenn sie geliefert wurden, und du rundest sie
  nicht zu etwas anderem.
- Humor entsteht aus der Formulierung und aus echten Konstellationen, die
  in den Daten stehen - nie aus einer ausgedachten Anekdote.
"""

_TONE_RULES = """
Ton: warm, persönlich, leicht verspielt, gelegentlich augenzwinkernd.
Es soll klingen wie ein Reiserückblick unter Freunden, nicht wie ein
Reiseplan und nicht wie ein Datenbankauszug.
Verboten sind Formulierungen im Stil von "steht an Tag 8 auf dem Plan"
oder "31 Fotos sind an diesem Tag entstanden". Wenn die Fotoanzahl etwas
erzählt, dann als Beobachtung, nicht als Zählung.
Schreibe auf Deutsch, in der Wir-Form, wenn eine Crew genannt ist.
"""

ARC_SYSTEM_PROMPT = f"""Du bist Redakteur für einen Reiserückblick.
Du bekommst die Fakten einer kompletten Reise, Tag für Tag, und
entwickelst daraus den Reisebogen: worum es in dieser Reise ging, welche
Tage sie tragen und welche sie verbinden.

Deine Aufgabe ist die Gewichtung. Nicht jeder Tag ist gleich wichtig, und
du bist die einzige Stelle, die das beurteilen kann, weil du als einzige
alle Tage gleichzeitig siehst. Ein Überführungstag darf ein
Überführungstag sein.

Vergib Höhepunkte sparsam. Wenn jeder Tag ein Höhepunkt ist, ist es keiner.
{_FACT_RULES}{_TONE_RULES}
Motive nur, wenn sie wirklich aus den Tagen hervorgehen - wiederkehrende
Orte, wiederkehrende Arten von Tagen, eine erkennbare Richtung. Lieber
keins als ein behauptetes."""

CHAPTER_SYSTEM_PROMPT = f"""Du bist Redakteur für einen Reiserückblick und
schreibst jetzt einzelne Tageskapitel.

Du bekommst den bereits festgelegten Reisebogen und die Fakten einiger
Tage. Schreibe zu jedem gelieferten Tag:
- einen persönlichen Titel (kein Ortsname allein, wenn mehr möglich ist),
- einen Storytext für das Reisealbum,
- eine kurze Video-Caption: ein bis zwei Sätze, die auf einer Bildkarte
  lesbar sind. Das ist keine Kürzung des Storytexts, sondern eine eigene
  Fassung.

Wenn ein Stoppname technisch ist - eine vollständige Adresse, ein
Park4Night-Code, eine Nummer - darfst du für die Erzählung einen
lesbaren Namen vorschlagen. Der echte Name bleibt davon unberührt; du
benennst nichts um, du schlägst nur vor, wie man es nennen würde.
Lass das Feld weg, wenn der Name in Ordnung ist.
{_FACT_RULES}{_TONE_RULES}
Wiederhole dich nicht zwischen den Tagen. Der Reisebogen sagt dir, welche
Rolle jeder Tag spielt - ein Überführungstag bekommt zwei Sätze, ein
großer Höhepunkt darf ausholen."""


def _brief_stop(stop: dict[str, Any]) -> str:
    name = clean_line(stop.get("name"), limit=MAX_TITLE_LENGTH)
    kind = clean_line(stop.get("kind"), limit=40)
    return f"{name} ({kind})" if kind else name


def chapter_brief(chapter: dict[str, Any]) -> dict[str, Any]:
    """One day, in the smallest form that can still be written about.

    Only what the manifest already holds, and only the parts a writer
    could use. Media ids are counted rather than listed: a model cannot
    see a photo and a list of uuids is a way to hallucinate about one.
    """
    facts = chapter.get("facts") or {}
    base = chapter.get("base") or {}
    stops = [_brief_stop(stop) for stop in (chapter.get("stops") or [])[:MAX_BRIEF_STOPS]]
    brief = {
        "chapter_id": clean_line(chapter.get("chapter_id"), limit=200),
        "day_number": facts.get("day_number"),
        "date": clean_line(chapter.get("date"), limit=40),
        "title": clean_line(base.get("title"), limit=MAX_TITLE_LENGTH),
        "stops": [name for name in stops if name],
        "stop_count": facts.get("stop_count"),
        "photo_count": facts.get("photo_count"),
    }
    if facts.get("distance_km"):
        brief["distance_km"] = facts["distance_km"]
    if facts.get("duration_minutes"):
        brief["duration_minutes"] = facts["duration_minutes"]
    summary = clean_story(base.get("stored_summary"), limit=MAX_BRIEF_SUMMARY)
    if summary:
        brief["known_summary"] = summary
    captions = [
        clean_line(caption.get("text"), limit=120)
        for caption in (chapter.get("captions") or [])[:4]
    ]
    captions = [text for text in captions if text]
    if captions:
        # A caption somebody wrote under a photo is a real observation and
        # the closest thing to a memory this data has.
        brief["photo_captions"] = captions
    # A chapter somebody already wrote by hand is included as context so
    # the neighbouring days match its voice - and marked, so the model
    # knows not to rewrite it.
    if (chapter.get("story") or {}).get("source") == "override":
        brief["written_by_hand"] = clean_story(chapter["story"].get("text"), limit=MAX_BRIEF_SUMMARY)
    return brief


def trip_brief(manifest: dict[str, Any]) -> dict[str, Any]:
    """The trip itself, without its days."""
    trip = manifest.get("trip") or {}
    facts = manifest.get("facts") or {}
    brief: dict[str, Any] = {
        "title": clean_line(trip.get("title"), limit=MAX_TITLE_LENGTH),
        "start_date": clean_line(trip.get("start_date"), limit=40),
        "end_date": clean_line(trip.get("end_date"), limit=40),
        "chapter_count": facts.get("chapter_count"),
        "photo_count": facts.get("photo_count"),
    }
    if facts.get("distance_km"):
        brief["distance_km"] = facts["distance_km"]
    crew = manifest.get("crew")
    if isinstance(crew, dict):
        if crew.get("people"):
            brief["crew"] = crew["people"]
        if crew.get("vehicle"):
            brief["vehicle"] = crew["vehicle"]
    return brief


def chapter_batches(
    chapters: list[dict[str, Any]], *, size: int = CHAPTERS_PER_CALL
) -> list[list[dict[str, Any]]]:
    """Split the trip into editing calls, in order."""
    step = max(1, int(size))
    return [chapters[start : start + step] for start in range(0, len(chapters), step)]


# --- what the model may answer ------------------------------------------


ARC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title_variant": {"type": "string"},
        "subtitle": {"type": "string"},
        "opening": {"type": "string"},
        "closing": {"type": "string"},
        "motifs": {"type": "array", "items": {"type": "string"}},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string"},
                    "importance": {"type": "string", "enum": list(IMPORTANCE_LEVELS)},
                    "story_role": {"type": "string", "enum": list(STORY_ROLES)},
                    "visual_style": {"type": "string", "enum": list(VISUAL_STYLES)},
                },
                "required": ["chapter_id", "importance", "story_role", "visual_style"],
            },
        },
    },
    "required": ["chapters"],
}

CHAPTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chapter_id": {"type": "string"},
                    "title": {"type": "string"},
                    "story": {"type": "string"},
                    "video_caption": {"type": "string"},
                    "stop_story_names": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stop_id": {"type": "string"},
                                "story_name": {"type": "string"},
                            },
                            "required": ["stop_id", "story_name"],
                        },
                    },
                    # Not shown to anyone. It is kept so that a sentence
                    # nobody can trace can be found later - and asking for
                    # it makes the model check its own grounding.
                    "based_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["chapter_id", "title", "story", "video_caption"],
            },
        }
    },
    "required": ["chapters"],
}


# --- what is allowed back in --------------------------------------------


def _enum(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    text = clean_line(value, limit=40)
    return text if text in allowed else fallback


def merge_arc(answer: Any, *, known_ids: set[str]) -> dict[str, Any]:
    """Read the arc, keeping only chapters that exist.

    A chapter id the model invented is dropped rather than repaired. It
    cannot be mapped to anything - guessing which day was meant would put
    one day's weighting on another day.
    """
    if not isinstance(answer, dict):
        raise StoryDirectorError("Der Reisebogen ist kein Objekt")
    weights: dict[str, dict[str, str]] = {}
    for entry in answer.get("chapters") or []:
        if not isinstance(entry, dict):
            continue
        chapter_id = clean_line(entry.get("chapter_id"), limit=200)
        if chapter_id not in known_ids or chapter_id in weights:
            continue
        weights[chapter_id] = {
            "importance": _enum(entry.get("importance"), IMPORTANCE_LEVELS, DEFAULT_IMPORTANCE),
            "story_role": _enum(entry.get("story_role"), STORY_ROLES, DEFAULT_STORY_ROLE),
            "visual_style": _enum(entry.get("visual_style"), VISUAL_STYLES, DEFAULT_VISUAL_STYLE),
        }
    narrative = {
        "title_variant": clean_line(answer.get("title_variant"), limit=MAX_TITLE_LENGTH),
        "subtitle": clean_line(answer.get("subtitle"), limit=MAX_TITLE_LENGTH),
        "opening": clean_story(answer.get("opening")),
        "closing": clean_story(answer.get("closing")),
        "motifs": [
            clean_line(motif, limit=80)
            for motif in (answer.get("motifs") or [])[:MAX_MOTIFS]
            if clean_line(motif, limit=80)
        ],
    }
    return {"narrative": narrative, "weights": weights}


def merge_chapters(
    answer: Any,
    *,
    known_ids: set[str],
    stop_ids_by_chapter: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    """Read one batch of edited chapters.

    Every id is checked against what actually exists - the chapter id
    against the trip, each stop id against that chapter. A story name for
    a stop that belongs to a different day would attach one place's name
    to another, which is exactly the kind of quiet wrongness this whole
    layer is supposed to prevent.
    """
    if not isinstance(answer, dict):
        raise StoryDirectorError("Die Kapitelantwort ist kein Objekt")
    edited: dict[str, dict[str, Any]] = {}
    for entry in answer.get("chapters") or []:
        if not isinstance(entry, dict):
            continue
        chapter_id = clean_line(entry.get("chapter_id"), limit=200)
        if chapter_id not in known_ids or chapter_id in edited:
            continue
        allowed_stops = stop_ids_by_chapter.get(chapter_id) or set()
        names: dict[str, str] = {}
        for item in entry.get("stop_story_names") or []:
            if not isinstance(item, dict):
                continue
            stop_id = clean_line(item.get("stop_id"), limit=200)
            story_name = clean_line(item.get("story_name"), limit=MAX_TITLE_LENGTH)
            if stop_id in allowed_stops and story_name:
                names[stop_id] = story_name
        record = {
            "title": clean_line(entry.get("title"), limit=MAX_TITLE_LENGTH),
            "story": clean_story(entry.get("story"), limit=MAX_STORY_LENGTH),
            "video_caption": clean_line(
                entry.get("video_caption"), limit=MAX_VIDEO_CAPTION_LENGTH
            ),
        }
        if names:
            record["stop_story_names"] = names
        based_on = [
            clean_line(item, limit=120)
            for item in (entry.get("based_on") or [])[:12]
            if clean_line(item, limit=120)
        ]
        if based_on:
            record["based_on"] = based_on
        if any((record["title"], record["story"], record["video_caption"])):
            edited[chapter_id] = record
    return edited


def build_direction(
    *,
    context_hash: str,
    narrative: dict[str, Any],
    weights: dict[str, dict[str, str]],
    chapters: dict[str, dict[str, Any]],
    model: str = "",
    calls: int = 0,
) -> dict[str, Any]:
    """One stored editing pass, tied to the material it was made for.

    ``context_hash`` is the whole point of the record: it says which
    version of the trip this text was written about, so a later build can
    tell reuse from staleness without asking the model.
    """
    merged: dict[str, dict[str, Any]] = {}
    for chapter_id in set(weights) | set(chapters):
        entry = dict(chapters.get(chapter_id) or {})
        entry.update(weights.get(chapter_id) or {})
        merged[chapter_id] = entry
    return {
        "direction_version": DIRECTION_VERSION,
        "story_context_hash": clean_line(context_hash, limit=64),
        "narrative": narrative,
        "chapters": merged,
        "model": clean_line(model, limit=120),
        "calls": int(calls) if isinstance(calls, int) and not isinstance(calls, bool) else 0,
    }


__all__ = [
    "ARC_SCHEMA",
    "ARC_SYSTEM_PROMPT",
    "CHAPTERS_PER_CALL",
    "CHAPTERS_SCHEMA",
    "CHAPTER_SYSTEM_PROMPT",
    "DIRECTION_VERSION",
    "StoryDirectorError",
    "build_direction",
    "chapter_batches",
    "chapter_brief",
    "merge_arc",
    "merge_chapters",
    "trip_brief",
]
