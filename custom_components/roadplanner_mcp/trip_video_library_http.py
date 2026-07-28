"""Authenticated download view for the durable trip-video library.

Unlike the PDF export's short-lived ticket-protected view, a generated
video is written to disk (see trip_video_export.py's module docstring for
why) and served here under normal Home Assistant session authentication -
there is no ticket to expire, so the link in the "video ready" persistent
notification keeps working whenever the user is logged in, even long after
the export finished.
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
    requires_auth = True

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
