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

class MusicPlanError(ValueError):
    """A film this plan cannot score, said rather than approximated."""


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
    # How many generations it takes to COVER the film, allowing for the
    # overlaps. This is a floor, not a preference.
    #
    # It used to be capped at four - the number of names in the table
    # below - and nothing then checked whether four sections of a
    # twelve-minute film would fit in four generations. They did not: the
    # planner asked for 186-second sections while one call yielded 118,
    # so each section played its track and then went quiet. Four and a
    # half minutes of silence in a twelve-minute film, and it would have
    # appeared on the first real generation, after it was paid for.
    #
    # Fewer sections is still better - §32's "large coherent sections",
    # and each one is a charge - but "fewer" may never win against
    # "covers the film".
    needed = max(1, int(-(-total // max(1.0, per_track - CROSSFADE_SECONDS))))
    # `MIN_SECTION_SECONDS` used to clamp this DOWNWARD, which is the
    # same bug one step further along: on a film where the two disagree,
    # a shorter list of longer sections is exactly a list of sections
    # that no single generation can fill. Coverage is a requirement;
    # "not too many small pieces" is a preference, and a preference does
    # not get to win.
    if needed > MAX_SECTIONS:
        # Said, not silently truncated. Clamping here would produce
        # sections longer than one generation can fill - the same silence
        # again, one guard further along. This is the third place in this
        # function where "keep the list short" could have quietly beaten
        # "cover the film"; it is a real condition and it gets a sentence.
        raise MusicPlanError(
            f"Ein Film von {round(total / 60)} Minuten braucht {needed} "
            f"Generierungen à {round(per_track)} s - mehr als die "
            f"{MAX_SECTIONS}, die ein Soundtrack haben soll. Mit diesem "
            "Modell ist er nicht vertonbar."
        )
    count = max(needed, 1)

    names = _order_for(count)
    share = total / count
    # A film long enough to need three journey sections would otherwise
    # show "Norden und große Reise" three times in the panel, and nobody
    # could tell which one had already been generated.
    repeats = {name: names.count(name) for name in set(names)}
    seen: dict[str, int] = {}
    sections: list[dict[str, Any]] = []
    for index, name in enumerate(names):
        seen[name] = seen.get(name, 0) + 1
        start = index * share
        # Every section but the first starts a crossfade early, so the
        # one before it is still sounding when it arrives.
        start = max(0.0, start - (CROSSFADE_SECONDS if index else 0.0))
        end = total if index == count - 1 else (index + 1) * share
        sections.append(
            {
                "section": name,
                "label": (
                    _SECTION_LABEL[name]
                    if repeats[name] == 1
                    else f"{_SECTION_LABEL[name]} {seen[name]}"
                ),
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
#
# Past four the middle stretches rather than the vocabulary growing: a
# long journey is more journey, not a new kind of thing. A twelve-minute
# film needs five generations to be covered at three minutes each, so
# five and six are ordinary cases rather than exotic ones.
_ORDER_FOR = {
    1: (SECTION_JOURNEY,),
    2: (SECTION_OPENING, SECTION_FINALE),
    3: (SECTION_OPENING, SECTION_JOURNEY, SECTION_FINALE),
    4: (SECTION_OPENING, SECTION_JOURNEY, SECTION_RETURN, SECTION_FINALE),
}
# The most a film may be cut into. Eight three-minute pieces is
# twenty-four minutes of music; a film longer than that is a different
# problem from the one this table solves.
MAX_SECTIONS = 8


def _order_for(count: int) -> tuple[str, ...]:
    """The section names for a film of this many pieces."""
    if count in _ORDER_FOR:
        return _ORDER_FOR[count]
    middle = count - 3
    return (
        (SECTION_OPENING,)
        + tuple(SECTION_JOURNEY for _ in range(max(1, middle)))
        + (SECTION_RETURN, SECTION_FINALE)
    )[:count]


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
        section["prompt"] = section_prompt(section, motifs=motifs, seconds=section["seconds"])
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


def section_prompt(section: dict[str, Any], *, motifs: list[str], seconds: float) -> str:
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
        "price_per_generation": round(float(price_per_generation), 2),
        "currency": "USD",
        "seconds": plan.get("film_seconds"),
        # How much audio is being ordered, which is not the same as the
        # film's length: sections overlap by a crossfade. Named because
        # "0,40 USD" alone says nothing about what is bought.
        "audio_seconds": round(
            sum(float(entry.get("seconds") or 0) for entry in plan.get("sections") or []),
            1,
        ),
        # Whether a model placed the boundaries or the arithmetic did.
        # The difference is audible and it is worth being able to see.
        "planned_by": plan.get("planned_by") or "arithmetik",
        # Said in the dialog rather than assumed: what is generated is
        # kept, so agreeing to this price agrees to it once.
        "assets_are_stored": True,
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
    "MusicPlanError",
    "MAX_SECTIONS",
    "section_prompt",
    "plan_sections",
    "section_cache_key",
]
