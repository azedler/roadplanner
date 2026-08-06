"""Keep every crew member's and vehicle's portrait stored locally.

Live request: "Ich fände es auch sinnvoll wenn die Bilder der Crew permanent
lokal gespeichert werden."

The job is small and deliberately lazy: whenever the panel or an export asks
for the crew, any portrait that is missing on disk is fetched once from the
assigned trip photo, cropped, shrunk and written. From then on the face
comes off local disk - no OneDrive round trip, no expiring Graph URL, and
nothing to re-render if that photo is later moved or deleted in the cloud.

Fail-open throughout: a portrait that cannot be fetched right now simply
stays missing and the caller falls back to the live reference. A face is
worth a retry, never an error page.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .crew_portraits import CrewPortraitStore, portrait_key, portrait_url, render_portrait
from .roadplanner import RoadplannerError
from .trip_export_photos import async_fetch_media_photo

_LOGGER = logging.getLogger(__name__)


class CrewPortraitService:
    """Materialise crew portraits on disk and annotate crew payloads."""

    def __init__(
        self,
        hass: HomeAssistant,
        experience: Any,
        store: CrewPortraitStore,
        *,
        media_cache: Any = None,
    ) -> None:
        self.hass = hass
        self.experience = experience
        self.store = store
        self.media_cache = media_cache
        self._lock = asyncio.Lock()

    def annotate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add ``portrait_url`` to every entry that already has a file.

        Pure and synchronous: a missing portrait yields an empty URL and the
        caller keeps its existing live-reference behaviour.
        """
        for kind, key in (("person", "people"), ("vehicle", "vehicles")):
            for entry in payload.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                filename = portrait_key(
                    str(entry.get("id") or ""),
                    str(entry.get("reference_media_id") or ""),
                    entry.get("reference_crop"),
                    kind=kind,
                )
                entry["portrait_url"] = (
                    portrait_url(filename) if filename and self.store.exists(filename) else ""
                )
        return payload

    async def async_refresh(
        self, payload: dict[str, Any], trip_id: str
    ) -> dict[str, Any]:
        """Fetch and store any portrait that is not on disk yet."""
        if not trip_id:
            return self.annotate(payload)
        wanted: list[tuple[str, str, str, Any]] = []
        for kind, key in (("person", "people"), ("vehicle", "vehicles")):
            for entry in payload.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                media_id = str(entry.get("reference_media_id") or "")
                entity_id = str(entry.get("id") or "")
                filename = portrait_key(
                    entity_id, media_id, entry.get("reference_crop"), kind=kind
                )
                if filename and not self.store.exists(filename):
                    wanted.append((filename, media_id, entity_id, entry.get("reference_crop")))
        if wanted:
            # One at a time: this runs off a panel load, and a crew of five
            # must not open five Graph downloads at once.
            async with self._lock:
                session = async_get_clientsession(self.hass)
                for filename, media_id, _entity_id, crop in wanted:
                    if self.store.exists(filename):
                        continue
                    await self._async_store_one(session, trip_id, filename, media_id, crop)
        return self.annotate(payload)

    async def _async_store_one(
        self, session: Any, trip_id: str, filename: str, media_id: str, crop: Any
    ) -> None:
        try:
            photo = await async_fetch_media_photo(
                session,
                self.experience,
                trip_id,
                {"id": media_id},
                cache=self.media_cache,
                hass=self.hass,
            )
        except (RoadplannerError, KeyError, TypeError) as err:
            _LOGGER.debug("Crew portrait fetch failed: %s", type(err).__name__)
            return
        if not photo:
            return
        rendered = await self.hass.async_add_executor_job(render_portrait, photo, crop)
        if not rendered:
            return
        await self.hass.async_add_executor_job(self.store.write, filename, rendered)

    async def async_prune(self, payload: dict[str, Any]) -> int:
        """Drop portraits nothing refers to any more."""
        keep: set[str] = set()
        for kind, key in (("person", "people"), ("vehicle", "vehicles")):
            for entry in payload.get(key) or []:
                if not isinstance(entry, dict):
                    continue
                filename = portrait_key(
                    str(entry.get("id") or ""),
                    str(entry.get("reference_media_id") or ""),
                    entry.get("reference_crop"),
                    kind=kind,
                )
                if filename:
                    keep.add(filename)
        return await self.hass.async_add_executor_job(self.store.prune, keep)
