"""Decision slides and OneDrive media albums for Roadplanner."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
from functools import partial
import json
import logging
import math
import re
import secrets
from time import monotonic
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientTimeout

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .assistant_provider import AssistantImageInput, AssistantProvider
from .const import EVENT_ROADPLANNER_UPDATED
from .canonical_day import canonical_roadbook_stops
from .decision_logic import (
    DecisionBaselineError,
    compact_decision_days,
    ensure_current_plan_option,
)
from .destination_images import DestinationImageProvider
from .destination_intelligence import analyze_destination, destination_image_query
from .media_intelligence import build_media_presentation, select_media_highlights
from .experience_store import (
    ExperienceStore,
    new_id,
    resolve_decision_media_references,
    utc_now_iso,
)
from .geocoding import NominatimGeocoder
from .manager import RoadplannerManager
from .onedrive_media import OneDrivePersonalClient, normalize_onedrive_folder_path
from .place_cleanup import PlaceCleanupService
from .place_enrichment import PlaceEnrichmentService
from .media_vision import (
    VISION_SELECTION_SCHEMA,
    build_vision_prompt,
    normalize_vision_selection,
    selection_fingerprint,
)
from .roadplanner import RoadplannerError, ValidationError
from .routing import OSRMRoutingClient, route_input_hash

_LOGGER = logging.getLogger(__name__)

_AUTOMATIC_RADIUS_M = 750.0
_SUGGESTED_RADIUS_M = 5_000.0
_MEDIA_TOKEN_TTL_SECONDS = 60 * 60
_IMAGE_MIME_PREFIX = "image/"
_MEDIA_SYNC_STRATEGY_VERSION = 3
_INITIAL_SCAN_MODE = "initial_scan"
_DELTA_CATCHUP_MODE = "delta_catchup"
_DELTA_MODE = "delta"
_SCAN_PAGE_SIZE = 200
_DEFAULT_SCAN_TIME_BUDGET_SECONDS = 12
_DECISION_GEOCODE_TIMEOUT_SECONDS = 12.0
_DECISION_IMAGE_TIMEOUT_SECONDS = 10.0
_DECISION_ROUTE_TIMEOUT_SECONDS = 15.0
_DESTINATION_GALLERY_SIZE = 3
_DESTINATION_AUTO_BATCH = 6
_DESTINATION_EMPTY_RETRY_SECONDS = 6 * 60 * 60
_DESTINATION_BACKGROUND_INTERVAL_MINUTES = 30
_DESTINATION_INITIAL_DELAY_SECONDS = 45
_DESTINATION_BACKGROUND_BATCH = 4
_VISION_BACKGROUND_BATCH = 3
_VISION_IMAGE_TIMEOUT_SECONDS = 12.0
_VISION_MAX_IMAGE_BYTES = 1_000_000
_VISION_MAX_TOTAL_BYTES = 8_000_000
_HIDDEN_MEDIA_FOLDERS = frozenset({
    ".picasaoriginals",
    ".thumbnails",
    "thumbs",
    "thumbnails",
    "cache",
})
_YEAR_FOLDER_RE = re.compile(r"^(?P<year>(?:19|20)\d{2})(?:\D.*)?$")
_YEAR_MONTH_FOLDER_RE = re.compile(
    r"^(?P<year>(?:19|20)\d{2})[-_. ]?(?P<month>0?[1-9]|1[0-2])(?:\D.*)?$"
)
_YEAR_ANYWHERE_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})(?!\d)")
_YEAR_MONTH_ANYWHERE_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})[-_. ]+(?P<month>0?[1-9]|1[0-2])(?!\d)"
)
_MONTH_FOLDER_RE = re.compile(r"^(?:0?[1-9]|1[0-2])$")
_MONTH_NAMES = {
    "januar": 1,
    "january": 1,
    "jan": 1,
    "februar": 2,
    "february": 2,
    "feb": 2,
    "maerz": 3,
    "märz": 3,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "june": 6,
    "jun": 6,
    "juli": 7,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "december": 12,
    "dez": 12,
    "dec": 12,
}

_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "question": {"type": "string"},
        "linked_day_id": {"type": ["string", "null"]},
        "options": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "place_query": {"type": "string"},
                    "stop_type": {"type": "string"},
                    "pros": {"type": "array", "items": {"type": "string"}},
                    "cons": {"type": "array", "items": {"type": "string"}},
                    "estimated_cost": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": ["number", "null"]},
                            "currency": {"type": ["string", "null"]},
                            "note": {"type": ["string", "null"]},
                        },
                    },
                    "is_current_plan": {"type": "boolean"},
                    "change_type": {"type": "string"},
                    "existing_stop_id": {"type": ["string", "null"]},
                },
                "required": ["title", "summary", "place_query", "stop_type", "pros", "cons"],
            },
        },
    },
    "required": ["title", "question", "options"],
}

_DECISION_PROMPT = """Du erstellst eine lokale Roadplanner-Entscheidungsvorlage aus genau einer bereits sichtbaren Assistentenantwort.
Extrahiere ausschließlich die konkreten Optionen, die in der Antwort wirklich genannt wurden. Erfinde keine ungeklärten Preise oder Orte.
Jede Option benötigt einen kurzen Titel, eine knappe Zusammenfassung, einen geocodierbaren Orts-/Anbieternamen in place_query, einen Roadplanner-Stopp-Typ sowie höchstens vier Vor- und Nachteile.
Wenn die Frage sinngemäß lautet, ob der bestehende Plan beibehalten oder durch eine Alternative ersetzt werden soll, MUSS der aktuelle Roadbook-Stopp als eigene erste Option enthalten sein. Setze dann is_current_plan=true, change_type=keep_existing und existing_stop_id auf die vorhandene Stop-ID. Alternativen erhalten is_current_plan=false und change_type=replace_existing.
Wenn die Antwort einen Reisetag eindeutig nennt, verwende ausschließlich eine vorhandene day_id aus dem mitgelieferten Roadbook. Andernfalls linked_day_id=null.
Die mitgelieferten Tage enthalten kompakte vorhandene Stopps. Verwende ihre IDs nur, wenn der Name und der Kontext eindeutig übereinstimmen.
Antworte ausschließlich im vorgegebenen JSON-Schema."""


def _clean(value: Any, maximum: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _coordinate(location: Any) -> tuple[float, float] | None:
    if not isinstance(location, dict):
        return None
    lat = location.get("latitude", location.get("lat"))
    lon = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    lat = float(lat)
    lon = float(lon)
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _day_date(day: dict[str, Any]) -> date | None:
    raw = str(day.get("date") or "").strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_roadbook_stops(day)


def _all_days(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("days")
    if isinstance(value, dict):
        value = value.get("days")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _trip_date_window(
    days: list[dict[str, Any]],
    buffer_days: int = 3,
) -> tuple[date, date] | None:
    values = sorted(value for day in days if (value := _day_date(day)) is not None)
    if not values:
        return None
    buffer_days = max(0, min(int(buffer_days), 30))
    tolerance = timedelta(days=buffer_days)
    return values[0] - tolerance, values[-1] + tolerance


def _folder_date_hint(
    name: str,
    parent_hint: tuple[int, int | None] | None = None,
) -> tuple[int, int | None] | None:
    """Return a year/month hint for common camera archive folder names.

    OneDrive camera tools often create names such as ``2026``, ``2026-07`` or
    ``Handy_Upload_Iphone_Aron_2026``. Month folders may also be named ``07``,
    ``Juli`` or ``August`` below a year folder. Recognizing these conventions
    lets Roadplanner avoid traversing historical branches altogether.
    """
    text = str(name or "").strip()
    if not text:
        return None
    month_match = _YEAR_MONTH_FOLDER_RE.fullmatch(text)
    if month_match:
        return int(month_match.group("year")), int(month_match.group("month"))
    month_match = _YEAR_MONTH_ANYWHERE_RE.search(text)
    if month_match:
        return int(month_match.group("year")), int(month_match.group("month"))
    year_match = _YEAR_FOLDER_RE.fullmatch(text)
    if year_match:
        return int(year_match.group("year")), None
    year_match = _YEAR_ANYWHERE_RE.search(text)
    if year_match:
        return int(year_match.group("year")), None
    if parent_hint and parent_hint[0]:
        normalized = re.sub(r"[^a-z0-9äöü]+", "", text.casefold())
        if _MONTH_FOLDER_RE.fullmatch(normalized):
            return int(parent_hint[0]), int(normalized)
        if normalized in _MONTH_NAMES:
            return int(parent_hint[0]), _MONTH_NAMES[normalized]
    return None


def _hint_intersects_window(
    hint: tuple[int, int | None] | None,
    window: tuple[date, date],
) -> bool:
    """Return whether a date-shaped folder can contain photos in the window."""
    if hint is None:
        return True
    year, month = hint
    if month is None:
        return window[0].year <= year <= window[1].year
    first = date(year, month, 1)
    if month == 12:
        after = date(year + 1, 1, 1)
    else:
        after = date(year, month + 1, 1)
    last = after - timedelta(days=1)
    return not (last < window[0] or first > window[1])


def _folder_scan_decision(
    name: str,
    window: tuple[date, date],
    *,
    recursive: bool,
    parent_hint: tuple[int, int | None] | None = None,
) -> tuple[bool, str, tuple[int, int | None] | None]:
    """Return whether a child folder should be traversed and why."""
    text = str(name or "").strip()
    lowered = text.casefold()
    if not recursive:
        return False, "recursion_disabled", None
    if not text or text.startswith(".") or lowered in _HIDDEN_MEDIA_FOLDERS:
        return False, "hidden_or_generated", None
    hint = _folder_date_hint(text, parent_hint)
    if not _hint_intersects_window(hint, window):
        return False, "outside_trip_window", hint
    return True, "included", hint


def _hint_to_json(hint: tuple[int, int | None] | None) -> dict[str, int] | None:
    if hint is None:
        return None
    value = {"year": int(hint[0])}
    if hint[1] is not None:
        value["month"] = int(hint[1])
    return value


def _hint_from_json(value: Any) -> tuple[int, int | None] | None:
    if not isinstance(value, dict):
        return None
    year = value.get("year")
    month = value.get("month")
    if isinstance(year, bool) or not isinstance(year, int):
        return None
    if month is not None and (isinstance(month, bool) or not isinstance(month, int)):
        return None
    return int(year), int(month) if month is not None else None


def _join_display_path(parent: str, child: str) -> str:
    """Join a compact display path without repeating camera-folder prefixes.

    Several iPhone upload tools create a root such as
    ``Handy_Upload_Iphone_Aron`` and a dated child named
    ``Handy_Upload_Iphone_Aron_2026``.  Showing both full names looked like a
    duplicated configuration even though the Graph folder IDs were correct.
    Keep the canonical IDs untouched and shorten only the human-readable path.
    """
    parent_path = normalize_onedrive_folder_path(parent, allow_empty=True)
    child_name = str(child or "").strip().strip("/")
    if not child_name:
        return parent_path
    if parent_path:
        last = parent_path.rsplit("/", 1)[-1]
        last_folded = last.casefold()
        child_folded = child_name.casefold()
        if last_folded == child_folded:
            return parent_path
        for separator in ("_", "-", " "):
            prefix = f"{last_folded}{separator}"
            if child_folded.startswith(prefix):
                compact_child = child_name[len(last) + 1 :].strip()
                if compact_child:
                    return f"{parent_path}/{compact_child}"
        return f"{parent_path}/{child_name}"
    return child_name


def _media_local_date(media: dict[str, Any]) -> date | None:
    taken = _parse_datetime(media.get("taken_at") or media.get("created_at"))
    return dt_util.as_local(taken).date() if taken is not None else None


def _provider_media(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict) or item.get("deleted"):
        return None
    file_data = item.get("file") if isinstance(item.get("file"), dict) else {}
    mime = str(file_data.get("mimeType") or "")
    if not mime.startswith(_IMAGE_MIME_PREFIX) and not isinstance(item.get("photo"), dict):
        return None
    photo = item.get("photo") if isinstance(item.get("photo"), dict) else {}
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    lat = location.get("latitude")
    lon = location.get("longitude")
    normalized_location: dict[str, Any] = {}
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
        normalized_location = {"latitude": float(lat), "longitude": float(lon)}
    hashes = file_data.get("hashes") if isinstance(file_data.get("hashes"), dict) else {}
    image_data = item.get("image") if isinstance(item.get("image"), dict) else {}
    return {
        "provider_item_id": str(item.get("id") or ""),
        "drive_id": str((item.get("parentReference") or {}).get("driveId") or "") if isinstance(item.get("parentReference"), dict) else None,
        "name": str(item.get("name") or "Foto"),
        "mime_type": mime or "image/jpeg",
        "media_type": "photo",
        "size_bytes": int(item.get("size") or 0),
        "taken_at": photo.get("takenDateTime") or (
            item.get("fileSystemInfo", {}).get("createdDateTime")
            if isinstance(item.get("fileSystemInfo"), dict)
            else None
        ),
        "created_at": item.get("createdDateTime"),
        "modified_at": item.get("lastModifiedDateTime"),
        "web_url": item.get("webUrl"),
        "location": normalized_location,
        "file_hash": hashes.get("quickXorHash") or hashes.get("sha1Hash") or hashes.get("sha256Hash"),
        "width": image_data.get("width"),
        "height": image_data.get("height"),
        "thumbnail_available": True,
        "last_seen_at": utc_now_iso(),
    }


class RoadplannerExperienceManager:
    """Coordinate decision cards and OneDrive Personal albums."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        onedrive: OneDrivePersonalClient,
        *,
        provider: AssistantProvider | None,
        assistant: Any,
        geocoder: NominatimGeocoder | None,
        router: OSRMRoutingClient,
        image_provider: DestinationImageProvider,
        media_curation_mode: str = "local",
        media_vision_max_candidates: int = 12,
        media_vision_max_highlights: int = 5,
        media_vision_daily_limit: int = 5,
        folder_path: str,
        sync_interval_minutes: int,
        auto_sync: bool,
        auto_assign: bool,
        recursive_subfolders: bool = True,
        date_buffer_days: int = 3,
        max_items_per_run: int = 2000,
        max_scan_seconds: int = _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.onedrive = onedrive
        self.provider = provider
        self.assistant = assistant
        self.geocoder = geocoder
        self.router = router
        self.image_provider = image_provider
        self.media_curation_mode = (
            "hybrid" if str(media_curation_mode or "").casefold() == "hybrid" else "local"
        )
        self.media_vision_max_candidates = max(3, min(int(media_vision_max_candidates), 15))
        self.media_vision_max_highlights = max(1, min(int(media_vision_max_highlights), 8))
        self.media_vision_daily_limit = max(0, min(int(media_vision_daily_limit), 50))
        self.place_enrichment = (
            PlaceEnrichmentService(
                geocoder,
                image_provider,
                cleanup_service=PlaceCleanupService(provider),
            )
            if geocoder is not None and geocoder.enabled
            else None
        )
        self.folder_path = normalize_onedrive_folder_path(
            folder_path or "Pictures/Camera Roll"
        )
        self.sync_interval_minutes = max(5, min(int(sync_interval_minutes), 1440))
        self.auto_sync = bool(auto_sync)
        self.auto_assign = bool(auto_assign)
        self.recursive_subfolders = bool(recursive_subfolders)
        self.date_buffer_days = max(0, min(int(date_buffer_days), 30))
        self.max_items_per_run = max(100, min(int(max_items_per_run), 5000))
        self.max_scan_seconds = max(3, min(int(max_scan_seconds), 60))
        self._sync_lock = asyncio.Lock()
        self._destination_enrichment_lock = asyncio.Lock()
        self._vision_lock = asyncio.Lock()
        self._unsub_interval: Any = None
        self._unsub_destination_interval: Any = None
        self._unsub_destination_start: Any = None
        self._vision_status: dict[str, Any] = {
            "enabled": self.media_curation_mode == "hybrid",
            "state": "idle",
            "last_run_at": None,
            "last_trip_id": None,
            "processed": 0,
            "curated": 0,
            "fallbacks": 0,
            "error": None,
        }
        self._destination_enrichment_status: dict[str, Any] = {
            "enabled": True,
            "state": "idle",
            "last_run_at": None,
            "last_trip_id": None,
            "searched": 0,
            "updated": 0,
            "error": None,
            "interval_minutes": _DESTINATION_BACKGROUND_INTERVAL_MINUTES,
        }
        self._token_secret = secrets.token_bytes(32)

    async def async_initialize(self) -> None:
        await self.hass.async_add_executor_job(self.store.initialize)
        await self.onedrive.async_initialize()
        stored = self.onedrive.stored_settings()
        if not stored:
            # 2.6.5 makes the in-panel OneDrive setup the single source of
            # truth. On the first start after upgrading, migrate the legacy
            # config-entry values into the private OneDrive settings store.
            migrated_max_items = (
                2000 if int(self.max_items_per_run or 0) == 250 else self.max_items_per_run
            )
            await self.onedrive.async_reconfigure(
                self.onedrive.client_id,
                settings={
                    "settings_version": 2,
                    "folder_path": self.folder_path,
                    "auto_sync": self.auto_sync,
                    "auto_assign": self.auto_assign,
                    "sync_interval_minutes": self.sync_interval_minutes,
                    "recursive_subfolders": self.recursive_subfolders,
                    "date_buffer_days": self.date_buffer_days,
                    "max_items_per_run": migrated_max_items,
                    "max_scan_seconds": self.max_scan_seconds,
                },
            )
            stored = self.onedrive.stored_settings()
        elif int(stored.get("settings_version") or 0) < 2:
            migrated = dict(stored)
            migrated["settings_version"] = 2
            migrated["folder_path"] = normalize_onedrive_folder_path(
                migrated.get("folder_path") or self.folder_path
            )
            if int(migrated.get("max_items_per_run") or 0) == 250:
                migrated["max_items_per_run"] = 2000
            migrated.setdefault("max_scan_seconds", self.max_scan_seconds)
            await self.onedrive.async_reconfigure(
                self.onedrive.client_id,
                settings=migrated,
            )
            stored = self.onedrive.stored_settings()
        if stored:
            self.folder_path = normalize_onedrive_folder_path(
                stored.get("folder_path") or self.folder_path
            )
            self.auto_sync = bool(stored.get("auto_sync", self.auto_sync))
            self.auto_assign = bool(stored.get("auto_assign", self.auto_assign))
            self.sync_interval_minutes = max(
                5,
                min(
                    int(stored.get("sync_interval_minutes") or self.sync_interval_minutes),
                    1440,
                ),
            )
            self.recursive_subfolders = bool(
                stored.get("recursive_subfolders", self.recursive_subfolders)
            )
            self.date_buffer_days = max(
                0,
                min(
                    int(stored.get("date_buffer_days", self.date_buffer_days)),
                    30,
                ),
            )
            self.max_items_per_run = max(
                100,
                min(
                    int(stored.get("max_items_per_run", self.max_items_per_run)),
                    5000,
                ),
            )
            self.max_scan_seconds = max(
                3,
                min(
                    int(stored.get("max_scan_seconds", self.max_scan_seconds)),
                    60,
                ),
            )
        self._reschedule_sync()
        self._reschedule_destination_enrichment()

    async def async_shutdown(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self._unsub_destination_interval:
            self._unsub_destination_interval()
            self._unsub_destination_interval = None
        if self._unsub_destination_start:
            self._unsub_destination_start()
            self._unsub_destination_start = None

    def _reschedule_sync(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self.auto_sync:
            self._unsub_interval = async_track_time_interval(
                self.hass,
                self._periodic_sync,
                timedelta(minutes=self.sync_interval_minutes),
            )

    def _reschedule_destination_enrichment(self) -> None:
        """Schedule bounded background planning-image enrichment."""
        if self._unsub_destination_interval:
            self._unsub_destination_interval()
            self._unsub_destination_interval = None
        if self._unsub_destination_start:
            self._unsub_destination_start()
            self._unsub_destination_start = None
        self._unsub_destination_interval = async_track_time_interval(
            self.hass,
            self._periodic_destination_enrichment,
            timedelta(minutes=_DESTINATION_BACKGROUND_INTERVAL_MINUTES),
        )
        self._unsub_destination_start = async_call_later(
            self.hass,
            _DESTINATION_INITIAL_DELAY_SECONDS,
            self._initial_destination_enrichment,
        )

    @callback
    def _initial_destination_enrichment(self, _now: datetime) -> None:
        self._unsub_destination_start = None
        self.hass.async_create_task(self._async_periodic_destination_enrichment())

    @callback
    def _periodic_destination_enrichment(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_periodic_destination_enrichment())

    async def _async_periodic_destination_enrichment(self) -> None:
        if self._destination_enrichment_lock.locked():
            return
        async with self._destination_enrichment_lock:
            trips = await self.manager.async_list_trips()
            active_trip = (
                str(trips.get("active_trip") or "")
                if isinstance(trips, dict)
                else ""
            )
            if not active_trip:
                return
            self._destination_enrichment_status.update(
                {
                    "state": "running",
                    "last_trip_id": active_trip,
                    "error": None,
                }
            )
            try:
                if self.vision_enabled:
                    await self.async_auto_curate_media(
                        active_trip,
                        limit=_VISION_BACKGROUND_BATCH,
                        include_experience=False,
                    )
                result = await self.async_auto_populate_destination_galleries(
                    active_trip,
                    limit=_DESTINATION_BACKGROUND_BATCH,
                    include_experience=False,
                )
            except (RoadplannerError, asyncio.TimeoutError) as err:
                self._destination_enrichment_status.update(
                    {
                        "state": "error",
                        "last_run_at": utc_now_iso(),
                        "searched": 0,
                        "updated": 0,
                        "error": str(err)[:500],
                    }
                )
                _LOGGER.debug("Background destination image enrichment failed: %s", err)
                return
            except Exception as err:  # noqa: BLE001 - background tasks must fail closed
                self._destination_enrichment_status.update(
                    {
                        "state": "error",
                        "last_run_at": utc_now_iso(),
                        "searched": 0,
                        "updated": 0,
                        "error": type(err).__name__,
                    }
                )
                _LOGGER.exception("Unexpected destination image enrichment failure")
                return
            self._destination_enrichment_status.update(
                {
                    "state": "idle",
                    "last_run_at": utc_now_iso(),
                    "searched": int(result.get("searched") or 0),
                    "updated": int(result.get("updated") or 0),
                    "error": None,
                }
            )
            if int(result.get("updated") or 0):
                self.hass.bus.async_fire(
                    EVENT_ROADPLANNER_UPDATED,
                    {
                        "experience_changed": True,
                        "source": "destination_image_enrichment",
                    },
                )

    async def async_reconfigure_onedrive(
        self,
        *,
        client_id: str,
        folder_path: str,
        auto_sync: bool,
        auto_assign: bool,
        sync_interval_minutes: int,
        recursive_subfolders: bool = True,
        date_buffer_days: int = 3,
        max_items_per_run: int = 2000,
        max_scan_seconds: int = _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    ) -> dict[str, Any]:
        """Update OneDrive Personal settings from the in-panel setup wizard."""
        self.folder_path = normalize_onedrive_folder_path(
            folder_path or "Pictures/Camera Roll"
        )
        self.auto_sync = bool(auto_sync)
        self.auto_assign = bool(auto_assign)
        self.sync_interval_minutes = max(5, min(int(sync_interval_minutes), 1440))
        self.recursive_subfolders = bool(recursive_subfolders)
        self.date_buffer_days = max(0, min(int(date_buffer_days), 30))
        self.max_items_per_run = max(100, min(int(max_items_per_run), 5000))
        self.max_scan_seconds = max(3, min(int(max_scan_seconds), 60))
        await self.onedrive.async_reconfigure(
            client_id,
            settings={
                "settings_version": 2,
                "folder_path": self.folder_path,
                "auto_sync": self.auto_sync,
                "auto_assign": self.auto_assign,
                "sync_interval_minutes": self.sync_interval_minutes,
                "recursive_subfolders": self.recursive_subfolders,
                "date_buffer_days": self.date_buffer_days,
                "max_items_per_run": self.max_items_per_run,
                "max_scan_seconds": self.max_scan_seconds,
            },
        )
        self._reschedule_sync()
        return {
            **self.onedrive.status(),
            "folder_path": self.folder_path,
            "auto_sync": self.auto_sync,
            "auto_assign": self.auto_assign,
            "sync_interval_minutes": self.sync_interval_minutes,
            "recursive_subfolders": self.recursive_subfolders,
            "date_buffer_days": self.date_buffer_days,
            "max_items_per_run": self.max_items_per_run,
            "max_scan_seconds": self.max_scan_seconds,
        }

    @callback
    def _periodic_sync(self, _now: datetime) -> None:
        if not self.onedrive.connected:
            return
        self.hass.async_create_task(self._async_periodic_sync())

    async def _async_periodic_sync(self) -> None:
        try:
            result = await self.async_sync_all_trips()
        except RoadplannerError as err:
            _LOGGER.debug("Periodic OneDrive photo sync failed: %s", err)
            return
        changed_trips = [
            item
            for item in result.get("trips", [])
            if isinstance(item, dict)
            and (
                int(item.get("added") or 0)
                or int(item.get("updated") or 0)
                or int(item.get("removed") or 0)
            )
        ]
        if changed_trips and self.vision_enabled:
            for item in changed_trips:
                trip_id = str(item.get("trip_id") or "")
                if not trip_id:
                    continue
                try:
                    await self.async_auto_curate_media(
                        trip_id,
                        limit=_VISION_BACKGROUND_BATCH,
                        include_experience=False,
                    )
                except (RoadplannerError, asyncio.TimeoutError):
                    pass
        if changed_trips:
            self.hass.bus.async_fire(
                EVENT_ROADPLANNER_UPDATED,
                {"experience_changed": True, "source": "onedrive_sync"},
            )

    def _token(self, trip_id: str, media_id: str, kind: str) -> str:
        expires = int(datetime.now(timezone.utc).timestamp()) + _MEDIA_TOKEN_TTL_SECONDS
        payload = f"{trip_id}|{media_id}|{kind}|{expires}"
        signature = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def validate_token(self, trip_id: str, media_id: str, kind: str, token: str) -> bool:
        try:
            expires_text, signature = token.split(".", 1)
            expires = int(expires_text)
        except (ValueError, AttributeError):
            return False
        if expires < int(datetime.now(timezone.utc).timestamp()):
            return False
        payload = f"{trip_id}|{media_id}|{kind}|{expires}"
        expected = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    async def async_media_redirect_url(self, trip_id: str, media_id: str, kind: str) -> str:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = next((item for item in state["media"] if item.get("id") == media_id), None)
        if media is None:
            raise ValidationError("Foto nicht gefunden")
        if kind == "thumbnail":
            return await self.onedrive.async_thumbnail_url(str(media["provider_item_id"]), "large")
        return await self.onedrive.async_download_url(str(media["provider_item_id"]))

    @property
    def vision_enabled(self) -> bool:
        """Return whether opt-in hybrid Vision curation can run."""
        return bool(
            self.media_curation_mode == "hybrid"
            and self.provider is not None
            and self.provider.configured
            and callable(getattr(self.provider, "async_analyze_images", None))
        )

    @staticmethod
    def _vision_context(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        place_parts = [
            _clean(location.get("label") or location.get("address"), 600),
            _clean(location.get("city") or stop.get("place") or stop.get("city"), 300),
            _clean(location.get("country") or location.get("country_code") or stop.get("country"), 200),
        ]
        description = " ".join(
            part
            for part in (
                _clean(stop.get("notes"), 1_000),
                _clean(stop.get("description"), 1_000),
                _clean(day.get("title") or day.get("summary"), 500),
            )
            if part
        )
        return {
            "day_id": str(day.get("id") or ""),
            "day_date": str(day.get("date") or ""),
            "stop_id": str(stop.get("id") or ""),
            "stop_name": _clean(stop.get("name"), 500),
            "category": _clean(stop.get("type") or stop.get("category"), 200),
            "place": ", ".join(part for part in place_parts if part),
            "description": description[:2_000],
            "latitude": location.get("latitude", location.get("lat")),
            "longitude": location.get(
                "longitude", location.get("lon", location.get("lng"))
            ),
        }

    async def _async_fetch_vision_image(
        self,
        *,
        image_id: str,
        url: str,
        label: str,
    ) -> AssistantImageInput | None:
        """Fetch one bounded thumbnail for a semantic Vision call."""
        if not url or not str(url).startswith("https://"):
            return None
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": (
                "HomeAssistant-Roadplanner/"
                f"{getattr(self.provider, 'model', 'vision')} (media curation)"
            )
        }
        try:
            async with session.get(
                str(url),
                headers=headers,
                timeout=ClientTimeout(total=_VISION_IMAGE_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                mime_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
                if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                    return None
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _VISION_MAX_IMAGE_BYTES:
                    return None
                data = await response.content.read(_VISION_MAX_IMAGE_BYTES + 1)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None
        if not data or len(data) > _VISION_MAX_IMAGE_BYTES:
            return None
        return AssistantImageInput(
            image_id=image_id,
            data=data,
            mime_type=mime_type,
            label=label,
        )

    async def _async_image_inputs(
        self,
        *,
        kind: str,
        candidates: list[dict[str, Any]],
    ) -> list[AssistantImageInput]:
        """Resolve bounded thumbnails after deterministic local preselection."""
        jobs: list[Any] = []
        for item in candidates[: self.media_vision_max_candidates]:
            image_id = str(item.get("id") or "").strip()
            if not image_id:
                continue
            if kind == "travel":
                provider_item_id = str(item.get("provider_item_id") or "").strip()
                if not provider_item_id:
                    continue
                try:
                    url = await self.onedrive.async_thumbnail_url(provider_item_id, "large")
                except RoadplannerError:
                    continue
                label = " · ".join(
                    part
                    for part in (
                        _clean(item.get("name"), 300),
                        _clean(item.get("taken_at"), 100),
                        f"lokaler Score {item.get('selection_score')}"
                        if item.get("selection_score") is not None
                        else "",
                    )
                    if part
                )
            else:
                url = str(item.get("thumbnail_url") or item.get("image_url") or "")
                label = " · ".join(
                    part
                    for part in (
                        _clean(item.get("title") or item.get("alt"), 400),
                        _clean(item.get("provider"), 100),
                        _clean(item.get("license"), 100),
                        f"lokaler Score {item.get('selection_score')}"
                        if item.get("selection_score") is not None
                        else "",
                    )
                    if part
                )
            jobs.append(
                self._async_fetch_vision_image(
                    image_id=image_id,
                    url=url,
                    label=label,
                )
            )
        if not jobs:
            return []
        results = await asyncio.gather(*jobs, return_exceptions=True)
        inputs: list[AssistantImageInput] = []
        total = 0
        for result in results:
            if not isinstance(result, AssistantImageInput):
                continue
            if total + len(result.data) > _VISION_MAX_TOTAL_BYTES:
                break
            total += len(result.data)
            inputs.append(result)
        return inputs

    async def _async_semantic_curation(
        self,
        *,
        trip_id: str,
        kind: str,
        day: dict[str, Any],
        stop: dict[str, Any],
        candidates: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Apply Vision only after local deterministic candidate reduction."""
        allowed_ids = [
            str(item.get("id") or "")
            for item in candidates[: self.media_vision_max_candidates]
            if str(item.get("id") or "")
        ]
        local_order = list(allowed_ids)
        context = self._vision_context(day, stop)
        model = str(getattr(self.provider, "model", "") or "")
        fingerprint = selection_fingerprint(
            kind=kind,
            context=context,
            candidates=candidates[: self.media_vision_max_candidates],
            model=model,
        )
        if (
            not force
            and isinstance(existing, dict)
            and existing.get("status") == "ready"
            and str(existing.get("fingerprint") or "") == fingerprint
        ):
            return deepcopy(existing)

        local_value = {
            "stop_id": str(stop.get("id") or ""),
            "kind": kind,
            "status": "local",
            "fingerprint": fingerprint,
            "selection_version": 1,
            "mode": "local",
            "model": None,
            "candidate_ids": allowed_ids,
            "cover_id": allowed_ids[0] if allowed_ids else None,
            "highlight_ids": allowed_ids[: self.media_vision_max_highlights],
            "rejected_ids": [],
            "reasons": {},
            "summary": "Lokale Vorauswahl nach Zuordnung, Qualität, Dubletten und Serien.",
            "usage": {},
            "selected_at": utc_now_iso(),
            "error": None,
        }
        if len(allowed_ids) < 2 or not self.vision_enabled:
            return local_value

        image_inputs = await self._async_image_inputs(kind=kind, candidates=candidates)
        if len(image_inputs) < 2:
            return {
                **local_value,
                "status": "local",
                "mode": "local_fallback",
                "error": "Zu wenige Bildvorschaudaten für die KI-Auswahl verfügbar",
            }
        input_ids = [item.image_id for item in image_inputs]
        reservation = await self.hass.async_add_executor_job(
            self.store.reserve_vision_call,
            trip_id,
            datetime.now(timezone.utc).date().isoformat(),
            self.media_vision_daily_limit,
        )
        if not reservation.get("reserved"):
            return {
                **local_value,
                "status": "quota_limited",
                "mode": "local_fallback",
                "error": "Tageslimit für KI-Bildauswahl erreicht",
                "usage": {"daily_limit": reservation},
            }

        system, prompt = build_vision_prompt(
            kind=kind,
            context=context,
            candidate_ids=input_ids,
            max_highlights=self.media_vision_max_highlights,
        )
        manual_cover_id = None
        if kind == "travel":
            manual_cover_id = next(
                (
                    str(item.get("id") or "")
                    for item in candidates
                    if item.get("is_cover") and str(item.get("id") or "") in input_ids
                ),
                None,
            )
        try:
            result = await self.provider.async_analyze_images(
                system_instruction=system,
                prompt=prompt,
                images=image_inputs,
                schema=VISION_SELECTION_SCHEMA,
                max_output_tokens=3_072,
            )
            selection = normalize_vision_selection(
                result.value,
                allowed_ids=input_ids,
                local_order=[item for item in local_order if item in input_ids],
                max_highlights=self.media_vision_max_highlights,
                manual_cover_id=manual_cover_id,
            )
        except (RoadplannerError, asyncio.TimeoutError) as err:
            return {
                **local_value,
                "status": "error",
                "mode": "local_fallback",
                "error": str(err)[:1_000],
            }
        return {
            **local_value,
            "status": "ready",
            "mode": "hybrid_vision",
            "model": result.model_version or model or None,
            "candidate_ids": input_ids,
            "cover_id": selection["cover_id"],
            "highlight_ids": selection["highlight_ids"],
            "rejected_ids": selection["rejected_ids"],
            "reasons": selection["reasons"],
            "summary": selection["summary"],
            "usage": deepcopy(result.usage),
            "selected_at": utc_now_iso(),
            "error": None,
        }

    async def async_curate_stop_media(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Curate one stop's locally prefiltered OneDrive photos."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = self._find_stop(days, day_id, stop_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [
            item
            for item in list(state.get("media") or [])
            if isinstance(item, dict) and str(item.get("linked_stop_id") or "") == stop_id
        ]
        local_candidates, _stats = select_media_highlights(
            media,
            limit=self.media_vision_max_candidates,
        )
        if not local_candidates:
            raise ValidationError("Für diesen Stopp sind noch keine eigenen Fotos vorhanden")
        curation = await self._async_semantic_curation(
            trip_id=trip_id,
            kind="travel",
            day=day,
            stop=stop,
            candidates=local_candidates,
            existing=(state.get("media_curations") or {}).get(stop_id),
            force=force,
        )
        stored = await self.hass.async_add_executor_job(
            self.store.upsert_media_curation,
            trip_id,
            curation,
        )
        return {
            "curation": stored,
            "experience": await self.async_panel_payload(trip_id, days=days),
        }

    async def async_auto_curate_media(
        self,
        trip_id: str,
        *,
        limit: int = _VISION_BACKGROUND_BATCH,
        force: bool = False,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        """Curate a bounded number of stop albums without blocking the UI."""
        if not self.vision_enabled:
            result: dict[str, Any] = {"processed": 0, "curated": 0, "fallbacks": 0}
            if include_experience:
                result["experience"] = await self.async_panel_payload(trip_id)
            return result
        if self._vision_lock.locked():
            return {"processed": 0, "curated": 0, "fallbacks": 0, "busy": True}
        async with self._vision_lock:
            payload = await self.manager.async_get_assistant_payload(trip_id)
            days = _all_days(payload)
            state = await self.hass.async_add_executor_job(self.store.load, trip_id)
            media = [item for item in list(state.get("media") or []) if isinstance(item, dict)]
            by_stop: dict[str, list[dict[str, Any]]] = {}
            for item in media:
                stop_id = str(item.get("linked_stop_id") or "")
                if stop_id:
                    by_stop.setdefault(stop_id, []).append(item)
            curations = state.get("media_curations") if isinstance(state.get("media_curations"), dict) else {}
            today = dt_util.now().date()

            def priority(day: dict[str, Any]) -> tuple[int, int, str]:
                value = _day_date(day)
                if value == today:
                    return (0, 0, str(day.get("id") or ""))
                if value and value > today:
                    return (1, value.toordinal(), str(day.get("id") or ""))
                return (2, -(value.toordinal() if value else 0), str(day.get("id") or ""))

            selected: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
            for day in sorted(days, key=priority):
                for stop in _stops(day):
                    stop_id = str(stop.get("id") or "")
                    items = by_stop.get(stop_id, [])
                    if len(items) < 2:
                        continue
                    candidates, _stats = select_media_highlights(
                        items,
                        limit=self.media_vision_max_candidates,
                    )
                    fingerprint = selection_fingerprint(
                        kind="travel",
                        context=self._vision_context(day, stop),
                        candidates=candidates,
                        model=str(getattr(self.provider, "model", "") or ""),
                    )
                    existing = curations.get(stop_id)
                    if (
                        not force
                        and isinstance(existing, dict)
                        and existing.get("status") == "ready"
                        and str(existing.get("fingerprint") or "") == fingerprint
                    ):
                        continue
                    selected.append((day, stop, candidates))
                    if len(selected) >= max(1, min(int(limit), 10)):
                        break
                if len(selected) >= max(1, min(int(limit), 10)):
                    break

            self._vision_status.update(
                {
                    "state": "running",
                    "last_trip_id": trip_id,
                    "processed": 0,
                    "curated": 0,
                    "fallbacks": 0,
                    "error": None,
                }
            )
            curated = 0
            fallbacks = 0
            for day, stop, candidates in selected:
                stop_id = str(stop.get("id") or "")
                curation = await self._async_semantic_curation(
                    trip_id=trip_id,
                    kind="travel",
                    day=day,
                    stop=stop,
                    candidates=candidates,
                    existing=curations.get(stop_id),
                    force=force,
                )
                await self.hass.async_add_executor_job(
                    self.store.upsert_media_curation,
                    trip_id,
                    curation,
                )
                if curation.get("status") == "ready":
                    curated += 1
                else:
                    fallbacks += 1
            self._vision_status.update(
                {
                    "state": "idle",
                    "last_run_at": utc_now_iso(),
                    "last_trip_id": trip_id,
                    "processed": len(selected),
                    "curated": curated,
                    "fallbacks": fallbacks,
                    "error": None,
                }
            )
            result = {
                "processed": len(selected),
                "curated": curated,
                "fallbacks": fallbacks,
            }
            if include_experience:
                result["experience"] = await self.async_panel_payload(trip_id, days=days)
            return result

    async def async_panel_payload(self, trip_id: str, *, days: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not trip_id:
            return {
                "decisions": [],
                "media": [],
                "destination_galleries": {},
                "stats": {},
                "by_day": {},
                "by_stop": {},
                "vision": deepcopy(self._vision_status),
                "onedrive": self.onedrive.status(),
            }
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media: list[dict[str, Any]] = []
        by_day: dict[str, list[str]] = {}
        by_stop: dict[str, list[str]] = {}
        for raw in state["media"]:
            item = deepcopy(raw)
            media_id = str(item["id"])
            item["thumbnail_url"] = f"/api/roadplanner/media/thumbnail/{quote(trip_id, safe='')}/{quote(media_id, safe='')}?token={self._token(trip_id, media_id, 'thumbnail')}"
            item["original_url"] = f"/api/roadplanner/media/original/{quote(trip_id, safe='')}/{quote(media_id, safe='')}?token={self._token(trip_id, media_id, 'original')}"
            media.append(item)
            if item.get("linked_day_id"):
                by_day.setdefault(str(item["linked_day_id"]), []).append(media_id)
            if item.get("linked_stop_id"):
                by_stop.setdefault(str(item["linked_stop_id"]), []).append(media_id)
        decisions = resolve_decision_media_references(state["decisions"], media)
        destination_galleries = deepcopy(state.get("destination_galleries") or {})
        media_curations = (
            state.get("media_curations")
            if isinstance(state.get("media_curations"), dict)
            else {}
        )
        presentation = build_media_presentation(
            media,
            limit=self.media_vision_max_highlights,
            curations=(media_curations if self.media_curation_mode == "hybrid" else {}),
        )
        if days is None:
            try:
                payload = await self.manager.async_get_assistant_payload(trip_id)
                days = list(payload.get("days", {}).get("days", []) or [])
            except RoadplannerError:
                days = []
        planning_day_covers: dict[str, dict[str, Any]] = {}
        for day in days or []:
            day_id = str(day.get("id") or "")
            if not day_id or day_id in presentation.get("day_covers", {}):
                continue
            for stop in _stops(day):
                gallery = destination_galleries.get(str(stop.get("id") or ""))
                if not isinstance(gallery, dict):
                    continue
                images = list(gallery.get("images") or [])
                if not images:
                    continue
                primary_id = str(gallery.get("primary_image_id") or "")
                primary = next((item for item in images if str(item.get("id") or "") == primary_id), images[0])
                planning_day_covers[day_id] = deepcopy(primary)
                break
        presentation["planning_day_covers"] = planning_day_covers
        return {
            "decisions": decisions,
            "media": media,
            "destination_galleries": destination_galleries,
            "presentation": presentation,
            "by_day": by_day,
            "by_stop": by_stop,
            "destination_enrichment": deepcopy(self._destination_enrichment_status),
            "vision": {
                **deepcopy(self._vision_status),
                "enabled": self.vision_enabled,
                "mode": self.media_curation_mode,
                "model": str(getattr(self.provider, "model", "") or "") or None,
                "max_candidates": self.media_vision_max_candidates,
                "max_highlights": self.media_vision_max_highlights,
                "daily_limit": self.media_vision_daily_limit,
                "usage": deepcopy(state.get("vision_usage") or {}),
            },
            "stats": {
                "decision_count": len(decisions),
                "open_decision_count": sum(1 for item in decisions if item.get("status") in {"draft", "open"}),
                "media_count": len(media),
                "automatic_count": sum(1 for item in media if item.get("assignment_status") == "automatic"),
                "suggested_count": sum(1 for item in media if item.get("assignment_status") == "suggested"),
                "unassigned_count": sum(1 for item in media if not item.get("linked_day_id")),
                "destination_gallery_count": sum(
                    1 for item in destination_galleries.values()
                    if isinstance(item, dict) and item.get("images")
                ),
                "destination_gallery_error_count": sum(
                    1 for item in destination_galleries.values()
                    if isinstance(item, dict) and item.get("status") == "error"
                ),
                "media_duplicate_count": int(
                    presentation.get("curation", {}).get("duplicate_count", 0)
                ),
                "media_burst_suppressed_count": int(
                    presentation.get("curation", {}).get("burst_suppressed_count", 0)
                ),
                "featured_stop_count": int(
                    presentation.get("curation", {}).get("featured_stop_count", 0)
                ),
                "featured_day_count": int(
                    presentation.get("curation", {}).get("featured_day_count", 0)
                ),
                "vision_curated_stop_count": int(
                    presentation.get("curation", {}).get("vision_curated_stop_count", 0)
                ),
            },
            "onedrive": {
                **self.onedrive.status(),
                "folder_path": self.folder_path,
                "auto_sync": self.auto_sync,
                "auto_assign": self.auto_assign,
                "sync_interval_minutes": self.sync_interval_minutes,
                "recursive_subfolders": self.recursive_subfolders,
                "date_buffer_days": self.date_buffer_days,
                "max_items_per_run": self.max_items_per_run,
                "max_scan_seconds": self.max_scan_seconds,
                "settings_source": "photo_setup",
                "sync_scope": "active_trip",
                "sync_state": deepcopy(state.get("media_sync") or {}),
            },
        }

    async def async_start_onedrive_auth(self) -> dict[str, Any]:
        return await self.onedrive.async_start_device_authorization()

    async def async_poll_onedrive_auth(self) -> dict[str, Any]:
        return await self.onedrive.async_poll_device_authorization()

    async def async_disconnect_onedrive(self) -> dict[str, Any]:
        await self.onedrive.async_disconnect()
        return self.onedrive.status()

    async def async_sync_all_trips(self) -> dict[str, Any]:
        """Synchronize only the globally active trip in the background.

        Manual synchronization from the panel still targets the currently
        selected trip.  Limiting periodic work to the active trip avoids
        repeatedly traversing the same large camera archive for old trips.
        """
        trips = await self.manager.async_list_trips()
        active_trip = str(trips.get("active_trip") or "") if isinstance(trips, dict) else ""
        if not active_trip:
            return {"ok": True, "active_trip_only": True, "trips": []}
        try:
            result = await self.async_sync_trip(active_trip)
        except RoadplannerError as err:
            result = {"trip_id": active_trip, "ok": False, "error": str(err)}
        return {
            "ok": bool(result.get("ok", False)),
            "active_trip_only": True,
            "trips": [result],
        }

    async def _new_initial_scan_state(
        self,
        *,
        folder: dict[str, Any],
        range_key: str,
    ) -> dict[str, Any]:
        folder_id = str(folder.get("id") or "")
        if not folder_id:
            raise ValidationError("OneDrive-Ordner-ID fehlt")
        baseline_delta_link = await self.onedrive.async_latest_delta(folder_id)
        now = utc_now_iso()
        root_name = str(folder.get("name") or self.folder_path or "Fotoordner")
        root_path = normalize_onedrive_folder_path(
            self.folder_path or root_name,
            allow_empty=True,
        ) or root_name
        root_hint = _folder_date_hint(root_name)
        return {
            "strategy_version": _MEDIA_SYNC_STRATEGY_VERSION,
            "folder_id": folder_id,
            "folder_path": self.folder_path,
            "trip_date_range": range_key,
            "recursive_subfolders": self.recursive_subfolders,
            "date_buffer_days": self.date_buffer_days,
            "max_items_per_run": self.max_items_per_run,
            "mode": _INITIAL_SCAN_MODE,
            "baseline_delta_link": baseline_delta_link,
            "scan_queue": [
                {
                    "folder_id": folder_id,
                    "name": root_name,
                    "path": root_path,
                    "date_hint": _hint_to_json(root_hint),
                    "next_link": None,
                }
            ],
            "scan_queued_folder_ids": [folder_id],
            "scan_seen_ids": [],
            "scan_started_at": now,
            "scan_stats": {
                "runs": 0,
                "entries_examined": 0,
                "folders_examined": 0,
                "folders_discovered": 1,
                "folders_completed": 0,
                "folders_skipped": 0,
                "hidden_folders_skipped": 0,
                "dated_folders_skipped": 0,
                "photo_files_examined": 0,
                "relevant_photos": 0,
                "outside_window_skipped": 0,
                "without_date_skipped": 0,
                "non_image_skipped": 0,
                "current_folder": root_path,
                "last_run_duration_ms": 0,
                "last_run_limit_reason": None,
            },
        }

    @staticmethod
    def _scan_state_matches(
        sync_state: dict[str, Any],
        *,
        folder_id: str,
        range_key: str,
        recursive_subfolders: bool,
        date_buffer_days: int,
    ) -> bool:
        return (
            int(sync_state.get("strategy_version") or 0)
            == _MEDIA_SYNC_STRATEGY_VERSION
            and str(sync_state.get("folder_id") or "") == folder_id
            and str(sync_state.get("trip_date_range") or "") == range_key
            and bool(sync_state.get("recursive_subfolders", True))
            == bool(recursive_subfolders)
            and int(sync_state.get("date_buffer_days") or 0)
            == int(date_buffer_days)
            and str(sync_state.get("mode") or "")
            in {_INITIAL_SCAN_MODE, _DELTA_CATCHUP_MODE, _DELTA_MODE}
        )

    async def _initial_scan_batch(
        self,
        sync_state: dict[str, Any],
        *,
        window: tuple[date, date],
        days: list[dict[str, Any]],
    ) -> dict[str, Any]:
        queue = [
            dict(item)
            for item in list(sync_state.get("scan_queue") or [])
            if isinstance(item, dict)
        ]
        queued_ids = {
            str(item)
            for item in list(sync_state.get("scan_queued_folder_ids") or [])
            if str(item)
        }
        seen_ids = {
            str(item)
            for item in list(sync_state.get("scan_seen_ids") or [])
            if str(item)
        }
        stats = dict(sync_state.get("scan_stats") or {})
        stats["runs"] = int(stats.get("runs") or 0) + 1
        normalized: list[dict[str, Any]] = []
        processed_entries = 0
        started = monotonic()
        deadline = started + float(self.max_scan_seconds)
        limit_reason: str | None = None

        while queue:
            if processed_entries >= self.max_items_per_run:
                limit_reason = "entry_budget"
                break
            if monotonic() >= deadline:
                limit_reason = "time_budget"
                break

            current = queue[0]
            folder_id = str(current.get("folder_id") or "")
            if not folder_id:
                queue.pop(0)
                continue
            current_path = str(
                current.get("path") or current.get("name") or "Fotoordner"
            )
            current_hint = _hint_from_json(current.get("date_hint"))
            stats["current_folder"] = current_path
            remaining = max(1, self.max_items_per_run - processed_entries)
            page_size = max(1, min(_SCAN_PAGE_SIZE, remaining))
            result = await self.onedrive.async_list_children(
                folder_id,
                cursor_link=str(current.get("next_link") or "") or None,
                page_size=page_size,
            )
            items = (
                result.get("items")
                if isinstance(result.get("items"), list)
                else []
            )
            processed_entries += len(items)
            stats["entries_examined"] = int(stats.get("entries_examined") or 0) + len(items)

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                is_folder = isinstance(raw.get("folder"), dict) or isinstance(
                    raw.get("package"), dict
                )
                if is_folder:
                    stats["folders_examined"] = int(stats.get("folders_examined") or 0) + 1
                    child_id = str(raw.get("id") or "")
                    child_name = str(raw.get("name") or "Ordner")
                    include, reason, child_hint = _folder_scan_decision(
                        child_name,
                        window,
                        recursive=self.recursive_subfolders,
                        parent_hint=current_hint,
                    )
                    if include and child_id and child_id not in queued_ids:
                        child_path = _join_display_path(current_path, child_name)
                        queue.append(
                            {
                                "folder_id": child_id,
                                "name": child_name,
                                "path": child_path,
                                "date_hint": _hint_to_json(child_hint),
                                "next_link": None,
                            }
                        )
                        queued_ids.add(child_id)
                        stats["folders_discovered"] = int(
                            stats.get("folders_discovered") or 0
                        ) + 1
                    elif not include:
                        stats["folders_skipped"] = int(stats.get("folders_skipped") or 0) + 1
                        if reason == "hidden_or_generated":
                            stats["hidden_folders_skipped"] = int(
                                stats.get("hidden_folders_skipped") or 0
                            ) + 1
                        elif reason == "outside_trip_window":
                            stats["dated_folders_skipped"] = int(
                                stats.get("dated_folders_skipped") or 0
                            ) + 1
                    continue

                media = _provider_media(raw)
                if media is None:
                    stats["non_image_skipped"] = int(stats.get("non_image_skipped") or 0) + 1
                    continue
                stats["photo_files_examined"] = int(
                    stats.get("photo_files_examined") or 0
                ) + 1
                local_date = _media_local_date(media)
                if local_date is None:
                    stats["without_date_skipped"] = int(
                        stats.get("without_date_skipped") or 0
                    ) + 1
                    continue
                if not (window[0] <= local_date <= window[1]):
                    stats["outside_window_skipped"] = int(
                        stats.get("outside_window_skipped") or 0
                    ) + 1
                    continue
                provider_id = str(media.get("provider_item_id") or "")
                if not provider_id:
                    continue
                seen_ids.add(provider_id)
                if self.auto_assign:
                    media.update(self._assignment_for(media, days))
                normalized.append(media)
                stats["relevant_photos"] = int(stats.get("relevant_photos") or 0) + 1

            next_link = str(result.get("next_link") or "") or None
            if next_link:
                queue[0]["next_link"] = next_link
            else:
                queue.pop(0)
                stats["folders_completed"] = int(stats.get("folders_completed") or 0) + 1

        duration_ms = max(0, int((monotonic() - started) * 1000))
        stats["last_run_duration_ms"] = duration_ms
        stats["last_run_limit_reason"] = limit_reason
        sync_state["scan_queue"] = queue
        sync_state["scan_queued_folder_ids"] = sorted(queued_ids)
        sync_state["scan_seen_ids"] = sorted(seen_ids)
        sync_state["scan_stats"] = stats
        sync_state["last_run_entry_count"] = processed_entries
        sync_state["last_relevant_count"] = len(normalized)
        sync_state["last_sync_at"] = utc_now_iso()
        completed = not queue
        if completed:
            sync_state["mode"] = _DELTA_CATCHUP_MODE
            sync_state["next_link"] = str(sync_state.get("baseline_delta_link") or "") or None
            sync_state["initial_scan_completed_at"] = utc_now_iso()
        return {
            "normalized": normalized,
            "remove_ids": set(),
            "completed": completed,
            "resync": False,
            "finalize_initial": False,
        }

    def _normalize_delta_items(
        self,
        raw_items: list[dict[str, Any]],
        *,
        window: tuple[date, date],
        days: list[dict[str, Any]],
        seen_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        remove_ids: set[str] = set()
        counters = {
            "delta_entries_examined": 0,
            "delta_relevant_photos": 0,
            "delta_outside_window": 0,
            "delta_deleted": 0,
            "delta_without_date": 0,
        }
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            counters["delta_entries_examined"] += 1
            provider_id = str(raw.get("id") or "")
            if raw.get("deleted"):
                if provider_id:
                    remove_ids.add(provider_id)
                    counters["delta_deleted"] += 1
                continue
            if isinstance(raw.get("folder"), dict) or isinstance(raw.get("package"), dict):
                continue
            media = _provider_media(raw)
            if media is None:
                # If a former image is converted or replaced by a non-image,
                # remove the stale Roadplanner reference when one exists.
                if provider_id and isinstance(raw.get("file"), dict):
                    remove_ids.add(provider_id)
                continue
            local_date = _media_local_date(media)
            if local_date is None:
                counters["delta_without_date"] += 1
                continue
            if not (window[0] <= local_date <= window[1]):
                if provider_id:
                    remove_ids.add(provider_id)
                counters["delta_outside_window"] += 1
                continue
            if provider_id and seen_ids is not None:
                seen_ids.add(provider_id)
            if self.auto_assign:
                media.update(self._assignment_for(media, days))
            normalized.append(media)
            counters["delta_relevant_photos"] += 1
        return {
            "normalized": normalized,
            "remove_ids": remove_ids,
            "counters": counters,
        }

    async def _delta_batch(
        self,
        sync_state: dict[str, Any],
        *,
        folder_id: str,
        window: tuple[date, date],
        days: list[dict[str, Any]],
    ) -> dict[str, Any]:
        mode = str(sync_state.get("mode") or _DELTA_MODE)
        cursor = str(sync_state.get("next_link") or "") or None
        if cursor is None:
            if mode == _DELTA_CATCHUP_MODE:
                cursor = str(sync_state.get("baseline_delta_link") or "") or None
            else:
                cursor = str(sync_state.get("delta_link") or "") or None
        if cursor is None:
            return {"resync": True, "normalized": [], "remove_ids": set(), "finalize_initial": False}

        delta = await self.onedrive.async_delta(
            folder_id,
            cursor_link=cursor,
            max_items=self.max_items_per_run,
        )
        if delta.get("resync"):
            return {"resync": True, "normalized": [], "remove_ids": set(), "finalize_initial": False}

        seen_ids = {
            str(item)
            for item in list(sync_state.get("scan_seen_ids") or [])
            if str(item)
        } if mode == _DELTA_CATCHUP_MODE else None
        processed = self._normalize_delta_items(
            [item for item in list(delta.get("items") or []) if isinstance(item, dict)],
            window=window,
            days=days,
            seen_ids=seen_ids,
        )
        next_link = str(delta.get("next_link") or "") or None
        final_delta = str(delta.get("delta_link") or "") or None
        if next_link:
            sync_state["next_link"] = next_link
        else:
            sync_state.pop("next_link", None)
            if final_delta:
                sync_state["delta_link"] = final_delta
        finalize_initial = mode == _DELTA_CATCHUP_MODE and not next_link
        if mode == _DELTA_CATCHUP_MODE:
            sync_state["scan_seen_ids"] = sorted(seen_ids or set())
            if finalize_initial:
                sync_state["mode"] = _DELTA_MODE
                sync_state["initial_scan_finalized_at"] = utc_now_iso()
                sync_state.pop("baseline_delta_link", None)
        sync_state["last_run_entry_count"] = len(delta.get("items") or [])
        sync_state["last_relevant_count"] = len(processed["normalized"])
        sync_state["last_sync_at"] = utc_now_iso()
        delta_stats = dict(sync_state.get("delta_stats") or {})
        for key, value in processed["counters"].items():
            delta_stats[key] = int(delta_stats.get(key) or 0) + int(value or 0)
        sync_state["delta_stats"] = delta_stats
        return {
            "resync": False,
            "normalized": processed["normalized"],
            "remove_ids": processed["remove_ids"],
            "finalize_initial": finalize_initial,
            "completed": not bool(next_link),
        }

    @staticmethod
    def _clean_scan_state(sync_state: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(sync_state)
        for key in (
            "scan_queue",
            "scan_queued_folder_ids",
            "scan_seen_ids",
            "baseline_delta_link",
        ):
            cleaned.pop(key, None)
        cleaned["mode"] = _DELTA_MODE
        return cleaned

    async def async_sync_trip(
        self, trip_id: str, *, full_rescan: bool = False
    ) -> dict[str, Any]:
        if not self.onedrive.connected:
            raise ValidationError("OneDrive ist nicht verbunden")
        async with self._sync_lock:
            payload = await self.manager.async_get_assistant_payload(trip_id)
            days = _all_days(payload)
            window = _trip_date_window(days, self.date_buffer_days)
            if window is None:
                return {
                    "ok": True,
                    "trip_id": trip_id,
                    "added": 0,
                    "updated": 0,
                    "removed": 0,
                    "total": 0,
                    "skipped": 0,
                    "message": "Die Reise besitzt noch keine datierten Reisetage.",
                }
            range_key = f"{window[0].isoformat()}..{window[1].isoformat()}"
            state = await self.hass.async_add_executor_job(self.store.load, trip_id)
            sync_state = dict(state.get("media_sync") or {})
            folder = await self.onedrive.async_resolve_folder(self.folder_path)
            folder_id = str(folder.get("id") or "")
            reset_scan = (
                full_rescan
                or not self._scan_state_matches(
                    sync_state,
                    folder_id=folder_id,
                    range_key=range_key,
                    recursive_subfolders=self.recursive_subfolders,
                    date_buffer_days=self.date_buffer_days,
                )
            )
            if reset_scan:
                sync_state = await self._new_initial_scan_state(
                    folder=folder,
                    range_key=range_key,
                )

            mode = str(sync_state.get("mode") or _INITIAL_SCAN_MODE)
            if mode == _INITIAL_SCAN_MODE:
                batch = await self._initial_scan_batch(
                    sync_state,
                    window=window,
                    days=days,
                )
            else:
                batch = await self._delta_batch(
                    sync_state,
                    folder_id=folder_id,
                    window=window,
                    days=days,
                )
                if batch.get("resync"):
                    sync_state = await self._new_initial_scan_state(
                        folder=folder,
                        range_key=range_key,
                    )
                    batch = await self._initial_scan_batch(
                        sync_state,
                        window=window,
                        days=days,
                    )
                    sync_state["resync_reason"] = "delta_cursor_expired"

            normalized = list(batch.get("normalized") or [])
            remove_ids = set(batch.get("remove_ids") or set())
            sync_state.update(
                {
                    "strategy_version": _MEDIA_SYNC_STRATEGY_VERSION,
                    "folder_id": folder_id,
                    "folder_path": self.folder_path,
                    "trip_date_range": range_key,
                    "recursive_subfolders": self.recursive_subfolders,
                    "date_buffer_days": self.date_buffer_days,
                    "max_items_per_run": self.max_items_per_run,
                    "max_scan_seconds": self.max_scan_seconds,
                    "truncated": str(sync_state.get("mode") or "") != _DELTA_MODE
                    or bool(sync_state.get("next_link")),
                }
            )

            removed = 0
            if remove_ids:
                removed += await self.hass.async_add_executor_job(
                    partial(
                        self.store.remove_media_by_provider_ids,
                        trip_id,
                        remove_ids,
                        sync_state=sync_state,
                    )
                )
            result = await self.hass.async_add_executor_job(
                partial(
                    self.store.upsert_media,
                    trip_id,
                    normalized,
                    sync_state=sync_state,
                )
            )

            if batch.get("finalize_initial"):
                current_state = await self.hass.async_add_executor_job(
                    self.store.load, trip_id
                )
                seen_ids = {
                    str(item)
                    for item in list(sync_state.get("scan_seen_ids") or [])
                    if str(item)
                }
                stale_ids = {
                    str(item.get("provider_item_id") or "")
                    for item in current_state.get("media", [])
                    if str(item.get("provider_item_id") or "")
                    and str(item.get("provider_item_id") or "") not in seen_ids
                }
                final_sync_state = self._clean_scan_state(sync_state)
                final_sync_state["truncated"] = False
                if stale_ids:
                    removed += await self.hass.async_add_executor_job(
                        partial(
                            self.store.remove_media_by_provider_ids,
                            trip_id,
                            stale_ids,
                            sync_state=final_sync_state,
                        )
                    )
                else:
                    await self.hass.async_add_executor_job(
                        partial(
                            self.store.upsert_media,
                            trip_id,
                            [],
                            sync_state=final_sync_state,
                        )
                    )
                sync_state = final_sync_state
                final_state = await self.hass.async_add_executor_job(
                    self.store.load, trip_id
                )
                result["total"] = len(final_state.get("media", []))

            scan_stats = dict(sync_state.get("scan_stats") or {})
            mode = str(sync_state.get("mode") or _DELTA_MODE)
            queue = list(sync_state.get("scan_queue") or [])
            scan_in_progress = mode != _DELTA_MODE or bool(sync_state.get("next_link"))
            if self.vision_enabled and (
                int(result.get("added") or 0)
                or int(result.get("updated") or 0)
                or removed
            ):
                self.hass.async_create_task(
                    self.async_auto_curate_media(
                        trip_id,
                        limit=_VISION_BACKGROUND_BATCH,
                        include_experience=False,
                    )
                )

            progress = {
                "phase": mode,
                "folders_discovered": int(scan_stats.get("folders_discovered") or 0),
                "folders_examined": int(scan_stats.get("folders_examined") or 0),
                "folders_completed": int(scan_stats.get("folders_completed") or 0),
                "folders_remaining": len(queue),
                "folders_skipped": int(scan_stats.get("folders_skipped") or 0),
                "hidden_folders_skipped": int(scan_stats.get("hidden_folders_skipped") or 0),
                "dated_folders_skipped": int(scan_stats.get("dated_folders_skipped") or 0),
                "entries_examined": int(scan_stats.get("entries_examined") or 0),
                "photo_files_examined": int(scan_stats.get("photo_files_examined") or 0),
                "relevant_photos": int(scan_stats.get("relevant_photos") or 0),
                "outside_window_skipped": int(scan_stats.get("outside_window_skipped") or 0),
                "current_folder": scan_stats.get("current_folder"),
                "last_run_duration_ms": int(scan_stats.get("last_run_duration_ms") or 0),
                "last_run_limit_reason": scan_stats.get("last_run_limit_reason"),
            }
            return {
                "ok": True,
                "trip_id": trip_id,
                **result,
                "removed": removed,
                "skipped": int(scan_stats.get("outside_window_skipped") or 0),
                "folder": folder.get("name"),
                "truncated": scan_in_progress,
                "scan_in_progress": scan_in_progress,
                "sync_mode": mode,
                "trip_date_range": range_key,
                "progress": progress,
            }

    def _assignment_for(self, media: dict[str, Any], days: list[dict[str, Any]]) -> dict[str, Any]:
        taken = _parse_datetime(media.get("taken_at") or media.get("created_at"))
        if taken is None:
            return {"assignment_status": "unassigned", "confidence": 0.0}
        local_date = dt_util.as_local(taken).date()
        exact_days = [day for day in days if _day_date(day) == local_date]
        nearby_days = [
            day
            for day in days
            if (day_date := _day_date(day)) is not None
            and abs((day_date - local_date).days) <= 1
        ]
        media_coord = _coordinate(media.get("location"))
        if media_coord is None:
            if not exact_days:
                return {"assignment_status": "unassigned", "confidence": 0.0}
            day_id = str(exact_days[0].get("id") or "")
            return {
                "linked_day_id": day_id or None,
                "assignment_status": "suggested",
                "confidence": 0.55,
            }

        stop_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for day in nearby_days:
            for stop in _stops(day):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    stop_candidates.append((_distance_m(media_coord, coord), day, stop))
        if stop_candidates:
            distance, day, stop = min(stop_candidates, key=lambda item: item[0])
            day_id = str(day.get("id") or "")
            stop_id = str(stop.get("id") or "")
            same_day = _day_date(day) == local_date
            if distance <= _AUTOMATIC_RADIUS_M and same_day:
                return {
                    "linked_day_id": day_id or None,
                    "linked_stop_id": stop_id or None,
                    "assignment_status": "automatic",
                    "confidence": round(max(0.75, 1 - distance / 3000), 4),
                    "distance_m": distance,
                }
            if distance <= _SUGGESTED_RADIUS_M:
                return {
                    "linked_day_id": day_id or None,
                    "linked_stop_id": stop_id or None,
                    "assignment_status": "suggested",
                    "confidence": round(max(0.45, 1 - distance / 10_000), 4),
                    "distance_m": distance,
                }
        if exact_days:
            return {
                "linked_day_id": str(exact_days[0].get("id") or "") or None,
                "assignment_status": "suggested",
                "confidence": 0.45,
            }
        return {"assignment_status": "unassigned", "confidence": 0.0}

    async def async_update_media(self, trip_id: str, media_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"linked_day_id", "linked_stop_id", "assignment_status", "caption", "is_cover"}
        filtered = {key: value for key, value in patch.items() if key in allowed}
        if "linked_stop_id" in filtered and filtered.get("linked_stop_id"):
            payload = await self.manager.async_get_assistant_payload(trip_id)
            found_day = None
            for day in _all_days(payload):
                if any(str(stop.get("id") or "") == str(filtered["linked_stop_id"]) for stop in _stops(day)):
                    found_day = str(day.get("id") or "")
                    break
            if not found_day:
                raise ValidationError("Der ausgewählte Stopp existiert nicht mehr")
            filtered["linked_day_id"] = found_day
        if not filtered.get("linked_day_id"):
            filtered["linked_day_id"] = None
            filtered["linked_stop_id"] = None
            filtered["assignment_status"] = "unassigned"
        else:
            filtered.setdefault("assignment_status", "manual")
        return await self.hass.async_add_executor_job(self.store.update_media, trip_id, media_id, filtered)

    async def async_delete_media(self, trip_id: str, media_id: str) -> dict[str, Any]:
        await self.hass.async_add_executor_job(self.store.delete_media, trip_id, media_id)
        return {"ok": True}

    @staticmethod
    def _destination_query(day: dict[str, Any], stop: dict[str, Any]) -> str:
        """Return a concise image query based on the stop identity/profile."""
        intent = analyze_destination(day, stop)
        return destination_image_query(day, stop, intent=intent)


    async def async_prepare_place_enrichment(
        self,
        *,
        user_id: str,
        trip_id: str,
        day_id: str | None = None,
        stop_id: str | None = None,
        limit: int = 20,
        use_ai_cleanup: bool = False,
    ) -> dict[str, Any]:
        """Return a reviewable full-place preview for incomplete stops."""
        if self.place_enrichment is None:
            raise ValidationError(
                "Die Ortsvervollständigung ist nicht aktiviert. Bitte Geocoding in den "
                "Roadplanner-Optionen einschalten."
            )
        payload = await self.manager.async_get_assistant_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "Ortsprofile können nur für die aktive Reise vorbereitet werden"
            )
        preview = await self.place_enrichment.async_prepare(
            user_id=user_id,
            trip_id=trip_id,
            days=_all_days(payload),
            day_id=day_id,
            stop_id=stop_id,
            limit=limit,
            use_ai_cleanup=use_ai_cleanup,
        )
        return {
            "preview": preview,
            "experience": await self.async_panel_payload(trip_id),
        }

    async def async_submit_place_enrichment(
        self,
        *,
        user_id: str,
        actor: str,
        trip_id: str,
        preview_id: str,
        selections: dict[str, str],
        manual_entries: dict[str, dict[str, Any]] | None = None,
        cleanup_confirmations: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Create one concrete, review-only ChangeSet from selected places."""
        if self.place_enrichment is None:
            raise ValidationError(
                "Die Ortsvervollständigung ist nicht aktiviert."
            )
        payload = await self.manager.async_get_assistant_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "Ortsprofile können nur für die aktive Reise übernommen werden"
            )
        operations, galleries = await self.place_enrichment.resolve_selections(
            user_id=user_id,
            trip_id=trip_id,
            preview_id=preview_id,
            selections={str(key): str(value) for key, value in selections.items()},
            manual_entries=manual_entries,
            cleanup_confirmations=cleanup_confirmations,
        )
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        revision = summary.get("revision")
        canonical_trip_id = str(trip.get("id") or payload.get("selected_trip_id") or "")
        if (
            not canonical_trip_id
            or isinstance(revision, bool)
            or not isinstance(revision, int)
        ):
            raise ValidationError(
                "Aktuelle Reise-ID oder Revision konnte nicht gelesen werden"
            )
        changeset_id = new_id("changeset")
        title = "Ortsprofile vervollständigen"
        count = len(operations)
        changeset: dict[str, Any] = {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": changeset_id,
            "trip_id": canonical_trip_id,
            "base_revision": revision,
            "created_at": utc_now_iso(),
            "title": title,
            "summary": (
                f"{count} bestätigte Ortsprofile mit Kartenpunkt, Adresse und "
                "verfügbaren Kontaktdaten ergänzen."
            ),
            "apply_mode": "review",
            "operations": operations,
            "open_questions": [],
            "assumptions": [],
            "research_notes": [
                "Die ausgewählten Ortsprofile wurden durch den Benutzer in der "
                "Roadplanner-Vorschau bestätigt."
            ],
            "metadata": {
                "created_by": "roadplanner_place_enrichment",
                "user_id": user_id,
                "actor": actor,
                "preview_id": preview_id,
                "review_only": True,
            },
        }
        source_digest = hashlib.sha256(
            json.dumps(
                changeset,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        ingest = await self.manager.async_ingest_external_changeset(
            changeset=changeset,
            title=title,
            source="roadplanner_place_enrichment",
            external_id=changeset_id,
            metadata={
                "place_enrichment": {
                    "user_id": user_id,
                    "actor": actor,
                    "preview_id": preview_id,
                    "review_only": True,
                }
            },
            source_payload_sha256=source_digest,
        )
        if galleries:
            await self.hass.async_add_executor_job(
                self.store.upsert_destination_galleries,
                trip_id,
                galleries,
            )
        self.hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {
                "experience_changed": bool(galleries),
                "source": "place_enrichment",
                "trip_id": trip_id,
            },
        )
        return {
            "request_id": preview_id,
            "changeset_id": changeset_id,
            "operation_count": count,
            "handoff": ingest.get("handoff"),
            "preview": ingest.get("preview"),
            "experience": await self.async_panel_payload(trip_id),
        }

    @staticmethod
    def _destination_query_fingerprint(
        day: dict[str, Any],
        stop: dict[str, Any],
        query: str,
    ) -> str:
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        value = {
            "day_id": day.get("id"),
            "stop_id": stop.get("id"),
            "query": query,
            "latitude": location.get("latitude", location.get("lat")),
            "longitude": location.get("longitude", location.get("lon", location.get("lng"))),
        }
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()

    @staticmethod
    def _find_stop(
        days: list[dict[str, Any]],
        day_id: str,
        stop_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve a stop even when the UI still carries its previous day ID."""
        for day in days:
            if str(day.get("id") or "") != day_id:
                continue
            for stop in _stops(day):
                if str(stop.get("id") or "") == stop_id:
                    return day, stop

        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for day in days:
            for stop in _stops(day):
                if str(stop.get("id") or "") == stop_id:
                    matches.append((day, stop))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValidationError(
                "Der ausgewählte Stopp ist mehreren Tagen zugeordnet. Bitte die Ansicht neu laden."
            )
        raise ValidationError("Der ausgewählte Stopp existiert nicht mehr")


    async def _destination_gallery_for_stop(
        self,
        trip_id: str,
        day: dict[str, Any],
        stop: dict[str, Any],
        *,
        existing_gallery: dict[str, Any] | None = None,
        force_vision: bool = False,
    ) -> dict[str, Any]:
        """Build a locally ranked gallery, optionally semantically curated."""
        query = self._destination_query(day, stop)
        if not query:
            raise ValidationError("Für diesen Stopp fehlen Angaben für die Bildsuche")
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        search_limit = (
            self.media_vision_max_candidates
            if self.vision_enabled
            else _DESTINATION_GALLERY_SIZE
        )
        result = await self.image_provider.async_search(
            query,
            limit=search_limit,
            latitude=location.get("latitude", location.get("lat")),
            longitude=location.get("longitude", location.get("lon", location.get("lng"))),
        )
        candidates = list(result.get("results") or [])[:search_limit]
        errors = dict(result.get("provider_errors") or {})
        existing_curation = (
            existing_gallery.get("curation")
            if isinstance(existing_gallery, dict)
            and isinstance(existing_gallery.get("curation"), dict)
            else None
        )
        curation: dict[str, Any] = {}
        images = candidates[:_DESTINATION_GALLERY_SIZE]
        if candidates:
            curation = await self._async_semantic_curation(
                trip_id=trip_id,
                kind="planning",
                day=day,
                stop=stop,
                candidates=candidates,
                existing=existing_curation,
                force=force_vision,
            )
            by_id = {
                str(item.get("id") or ""): item
                for item in candidates
                if str(item.get("id") or "")
            }
            ordered_ids = [
                str(item)
                for item in list(curation.get("highlight_ids") or [])
                if str(item) in by_id
            ]
            for item in candidates:
                image_id = str(item.get("id") or "")
                if image_id and image_id not in ordered_ids:
                    ordered_ids.append(image_id)
            images = [deepcopy(by_id[item]) for item in ordered_ids[:_DESTINATION_GALLERY_SIZE]]
        if images and errors:
            status = "partial"
        elif images:
            status = "ready"
        elif errors:
            status = "error"
        else:
            status = "empty"
        primary = str(curation.get("cover_id") or "")
        if not any(str(item.get("id") or "") == primary for item in images):
            primary = str(images[0].get("id") or "") if images else ""
        return {
            "stop_id": str(stop.get("id") or ""),
            "day_id": str(day.get("id") or ""),
            "query": query,
            "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
            "status": status,
            "images": images,
            "primary_image_id": primary or None,
            "provider_errors": errors,
            "curation": curation,
            "attempted_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }

    async def async_refresh_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = self._find_stop(days, day_id, stop_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        gallery = await self._destination_gallery_for_stop(
            trip_id,
            day,
            stop,
            existing_gallery=(state.get("destination_galleries") or {}).get(stop_id),
            force_vision=True,
        )
        await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            [gallery],
        )
        return {
            "gallery": gallery,
            "experience": await self.async_panel_payload(trip_id),
        }

    async def async_save_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
        images: list[dict[str, Any]],
        primary_image_id: str | None,
    ) -> dict[str, Any]:
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = self._find_stop(days, day_id, stop_id)
        query = self._destination_query(day, stop)
        selected_images = list(images or [])[:_DESTINATION_GALLERY_SIZE]
        selected_ids = [
            str(item.get("id") or "")
            for item in selected_images
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
        if primary_image_id and primary_image_id in selected_ids:
            selected_ids = [primary_image_id, *[item for item in selected_ids if item != primary_image_id]]
        gallery = {
            "stop_id": stop_id,
            "day_id": day_id,
            "query": query,
            "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
            "status": "ready" if selected_images else "empty",
            "images": selected_images,
            "primary_image_id": primary_image_id,
            "provider_errors": {},
            "curation": {
                "stop_id": stop_id,
                "kind": "planning",
                "status": "ready",
                "mode": "manual",
                "model": None,
                "candidate_ids": selected_ids,
                "cover_id": primary_image_id if primary_image_id in selected_ids else (selected_ids[0] if selected_ids else None),
                "highlight_ids": selected_ids,
                "rejected_ids": [],
                "reasons": {},
                "summary": "Vom Benutzer ausgewählte Planungsbilder.",
                "selected_at": utc_now_iso(),
                "error": None,
            },
            "attempted_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            [gallery],
        )
        stored = (
            await self.hass.async_add_executor_job(self.store.load, trip_id)
        ).get("destination_galleries", {}).get(stop_id)
        return {
            "gallery": stored,
            "experience": await self.async_panel_payload(trip_id),
        }

    async def async_delete_destination_gallery(
        self,
        trip_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        await self.hass.async_add_executor_job(
            self.store.delete_destination_gallery,
            trip_id,
            stop_id,
        )
        return {
            "ok": True,
            "experience": await self.async_panel_payload(trip_id),
        }

    async def async_auto_populate_destination_galleries(
        self,
        trip_id: str,
        *,
        limit: int = _DESTINATION_AUTO_BATCH,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        """Populate missing planning galleries without replacing own travel photos."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        existing = dict(state.get("destination_galleries") or {})
        own_media_stop_ids = {
            str(item.get("linked_stop_id") or "")
            for item in list(state.get("media") or [])
            if isinstance(item, dict) and str(item.get("linked_stop_id") or "")
        }
        today = dt_util.now().date()

        def day_priority(day: dict[str, Any]) -> tuple[int, int, str]:
            value = _day_date(day)
            if value is None:
                return (3, 0, str(day.get("id") or ""))
            if value == today:
                return (0, value.toordinal(), str(day.get("id") or ""))
            if value > today:
                return (1, value.toordinal(), str(day.get("id") or ""))
            return (2, -value.toordinal(), str(day.get("id") or ""))

        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        now = dt_util.now()
        batch_limit = max(1, min(int(limit), 12))
        for day in sorted(days, key=day_priority):
            for stop in _stops(day):
                stop_id = str(stop.get("id") or "")
                if not stop_id or stop_id in own_media_stop_ids:
                    continue
                query = self._destination_query(day, stop)
                fingerprint = self._destination_query_fingerprint(day, stop, query)
                gallery = existing.get(stop_id)
                if isinstance(gallery, dict) and gallery.get("query_fingerprint") == fingerprint:
                    gallery_curation = (
                        gallery.get("curation")
                        if isinstance(gallery.get("curation"), dict)
                        else {}
                    )
                    needs_vision_refresh = bool(
                        gallery.get("images")
                        and self.vision_enabled
                        and not (
                            gallery_curation.get("status") == "ready"
                            and gallery_curation.get("mode") in {"hybrid_vision", "manual"}
                        )
                    )
                    if gallery.get("images") and not needs_vision_refresh:
                        continue
                    attempted = _parse_datetime(gallery.get("attempted_at"))
                    if (
                        not needs_vision_refresh
                        and attempted
                        and (now - attempted).total_seconds() < _DESTINATION_EMPTY_RETRY_SECONDS
                    ):
                        continue
                candidates.append((day, stop))
                if len(candidates) >= batch_limit:
                    break
            if len(candidates) >= batch_limit:
                break
        if not candidates:
            result: dict[str, Any] = {
                "searched": 0,
                "updated": 0,
            }
            if include_experience:
                result["experience"] = await self.async_panel_payload(trip_id)
            return result

        semaphore = asyncio.Semaphore(3)

        async def build(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self._destination_gallery_for_stop(
                        trip_id,
                        day,
                        stop,
                        existing_gallery=existing.get(str(stop.get("id") or "")),
                    )
                except asyncio.CancelledError:
                    raise
                except RoadplannerError as err:
                    query = self._destination_query(day, stop)
                    return {
                        "stop_id": str(stop.get("id") or ""),
                        "day_id": str(day.get("id") or ""),
                        "query": query,
                        "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
                        "status": "error",
                        "images": [],
                        "primary_image_id": None,
                        "provider_errors": {"roadplanner": str(err)[:500]},
                        "attempted_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                    }

        galleries = await asyncio.gather(
            *(build(day, stop) for day, stop in candidates)
        )
        result = await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            list(galleries),
        )
        response: dict[str, Any] = {
            "searched": len(candidates),
            "updated": int(result.get("updated") or 0),
        }
        if include_experience:
            response["experience"] = await self.async_panel_payload(trip_id)
        return response

    async def async_create_decision_from_message(self, *, user_id: str, trip_id: str, message_id: str) -> dict[str, Any]:
        if self.provider is None or not self.provider.configured:
            raise ValidationError("Der Assistent ist nicht konfiguriert")
        request_id = f"decision-{secrets.token_hex(6)}"
        assistant_state = self.assistant.state(user_id, trip_id)
        message = next((item for item in assistant_state.get("messages", []) if str(item.get("id") or "") == message_id and item.get("role") == "assistant"), None)
        if message is None:
            raise ValidationError("Assistentenantwort nicht gefunden")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        compact_days = compact_decision_days(days)
        try:
            result = await self.provider.async_generate_json_result(
                system_instruction=_DECISION_PROMPT,
                messages=[{"role": "user", "content": json.dumps({"assistant_message": message.get("content"), "local_date": dt_util.now().date().isoformat(), "available_days": compact_days}, ensure_ascii=False)}],
                schema=_DECISION_SCHEMA,
                enable_search=False,
                max_output_tokens=4096,
                temperature=0.05,
            )
        except RoadplannerError as err:
            raise ValidationError(f"{err} (Anfrage {request_id})") from err
        raw = result.value
        options_raw = raw.get("options") if isinstance(raw.get("options"), list) else []
        options: list[dict[str, Any]] = []
        linked_day_id = str(raw.get("linked_day_id") or "").strip() or None
        valid_day_ids = {str(day.get("id") or "") for day in days}
        if linked_day_id not in valid_day_ids:
            linked_day_id = None
        for index, option_raw in enumerate(options_raw[:4]):
            if not isinstance(option_raw, dict):
                continue
            options.append(
                {
                    "id": f"option-{index + 1}",
                    "title": _clean(option_raw.get("title"), 300) or f"Option {index + 1}",
                    "summary": _clean(option_raw.get("summary"), 2_000),
                    "place_query": _clean(option_raw.get("place_query"), 500),
                    "stop_type": _clean(option_raw.get("stop_type"), 100) or "waypoint",
                    "pros": [_clean(item, 300) for item in list(option_raw.get("pros") or [])[:4] if _clean(item, 300)],
                    "cons": [_clean(item, 300) for item in list(option_raw.get("cons") or [])[:4] if _clean(item, 300)],
                    "estimated_cost": option_raw.get("estimated_cost") if isinstance(option_raw.get("estimated_cost"), dict) else {},
                    "details": {},
                    "is_current_plan": bool(option_raw.get("is_current_plan", False)),
                    "change_type": _clean(option_raw.get("change_type"), 80) or "choose",
                    "existing_stop_id": _clean(option_raw.get("existing_stop_id"), 200) or None,
                }
            )
        try:
            options, linked_day_id, baseline_required, current_plan_option_id = ensure_current_plan_option(
                assistant_message=str(message.get("content") or ""),
                decision_title=_clean(raw.get("title"), 400),
                question=_clean(raw.get("question"), 1_000),
                linked_day_id=linked_day_id,
                days=days,
                options=options,
            )
        except DecisionBaselineError as err:
            raise ValidationError(f"{err} (Anfrage {request_id})") from err
        if len(options) < 2:
            raise ValidationError("In dieser Antwort konnten nicht mindestens zwei konkrete Optionen erkannt werden")
        experience_payload = await self.async_panel_payload(trip_id)
        media_by_id = {
            str(item.get("id") or ""): item
            for item in experience_payload.get("media", [])
            if isinstance(item, dict)
        }
        for option in options:
            stop_id = str(option.get("existing_stop_id") or "")
            if not stop_id:
                continue
            featured_ids = experience_payload.get("presentation", {}).get("stop_highlights", {}).get(stop_id)
            media_ids = (
                featured_ids
                if isinstance(featured_ids, list) and featured_ids
                else experience_payload.get("by_stop", {}).get(stop_id, [])
            )
            own_media = [
                media_by_id[media_id]
                for media_id in media_ids
                if media_id in media_by_id
            ][:3]
            if own_media:
                option["images"] = [
                    {
                        "id": f"media-{str(item.get('id') or '')}",
                        "media_id": str(item.get("id") or ""),
                        "provider": "onedrive",
                        "alt": item.get("caption") or item.get("name") or option.get("title"),
                        "attribution": "Eigenes Reisefoto",
                    }
                    for item in own_media
                    if item.get("thumbnail_url")
                ]
            else:
                gallery = experience_payload.get("destination_galleries", {}).get(stop_id)
                if isinstance(gallery, dict):
                    gallery_images = list(gallery.get("images") or [])[:3]
                    if gallery_images:
                        option["images"] = deepcopy(gallery_images)
            if option.get("images"):
                option["image"] = deepcopy(option["images"][0])
        # Geocoding, image lookup and route enrichment are independent for each
        # option. Running the options concurrently avoids multiplying provider
        # latency by the number of slides. Every enrichment step is fail-open:
        # a missing image or route must never discard an otherwise usable choice.
        try:
            await asyncio.gather(
                *(self._enrich_option(option, linked_day_id, days) for option in options)
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # defensive decision boundary
            _LOGGER.exception("Unexpected decision enrichment failure (%s)", request_id)
            raise ValidationError(
                f"Die Entscheidungsoptionen konnten nicht sicher vorbereitet werden (Anfrage {request_id})."
            ) from err
        decision = await self.hass.async_add_executor_job(
            self.store.create_decision,
            trip_id,
            {
                "id": new_id("decision"),
                "title": _clean(raw.get("title"), 400) or "Entscheidung",
                "question": _clean(raw.get("question"), 1_000),
                "status": "open",
                "linked_day_id": linked_day_id,
                "source_message_id": message_id,
                "baseline_required": baseline_required,
                "current_plan_option_id": current_plan_option_id,
                "options": options,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        return {"decision": decision, "experience": await self.async_panel_payload(trip_id)}

    async def _enrich_option(self, option: dict[str, Any], linked_day_id: str | None, days: list[dict[str, Any]]) -> None:
        """Enrich one decision option without making the decision depend on it."""
        started = monotonic()
        details = option.setdefault("details", {})
        query = str(option.get("place_query") or "").strip()

        async def resolve_location() -> tuple[Any, list[Any]]:
            if _coordinate(option.get("location")) is not None:
                return None, []
            if not query or not self.geocoder or not self.geocoder.enabled:
                return None, []
            async with asyncio.timeout(_DECISION_GEOCODE_TIMEOUT_SECONDS):
                return await self.geocoder.async_resolve(query, language="de")

        async def resolve_image() -> dict[str, Any] | None:
            existing = option.get("image") if isinstance(option.get("image"), dict) else {}
            existing_images = [
                item for item in list(option.get("images") or [])[:3]
                if isinstance(item, dict) and (item.get("image_url") or item.get("media_id"))
            ]
            if existing.get("image_url") or existing.get("media_id"):
                return {
                    "primary": existing,
                    "images": existing_images or [existing],
                    "provider_errors": {},
                }
            if not query:
                return None
            async with asyncio.timeout(_DECISION_IMAGE_TIMEOUT_SECONDS):
                location = option.get("location") if isinstance(option.get("location"), dict) else {}
                images = await self.image_provider.async_search(
                    query,
                    limit=3,
                    latitude=location.get("latitude"),
                    longitude=location.get("longitude"),
                )
                if images.get("results"):
                    return {
                        "primary": images["results"][0],
                        "images": images["results"][:3],
                        "provider_errors": images.get("provider_errors") or {},
                    }
                return {
                    "primary": None,
                    "images": [],
                    "provider_errors": images.get("provider_errors") or {},
                }

        location_result, image_result = await asyncio.gather(
            resolve_location(),
            resolve_image(),
            return_exceptions=True,
        )

        if isinstance(location_result, BaseException):
            if isinstance(location_result, asyncio.CancelledError):
                raise location_result
            if isinstance(location_result, TimeoutError):
                details["geocoding_error"] = "Ortsauflösung hat das Zeitlimit überschritten"
            elif isinstance(location_result, RoadplannerError):
                details["geocoding_error"] = str(location_result)
            else:
                _LOGGER.warning(
                    "Decision option geocoding failed for %s: %s",
                    option.get("id"),
                    type(location_result).__name__,
                )
                details["geocoding_error"] = "Ortsauflösung ist vorübergehend fehlgeschlagen"
        else:
            best, candidates = location_result
            if best is not None:
                option["location"] = best.as_location()
                details["geocoding"] = best.as_provenance()
            elif candidates:
                details["geocoding_candidates"] = [
                    {
                        "location": candidate.as_location(),
                        "provenance": candidate.as_provenance(),
                    }
                    for candidate in candidates[:3]
                ]

        if isinstance(image_result, BaseException):
            if isinstance(image_result, asyncio.CancelledError):
                raise image_result
            if isinstance(image_result, TimeoutError):
                details["image_error"] = "Bildsuche hat das Zeitlimit überschritten"
            elif isinstance(image_result, RoadplannerError):
                details["image_error"] = str(image_result)
            else:
                _LOGGER.warning(
                    "Decision option image lookup failed for %s: %s",
                    option.get("id"),
                    type(image_result).__name__,
                )
                details["image_error"] = "Bildsuche ist vorübergehend fehlgeschlagen"
        elif image_result is not None:
            option["images"] = list(image_result.get("images") or [])[:3]
            if image_result.get("primary") is not None:
                option["image"] = image_result["primary"]
            if image_result.get("provider_errors"):
                details["image_provider_errors"] = image_result["provider_errors"]

        coord = _coordinate(option.get("location"))
        if coord is not None and linked_day_id and self.router.configured:
            day_index = next(
                (
                    index
                    for index, day in enumerate(days)
                    if str(day.get("id") or "") == linked_day_id
                ),
                None,
            )
            if day_index is not None:
                origin = self._day_origin(days, day_index)
                onward = self._day_onward(days, day_index)
                if origin is not None:
                    try:
                        points = [
                            {"latitude": origin[0], "longitude": origin[1]},
                            {"latitude": coord[0], "longitude": coord[1]},
                        ]
                        if onward is not None:
                            points.append(
                                {"latitude": onward[0], "longitude": onward[1]}
                            )
                        async with asyncio.timeout(_DECISION_ROUTE_TIMEOUT_SECONDS):
                            result = await self.router.async_calculate(
                                points,
                                input_hash=route_input_hash(
                                    points, self.router.profile
                                ),
                            )
                        option["route_metrics"] = {
                            "distance_km": round(
                                float(result.get("distance_m") or 0) / 1000, 1
                            ),
                            "drive_minutes": round(
                                float(result.get("duration_s") or 0) / 60
                            ),
                            "point_count": len(points),
                        }
                    except TimeoutError:
                        details["routing_error"] = (
                            "Routenberechnung hat das Zeitlimit überschritten"
                        )
                    except RoadplannerError as err:
                        details["routing_error"] = str(err)
                    except Exception as err:  # defensive enrichment boundary
                        _LOGGER.warning(
                            "Decision option routing failed for %s: %s",
                            option.get("id"),
                            type(err).__name__,
                        )
                        details["routing_error"] = (
                            "Routenberechnung ist vorübergehend fehlgeschlagen"
                        )

        details["enrichment_duration_ms"] = int((monotonic() - started) * 1000)

    @staticmethod
    def _day_origin(days: list[dict[str, Any]], index: int) -> tuple[float, float] | None:
        day = days[index]
        for stop in _stops(day):
            coord = _coordinate(stop.get("location"))
            if coord is not None:
                return coord
        if index > 0:
            for stop in reversed(_stops(days[index - 1])):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    return coord
        return None

    @staticmethod
    def _day_onward(days: list[dict[str, Any]], index: int) -> tuple[float, float] | None:
        day = days[index]
        for stop in reversed(_stops(day)):
            coord = _coordinate(stop.get("location"))
            if coord is not None:
                return coord
        if index + 1 < len(days):
            for stop in _stops(days[index + 1]):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    return coord
        return None

    async def async_select_decision(self, trip_id: str, decision_id: str, option_id: str) -> dict[str, Any]:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        current = next((item for item in state["decisions"] if item.get("id") == decision_id), None)
        if current is None:
            raise ValidationError("Entscheidung nicht gefunden")
        if not any(item.get("id") == option_id for item in current.get("options", [])):
            raise ValidationError("Entscheidungsoption nicht gefunden")
        decision = await self.hass.async_add_executor_job(
            self.store.update_decision,
            trip_id,
            decision_id,
            {"selected_option_id": option_id, "status": "selected"},
        )
        return {"decision": decision, "experience": await self.async_panel_payload(trip_id)}

    async def async_transfer_decision(self, *, user_id: str, trip_id: str, decision_id: str) -> dict[str, Any]:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        decision = next((item for item in state["decisions"] if item.get("id") == decision_id), None)
        if decision is None:
            raise ValidationError("Entscheidung nicht gefunden")
        selected_id = decision.get("selected_option_id")
        option = next((item for item in decision.get("options", []) if item.get("id") == selected_id), None)
        if option is None:
            raise ValidationError("Bitte zuerst eine Option auswählen")
        if bool(option.get("is_current_plan")) or str(option.get("change_type") or "") == "keep_existing":
            updated = await self.hass.async_add_executor_job(
                self.store.update_decision,
                trip_id,
                decision_id,
                {"status": "selected"},
            )
            return {
                "decision": updated,
                "kept_existing": True,
                "assistant": self.assistant.state(user_id, trip_id),
                "experience": await self.async_panel_payload(trip_id),
            }
        result = await self.assistant.async_add_decision_draft(user_id=user_id, trip_id=trip_id, decision=decision, option=option)
        draft = result.get("draft") or {}
        updated = await self.hass.async_add_executor_job(self.store.update_decision, trip_id, decision_id, {"status": "transferred", "transferred_draft_id": draft.get("id")})
        return {"decision": updated, "assistant": result.get("assistant"), "experience": await self.async_panel_payload(trip_id)}

    async def async_archive_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        decision = await self.hass.async_add_executor_job(self.store.update_decision, trip_id, decision_id, {"status": "archived"})
        return {"decision": decision, "experience": await self.async_panel_payload(trip_id)}

    async def async_delete_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        await self.hass.async_add_executor_job(self.store.delete_decision, trip_id, decision_id)
        return {"ok": True, "experience": await self.async_panel_payload(trip_id)}
