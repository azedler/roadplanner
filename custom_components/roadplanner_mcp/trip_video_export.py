"""Gather trip/photo/map/narrative data and orchestrate trip video generation.

Mirrors trip_pdf_export.py's role and structure, but the actual "rendering"
step is an ffmpeg subprocess (ffmpeg_runner.py), not an in-process reportlab
call - video encoding takes far longer than a PDF render, which is why it
never touches hass.async_add_executor_job for the encode itself. See
ffmpeg_runner.py's module docstring for the full reasoning.

Unlike the PDF export (a short-lived, ticket-protected in-memory download),
a finished video is written to a small durable library on disk and
announced via a Home Assistant persistent notification. A render can take
minutes; the user asking for it may well have closed the app by the time
it's ready, and an in-memory ticket tied to that one WebSocket response
would be lost forever in that case. Writing to disk plus notifying means
the result survives regardless of what the client did in the meantime.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import re
import tempfile
from typing import Any
import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .canonical_day import canonical_roadbook_stops
from .experience_store import utc_now_iso
from .const import MAX_STORED_TRIP_VIDEOS
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

_FFMPEG_TIMEOUT_SECONDS = 240
VIDEO_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.mp4$")

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
    """Build a trip-summary video, store it in a durable library, and notify."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        provider: Any,
        *,
        map_snapshot_provider: str,
        google_maps_api_key: str | None,
        library_dir: Path,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self.provider = provider
        self._map_snapshot_provider = map_snapshot_provider
        self._google_maps_api_key = str(google_maps_api_key or "").strip() or None
        self.library_dir = library_dir
        self._status: dict[str, Any] = {"state": "idle"}
        self._task: Any = None

    def _set_stage(self, stage: str) -> None:
        if self._status.get("state") == "running":
            self._status["stage"] = stage

    def async_start(self, trip_id: str, *, style: str = DEFAULT_VIDEO_STYLE) -> dict[str, Any]:
        """Start one background video build; reject a second parallel run.

        The build takes minutes - a request held open for that long dies on
        every mobile connection change (live report: "Video erstellen"
        pressed, nothing traceable afterwards). The panel polls
        ``async_status`` instead.
        """
        if self._task is not None and not self._task.done():
            raise ValidationError(
                "Es läuft bereits eine Video-Erstellung - Status siehe "
                "Gesamtroute; bitte warten, bis sie abgeschlossen ist"
            )
        if not ffmpeg_available():
            raise RoadplannerError(
                "Der Video-Export ist auf diesem Home-Assistant-Host nicht "
                "verfügbar (ffmpeg wurde nicht gefunden)"
            )
        self._status = {
            "state": "running",
            "style": style,
            "stage": "Vorbereitung",
            "started_at": utc_now_iso(),
        }
        self._task = self.hass.async_create_task(self._async_run(trip_id, style))
        return dict(self._status)

    async def _async_run(self, trip_id: str, style: str) -> None:
        try:
            download_url = await self.async_generate_and_publish(trip_id, style=style)
        except (RoadplannerError, ValidationError) as err:
            self._status.update(
                {
                    "state": "error",
                    "stage": "Fehlgeschlagen",
                    "error": str(err)[:500],
                    "finished_at": utc_now_iso(),
                }
            )
            _LOGGER.warning("Trip video build failed: %s", err)
        except Exception as err:  # noqa: BLE001 - status must always resolve
            self._status.update(
                {
                    "state": "error",
                    "stage": "Fehlgeschlagen",
                    "error": f"Unerwarteter Fehler ({type(err).__name__})",
                    "finished_at": utc_now_iso(),
                }
            )
            _LOGGER.exception("Trip video build crashed")
        else:
            self._status.update(
                {
                    "state": "ready",
                    "stage": "Fertig",
                    "download_url": download_url,
                    "finished_at": utc_now_iso(),
                }
            )

    def _library_latest(self) -> dict[str, Any] | None:
        """Newest stored video - blocking, executor only."""
        try:
            videos = sorted(
                self.library_dir.glob("*.mp4"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        if not videos:
            return None
        stat = videos[0].stat()
        return {
            "url": f"/api/roadplanner/trip_video_library/{videos[0].name}",
            "created_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
            "size_mb": round(stat.st_size / 1_000_000, 1),
        }

    async def async_status(self) -> dict[str, Any]:
        """Current build state plus the newest retrievable library video."""
        status = dict(self._status)
        status["available"] = ffmpeg_available()
        status["last_video"] = await self.hass.async_add_executor_job(
            self._library_latest
        )
        return status

    async def async_generate_and_publish(
        self, trip_id: str, *, style: str = DEFAULT_VIDEO_STYLE
    ) -> str:
        """Generate the video, save it to the library, notify, return its URL."""
        trip_title, video_bytes = await self.async_generate(trip_id, style=style)
        self._set_stage("Video speichern")
        filename = await self.hass.async_add_executor_job(
            self._save_to_library, video_bytes
        )
        download_url = f"/api/roadplanner/trip_video_library/{filename}"
        await self._async_notify_ready(trip_title, download_url)
        return download_url

    async def async_generate(
        self, trip_id: str, *, style: str = DEFAULT_VIDEO_STYLE
    ) -> tuple[str, bytes]:
        if not ffmpeg_available():
            raise RoadplannerError(
                "Der Video-Export ist auf diesem Home-Assistant-Host nicht verfügbar "
                "(ffmpeg wurde nicht gefunden)"
            )
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für den Video-Export fehlt die Reise-ID")
        style = style if style in _MAX_CHAPTERS else DEFAULT_VIDEO_STYLE

        self._set_stage("Reisedaten und Fotos laden")
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
        chapter_days = days_for_style[:max_chapters]
        for chapter_index, day in enumerate(chapter_days):
            self._set_stage(
                f"Kapitel {chapter_index + 1}/{len(chapter_days)}: "
                "Fotos, Karte und Text"
            )
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

        photos_total = sum(len(chapter.photos) for chapter in chapters)
        maps_total = sum(1 for chapter in chapters if chapter.map_snapshot)
        stops_with_personal = sum(
            1
            for day in chapter_days
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict) and str(stop.get("id") or "") in media_by_stop
        )
        stops_with_gallery = sum(
            1
            for day in chapter_days
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict)
            and destination_galleries.get(str(stop.get("id") or ""))
        )
        if self._status.get("state") == "running":
            self._status["stats"] = {
                "chapters": len(chapters),
                "photos": photos_total,
                "map_snapshots": maps_total,
                "stops_with_personal_media": stops_with_personal,
                "stops_with_gallery": stops_with_gallery,
            }
        _LOGGER.info(
            "Trip video assets: %d chapters, %d photos, %d map snapshots "
            "(%d stops with personal media, %d with galleries)",
            len(chapters),
            photos_total,
            maps_total,
            stops_with_personal,
            stops_with_gallery,
        )
        if chapters and not photos_total and not maps_total:
            raise ValidationError(
                "Für dieses Video kam kein einziges Bild durch: "
                f"{stops_with_personal} Stopps haben eigene Fotos und "
                f"{stops_with_gallery} Planungsbilder, aber alle Downloads "
                "sind fehlgeschlagen (auch die Kartenbilder). Details stehen "
                "im Home-Assistant-Log unter roadplanner_mcp - typische "
                "Ursachen: OneDrive-Anmeldung abgelaufen oder kein "
                "Internetzugriff vom Home-Assistant-Host."
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
            self._set_stage("Video rendern (ffmpeg)")
            await async_run_ffmpeg(ffmpeg_args, timeout_seconds=_FFMPEG_TIMEOUT_SECONDS)
            return data.title, output_path.read_bytes()

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

    def _save_to_library(self, video_bytes: bytes) -> str:
        """Write the video to the durable library and prune old entries.

        Runs on the executor (blocking file I/O) - called via
        hass.async_add_executor_job, never directly from async code.
        """
        self.library_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.mp4"
        (self.library_dir / filename).write_bytes(video_bytes)
        self._prune_library()
        return filename

    def _prune_library(self) -> None:
        videos = sorted(
            self.library_dir.glob("*.mp4"), key=lambda path: path.stat().st_mtime
        )
        for stale in videos[:-MAX_STORED_TRIP_VIDEOS] if len(videos) > MAX_STORED_TRIP_VIDEOS else []:
            stale.unlink(missing_ok=True)

    async def _async_notify_ready(self, trip_title: str, download_url: str) -> None:
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Reise-Video fertig",
                    "message": (
                        f'Das Video für "{trip_title}" ist fertig. '
                        f"[Jetzt herunterladen]({download_url})"
                    ),
                    "notification_id": f"roadplanner_trip_video_{uuid.uuid4().hex}",
                },
            )
        except Exception as err:  # noqa: BLE001 - a failed notification must not undo a successful export
            _LOGGER.warning(
                "Trip video ready notification failed: %s", type(err).__name__
            )
