"""Asking a model what is in a photograph, and what it is worth.

The old vision contract asked one question - *pick the best few of
these* - and got back a list of IDs. That is a verdict without evidence:
nothing survives it except an order, so nothing downstream can ask "does
any of these show a moose?".

This contract asks per photograph instead, and asks for two different
things:

- **How good is it** - sharpness, composition, light, and how much of a
  moment it is rather than an exposure.
- **What is in it** - a handful of plain nouns. Not a caption, not a
  story: the words somebody would use to say what they were looking at.

The nouns are the part that matters. A day whose stop is called
"Elchpark" needs a picture whose nouns include a moose, and no amount of
ranking can answer that if ranking is all that comes back.

What the model is deliberately NOT asked
----------------------------------------

Where this was, when it was, whose trip it is, or what happened. All of
that is in the roadbook, measured, and a second opinion about it is not
a second source - it is a way for a film to state something nobody
recorded. It is also not asked who anybody is: "two adults and two
children" is a description, a name is an identification.

Nothing here calls anything. It builds a request and reads an answer.
"""

from __future__ import annotations

import hashlib
from typing import Any

CURATION_VISION_VERSION = 1

# One call carries a whole day. Per photograph is a call per photograph,
# and a 25-day trip would be several hundred - the batch is what makes a
# thorough look affordable.
MAX_IMAGES_PER_CALL = 24

SYSTEM_PROMPT = """Du hilfst bei der Auswahl von Fotos für einen privaten Reisefilm.

Du bekommst mehrere Fotos eines einzelnen Reisetags, jedes mit einer ID.
Bewerte jedes Foto einzeln.

Für jedes Foto:
- motive: zwei bis fünf einzelne Substantive, die benennen, was zu sehen
  ist (zum Beispiel "Elch", "Wasserfall", "Wohnmobil", "Hafen", "Kinder",
  "Abendlicht"). Keine Sätze, keine Geschichten, keine Orte, keine Namen.
- visual_quality: 0-5 für Schärfe, Belichtung, Bildaufbau.
- story_value: 0-5 dafür, wie sehr das Foto einen Moment zeigt statt nur
  eine Aufnahme. Ein gelungener Moment mit kleinen technischen Schwächen
  ist mehr wert als ein perfektes Bild von nichts.
- emotion: 0-5 für die Wirkung.
- uniqueness: 0-5 dafür, wie sehr es sich von den anderen gezeigten
  Fotos unterscheidet.
- hero: true, wenn es allein für den Tag stehen könnte.
- zeigt: falls dir eine Liste von Begriffen mitgegeben wurde, nenne
  daraus nur die, die auf diesem Foto **eindeutig** zu sehen sind. Im
  Zweifel nichts nennen. Diese Begriffe stammen aus dem Reisetagebuch;
  dass einer davon genannt wird, heißt nicht, dass er zu sehen sein
  muss.

Regeln:
- Erfinde nichts. Wenn du etwas nicht erkennst, nenne es nicht.
- Du weißt nicht, wo oder wann das aufgenommen wurde, und sollst darüber
  nichts sagen. Diese Angaben kommen aus dem Reisetagebuch.
- Identifiziere keine Personen. Beschreibe höchstens, dass Menschen zu
  sehen sind und was sie tun.
- Bewerte jedes gelieferte Foto genau einmal, mit seiner ID.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "maxItems": MAX_IMAGES_PER_CALL,
            "items": {
                "type": "object",
                "properties": {
                    "image_id": {"type": "string"},
                    "motifs": {"type": "array", "items": {"type": "string"}},
                    "visual_quality": {"type": "integer"},
                    "story_value": {"type": "integer"},
                    "emotion": {"type": "integer"},
                    "uniqueness": {"type": "integer"},
                    "hero": {"type": "boolean"},
                    "shows": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["image_id", "motifs", "visual_quality", "story_value"],
            },
        }
    },
    "required": ["photos"],
}

MAX_MOTIFS = 5
MAX_MOTIF_LENGTH = 40


def build_prompt(count: int, checks: list[str] | None = None) -> str:
    """What one batch is asked.

    The list of terms is the one thing about the day that goes out, and
    it is a question rather than a statement: *is any of this visible?*
    It exists because matching a German model answer against a place
    name is a language problem no table survives - "Santa Claus Village"
    is not the word anybody would use for what is in the picture, and a
    photograph of a moose is only "Elch" if the day was written in
    German too.

    Asking directly also makes the honest answer available: a term
    nobody photographed simply comes back in nobody's list, and the
    prompt says outright that being asked is not evidence.
    """
    lines = [
        f"Hier sind {count} Fotos eines Reisetags.",
        "Bewerte jedes einzeln nach den Regeln und gib für jedes seine ID zurück.",
    ]
    terms = [str(value).strip() for value in (checks or []) if str(value).strip()]
    if terms:
        lines.append(
            "Prüfe außerdem für jedes Foto, welche dieser Begriffe eindeutig "
            f"darauf zu sehen sind: {', '.join(terms)}. "
            "Nenne sie unter \"zeigt\". Wenn keiner zu sehen ist, lass das Feld leer."
        )
    return " ".join(lines)


def analysis_key(media_ids: list[str], fingerprints: dict[str, str]) -> str:
    """The name one batch's answer is cached under.

    Built from what was actually looked at - each photograph's own hash -
    plus the contract version, because a changed prompt is a changed
    question and a cached answer to the old one is worse than none.
    """
    digest = hashlib.sha256()
    for media_id in sorted(media_ids):
        digest.update(str(media_id).encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(fingerprints.get(media_id) or "").encode("utf-8"))
        digest.update(b"\x1e")
    digest.update(str(CURATION_VISION_VERSION).encode("utf-8"))
    return digest.hexdigest()[:32]


def parse_analyses(
    payload: Any, *, known_ids: set[str], checks: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    """Read one batch's answer, keeping only what was actually asked about.

    An ID that was never sent is discarded rather than trusted. A model
    inventing an ID is not a rare event to guard against out of caution -
    it is the one way a curation result could point at a photograph that
    does not exist, and every later stage would treat it as real.
    """
    if not isinstance(payload, dict):
        return {}
    analyses: dict[str, dict[str, Any]] = {}
    for entry in payload.get("photos") or []:
        if not isinstance(entry, dict):
            continue
        media_id = str(entry.get("image_id") or "").strip()
        if media_id not in known_ids or media_id in analyses:
            continue
        motifs: list[str] = []
        for raw in entry.get("motifs") or []:
            motif = " ".join(str(raw or "").split())[:MAX_MOTIF_LENGTH]
            if motif and motif.casefold() not in {value.casefold() for value in motifs}:
                motifs.append(motif)
        allowed = {str(value).strip().casefold() for value in (checks or [])}
        shows = [
            str(value).strip()
            for value in (entry.get("shows") or [])
            if str(value).strip().casefold() in allowed
        ]
        analyses[media_id] = {
            "motifs": motifs[:MAX_MOTIFS],
            # Only terms that were actually asked about survive. A model
            # answering with something it was never given is answering a
            # different question.
            "shows": shows[:MAX_MOTIFS],
            "visual_quality": _score(entry.get("visual_quality")),
            "story_value": _score(entry.get("story_value")),
            "emotion": _score(entry.get("emotion")),
            "uniqueness": _score(entry.get("uniqueness")),
            "hero": bool(entry.get("hero")),
            "analysis_version": CURATION_VISION_VERSION,
        }
    return analyses


def _score(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, number))


def batches(media_ids: list[str], *, size: int = MAX_IMAGES_PER_CALL) -> list[list[str]]:
    """Split a day into calls, evenly rather than by the leftover.

    Twenty-five photographs as 24+1 asks a model to judge uniqueness with
    a sample of one. Two batches of thirteen and twelve both have enough
    to compare against.
    """
    total = len(media_ids)
    if total <= size:
        return [list(media_ids)] if total else []
    count = (total + size - 1) // size
    per = (total + count - 1) // count
    return [media_ids[index : index + per] for index in range(0, total, per)]


__all__ = [
    "CURATION_VISION_VERSION",
    "MAX_IMAGES_PER_CALL",
    "RESPONSE_SCHEMA",
    "SYSTEM_PROMPT",
    "analysis_key",
    "batches",
    "build_prompt",
    "parse_analyses",
]
