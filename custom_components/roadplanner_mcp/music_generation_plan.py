"""What has to be BOUGHT, as its own answer.

Between "this is the music the film should have" and "send the request"
sits a question nobody had been asking out loud: *which pieces must
actually be generated right now, and what does that cost?* The plan says
what the film wants; the cache says what already exists; the provider
says what one request delivers. Only all three together answer it.

Two things make this worth its own module rather than a few lines inside
the service.

**A prototype is not a small soundtrack.** Before spending on twelve
minutes of music, one wants sixty to ninety seconds of the real film with
real music, made through the real path - same cue sheet, same planner,
same provider, same mux - and judged. That is a different SCOPE, not a
different architecture, so it is expressed here as a window rather than
as a second code path.

**A request is not a duration.** The provider bills per request and
delivers a piece of its own length. Quoting "75 seconds of music" as if
seconds were the unit would be a price nobody is charged. What is bought
is one generation; what is used from it is a separate number, and both
belong in front of somebody before they agree.

The official documentation for the current model is not reachable from
the build environment, so the numbers here are declared as ESTIMATES and
the words say so. The installation itself can ask the provider what it
offers - that is what the system check's model probe is for - and the
actual charge comes from the bill, not from this file.
"""

from __future__ import annotations

import hashlib
from typing import Any

# What a generation plan is FOR. A prototype exists to answer "is this
# music right at all" as cheaply as an answer can be had; the full score
# is what gets made once that is settled.
SCOPE_PROTOTYPE = "prototype"
SCOPE_FULL_FILM = "full_film"

# The prototype window. Deliberately the same bounds the quality excerpt
# uses - the point is to hear the music under the film somebody is
# actually going to look at, not under a specially made piece.
PROTOTYPE_MIN_SECONDS = 60.0
PROTOTYPE_MAX_SECONDS = 90.0


class GenerationPlanError(ValueError):
    """The plan cannot be turned into purchases. Always says why."""


def _overlap(start: float, end: float, window_start: float, window_end: float) -> float:
    return max(0.0, min(end, window_end) - max(start, window_start))


def build_generation_plan(
    plan: dict[str, Any],
    *,
    scope: str,
    cached_by_section: dict[str, str] | None = None,
    window: tuple[float, float] | None = None,
    track_seconds: float,
    price_per_generation: float,
    model: str,
    currency: str = "USD",
) -> dict[str, Any]:
    """Which pieces to generate now, how many requests, at what price.

    `window` narrows the plan to a stretch of the film without changing
    the plan: a prototype covers one section of the same score the whole
    film would get, so what is learned from it transfers. A section the
    window does not touch is simply not bought yet.

    Nothing here contacts anything. It is arithmetic over a plan, a cache
    listing and the provider's own limits, and it exists so the number
    somebody agrees to is computed once and shown before the request
    rather than derived again afterwards.
    """
    if scope not in (SCOPE_PROTOTYPE, SCOPE_FULL_FILM):
        raise GenerationPlanError(f"Unbekannter Umfang: {scope!r}")
    sections = list(plan.get("sections") or [])
    if not sections:
        raise GenerationPlanError("Der Musikplan enthält keine Abschnitte")
    limit = float(track_seconds)
    if limit <= 0:
        raise GenerationPlanError("Die Länge einer Generierung ist unbekannt")

    cached = dict(cached_by_section or {})
    start, end = window if window else (0.0, float(plan.get("film_seconds") or 0.0))
    if end <= start:
        raise GenerationPlanError("Das Zeitfenster hat keine Länge")

    wanted: list[dict[str, Any]] = []
    for section in sections:
        section_start = float(section.get("start_seconds") or 0.0)
        section_end = float(section.get("end_seconds") or 0.0)
        heard = _overlap(section_start, section_end, start, end)
        if heard <= 0:
            continue
        name = str(section.get("section") or "")
        needed = float(section.get("seconds") or 0.0)
        if needed > limit + 0.01:
            # The failure this area has already had once, refused rather
            # than paid for: a section longer than one generation plays
            # its track and then goes quiet.
            raise GenerationPlanError(
                f"Abschnitt '{section.get('label') or name}' braucht "
                f"{round(needed)} s, eine Generierung liefert höchstens "
                f"{round(limit)} s - der Rest wäre still"
            )
        wanted.append(
            {
                "section": name,
                "label": section.get("label") or name,
                "prompt": section.get("prompt") or "",
                "mood": section.get("mood") or "",
                # What the film needs from this section, and how much of
                # it this window actually hears. For a prototype the two
                # differ, and the difference is the whole point: a piece
                # is bought whole and judged in part.
                "seconds": round(needed, 2),
                "heard_seconds": round(heard, 2),
                "start_seconds": round(section_start, 2),
                "end_seconds": round(section_end, 2),
                "cached_name": cached.get(name, ""),
            }
        )

    if not wanted:
        raise GenerationPlanError(
            "In diesem Zeitfenster spielt kein geplanter Musikabschnitt"
        )

    missing = [entry for entry in wanted if not entry["cached_name"]]
    generations = len(missing)
    return {
        "scope": scope,
        "model": model,
        "window_start_seconds": round(start, 2),
        "window_end_seconds": round(end, 2),
        "window_seconds": round(end - start, 2),
        "sections": wanted,
        "reused": [entry for entry in wanted if entry["cached_name"]],
        "to_generate": missing,
        "generation_count": generations,
        # How much audio is being ordered - which is NOT what is billed.
        "requested_seconds": round(sum(entry["seconds"] for entry in missing), 1),
        # And what one request actually delivers, so nobody reads the
        # number above as the unit of price.
        "provider_track_seconds": round(limit),
        "billed_per": "generation",
        "price_per_generation": round(float(price_per_generation), 4),
        "estimated_cost": round(generations * float(price_per_generation), 2),
        "currency": currency,
        # Said plainly rather than implied: this is an estimate made
        # without the provider's own current price list, which is not
        # reachable from where this is built.
        "cost_is_estimate": True,
        "plan_hash": plan_hash(plan, model=model),
    }


def plan_hash(plan: dict[str, Any], *, model: str) -> str:
    """What the audio depends on. Two plans that ask for the same music
    at the same times must not buy it twice."""
    digest = hashlib.sha256()
    digest.update(f"{model}".encode("utf-8"))
    for section in plan.get("sections") or []:
        digest.update(
            "|".join(
                (
                    str(section.get("section") or ""),
                    str(section.get("prompt") or ""),
                    f"{float(section.get('seconds') or 0):.1f}",
                )
            ).encode("utf-8")
        )
        digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def describe(generation_plan: dict[str, Any]) -> str:
    """One sentence a person can check before agreeing to a charge.

    Deliberately says the unit. "75 Sekunden Musik" invites reading the
    seconds as the thing being paid for, which they are not.
    """
    count = int(generation_plan.get("generation_count") or 0)
    if not count:
        return "Alles schon erzeugt - ein weiterer Lauf kostet nichts."
    track = generation_plan.get("provider_track_seconds")
    cost = generation_plan.get("estimated_cost")
    currency = generation_plan.get("currency") or "USD"
    used = generation_plan.get("window_seconds")
    piece = "Generierung" if count == 1 else "Generierungen"
    return (
        f"{count} {piece} · Lyria liefert je ein Stück von bis zu {track} s · "
        f"davon im Film zu hören: {used} s · geschätzt {cost} {currency}, "
        "abgerechnet pro Anfrage"
    )


__all__ = [
    "PROTOTYPE_MAX_SECONDS",
    "PROTOTYPE_MIN_SECONDS",
    "SCOPE_FULL_FILM",
    "SCOPE_PROTOTYPE",
    "GenerationPlanError",
    "build_generation_plan",
    "describe",
    "plan_hash",
]
