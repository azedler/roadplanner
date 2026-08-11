"""Which clips are worth looking at, decided locally and for free.

A phone's camera roll from a three-week trip holds a lot of video, and
most of it is not film material: a clip that starts with a black frame
because the shutter was still opening, a thirty-second pan of nothing, a
recording somebody stopped two seconds after starting it by accident,
four takes of the same thing.

Sending all of that to a model would be slow, would cost money, and -
the part that actually matters - would send a family's private footage
to a cloud service in order to be told it is uninteresting. So the
obvious cases are settled here, offline, before anything leaves the
house.

What this deliberately is not
-----------------------------

Not computer vision. There is no model here, no scene classifier, no
quality score pretending to be an opinion. It reads what ``ffprobe``
already knows plus a handful of sampled frames, and it answers exactly
one question: **is this worth the cost of a real look?**

Everything it rejects it rejects for a reason it can state. Everything
it is unsure about it passes on, because a clip wrongly dropped here is
a memory nobody will ever be offered again, while a clip wrongly passed
on costs a fraction of a cent.

Segments rather than clips
--------------------------

A long recording is not one candidate. Twelve minutes of driving
contains perhaps two moments; asking about the whole thing gets an
answer about the whole thing. So anything past `MAX_CANDIDATE_SECONDS`
is cut into overlapping windows and each is offered separately.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Below this a clip is an accident - a finger on the button, a recording
# stopped immediately. Above the upper bound it is not one moment.
MIN_CLIP_SECONDS = 1.5
MAX_CANDIDATE_SECONDS = 45.0
# How long a window is when a long recording is cut up, and how much the
# windows overlap so a moment on a boundary is not halved.
WINDOW_SECONDS = 30.0
WINDOW_OVERLAP_SECONDS = 4.0
# A film frame is 1280x720. Something much smaller than that was either
# a thumbnail or a screen recording of a chat.
MIN_HEIGHT = 480
# More than this from one trip is not a selection any more, it is an
# upload. The cap is on what gets *analysed*, not on what exists.
MAX_CANDIDATES_PER_TRIP = 60
MAX_CANDIDATES_PER_CHAPTER = 6
# One long recording may not become the whole day's shortlist. A twelve
# minute drive offers a few moments, not twelve.
MAX_WINDOWS_PER_RECORDING = 3

REASON_TOO_SHORT = "zu kurz"
REASON_TOO_SMALL = "zu klein"
REASON_NO_DURATION = "Länge unbekannt"
REASON_STATIC = "praktisch unbewegt"
REASON_DARK = "durchgehend dunkel"
REASON_DUPLICATE = "fast identisch mit einem anderen Clip"


def _seconds(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def technical_verdict(asset: dict[str, Any]) -> dict[str, Any]:
    """Judge one video on what its container already says.

    Free and instant: no decoding happens here. It catches the cases that
    are wrong regardless of content, which is most of what a camera roll
    accumulates.
    """
    duration = _seconds(asset.get("duration_seconds"))
    height = asset.get("height")
    if duration is None:
        # Unknown length is not a rejection. Some containers simply do not
        # say, and refusing them would quietly drop whole cameras.
        return {"usable": True, "reason": "", "note": REASON_NO_DURATION}
    if duration < MIN_CLIP_SECONDS:
        return {"usable": False, "reason": REASON_TOO_SHORT}
    if isinstance(height, int) and 0 < height < MIN_HEIGHT:
        return {"usable": False, "reason": REASON_TOO_SMALL}
    return {"usable": True, "reason": ""}


def windows_for(duration: float | None) -> list[tuple[float, float]]:
    """How one recording is offered: whole, or as overlapping windows.

    A twelve-minute drive holds perhaps two moments. Asked about as one
    thing it gets one answer about one thing, and the moments are lost in
    the average.
    """
    if duration is None or duration <= 0:
        return [(0.0, MAX_CANDIDATE_SECONDS)]
    if duration <= MAX_CANDIDATE_SECONDS:
        return [(0.0, round(duration, 3))]
    windows: list[tuple[float, float]] = []
    step = WINDOW_SECONDS - WINDOW_OVERLAP_SECONDS
    start = 0.0
    while start < duration and len(windows) < 12:
        end = min(duration, start + WINDOW_SECONDS)
        if end - start >= MIN_CLIP_SECONDS:
            windows.append((round(start, 3), round(end, 3)))
        if end >= duration:
            break
        start += step
    return windows


def frame_verdict(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Judge one window on a handful of sampled frames.

    ``samples`` is what the renderer app measured: for each sampled frame
    a mean brightness and a difference from the previous one, both 0..1.
    Two questions only, and both are about whether there is anything
    there at all:

    - Is it dark the whole way through? A lens cap, a pocket, a night
      recording with nothing in it.
    - Does anything change? A camera left running on a table is not a
      clip, however sharp it is.

    Sharpness is deliberately not judged. "Blurry" is the kind of measure
    that throws away the best moment of a trip because somebody moved.
    """
    if not samples:
        return {"usable": True, "reason": ""}
    brightness = [float(sample.get("brightness") or 0.0) for sample in samples]
    motion = [float(sample.get("motion") or 0.0) for sample in samples]
    if brightness and max(brightness) < 0.06:
        return {"usable": False, "reason": REASON_DARK}
    if len(motion) >= 3 and max(motion) < 0.012:
        return {"usable": False, "reason": REASON_STATIC}
    return {"usable": True, "reason": ""}


def deduplicate(assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Drop recordings that are the same take twice.

    Same day, same moment: a second attempt at one thing, and a film
    wants one of them. The longer one wins, because the shorter is
    usually the aborted try.

    Runs on **recordings**, before they are cut into windows, and that
    ordering is the whole correctness of it. Applied to windows it ate
    them: every window of one long recording carries that recording's
    capture time, so a twelve-minute drive looked like twelve copies of
    itself and came out as one thirty-second window.

    Returns kept and dropped, because a candidate that vanishes without a
    reason is indistinguishable from one that was never seen.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for asset in sorted(
        assets,
        key=lambda entry: (
            str(entry.get("taken_at") or ""),
            -float(entry.get("duration_seconds") or 0.0),
        ),
    ):
        taken = str(asset.get("taken_at") or "")
        chapter = str(asset.get("chapter_id") or "")
        if taken and any(
            str(other.get("taken_at") or "") == taken
            and str(other.get("chapter_id") or "") == chapter
            for other in kept
        ):
            dropped.append({**asset, "skipped_reason": REASON_DUPLICATE})
            continue
        kept.append(asset)
    return kept, dropped


def select_candidates(
    assets: list[dict[str, Any]],
    *,
    per_chapter: int = MAX_CANDIDATES_PER_CHAPTER,
    total: int = MAX_CANDIDATES_PER_TRIP,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The clips worth a paid look, and the ones that are not, with reasons.

    Returns ``(candidates, rejected)``. Both are returned because a
    number nobody can explain is worse than no number: the panel says how
    many were skipped and why, which is what makes "only three of your
    forty videos were analysed" an answer rather than a suspicion.

    The per-chapter cap spreads the budget across the trip. Without it a
    single afternoon with the camera running takes the whole allowance
    and the rest of the journey is never looked at.
    """
    rejected: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    for asset in assets:
        if str(asset.get("media_type") or "") != "video":
            continue
        verdict = technical_verdict(asset)
        if not verdict["usable"]:
            rejected.append({**asset, "skipped_reason": verdict["reason"]})
            continue
        usable.append(asset)

    # Recordings first, then windows. The other order eats long clips.
    usable, duplicates = deduplicate(usable)
    rejected.extend(duplicates)

    candidates: list[dict[str, Any]] = []
    for asset in usable:
        duration = _seconds(asset.get("duration_seconds"))
        # Spread the windows across the recording rather than taking the
        # first few: the first ninety seconds of a drive are the same
        # ninety seconds of road every time.
        every = windows_for(duration)
        if len(every) > MAX_WINDOWS_PER_RECORDING:
            step = len(every) / MAX_WINDOWS_PER_RECORDING
            every = [every[int(index * step)] for index in range(MAX_WINDOWS_PER_RECORDING)]
        for start, end in every:
            candidates.append(
                {
                    **asset,
                    "segment_start": start,
                    "segment_end": end,
                    "duration_seconds": round(end - start, 3),
                }
            )

    per_chapter_count: dict[str, int] = {}
    chosen: list[dict[str, Any]] = []
    for candidate in candidates:
        # The DAY, read from the field the library actually stores.
        #
        # This asked for `chapter_id`, which nothing in the media library
        # writes: every recording answered "" and the whole trip shared
        # one day's allowance. On a real trip that meant twenty videos in,
        # six out, and eighteen rejected as "Tagesbudget erreicht" - a
        # budget that had never been per day at all. Same shape as the
        # cache key that read `content_hash`: a field name from a
        # neighbouring structure, and a silently wrong answer.
        chapter = str(
            candidate.get("linked_day_id")
            or candidate.get("day_id")
            or candidate.get("chapter_id")
            or ""
        )
        if per_chapter_count.get(chapter, 0) >= per_chapter:
            rejected.append({**candidate, "skipped_reason": "Tagesbudget erreicht"})
            continue
        if len(chosen) >= total:
            rejected.append({**candidate, "skipped_reason": "Reisebudget erreicht"})
            continue
        per_chapter_count[chapter] = per_chapter_count.get(chapter, 0) + 1
        chosen.append(candidate)
    return chosen, rejected


__all__ = [
    "MAX_CANDIDATES_PER_CHAPTER",
    "MAX_CANDIDATES_PER_TRIP",
    "MAX_CANDIDATE_SECONDS",
    "MIN_CLIP_SECONDS",
    "deduplicate",
    "frame_verdict",
    "select_candidates",
    "technical_verdict",
    "windows_for",
]
