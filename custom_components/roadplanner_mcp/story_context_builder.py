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
from .trip_summaries import SUMMARY_DETAIL_KEY

_LOGGER = logging.getLogger(__name__)

OVERRIDE_TITLE_KEY = "story_title_override"
OVERRIDE_STORY_KEY = "story_override"

# How many media a chapter carries. A description, not an album: a renderer
# that wants everything can still read the media records themselves.
MEDIA_PER_CHAPTER = 8


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

    def __init__(self, hass: HomeAssistant, manager: Any, experience: Any) -> None:
        self._hass = hass
        self._manager = manager
        self._experience = experience
        self._cache: dict[str, dict[str, Any]] = {}

    async def async_manifest(self, trip_id: str, *, force: bool = False) -> dict[str, Any]:
        """The manifest for a trip, rebuilt only when the trip has moved."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Reisegeschichte fehlt die Reise-ID")
        context = await self._async_context(trip_id)
        cached = self._cache.get(trip_id)
        if (
            not force
            and cached is not None
            and cached.get("revision") == context["revision"]
            and cached["manifest"].get("manifest_version")
        ):
            return cached["manifest"]
        manifest = _build(context)
        self._cache[trip_id] = {"revision": context["revision"], "manifest": manifest}
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
    )


def _chapter(
    *,
    day: dict[str, Any],
    day_id: str,
    index: int,
    stops: list[dict[str, Any]],
    media_by_day: dict[str, list[dict[str, Any]]],
    media_by_stop: dict[str, list[dict[str, Any]]],
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

    # The same curation the panel shows and the exports use. Sharing it is
    # the point: a manifest that picked different photos than the album
    # would be a second opinion about the same day.
    highlights, _stats = select_media_highlights(pool, limit=MEDIA_PER_CHAPTER)
    media = [
        {
            "media_id": str(item.get("id") or ""),
            "role": _role(item),
            "stop_id": str(item.get("linked_stop_id") or "") or None,
        }
        for item in highlights
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
            for item in highlights
            if clean_line(item.get("caption"), limit=200)
        ],
        map_hint=_map_hint(stops, stop_ids),
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
