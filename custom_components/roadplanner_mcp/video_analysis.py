"""Asking a model for the good moment in a clip, and refusing the rest.

The user's wish is simple to say and easy to get wrong: find the good
moments in our videos and cut them into the film. The two ways it goes
wrong are worth naming before any code.

**A model that summarises is useless here.** "A family at a lake, warm
light, children playing" is a caption. What a film needs is *from 4.2 to
9.6 seconds*, because that is what a cut is made of. So the answer is a
time range or it is nothing.

**A model that is asked about a journey will describe the journey.** Given
a clip and a day, it will happily say which day it is, where it was, what
happened before. All of that already exists in the roadbook, measured,
and a second opinion about it is not a second source - it is a way for a
film to state something nobody recorded. So the model is told the facts
it may need and is forbidden to return any of them: day and stop come
from Roadplanner and are attached afterwards.

What comes back is checked as arithmetic, not as prose. A range outside
the clip, inverted, longer than the clip, or shorter than a cut can be
is thrown away rather than clamped - a wrong number quietly corrected is
a wrong number nobody notices.

Cost, and why the shape of the request matters
----------------------------------------------

Video is billed by what it costs to look at: roughly 300 tokens per
second of clip - a frame a second at 258 tokens plus about 32 tokens of
audio per second. That is cheap per clip and not cheap per library,
which is the whole reason `video_prefilter` exists and why analysis
never runs over a whole folder.

Nothing here calls anything. It builds a request and reads an answer, so
both can be tested without a key, without a network, and without
spending anything.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

ANALYSIS_VERSION = 1

# What a clip may be used for. The film decides where each belongs; the
# model only says which of these it could be.
ROLE_HERO = "hero_clip"
ROLE_AMBIENT = "ambient_clip"
ROLE_TRANSITION = "transition_clip"
ROLE_MAP_COMPANION = "map_companion"
CLIP_ROLES = (ROLE_HERO, ROLE_AMBIENT, ROLE_TRANSITION, ROLE_MAP_COMPANION)

# A cut shorter than this is a flicker; longer than this is a home video
# playing inside a film. The brief asks for roughly two to eight seconds
# and allows a real highlight to run longer.
MIN_SEGMENT_SECONDS = 1.8
MAX_SEGMENT_SECONDS = 8.0
MAX_HIGHLIGHT_SECONDS = 12.0

# Roughly what a second of video costs to look at: a sampled frame at
# 258 tokens plus about 32 tokens of audio. Used for the estimate shown
# before anybody agrees to anything - not for billing, which is Google's
# to do.
TOKENS_PER_VIDEO_SECOND = 300

SYSTEM_PROMPT = """Du bist Cutter für einen privaten Reisefilm.

Du bekommst einen kurzen Videoausschnitt. Deine einzige Aufgabe ist es,
darin den besten zusammenhängenden Moment zu finden und ihn als
Zeitbereich zu benennen.

Regeln:
- Antworte mit Sekunden relativ zum Anfang des gelieferten Ausschnitts.
- Der Bereich muss innerhalb des Ausschnitts liegen und darf höchstens
  so lang sein, wie es die Vorgabe erlaubt.
- Wenn der Ausschnitt keinen brauchbaren Moment enthält, sage das
  ausdrücklich, statt einen schwachen auszuwählen. Ein Film ohne
  schlechten Clip ist besser als ein Film mit einem.
- Beschreibe, was zu sehen ist, in einem knappen Satz. Erfinde nichts:
  keine Orte, keine Namen, keine Ereignisse, keine Gefühle, keine
  Zeitpunkte.
- Du weißt nicht, an welchem Tag oder an welchem Ort das aufgenommen
  wurde, und sollst darüber nichts sagen. Diese Angaben kommen aus dem
  Reisetagebuch und nicht von dir.
- Identifiziere keine Personen. Beschreibe höchstens, dass Menschen zu
  sehen sind und was sie tun.
"""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "usable": {"type": "boolean"},
        "start_seconds": {"type": "number"},
        "end_seconds": {"type": "number"},
        "role": {"type": "string", "enum": list(CLIP_ROLES)},
        "visual_quality": {"type": "integer"},
        "story_value": {"type": "integer"},
        "subject": {"type": "string"},
        "reason": {"type": "string"},
        "original_sound_interesting": {"type": "boolean"},
    },
    "required": ["usable", "reason"],
}


def analysis_key(asset: dict[str, Any], start: float, end: float) -> str:
    """The name one analysis is cached under.

    Built from what was actually looked at - the file's own hash, the
    window, and the version of this contract - so a re-run of the same
    question is free and a changed file is a different question. The
    version is in there because a changed prompt is a changed question
    too, and a cached answer to the old one would be worse than none.
    """
    digest = hashlib.sha256()
    for part in (
        str(asset.get("file_hash") or asset.get("provider_item_id") or ""),
        str(asset.get("media_id") or ""),
        f"{float(start):.3f}",
        f"{float(end):.3f}",
        str(ANALYSIS_VERSION),
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:32]


def build_request(asset: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """What is asked about one window.

    Deliberately thin: the model is shown a clip and told what a usable
    answer looks like. It is *not* told the day, the place, the trip or
    the story - not because they are secret, but because everything it is
    told is something it can repeat back as if it had seen it.
    """
    length = max(0.0, float(end) - float(start))
    return {
        "system_instruction": SYSTEM_PROMPT,
        "prompt": (
            f"Der Ausschnitt ist {length:.1f} Sekunden lang. "
            f"Wähle daraus höchstens {MAX_SEGMENT_SECONDS:.0f} Sekunden "
            f"(mindestens {MIN_SEGMENT_SECONDS:.1f}), die als Filmmoment "
            "funktionieren."
        ),
        "schema": RESPONSE_SCHEMA,
        "window": {"start": round(float(start), 3), "end": round(float(end), 3)},
    }


def estimated_tokens(seconds: float) -> int:
    """Roughly what looking at this much video costs, in tokens."""
    return int(max(0.0, float(seconds)) * TOKENS_PER_VIDEO_SECOND)


def parse_analysis(
    payload: Any, *, window_seconds: float, allow_highlight: bool = False
) -> dict[str, Any] | None:
    """Read one answer, or refuse it.

    Refuse rather than repair. A range that runs past the end of the clip
    is not a range that needs clamping - it is evidence the answer is
    about something other than what was sent, and a corrected version of
    it would look exactly like a good one.
    """
    if not isinstance(payload, dict):
        return None
    if not payload.get("usable"):
        return None
    try:
        start = float(payload.get("start_seconds"))
        end = float(payload.get("end_seconds"))
    except (TypeError, ValueError):
        return None
    if not (0.0 <= start < end):
        return None
    if end > window_seconds + 0.25:
        return None
    length = end - start
    ceiling = MAX_HIGHLIGHT_SECONDS if allow_highlight else MAX_SEGMENT_SECONDS
    if length < MIN_SEGMENT_SECONDS or length > ceiling:
        return None
    role = str(payload.get("role") or "")
    return {
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "duration_seconds": round(length, 3),
        "role": role if role in CLIP_ROLES else ROLE_AMBIENT,
        "visual_quality": _score(payload.get("visual_quality")),
        "story_value": _score(payload.get("story_value")),
        # Trimmed hard: this is shown to nobody and exists so a person
        # can see why a clip was chosen.
        "subject": " ".join(str(payload.get("subject") or "").split())[:160],
        "reason": " ".join(str(payload.get("reason") or "").split())[:200],
        # A recommendation and nothing more. Original sound is never
        # switched on by an analysis - somebody has to want it.
        "original_sound_interesting": bool(payload.get("original_sound_interesting")),
        "analysis_version": ANALYSIS_VERSION,
    }


def _score(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, number))


def clip_score(analysis: dict[str, Any]) -> float:
    """How much a film wants this clip.

    Story value counts double. A technically perfect shot of nothing is
    what a camera roll is full of, and the point of the whole exercise is
    to find the moments rather than the exposures.
    """
    return float(analysis.get("story_value", 0)) * 2 + float(
        analysis.get("visual_quality", 0)
    )


__all__ = [
    "ANALYSIS_VERSION",
    "CLIP_ROLES",
    "MAX_HIGHLIGHT_SECONDS",
    "MAX_SEGMENT_SECONDS",
    "MIN_SEGMENT_SECONDS",
    "RESPONSE_SCHEMA",
    "ROLE_AMBIENT",
    "ROLE_HERO",
    "SYSTEM_PROMPT",
    "TOKENS_PER_VIDEO_SECOND",
    "analysis_key",
    "build_request",
    "clip_score",
    "estimated_tokens",
    "parse_analysis",
]
