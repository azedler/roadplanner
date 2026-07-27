"""Short-lived authenticated redirect for Google Places photos.

Mirrors experience_http.py's OneDrive media redirect view. The panel never
receives a raw Google photo URL for a saved gallery image - only this
signed redirect URL, built from the durable `photo_name` reference. Google's
own image bytes are fetched by the browser directly from Google on each
view; Roadplanner never stores or proxies the bytes themselves.
"""

from __future__ import annotations

import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import RoadplannerError

_LOGGER = logging.getLogger(__name__)


def _experience(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        return None
    runtime = next(iter(runtimes.values()))
    return getattr(runtime, "experience", None)


class RoadplannerGooglePhotoView(HomeAssistantView):
    """Redirect a short-lived Roadplanner URL to a freshly resolved Google photo URL."""

    # The random, short-lived HMAC URL is the authorization mechanism, same
    # as the OneDrive media view - an <img> element cannot attach Home
    # Assistant's bearer token.
    requires_auth = False
    url = "/api/roadplanner/google_photo/{photo_name:.*}"
    name = "api:roadplanner:google_photo"

    async def get(self, request: web.Request, photo_name: str) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        experience = _experience(hass)
        if experience is None:
            raise web.HTTPServiceUnavailable()
        token = str(request.query.get("token") or "")
        if not experience.validate_google_photo_token(photo_name, token):
            raise web.HTTPUnauthorized()
        try:
            target = await experience.async_google_photo_redirect_url(photo_name)
        except RoadplannerError as err:
            _LOGGER.debug("Roadplanner Google photo redirect rejected: %s", err)
            raise web.HTTPNotFound() from err
        response = web.HTTPFound(target)
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def async_register_google_photo_view(hass: HomeAssistant) -> None:
    """Register the Google photo redirect view once per HA process."""
    marker = f"{DOMAIN}_google_photo_view_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerGooglePhotoView())
    hass.data[marker] = True
