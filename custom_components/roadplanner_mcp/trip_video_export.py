"""Gather trip/photo/map/narrative data and orchestrate trip video generation.

Mirrors trip_pdf_export.py's role and structure, but the actual "rendering"
step is an ffmpeg subprocess (ffmpeg_runner.py), not an in-process reportlab
call - video encoding takes far longer than a PDF render, which is why it
never touches hass.async_add_executor_job for the encode itself. See
ffmpeg_runner.py's module docstring for the full reasoning.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import tempfile
import time
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .canonical_day import canonical_roadbook_stops
from .ffmpeg_runner import async_run_ffmpeg, ffmpeg_available
from .map_snapshot import async_fetch_snapshot
from .roadplanner import RoadplannerError, ValidationError
from .trip_export_photos import async_fetch_day_photos
from .trip_video import (
    TripVideoData,
    VideoChapter,
    build_ffmpeg_filter_graph,
    prepare_chapter_assets,
)

_LOGGER = logging.getLogger(__name__)

_TICKET_TTL_SECONDS = 5 * 60
# Lower than the PDF's 20 - an in-memory MP4 is tens of MB, not a few KB.
_MAX_TICKETS = 5
_MAX_TICKET_USES = 3
_FFMPEG_TIMEOUT_SECONDS = 240

VIDEO_STYLES = ("highlight", "full")
DEFAULT_VIDEO_STYLE = "highlight"
_MAX_PHOTOS_PER_CHAPTER = {"highlight": 1, "full": 3}
_MAX_CHAPTERS = {"highlight": 8, "full": 40}

_MUSIC_DIR_NAME = "assets/music"
_MUSIC_EXTENSIONS = (".mp3",)

_NARRATIVE_SYSTEM_PROMPT = (
    "Du schreibst eine kurze, warmherzige Reise-Story für einen Tag einer "
    "Roadtrip-Zusammenfassung, die als Texteinblendung in einem Video "
    "erscheint. Nutze ausschließlich die unten genannten Fakten (Ort, "
    "Datum, Stopps, gefahrene Distanz). Erfinde keine zusätzlichen "
    "Details, Namen, Wetterangaben oder Ereignisse, die nicht explizit "
    "genannt sind. Maximal 3 kurze Sätze, auf Deutsch, keine "
    "Anführungszeichen, keine Emojis."
)


@dataclass(slots=True)
class _VideoTicket:
    video_bytes: bytes
    user_id: str
    expires_monotonic: float
    remaining_uses: int = _MAX_TICKET_USES


def _music_directory() -> Path:
    return Path(__file__).parent / _MUSIC_DIR_NAME


def pick_music_track(trip_id: str) -> Path | None:
    """Deterministically pick a bundled track, or None if the folder is empty.

    The folder ships empty in this pass (see assets/music/README.md) -
    sourcing real royalty-free tracks is a manual follow-up. A missing or
    empty folder must produce a silent video, never a crash.
    """
    directory = _music_directory()
    if not directory.is_dir():
        return None
    tracks = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.casefold() in _MUSIC_EXTENSIONS
    )
    if not tracks:
        return None
    index = int(hashlib.sha256(trip_id.encode("utf-8")).hexdigest(), 16) % len(tracks)
    return tracks[index]


def _stop_coordinate(stop: dict[str, Any]) -> tuple[float, float] | None:
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    try:
        return float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None


class TripVideoExporter:
    """Build a downloadable trip-summary video and issue short-lived tickets."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        provider: Any,
        *,
        map_snapshot_provider: str,
        google_maps_api_key: str | None,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self.provider = provider
        self._map_snapshot_provider = map_snapshot_provider
        self._google_maps_api_key = str(google_maps_api_key or "").strip() or None
        self._tickets_lock = asyncio.Lock()
        self._tickets: dict[str, _VideoTicket] = {}

    async def async_generate(self, trip_id: str, *, style: str = DEFAULT_VIDEO_STYLE) -> bytes:
        if not ffmpeg_available():
            raise RoadplannerError(
                "Der Video-Export ist auf diesem Home-Assistant-Host nicht verfügbar "
                "(ffmpeg wurde nicht gefunden)"
            )
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für den Video-Export fehlt die Reise-ID")
        style = style if style in _MAX_CHAPTERS else DEFAULT_VIDEO_STYLE

        payload = await self.manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError(
                "Die Reise für den Video-Export konnte nicht geladen werden"
            )
        summary = (
            payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        )
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        days_raw = [
            day for day in payload.get("days", {}).get("days", []) or [] if isinstance(day, dict)
        ]

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

        max_photos = _MAX_PHOTOS_PER_CHAPTER[style]
        max_chapters = _MAX_CHAPTERS[style]

        days_for_style = days_raw
        if style == "highlight":
            days_with_media = [
                day
                for day in days_raw
                if any(
                    str(stop.get("id") or "") in media_by_stop
                    or destination_galleries.get(str(stop.get("id") or ""))
                    for stop in canonical_roadbook_stops(day)
                    if isinstance(stop, dict)
                )
            ]
            days_for_style = days_with_media or days_raw

        session = async_get_clientsession(self.hass)
        chapters: list[VideoChapter] = []
        for day in days_for_style[:max_chapters]:
            stops = [
                stop for stop in canonical_roadbook_stops(day) if isinstance(stop, dict)
            ]
            photos = await async_fetch_day_photos(
                session,
                self.experience,
                trip_id,
                stops,
                media_by_stop,
                destination_galleries,
                max_photos=max_photos,
            )
            map_snapshot = await self._async_fetch_chapter_map_snapshot(session, stops)
            narrative = await self._async_generate_narrative(day, stops)
            chapters.append(
                VideoChapter(
                    title=str(day.get("title") or ""),
                    date=str(day.get("date") or ""),
                    narrative=narrative,
                    map_snapshot=map_snapshot,
                    photos=photos,
                )
            )

        data = TripVideoData(
            title=str(trip.get("title") or "Roadplanner-Reise"),
            start_date=str(trip.get("start_date") or ""),
            end_date=str(trip.get("end_date") or ""),
            chapters=chapters,
        )

        music_path = pick_music_track(trip_id)

        with tempfile.TemporaryDirectory(prefix="roadplanner_trip_video_") as tmp:
            workdir = Path(tmp)
            frame_paths = await self.hass.async_add_executor_job(
                prepare_chapter_assets, data, workdir
            )
            if not frame_paths:
                raise ValidationError(
                    "Für diese Reise wurden keine Fotos für das Video gefunden"
                )
            input_args, filter_complex, video_label = build_ffmpeg_filter_graph(
                data, frame_paths
            )
            output_path = workdir / "trip_video.mp4"
            ffmpeg_args = list(input_args)
            audio_input_index: int | None = None
            if music_path is not None:
                audio_input_index = len(frame_paths)
                ffmpeg_args += ["-i", str(music_path)]
            ffmpeg_args += ["-filter_complex", filter_complex, "-map", video_label]
            if audio_input_index is not None:
                ffmpeg_args += ["-map", f"{audio_input_index}:a", "-shortest", "-c:a", "aac"]
            ffmpeg_args += [
                "-r", str(data.fps),
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264",
                str(output_path),
            ]
            await async_run_ffmpeg(ffmpeg_args, timeout_seconds=_FFMPEG_TIMEOUT_SECONDS)
            return output_path.read_bytes()

    async def _async_fetch_chapter_map_snapshot(
        self, session: Any, stops: list[dict[str, Any]]
    ) -> bytes | None:
        coordinates = [
            coordinate
            for stop in stops
            if (coordinate := _stop_coordinate(stop)) is not None
        ]
        if not coordinates:
            return None
        center_lat = sum(c[0] for c in coordinates) / len(coordinates)
        center_lon = sum(c[1] for c in coordinates) / len(coordinates)
        try:
            return await async_fetch_snapshot(
                session,
                self._map_snapshot_provider,
                self._google_maps_api_key,
                center_lat=center_lat,
                center_lon=center_lon,
                markers=coordinates,
                zoom=9,
            )
        except Exception as err:  # noqa: BLE001 - a missing map snapshot must not abort the export
            _LOGGER.debug("Trip video map snapshot failed: %s", type(err).__name__)
            return None

    async def _async_generate_narrative(
        self, day: dict[str, Any], stops: list[dict[str, Any]]
    ) -> str:
        if self.provider is None:
            return ""
        stop_names = ", ".join(
            str(stop.get("name") or "").strip() for stop in stops if stop.get("name")
        )
        facts = (
            f"Titel: {day.get('title') or ''}\n"
            f"Datum: {day.get('date') or ''}\n"
            f"Stopps: {stop_names or 'keine erfassten Stopps'}\n"
            f"Gefahrene Strecke: {day.get('distance_km') or 'unbekannt'} km"
        )
        try:
            result = await self.provider.async_generate_text(
                system_instruction=_NARRATIVE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": facts}],
                enable_search=False,
                max_output_tokens=220,
                temperature=0.4,
            )
        except RoadplannerError as err:
            _LOGGER.debug("Trip video narrative generation failed: %s", type(err).__name__)
            return ""
        return str(getattr(result, "text", "") or "").strip()

    async def async_create_ticket(self, video_bytes: bytes, *, user_id: str) -> str:
        async with self._tickets_lock:
            self._purge()
            token = uuid.uuid4().hex
            self._tickets[token] = _VideoTicket(
                video_bytes=video_bytes,
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
                raise ValidationError("Der Video-Download ist abgelaufen oder ungültig")
            ticket.remaining_uses -= 1
            if ticket.remaining_uses <= 0:
                self._tickets.pop(token, None)
            return ticket.video_bytes

    def _purge(self) -> None:
        now = time.monotonic()
        self._tickets = {
            token: ticket
            for token, ticket in self._tickets.items()
            if ticket.expires_monotonic >= now and ticket.remaining_uses > 0
        }
