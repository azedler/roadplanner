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


#: How the same URL is asked for a download rather than for playback.
#: A query parameter rather than a second route, so an existing link
#: keeps working and the player simply stops asking for it.
DOWNLOAD_QUERY = "download"


def _wants_download(request: web.Request) -> bool:
    return str(request.query.get(DOWNLOAD_QUERY, "")).strip().lower() in {"1", "true", "yes"}


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

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        """Serve the film as a file, so a browser can seek inside it.

        `FileResponse`, not a `Response` with the bytes in it. The old
        handler read the whole film with `path.read_bytes()` and answered
        every request with 200 and the entire file:

        - **Seeking was impossible.** No `Accept-Ranges`, no 206, so
          `video.currentTime = 26` snapped back to 0 until the file had
          finished downloading. On a 584 MB film that is the whole film
          before minute eight - and the player, the thing this endpoint
          exists for, runs on a wall-mounted tablet with no patience.
        - **The film sat in Home Assistant's memory.** 584 MB in one
          `bytes` object, on a box that has a household to run.
        - **`no-store` re-downloaded it every time** the player was
          opened.

        aiohttp answers `Range` with 206 and `Content-Range` by itself,
        streams with sendfile, and holds nothing.

        The disposition is the other half: the same URL is the player's
        `<video src>` AND the download link, and `attachment` on a
        `<video>` is meaningless at best. It is now sent only when a
        download is what was actually asked for.
        """
        if not VIDEO_FILENAME_RE.match(filename):
            raise web.HTTPBadRequest(text="Ungültiger Dateiname")
        runtime = _runtime(self.hass)
        path = runtime.trip_video.library_dir / filename
        if not await self.hass.async_add_executor_job(path.is_file):
            raise web.HTTPNotFound(text="Das Video wurde nicht gefunden oder ist abgelaufen")
        headers = {
            "Content-Type": "video/mp4",
            # Private, because the filename is the capability - but
            # cacheable, because the file behind a given filename never
            # changes: a new render gets a new name.
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        if _wants_download(request):
            headers["Content-Disposition"] = 'attachment; filename="reise.mp4"'
        return web.FileResponse(path, headers=headers)


def async_register_trip_video_library_view(hass: HomeAssistant) -> None:
    """Register the trip-video library HTTP view once per Home Assistant process."""
    marker = f"{DOMAIN}_trip_video_library_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerTripVideoLibraryView(hass))
    hass.data[marker] = True
