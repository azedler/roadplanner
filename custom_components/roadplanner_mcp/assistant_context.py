"""Question-aware context builder for the Roadplanner assistant.

The Roadbook remains the source of truth. This module only creates a bounded,
request-specific view for the language model. It always includes a compact
index and an ID catalogue, while full day/stop details are limited to the days
that are likely relevant for the current question or change basket.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
import json
import re
from typing import Any

from homeassistant.util import dt as dt_util

MAX_CONTEXT_CHARACTERS = 120_000
MAX_INDEX_DAYS = 180
MAX_DETAILED_DAYS = 30

OVERNIGHT_STOP_TYPES = {
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
}

_GLOBAL_QUERY_MARKERS = (
    "gesamte reise",
    "ganze reise",
    "alle tage",
    "rest der reise",
    "zweite hälfte",
    "erste hälfte",
    "komplette route",
    "gesamtstrecke",
    "alle stopps",
    "alle stellplätze",
    "alle campingplätze",
)

_FIVE_DAY_MARKERS = (
    "nächsten fünf",
    "kommenden fünf",
    "5 tage",
    "fünf tage",
)

_DATE_PATTERN = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_GERMAN_DATE_PATTERN = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")


def _clean_text(value: Any, *, maximum: int = 4_000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _coordinate(location: dict[str, Any], *names: str) -> float:
    for name in names:
        value = location.get(name)
        if value is not None and value != "":
            return float(value)
    raise ValueError("coordinate missing")


def same_place(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Return whether two stop-like objects refer to the same place."""
    if first.get("id") and first.get("id") == second.get("id"):
        return True
    first_name = _clean_text(first.get("name"), maximum=500).casefold()
    second_name = _clean_text(second.get("name"), maximum=500).casefold()
    if first_name and first_name == second_name:
        return True
    first_location = first.get("location") if isinstance(first.get("location"), dict) else {}
    second_location = second.get("location") if isinstance(second.get("location"), dict) else {}
    try:
        first_lat = _coordinate(first_location, "latitude", "lat")
        first_lon = _coordinate(first_location, "longitude", "lon", "lng")
        second_lat = _coordinate(second_location, "latitude", "lat")
        second_lon = _coordinate(second_location, "longitude", "lon", "lng")
    except (TypeError, ValueError):
        return False
    return abs(first_lat - second_lat) < 0.00005 and abs(first_lon - second_lon) < 0.00005


def with_overnight_continuity(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate the logical start inherited from the prior overnight stop."""
    result = deepcopy(days)
    previous: dict[str, Any] | None = None
    for day in result:
        stops = day.get("stops") if isinstance(day.get("stops"), list) else []
        inherited = None
        if previous is not None:
            previous_stops = previous.get("stops") if isinstance(previous.get("stops"), list) else []
            if previous_stops:
                last_stop = previous_stops[-1]
                if str(last_stop.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES:
                    first_stop = stops[0] if stops else None
                    if not isinstance(first_stop, dict) or not same_place(last_stop, first_stop):
                        inherited = {
                            "source_day_id": previous.get("id"),
                            "source_stop_id": last_stop.get("id"),
                            "name": last_stop.get("name"),
                            "type": last_stop.get("type"),
                            "location": deepcopy(last_stop.get("location") or {}),
                            "departure_time": None,
                            "read_only": True,
                        }
        day["inherited_start_stop"] = inherited
        previous = day
    return result


def _parse_day_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _extract_requested_dates(text: str) -> set[date]:
    result: set[date] = set()
    for raw in _DATE_PATTERN.findall(text):
        try:
            result.add(date.fromisoformat(raw))
        except ValueError:
            pass
    for day_value, month_value, year_value in _GERMAN_DATE_PATTERN.findall(text):
        try:
            result.add(date(int(year_value), int(month_value), int(day_value)))
        except ValueError:
            pass
    return result


def _trim_details(value: Any, *, entity: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if entity == "trip":
        allowed = {
            "planning_preferences",
            "bookings",
            "costs",
            "budget",
            "media",
            "documents",
        }
    elif entity == "day":
        allowed = {
            "planning_preferences",
            "bookings",
            "costs",
            "budget",
            "media",
            "weather",
        }
    else:
        allowed = {
            "booking",
            "bookings",
            "opening_hours",
            "dog",
            "geocoding",
            "costs",
            "media",
            "service",
        }
    return {key: deepcopy(child) for key, child in value.items() if key in allowed}


def _trim_day(day: dict[str, Any], *, aggressive: bool = False) -> dict[str, Any]:
    value = deepcopy(day)
    value["notes"] = str(value.get("notes") or "")[: (1_000 if aggressive else 3_000)]
    value["details"] = _trim_details(value.get("details"), entity="day")
    stops: list[dict[str, Any]] = []
    for raw_stop in value.get("stops", []):
        if not isinstance(raw_stop, dict):
            continue
        stop = deepcopy(raw_stop)
        stop["notes"] = str(stop.get("notes") or "")[: (700 if aggressive else 2_000)]
        stop["details"] = _trim_details(stop.get("details"), entity="stop")
        stops.append(stop)
    value["stops"] = stops
    return value


def _day_index(day: dict[str, Any]) -> dict[str, Any]:
    stops = [stop for stop in day.get("stops", []) if isinstance(stop, dict)]
    overnight = None
    if stops and str(stops[-1].get("type") or "").casefold() in OVERNIGHT_STOP_TYPES:
        overnight = {
            "id": stops[-1].get("id"),
            "name": stops[-1].get("name"),
            "type": stops[-1].get("type"),
        }
    return {
        "id": day.get("id"),
        "date": day.get("date"),
        "title": day.get("title"),
        "start": day.get("start"),
        "end": day.get("end"),
        "status": day.get("status"),
        "stop_count": len(stops),
        "overnight_stop": overnight,
    }


def _id_catalog(days: list[dict[str, Any]], trip: dict[str, Any]) -> dict[str, Any]:
    stop_ids: dict[str, list[str]] = {}
    day_ids: list[str] = []
    preference_ids: list[str] = []
    details = trip.get("details") if isinstance(trip.get("details"), dict) else {}
    preferences = details.get("planning_preferences") if isinstance(details, dict) else None
    if isinstance(preferences, list):
        preference_ids.extend(
            str(item.get("id"))
            for item in preferences
            if isinstance(item, dict) and item.get("id")
        )
    for day in days:
        day_id = str(day.get("id") or "")
        if not day_id:
            continue
        day_ids.append(day_id)
        stop_ids[day_id] = [
            str(stop.get("id"))
            for stop in day.get("stops", [])
            if isinstance(stop, dict) and stop.get("id")
        ]
        day_details = day.get("details") if isinstance(day.get("details"), dict) else {}
        day_preferences = day_details.get("planning_preferences") if isinstance(day_details, dict) else None
        if isinstance(day_preferences, list):
            preference_ids.extend(
                str(item.get("id"))
                for item in day_preferences
                if isinstance(item, dict) and item.get("id")
            )
    return {
        "day_ids": day_ids,
        "stop_ids_by_day": stop_ids,
        "preference_ids": sorted(set(preference_ids)),
    }


@dataclass(slots=True)
class AssistantContextResult:
    """One bounded context plus non-sensitive diagnostics."""

    context: dict[str, Any]
    metadata: dict[str, Any]


class AssistantContextBuilder:
    """Build compact, question-aware Roadplanner contexts."""

    def __init__(self, *, max_characters: int = MAX_CONTEXT_CHARACTERS) -> None:
        self.max_characters = max(40_000, min(int(max_characters), 240_000))

    @staticmethod
    def _current_index(days: list[dict[str, Any]], today: date) -> int | None:
        dated: list[tuple[int, date]] = []
        for index, day in enumerate(days):
            parsed = _parse_day_date(day.get("date"))
            if parsed is None:
                continue
            if parsed == today:
                return index
            dated.append((index, parsed))
        for index, parsed in dated:
            if parsed > today:
                return index
        if dated:
            return dated[-1][0]
        return 0 if days else None

    @staticmethod
    def _indices_for_dates(days: list[dict[str, Any]], requested: set[date]) -> set[int]:
        result: set[int] = set()
        if not requested:
            return result
        for index, day in enumerate(days):
            parsed = _parse_day_date(day.get("date"))
            if parsed in requested:
                result.add(index)
        return result

    @staticmethod
    def _indices_for_basket(days: list[dict[str, Any]], basket: list[dict[str, Any]]) -> set[int]:
        result: set[int] = set()
        for index, day in enumerate(days):
            day_id = str(day.get("id") or "")
            day_date = str(day.get("date") or "")
            stop_ids = {
                str(stop.get("id"))
                for stop in day.get("stops", [])
                if isinstance(stop, dict) and stop.get("id")
            }
            for item in basket:
                if not isinstance(item, dict):
                    continue
                if str(item.get("day_id") or "") == day_id:
                    result.add(index)
                if str(item.get("day_date") or "") == day_date:
                    result.add(index)
                if str(item.get("target_id") or "") in stop_ids:
                    result.add(index)
        return result

    @staticmethod
    def _indices_for_text_mentions(
        days: list[dict[str, Any]],
        text: str,
    ) -> set[int]:
        """Return days whose route labels are explicitly mentioned by the user."""
        normalized = " ".join(str(text or "").casefold().split())
        if len(normalized) < 3:
            return set()
        result: set[int] = set()
        for index, day in enumerate(days):
            candidates = [
                day.get("title"),
                day.get("start"),
                day.get("end"),
            ]
            candidates.extend(
                stop.get("name")
                for stop in day.get("stops", [])
                if isinstance(stop, dict)
            )
            for candidate in candidates:
                label = " ".join(str(candidate or "").casefold().split())
                # Very short names create too many accidental matches. Longer
                # place names are reliable enough for request-scoped context.
                if len(label) >= 4 and label in normalized:
                    result.add(index)
                    break
        return result

    def build(
        self,
        payload: dict[str, Any],
        *,
        user_text: str = "",
        basket: list[dict[str, Any]] | None = None,
        purpose: str = "chat",
    ) -> AssistantContextResult:
        now = dt_util.now()
        today = now.date()
        text = " ".join(str(user_text or "").casefold().split())
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = deepcopy(summary.get("trip") or {})
        trip["details"] = _trim_details(trip.get("details"), entity="trip")
        raw_days = payload.get("days", {}).get("days", [])
        days = with_overnight_continuity(
            [deepcopy(day) for day in raw_days if isinstance(day, dict)]
        )
        for day in days:
            day["is_today"] = day.get("date") == today.isoformat()

        current_index = self._current_index(days, today)
        relevant: set[int] = set()
        strategy = "current_window"

        if purpose == "compile":
            relevant.update(self._indices_for_basket(days, list(basket or [])))
            strategy = "basket_targets"
        elif purpose == "briefing":
            strategy = "today_and_tomorrow"
        else:
            requested_dates = _extract_requested_dates(text)
            if "heute" in text:
                requested_dates.add(today)
            if "morgen" in text:
                requested_dates.add(today + timedelta(days=1))
            if "übermorgen" in text:
                requested_dates.add(today + timedelta(days=2))
            if "gestern" in text:
                requested_dates.add(today - timedelta(days=1))
            relevant.update(self._indices_for_dates(days, requested_dates))
            mentioned = self._indices_for_text_mentions(days, text)
            relevant.update(mentioned)
            if requested_dates:
                strategy = "explicit_dates"
            elif mentioned:
                strategy = "mentioned_places"
            if any(marker in text for marker in _GLOBAL_QUERY_MARKERS):
                relevant.update(range(min(len(days), MAX_DETAILED_DAYS)))
                strategy = "full_trip_request"
            elif any(marker in text for marker in _FIVE_DAY_MARKERS) and current_index is not None:
                relevant.update(range(current_index, min(len(days), current_index + 5)))
                strategy = "five_day_window"

        if current_index is not None:
            if purpose == "briefing":
                relevant.update(range(current_index, min(len(days), current_index + 2)))
            elif purpose == "compile" and not relevant:
                relevant.update(range(max(0, current_index - 1), min(len(days), current_index + 3)))
            elif not relevant:
                relevant.update(range(max(0, current_index - 1), min(len(days), current_index + 2)))

        # Include one neighbour around each explicitly targeted day for continuity.
        expanded = set(relevant)
        for index in list(relevant):
            if index > 0:
                expanded.add(index - 1)
            if index + 1 < len(days):
                expanded.add(index + 1)
        relevant = set(sorted(expanded)[:MAX_DETAILED_DAYS])

        detailed_days = [_trim_day(days[index]) for index in sorted(relevant)]
        index_days = days[:MAX_INDEX_DAYS]
        # Chat and briefings need only the IDs of the detailed request window.
        # Review compilation receives the full catalogue so the model can map
        # draft intents to existing canonical objects without inventing IDs.
        catalog_days = days if purpose == "compile" else detailed_days
        id_catalog_scope = "full_trip" if purpose == "compile" else "detailed_days"
        context: dict[str, Any] = {
            "context_version": 2,
            "generated_for_assistant_at": now.isoformat(),
            "local_date": today.isoformat(),
            "local_time": now.strftime("%H:%M"),
            "timezone": str(now.tzinfo or ""),
            "selected_trip_id": payload.get("selected_trip_id"),
            "active_trip_id": payload.get("active_trip_id"),
            "selected_is_active": bool(payload.get("selected_is_active")),
            "revision": summary.get("revision"),
            "trip": trip,
            "trip_index": [_day_index(day) for day in index_days],
            "days": detailed_days,
            "id_catalog": _id_catalog(catalog_days, trip),
            "day_count": summary.get("day_count", len(days)),
            "stop_count": summary.get("stop_count", 0),
            "travel_archive": deepcopy(payload.get("travel_archive") or {}),
            "days_truncated": bool(payload.get("days", {}).get("has_more")) or len(days) > MAX_INDEX_DAYS,
            "scope": {
                "purpose": purpose,
                "strategy": strategy,
                "current_day_id": days[current_index].get("id") if current_index is not None else None,
                "detailed_day_ids": [day.get("id") for day in detailed_days],
                "indexed_day_count": len(index_days),
                "id_catalog_scope": id_catalog_scope,
            },
        }

        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > self.max_characters and context.get("travel_archive"):
            archive = context["travel_archive"]
            if isinstance(archive, dict):
                context["travel_archive"] = {
                    "summary": archive.get("summary", {}),
                    "today": archive.get("today", {}),
                    "documents": list(archive.get("documents", []))[:20],
                    "expenses": list(archive.get("expenses", []))[:30],
                    "todos": list(archive.get("todos", []))[:30],
                    "truncated": True,
                }
            encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > self.max_characters:
            context["days"] = [_trim_day(day, aggressive=True) for day in detailed_days[:12]]
            context["scope"]["context_trimmed"] = True
            encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > self.max_characters:
            context["days"] = context["days"][:6]
            context["trip_index"] = context["trip_index"][:90]
            context["scope"]["context_truncated"] = True
            encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

        metadata = {
            "purpose": purpose,
            "strategy": strategy,
            "characters": len(encoded),
            "detailed_day_count": len(context.get("days", [])),
            "indexed_day_count": len(context.get("trip_index", [])),
            "current_day_id": context.get("scope", {}).get("current_day_id"),
            "id_catalog_scope": context.get("scope", {}).get("id_catalog_scope"),
            "context_trimmed": bool(context.get("scope", {}).get("context_trimmed")),
            "context_truncated": bool(context.get("scope", {}).get("context_truncated")),
        }
        return AssistantContextResult(context=context, metadata=metadata)
