"""Film a whole trip from its TravelStoryManifest.

The first real consumer of the manifest, and the point of it: if the
description cannot carry a film, the description is wrong, and this is how
that becomes visible.

The rule that shapes every line here is that **the manifest is the only
story source**. Titles, sentences, facts and the choice of pictures all
come from it. This module fetches bytes for media ids the manifest already
picked, shrinks them, and arranges them - it never decides what a day is
about and never reaches back into the roadbook for something the manifest
left out.

Where the manifest is thin, the thinness travels. A day with no photos
arrives at the renderer as a chapter with zero images, and the composition
draws that as a gap. That is the finding, not a defect to hide.

What this is not: a replacement for the existing exports. The PDF and the
ffmpeg video are untouched, still built their own way, and this runs beside
them as an experiment with its own action, its own artefact name and its
own limits.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .roadplanner import RoadplannerError, ValidationError
from .trip_export_photos import async_fetch_media_photo
from .trip_film_package import (
    MAX_FILM_IMAGES,
    MAX_PHOTOS_PER_CHAPTER,
    build_film_package,
    shrink_film_photo,
)
from .trip_film_plan import allocate_photos
from .trip_map_builder import MapContextBuilder
from .trip_day_render_package import RenderPackageError

_LOGGER = logging.getLogger(__name__)


class TripFilmExporter:
    """Assemble a whole-trip film package and hand it to the renderer app."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        story_context: Any,
        renderer_app: Any,
        *,
        media_cache: Any = None,
    ) -> None:
        self._hass = hass
        self._manager = manager
        self._experience = experience
        self._story_context = story_context
        self._renderer_app = renderer_app
        self._media_cache = media_cache
        # The manifest carries no coordinates and is not going to start.
        # Where the trip happened is asked for separately, by chapter id,
        # from the canonical routing data.
        self._map = MapContextBuilder(hass, manager)

    async def async_preview(self, trip_id: str) -> dict[str, Any]:
        """What a film of this trip would contain, without building it.

        Cheap enough to call whenever the panel opens: it reads the cached
        manifest and counts. Nothing is downloaded.
        """
        manifest = await self._story_context.async_manifest(trip_id)
        chapters = manifest.get("chapters") or []
        budget = allocate_photos(
            chapters, total_budget=MAX_FILM_IMAGES, per_chapter_cap=MAX_PHOTOS_PER_CHAPTER
        )
        with_photos = sum(1 for chapter in chapters if (chapter.get("media") or []))
        planned = sum(budget.values())
        per_chapter = max(budget.values()) if budget else 0
        return {
            "trip_title": (manifest.get("trip") or {}).get("title") or "",
            "manifest_content_hash": manifest.get("content_hash") or "",
            "chapter_count": len(chapters),
            "chapters_with_photos": with_photos,
            "chapters_without_photos": len(chapters) - with_photos,
            "photos_per_chapter": per_chapter,
            "planned_photo_count": planned,
            "story_sources": manifest.get("story_sources") or {},
        }

    async def async_submit(self, trip_id: str) -> dict[str, Any]:
        """Build the package for the whole trip and queue the render."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für den Reisefilm fehlt die Reise-ID")

        manifest = await self._story_context.async_manifest(trip_id)
        chapters = manifest.get("chapters") or []
        if not chapters:
            raise ValidationError("Diese Reise hat noch keine Kapitel")

        media_by_id = await self._async_media_records(trip_id)
        # Weighted, not flat. A transfer day and the reason for the whole
        # trip used to get the same three pictures, which is most of why
        # film v0 felt like a contact sheet.
        budget = allocate_photos(
            chapters, total_budget=MAX_FILM_IMAGES, per_chapter_cap=MAX_PHOTOS_PER_CHAPTER
        )
        session = async_get_clientsession(self._hass)

        photos_by_chapter: dict[str, list[bytes]] = {}
        missing_media = 0
        for chapter in chapters:
            prepared: list[bytes] = []
            # The manifest already chose and ordered these. Taking the first
            # N is the only decision made here, and it is the budget.
            wanted = budget.get(str(chapter.get("chapter_id") or ""), 0)
            for entry in (chapter.get("media") or [])[:wanted]:
                record = media_by_id.get(str(entry.get("media_id") or ""))
                if record is None:
                    missing_media += 1
                    continue
                raw = await async_fetch_media_photo(
                    session,
                    self._experience,
                    trip_id,
                    record,
                    cache=self._media_cache,
                    hass=self._hass,
                )
                if not raw:
                    missing_media += 1
                    continue
                shrunk = await self._hass.async_add_executor_job(shrink_film_photo, raw)
                if shrunk:
                    prepared.append(shrunk)
                else:
                    missing_media += 1
            photos_by_chapter[str(chapter.get("chapter_id") or "")] = prepared

        map_context = await self._map.async_build(trip_id, manifest)

        try:
            package, files = build_film_package(
                job_id="00000000-0000-0000-0000-000000000000",
                manifest=manifest,
                photos_by_chapter=photos_by_chapter,
                map_context=map_context,
            )
        except RenderPackageError as err:
            raise ValidationError(str(err)) from err

        submitted = await self._renderer_app.async_submit_trip_film_job(
            package=package,
            files=files,
            title=(manifest.get("trip") or {}).get("title") or "Reisefilm",
        )
        empty_chapters = sum(1 for value in photos_by_chapter.values() if not value)
        _LOGGER.debug(
            "Reisefilm für %s eingereicht: %s Kapitel, %s Bilder, %s Byte",
            trip_id,
            len(chapters),
            len(files),
            submitted["package_bytes"],
        )
        return {
            **submitted,
            "chapter_count": len(chapters),
            "photo_count": len(files),
            "chapters_without_photos": empty_chapters,
            # Reported rather than hidden: a photo the manifest listed but
            # that could not be fetched is a gap the film will show.
            "unavailable_media": missing_media,
            "manifest_content_hash": manifest.get("content_hash") or "",
            "mapped_chapters": (map_context or {}).get("chapter_count", 0),
            "has_ferry": bool((map_context or {}).get("has_ferry")),
        }

    async def _async_media_records(self, trip_id: str) -> dict[str, dict[str, Any]]:
        """The media records for the ids the manifest refers to.

        The manifest carries ids by design; the bytes live in the
        experience sidecar. This is the join, and it is the only place the
        film looks anything up.
        """
        try:
            state = await self._experience.async_panel_payload(trip_id)
        except RoadplannerError:
            return {}
        return {
            str(item.get("id") or ""): item
            for item in state.get("media") or []
            if isinstance(item, dict) and str(item.get("id") or "")
        }


__all__ = ["TripFilmExporter"]
