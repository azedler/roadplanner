"""Download view for the durable trip-PDF library.

A download ticket lives five minutes, so a summary built earlier in the day
is gone by the time anyone wants it again. Generated PDFs are therefore also
written to the export library and served here - the same arrangement the
video library already uses, including its authentication reasoning: the
download is a plain link click, and the mobile companion app performs it
without attaching an auth token. The capability IS the filename, a random
128-bit uuid4 hex generated server-side, validated against a strict pattern,
never listed anywhere, and only handed out through authenticated panel
actions.
"""

from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import ValidationError
from .trip_pdf_export import PDF_FILENAME_RE

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_URL = "/api/roadplanner/trip_pdf_library/{filename}"


def _runtime(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


class RoadplannerTripPdfLibraryView(HomeAssistantView):
    """Serve a stored trip PDF by its generated filename."""

    url = DOWNLOAD_URL
    name = "api:roadplanner:trip_pdf_library"
    # The unguessable uuid4-hex filename is the access token - see module
    # docstring. Session auth would break the companion app's plain-link
    # download.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, filename: str) -> web.Response:
        if not PDF_FILENAME_RE.match(filename):
            raise web.HTTPBadRequest(text="Ungültiger Dateiname")
        runtime = _runtime(self.hass)
        library_dir = getattr(runtime.trip_pdf, "library_dir", None)
        if library_dir is None:
            raise web.HTTPNotFound(text="Die PDF-Bibliothek ist nicht eingerichtet")
        path = library_dir / filename
        if not path.is_file():
            raise web.HTTPNotFound(
                text="Das PDF wurde nicht gefunden oder ist abgelaufen"
            )
        pdf_bytes = await self.hass.async_add_executor_job(path.read_bytes)
        return web.Response(
            body=pdf_bytes,
            headers={
                "Content-Type": "application/pdf",
                "Content-Disposition": 'attachment; filename="reise.pdf"',
                "Cache-Control": "private, no-store, max-age=0",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )


def async_register_trip_pdf_library_view(hass: HomeAssistant) -> None:
    """Register the trip-PDF library HTTP view once per Home Assistant process."""
    marker = f"{DOMAIN}_trip_pdf_library_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerTripPdfLibraryView(hass))
    hass.data[marker] = True
