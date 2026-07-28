"""Ticket-protected download view for generated trip-summary videos."""

from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

DOWNLOAD_URL = "/api/roadplanner/trip_video/{token}"


def _runtime(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


class RoadplannerTripVideoView(HomeAssistantView):
    """Serve one already-generated trip video through a short-lived ticket."""

    url = DOWNLOAD_URL
    name = "api:roadplanner:trip_video"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, token: str) -> web.Response:
        try:
            runtime = _runtime(self.hass)
            video_bytes = await runtime.trip_video.async_resolve_ticket(token)
        except RoadplannerError as err:
            raise web.HTTPUnauthorized(text=str(err)) from err
        return web.Response(
            body=video_bytes,
            headers={
                "Content-Type": "video/mp4",
                "Content-Disposition": 'attachment; filename="reise.mp4"',
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )


def async_register_trip_video_view(hass: HomeAssistant) -> None:
    """Register the trip-video HTTP view once per Home Assistant process."""
    marker = f"{DOMAIN}_trip_video_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerTripVideoView(hass))
    hass.data[marker] = True
