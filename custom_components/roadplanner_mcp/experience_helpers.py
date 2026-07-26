"""Pure day/stop/geo/folder-name parsing helpers shared across the
experience manager's collaborators (media sync, decisions, destination
galleries).

Nothing here holds state or talks to Home Assistant/providers - these are
leaf-level primitives.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math
import re
from typing import Any

from homeassistant.util import dt as dt_util

from .canonical_day import canonical_roadbook_stops
from .experience_store import utc_now_iso
from .onedrive_media import normalize_onedrive_folder_path
from .roadplanner import ValidationError

_IMAGE_MIME_PREFIX = "image/"

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
