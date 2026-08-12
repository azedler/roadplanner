"""The whole A/B/C experiment, decided before anything is bought.

One stretch of film, three soundtracks, one question: does a travel film
want a single coherent piece, a restrained continuous atmosphere with a
characterful layer on top, or the atmosphere alone?

Everything about that experiment is arithmetic, and it is all here, in
front of the money:

**Which pieces have to be bought.** Three variants do not mean three
purchases. The layered variant and the atmosphere variant share their
bed - the same file, the same bytes - so the comparison between them is
a comparison of *architecture* rather than of two different beds that
happened to come back differently. Three variants, three roles, at most
three generations.

**What each one costs, before it is ordered.** The provider bills per
request, so the number that matters is how many requests there are; the
seconds are what is being asked for, not what is being paid for.

**That this cannot become a soundtrack.** A prototype that could quietly
generate music for a fifteen-minute film would be a prototype in name
only, and the whole reason for a small first test is that nobody has yet
heard whether the architecture is worth twelve minutes of it. So the
window is bounded here, by refusal rather than by intention.

Nothing in this module contacts anything. A model may improve the
wording of a request - that is what the director hook is for - but if it
is absent, or if what it returns breaks a rule, the deterministic prompt
stands and the experiment still runs.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .music_architecture import (
    PREVIEW_FADE_IN_SECONDS,
    PREVIEW_FADE_OUT_SECONDS,
    ROLE_BED,
    TARGET_LUFS,
    TRUE_PEAK_CEILING_DBTP,
    VARIANTS,
    ArchitectureError,
    describe_variant,
    required_roles,
    role_prompt,
    variant_layers,
)
from .music_cue_sheet import (
    ENERGY_CALM,
    ENERGY_LIVELY,
    ENERGY_STEADY,
    build_window_cue_sheet,
)
from .music_style_lock import build_style_lock, style_sentence
from .qa_excerpt import QA_MAX_SECONDS, QA_MIN_SECONDS, excerpt_range

PROTOTYPE_VERSION = 1

# What one generation is asked for, relative to what the film needs.
# There is no duration parameter, so a piece can come back short, and a
# piece that comes back short is silence at the end of the excerpt.
LENGTH_HEADROOM = 1.12

# The refusal that keeps a prototype a prototype. Anything longer than
# the quality excerpt's own ceiling is not a test of an architecture,
# it is the soundtrack - and buying that before the listening test is
# exactly the decision this block exists to postpone.
MAX_PROTOTYPE_SECONDS = QA_MAX_SECONDS

# How much text a planner may contribute to one request. Long enough for
# a sentence of musical direction, short enough that it cannot smuggle
# in a second style description that would break the style lock.
MAX_DIRECTOR_CHARS = 240

# A planner describes character. Times are the film's, and they are
# already known exactly - so a proposal that mentions one is refused
# rather than trimmed, because a number in a prompt reads as authority
# and this one would have been invented.
_TIME_WORDS = re.compile(
    r"\b(\d+\s*(s|sec|secs|second|seconds|min|minute|minutes|bpm)|"
    r"minute|second|timestamp|timecode)\b",
    re.IGNORECASE,
)

_ENERGY_WORDS = {
    ENERGY_CALM: "calm",
    ENERGY_STEADY: "steady",
    ENERGY_LIVELY: "moving",
}


class PrototypeError(ValueError):
    """The experiment cannot be laid out. Always says which part."""


def arc_sentence(window_sheet: dict[str, Any]) -> str:
    """The energy of the excerpt as one sentence, in its own order.

    Deliberately coarse. It names the shape of a minute, not the cuts
    inside it: a piece of music that tried to follow every scene change
    would be scoring the edit rather than the journey, and it would
    sound like it.
    """
    cues = list(window_sheet.get("cues") or [])
    if not cues:
        return ""
    words: list[str] = []
    for cue in cues:
        word = _ENERGY_WORDS.get(str(cue.get("energy_hint") or ""), "steady")
        if not words or words[-1] != word:
            words.append(word)
    if len(words) == 1:
        return f"Energy: {words[0]} throughout."
    return "Energy: " + ", then ".join(words) + "."


def validate_director_text(text: str) -> str:
    """One clause of musical direction from a planner, or nothing.

    Refused rather than repaired, and the reason is the same one the
    section planner already learned: a proposal bent into shape is a
    proposal nobody made. An empty return is a first-class answer here -
    the deterministic prompt is complete on its own.
    """
    found = " ".join(str(text or "").split())
    if not found:
        return ""
    if len(found) > MAX_DIRECTOR_CHARS:
        raise PrototypeError(
            f"Der Vorschlag ist {len(found)} Zeichen lang, erlaubt sind "
            f"{MAX_DIRECTOR_CHARS}"
        )
    if _TIME_WORDS.search(found):
        raise PrototypeError(
            "Der Vorschlag nennt Zeiten oder ein Tempo - beides bestimmt "
            "der Film beziehungsweise der StyleLock, nicht der Planer"
        )
    return found


def asset_cache_key(
    *,
    role: str,
    prompt: str,
    seconds: float,
    model: str,
    style_lock_hash: str,
) -> str:
    """What the audio for one role depends on, and nothing else.

    The prompt is in it, so a reworded request is a different asset. The
    style lock hash is in it too even though the prompt already contains
    the style - because the day the style sentence is reformatted
    without changing its meaning, the cache should still hold.
    """
    digest = hashlib.sha256()
    digest.update(
        "|".join(
            (
                f"proto{PROTOTYPE_VERSION}",
                str(role),
                str(model),
                str(style_lock_hash),
                f"{float(seconds):.1f}",
                str(prompt),
            )
        ).encode("utf-8")
    )
    return digest.hexdigest()[:16]


def build_prototype(
    scene_plan: dict[str, Any],
    *,
    chapters: list[dict[str, Any]] | None = None,
    style_lock: dict[str, Any] | None = None,
    variants: tuple[str, ...] | list[str] = VARIANTS,
    cached_by_key: dict[str, str] | None = None,
    director_text_by_role: dict[str, str] | None = None,
    model: str,
    track_seconds: float,
    price_per_generation: float,
    currency: str = "USD",
    chapter_id: str = "",
    start_seconds: float | None = None,
) -> dict[str, Any]:
    """The experiment: which excerpt, which pieces, what it costs.

    The excerpt comes from the same chooser the quality check uses, and
    that is not a convenience - the music has to be judged under the
    film somebody is actually going to look at. A window of its own
    would have proved something about a piece nobody ships.
    """
    wanted = [str(name) for name in (variants or [])]
    if not wanted:
        raise PrototypeError("Ohne Varianten gibt es nichts zu vergleichen")
    try:
        chosen = [describe_variant(name) for name in wanted]
    except ArchitectureError as err:
        raise PrototypeError(str(err)) from err

    excerpt = excerpt_range(
        scene_plan,
        chapter_id=chapter_id,
        start_seconds=start_seconds,
        min_seconds=QA_MIN_SECONDS,
        max_seconds=QA_MAX_SECONDS,
    )
    window_seconds = float(excerpt["seconds"])
    if window_seconds > MAX_PROTOTYPE_SECONDS + 0.5:
        # The guard, not a rounding note. Reached when a film is short
        # enough that the excerpt IS the film, and a whole-film
        # soundtrack is the one thing this block must not order.
        raise PrototypeError(
            f"Der Ausschnitt ist {round(window_seconds)} s lang - der Prototyp "
            f"deckt höchstens {round(MAX_PROTOTYPE_SECONDS)} s ab"
        )

    sheet = build_window_cue_sheet(
        scene_plan,
        start_frame=int(excerpt["start_frame"]),
        end_frame=int(excerpt["start_frame"]) + int(excerpt["frames"]),
        chapters=chapters,
    )
    lock = style_lock or build_style_lock()
    sentence = style_sentence(lock)
    arc = arc_sentence(sheet)

    asked = min(float(track_seconds), window_seconds * LENGTH_HEADROOM)
    if asked < window_seconds:
        raise PrototypeError(
            f"Eine Generierung liefert höchstens {round(float(track_seconds))} s, "
            f"der Ausschnitt braucht {round(window_seconds)} s"
        )

    cached = dict(cached_by_key or {})
    director = dict(director_text_by_role or {})
    assets: dict[str, dict[str, Any]] = {}
    for role in required_roles(wanted):
        prompt = role_prompt(role, style_sentence=sentence, arc=arc)
        extra = validate_director_text(director.get(role, ""))
        if extra:
            prompt = f"{prompt} {extra}"
        key = asset_cache_key(
            role=role,
            prompt=prompt,
            seconds=asked,
            model=model,
            style_lock_hash=str(lock.get("hash") or ""),
        )
        assets[role] = {
            "role": role,
            "prompt": prompt,
            "requested_seconds": round(asked, 2),
            "needed_seconds": round(window_seconds, 2),
            "cache_key": key,
            "cached_name": cached.get(key, ""),
            "planned_by": "gemini" if extra else "deterministisch",
        }

    missing = [asset for asset in assets.values() if not asset["cached_name"]]
    mixes: list[dict[str, Any]] = []
    for entry in chosen:
        layers = []
        for layer in variant_layers(entry["variant"]):
            asset = assets[layer["role"]]
            layers.append(
                {
                    "role": layer["role"],
                    "gain": layer["gain"],
                    "cache_key": asset["cache_key"],
                    "cached_name": asset["cached_name"],
                    "start_seconds": 0.0,
                    "seconds": round(window_seconds, 2),
                    # Preview fades, and named as such. A cut out of the
                    # middle of a film has no reason to start abruptly;
                    # that is not a statement about how the finished
                    # film's music should begin.
                    "fade_in_seconds": PREVIEW_FADE_IN_SECONDS,
                    "fade_out_seconds": PREVIEW_FADE_OUT_SECONDS,
                }
            )
        mixes.append(
            {
                **entry,
                "layers": layers,
                "ready": all(layer["cached_name"] for layer in layers),
            }
        )

    return {
        "prototype_version": PROTOTYPE_VERSION,
        "model": model,
        "excerpt": excerpt,
        "cue_sheet": sheet,
        "style_lock": lock,
        "style_sentence": sentence,
        "energy_arc": arc,
        "assets": [assets[role] for role in required_roles(wanted)],
        "variants": mixes,
        # The number that is actually billed, and the number that is
        # merely being asked for, side by side and labelled - the
        # provider charges per request and this project has already
        # shown somebody seconds where it meant requests.
        "generation_count": len(missing),
        "reused_count": len(assets) - len(missing),
        "requested_seconds_each": round(asked, 2),
        "provider_track_seconds": round(float(track_seconds)),
        "billed_per": "generation",
        "price_per_generation": round(float(price_per_generation), 4),
        "estimated_cost": round(len(missing) * float(price_per_generation), 2),
        "currency": currency,
        "cost_is_estimate": True,
        "window_seconds": round(window_seconds, 2),
        "target_lufs": TARGET_LUFS,
        "true_peak_ceiling_dbtp": TRUE_PEAK_CEILING_DBTP,
    }


def describe(prototype: dict[str, Any]) -> str:
    """One sentence to agree to, with the unit said out loud."""
    count = int(prototype.get("generation_count") or 0)
    variants = len(prototype.get("variants") or [])
    if not count:
        return (
            f"Alle Stücke sind vorhanden - die {variants} Vergleichsfassungen "
            "kosten nichts mehr."
        )
    piece = "Generierung" if count == 1 else "Generierungen"
    return (
        f"Music Architecture Prototype · {count} {piece} für {variants} "
        f"Fassungen à {round(float(prototype.get('window_seconds') or 0))} s · "
        f"geschätzt {prototype.get('estimated_cost')} "
        f"{prototype.get('currency') or 'USD'}, abgerechnet pro Anfrage"
    )


def bed_is_shared(prototype: dict[str, Any]) -> bool:
    """Whether the atmosphere really is one purchase, not two.

    Checked as a property rather than trusted, because the moment it
    stops being true the comparison between the layered and the
    atmosphere variant stops being about layering.
    """
    keys = {
        layer["cache_key"]
        for variant in prototype.get("variants") or []
        for layer in variant.get("layers") or []
        if layer.get("role") == ROLE_BED
    }
    return len(keys) <= 1


__all__ = [
    "LENGTH_HEADROOM",
    "MAX_DIRECTOR_CHARS",
    "MAX_PROTOTYPE_SECONDS",
    "PROTOTYPE_VERSION",
    "PrototypeError",
    "arc_sentence",
    "asset_cache_key",
    "bed_is_shared",
    "build_prototype",
    "describe",
    "validate_director_text",
]
