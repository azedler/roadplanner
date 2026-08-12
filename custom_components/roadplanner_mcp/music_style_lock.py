"""The musical common ground three variants have to share.

An A/B/C comparison is only worth listening to if the thing being
compared is the *architecture* and not three different tastes in music.
If the single score happens to come back as a guitar piece and the
atmosphere bed as something orchestral, the listener answers "which
music do I like" and the question about layering never gets asked.

So the style is fixed once, as data, and every request in the test is
built from it. The lock has a hash, and the hash is part of what the
cache is keyed on: change the style and the assets are genuinely
different assets, keep it and every variant is genuinely comparable.

What this file will NOT do
--------------------------

It will not pretend the provider takes any of this as an instruction.

Lyria's request carries a model, a free-text prompt and an optional
output format. There is no tempo field, no key field, no seed, not even
a duration - and no `negative_prompt`, so everything the music must not
be has to be said in the same sentence as everything it must be. Tempo
and key here are therefore WISHES, and each field says so itself rather
than leaving somebody to infer it from a schema that looks
authoritative.

That distinction is the point of `INFLUENCE`. This project has already
shipped a figure that read like a guarantee and was a guess; a style
lock whose fields all looked equally binding would be the same mistake
in a new place. What is actually verifiable afterwards is verifiable by
measurement, and `MEASURABLE` says which of those there are - which is
fewer than one would like, and saying the number honestly is cheaper
than building a music-analysis stack for one listening test.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

STYLE_LOCK_VERSION = 1

# How much say a request really has over each property.
#
# Every value here is PROMPT_ONLY today. That is not an oversight - it
# is what the provider's interface offers, and writing it out is the
# only way the difference between "asked for" and "got" stays visible
# once these fields are sitting in a UI looking official.
INFLUENCE_PROMPT_ONLY = "prompt_only"
INFLUENCE_PARAMETER = "parameter"

INFLUENCE: dict[str, str] = {
    "style": INFLUENCE_PROMPT_ONLY,
    "tempo_family": INFLUENCE_PROMPT_ONLY,
    "target_tempo_bpm": INFLUENCE_PROMPT_ONLY,
    "key_family": INFLUENCE_PROMPT_ONLY,
    "core_instruments": INFLUENCE_PROMPT_ONLY,
    "character": INFLUENCE_PROMPT_ONLY,
    "avoid": INFLUENCE_PROMPT_ONLY,
}

# What can be checked on the returned audio without building a
# music-information-retrieval stack for one listening test.
#
# Length and loudness come out of ffprobe and ffmpeg's own R128 meter,
# which are already in the renderer. Tempo and key would need an
# analysis library and a tolerance argument nobody has agreed on, so
# they are named as NOT measured rather than estimated badly - an
# estimate wearing the same field name as a measurement is how a guess
# becomes a fact in a report.
MEASURABLE = ("duration_seconds", "loudness_lufs", "true_peak_dbfs")
NOT_MEASURED = ("tempo_bpm", "key")

# The style for the first architecture test. Deliberately narrow: a
# comparison of architectures wants one taste held still, not a survey.
DEFAULT_STYLE_LOCK: dict[str, Any] = {
    "style": "warm nordic family travel score",
    "tempo_family": "gentle to moderate",
    "target_tempo_bpm": [75, 85],
    "key_family": "D major or a closely related key",
    "core_instruments": ["acoustic guitar", "soft cello", "warm atmospheric pad"],
    "character": ["warm", "intimate", "lightly playful", "spacious", "restrained"],
    # The whole no-list in one place, because the provider has no
    # negative prompt and this is the only channel there is. Half of an
    # earlier version of this list lived in a brief and never reached a
    # single request.
    "avoid": [
        "vocals",
        "spoken voice",
        "trailer bombast",
        "melodrama",
        "comedy underscore",
        "dominant electronic beats",
        "aggressive drums",
        "advertising music",
        "generic stock-music neutrality",
    ],
}


class StyleLockError(ValueError):
    """A style that cannot be locked. Always says which field."""


def build_style_lock(**overrides: Any) -> dict[str, Any]:
    """The locked style, with any field deliberately overridden.

    Unknown fields are refused rather than carried. A misspelled key
    that rode along silently would change the hash - and therefore buy
    a second set of assets - without changing a single word of any
    prompt.
    """
    lock = json.loads(json.dumps(DEFAULT_STYLE_LOCK))
    for name, value in overrides.items():
        if name not in DEFAULT_STYLE_LOCK:
            raise StyleLockError(f"Unbekanntes StyleLock-Feld: {name!r}")
        lock[name] = value
    lock["style_lock_version"] = STYLE_LOCK_VERSION
    # Carried WITH the values rather than kept in a document beside
    # them, so nothing downstream can display a tempo without also
    # having been handed the sentence that says it is only a wish.
    lock["influence"] = dict(INFLUENCE)
    lock["measurable"] = list(MEASURABLE)
    lock["not_measured"] = list(NOT_MEASURED)
    lock["hash"] = style_lock_hash(lock)
    return lock


def style_lock_hash(lock: dict[str, Any]) -> str:
    """What the assets depend on: the musical content, nothing else.

    The influence and measurability notes are documentation about the
    provider, not about the music, so they stay out - otherwise a
    corrected note would invalidate a cache full of perfectly good
    audio.
    """
    payload = {
        name: lock.get(name)
        for name in sorted(DEFAULT_STYLE_LOCK)
    }
    payload["style_lock_version"] = STYLE_LOCK_VERSION
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )
    return digest.hexdigest()[:16]


def style_sentence(lock: dict[str, Any]) -> str:
    """The lock as the prompt text every role shares.

    English, because that is the language the provider's own
    documentation writes prompts in, and a style term is a term of art
    rather than a sentence to a person.
    """
    tempo = lock.get("target_tempo_bpm") or []
    if isinstance(tempo, (list, tuple)) and len(tempo) == 2:
        pace = f"around {int(tempo[0])}-{int(tempo[1])} BPM"
    elif tempo:
        pace = f"around {int(tempo if not isinstance(tempo, (list, tuple)) else tempo[0])} BPM"
    else:
        pace = str(lock.get("tempo_family") or "")
    parts = [
        f"Style: {lock.get('style')}.",
        f"Tempo: {lock.get('tempo_family')}, {pace}.".replace(", .", "."),
        f"Harmony: {lock.get('key_family')}.",
        "Instruments: " + ", ".join(lock.get("core_instruments") or []) + ".",
        "Character: " + ", ".join(lock.get("character") or []) + ".",
        # Last, and phrased as one clause, because it is the longest
        # part and a prompt that opens with a list of prohibitions
        # reads as a description of what to make.
        "Avoid: " + ", ".join(lock.get("avoid") or []) + ".",
    ]
    return " ".join(part for part in parts if part.strip(" ."))


def requested_versus_measured(
    lock: dict[str, Any], measured: dict[str, Any] | None = None
) -> dict[str, Any]:
    """What was asked for, what came back, and what was never checked.

    The third list is the one that matters. A report that shows a
    requested tempo beside a measured length invites reading the tempo
    as confirmed too, and nothing here measured it.
    """
    found = dict(measured or {})
    return {
        "requested_style_lock": {
            name: lock.get(name) for name in sorted(DEFAULT_STYLE_LOCK)
        },
        "style_lock_hash": lock.get("hash") or style_lock_hash(lock),
        "measured": {name: found.get(name) for name in MEASURABLE if name in found},
        # Named, not omitted. An absent answer rendered as a state is
        # this project's most repeated failure.
        "not_measured": [
            *NOT_MEASURED,
            *[name for name in MEASURABLE if name not in found],
        ],
        "influence": dict(INFLUENCE),
    }


__all__ = [
    "DEFAULT_STYLE_LOCK",
    "INFLUENCE",
    "INFLUENCE_PARAMETER",
    "INFLUENCE_PROMPT_ONLY",
    "MEASURABLE",
    "NOT_MEASURED",
    "STYLE_LOCK_VERSION",
    "StyleLockError",
    "build_style_lock",
    "requested_versus_measured",
    "style_lock_hash",
    "style_sentence",
]
