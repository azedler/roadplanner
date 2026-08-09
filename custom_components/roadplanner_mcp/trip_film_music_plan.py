"""How a film's music is laid out before anything is generated.

A soundtrack is not one prompt. A three-week journey has an opening, a
long middle, a turn for home and an ending, and a single track looped
across all of it says the same thing over a departure and an arrival.

But the opposite is worse. One short piece per day would be twenty-three
generations, twenty-three prices, and twenty-three seams - and each
seam is audible in a way a cut between photographs is not. So the plan
uses **few, long sections**: the fewest that still let the music change
where the journey does.

What this module is and is not
------------------------------

It is arithmetic over a film that already exists: its length, its
chapters, their importance and their story roles. It decides how many
sections there are, how long each runs, where they overlap and what
each one should sound like.

It generates nothing and costs nothing. That separation is deliberate -
the plan is what the cost dialog quotes and what the cache is keyed on,
so both can be computed, shown and compared without a provider ever
being called.
"""

from __future__ import annotations

import hashlib
from typing import Any

MUSIC_PLAN_VERSION = 1

# What the whole soundtrack should sound like. One sentence, reused by
# every section, because a film whose music changes character between
# sections sounds like a playlist rather than a score.
BASE_STYLE = (
    "Instrumental, warm, nordisch, leicht verspielt, atmosphärisch. "
    "Ruhig genug für einen Familien-Reisefilm. "
    "Kein Gesang, keine dominante Trailer-Musik - die Bilder bleiben vorn."
)

# The sections a journey can have, in order, each with what it is for.
# Deliberately a fixed vocabulary rather than something a model invents:
# four names that a person recognises, and that map onto the story roles
# the director already assigns.
SECTION_OPENING = "aufbruch"
SECTION_JOURNEY = "reise"
SECTION_RETURN = "rueckweg"
SECTION_FINALE = "finale"

_SECTION_MOOD = {
    SECTION_OPENING: "Aufbruchsstimmung, neugierig, leicht, vorwärts gerichtet.",
    SECTION_JOURNEY: "Weite, Landschaft, gleichmäßige Bewegung, ruhige Wärme.",
    SECTION_RETURN: "Etwas wehmütiger, satter, langsamer werdend.",
    SECTION_FINALE: "Ankommen, ruhig ausklingend, dankbar statt triumphal.",
}

_SECTION_LABEL = {
    SECTION_OPENING: "Aufbruch",
    SECTION_JOURNEY: "Norden und große Reise",
    SECTION_RETURN: "Rückweg",
    SECTION_FINALE: "Ankommen",
}

# A section shorter than this is a cue, not a chapter of a score - and a
# generation costs the same whether it covers forty seconds or two
# minutes, so a short section is mostly a wasted price.
MIN_SECTION_SECONDS = 45.0
# How long the two neighbours overlap. Long enough that neither is heard
# to stop, short enough that the change is still noticed.
CROSSFADE_SECONDS = 4.0
# Silence at both ends of the film, so the music does not begin on the
# first frame or end on the last.
FADE_IN_SECONDS = 2.0
FADE_OUT_SECONDS = 5.0


def plan_sections(
    *,
    film_seconds: float,
    track_seconds: float,
    chapters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Which musical sections this film gets, and how long each runs.

    The count comes from the film's length rather than from a taste: a
    two-minute film is one piece of music, a seven-minute film is four.
    Anything else is either a loop or a playlist.
    """
    total = max(0.0, float(film_seconds))
    if total <= 0:
        return []
    per_track = max(30.0, float(track_seconds))
    # How many generations it takes to cover the film once, allowing for
    # the overlaps, and never more sections than the film has room for.
    needed = max(1, int(-(-total // max(1.0, per_track - CROSSFADE_SECONDS))))
    room = max(1, int(total // MIN_SECTION_SECONDS))
    count = max(1, min(needed, room, len(_ORDER_FOR)))

    names = _ORDER_FOR[count]
    share = total / count
    sections: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        start = index * share
        # Every section but the first starts a crossfade early, so the
        # one before it is still sounding when it arrives.
        start = max(0.0, start - (CROSSFADE_SECONDS if index else 0.0))
        end = total if index == count - 1 else (index + 1) * share
        sections.append(
            {
                "section": name,
                "label": _SECTION_LABEL[name],
                "start_seconds": round(start, 2),
                "end_seconds": round(end, 2),
                "seconds": round(end - start, 2),
                "fade_in_seconds": FADE_IN_SECONDS if index == 0 else CROSSFADE_SECONDS,
                "fade_out_seconds": (
                    FADE_OUT_SECONDS if index == count - 1 else CROSSFADE_SECONDS
                ),
                "mood": _SECTION_MOOD[name],
            }
        )
    return sections


# Which sections a film of N pieces uses. A short film does not get a
# "Rückweg" it never had time to feel.
_ORDER_FOR = {
    1: (SECTION_JOURNEY,),
    2: (SECTION_OPENING, SECTION_FINALE),
    3: (SECTION_OPENING, SECTION_JOURNEY, SECTION_FINALE),
    4: (SECTION_OPENING, SECTION_JOURNEY, SECTION_RETURN, SECTION_FINALE),
}


def build_plan(
    *,
    trip: dict[str, Any],
    narrative: dict[str, Any] | None,
    film_seconds: float,
    track_seconds: float,
    chapters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The whole soundtrack, described but not generated."""
    sections = plan_sections(
        film_seconds=film_seconds, track_seconds=track_seconds, chapters=chapters
    )
    title = " ".join(str(trip.get("title") or "").split())[:120]
    motifs = _motifs(narrative, chapters)
    for section in sections:
        section["prompt"] = _prompt(section, motifs=motifs, seconds=section["seconds"])
    return {
        "music_plan_version": MUSIC_PLAN_VERSION,
        "trip_title": title,
        "film_seconds": round(max(0.0, float(film_seconds)), 2),
        "style": BASE_STYLE,
        "motifs": motifs,
        "sections": sections,
        "generation_count": len(sections),
    }


def _motifs(
    narrative: dict[str, Any] | None, chapters: list[dict[str, Any]] | None
) -> list[str]:
    """A few words about what this journey WAS, for the music to sit on.

    Kept short and taken from what is already written down. A long
    prompt does not make a better score; it makes a less predictable
    one, and predictability is the whole reason the cache works.
    """
    words: list[str] = []
    for value in (
        (narrative or {}).get("arc"),
        (narrative or {}).get("opening"),
        (narrative or {}).get("closing"),
    ):
        for part in str(value or "").replace("·", " ").split():
            cleaned = part.strip(".,;:!?\"'()").casefold()
            if len(cleaned) >= 5 and cleaned not in words:
                words.append(cleaned)
    return words[:6]


def _prompt(section: dict[str, Any], *, motifs: list[str], seconds: float) -> str:
    """What one section is asked for.

    The length is stated in words because that is how the long-form
    model is steered, and the style sentence is repeated in every
    section so the four pieces belong to one score.
    """
    minutes = max(1, round(seconds / 60))
    parts = [
        f"Erzeuge ein etwa {minutes}-minütiges Instrumentalstück.",
        BASE_STYLE,
        section["mood"],
    ]
    if motifs:
        parts.append("Stimmungsbilder: " + ", ".join(motifs) + ".")
    return " ".join(parts)


def plan_cache_key(plan: dict[str, Any], *, model: str) -> str:
    """One key for the whole plan, and one per section.

    Keyed on what actually determines the audio - the prompts, the
    lengths, the model and the contract version - so a re-render with
    the same film reuses every track, and a changed film regenerates
    only the sections whose prompt or length changed.
    """
    digest = hashlib.sha256()
    digest.update(str(model).encode("utf-8"))
    digest.update(str(plan.get("music_plan_version") or "").encode("utf-8"))
    for section in plan.get("sections") or []:
        digest.update(str(section.get("section") or "").encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(str(section.get("prompt") or "").encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(f"{float(section.get('seconds') or 0):.1f}".encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()[:32]


def section_cache_key(section: dict[str, Any], *, model: str) -> str:
    """The key one generated track is stored under."""
    digest = hashlib.sha256()
    for part in (
        str(model),
        str(MUSIC_PLAN_VERSION),
        str(section.get("section") or ""),
        str(section.get("prompt") or ""),
        f"{float(section.get('seconds') or 0):.1f}",
    ):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def cost_notice(
    plan: dict[str, Any],
    *,
    model: str,
    price_per_generation: float,
    cached: int = 0,
) -> dict[str, Any]:
    """What the dialog says before anybody agrees to anything.

    Asking somebody to approve a cost without naming one is not asking.
    The cached count is part of it: "four sections, three already
    generated, one new" is a different decision from "four new".
    """
    total = int(plan.get("generation_count") or 0)
    new = max(0, total - max(0, int(cached)))
    return {
        "model": model,
        "sections": total,
        "cached": min(total, max(0, int(cached))),
        "new_generations": new,
        "estimated_cost": round(new * float(price_per_generation), 2),
        "currency": "USD",
        "seconds": plan.get("film_seconds"),
        "reused": new == 0,
    }


__all__ = [
    "BASE_STYLE",
    "CROSSFADE_SECONDS",
    "FADE_IN_SECONDS",
    "FADE_OUT_SECONDS",
    "MIN_SECTION_SECONDS",
    "MUSIC_PLAN_VERSION",
    "SECTION_FINALE",
    "SECTION_JOURNEY",
    "SECTION_OPENING",
    "SECTION_RETURN",
    "build_plan",
    "cost_notice",
    "plan_cache_key",
    "plan_sections",
    "section_cache_key",
]
