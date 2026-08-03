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
import re
import time
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import AssistantImageInput
from .canonical_day import canonical_roadbook_stops
from .media_intelligence import media_quality_score
from .roadplanner import RoadplannerError, ValidationError
from .trip_export_photos import async_fetch_day_photos, async_fetch_media_photo
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


def _media_mentioning(
    media: list[dict[str, Any]], name: str
) -> list[dict[str, Any]]:
    """Photos whose caption mentions ``name``, best quality first."""
    cleaned = str(name or "").strip()
    if len(cleaned) < 2:
        return []
    pattern = re.compile(rf"(?<!\w){re.escape(cleaned)}(?!\w)", re.IGNORECASE)
    matches = [
        item
        for item in media
        if pattern.search(str(item.get("caption") or ""))
    ]
    matches.sort(key=media_quality_score, reverse=True)
    return matches


_HIGHLIGHT_STOP_TYPES = {
    "activity",
    "attraction",
    "sightseeing",
    "viewpoint",
    "ferry",
    "fishing",
}
_OVERNIGHT_STOP_TYPES = {
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
}


def _day_highlights(
    stops: list[dict[str, Any]],
    media_by_stop: dict[str, list[dict[str, Any]]],
) -> list[str]:
    """Deterministic keyword bullets for a day - no AI, no invented text."""
    highlights = [
        str(stop.get("name") or "").strip()[:40]
        for stop in stops
        if str(stop.get("type") or "").casefold() in _HIGHLIGHT_STOP_TYPES
        and str(stop.get("name") or "").strip()
    ][:3]
    if not highlights:
        overnight = next(
            (
                stop
                for stop in stops
                if str(stop.get("type") or "").casefold() in _OVERNIGHT_STOP_TYPES
                and str(stop.get("name") or "").strip()
            ),
            None,
        )
        if overnight:
            highlights = [str(overnight.get("name") or "").strip()[:40]]
    media_count = sum(
        len(media_by_stop.get(str(stop.get("id") or ""), [])) for stop in stops
    )
    if media_count:
        highlights.append(
            f"{media_count} eigene Fotos" if media_count > 1 else "1 eigenes Foto"
        )
    return highlights


_PERSON_SUMMARY_PROMPT = (
    "Du fasst für den Reise-Rückblick einer Camperreise zusammen, was EINE "
    "Person laut den Bildunterschriften ihrer Fotos erlebt hat. Nutze "
    "ausschließlich die genannten Bildunterschriften und die Kurznotiz - "
    "erfinde nichts dazu. Maximal 2 kurze, warmherzige Sätze auf Deutsch in "
    "der dritten Person, keine Anführungszeichen, keine Emojis."
)


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _optional_minutes(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None

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

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        provider: Any = None,
        crew: Any = None,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self.provider = provider
        self.crew = crew
        self._tickets_lock = asyncio.Lock()
        self._tickets: dict[str, _PdfTicket] = {}

    async def _async_person_summary(
        self, name: str, note: str, captions: list[str]
    ) -> str:
        if not captions:
            return ""
        if self.provider is None:
            return " · ".join(captions[:2])[:200]
        facts = f"Person: {name}\nNotiz: {note or '-'}\nBildunterschriften:\n" + "\n".join(
            f"- {caption}" for caption in captions
        )
        try:
            result = await self.provider.async_generate_text(
                system_instruction=_PERSON_SUMMARY_PROMPT,
                messages=[{"role": "user", "content": facts}],
                enable_search=False,
                max_output_tokens=160,
                temperature=0.4,
            )
        except RoadplannerError as err:
            _LOGGER.debug(
                "PDF person summary generation failed: %s", type(err).__name__
            )
            return " · ".join(captions[:2])[:200]
        text = str(getattr(result, "text", "") or "").strip()
        return text[:300] or " · ".join(captions[:2])[:200]

    async def _async_person_vision_summary(
        self, name: str, reference_photo: bytes, day_photos: list[bytes]
    ) -> str:
        """Recognize the person on the day photos via the reference face.

        Bounded (reference + at most 10 already-downloaded photos), strictly
        fail-open, and only claims what Vision confidently sees.
        """
        if (
            self.provider is None
            or not callable(getattr(self.provider, "async_analyze_images", None))
            or not day_photos
        ):
            return ""
        images = [
            AssistantImageInput(
                image_id="referenz",
                data=reference_photo,
                mime_type="image/jpeg",
                label=f"Referenzfoto: Das ist {name}",
            )
        ]
        for index, photo in enumerate(day_photos[:10]):
            images.append(
                AssistantImageInput(
                    image_id=f"foto-{index + 1}",
                    data=photo,
                    mime_type="image/jpeg",
                    label=f"Reisefoto {index + 1}",
                )
            )
        try:
            result = await self.provider.async_analyze_images(
                system_instruction=(
                    "Das erste Bild ist ein Referenzfoto und zeigt die Person "
                    f"{name}. Prüfe, auf welchen der übrigen Reisefotos "
                    "dieselbe Person eindeutig zu erkennen ist, und was sie "
                    "dort gerade macht. Fasse das in maximal 2 kurzen, "
                    "warmherzigen Sätzen auf Deutsch zusammen (dritte "
                    "Person, keine Anführungszeichen, keine Emojis). Ist die "
                    "Person auf keinem Foto sicher zu erkennen, gib einen "
                    "leeren Text zurück. Beschreibe nur, was wirklich zu "
                    "sehen ist."
                ),
                prompt=f"Was erlebt {name} auf diesen Reisefotos?",
                images=images,
                schema={
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"],
                },
                max_output_tokens=400,
            )
        except RoadplannerError as err:
            _LOGGER.debug(
                "PDF person vision summary failed: %s", type(err).__name__
            )
            return ""
        return str((result.value or {}).get("summary") or "").strip()[:300]

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
        all_media = [
            item
            for item in experience_state.get("media") or []
            if isinstance(item, dict)
        ]
        media_by_stop: dict[str, list[dict[str, Any]]] = {}
        for item in all_media:
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
                    arrival_time=str(stop.get("arrival_time") or ""),
                    departure_time=str(stop.get("departure_time") or ""),
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
                    distance_km=_optional_float(day.get("distance_km")),
                    duration_minutes=_optional_minutes(
                        day.get("drive_minutes", day.get("duration_minutes"))
                    ),
                    highlights=_day_highlights(stops, media_by_stop),
                )
            )

        day_photo_bytes = [photo for pdf_day in days for photo in pdf_day.photos][:10]
        # Persönliche Crew-Karten (live request: "bissel persönliches
        # reinbringen", "wer ist wer" ohne Bildunterschriften): the
        # reference photo assigned in the crew settings is the portrait and
        # the Vision reference face; photo captions mentioning the name
        # stay as an additional source.
        media_by_id = {
            str(item.get("id") or ""): item for item in all_media
        }
        reference_by_name: dict[str, str] = {}
        if self.crew is not None:
            try:
                crew_payload = await self.crew.async_panel_payload()
            except (RoadplannerError, OSError):
                crew_payload = {}
            for person in crew_payload.get("people") or []:
                if isinstance(person, dict) and person.get("reference_media_id"):
                    reference_by_name[str(person.get("name") or "").casefold()] = str(
                        person["reference_media_id"]
                    )
        for member in crew:
            reference_id = reference_by_name.get(member.name.casefold(), "")
            reference_item = media_by_id.get(reference_id)
            mentions = _media_mentioning(all_media, member.name)
            portrait_item = reference_item or (mentions[0] if mentions else None)
            if portrait_item is None:
                continue
            member.photo = await async_fetch_media_photo(
                session, self.experience, trip_id, portrait_item
            )
            captions = [
                str(item.get("caption") or "").strip()[:200]
                for item in mentions[:6]
                if str(item.get("caption") or "").strip()
            ]
            member.summary = await self._async_person_summary(
                member.name, member.note, captions
            )
            if not member.summary and member.photo and reference_item is not None:
                # No captions at all: recognize the person on the already
                # downloaded day photos via the reference face (bounded,
                # fail-open).
                member.summary = await self._async_person_vision_summary(
                    member.name, member.photo, day_photo_bytes
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
        return await async_fetch_day_photos(
            session,
            self.experience,
            trip_id,
            stops,
            media_by_stop,
            destination_galleries,
            max_photos=MAX_PHOTOS_PER_DAY,
        )

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
