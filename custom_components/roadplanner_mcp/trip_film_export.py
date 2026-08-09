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
from .crew_portraits import portrait_key
from .character_assets import build_character_package
from .trip_film_crew import build_crew_package
from .trip_film_music import (
    build_music_package,
    build_music_timeline_package,
    list_tracks,
)
from .trip_film_plan import (
    FilmPlanError,
    allocate_photos,
    build_scene_plan,
    plan_seconds,
)
from .trip_map_builder import MapContextBuilder
from .trip_day_render_package import RenderPackageError

_LOGGER = logging.getLogger(__name__)

# A reserved NAME for "the score that was generated for this trip".
# Deliberately not a path and deliberately not a real filename: it is
# compared for equality before the ordinary folder lookup, so nothing
# sent from a browser gains a new way to name a file.
GENERATED_MUSIC = "__generated__"


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
        crew: Any = None,
        crew_portraits: Any = None,
        characters: Any = None,
        music_timeline: Any = None,
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
        # Names and faces for the opening. Read here rather than through
        # the story layer, which deliberately knows crew names only - a
        # portrait has no business in a description of a journey.
        self._crew = crew
        self._crew_portraits = crew_portraits
        self._characters = characters
        # Read-only by construction: what has already been generated and
        # when it plays. Not the music service - see `_async_music`.
        self._music_timeline = music_timeline

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
        # The map belongs in the answer to "what would be in the film?".
        # It was left out, and the first thing a real trip asked was
        # exactly this: the film came back without a map and there was no
        # way to tell whether the reason was the trip's data or the
        # version that rendered it. Built by the same builder the render
        # uses, so the count cannot disagree with what gets drawn.
        map_context = await self._map.async_build(trip_id, manifest)
        return {
            "trip_title": (manifest.get("trip") or {}).get("title") or "",
            "manifest_content_hash": manifest.get("content_hash") or "",
            "chapter_count": len(chapters),
            "chapters_with_photos": with_photos,
            "chapters_without_photos": len(chapters) - with_photos,
            "photos_per_chapter": per_chapter,
            "planned_photo_count": planned,
            "story_sources": manifest.get("story_sources") or {},
            "mapped_chapters": (map_context or {}).get("chapter_count", 0),
            "map_has_ferry": bool((map_context or {}).get("has_ferry")),
            # Days whose line is a straight guess between stops because no
            # route was ever calculated. Named, because "the map looks
            # wrong" and "the map is honest about not knowing" are two
            # different findings.
            "estimated_map_chapters": (map_context or {}).get("estimated_chapters", 0),
            "film_seconds": _estimated_seconds(
                chapters, budget, map_context, manifest.get("narrative")
            ),
        }

    async def async_estimate_seconds(self, trip_id: str) -> float:
        """How long a film of this trip would run, before it is built.

        The music has to be ordered before the film exists, and a
        soundtrack laid out for the wrong length is either short or a
        loop. So the same planner that times the real render is run over
        the manifest and the photo budget - no bytes, no downloads.

        It is an estimate in exactly one respect: a picture that turns
        out to be unfetchable shortens its day. That moves the total by
        seconds, which is why the music plan rounds before it decides
        anything (see ``trip_film_music_service``).
        """
        manifest = await self._story_context.async_manifest(trip_id)
        chapters = manifest.get("chapters") or []
        if not chapters:
            return 0.0
        budget = allocate_photos(
            chapters, total_budget=MAX_FILM_IMAGES, per_chapter_cap=MAX_PHOTOS_PER_CHAPTER
        )
        map_context = await self._map.async_build(trip_id, manifest)
        return _estimated_seconds(
            chapters, budget, map_context, manifest.get("narrative")
        )

    async def async_music_options(self) -> list[dict[str, Any]]:
        """What could be played under a film. Names and sizes only."""
        return await self._hass.async_add_executor_job(list_tracks)

    async def async_submit(self, trip_id: str, *, music: str = "") -> dict[str, Any]:
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
        crew, crew_files = await self._async_crew()
        characters, character_files = await self._async_characters()
        music_entry, music_files = await self._async_music(trip_id, music)

        try:
            package, files = build_film_package(
                job_id="00000000-0000-0000-0000-000000000000",
                manifest=manifest,
                photos_by_chapter=photos_by_chapter,
                map_context=map_context,
                crew=crew,
                crew_files={**crew_files, **music_files, **character_files},
                music=music_entry,
                characters=characters,
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
            "crew_count": len((crew or {}).get("members") or []),
            "music": (music_entry or {}).get("title", ""),
            "character_assets": len((characters or {}).get("assets") or []),
        }


    async def _async_characters(self) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
        """Confirmed illustrations of the camper, as bytes.

        Fail-open like the crew: no store, no confirmed asset, or an
        unreadable file all mean the film draws the camper instead. The
        drawing is a worse picture and a complete film, and a missing
        illustration must never be the reason an export fails.

        Only *confirmed* assets are read. A candidate somebody generated
        and has not looked at yet is not something a film may quietly
        start using.
        """
        store = self._characters
        if store is None:
            return None, {}
        try:
            found = await self._hass.async_add_executor_job(store.confirmed)
        except Exception as err:  # noqa: BLE001 - a broken store is not a broken film
            _LOGGER.debug("Figurenbilder nicht lesbar: %s", type(err).__name__)
            return None, {}
        assets: list[dict[str, Any]] = []
        for entry in found:
            data = await self._hass.async_add_executor_job(store.read, entry["filename"])
            if not data:
                continue
            assets.append({"kind": entry["kind"], "variant": entry["variant"], "data": data})
        if not assets:
            return None, {}
        return await self._hass.async_add_executor_job(build_character_package, assets)

    async def _async_music(
        self, trip_id: str, music: str
    ) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
        """The soundtrack: a chosen file, or the generated score.

        `GENERATED_MUSIC` is a reserved NAME, not a path, and it is the
        only value handled here rather than looked up in the folder -
        the ordinary path still matches whatever was sent against the
        folder listing before anything is opened.

        The generated score is read through a callable that can only
        report what already exists. The exporter is deliberately not
        given the music service: rendering a film must have no route to
        a paid call, not even an accidental one, and the strongest form
        of that rule is not having the method in reach.
        """
        if str(music or "") != GENERATED_MUSIC:
            return await self._hass.async_add_executor_job(build_music_package, music)
        if self._music_timeline is None:
            _LOGGER.info("KI-Musik angefragt, aber kein Musikdienst verdrahtet")
            return None, {}
        seconds = await self.async_estimate_seconds(trip_id)
        timeline = await self._music_timeline(trip_id, seconds)
        if not timeline:
            # Nothing was generated for this film yet. A film without
            # music is a complete film; a render that fails because
            # somebody has not paid for a soundtrack is not.
            _LOGGER.info("Für diese Reise ist noch keine KI-Musik erzeugt worden")
            return None, {}
        return await self._hass.async_add_executor_job(
            build_music_timeline_package, timeline
        )

    async def _async_crew(self) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
        """Names and locally stored portraits, prepared for the film.

        Fail-open throughout: a crew that cannot be read means a film
        without a crew scene, never a film that does not render. And the
        portraits are read from disk as bytes rather than referenced by
        URL - the crew portrait route is guarded only by an unguessable
        filename, which must not be copied into a package written to a
        shared folder.
        """
        if self._crew is None:
            return None, {}
        try:
            registry = await self._crew.async_panel_payload()
        except Exception:  # noqa: BLE001 - decoration must not fail a film
            _LOGGER.debug("Crew für den Film nicht lesbar", exc_info=True)
            return None, {}

        members: list[dict[str, Any]] = []
        for person in registry.get("people") or []:
            if not isinstance(person, dict) or not person.get("active", True):
                continue
            members.append(
                {
                    "name": str(person.get("name") or "").strip(),
                    "portrait": await self._async_portrait_bytes(person),
                }
            )
        members = [member for member in members if member["name"]]
        if not members:
            return None, {}
        return await self._hass.async_add_executor_job(build_crew_package, members)

    async def _async_portrait_bytes(self, person: dict[str, Any]) -> bytes | None:
        """The stored portrait file for one person, or nothing.

        The filename is derived the same way the portrait service derives
        it, from the person, the source photo and the crop. Deriving it
        rather than parsing a URL means no URL is ever formed here at
        all - the bearer secret cannot leak from a string that does not
        exist.
        """
        store = self._crew_portraits
        if store is None:
            return None
        filename = portrait_key(
            str(person.get("id") or ""),
            str(person.get("reference_media_id") or ""),
            person.get("reference_crop"),
            kind="person",
        )
        if not filename:
            return None
        try:
            return await self._hass.async_add_executor_job(store.read, filename)
        except Exception:  # noqa: BLE001
            return None

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


def _estimated_seconds(
    chapters: list[dict[str, Any]],
    budget: dict[str, int],
    map_context: dict[str, Any] | None,
    narrative: Any = None,
) -> float:
    """Run the real scene planner over what the film would contain.

    Deliberately the same function the render uses rather than an
    average-seconds-per-picture rule of thumb. A rule of thumb drifts
    away from the film the moment any timing constant moves, and then
    the soundtrack is laid out for a film nobody makes.
    """
    if not chapters:
        return 0.0
    planned = []
    for chapter in chapters:
        wanted = int(budget.get(str(chapter.get("chapter_id") or ""), 0))
        entry = dict(chapter)
        # The planner counts pictures; it never looks inside them.
        entry["images"] = [""] * max(0, wanted)
        planned.append(entry)
    try:
        plan = build_scene_plan(
            trip={},
            chapters=planned,
            narrative=narrative if isinstance(narrative, dict) else {},
            map_context=map_context,
            # The closing collage is a scene like any other and costs
            # real seconds; leaving it out here would under-run the
            # estimate by exactly its length.
            outro_photos=(
                [{"path": "x"}, {"path": "y"}]
                if sum(len(entry["images"]) for entry in planned) >= 2
                else []
            ),
        )
    except FilmPlanError:
        return 0.0
    return plan_seconds(plan)


__all__ = ["GENERATED_MUSIC", "TripFilmExporter"]
