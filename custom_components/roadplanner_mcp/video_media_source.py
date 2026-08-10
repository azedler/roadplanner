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

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .roadplanner import ValidationError

_LOGGER = logging.getLogger(__name__)

# Streamed rather than read whole: a phone's video is hundreds of
# megabytes and a Home Assistant box is not a workstation.
CHUNK_BYTES = 1024 * 1024

# What one recording may weigh before it is refused. Not a judgement
# about the film - a guard so a single misfiled file cannot fill /share.
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024


class VideoMediaSource:
    """Turns a stored media record into a file on disk."""

    def __init__(self, hass: Any, onedrive: Any) -> None:
        self._hass = hass
        self._onedrive = onedrive

    async def async_download_to(self, record: dict[str, Any], target: Path) -> int:
        """Fetch the recording behind this record. Returns bytes written."""
        item_id = str((record or {}).get("provider_item_id") or "").strip()
        if not item_id:
            raise ValidationError("Für dieses Video ist keine Quelldatei hinterlegt")

        url = await self._onedrive.async_download_url(item_id)
        session = async_get_clientsession(self._hass)
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        async with session.get(url) as response:
            if response.status != 200:
                raise ValidationError(
                    f"Das Video konnte nicht geladen werden (HTTP {response.status})"
                )
            with target.open("wb") as handle:
                async for chunk in response.content.iter_chunked(CHUNK_BYTES):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        # Stop mid-stream rather than after: the point of a
                        # ceiling is to not have written the thing.
                        handle.close()
                        target.unlink(missing_ok=True)
                        raise ValidationError("Das Video ist zu groß zum Verarbeiten")
                    handle.write(chunk)
        if written <= 0:
            target.unlink(missing_ok=True)
            raise ValidationError("Das Video kam leer an")
        return written


__all__ = ["CHUNK_BYTES", "MAX_DOWNLOAD_BYTES", "VideoMediaSource"]
