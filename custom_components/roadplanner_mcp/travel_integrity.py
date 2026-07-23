"""Derived travel-integrity report for Roadplanner trips.

The Roadbook remains the source of truth.  This module inspects the current
canonical day payload plus sidecar data and returns a bounded, read-only report
for the panel and the assistant.  It never mutates trip data and never invents
coordinates, times, images, or route metrics.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .canonical_day import is_overnight_stop, location_status
from .stop_ordering import has_complete_explicit_positions

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_ROUTE_GOOD = frozenset({"calculated", "manual_override", "not_required"})
_ROUTE_PARTIAL = frozenset({"partial", "stale"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(day: dict[str, Any]) -> dict[str, Any]:
    value = day.get("canonical")
    return value if isinstance(value, dict) else {}


def _roadbook_stops(day: dict[str, Any]) -> list[dict[str, Any]]:
    model = _canonical(day)
    source = model.get("stops") if isinstance(model.get("stops"), list) else day.get("stops")
    return [item for item in _list(source) if isinstance(item, dict)]


def _route_nodes(day: dict[str, Any]) -> list[dict[str, Any]]:
    model = _canonical(day)
    source = model.get("route_nodes") if isinstance(model.get("route_nodes"), list) else _roadbook_stops(day)
    return [item for item in _list(source) if isinstance(item, dict)]


def _routing(day: dict[str, Any]) -> dict[str, Any]:
    direct = day.get("routing")
    if isinstance(direct, dict):
        return direct
    details = _dict(day.get("details"))
    return _dict(details.get("routing"))


def _image_count_for_stop(
    stop_id: str,
    *,
    destination_galleries: dict[str, Any],
    media_by_stop: dict[str, Any],
) -> tuple[int, str]:
    own = media_by_stop.get(stop_id)
    if isinstance(own, list) and own:
        return len(own), "travel"
    gallery = destination_galleries.get(stop_id)
    images = gallery.get("images") if isinstance(gallery, dict) else None
    if isinstance(images, list) and images:
        return len(images), "planning"
    return 0, "missing"


def _issue(
    *,
    code: str,
    severity: str,
    category: str,
    title: str,
    message: str,
    day: dict[str, Any] | None = None,
    stop: dict[str, Any] | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    value = {
        "id": ":".join(
            part
            for part in (
                code,
                _text(day.get("id")) if day else "",
                _text(stop.get("id")) if stop else "",
            )
            if part
        ),
        "code": code,
        "severity": severity if severity in _SEVERITY_ORDER else "info",
        "category": category,
        "title": title,
        "message": message,
        "day_id": _text(day.get("id")) or None if day else None,
        "day_title": _text(day.get("title")) or None if day else None,
        "day_date": _text(day.get("date")) or None if day else None,
        "stop_id": _text(stop.get("id")) or None if stop else None,
        "stop_name": _text(stop.get("name")) or None if stop else None,
        "action": action,
    }
    return value


def _percentage(numerator: float, denominator: float, *, empty: int = 100) -> int:
    if denominator <= 0:
        return empty
    return max(0, min(100, round(100 * numerator / denominator)))


def _route_score(day_reports: list[dict[str, Any]]) -> int:
    candidates = [item for item in day_reports if item["route_required"]]
    if not candidates:
        return 100
    points = 0.0
    for item in candidates:
        status = item["route_status"]
        if status in _ROUTE_GOOD:
            points += 1.0
        elif status in _ROUTE_PARTIAL:
            points += 0.45
        elif item["coordinate_count"] >= 2:
            points += 0.25
    return _percentage(points, len(candidates))


def build_travel_integrity(
    days: Iterable[dict[str, Any]],
    *,
    destination_galleries: dict[str, Any] | None = None,
    media_by_stop: dict[str, Any] | None = None,
    route_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one bounded trip-quality report.

    Schedule times are intentionally informational.  Missing times do not lower
    the integrity score because the confirmed stop order is independent from a
    minute-by-minute schedule.
    """

    galleries = destination_galleries if isinstance(destination_galleries, dict) else {}
    own_media = media_by_stop if isinstance(media_by_stop, dict) else {}
    route_metrics = route_metrics if isinstance(route_metrics, dict) else {}
    valid_days = [deepcopy(day) for day in days if isinstance(day, dict)]

    if not valid_days:
        issue = _issue(
            code="trip_without_days",
            severity="error",
            category="planning",
            title="Die Reise enthält noch keine Reisetage",
            message="Lege mindestens einen Reisetag an, bevor Roadplanner die Reisequalität bewerten kann.",
            action="review_trip",
        )
        return {
            "version": 1,
            "status": "incomplete",
            "score": 0,
            "dimensions": {"sequence": 0, "locations": 0, "routes": 0, "visuals": 0},
            "stats": {
                "day_count": 0,
                "stop_count": 0,
                "position_issue_count": 0,
                "location_attention_count": 0,
                "missing_location_count": 0,
                "ambiguous_location_count": 0,
                "unverified_location_count": 0,
                "unreviewed_place_count": 0,
                "route_issue_count": 0,
                "visual_missing_count": 0,
                "visualized_stop_count": 0,
                "own_photo_stop_count": 0,
                "schedule_hint_count": 0,
                "overnight_missing_day_count": 0,
                "repairable_location_count": 0,
                "trip_route_status": _text(route_metrics.get("status")) or None,
            },
            "days": [],
            "issues": [issue],
            "blocking_issue_count": 1,
            "warning_count": 0,
            "info_count": 0,
        }

    issues: list[dict[str, Any]] = []
    day_reports: list[dict[str, Any]] = []
    total_stops = 0
    positioned_stops = 0
    resolved_locations = 0.0
    attention_locations = 0
    missing_locations = 0
    ambiguous_locations = 0
    unverified_locations = 0
    unreviewed_places = 0
    visualized_stops = 0
    own_photo_stops = 0
    schedule_hint_count = 0
    overnight_missing_days = 0

    for day_index, day in enumerate(valid_days):
        stops = _roadbook_stops(day)
        route_nodes = _route_nodes(day)
        model = _canonical(day)
        total_stops += len(stops)
        complete_positions = len(stops) <= 1 or has_complete_explicit_positions(stops)
        if complete_positions:
            positioned_stops += len(stops)
        else:
            explicit_positions = {
                item.get("position")
                for item in stops
                if isinstance(item.get("position"), int)
                and not isinstance(item.get("position"), bool)
                and item.get("position") > 0
            }
            positioned_stops += min(len(explicit_positions), len(stops))
            issues.append(
                _issue(
                    code="sequence_incomplete",
                    severity="warning",
                    category="sequence",
                    title="Stopp-Reihenfolge ist nicht vollständig gespeichert",
                    message=(
                        "Die sichtbare Roadbook-Reihenfolge bleibt erhalten, sollte aber "
                        "als lückenlose Positionen gespeichert werden."
                    ),
                    day=day,
                    action="normalize_sequence",
                )
            )

        day_missing = 0
        day_ambiguous = 0
        day_unverified = 0
        day_unreviewed = 0
        day_visuals = 0
        day_own_photos = 0
        day_schedule_hints = 0
        for stop in stops:
            stop_id = _text(stop.get("id"))
            status = _text(stop.get("location_status")) or location_status(stop)
            details = _dict(stop.get("details"))
            place_profile = _dict(details.get("place_profile"))
            place_confirmed = bool(_text(place_profile.get("confirmed_at")))
            if status == "resolved" and place_confirmed:
                resolved_locations += 1.0
            elif status == "resolved":
                # A coordinate alone is not enough for a trustworthy stop. Keep
                # routing available, but ask the user to confirm the actual
                # place, address and provider identity once.
                resolved_locations += 0.75
                attention_locations += 1
                unreviewed_places += 1
                day_unreviewed += 1
                issues.append(
                    _issue(
                        code="place_profile_unreviewed",
                        severity="warning",
                        category="location",
                        title=f"Ort für {_text(stop.get('name')) or 'Stopp'} vervollständigen",
                        message=(
                            "Koordinaten sind vorhanden. Bestätige zusätzlich Name, Adresse, "
                            "Kategorie, Quelle und verfügbare Kontaktdaten."
                        ),
                        day=day,
                        stop=stop,
                        action="repair_location",
                    )
                )
            elif status == "unverified":
                resolved_locations += 0.5
                attention_locations += 1
                unverified_locations += 1
                day_unverified += 1
                issues.append(
                    _issue(
                        code="location_unverified",
                        severity="warning",
                        category="location",
                        title=f"Ort für {_text(stop.get('name')) or 'Stopp'} prüfen",
                        message="Koordinaten sind vorhanden, der konkrete Ort ist aber noch nicht eindeutig bestätigt.",
                        day=day,
                        stop=stop,
                        action="repair_location",
                    )
                )
            elif status == "ambiguous":
                attention_locations += 1
                ambiguous_locations += 1
                day_ambiguous += 1
                issues.append(
                    _issue(
                        code="location_ambiguous",
                        severity="error",
                        category="location",
                        title=f"Ort für {_text(stop.get('name')) or 'Stopp'} auswählen",
                        message="Mehrere mögliche Ortsprofile wurden gefunden.",
                        day=day,
                        stop=stop,
                        action="repair_location",
                    )
                )
            else:
                attention_locations += 1
                missing_locations += 1
                day_missing += 1
                issues.append(
                    _issue(
                        code="location_missing",
                        severity="error",
                        category="location",
                        title=f"Ort für {_text(stop.get('name')) or 'Stopp'} fehlt",
                        message=(
                            "Der Stopp bleibt im Tagesablauf, benötigt aber einen bestätigten "
                            "Kartenpunkt und ein überprüfbares Ortsprofil."
                        ),
                        day=day,
                        stop=stop,
                        action="repair_location",
                    )
                )

            if not _text(stop.get("arrival_time")) and not _text(stop.get("departure_time")):
                schedule_hint_count += 1
                day_schedule_hints += 1

            count, source = _image_count_for_stop(
                stop_id,
                destination_galleries=galleries,
                media_by_stop=own_media,
            )
            if count:
                visualized_stops += 1
                day_visuals += 1
                if source == "travel":
                    own_photo_stops += 1
                    day_own_photos += 1
            elif stop_id:
                issues.append(
                    _issue(
                        code="visual_missing",
                        severity="info",
                        category="visual",
                        title=f"Noch kein Bild für {_text(stop.get('name')) or 'Stopp'}",
                        message="Roadplanner kann automatisch Planungsbilder ergänzen.",
                        day=day,
                        stop=stop,
                        action="enrich_visual",
                    )
                )

        route = _routing(day)
        route_status = _text(route.get("status")) or (
            "partial" if model.get("missing_coordinate_count") else "not_calculated"
        )
        route_required = len(route_nodes) >= 2
        coordinate_count = int(model.get("coordinate_count") or 0)
        if route_required:
            if day_missing or day_ambiguous:
                issues.append(
                    _issue(
                        code="route_incomplete_locations",
                        severity="warning",
                        category="route",
                        title="Tagesroute ist wegen fehlender GPS-Daten unvollständig",
                        message=f"{day_missing + day_ambiguous} Stopp(s) benötigen einen Kartenpunkt.",
                        day=day,
                        action="repair_location",
                    )
                )
            elif route_status in {"stale", "not_calculated", ""}:
                issues.append(
                    _issue(
                        code="route_not_current",
                        severity="warning",
                        category="route",
                        title="Tagesroute neu berechnen",
                        message="Die Stopps sind kartierbar, aber die gespeicherte Route ist nicht aktuell.",
                        day=day,
                        action="calculate_route",
                    )
                )
            elif route_status == "partial":
                issues.append(
                    _issue(
                        code="route_partial",
                        severity="warning",
                        category="route",
                        title="Tagesroute enthält Lücken",
                        message="Mindestens ein Routensegment konnte nicht vollständig berechnet werden.",
                        day=day,
                        action="calculate_route",
                    )
                )

        overnight = any(is_overnight_stop(stop) for stop in stops)
        if stops and day_index < len(valid_days) - 1 and not overnight:
            overnight_missing_days += 1
            issues.append(
                _issue(
                    code="overnight_missing",
                    severity="info",
                    category="planning",
                    title="Kein Übernachtungsstopp markiert",
                    message="Der Folgetag kann deshalb keinen eindeutigen Start vom Vortag erben.",
                    day=day,
                    action="review_day",
                )
            )

        day_reports.append(
            {
                "day_id": _text(day.get("id")),
                "date": _text(day.get("date")) or None,
                "title": _text(day.get("title")) or "Reisetag",
                "stop_count": len(stops),
                "positions_complete": complete_positions,
                "resolved_location_count": len(stops) - day_missing - day_ambiguous - day_unverified,
                "missing_location_count": day_missing,
                "ambiguous_location_count": day_ambiguous,
                "unverified_location_count": day_unverified,
                "unreviewed_place_count": day_unreviewed,
                "visual_count": day_visuals,
                "own_photo_stop_count": day_own_photos,
                "schedule_hint_count": day_schedule_hints,
                "coordinate_count": coordinate_count,
                "route_required": route_required,
                "route_status": route_status,
                "overnight_present": overnight,
            }
        )

    if total_stops == 0:
        issues.append(
            _issue(
                code="trip_without_stops",
                severity="error",
                category="planning",
                title="Die Reise enthält noch keine Stopps",
                message="Lege mindestens einen konkreten Stopp an, damit Roadplanner Route, GPS und Bilder prüfen kann.",
                action="review_trip",
            )
        )

    sequence_score = _percentage(positioned_stops, total_stops, empty=0)
    location_score = _percentage(resolved_locations, total_stops, empty=0)
    route_score = _route_score(day_reports) if total_stops else 0
    visual_score = _percentage(visualized_stops, total_stops, empty=0)
    overall_score = round(
        sequence_score * 0.35
        + location_score * 0.35
        + route_score * 0.20
        + visual_score * 0.10
    )
    status = (
        "ready"
        if total_stops and overall_score >= 90 and not attention_locations
        else "attention"
        if total_stops and overall_score >= 70
        else "incomplete"
    )

    issues.sort(
        key=lambda item: (
            _SEVERITY_ORDER.get(_text(item.get("severity")), 9),
            _text(item.get("day_date")),
            _text(item.get("day_title")),
            _text(item.get("stop_name")),
            _text(item.get("code")),
        )
    )

    return {
        "version": 1,
        "status": status,
        "score": overall_score,
        "dimensions": {
            "sequence": sequence_score,
            "locations": location_score,
            "routes": route_score,
            "visuals": visual_score,
        },
        "stats": {
            "day_count": len(valid_days),
            "stop_count": total_stops,
            "position_issue_count": sum(1 for item in day_reports if not item["positions_complete"]),
            "location_attention_count": attention_locations,
            "missing_location_count": missing_locations,
            "ambiguous_location_count": ambiguous_locations,
            "unverified_location_count": unverified_locations,
            "unreviewed_place_count": unreviewed_places,
            "route_issue_count": sum(1 for item in issues if item["category"] == "route"),
            "visual_missing_count": max(0, total_stops - visualized_stops),
            "visualized_stop_count": visualized_stops,
            "own_photo_stop_count": own_photo_stops,
            "schedule_hint_count": schedule_hint_count,
            "overnight_missing_day_count": overnight_missing_days,
            "repairable_location_count": attention_locations,
            "trip_route_status": _text(route_metrics.get("status")) or None,
        },
        "days": day_reports,
        "issues": issues[:500],
        "blocking_issue_count": sum(1 for item in issues if item["severity"] == "error"),
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
        "info_count": sum(1 for item in issues if item["severity"] == "info"),
    }
