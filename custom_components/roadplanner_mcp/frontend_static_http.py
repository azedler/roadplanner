"""Serve the Roadplanner panel's frontend files with cache headers that
always force revalidation.

Home Assistant's `async_register_static_paths` (used here previously) either
sends a far-future Cache-Control (``cache_headers=True``) or no explicit
Cache-Control header at all (``cache_headers=False``, what this integration
used). Without an explicit header, browsers - especially the mobile
Companion app's WebView - apply heuristic caching to any file with no query
string, and that is every ES module the panel entry file imports
(``./features/pitches.js``, ``../lib/core-helpers.js``, ...). Only the entry
file itself is cache-busted, via the ``?v=<version>`` query parameter
panel_custom appends to module_url - so after a HACS update, a user could
load a freshly-fetched entry file that still imports STALE cached
submodules from the previous release, with no error and no visible sign
anything is wrong. That is exactly what happened once: the Stellplätze tab
kept showing 4.10.0-era markup and copy after upgrading to 4.11.0.

This view sends ``Cache-Control: no-cache`` on every response instead. That
does not prevent caching, but forces a conditional revalidation
(If-None-Match/If-Modified-Since) on every load - handled by aiohttp's
FileResponse itself - so a changed submodule is always picked up without
needing every internal import to carry its own cache-busting query string.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class RoadplannerFrontendStaticView(HomeAssistantView):
    """Serve one file from the panel's frontend directory, never cached silently."""

    requires_auth = False  # Matches the previous static-path registration - a
    # panel JS/CSS bundle is public-ish, like Home Assistant's own frontend assets.

    def __init__(self, url: str, name: str, frontend_dir: Path) -> None:
        self.url = url
        self.name = name
        self._frontend_dir = frontend_dir.resolve()

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        try:
            target = (self._frontend_dir / filename).resolve()
        except (OSError, ValueError) as err:
            raise web.HTTPNotFound() from err
        if target != self._frontend_dir and self._frontend_dir not in target.parents:
            raise web.HTTPForbidden()
        if not target.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(
            target,
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )


def async_register_frontend_static_view(
    hass: HomeAssistant,
    *,
    url_path: str,
    frontend_dir: Path,
) -> None:
    """Register the always-revalidating frontend file view once per process."""
    hass.http.register_view(
        RoadplannerFrontendStaticView(
            f"{url_path}/{{filename:.+}}",
            f"api:roadplanner:frontend_static:{url_path}",
            frontend_dir,
        )
    )
