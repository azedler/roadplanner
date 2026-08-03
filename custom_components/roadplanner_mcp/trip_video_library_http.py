"""Download view for the durable trip-video library.

A generated video is written to disk (see trip_video_export.py's module
docstring for why) and served here WITHOUT Home Assistant session
authentication - exactly like the PDF ticket view: the download is
triggered as a plain link click, and the mobile companion app performs
that download without attaching an auth token, which turned every video
into a 17-byte "401: Unauthorized" file (live report). The capability IS
the filename: a random 128-bit uuid4 hex generated server-side, validated
against a strict pattern, never listed anywhere, only handed out through
authenticated panel actions and the owner's notification. Guessing it is
as hard as guessing a PDF ticket token.
"""

from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import ValidationError
from .trip_video_export import VIDEO_FILENAME_RE

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_URL = "/api/roadplanner/trip_video_library/{filename}"


def _runtime(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


class RoadplannerTripVideoLibraryView(HomeAssistantView):
    """Serve a stored trip video by its generated filename."""

    url = DOWNLOAD_URL
    name = "api:roadplanner:trip_video_library"
    # The unguessable uuid4-hex filename is the access token - see module
    # docstring. Session auth would break the companion app's plain-link
    # download (401 body saved as the "video").
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, filename: str) -> web.Response:
        if not VIDEO_FILENAME_RE.match(filename):
            raise web.HTTPBadRequest(text="Ungültiger Dateiname")
        runtime = _runtime(self.hass)
        path = runtime.trip_video.library_dir / filename
        if not path.is_file():
            raise web.HTTPNotFound(text="Das Video wurde nicht gefunden oder ist abgelaufen")
        video_bytes = await self.hass.async_add_executor_job(path.read_bytes)
        return web.Response(
            body=video_bytes,
            headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": 'attachment; filename="reise.mp4"',
                "Cache-Control": "private, no-store, max-age=0",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )


def async_register_trip_video_library_view(hass: HomeAssistant) -> None:
    """Register the trip-video library HTTP view once per Home Assistant process."""
    marker = f"{DOMAIN}_trip_video_library_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerTripVideoLibraryView(hass))
    hass.data[marker] = True
