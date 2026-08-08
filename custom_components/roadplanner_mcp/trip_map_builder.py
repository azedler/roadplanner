"""Fetch the canonical geography for the chapters a manifest already chose.

This is the boundary the map engine is allowed to cross and the only one.
The story manifest says *which* chapters exist and in what order; it says
nothing about where they are, and it must not start to. So the film asks
here instead, with the identifiers the manifest already carries -
``chapter_id`` and the stop ids under it - and this layer looks the
answer up in the same roadbook the rest of Roadplanner reads.

Two rules make that safe:

- **No second geography.** Nothing here computes, guesses or stores a
  route. It reads ``details.routing.segments`` - written by the router,
  the same data the route map in the panel draws - and reduces it. If the
  roadbook is wrong about where the trip went, the film is wrong in
  exactly the same way, which is the correct behaviour.
- **No writing and no fetching.** One payload read, then pure functions.
  A map that cost a network call per chapter would make a re-render
  expensive and non-deterministic; this one costs a dictionary lookup.

A chapter whose day has vanished, or whose day has no usable coordinate,
simply has no map. The film then draws that chapter without one rather
than inventing a location for it.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .canonical_day import canonical_roadbook_stops
from .roadplanner import RoadplannerError
from .trip_map_context import build_map_context, chapter_map

_LOGGER = logging.getLogger(__name__)


class MapContextBuilder:
    """Turn a manifest's chapter list into the trip's drawable geography."""

    def __init__(self, hass: HomeAssistant, manager: Any) -> None:
        self._hass = hass
        self._manager = manager

    async def async_build(self, trip_id: str, manifest: dict[str, Any]) -> dict[str, Any] | None:
        """The map context for this manifest, or None when there is none.

        Never raises for a missing or unroutable trip. A film without a
        map is a film; a film that fails to render because the roadbook
        had no coordinates is a bug.
        """
        chapters = manifest.get("chapters") or []
        if not chapters:
            return None
        days = await self._async_days(trip_id)
        if not days:
            return None
        entries = []
        for chapter in chapters:
            chapter_id = str(chapter.get("chapter_id") or "")
            day = days.get(chapter_id)
            if day is None:
                continue
            stops = [
                stop for stop in canonical_roadbook_stops(day) if isinstance(stop, dict)
            ]
            entries.append(
                {
                    "chapter_id": chapter_id,
                    "index": int(chapter.get("index") or 0),
                    "map": chapter_map(day, stops),
                }
            )
        context = build_map_context(entries)
        if context is not None:
            _LOGGER.debug(
                "Kartenkontext für %s: %s Kapitel, %s Punkte, Fähre: %s",
                trip_id,
                context["chapter_count"],
                context["point_count"],
                context["has_ferry"],
            )
        return context

    async def _async_days(self, trip_id: str) -> dict[str, dict[str, Any]]:
        """The trip's days by id, from the same payload the manifest used."""
        try:
            payload = await self._manager.async_get_assistant_payload(trip_id)
        except RoadplannerError:
            return {}
        if str(payload.get("selected_trip_id") or "") != trip_id:
            return {}
        raw = (payload.get("days") or {}).get("days") or []
        return {
            str(day.get("id") or ""): day
            for day in raw
            if isinstance(day, dict) and str(day.get("id") or "")
        }


__all__ = ["MapContextBuilder"]
