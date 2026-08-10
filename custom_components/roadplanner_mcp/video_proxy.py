"""Cutting the small copy that leaves the house, and the one the film shows.

Two proxies, for two entirely different reasons, and confusing them is
the mistake this module exists to prevent.

The **analysis proxy** is what a model is allowed to see. It is small,
short and quiet: one window of a recording, downscaled, stripped of its
audio, at a frame rate that is enough to judge a moment and not enough to
be a copy of the family's footage. Video is billed by what it costs to
look at, so its size is also its price - but the reason it is small comes
first, and the reason is that a private recording should leave in the
smallest form that can answer the question.

The **render proxy** is what the film plays. It is cut to the segment the
analysis chose, at the resolution the film actually shows, and it exists
so the renderer never touches an original and never reaches the network.
It keeps no audio either: original sound is off by default, and a track
nobody asked for is a private conversation waiting to be played at a
family gathering.

Neither of them modifies the original. Both are written to a temporary
place the caller owns and deletes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .ffmpeg_runner import async_run_ffmpeg

_LOGGER = logging.getLogger(__name__)

# What a model needs to judge a moment.
#
# 360 lines and 8 frames a second is deliberately below "nice to watch":
# the question is "is something happening here, and when", not "is this
# sharp". Every step down is also a step down in what a cloud service
# receives.
ANALYSIS_HEIGHT = 360
ANALYSIS_FPS = 8
ANALYSIS_CRF = 32

# What the film shows. 720p because that is what the film renders at, so
# anything larger is bytes nobody sees.
RENDER_HEIGHT = 720
RENDER_CRF = 23

# A window longer than this is not a moment. The prefilter already cuts
# recordings into windows; this is the guard for a caller that did not.
MAX_ANALYSIS_SECONDS = 45.0
MAX_RENDER_SECONDS = 12.0

# ffmpeg gets a wall clock. A cut that takes minutes is a stuck ffmpeg,
# and holding the executor forever is how a panel stops responding.
ANALYSIS_TIMEOUT = 180.0
RENDER_TIMEOUT = 180.0


class VideoProxyError(RuntimeError):
    """A proxy could not be produced. Never raised for a missing source."""


def _clamp(value: Any, *, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(low, min(high, number))


def analysis_args(
    source: Path, target: Path, *, start: float, end: float
) -> list[str]:
    """The ffmpeg call for an analysis proxy. Pure, so a test can read it.

    `-ss` before `-i` seeks by keyframe, which is fast and approximate.
    That is the right trade here: the model is told the window's offsets
    separately, and a proxy that carries a little handle around the window
    is easier to make than a frame-exact one.
    """
    start = _clamp(start, low=0.0, high=86_400.0, default=0.0)
    end = _clamp(end, low=0.0, high=86_400.0, default=0.0)
    duration = min(max(end - start, 0.5), MAX_ANALYSIS_SECONDS)
    return [
        "-nostdin",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        # No audio, at all. Not "muted" - absent. A track that is merely
        # muted still travels.
        "-an",
        "-vf",
        f"scale=-2:{ANALYSIS_HEIGHT}:force_original_aspect_ratio=decrease,fps={ANALYSIS_FPS}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(ANALYSIS_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    ]


def render_args(source: Path, target: Path, *, start: float, end: float) -> list[str]:
    """The ffmpeg call for the piece the film plays.

    Accurate rather than fast: `-ss` after `-i` decodes to the exact
    point, because this cut is what the viewer sees and a keyframe's worth
    of drift is a clip that starts before the moment does.
    """
    start = _clamp(start, low=0.0, high=86_400.0, default=0.0)
    end = _clamp(end, low=0.0, high=86_400.0, default=0.0)
    duration = min(max(end - start, 0.5), MAX_RENDER_SECONDS)
    return [
        "-nostdin",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        # Original sound is off for every clip in the film. It is stored
        # as a recommendation and never acted on automatically - a private
        # conversation must not become audible because a model found the
        # scene interesting.
        "-an",
        "-vf",
        f"scale=-2:{RENDER_HEIGHT}:force_original_aspect_ratio=decrease",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(RENDER_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(target),
    ]


async def async_cut_analysis_proxy(
    source: Path, target: Path, *, start: float, end: float
) -> int:
    """Cut the small copy a model may see. Returns its size in bytes."""
    return await _async_cut(
        analysis_args(source, target, start=start, end=end),
        target,
        timeout=ANALYSIS_TIMEOUT,
        what="Analyseproxy",
    )


async def async_cut_render_proxy(
    source: Path, target: Path, *, start: float, end: float
) -> int:
    """Cut the piece the film plays. Returns its size in bytes."""
    return await _async_cut(
        render_args(source, target, start=start, end=end),
        target,
        timeout=RENDER_TIMEOUT,
        what="Renderproxy",
    )


async def _async_cut(args: list[str], target: Path, *, timeout: float, what: str) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        await async_run_ffmpeg(args, timeout_seconds=timeout)
    except Exception as err:  # noqa: BLE001 - ffmpeg failures are all the same here
        raise VideoProxyError(f"{what} konnte nicht erzeugt werden: {err}") from err
    # ffmpeg exits 0 on some failures that produce nothing. The file is
    # the answer, not the exit code - the same rule the renderer applies
    # to its own output.
    if not target.exists() or target.stat().st_size <= 0:
        raise VideoProxyError(f"{what} wurde nicht geschrieben")
    return target.stat().st_size


__all__ = [
    "ANALYSIS_FPS",
    "ANALYSIS_HEIGHT",
    "MAX_ANALYSIS_SECONDS",
    "MAX_RENDER_SECONDS",
    "RENDER_HEIGHT",
    "VideoProxyError",
    "analysis_args",
    "async_cut_analysis_proxy",
    "async_cut_render_proxy",
    "render_args",
]
