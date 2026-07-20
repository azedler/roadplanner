"""Short-lived authenticated redirects for private OneDrive media."""

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


class RoadplannerMediaView(HomeAssistantView):
    """Redirect a short-lived Roadplanner URL to a Graph pre-auth URL."""

    # The random, short-lived HMAC URL is the authorization mechanism.  An
    # <img> element cannot attach Home Assistant's bearer token, so requiring
    # normal HA auth here would make thumbnails fail in the panel.
    requires_auth = False

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.url = f"/api/roadplanner/media/{kind}/{{trip_id}}/{{media_id}}"
        self.name = f"api:roadplanner:media:{kind}"

    async def get(self, request: web.Request, trip_id: str, media_id: str) -> web.StreamResponse:
        hass: HomeAssistant = request.app["hass"]
        experience = _experience(hass)
        if experience is None:
            raise web.HTTPServiceUnavailable()
        token = str(request.query.get("token") or "")
        if not experience.validate_token(trip_id, media_id, self.kind, token):
            raise web.HTTPUnauthorized()
        try:
            target = await experience.async_media_redirect_url(trip_id, media_id, self.kind)
        except RoadplannerError as err:
            _LOGGER.debug("Roadplanner media redirect rejected: %s", err)
            raise web.HTTPNotFound() from err
        response = web.HTTPFound(target)
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def async_register_experience_views(hass: HomeAssistant) -> None:
    """Register media redirect views once."""
    marker = f"{DOMAIN}_experience_views_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerMediaView("thumbnail"))
    hass.http.register_view(RoadplannerMediaView("original"))
    hass.data[marker] = True
