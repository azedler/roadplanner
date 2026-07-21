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

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .assistant_provider import AssistantProvider
from .const import EVENT_ROADPLANNER_UPDATED
from .destination_images import DestinationImageProvider
from .experience_store import ExperienceStore, new_id, utc_now_iso
from .geocoding import NominatimGeocoder
from .manager import RoadplannerManager
from .onedrive_media import OneDrivePersonalClient, normalize_onedrive_folder_path
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
            "maxItems": 3,
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
                },
                "required": ["title", "summary", "place_query", "stop_type", "pros", "cons"],
            },
        },
    },
    "required": ["title", "question", "options"],
}

_DECISION_PROMPT = """Du erstellst eine lokale Roadplanner-Entscheidungsvorlage aus genau einer bereits sichtbaren Assistentenantwort.
Extrahiere ausschließlich die zwei oder drei konkreten Optionen, die in der Antwort wirklich genannt wurden. Erfinde keine zusätzliche Option und keine ungeklärten Preise.
Jede Option benötigt einen kurzen Titel, eine knappe Zusammenfassung, einen geocodierbaren Orts-/Anbieternamen in place_query, einen Roadplanner-Stopp-Typ sowie höchstens vier Vor- und Nachteile.
Wenn die Antwort einen Reisetag eindeutig nennt, verwende ausschließlich eine vorhandene day_id aus dem mitgelieferten Roadbook. Andernfalls linked_day_id=null.
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
    value = day.get("stops")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
        self._unsub_interval: Any = None
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

    async def async_shutdown(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

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
        if any(
            int(item.get("added") or 0)
            or int(item.get("updated") or 0)
            or int(item.get("removed") or 0)
            for item in result.get("trips", [])
            if isinstance(item, dict)
        ):
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

    async def async_panel_payload(self, trip_id: str) -> dict[str, Any]:
        if not trip_id:
            return {"decisions": [], "media": [], "stats": {}, "by_day": {}, "by_stop": {}, "onedrive": self.onedrive.status()}
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
        decisions = deepcopy(state["decisions"])
        return {
            "decisions": decisions,
            "media": media,
            "by_day": by_day,
            "by_stop": by_stop,
            "stats": {
                "decision_count": len(decisions),
                "open_decision_count": sum(1 for item in decisions if item.get("status") in {"draft", "open"}),
                "media_count": len(media),
                "automatic_count": sum(1 for item in media if item.get("assignment_status") == "automatic"),
                "suggested_count": sum(1 for item in media if item.get("assignment_status") == "suggested"),
                "unassigned_count": sum(1 for item in media if not item.get("linked_day_id")),
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
        compact_days = [{"id": day.get("id"), "date": day.get("date"), "title": day.get("title"), "start": day.get("start"), "end": day.get("end")} for day in days]
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
        for index, option_raw in enumerate(options_raw[:3]):
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
                }
            )
        if len(options) < 2:
            raise ValidationError("In dieser Antwort konnten nicht mindestens zwei konkrete Optionen erkannt werden")
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
            {"id": new_id("decision"), "title": _clean(raw.get("title"), 400) or "Entscheidung", "question": _clean(raw.get("question"), 1_000), "status": "open", "linked_day_id": linked_day_id, "source_message_id": message_id, "options": options, "created_at": utc_now_iso(), "updated_at": utc_now_iso()},
        )
        return {"decision": decision, "experience": await self.async_panel_payload(trip_id)}

    async def _enrich_option(self, option: dict[str, Any], linked_day_id: str | None, days: list[dict[str, Any]]) -> None:
        """Enrich one decision option without making the decision depend on it."""
        started = monotonic()
        details = option.setdefault("details", {})
        query = str(option.get("place_query") or "").strip()

        async def resolve_location() -> tuple[Any, list[Any]]:
            if not query or not self.geocoder or not self.geocoder.enabled:
                return None, []
            async with asyncio.timeout(_DECISION_GEOCODE_TIMEOUT_SECONDS):
                return await self.geocoder.async_resolve(query, language="de")

        async def resolve_image() -> dict[str, Any] | None:
            if not query:
                return None
            async with asyncio.timeout(_DECISION_IMAGE_TIMEOUT_SECONDS):
                images = await self.image_provider.async_search(query, limit=1)
                if images.get("results"):
                    return images["results"][0]
                return None

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
            option["image"] = image_result

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
