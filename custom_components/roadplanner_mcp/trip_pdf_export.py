"""Gather trip/crew/photo data and orchestrate trip-summary PDF generation.

The actual rendering (trip_pdf.py) is pure and synchronous; this module does
all the async work first - reading the roadbook/crew snapshot already stored
on the trip, and best-effort downloading a couple of already-confirmed
destination photos per day - then hands a plain, fully-resolved data
structure to the renderer via an executor job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any
import uuid

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .canonical_day import canonical_roadbook_stops
from .roadplanner import RoadplannerError, ValidationError
from .trip_pdf import (
    MAX_PHOTOS_PER_DAY,
    PdfCrewMember,
    PdfDay,
    PdfStop,
    PdfVehicle,
    TripPdfData,
    build_trip_pdf,
)

_LOGGER = logging.getLogger(__name__)

_MAX_PHOTO_BYTES = 6 * 1024 * 1024
_PHOTO_FETCH_TIMEOUT = ClientTimeout(total=8)
_TICKET_TTL_SECONDS = 5 * 60
_MAX_TICKETS = 20
_MAX_TICKET_USES = 3


@dataclass(slots=True)
class _PdfTicket:
    pdf_bytes: bytes
    user_id: str
    expires_monotonic: float
    remaining_uses: int = _MAX_TICKET_USES


class TripPdfExporter:
    """Build a downloadable trip-summary PDF and issue short-lived tickets."""

    def __init__(self, hass: HomeAssistant, manager: Any, experience: Any) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self._tickets_lock = asyncio.Lock()
        self._tickets: dict[str, _PdfTicket] = {}

    async def async_generate(self, trip_id: str) -> bytes:
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für den PDF-Export fehlt die Reise-ID")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError(
                "Die Reise für den PDF-Export konnte nicht geladen werden"
            )
        summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        days_raw = list(payload.get("days", {}).get("days", []) or [])

        crew = [
            PdfCrewMember(
                name=str(item.get("name") or "").strip(),
                kind=(str(item.get("kind") or "person").strip().casefold() or "person"),
                note=str(item.get("note") or "").strip(),
            )
            for item in (trip.get("travelers") or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        vehicle_raw = (
            trip.get("vehicle") if isinstance(trip.get("vehicle"), dict) else {}
        )
        vehicle_name = str(vehicle_raw.get("name") or "").strip()
        vehicle = (
            PdfVehicle(
                name=vehicle_name,
                note=str(vehicle_raw.get("description") or "").strip(),
            )
            if vehicle_name
            else None
        )

        try:
            experience_state = await self.experience.async_panel_payload(
                trip_id, days=days_raw
            )
        except RoadplannerError:
            experience_state = {}
        destination_galleries = (
            experience_state.get("destination_galleries")
            if isinstance(experience_state.get("destination_galleries"), dict)
            else {}
        )
        media_by_stop: dict[str, list[dict[str, Any]]] = {}
        for item in experience_state.get("media") or []:
            if not isinstance(item, dict):
                continue
            stop_id = str(item.get("linked_stop_id") or "")
            if stop_id:
                media_by_stop.setdefault(stop_id, []).append(item)

        session = async_get_clientsession(self.hass)
        country_codes: set[str] = set()
        days: list[PdfDay] = []
        for day in days_raw:
            if not isinstance(day, dict):
                continue
            stops = [
                stop
                for stop in canonical_roadbook_stops(day)
                if isinstance(stop, dict)
            ]
            pdf_stops = [
                PdfStop(
                    name=str(stop.get("name") or ""),
                    stop_type=str(stop.get("type") or ""),
                )
                for stop in stops
                if str(stop.get("name") or "").strip()
            ]
            for stop in stops:
                location = (
                    stop.get("location")
                    if isinstance(stop.get("location"), dict)
                    else {}
                )
                code = str(location.get("country_code") or "").strip().upper()
                if code:
                    country_codes.add(code)
            photos = await self._async_fetch_day_photos(
                session, trip_id, stops, media_by_stop, destination_galleries
            )
            days.append(
                PdfDay(
                    title=str(day.get("title") or ""),
                    date=str(day.get("date") or ""),
                    stops=pdf_stops,
                    photos=photos,
                )
            )

        try:
            total_distance_km = float(summary.get("total_distance_km") or 0.0)
        except (TypeError, ValueError):
            total_distance_km = 0.0

        data = TripPdfData(
            title=str(trip.get("title") or "Roadplanner-Reise"),
            start_date=str(trip.get("start_date") or ""),
            end_date=str(trip.get("end_date") or ""),
            crew=crew,
            vehicle=vehicle,
            days=days,
            total_distance_km=total_distance_km,
            country_count=len(country_codes),
        )
        return await self.hass.async_add_executor_job(build_trip_pdf, data)

    async def _async_fetch_day_photos(
        self,
        session: Any,
        trip_id: str,
        stops: list[dict[str, Any]],
        media_by_stop: dict[str, list[dict[str, Any]]],
        destination_galleries: dict[str, Any],
    ) -> list[bytes]:
        """Best-effort download of up to two photos, one per stop.

        A stop's own real photos (OneDrive-synced or manually uploaded, the
        same personal media shown in "Erinnerungen") are always preferred -
        the stock destination-gallery image is only used as a fallback when
        the stop has no personal photo of its own. Only a plain, directly
        fetchable HTTPS image URL is ever downloaded (Wikimedia Commons/
        Openverse for stock images; a temporary Microsoft Graph download URL
        for personal media). Google Places photos resolve to a redirect
        meant for the browser session, not a server-side background job, so
        they are deliberately skipped. A missing or failed photo just falls
        back to the drawn placeholder, never an error.
        """
        photos: list[bytes] = []
        for stop in stops:
            if len(photos) >= MAX_PHOTOS_PER_DAY:
                break
            photo = await self._async_fetch_personal_stop_photo(
                session, trip_id, str(stop.get("id") or ""), media_by_stop
            )
            if photo is None:
                photo = await self._async_fetch_stock_stop_photo(
                    session, str(stop.get("id") or ""), destination_galleries
                )
            if photo:
                photos.append(photo)
        return photos

    async def _async_fetch_personal_stop_photo(
        self,
        session: Any,
        trip_id: str,
        stop_id: str,
        media_by_stop: dict[str, list[dict[str, Any]]],
    ) -> bytes | None:
        candidates = media_by_stop.get(stop_id) or []
        if not candidates:
            return None
        media_item = next(
            (item for item in candidates if item.get("is_cover")), candidates[0]
        )
        media_id = str(media_item.get("id") or "")
        if not media_id:
            return None
        try:
            url = await self.experience.async_media_redirect_url(
                trip_id, media_id, "original"
            )
        except RoadplannerError as err:
            _LOGGER.debug(
                "Trip PDF export could not resolve a personal photo: %s",
                type(err).__name__,
            )
            return None
        if not str(url or "").casefold().startswith("https://"):
            return None
        return await self._async_download_photo(session, url)

    async def _async_fetch_stock_stop_photo(
        self,
        session: Any,
        stop_id: str,
        destination_galleries: dict[str, Any],
    ) -> bytes | None:
        gallery = destination_galleries.get(stop_id)
        if not isinstance(gallery, dict):
            return None
        images = [
            image for image in gallery.get("images") or [] if isinstance(image, dict)
        ]
        if not images:
            return None
        primary_id = str(gallery.get("primary_image_id") or "")
        image = next(
            (item for item in images if str(item.get("id") or "") == primary_id),
            images[0],
        )
        if str(image.get("provider") or "").casefold() == "google_places":
            return None
        url = str(image.get("image_url") or "")
        if not url.casefold().startswith("https://"):
            return None
        return await self._async_download_photo(session, url)

    async def _async_download_photo(self, session: Any, url: str) -> bytes | None:
        try:
            async with session.get(
                url, timeout=_PHOTO_FETCH_TIMEOUT, allow_redirects=True
            ) as response:
                if response.status != 200:
                    return None
                if (
                    response.content_length is not None
                    and response.content_length > _MAX_PHOTO_BYTES
                ):
                    return None
                body = await response.content.read(_MAX_PHOTO_BYTES + 1)
                if len(body) > _MAX_PHOTO_BYTES:
                    return None
                return body
        except (ClientError, TimeoutError, asyncio.TimeoutError) as err:
            _LOGGER.debug(
                "Trip PDF export could not fetch a destination photo: %s",
                type(err).__name__,
            )
            return None

    async def async_create_ticket(self, pdf_bytes: bytes, *, user_id: str) -> str:
        async with self._tickets_lock:
            self._purge()
            token = uuid.uuid4().hex
            self._tickets[token] = _PdfTicket(
                pdf_bytes=pdf_bytes,
                user_id=str(user_id or ""),
                expires_monotonic=time.monotonic() + _TICKET_TTL_SECONDS,
            )
            if len(self._tickets) > _MAX_TICKETS:
                oldest = next(iter(self._tickets))
                self._tickets.pop(oldest, None)
            return token

    async def async_resolve_ticket(self, token: str) -> bytes:
        async with self._tickets_lock:
            self._purge()
            ticket = self._tickets.get(token)
            if ticket is None:
                raise ValidationError("Der PDF-Download ist abgelaufen oder ungültig")
            ticket.remaining_uses -= 1
            if ticket.remaining_uses <= 0:
                self._tickets.pop(token, None)
            return ticket.pdf_bytes

    def _purge(self) -> None:
        now = time.monotonic()
        self._tickets = {
            token: ticket
            for token, ticket in self._tickets.items()
            if ticket.expires_monotonic >= now and ticket.remaining_uses > 0
        }
