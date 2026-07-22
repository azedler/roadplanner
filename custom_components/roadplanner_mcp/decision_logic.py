"""Pure decision-template helpers shared by backend logic and tests.

The helpers in this module deliberately avoid Home Assistant imports.  They
provide deterministic safeguards around the current Roadbook plan so a
"keep or replace" decision never hides the option that is already planned.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from .canonical_day import canonical_roadbook_stops

_MAX_ALTERNATIVES_WITH_BASELINE = 3
_OVERNIGHT_TYPES = frozenset(
    {
        "overnight",
        "campsite",
        "camping",
        "stellplatz",
        "wildcamp",
        "accommodation",
    }
)
_CURRENT_PLAN_HINT_RE = re.compile(
    r"(?:\b(?:beibehalten|behalten|bleiben|wechseln|stattdessen)\b"
    r"|\b(?:aktuell(?:e[rsn]?|er)?|bereits)\s+geplant\b"
    r"|\bgeplant(?:e[rsn]?|er)?\b.{0,100}\b(?:bleiben|behalten)\b"
    r"|\b(?:eine\s+)?alternative(?:n)?\s+(?:waehlen|wählen|nehmen)\b"
    r"|\boder\s+(?:eine\s+)?alternative\b)",
    re.IGNORECASE | re.DOTALL,
)


class DecisionBaselineError(ValueError):
    """Raised when a required current-plan option cannot be resolved safely."""


def _clean(value: Any, maximum: int = 2_000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _match_text(value: Any) -> str:
    return re.sub(r"[^\w]+", " ", str(value or "").casefold()).strip()


def _stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    return canonical_roadbook_stops(day)


def _location_query(stop: dict[str, Any]) -> str:
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    parts = [
        _clean(stop.get("name"), 500),
        _clean(location.get("label"), 500),
        _clean(location.get("address"), 500),
        _clean(location.get("city"), 300),
        _clean(location.get("country_code"), 20),
    ]
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        folded = part.casefold()
        if part and folded not in seen:
            seen.add(folded)
            unique.append(part)
    return ", ".join(unique)[:1_000]


def _stop_image(stop: dict[str, Any]) -> dict[str, Any]:
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    media = details.get("media") if isinstance(details.get("media"), dict) else details
    result: dict[str, Any] = {}
    for key in ("image_url", "source_url", "alt", "attribution", "provider"):
        value = _clean(media.get(key), 2_000)
        if value:
            result[key] = value
    return result


def compact_decision_days(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return bounded day/stop context for decision extraction."""
    compact: list[dict[str, Any]] = []
    for day in days[:180]:
        if not isinstance(day, dict):
            continue
        stops: list[dict[str, Any]] = []
        for stop in _stops(day)[:40]:
            location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
            stops.append(
                {
                    "id": _clean(stop.get("id"), 200),
                    "name": _clean(stop.get("name"), 500),
                    "type": _clean(stop.get("type"), 100),
                    "position": stop.get("position"),
                    "arrival_time": _clean(stop.get("arrival_time"), 50),
                    "departure_time": _clean(stop.get("departure_time"), 50),
                    "location": {
                        key: location.get(key)
                        for key in (
                            "label",
                            "address",
                            "city",
                            "country_code",
                            "latitude",
                            "longitude",
                            "lat",
                            "lon",
                            "lng",
                        )
                        if location.get(key) is not None
                    },
                }
            )
        compact.append(
            {
                "id": _clean(day.get("id"), 200),
                "date": _clean(day.get("date"), 50),
                "title": _clean(day.get("title"), 500),
                "start": _clean(day.get("start"), 500),
                "end": _clean(day.get("end"), 500),
                "stops": stops,
            }
        )
    return compact


def decision_requires_current_plan(*values: Any) -> bool:
    """Return whether wording explicitly compares the existing plan to alternatives."""
    text = "\n".join(_clean(value, 20_000) for value in values if _clean(value, 20_000))
    return bool(text and _CURRENT_PLAN_HINT_RE.search(text))


def _candidate_score(stop: dict[str, Any], message_text: str) -> int:
    normalized_message = _match_text(message_text)
    name = _match_text(stop.get("name"))
    if not name or len(name) < 3:
        return 0
    if name in normalized_message:
        return 1_000 + len(name)
    location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
    label = _match_text(location.get("label"))
    if label and len(label) >= 4 and label in normalized_message:
        return 600 + len(label)
    return 0


def _find_current_stop(
    days: list[dict[str, Any]],
    *,
    linked_day_id: str | None,
    message_text: str,
    option_types: set[str],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    linked_days = [day for day in days if str(day.get("id") or "") == str(linked_day_id or "")]
    search_groups = [linked_days, days] if linked_days else [days]
    for group in search_groups:
        scored: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
        for day in group:
            for stop in _stops(day):
                score = _candidate_score(stop, message_text)
                if score:
                    scored.append((score, day, stop))
        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            best_score = scored[0][0]
            best = [item for item in scored if item[0] == best_score]
            unique_ids = {str(item[2].get("id") or "") for item in best}
            if len(unique_ids) == 1:
                return best[0][1], best[0][2]

    normalized_types = {str(item or "").casefold() for item in option_types if item}
    overnight_decision = bool(normalized_types) and normalized_types <= _OVERNIGHT_TYPES
    if linked_days and overnight_decision:
        overnight = [
            stop
            for stop in _stops(linked_days[0])
            if str(stop.get("type") or "").casefold() in _OVERNIGHT_TYPES
        ]
        if overnight:
            return linked_days[0], overnight[-1]
    return None


def _option_matches_stop(option: dict[str, Any], stop: dict[str, Any]) -> bool:
    stop_id = _clean(stop.get("id"), 200)
    if stop_id and _clean(option.get("existing_stop_id"), 200) == stop_id:
        return True
    stop_name = _match_text(stop.get("name"))
    if not stop_name:
        return False
    title = _match_text(option.get("title"))
    query = _match_text(option.get("place_query"))
    return any(
        candidate
        and (candidate == stop_name or stop_name in candidate or candidate in stop_name)
        for candidate in (title, query)
    )


def _baseline_option(stop: dict[str, Any], *, option_id: str = "option-current") -> dict[str, Any]:
    location = deepcopy(stop.get("location")) if isinstance(stop.get("location"), dict) else {}
    image = _stop_image(stop)
    summary = _clean(stop.get("notes"), 2_000) or "Aktuell im Roadbook geplanter Stopp."
    result: dict[str, Any] = {
        "id": option_id,
        "title": _clean(stop.get("name"), 500) or "Aktuell geplanter Stopp",
        "summary": summary,
        "place_query": _location_query(stop),
        "stop_type": _clean(stop.get("type"), 100) or "waypoint",
        "pros": ["Keine Planänderung nötig"],
        "cons": [],
        "estimated_cost": {},
        "details": {
            "source": "roadbook",
            "baseline_reason": "Aktuell gespeicherter Roadbook-Plan",
        },
        "is_current_plan": True,
        "change_type": "keep_existing",
        "existing_stop_id": _clean(stop.get("id"), 200) or None,
    }
    if location:
        result["location"] = location
    if image:
        result["image"] = image
    return result


def ensure_current_plan_option(
    *,
    assistant_message: str,
    decision_title: str,
    question: str,
    linked_day_id: str | None,
    days: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None, bool, str | None]:
    """Verify and prepend the current Roadbook plan when the wording requires it.

    Returns ``(options, linked_day_id, baseline_required,
    current_plan_option_id)``.  The function is deliberately conservative: if
    the text asks whether the existing plan should be kept but no unique
    Roadbook stop can be resolved, it raises instead of presenting a misleading
    comparison.
    """
    normalized_options = [deepcopy(item) for item in options if isinstance(item, dict)]
    baseline_required = decision_requires_current_plan(
        assistant_message,
        decision_title,
        question,
    )
    if not baseline_required:
        for option in normalized_options:
            option["is_current_plan"] = False
            option["change_type"] = _clean(option.get("change_type"), 80) or "choose"
            option["existing_stop_id"] = None
        return normalized_options, linked_day_id, False, None

    option_types = {
        _clean(option.get("stop_type"), 100).casefold()
        for option in normalized_options
        if _clean(option.get("stop_type"), 100)
    }
    found = _find_current_stop(
        days,
        linked_day_id=linked_day_id,
        message_text="\n".join((assistant_message, decision_title, question)),
        option_types=option_types,
    )
    if found is None:
        raise DecisionBaselineError(
            "Die Entscheidung vergleicht den bestehenden Plan mit Alternativen, "
            "aber der aktuell geplante Stopp konnte im Roadbook nicht eindeutig "
            "zugeordnet werden. Bitte nenne den geplanten Stopp ausdrücklich."
        )

    day, stop = found
    resolved_day_id = _clean(day.get("id"), 200) or linked_day_id
    current: dict[str, Any] | None = None
    alternatives: list[dict[str, Any]] = []
    for option in normalized_options:
        if current is None and _option_matches_stop(option, stop):
            current = option
        else:
            alternatives.append(option)

    if current is None:
        current = _baseline_option(stop)
    else:
        baseline = _baseline_option(stop, option_id=_clean(current.get("id"), 200) or "option-current")
        for key in ("title", "summary", "place_query", "stop_type", "pros", "cons", "estimated_cost"):
            value = current.get(key)
            if value not in (None, "", [], {}):
                baseline[key] = deepcopy(value)
        current = baseline

    stop_name = _match_text(stop.get("name"))
    deduplicated: list[dict[str, Any]] = []
    for option in alternatives:
        option["is_current_plan"] = False
        option["change_type"] = "replace_existing"
        option["existing_stop_id"] = None
        title = _match_text(option.get("title"))
        query = _match_text(option.get("place_query"))
        if stop_name and any(
            candidate
            and (candidate == stop_name or stop_name in candidate or candidate in stop_name)
            for candidate in (title, query)
        ):
            continue
        deduplicated.append(option)

    final_options = [current, *deduplicated[:_MAX_ALTERNATIVES_WITH_BASELINE]]
    return final_options, resolved_day_id, True, str(current.get("id") or "option-current")
