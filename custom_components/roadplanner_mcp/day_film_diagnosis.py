"""Where a day's pictures were lost, stage by stage.

The Wolfsschanze is named in its day's story and is barely visible in the
film. That is one symptom with at least four possible causes, and they
need opposite fixes:

1. The photographs were never accepted technically.
2. They were accepted but never reached the candidate pool.
3. They reached the pool, but the vision pass did not connect them to the
   motif the story is about.
4. They were curated correctly and the **scene plan** dropped them,
   because the day's frame budget could not pay for them.

Guessing between those is how a special case for one place gets written.
So this module does not fix anything. It counts, at every stage, and says
which stage is the first one where the number stops making sense.

The verdict is deliberately blunt: `curation` or `scene_plan` or
`no_material`. A day whose photographs simply do not show the thing is a
real answer too, and the most important one to be able to give - because
no amount of weighting conjures a picture nobody took.
"""

from __future__ import annotations

from typing import Any

from .media_curation import motif_matches

# A motif is "represented" once this share of the day's chosen pictures
# show it. Below that, a story that talks about a place is illustrated by
# something else - which is exactly the reported symptom.
REPRESENTATION_SHARE = 0.25
# ...and never fewer than this many pictures on a day that matters.
REPRESENTATION_MINIMUM = 2

VERDICT_NO_MATERIAL = "no_material"
VERDICT_CURATION = "curation"
VERDICT_SCENE_PLAN = "scene_plan"
VERDICT_OK = "ok"


def diagnose_day(
    *,
    chapter: dict[str, Any],
    curation: dict[str, Any] | None,
    media: list[dict[str, Any]],
    scene_plan: dict[str, Any] | None = None,
    film_media_ids: list[str] | None = None,
) -> dict[str, Any]:
    """The whole chain for one day, as counts and one verdict.

    ``media`` is every media record of the day, ``curation`` the stored
    derivation, ``film_media_ids`` what the film actually carried. Any of
    them may be missing - a day nobody curated is itself a finding, not
    an error.
    """
    curation = curation if isinstance(curation, dict) else {}
    records = [item for item in (media or []) if isinstance(item, dict)]
    pool = list(curation.get("pool_media_ids") or [])
    selected = list(curation.get("media_ids") or [])
    analyses = curation.get("analyses") or {}
    brief = curation.get("brief") or {}
    coverage = curation.get("coverage") or {}

    # What the day is supposed to be about, from the day's own text.
    must = [str(name) for name in (brief.get("must_cover") or [])]
    labels = brief.get("labels") or {}

    stages = {
        "media_total": len(records),
        "media_with_position": sum(1 for item in records if item.get("latitude")),
        "technically_accepted": len(curation.get("rejected") or []) + len(pool)
        if curation
        else 0,
        "rejected": len(curation.get("rejected") or []),
        "series_count": int(curation.get("series_count") or 0),
        "pool_size": len(pool),
        "analysed": int(curation.get("analysis_count") or 0),
        "selected": len(selected),
        "in_film": len(film_media_ids or []),
    }

    # Per motif: how many pictures in the pool show it, and how many of
    # the chosen ones do. The gap between those two numbers is the whole
    # diagnosis.
    motifs: list[dict[str, Any]] = []
    alternatives = brief.get("alternatives") or {}
    for name in must:
        # The same alternatives the selection used. A requirement is
        # "Wolfsschanze OR Bunker OR Gierloz", and checking only the
        # first would under-count exactly the way the coverage does not.
        options = list(alternatives.get(name) or [])
        in_pool = [
            media_id
            for media_id in pool
            if _shows(analyses.get(media_id), name, options)
        ]
        in_selection = [media_id for media_id in selected if media_id in in_pool]
        in_film = [
            media_id for media_id in (film_media_ids or []) if media_id in in_pool
        ]
        motifs.append(
            {
                "motif": name,
                "label": labels.get(name) or name,
                "in_pool": len(in_pool),
                "in_selection": len(in_selection),
                "in_film": len(in_film),
                "met": name in (coverage.get("met") or []),
                "pool_media_ids": in_pool[:20],
            }
        )

    verdict, detail = _verdict(stages, motifs, selected=selected, film=film_media_ids)
    return {
        "day_id": curation.get("day_id") or chapter.get("chapter_id") or "",
        "title": chapter.get("title") or "",
        "importance": chapter.get("importance") or "normal",
        "curated": bool(curation),
        "stages": stages,
        "must_cover": must,
        "motifs": motifs,
        "covered": list(coverage.get("met") or []),
        "missing": list(coverage.get("unmet") or []),
        "scene_plan_frames": int((scene_plan or {}).get("frames") or 0),
        "verdict": verdict,
        "detail": detail,
    }


def _shows(analysis: Any, motif: str, alternatives: list[str] | None = None) -> bool:
    """Whether the vision pass connected this picture to this motif.

    Uses the curation's OWN matcher against the curation's own fields.
    The first version read `zeigt` and `motive`, which the analyses do
    not have - they store `motifs` and `shows` - so it reported zero
    matches on every day of a real trip while the coverage on the same
    data reported the motif as met.

    That is the failure this module's docstring warns about, committed
    by the module itself: a diagnosis with its own matching describes a
    system nobody is running. Worse than no diagnosis, because it points
    confidently at the wrong stage.
    """
    if not isinstance(analysis, dict):
        return False
    seen: list[str] = []
    for key in ("motifs", "shows"):
        value = analysis.get(key)
        if isinstance(value, list):
            seen.extend(str(entry) for entry in value)
        elif isinstance(value, str):
            seen.append(value)
    wanted = list(alternatives or []) or [motif]
    return any(motif_matches(token, seen) for token in wanted)


def _verdict(
    stages: dict[str, int],
    motifs: list[dict[str, Any]],
    *,
    selected: list[str],
    film: list[str] | None,
) -> tuple[str, str]:
    """Which stage is the first one where the numbers stop adding up."""
    if not stages.get("media_total"):
        return VERDICT_NO_MATERIAL, "Der Tag hat keine Medien."
    # Judged on what reached the FILM when that is known. The reported
    # symptom is about the finished film, and a motif that is well
    # curated and then dropped by the scene plan would read as "fine"
    # if only the selection were counted - which is precisely the case
    # this module has to be able to name.
    in_film_known = film is not None
    reference = len(film or []) if in_film_known else len(selected or [])
    weak = [
        entry
        for entry in motifs
        if not _is_represented(entry, reference, in_film=in_film_known)
    ]
    if not weak:
        return VERDICT_OK, "Jedes Pflichtmotiv ist angemessen vertreten."

    names = ", ".join(entry["label"] for entry in weak)
    # The order of these three questions is the point of the module.
    if all(entry["in_pool"] == 0 for entry in weak):
        return (
            VERDICT_CURATION,
            f"Für {names} gibt es im Kandidatenpool kein einziges Bild, das das "
            "Motiv zeigt. Entweder existieren keine passenden Fotos, oder die "
            "Bildanalyse hat sie nicht mit dem Motiv verbunden. Die Bilder in "
            "`pool_media_ids` je Motiv sind leer - das ist die Stelle zum "
            "Nachsehen.",
        )
    if any(entry["in_pool"] > 0 and entry["in_selection"] == 0 for entry in weak):
        return (
            VERDICT_CURATION,
            f"Für {names} liegen passende Bilder im Pool, die Auswahl hat aber "
            "keins davon genommen. Der Fehler sitzt in der Kuratierung "
            "(Visual Coverage oder Bewertung), nicht im Film.",
        )
    if film is not None and any(
        entry["in_selection"] > 0 and entry["in_film"] < entry["in_selection"]
        for entry in weak
    ):
        return (
            VERDICT_SCENE_PLAN,
            f"Für {names} sind Bilder kuratiert, aber der Szenenplan hat sie "
            "verdrängt. Der Fehler sitzt in der Filmgewichtung, nicht in der "
            "Bildauswahl.",
        )
    return (
        VERDICT_CURATION,
        f"{names} ist zu schwach vertreten, ohne dass eine einzelne Stufe "
        "eindeutig schuld ist. Die Zahlen je Motiv zeigen, wo es dünn wird.",
    )


def _is_represented(motif: dict[str, Any], chosen: int, *, in_film: bool) -> bool:
    """Whether a motif got a fair share of the day's pictures."""
    if not chosen:
        return False
    needed = max(1, min(REPRESENTATION_MINIMUM, chosen))
    share = int(chosen * REPRESENTATION_SHARE)
    have = int(motif.get("in_film" if in_film else "in_selection") or 0)
    return have >= max(needed, share)


__all__ = [
    "REPRESENTATION_MINIMUM",
    "REPRESENTATION_SHARE",
    "VERDICT_CURATION",
    "VERDICT_NO_MATERIAL",
    "VERDICT_OK",
    "VERDICT_SCENE_PLAN",
    "diagnose_day",
]
