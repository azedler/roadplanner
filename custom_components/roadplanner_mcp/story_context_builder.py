"""Read a real trip and describe it as a TravelStoryManifest.

This is the only place that knows where a trip's narrative material lives.
Everything it reads, it reads through the paths the existing exports
already use - the assistant payload for the roadbook, the experience
payload for media - so the manifest cannot describe a different trip than
the PDF and the video see.

It reads and never writes. No summary is generated, no route looked up, no
photo downloaded, no provider called. A builder that quietly triggered a
Gemini call would make "deterministic" untrue and would cost money every
time a cache missed.

Caching
-------

The manifest is a pure function of the roadbook revision and the media
records. The revision is therefore the cache key: while it is unchanged,
the description cannot have changed either. ``content_hash`` then answers
the cheaper question - whether a rebuild actually produced anything
different - so a consumer can skip work even when the cache was missed.

The cache lives in memory and is deliberately small: one entry per trip.
Losing it on a Home Assistant restart costs one rebuild, and a rebuild is
a few milliseconds of sorting.

Overrides
---------

A chapter title or story may be replaced by hand. The replacement is read
from the day's own ``details``:

- ``story_title_override`` - replaces the chapter title;
- ``story_override`` - replaces the chapter story.

They live there rather than in a new store because a day already has a
supported write path, so a story editor can be built later without any
migration - and because an override that travels with its day cannot be
orphaned by a day being removed. What is NOT here is an editor; this
module only applies what it finds.

The directed edit
-----------------

A stored Gemini editing pass is applied the same way and with the same
rule: found and applied, never produced. It arrives from its own sidecar
store keyed by the manifest's ``story_context_hash``, and it loses to a
human override in every chapter where both exist.

Two consequences worth stating. The cache key is no longer the roadbook
revision alone - an edit can land or be discarded while the trip stands
still - so it now covers the edit and the crew names too. And the module
remains provider-free: nothing in here can start an edit, which is what
keeps a panel refresh from ever costing money.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .canonical_day import canonical_roadbook_stops
from .media_intelligence import select_media_highlights
from .roadplanner import RoadplannerError, ValidationError
from .travel_story_manifest import (
    MEDIA_ROLE_DAY_COVER,
    MEDIA_ROLE_HIGHLIGHT,
    MEDIA_ROLE_STOP_COVER,
    MEDIA_ROLE_TRIP_COVER,
    build_chapter,
    build_manifest,
    clean_line,
)
from .film_photo_allocation import PHOTO_CAPS_BY_IMPORTANCE, allocate_trip
from .media_curation import group_series
from .trip_summaries import SUMMARY_DETAIL_KEY

_LOGGER = logging.getLogger(__name__)

OVERRIDE_TITLE_KEY = "story_title_override"
OVERRIDE_STORY_KEY = "story_override"

# How many media a chapter carries. A description, not an album: a renderer
# that wants everything can still read the media records themselves.
#
# The ceiling comes from the film's own caps rather than from a number
# typed here, because these two are the same fact seen from two sides: a
# chapter that carried fewer than a major highlight may show would cut
# the film's most important day without anything saying so.
MEDIA_PER_CHAPTER = max(PHOTO_CAPS_BY_IMPORTANCE.values())


def _media_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    """Order media by when they were taken, then by id.

    The id is the tiebreaker rather than a nicety: the curation below
    collapses duplicates by iteration order, so feeding it an unordered
    list would make the manifest depend on how the store happened to
    return its records.
    """
    return (
        str(item.get("taken_at") or item.get("created_at") or ""),
        str(item.get("id") or ""),
    )


class StoryContextBuilder:
    """Turn one trip into a manifest, and remember it while it is valid."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        *,
        direction_store: Any = None,
        crew: Any = None,
    ) -> None:
        self._hass = hass
        self._manager = manager
        self._experience = experience
        # Read-only here, both of them. The builder applies an edit it
        # finds and never makes one, which is what keeps "deterministic"
        # true: given the same roadbook and the same stored edit, this
        # produces the same bytes, and no provider is ever called.
        self._direction_store = direction_store
        self._crew = crew
        self._cache: dict[str, dict[str, Any]] = {}

    async def async_manifest(self, trip_id: str, *, force: bool = False) -> dict[str, Any]:
        """The manifest for a trip, rebuilt only when the trip has moved."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Reisegeschichte fehlt die Reise-ID")
        context = await self._async_context(trip_id)
        # The revision alone is not the key any more: an edit can land or
        # be discarded without the roadbook moving at all, and a crew
        # member can be renamed in a different store entirely.
        fingerprint = (
            context["revision"],
            (context["direction"] or {}).get("story_context_hash"),
            len((context["direction"] or {}).get("chapters") or {}),
            _crew_fingerprint(context["crew"]),
        )
        cached = self._cache.get(trip_id)
        if (
            not force
            and cached is not None
            and cached.get("fingerprint") == fingerprint
            and cached["manifest"].get("manifest_version")
        ):
            return cached["manifest"]
        manifest = _build(context)
        self._cache[trip_id] = {"fingerprint": fingerprint, "manifest": manifest}
        _LOGGER.debug(
            "Reisegeschichte für %s gebaut: %s Kapitel, Hash %s",
            trip_id,
            len(manifest["chapters"]),
            manifest["content_hash"][:12],
        )
        return manifest

    def invalidate(self, trip_id: str = "") -> None:
        """Drop the cache. Called when a trip changes under us."""
        if trip_id:
            self._cache.pop(str(trip_id), None)
        else:
            self._cache.clear()

    # --- reading --------------------------------------------------------

    async def _async_context(self, trip_id: str) -> dict[str, Any]:
        payload = await self._manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError("Die Reise für die Reisegeschichte ist nicht ladbar")
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        days = [
            day
            for day in (payload.get("days", {}) or {}).get("days", []) or []
            if isinstance(day, dict) and str(day.get("id") or "")
        ]

        try:
            state = await self._experience.async_panel_payload(trip_id, days=days)
        except RoadplannerError:
            # A trip without the experience sidecar still has a story; it
            # just has no photos in it. Failing here would make the whole
            # description depend on an optional subsystem.
            state = {}

        media = sorted(
            (item for item in state.get("media") or [] if isinstance(item, dict)),
            key=_media_sort_key,
        )
        return {
            "trip_id": trip_id,
            "trip": trip,
            "revision": summary.get("revision"),
            "days": days,
            "media": media,
            "day_curations": state.get("day_curations") or {},
            "direction": await self._async_direction(trip_id),
            "crew": await self._async_crew(),
        }

    async def _async_direction(self, trip_id: str) -> dict[str, Any] | None:
        """The stored editing pass, if there is one.

        Read rather than produced. A builder that could trigger an edit
        would make every panel refresh a possible Gemini bill, which is
        exactly the thing this whole caching design exists to prevent.
        """
        if self._direction_store is None:
            return None
        try:
            return await self._hass.async_add_executor_job(
                self._direction_store.load, trip_id
            )
        except RoadplannerError:
            # A story is decoration on a trip. Losing it must not make the
            # trip unreadable.
            return None

    async def _async_crew(self) -> dict[str, Any] | None:
        if self._crew is None:
            return None
        try:
            registry = await self._crew.async_panel_payload()
        except (RoadplannerError, AttributeError, TypeError):
            return None
        people = [
            str(person.get("name") or "").strip()
            for person in registry.get("people") or []
            if isinstance(person, dict) and person.get("active", True)
        ]
        vehicles = [
            str(vehicle.get("name") or "").strip()
            for vehicle in registry.get("vehicles") or []
            if isinstance(vehicle, dict) and vehicle.get("active", True)
        ]
        people = [name for name in people if name]
        vehicles = [name for name in vehicles if name]
        if not people and not vehicles:
            return None
        # Names only - see the comment in build_manifest. The rest of a
        # crew record (notes, portraits, generated summaries) stays where
        # it is.
        return {"people": people, "vehicle": vehicles[0] if vehicles else ""}


def _crew_fingerprint(crew: dict[str, Any] | None) -> str:
    if not crew:
        return ""
    return "|".join([*crew.get("people", []), str(crew.get("vehicle") or "")])


def _film_allocation(
    days: list[dict[str, Any]],
    media_by_day: dict[str, list[dict[str, Any]]],
    day_curations: Any,
    directed_chapters: Any,
) -> dict[str, list[str]]:
    """Which of a day's curated pictures have earned a place in the film.

    Importance is a CEILING here, never an allowance: a day gets as many
    pictures as it has good, non-redundant ones and no more than its rank
    permits. The rule itself lives in `film_photo_allocation`; this only
    assembles what it reads.

    A day without stored analyses returns nothing and keeps the whole
    curated selection. That is deliberate: a bar applied to scores that
    do not exist would silently empty a chapter, and a film with weaker
    pictures beats a film with none.
    """
    if not isinstance(day_curations, dict):
        return {}
    directed = directed_chapters if isinstance(directed_chapters, dict) else {}
    entries: list[dict[str, Any]] = []
    for day in days:
        day_id = str(day.get("id") or "")
        record = day_curations.get(day_id)
        if not day_id or not isinstance(record, dict):
            continue
        analyses = record.get("analyses") or {}
        if not analyses:
            continue
        brief = record.get("brief") or {}
        edit = directed.get(day_id) if isinstance(directed.get(day_id), dict) else {}
        entries.append(
            {
                "chapter_id": day_id,
                "importance": (edit or {}).get("importance"),
                "curated": list(record.get("media_ids") or []),
                "analyses": analyses,
                "series_by_media": {
                    media_id: group["series_id"]
                    for group in group_series(media_by_day.get(day_id) or [])
                    for media_id in group.get("media_ids") or []
                },
                "pinned": list(record.get("pinned") or []),
                "excluded": list(record.get("excluded") or []),
                "must_cover": list(brief.get("must_cover") or []),
                "alternatives": brief.get("alternatives") or {},
            }
        )
    if not entries:
        return {}
    result = allocate_trip(entries)
    return {
        day_id: list(found["media_ids"])
        for day_id, found in (result.get("days") or {}).items()
    }


def _build(context: dict[str, Any]) -> dict[str, Any]:
    """Assemble the manifest. Pure once the reading is done."""
    media_by_day: dict[str, list[dict[str, Any]]] = {}
    media_by_stop: dict[str, list[dict[str, Any]]] = {}
    trip_cover = ""
    for item in context["media"]:
        if item.get("is_trip_cover") and not trip_cover:
            trip_cover = str(item.get("id") or "")
        day_id = str(item.get("linked_day_id") or "")
        stop_id = str(item.get("linked_stop_id") or "")
        if day_id:
            media_by_day.setdefault(day_id, []).append(item)
        if stop_id:
            media_by_stop.setdefault(stop_id, []).append(item)

    day_curations = context.get("day_curations") or {}
    direction = context.get("direction") or {}
    directed_chapters = direction.get("chapters") or {}

    # Which pictures the film carries, decided here because this is the
    # only place that holds all three things it needs at once: what the
    # curation chose and scored, which pictures belong to one burst, and
    # what the editing pass said the day was worth. The alternative -
    # deciding it in the export - would need the analyses shipped there,
    # or a second, weaker rule beside the real one.
    allocation = _film_allocation(
        context["days"], media_by_day, day_curations, directed_chapters
    )

    chapters = []
    for index, day in enumerate(context["days"]):
        day_id = str(day.get("id") or "")
        stops = [
            stop for stop in canonical_roadbook_stops(day) if isinstance(stop, dict)
        ]
        chapters.append(
            _chapter(
                day=day,
                day_id=day_id,
                index=index,
                stops=stops,
                media_by_day=media_by_day,
                media_by_stop=media_by_stop,
                curation=day_curations.get(day_id)
                if isinstance(day_curations, dict)
                else None,
                film_media_ids=allocation.get(day_id),
                # An edit for a day that no longer exists simply never
                # finds a chapter to attach to.
                direction=directed_chapters.get(day_id),
            )
        )

    trip = context["trip"]
    return build_manifest(
        trip_id=context["trip_id"],
        title=str(trip.get("title") or trip.get("name") or ""),
        start_date=str(trip.get("start_date") or ""),
        end_date=str(trip.get("end_date") or ""),
        revision=context["revision"],
        chapters=chapters,
        trip_facts={"distance_km": trip.get("total_distance_km")},
        trip_cover_media_id=trip_cover,
        narrative=direction.get("narrative"),
        crew=context.get("crew"),
    )


def _chapter(
    *,
    day: dict[str, Any],
    day_id: str,
    index: int,
    stops: list[dict[str, Any]],
    media_by_day: dict[str, list[dict[str, Any]]],
    media_by_stop: dict[str, list[dict[str, Any]]],
    curation: dict[str, Any] | None = None,
    film_media_ids: list[str] | None = None,
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stop_ids = [str(stop.get("id") or "") for stop in stops if str(stop.get("id") or "")]
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in media_by_day.get(day_id, []) + [
        item for stop_id in stop_ids for item in media_by_stop.get(stop_id, [])
    ]:
        identifier = str(item.get("id") or "")
        if identifier and identifier not in seen:
            seen.add(identifier)
            pool.append(item)

    # The curation decides which photographs tell this day; this is only
    # where the answer is read. It used to be decided here, by a function
    # that scored file sizes - which is how a day at a moose park reached
    # the film without a moose.
    #
    # No curation yet (a trip that has never been curated, or a day added
    # since) falls back to the old local ranking. A film with weaker
    # pictures is better than a film with none.
    by_id = {str(item.get("id") or ""): item for item in pool if str(item.get("id") or "")}
    chosen: list[dict[str, Any]] = []
    # What the film carries, when the allocation had scores to read. It is
    # a subset of the curation in the curation's own order - the selection
    # decides WHICH pictures, never in what sequence a day reads.
    source = list(film_media_ids or []) or list(
        (curation or {}).get("media_ids") or [] if isinstance(curation, dict) else []
    )
    for media_id in source:
        item = by_id.get(str(media_id))
        if item is not None and item not in chosen:
            chosen.append(item)
    if not chosen:
        chosen, _stats = select_media_highlights(pool, limit=MEDIA_PER_CHAPTER)
    hero_id = str((curation or {}).get("hero_media_id") or "")
    media = [
        {
            "media_id": str(item.get("id") or ""),
            "role": "hero" if str(item.get("id") or "") == hero_id else _role(item),
            "stop_id": str(item.get("linked_stop_id") or "") or None,
        }
        for item in chosen[:MEDIA_PER_CHAPTER]
    ]

    details = day.get("details") if isinstance(day.get("details"), dict) else {}
    return build_chapter(
        day_id=day_id,
        index=index,
        date=str(day.get("date") or ""),
        title=str(day.get("title") or ""),
        stops=[
            {
                "stop_id": str(stop.get("id") or ""),
                "name": str(stop.get("name") or ""),
                "kind": str(stop.get("type") or ""),
                "arrival_time": str(stop.get("arrival_time") or ""),
            }
            for stop in stops
        ],
        media=media,
        facts={
            "day_number": index + 1,
            "distance_km": day.get("distance_km"),
            "duration_minutes": day.get("drive_minutes", day.get("duration_minutes")),
            # What the day HAS, not what this chapter carries.
            "photo_count": len(pool),
        },
        stored_summary=str(details.get(SUMMARY_DETAIL_KEY) or ""),
        overrides={
            "title": details.get(OVERRIDE_TITLE_KEY),
            "story": details.get(OVERRIDE_STORY_KEY),
        },
        captions=[
            {"media_id": str(item.get("id") or ""), "text": str(item.get("caption") or "")}
            for item in chosen[:MEDIA_PER_CHAPTER]
            if clean_line(item.get("caption"), limit=200)
        ],
        map_hint=_map_hint(stops, stop_ids),
        direction=direction,
    )


def _role(item: dict[str, Any]) -> str:
    if item.get("is_trip_cover"):
        return MEDIA_ROLE_TRIP_COVER
    if item.get("is_day_cover"):
        return MEDIA_ROLE_DAY_COVER
    if item.get("is_cover"):
        return MEDIA_ROLE_STOP_COVER
    return MEDIA_ROLE_HIGHLIGHT


def _map_hint(stops: list[dict[str, Any]], stop_ids: list[str]) -> dict[str, Any] | None:
    """Say whether a map could be drawn, and from which stops.

    No coordinates travel in the manifest. Drawing the map is a separate
    piece of work with its own decisions - tiles, provider, attribution -
    and putting the numbers here would be the first half of it, made
    before anyone chose the second.
    """
    if not stop_ids:
        return None
    has_coordinates = any(
        isinstance(stop.get("location"), dict)
        and isinstance(stop["location"].get("lat"), (int, float))
        and isinstance(stop["location"].get("lon"), (int, float))
        for stop in stops
    )
    return {"kind": "day_route", "stop_ids": stop_ids, "has_coordinates": has_coordinates}


__all__ = ["OVERRIDE_STORY_KEY", "OVERRIDE_TITLE_KEY", "StoryContextBuilder"]
