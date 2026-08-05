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
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import time
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import AssistantImageInput
from .canonical_day import canonical_roadbook_stops
from .map_snapshot import async_fetch_snapshot, fit_center_zoom
from .media_intelligence import media_quality_score
from .roadplanner import RoadplannerError
from .roadplanner import ValidationError
from .trip_summaries import SUMMARY_DETAIL_KEY
from .trip_export_photos import (
    LAST_PHOTO_ERROR,
    async_fetch_day_photos,
    async_fetch_media_photo,
    crop_photo,
)
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


def _stored_summary(document: Any) -> str:
    """Read the generated summary a document carries, if any."""
    details = document.get("details") if isinstance(document, dict) else None
    if not isinstance(details, dict):
        return ""
    return _one_line(details.get(SUMMARY_DETAIL_KEY))[:1_200]


def _day_highlights(
    stops: list[dict[str, Any]],
    media_by_stop: dict[str, list[dict[str, Any]]],
    day_media: list[dict[str, Any]] | None = None,
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
    stop_media_ids = {
        str(item.get("id") or "")
        for stop in stops
        for item in media_by_stop.get(str(stop.get("id") or ""), [])
    }
    media_count = len(stop_media_ids) + len(
        [
            item
            for item in day_media or []
            if str(item.get("id") or "") not in stop_media_ids
        ]
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


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f  ]+")


def _one_line(value: Any) -> str:
    """Collapse newlines/control characters into a readable single line.

    reportlab draws one line at a time - an embedded newline came out as a
    black .notdef box ("Besitzer von Notbert[]Mag Natur", live report).
    """
    text = _CONTROL_CHARS_RE.sub(" · ", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).replace(" · · ", " · ").strip(" ·").strip()


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

# A ticket expires after five minutes, so a PDF built an hour ago is gone.
# The video export already keeps its result retrievable ("Letztes Video");
# the PDF does the same, in the same library folder, so a summary built on
# the road can still be fetched later without rebuilding it.
MAX_STORED_TRIP_PDFS = 5
PDF_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.pdf$")


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
        *,
        map_snapshot_provider: str = "openstreetmap",
        google_maps_api_key: str | None = None,
        media_cache: Any = None,
        library_dir: Path | None = None,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self.provider = provider
        self.crew = crew
        self._map_snapshot_provider = map_snapshot_provider
        self._google_maps_api_key = str(google_maps_api_key or "").strip() or None
        self.media_cache = media_cache
        # Shared with the video export: one folder for generated trip
        # exports. Without it the PDF is still generated, only the "last
        # PDF" retrieval is unavailable.
        self.library_dir = library_dir
        self._tickets_lock = asyncio.Lock()
        self._tickets: dict[str, _PdfTicket] = {}

    def _save_to_library(self, pdf_bytes: bytes) -> str:
        """Write the PDF to the durable library and prune old entries.

        Blocking file I/O - executor only, never called from async code.
        """
        assert self.library_dir is not None
        self.library_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.pdf"
        (self.library_dir / filename).write_bytes(pdf_bytes)
        stored = sorted(
            self.library_dir.glob("*.pdf"), key=lambda path: path.stat().st_mtime
        )
        for stale in (
            stored[:-MAX_STORED_TRIP_PDFS]
            if len(stored) > MAX_STORED_TRIP_PDFS
            else []
        ):
            stale.unlink(missing_ok=True)
        return filename

    def _library_latest(self) -> dict[str, Any] | None:
        """Newest stored PDF - blocking, executor only."""
        if self.library_dir is None:
            return None
        try:
            stored = sorted(
                self.library_dir.glob("*.pdf"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        if not stored:
            return None
        stat = stored[0].stat()
        return {
            "url": f"/api/roadplanner/trip_pdf_library/{stored[0].name}",
            "created_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "size_mb": round(stat.st_size / 1_000_000, 1),
        }

    async def async_store_in_library(self, pdf_bytes: bytes) -> str:
        """Keep the freshly built PDF retrievable, return its download URL."""
        if self.library_dir is None:
            return ""
        try:
            filename = await self.hass.async_add_executor_job(
                self._save_to_library, pdf_bytes
            )
        except OSError as err:
            # The download itself already works via the ticket - a library
            # that cannot be written must never fail the export.
            _LOGGER.warning("Trip PDF could not be stored for later: %s", err)
            return ""
        return f"/api/roadplanner/trip_pdf_library/{filename}"

    async def async_status(self) -> dict[str, Any]:
        """The newest retrievable library PDF, if any."""
        return {
            "last_pdf": await self.hass.async_add_executor_job(self._library_latest)
        }

    async def _async_route_map(
        self, session: Any, days: list[dict[str, Any]]
    ) -> bytes | None:
        """Render the trip's REAL route as a static map, framed to fit.

        Live report: "Karte macht keinen Sinn" - the schematic zigzag has
        no relation to where the trip actually went. Fails open: without
        coordinates or on any provider error the schematic stays.
        """
        points: list[tuple[float, float]] = []
        for day in days:
            for stop in canonical_roadbook_stops(day):
                if not isinstance(stop, dict):
                    continue
                location = (
                    stop.get("location")
                    if isinstance(stop.get("location"), dict)
                    else {}
                )
                try:
                    latitude = float(location.get("latitude", location.get("lat")))
                    longitude = float(
                        location.get("longitude", location.get("lon", location.get("lng")))
                    )
                except (TypeError, ValueError):
                    continue
                if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                    points.append((latitude, longitude))
                    break  # one marker per day keeps the map readable
        if len(points) < 2:
            return None
        width_px, height_px = 1000, 720
        center_lat, center_lon, zoom = fit_center_zoom(
            points, width_px=width_px, height_px=height_px
        )
        try:
            return await async_fetch_snapshot(
                session,
                self._map_snapshot_provider,
                self._google_maps_api_key,
                center_lat=center_lat,
                center_lon=center_lon,
                markers=points,
                # The same points as a connected line, in travel order:
                # loose dots show where the trip touched down but nothing
                # about the order (live report: "Die Route ergibt so noch
                # keinen Sinn").
                path=points,
                zoom=zoom,
                width_px=width_px,
                height_px=height_px,
            )
        except Exception as err:  # noqa: BLE001 - a missing map must not abort the PDF
            _LOGGER.debug("PDF route map snapshot failed: %s", type(err).__name__)
            return None

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
                name=_one_line(item.get("name")),
                kind=(str(item.get("kind") or "person").strip().casefold() or "person"),
                note=_one_line(item.get("note")),
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
                note=_one_line(vehicle_raw.get("description")),
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
        media_by_day: dict[str, list[dict[str, Any]]] = {}
        for item in all_media:
            stop_id = str(item.get("linked_stop_id") or "")
            if stop_id:
                media_by_stop.setdefault(stop_id, []).append(item)
            day_id = str(item.get("linked_day_id") or "")
            if day_id:
                media_by_day.setdefault(day_id, []).append(item)

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
                    name=_one_line(stop.get("name")),
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
                session,
                trip_id,
                stops,
                media_by_stop,
                destination_galleries,
                media_by_day.get(str(day.get("id") or "")),
            )
            linked_photo_count = len(
                media_by_day.get(str(day.get("id") or "")) or []
            ) + sum(
                len(media_by_stop.get(str(stop.get("id") or ""), [])) for stop in stops
            )
            photo_note = ""
            if linked_photo_count and not photos:
                # A page that says "20 eigene Fotos" and then shows none has
                # to name the cause (live report: "Das pdf ist untauglich").
                reason = LAST_PHOTO_ERROR.get("reason") or "Grund unbekannt"
                photo_note = (
                    f"{linked_photo_count} eigene Fotos zugeordnet, aber keines "
                    f"konnte geladen werden - {reason}"
                )
            days.append(
                PdfDay(
                    title=_one_line(day.get("title")),
                    date=str(day.get("date") or ""),
                    stops=pdf_stops,
                    photos=photos,
                    photo_note=photo_note,
                    distance_km=_optional_float(day.get("distance_km")),
                    duration_minutes=_optional_minutes(
                        day.get("drive_minutes", day.get("duration_minutes"))
                    ),
                    highlights=_day_highlights(
                        stops,
                        media_by_stop,
                        media_by_day.get(str(day.get("id") or "")),
                    ),
                    # Written once by the summary generator and STORED on
                    # the day, not produced here: a Vision call per day
                    # would make every export take minutes (see
                    # trip_summary_service.py).
                    summary=_stored_summary(day),
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
        reference_by_name: dict[str, dict[str, Any]] = {}
        if self.crew is not None:
            try:
                crew_payload = await self.crew.async_panel_payload()
            except (RoadplannerError, OSError):
                crew_payload = {}
            for person in crew_payload.get("people") or []:
                if not isinstance(person, dict):
                    continue
                # Every person, not only those WITH a reference photo: the
                # stored summary has to be found for all of them.
                reference_by_name[str(person.get("name") or "").casefold()] = {
                    "media_id": str(person.get("reference_media_id") or ""),
                    "crop": person.get("reference_crop"),
                    "summary": str(person.get("summary") or ""),
                }
        for member in crew:
            reference = reference_by_name.get(member.name.casefold()) or {}
            reference_item = media_by_id.get(str(reference.get("media_id") or ""))
            mentions = _media_mentioning(all_media, member.name)
            portrait_item = reference_item or (mentions[0] if mentions else None)
            if portrait_item is None:
                continue
            member.photo = await async_fetch_media_photo(
                session,
                self.experience,
                trip_id,
                portrait_item,
                cache=self.media_cache,
                hass=self.hass,
            )
            if reference_item is not None and portrait_item is reference_item:
                # On a group photo only the chosen region is this person.
                member.photo = crop_photo(member.photo, reference.get("crop"))
            captions = [
                str(item.get("caption") or "").strip()[:200]
                for item in mentions[:6]
                if str(item.get("caption") or "").strip()
            ]
            stored = str((reference or {}).get("summary") or "").strip()
            if stored:
                # Generated once from the photos and stored on the crew
                # record - and correctable by hand, which a freshly rolled
                # text never is.
                member.summary = stored[:400]
                continue
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
            route_map=await self._async_route_map(session, days_raw),
            # The trip's own best photo owns the cover instead of an empty
            # decorative half-page.
            cover_photo=day_photo_bytes[0] if day_photo_bytes else None,
            summary=_stored_summary(trip),
        )
        return await self.hass.async_add_executor_job(build_trip_pdf, data)

    async def _async_fetch_day_photos(
        self,
        session: Any,
        trip_id: str,
        stops: list[dict[str, Any]],
        media_by_stop: dict[str, list[dict[str, Any]]],
        destination_galleries: dict[str, Any],
        day_media: list[dict[str, Any]] | None = None,
    ) -> list[bytes]:
        return await async_fetch_day_photos(
            session,
            self.experience,
            trip_id,
            stops,
            media_by_stop,
            destination_galleries,
            max_photos=MAX_PHOTOS_PER_DAY,
            day_media=day_media,
            cache=self.media_cache,
            hass=self.hass,
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
