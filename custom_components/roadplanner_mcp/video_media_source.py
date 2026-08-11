"""Getting one recording onto disk, and nothing else.

A tiny adapter with one job, kept apart from the service that uses it for
two reasons. It is the only place a video's bytes cross the network, so
the rule "the original is a copy that gets deleted" has somewhere to
live; and a test can hand the service a local file without a token, a
session or a fixture server.

The original is never modified. It is downloaded, cut from, and removed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .roadplanner import ValidationError

_LOGGER = logging.getLogger(__name__)

# Streamed rather than read whole: a phone's video is hundreds of
# megabytes and a Home Assistant box is not a workstation.
CHUNK_BYTES = 1024 * 1024

# What one recording may weigh before it is refused. Not a judgement
# about the film - a guard so a single misfiled file cannot fill /share.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024

# Every other slow step in the video path states its own limit: ffmpeg
# has one, the provider call has one. This one did not, and it is the
# step most likely to stop moving - a phone video over a domestic uplink
# from a cloud drive. Without it, a stalled stream is indistinguishable
# from a slow one for as long as the box stays up, the run never returns,
# and the "a run is already going" guard then locks the trip out for
# good. Stated per stage rather than as one total, because a large file
# on a slow line is legitimate and a socket that went quiet is not.
CONNECT_TIMEOUT_SECONDS = 60
# Longest gap between two chunks before the stream counts as stalled.
STALL_TIMEOUT_SECONDS = 120


class VideoMediaSource:
    """Turns a stored media record into a file on disk."""

    def __init__(self, hass: Any, onedrive: Any) -> None:
        self._hass = hass
        self._onedrive = onedrive

    @staticmethod
    def _write(handle: Any, chunk: bytes) -> None:
        handle.write(chunk)

    async def async_download_to(self, record: dict[str, Any], target: Path) -> int:
        """Fetch the recording behind this record. Returns bytes written."""
        item_id = str((record or {}).get("provider_item_id") or "").strip()
        if not item_id:
            raise ValidationError("Für dieses Video ist keine Quelldatei hinterlegt")

        url = await self._onedrive.async_download_url(item_id)
        session = async_get_clientsession(self._hass)
        await self._hass.async_add_executor_job(
            lambda: target.parent.mkdir(parents=True, exist_ok=True)
        )
        written = 0
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=CONNECT_TIMEOUT_SECONDS,
            sock_read=STALL_TIMEOUT_SECONDS,
        )
        # Raised as this module's own error rather than let out as an
        # asyncio.TimeoutError: the caller catches RoadplannerError and
        # loses ONE recording to it, which is the whole point. An
        # unfamiliar exception type would have ended the run instead, so
        # one stalled download would cost every recording after it.
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    raise ValidationError(
                        f"Das Video konnte nicht geladen werden (HTTP {response.status})"
                    )
                # Opened and written through the executor. Home Assistant
                # reported this one itself: "Detected blocking call to open
                # ... inside the event loop". A few hundred megabytes
                # arriving a megabyte at a time is a few hundred blocking
                # writes on the loop that also runs the rest of the house.
                handle = await self._hass.async_add_executor_job(
                    target.open, "wb"
                )
                try:
                    async for chunk in response.content.iter_chunked(CHUNK_BYTES):
                        written += len(chunk)
                        if written > MAX_DOWNLOAD_BYTES:
                            # Stop mid-stream rather than after: the point
                            # of a ceiling is to not have written the thing.
                            raise ValidationError(
                                "Das Video ist zu groß zum Verarbeiten"
                            )
                        await self._hass.async_add_executor_job(
                            self._write, handle, chunk
                        )
                finally:
                    await self._hass.async_add_executor_job(handle.close)
        except TimeoutError as err:
            target.unlink(missing_ok=True)
            raise ValidationError(
                f"Der Download blieb stehen (nach {written // (1024 * 1024)} MB) - "
                "die Aufnahme wird übersprungen"
            ) from err
        except aiohttp.ClientError as err:
            target.unlink(missing_ok=True)
            raise ValidationError(
                f"Das Video konnte nicht geladen werden: {str(err)[:120]}"
            ) from err
        except ValidationError:
            # Includes the size ceiling, whose whole point is to NOT have
            # written the thing - the partial file goes either way, and
            # saying so once here beats remembering it at each raise.
            target.unlink(missing_ok=True)
            raise
        if written <= 0:
            target.unlink(missing_ok=True)
            raise ValidationError("Das Video kam leer an")
        return written


__all__ = ["CHUNK_BYTES", "MAX_DOWNLOAD_BYTES", "VideoMediaSource"]
