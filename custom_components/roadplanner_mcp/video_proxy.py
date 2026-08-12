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

import asyncio
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

# What the film shows. It used to be a flat 720 with the reasoning "that
# is what the film renders at" - true when there was one size, and the
# same sentence became wrong the moment a profile could ask for 1440.
# A clip played full frame at 1440p was being scaled 2x by the renderer.
#
# Derived from the profile now, and NEVER above the recording's own
# height: a phone video is not improved by being enlarged before it is
# handed over, it only costs bytes and decode time.
RENDER_HEIGHT = 720
RENDER_CRF = 23


def render_height(profile_id: str = "", source_height: int = 0) -> int:
    """How many lines the film's copy of a clip should have.

    Full frame, because a clip that is shown small is still cut at the
    size it might be shown large - the scene plan decides where a clip
    goes, and it is not consulted here. That asymmetry with photographs
    is deliberate: a clip costs one cut either way, while pictures are
    two hundred and sixty files where the difference is the package.
    """
    from .render_profiles import render_profile  # noqa: PLC0415 - avoids a cycle

    wanted = int(render_profile(profile_id)["height"])
    if source_height and source_height > 0:
        return max(ANALYSIS_HEIGHT, min(wanted, int(source_height)))
    return wanted

# An effectively unbounded width, because the limit that matters is the
# height. It has to be stated: `force_original_aspect_ratio` treats w and
# h as a BOX and recomputes both - so a `-2` width, which is ffmpeg's way
# of saying "auto, and even", is discarded and the result can land on an
# odd number. That is not a rounding detail: libx264 refuses an odd
# dimension outright.
MAX_PROXY_WIDTH = 4096

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
        # `force_divisible_by=2` is the whole point of this line. With
        # `scale=-2:360:force_original_aspect_ratio=decrease` a portrait
        # phone recording came out 202x359, and libx264 answered "height
        # not divisible by 2" and wrote nothing - which is exactly what
        # happened on the live system: eleven windows, every one of them
        # a portrait recording, every one refused before a single frame
        # was encoded.
        f"scale=w={MAX_PROXY_WIDTH}:h={ANALYSIS_HEIGHT}"
        f":force_original_aspect_ratio=decrease:force_divisible_by=2"
        f",fps={ANALYSIS_FPS}",
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


def render_args(
    source: Path,
    target: Path,
    *,
    start: float,
    end: float,
    height: int = RENDER_HEIGHT,
) -> list[str]:
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
        # Same trap, same fix: the film's clips are cut with this call,
        # so a portrait recording would have been refused here too - one
        # step later, in the middle of a film export.
        f"scale=w={MAX_PROXY_WIDTH}:h={max(ANALYSIS_HEIGHT, int(height))}"
        f":force_original_aspect_ratio=decrease:force_divisible_by=2",
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
    source: Path,
    target: Path,
    *,
    start: float,
    end: float,
    height: int = RENDER_HEIGHT,
) -> int:
    """Cut the piece the film plays. Returns its size in bytes."""
    return await _async_cut(
        render_args(source, target, start=start, end=end, height=height),
        target,
        timeout=RENDER_TIMEOUT,
        what="Renderproxy",
    )


async def async_probe_shape(path: Path) -> tuple[int, int]:
    """The dimensions of a file as it will actually be played.

    Measured rather than carried over from the library record, because
    those two disagree exactly where it matters. A phone held upright
    whose camera writes a rotation matrix stores 1920x1080 and displays
    1080x1920; ffmpeg applies the matrix when it cuts, so the proxy is
    genuinely upright while the record still says landscape. Copying the
    record into the render package therefore described the clip as the
    opposite shape of the file beside it.

    Zero on failure rather than a guess: a dimension nobody could read is
    better absent than invented, and the renderer already treats zero as
    "decide from the file".
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except (OSError, TimeoutError):
        return 0, 0
    if process.returncode != 0:
        return 0, 0
    parts = (stdout or b"").decode("utf-8", errors="replace").strip().split(",")
    try:
        return int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return 0, 0


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
    "async_probe_shape",
    "MAX_PROXY_WIDTH",
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
