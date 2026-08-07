"""Hand one real trip day to the renderer app, and nothing more.

This is the narrow proof the mini export exists for: that Roadplanner can
put **real** travel data through the shared directory in a controlled way
and get a validated MP4 back. It is deliberately not an export feature.
There is no library, no ticket, no download URL, no notification - all of
which the PDF and ffmpeg video exports already have, and none of which
this needs in order to answer the question.

What it does, in order:

1. reads the trip the panel already has, picks **one** day by id;
2. collects that day's stops and up to five of its photos, through the
   same read-only helpers the existing exports use, so photo selection
   cannot drift between them;
3. shrinks and strips the photos and builds the package
   (``trip_day_render_package``);
4. writes the package and then the job into the exchange directory;
5. returns the job id. Polling is the panel's job, exactly as it is for
   the test render.

What it deliberately does not do: generate a summary, call a provider,
fetch a map, look up a distance. Every value in the package is one
Roadplanner already had. A mini export that quietly triggered a Gemini
call or a routing lookup would be measuring something other than the data
path it is supposed to prove.

The existing ffmpeg video pipeline is untouched by all of this. It is a
separate module with a separate entry point and shares only the read-only
photo helper.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .canonical_day import canonical_roadbook_stops
from .renderer_app_client import RendererAppClient
from .roadplanner import RoadplannerError, ValidationError
from .trip_day_render_package import (
    MAX_IMAGES,
    RenderPackageError,
    build_package,
)
from .trip_export_photos import async_fetch_day_photos

_LOGGER = logging.getLogger(__name__)


class TripDayMiniExporter:
    """Assemble one day's render package and put it into the channel."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        renderer_app: RendererAppClient,
        *,
        media_cache: Any = None,
    ) -> None:
        self._hass = hass
        self._manager = manager
        self._experience = experience
        self._renderer_app = renderer_app
        self._media_cache = media_cache

    async def async_days(self, trip_id: str) -> list[dict[str, Any]]:
        """The days that can be exported, with why they can or cannot.

        The panel needs to offer a choice, and a day without a usable
        photo cannot produce this video. Saying so in the list beats
        letting someone pick it and read an error twenty seconds later.
        """
        context = await self._async_context(trip_id)
        days: list[dict[str, Any]] = []
        for position, day in enumerate(context["days"], start=1):
            day_id = str(day.get("id") or "")
            stops = [
                stop
                for stop in canonical_roadbook_stops(day)
                if isinstance(stop, dict)
            ]
            photo_count = len(context["media_by_day"].get(day_id, [])) + sum(
                len(context["media_by_stop"].get(str(stop.get("id") or ""), []))
                for stop in stops
            )
            days.append(
                {
                    "day_id": day_id,
                    "number": position,
                    "date": str(day.get("date") or ""),
                    "title": str(day.get("title") or ""),
                    "stop_count": len(stops),
                    "photo_count": photo_count,
                    # Counted, not downloaded: this list is built every
                    # time the panel opens and must stay cheap.
                    "exportable": photo_count > 0,
                }
            )
        return days

    async def async_submit(self, trip_id: str, day_id: str) -> dict[str, Any]:
        """Build the package for one day and queue the render job."""
        day_id = str(day_id or "").strip()
        if not day_id:
            raise ValidationError("Für den Mini-Export fehlt der Reisetag")

        context = await self._async_context(trip_id)
        days = context["days"]
        selected = next(
            (day for day in days if str(day.get("id") or "") == day_id), None
        )
        if selected is None:
            raise ValidationError("Der gewählte Reisetag gehört nicht zu dieser Reise")

        stops = [
            stop for stop in canonical_roadbook_stops(selected) if isinstance(stop, dict)
        ]
        session = async_get_clientsession(self._hass)
        photos = await async_fetch_day_photos(
            session,
            self._experience,
            trip_id,
            stops,
            context["media_by_stop"],
            context["destination_galleries"],
            max_photos=MAX_IMAGES,
            day_media=context["media_by_day"].get(day_id),
            cache=self._media_cache,
            hass=self._hass,
        )
        if not photos:
            raise ValidationError(
                "Für diesen Reisetag konnte kein Foto geladen werden - "
                "ohne Bild ergibt der Mini-Export keinen Nachweis"
            )

        try:
            # Decoding, rotating, scaling and re-encoding up to five photos
            # is CPU-bound Pillow work and would stall the event loop.
            package, images = await self._hass.async_add_executor_job(
                _build,
                context["trip_title"],
                selected,
                stops,
                photos,
                [str(day.get("id") or "") for day in days].index(day_id) + 1,
                len(days),
            )
        except RenderPackageError as err:
            raise ValidationError(str(err)) from err

        submitted = await self._renderer_app.async_submit_trip_day_job(
            package=package,
            images=images,
            title=str(selected.get("title") or "Reisetag"),
        )
        _LOGGER.debug(
            "Mini-Export für Tag %s eingereicht (%s Bilder, %s Byte)",
            day_id,
            submitted["image_count"],
            submitted["package_bytes"],
        )
        return {
            **submitted,
            "day_id": day_id,
            "day_title": str(selected.get("title") or ""),
            "day_date": str(selected.get("date") or ""),
            "stop_count": len(package["stops"]),
        }

    # --- shared reading -------------------------------------------------

    async def _async_context(self, trip_id: str) -> dict[str, Any]:
        """Everything read from Roadplanner, in one place and read-only."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für den Mini-Export fehlt die Reise-ID")
        payload = await self._manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError("Die Reise für den Mini-Export ist nicht ladbar")
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
            state = {}
        media_by_stop: dict[str, list[dict[str, Any]]] = {}
        media_by_day: dict[str, list[dict[str, Any]]] = {}
        for item in state.get("media") or []:
            if not isinstance(item, dict):
                continue
            stop_id = str(item.get("linked_stop_id") or "")
            if stop_id:
                media_by_stop.setdefault(stop_id, []).append(item)
            day_id = str(item.get("linked_day_id") or "")
            if day_id:
                media_by_day.setdefault(day_id, []).append(item)
        return {
            "trip_title": str(trip.get("title") or trip.get("name") or "Roadplanner"),
            "days": days,
            "media_by_stop": media_by_stop,
            "media_by_day": media_by_day,
            "destination_galleries": state.get("destination_galleries")
            if isinstance(state.get("destination_galleries"), dict)
            else {},
        }


def _build(
    trip_title: str,
    day: dict[str, Any],
    stops: list[dict[str, Any]],
    photos: list[bytes],
    day_number: int,
    day_count: int,
) -> tuple[dict[str, Any], list[bytes]]:
    """Executor entry point - the job id is filled in at submit time."""
    return build_package(
        job_id="00000000-0000-0000-0000-000000000000",
        trip_title=trip_title,
        day=day,
        stops=stops,
        photos=photos,
        day_number=day_number,
        day_count=day_count,
    )


__all__ = ["TripDayMiniExporter"]
