"""Local, deterministic media curation for Roadplanner 3.0.

The module deliberately avoids downloading image bytes.  It ranks and
suppresses obvious duplicates using OneDrive metadata that is already available
in the private experience sidecar.  Optional vision-based curation can be added
later without changing the panel contract.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import PurePath
import re
from typing import Any, Iterable

_SCREENSHOT_MARKERS = (
    "screenshot",
    "bildschirmfoto",
    "screen shot",
    "screen-shot",
)
_BURST_SECONDS = 4
_BURST_DISTANCE_M = 30.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_datetime(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coordinate(item: dict[str, Any]) -> tuple[float, float] | None:
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _distance_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    latitude_1, longitude_1 = first
    latitude_2, longitude_2 = second
    radius = 6_371_000.0
    phi_1 = math.radians(latitude_1)
    phi_2 = math.radians(latitude_2)
    delta_phi = math.radians(latitude_2 - latitude_1)
    delta_lambda = math.radians(longitude_2 - longitude_1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_1) * math.cos(phi_2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def _normalized_name(value: Any) -> str:
    name = PurePath(_text(value)).stem.casefold()
    name = re.sub(r"(?:copy|kopie|duplicate|duplikat|\(\d+\)|_\d+)$", "", name).strip(" _-()")
    return name


def duplicate_key(item: dict[str, Any]) -> str:
    """Return a conservative exact/near-exact duplicate key."""
    file_hash = _text(item.get("file_hash"))
    if file_hash:
        return f"hash:{file_hash.casefold()}"
    provider_id = _text(item.get("provider_item_id"))
    if provider_id:
        # Provider IDs are unique, but a later fallback may still collapse files
        # copied under a different item ID when their metadata is identical.
        timestamp = _parse_datetime(item.get("taken_at") or item.get("created_at"))
        size = int(item.get("size_bytes") or 0)
        name = _normalized_name(item.get("name"))
        coordinate = _coordinate(item)
        if timestamp and size and name:
            rounded = timestamp.replace(microsecond=0).isoformat()
            coordinate_key = (
                f":{coordinate[0]:.5f}:{coordinate[1]:.5f}"
                if coordinate
                else ""
            )
            return f"meta:{name}:{size}:{rounded}{coordinate_key}"
        return f"provider:{provider_id}"
    return f"id:{_text(item.get('id'))}"


def media_quality_score(item: dict[str, Any]) -> float:
    """Score one media reference using only deterministic local metadata."""
    score = 0.0
    if item.get("is_cover"):
        score += 100.0
    assignment = _text(item.get("assignment_status")).casefold()
    score += {
        "manual": 24.0,
        "automatic": 14.0,
        "suggested": 7.0,
        "unassigned": 0.0,
    }.get(assignment, 0.0)
    confidence = item.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        score += max(0.0, min(float(confidence), 1.0)) * 20.0
    distance = item.get("distance_m")
    if isinstance(distance, (int, float)) and not isinstance(distance, bool):
        distance = max(0.0, float(distance))
        score += max(0.0, 10.0 - min(distance, 2_000.0) / 200.0)
    size = item.get("size_bytes")
    if isinstance(size, int) and not isinstance(size, bool) and size > 0:
        score += min(10.0, math.log10(max(size, 1)) * 1.5)
    width = item.get("width")
    height = item.get("height")
    if (
        isinstance(width, int)
        and not isinstance(width, bool)
        and isinstance(height, int)
        and not isinstance(height, bool)
        and width > 0
        and height > 0
    ):
        megapixels = (width * height) / 1_000_000.0
        score += min(12.0, math.log2(max(megapixels, 0.25) + 1.0) * 4.0)
        # Landscape and near-square images are generally more useful for day
        # headers and stop cards than very narrow panoramas or extreme crops.
        ratio = width / height
        if 0.8 <= ratio <= 2.2:
            score += 2.0
        elif ratio >= 4.0 or ratio <= 0.25:
            score -= 3.0
    if item.get("thumbnail_available"):
        score += 2.0
    if _parse_datetime(item.get("taken_at")):
        score += 2.0
    if _coordinate(item):
        score += 2.0
    name = _text(item.get("name")).casefold()
    if any(marker in name for marker in _SCREENSHOT_MARKERS):
        score -= 30.0
    if _text(item.get("media_type")).casefold() != "photo":
        score -= 4.0
    return round(score, 3)


def _same_burst(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_time = _parse_datetime(first.get("taken_at") or first.get("created_at"))
    second_time = _parse_datetime(second.get("taken_at") or second.get("created_at"))
    if first_time is None or second_time is None:
        return False
    if abs((first_time - second_time).total_seconds()) > _BURST_SECONDS:
        return False
    first_coordinate = _coordinate(first)
    second_coordinate = _coordinate(second)
    if first_coordinate and second_coordinate:
        return _distance_m(first_coordinate, second_coordinate) <= _BURST_DISTANCE_M
    return True


def select_media_highlights(
    media: Iterable[dict[str, Any]],
    *,
    limit: int = 3,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return deterministic highlights plus curation statistics.

    Exact duplicate keys collapse to the highest-scoring item.  Near-concurrent
    burst photos are grouped and only the strongest item from a burst is placed
    in the highlight strip.  The complete album remains available separately.
    """
    values = [deepcopy(item) for item in media if isinstance(item, dict)]
    best_by_duplicate: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for item in values:
        item["selection_score"] = media_quality_score(item)
        key = duplicate_key(item)
        current = best_by_duplicate.get(key)
        if current is None or item["selection_score"] > current["selection_score"]:
            if current is not None:
                duplicate_count += 1
            best_by_duplicate[key] = item
        else:
            duplicate_count += 1

    ranked = sorted(
        best_by_duplicate.values(),
        key=lambda item: (
            -float(item.get("selection_score") or 0.0),
            _text(item.get("taken_at") or item.get("created_at")),
            _text(item.get("id")),
        ),
    )
    burst_representatives: list[dict[str, Any]] = []
    burst_suppressed = 0
    for candidate in ranked:
        if any(_same_burst(candidate, existing) for existing in burst_representatives):
            burst_suppressed += 1
            continue
        burst_representatives.append(candidate)

    selected = burst_representatives[: max(1, min(int(limit), 12))]
    for position, item in enumerate(selected, start=1):
        item["highlight_position"] = position
        item["selection_reason"] = (
            "manuell gewählt"
            if item.get("is_cover") or item.get("assignment_status") == "manual"
            else "lokal vorausgewählt"
        )

    return selected, {
        "source_count": len(values),
        "unique_count": len(best_by_duplicate),
        "duplicate_count": duplicate_count,
        "burst_suppressed_count": burst_suppressed,
        "highlight_count": len(selected),
    }


def build_featured_media_indexes(
    media: Iterable[dict[str, Any]],
    *,
    limit: int = 3,
) -> dict[str, Any]:
    """Build stable featured-media IDs for day and stop albums."""
    by_stop: dict[str, list[dict[str, Any]]] = {}
    by_day: dict[str, list[dict[str, Any]]] = {}
    all_media = [item for item in media if isinstance(item, dict)]
    for item in all_media:
        stop_id = _text(item.get("linked_stop_id"))
        day_id = _text(item.get("linked_day_id"))
        if stop_id:
            by_stop.setdefault(stop_id, []).append(item)
        if day_id:
            by_day.setdefault(day_id, []).append(item)

    featured_by_stop: dict[str, list[str]] = {}
    featured_by_day: dict[str, list[str]] = {}
    stats = {
        "duplicate_count": 0,
        "burst_suppressed_count": 0,
        "featured_stop_count": 0,
        "featured_day_count": 0,
    }
    for stop_id, items in by_stop.items():
        selected, group_stats = select_media_highlights(items, limit=limit)
        featured_by_stop[stop_id] = [_text(item.get("id")) for item in selected if _text(item.get("id"))]
        stats["duplicate_count"] += group_stats["duplicate_count"]
        stats["burst_suppressed_count"] += group_stats["burst_suppressed_count"]
        if selected:
            stats["featured_stop_count"] += 1
    for day_id, items in by_day.items():
        selected, _group_stats = select_media_highlights(items, limit=limit)
        featured_by_day[day_id] = [_text(item.get("id")) for item in selected if _text(item.get("id"))]
        if selected:
            stats["featured_day_count"] += 1

    return {
        "featured_by_stop": featured_by_stop,
        "featured_by_day": featured_by_day,
        "stats": stats,
    }


def build_media_presentation(media: Iterable[dict[str, Any]], *, limit: int = 3) -> dict[str, Any]:
    """Return the stable panel contract for personal travel-photo highlights."""
    indexes = build_featured_media_indexes(media, limit=limit)
    stop_highlights = indexes["featured_by_stop"]
    day_highlights = indexes["featured_by_day"]
    return {
        "version": 1,
        "stop_highlights": stop_highlights,
        "day_highlights": day_highlights,
        "stop_covers": {
            key: values[0]
            for key, values in stop_highlights.items()
            if values
        },
        "day_covers": {
            key: values[0]
            for key, values in day_highlights.items()
            if values
        },
        "planning_day_covers": {},
        "curation": indexes["stats"],
        "selection_mode": "local_metadata",
    }
