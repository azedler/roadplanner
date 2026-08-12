"""Letting a model decide where the music changes - within limits.

The deterministic planner cuts the film into equal shares. That is
defensible and it is also deaf: a boundary lands where the arithmetic
says, not where the journey turns. With a cue sheet describing what
actually happens, a model can do better - it can put the change where the
north begins rather than at minute four.

What it may decide is exactly one thing: **where the sections start and
what each should feel like.** Everything else is arithmetic that has
already been argued about, and none of it is up for negotiation:

- a section may never be longer than one generation delivers, because the
  rest of it would be silence;
- the sections must cover the film from nothing to its last second;
- a boundary sits on a cue boundary, so music never changes in the middle
  of a day;
- the count stays small, because a soundtrack is not a playlist and every
  section is a charge.

**A model's answer is a proposal, not a result.** Whatever comes back is
measured against those rules, and a proposal that breaks one is not
patched into shape - the deterministic plan is used instead and the
reason is recorded. Music that quietly went half-silent because a model
returned a number nobody checked is precisely the failure this area
already had.

**Nothing personal is sent.** The model sees times, roles, scene kinds
and the trip's own words - never a photograph, never a video, never a
place somebody stayed at that the story did not already name.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .music_cue_sheet import summarise

_LOGGER = logging.getLogger(__name__)

# Bumped when the instruction below changes what a plan MEANS. Part of
# the cache key, so a reworded brief does not silently reuse music that
# was composed for the old one.
PROMPT_VERSION = 1

# How few is few enough. Two is the smallest arrangement that can have a
# beginning and an end; past six a score reads as a playlist, which is
# what §33 exists to prevent.
MIN_SECTIONS = 1
MAX_SECTIONS = 6

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # Which cue this section opens on. An index into the
                    # sheet rather than a time: a model that answers in
                    # seconds answers with a number that can land inside
                    # a day, and then the music changes mid-scene.
                    "starts_at_cue": {"type": "integer"},
                    "label": {"type": "string"},
                    "mood": {"type": "string"},
                },
                "required": ["starts_at_cue", "label", "mood"],
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["sections"],
}


class PlanDirectorError(ValueError):
    """The proposal cannot be used. Always says which rule it broke."""


def build_brief(
    sheet: dict[str, Any],
    *,
    trip_title: str,
    narrative: dict[str, Any] | None,
    motifs: list[str],
    max_sections: int,
    style: str,
) -> str:
    """What the model is told. Structured facts and the trip's own words.

    Deliberately no photograph, no video, no frame - §28. A cue sheet is
    times and kinds, and that is enough to know where a journey turns.
    """
    arc = narrative if isinstance(narrative, dict) else {}
    cues = [
        {
            "cue": cue["index"],
            "start": cue["start_seconds"],
            "seconds": cue["seconds"],
            "day": cue.get("day_number"),
            "role": cue.get("story_role") or None,
            "importance": cue.get("importance") or None,
            "energy": cue.get("energy_hint"),
            "function": cue.get("narrative_function"),
            "video": bool(cue.get("has_video")),
            "map": bool(cue.get("has_map")),
        }
        for cue in sheet.get("cues") or []
    ]
    facts = {
        "film_seconds": sheet.get("film_seconds"),
        "overview": summarise(sheet),
        "cues": cues,
    }
    lines = [
        "Du planst die Musikabschnitte für einen privaten Reisefilm.",
        "",
        f"Reise: {trip_title}".strip(),
    ]
    for name, value in (
        ("Bogen", arc.get("arc")),
        ("Anfang", arc.get("opening")),
        ("Ende", arc.get("closing")),
    ):
        text = " ".join(str(value or "").split())
        if text:
            lines.append(f"{name}: {text[:300]}")
    if motifs:
        lines.append("Motive: " + ", ".join(motifs))
    lines += [
        "",
        f"Stil, der für alle Abschnitte gilt: {style}",
        "",
        "Der Film, Cue für Cue (Zeiten in Sekunden):",
        json.dumps(facts, ensure_ascii=False),
        "",
        "Aufgabe:",
        f"Teile den Film in {MIN_SECTIONS} bis {max_sections} zusammenhängende",
        "Musikabschnitte. Ein Abschnitt beginnt immer AUF einem Cue - gib",
        "seinen Index an. Der erste Abschnitt beginnt auf Cue 0.",
        "",
        "Setze die Grenzen dort, wo die Reise sich wendet: der Aufbruch, der",
        "Norden, der Rückweg, das Ankommen. Nicht bei jedem Tag - die Musik",
        "trägt große Bögen und markiert keine Kapitelschnitte.",
        "",
        "Wichtig:",
        "- Erfinde keine Reiseereignisse und keine Gefühle als Tatsachen.",
        "  Was du nicht in den Daten siehst, steht dir nicht zur Verfügung.",
        "- Die Abschnitte sind ein Soundtrack, kein Sampler: gleiche",
        "  Instrumentierung, gleiche Tempofamilie, gleiche Klangfarbe.",
        "- 'mood' beschreibt die Haltung eines Abschnitts in einem Satz.",
        "- 'label' ist eine kurze Überschrift, die ein Mensch wiedererkennt.",
    ]
    return "\n".join(lines)


def _cue_starts(sheet: dict[str, Any]) -> list[float]:
    return [float(cue["start_seconds"]) for cue in sheet.get("cues") or []]


def validate_proposal(
    proposal: Any,
    sheet: dict[str, Any],
    *,
    track_seconds: float,
    max_sections: int = MAX_SECTIONS,
) -> list[dict[str, Any]]:
    """Measure the model's answer against the rules that cost money.

    Returns the section boundaries it implies, or raises with the rule
    that was broken. Deliberately no repairing: a proposal nudged into
    shape is a plan nobody chose, and the deterministic one is better
    than a guess about what the model meant.
    """
    if not isinstance(proposal, dict):
        raise PlanDirectorError("Der Musikvorschlag ist kein Objekt")
    raw = proposal.get("sections")
    if not isinstance(raw, list) or not raw:
        raise PlanDirectorError("Der Musikvorschlag enthält keine Abschnitte")
    if len(raw) > max_sections:
        raise PlanDirectorError(
            f"{len(raw)} Abschnitte - höchstens {max_sections} ergeben einen Soundtrack"
        )

    starts = _cue_starts(sheet)
    if not starts:
        raise PlanDirectorError("Das Cue Sheet ist leer")
    total = float(sheet.get("film_seconds") or 0.0)
    if total <= 0:
        raise PlanDirectorError("Der Film hat keine Länge")

    chosen: list[tuple[int, str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise PlanDirectorError("Ein Abschnitt ist kein Objekt")
        index = entry.get("starts_at_cue")
        if not isinstance(index, int) or isinstance(index, bool):
            raise PlanDirectorError("Ein Abschnitt nennt keinen Cue-Index")
        if not 0 <= index < len(starts):
            raise PlanDirectorError(
                f"Cue {index} gibt es nicht - der Film hat {len(starts)}"
            )
        chosen.append(
            (
                index,
                " ".join(str(entry.get("label") or "").split())[:60],
                " ".join(str(entry.get("mood") or "").split())[:300],
            )
        )

    indices = [index for index, _label, _mood in chosen]
    if indices[0] != 0:
        raise PlanDirectorError(
            "Der erste Abschnitt beginnt nicht am Filmanfang - der Anfang wäre still"
        )
    if indices != sorted(indices) or len(set(indices)) != len(indices):
        raise PlanDirectorError("Die Abschnitte sind nicht in Reihenfolge oder doppelt")

    sections: list[dict[str, Any]] = []
    for position, (index, label, mood) in enumerate(chosen):
        start = starts[index]
        end = starts[indices[position + 1]] if position + 1 < len(chosen) else total
        length = end - start
        if length <= 0:
            raise PlanDirectorError("Ein Abschnitt hat keine Länge")
        if length > float(track_seconds):
            # The failure this whole area just had, arriving from a new
            # direction: a section longer than one generation plays its
            # track and then goes quiet.
            raise PlanDirectorError(
                f"Abschnitt '{label or position}' dauert {round(length)} s, eine "
                f"Generierung liefert höchstens {round(track_seconds)} s - der Rest "
                "wäre still"
            )
        sections.append(
            {
                "starts_at_cue": index,
                "label": label,
                "mood": mood,
                "start_seconds": round(start, 2),
                "end_seconds": round(end, 2),
                "seconds": round(length, 2),
            }
        )
    return sections


def proposal_hash(sections: list[dict[str, Any]], *, model: str) -> str:
    """What the audio depends on, and nothing else.

    Not the reasoning, not the labels' wording beyond what reaches a
    prompt: two proposals that ask for the same music at the same times
    must reuse the same tracks rather than buy them twice.
    """
    digest = hashlib.sha256()
    digest.update(f"{PROMPT_VERSION}|{model}".encode("utf-8"))
    for section in sections:
        digest.update(
            "|".join(
                (
                    str(section.get("start_seconds")),
                    str(section.get("seconds")),
                    str(section.get("mood")),
                )
            ).encode("utf-8")
        )
    return digest.hexdigest()[:16]


__all__ = [
    "MAX_SECTIONS",
    "MIN_SECTIONS",
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA",
    "PlanDirectorError",
    "build_brief",
    "proposal_hash",
    "validate_proposal",
]
