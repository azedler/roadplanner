"""Whether the subject of a day actually gets seen, not just included.

Coverage answers "is the central motif in the film at all?". A real film
showed why that is not enough: the bunkers of the day the trip was partly
about were present, correctly, in three quarter-size collage tiles, while
a picnic photograph held the screen alone. Every check passed. The day
still did not show what it was about.

So there is a second question, and it is this module:

    coverage    - is the motif there?          covered | unmet
    prominence  - does it get a real place?    none | supporting | prominent

A place counts as prominent when the medium holding it has the frame to
itself for a moment - the day's opening clip, or the hero photograph.
Four pictures sharing one tile for four seconds is *supporting*: worth
having, not the same as being shown.

Medium-neutral on purpose
-------------------------

If the best evidence of the day's subject is a video, the video takes the
prominent place. Preferring a photograph because the system knew
photographs first is exactly the kind of accident that becomes a rule.

Nothing here selects or rejects media. It reorders, so the medium that
best shows the day's subject is the one in the slot that shows it - and
it invents nothing: a day with no central motif, or with no medium that
covers one, is left exactly as it was.
"""

from __future__ import annotations

from typing import Any

from .film_photo_allocation import _covers, score_of
from .media_curation import motif_matches
from .video_analysis import ROLE_HERO, clip_score

STATE_NONE = "none"
STATE_SUPPORTING = "supporting"
STATE_PROMINENT = "prominent"

# How many prominent places a day has. One: the thing it opens on. A day
# with two "the point of the day" moments has none, which is the same
# reason a day gets at most one hero clip.
PROMINENT_SLOTS = 1


def _clip_covers(segment: dict[str, Any], motif: str, alternatives: list[str]) -> bool:
    """Whether this moment is evidence of that subject.

    A segment has no motif list - it has the short subject line the
    analysis wrote. So the same matcher the photographs use is pointed at
    that text, rather than a second rule invented here for videos.
    """
    if not isinstance(segment, dict):
        return False
    seen = [str(segment.get("subject") or ""), str(segment.get("reason") or "")]
    for token in alternatives or [motif]:
        if motif_matches(token, seen):
            return True
    return False


def prominence_for_day(
    *,
    must_cover: list[str] | None,
    alternatives: dict[str, list[str]] | None = None,
    media_ids: list[str] | None = None,
    analyses: dict[str, Any] | None = None,
    clips: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """What each central motif gets, and what would show it best.

    Read-only: it reports. The reordering is a separate step, so a panel
    can show the verdict without anything moving underneath it.
    """
    motifs = [str(value) for value in (must_cover or []) if str(value)]
    media_ids = list(media_ids or [])
    analyses = analyses or {}
    clips = list(clips or [])
    alternatives = alternatives or {}

    # What currently holds a prominent place: the day's first clip, if it
    # has one, and otherwise its first photograph.
    leading_clip = clips[0] if clips else None
    leading_photo = media_ids[0] if media_ids else ""

    states: dict[str, str] = {}
    best: dict[str, dict[str, Any]] = {}
    for motif in motifs:
        options = list(alternatives.get(motif) or [])

        photo_matches = [
            media_id
            for media_id in media_ids
            if _covers(analyses.get(media_id), motif, options)
        ]
        clip_matches = [
            segment for segment in clips if _clip_covers(segment, motif, options)
        ]

        if not photo_matches and not clip_matches:
            states[motif] = STATE_NONE
            continue

        already_prominent = (
            leading_clip is not None and leading_clip in clip_matches
        ) or (leading_photo and leading_photo in photo_matches)
        states[motif] = STATE_PROMINENT if already_prominent else STATE_SUPPORTING

        # What WOULD show it best, whether or not it is needed. A video
        # wins on its own merit rather than by being a video: the two
        # scores are compared as they are, and a strong clip of the
        # subject beats a middling photograph of it.
        candidate: dict[str, Any] | None = None
        if clip_matches:
            strongest = max(clip_matches, key=clip_score)
            candidate = {
                "media_type": "video",
                "media_id": str(strongest.get("media_id") or ""),
                "score": clip_score(strongest),
                "is_hero_clip": strongest.get("role") == ROLE_HERO,
            }
        if photo_matches:
            strongest_photo = max(
                photo_matches, key=lambda media_id: score_of(analyses, media_id)
            )
            photo_score = score_of(analyses, strongest_photo)
            if candidate is None or photo_score > candidate["score"]:
                candidate = {
                    "media_type": "image",
                    "media_id": strongest_photo,
                    "score": photo_score,
                    "is_hero_clip": False,
                }
        if candidate is not None:
            best[motif] = candidate

    return {
        "coverage": {
            motif: "unmet" if states[motif] == STATE_NONE else "covered"
            for motif in states
        },
        "prominence": states,
        "best": best,
        "unmet_prominence": [
            motif for motif, state in states.items() if state == STATE_SUPPORTING
        ],
    }


def reserve_for_prominence(
    media_ids: list[str],
    *,
    must_cover: list[str] | None,
    alternatives: dict[str, list[str]] | None = None,
    analyses: dict[str, Any] | None = None,
    clips: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The order, and which medium was RESERVED for a prominent slot.

    Reordering alone was not enough, and the reason is one line in the
    scene grammar: a day whose visual style is "collage" has no prominent
    slot at all - every picture goes into a tile, including the one moved
    to the front. So on exactly the kind of day that has a lot of
    material, the day's own subject stayed small however it was sorted.
    A live film showed it twice.

    So the caller is told WHICH medium was chosen, not only where it
    ended up, and the planner reserves a slot for it before packing the
    rest. The choice itself is unchanged: one per day, medium-neutral,
    and nothing at all when the day has no central motif or when a clip
    already opens on it.
    """
    ordered = list(media_ids or [])
    found = prominence_for_day(
        must_cover=must_cover,
        alternatives=alternatives,
        media_ids=ordered,
        analyses=analyses,
        clips=clips,
    )
    wanted = found["unmet_prominence"]
    if not wanted or len(ordered) < 2:
        return {"order": ordered, "reserved": "", "state": found}

    candidates = [
        found["best"][motif]
        for motif in wanted
        if motif in found["best"] and found["best"][motif]["media_type"] == "image"
    ]
    if not candidates:
        # The best evidence is a video that is not leading, or there is
        # none at all. Neither is fixed by moving photographs about.
        return {"order": ordered, "reserved": "", "state": found}

    chosen = max(candidates, key=lambda entry: entry["score"])["media_id"]
    if chosen not in ordered:
        return {"order": ordered, "reserved": "", "state": found}
    if ordered[0] != chosen:
        ordered = [chosen] + [media_id for media_id in ordered if media_id != chosen]
    return {"order": ordered, "reserved": chosen, "state": found}


def promote_for_prominence(
    media_ids: list[str],
    *,
    must_cover: list[str] | None,
    alternatives: dict[str, list[str]] | None = None,
    analyses: dict[str, Any] | None = None,
    clips: list[dict[str, Any]] | None = None,
) -> list[str]:
    """The same photographs, with the day's subject moved to the front.

    The scene plan makes the first photograph the hero, so moving one is
    the whole of "give it a prominent place" - no new scene type, no new
    layout rule, nothing for the renderer to learn.

    Three things it deliberately does not do. It never adds or drops a
    picture. It never moves anything when a clip already shows the
    subject, because that clip opens the day and the place is taken. And
    with several central motifs it promotes ONE - the strongest evidence
    of any of them - because a day has one opening, and promoting a
    second would only demote the first.
    """
    """The same decision, order only.

    Kept because a caller that just wants the sequence should not have to
    know about reservation. The reservation IS the same choice - one
    function makes it, so the two can never disagree.
    """
    return reserve_for_prominence(
        media_ids,
        must_cover=must_cover,
        alternatives=alternatives,
        analyses=analyses,
        clips=clips,
    )["order"]


__all__ = [
    "PROMINENT_SLOTS",
    "reserve_for_prominence",
    "STATE_NONE",
    "STATE_PROMINENT",
    "STATE_SUPPORTING",
    "prominence_for_day",
    "promote_for_prominence",
]
