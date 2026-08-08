"""Serve a locally stored crew/vehicle portrait.

This view used to require Home Assistant's session authentication, and
the docstring argued for it: "a portrait is displayed inside the panel by
a logged-in browser". The premise was wrong, and expensively so. **A
browser does not attach a bearer token to an ``<img src>`` request.** The
panel's own JavaScript sends one; an image tag never does.

So every portrait answered 401. Two consequences, and the second one is
the reason this is a bug report rather than a cosmetic note:

- the pictures stayed blank, which is what a broken image looks like;
- Home Assistant's ban middleware treats a 401 from **any** endpoint as a
  failed login. A crew page with four people is four failed logins per
  visit, and a few visits ban the household's own IP address out of its
  own Home Assistant (live report).

Every other file-serving view in this integration had already learned
this - the PDF, the video library, the media redirects - and this one was
the single exception. The capability is therefore the filename, as it is
there: a SHA-1 over the person id, the media id and the crop rectangle,
none of which is reachable without an authenticated panel payload, and
none of which appears in a URL anybody else sees.

Portraits are immutable per filename - the source photo and the crop are
both part of the hash - so they can be cached hard. A new crop is a new
name, never a stale image.

What this route is, stated plainly
----------------------------------

An unguessable filename is a **bearer secret, not a session**. Anyone
holding the URL can fetch the picture, and it does not expire. That is an
acceptable trade for a photograph shown in a panel the household already
has access to, and it is the same trade every other file-serving view
here makes - but it only holds while the URL stays inside the panel.

So a portrait URL must never travel into:

- a model context (the story layer sends crew **names** and nothing else,
  which is why ``story_context_builder`` builds its crew dictionary from
  ``name`` alone);
- a render package (the film carries trip photos by generated path and
  has no crew section at all);
- a log line;
- exported story data.

Those four are checked in ``tests/test_story_layer_contract.py`` rather
than left to memory. Widening this into real per-request authentication
would be a larger change than this route justifies today, and nothing in
the current failure modes calls for it - but the reason it is acceptable
is written here so that a future reader can disagree with the reasoning
instead of guessing at it.
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
    # The unguessable filename is the capability - see the module
    # docstring. Session auth here does not protect the picture, it only
    # makes the browser fail to load it and gets the household banned.
    requires_auth = False

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
