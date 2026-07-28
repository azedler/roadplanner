"""Isolated ffmpeg subprocess execution for the trip video export.

Video encoding runs tens of seconds to minutes - far too long to occupy a
thread in Home Assistant's small, shared executor pool (that pattern is
meant for short, bounded CPU work like trip_pdf.py's reportlab render).
ffmpeg is an external binary, not a Python library, so it is invoked via
``asyncio.create_subprocess_exec`` and awaited without ever touching the
executor pool.

ffmpeg cannot be declared in manifest.json's "requirements" (that field is
pip packages only, not system binaries) - callers must check
``ffmpeg_available()`` up front and fail fast with a clear message before
doing any other export work.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from .roadplanner import RoadplannerError

_LOGGER = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 2_000


def ffmpeg_available() -> bool:
    """Return whether an ``ffmpeg`` binary is present on PATH."""
    return shutil.which("ffmpeg") is not None


async def async_run_ffmpeg(args: list[str], *, timeout_seconds: float) -> None:
    """Run ``ffmpeg`` with the given arguments, raising on failure or timeout.

    ``args`` excludes the ``ffmpeg`` executable name itself. Always passes
    ``-y`` implicitly is the caller's responsibility (kept explicit here so
    the full invocation is visible in one place for anyone debugging a
    render).
    """
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RoadplannerError(
            "Die Videoerstellung hat zu lange gedauert und wurde abgebrochen"
        ) from None
    if process.returncode != 0:
        tail = (stderr or b"").decode("utf-8", errors="replace")[-_STDERR_TAIL_CHARS:]
        _LOGGER.warning(
            "ffmpeg exited with code %s: %s", process.returncode, tail
        )
        raise RoadplannerError(
            "Die Videoerstellung ist mit einem Fehler fehlgeschlagen"
        )
