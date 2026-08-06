"""Serve a locally stored crew/vehicle portrait.

Unlike the PDF and video downloads, this one KEEPS Home Assistant session
authentication: a portrait is displayed inside the panel by a logged-in
browser, never fetched by the companion app as a plain link, so there is no
reason to weaken the check. The filename is still a content hash, so it
leaks nothing about who is in the picture.

Portraits are immutable per filename - the source photo and the crop are
both part of the hash - so they can be cached hard. A new crop is a new
name, never a stale image.
"""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import ValidationError

PORTRAIT_URL = "/api/roadplanner/crew_portrait/{filename}"


def _runtime(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


class RoadplannerCrewPortraitView(HomeAssistantView):
    """Serve one stored portrait image."""

    url = PORTRAIT_URL
    name = "api:roadplanner:crew_portrait"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, filename: str) -> web.Response:
        runtime = _runtime(self.hass)
        store = getattr(runtime, "crew_portraits", None)
        if store is None:
            raise web.HTTPNotFound(text="Porträts sind nicht verfügbar")
        path = await self.hass.async_add_executor_job(store.path_for, filename)
        if path is None:
            raise web.HTTPNotFound(text="Das Porträt wurde nicht gefunden")
        data = await self.hass.async_add_executor_job(path.read_bytes)
        return web.Response(
            body=data,
            headers={
                "Content-Type": "image/jpeg",
                # Immutable by construction: the crop is part of the name.
                "Cache-Control": "private, max-age=604800, immutable",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            },
        )


def async_register_crew_portrait_view(hass: HomeAssistant) -> None:
    marker = f"{DOMAIN}_crew_portrait_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerCrewPortraitView(hass))
    hass.data[marker] = True
