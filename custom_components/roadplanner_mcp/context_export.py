"""Bounded, read-only handoff-context JSON/Markdown generation.

Writes derived context files (never canonical trip data) for external
bridges such as Drive/OneDrive sync - a failure here must never roll back a
canonical mutation, hence the "best effort" wrapper other collaborators call
after a successful commit.
"""

from __future__ import annotations

import logging
from typing import Any

from .bounded_json import _bounded_json_value
from .json_io import _json_bytes, _write_json_atomic, _write_text_atomic, utc_now_iso
from .stop_ordering import canonical_order_stops
from .trip_documents import HANDOFF_CONTEXT_SCHEMA_VERSION
from .trip_projections import _compact_day, _compact_trip
from .trip_repository import TripRepository
from .trip_state import TripState

MAX_CONTEXT_DAYS = 180
MAX_CONTEXT_STOPS_PER_DAY = 40
MAX_CONTEXT_JSON_BYTES = 3 * 1024 * 1024
MAX_CONTEXT_MARKDOWN_CHARS = 300_000

_LOGGER = logging.getLogger(__name__)


class ContextExport:
    """Build and write the bounded handoff-context JSON/Markdown companion."""

    def __init__(self, repository: TripRepository) -> None:
        self._repository = repository

    def _context_stop(self, stop: dict[str, Any]) -> dict[str, Any]:
        """Return a deliberately small stop projection for external planning."""
        details = stop.get("details", {})
        result = {
            "id": stop["id"],
            "name": _bounded_json_value(stop["name"], max_string=500),
            "type": _bounded_json_value(stop["type"], max_string=100),
            "arrival_time": stop.get("arrival_time"),
            "departure_time": stop.get("departure_time"),
            "location": _bounded_json_value(
                stop.get("location", {}),
                max_depth=4,
                max_items=25,
                max_string=500,
            ),
            "notes": _bounded_json_value(stop.get("notes", ""), max_string=1_500),
        }
        if isinstance(details, dict) and details:
            result["detail_sections"] = sorted(str(key) for key in details)[:50]
        return result

    def _context_payload(self, current: TripState) -> dict[str, Any]:
        """Build a bounded, read-only route context for external assistants."""
        ordered = current.ordered_days()
        total_stops = sum(len(document["stops"]) for document in ordered)
        context: dict[str, Any] = {
            "schema_version": HANDOFF_CONTEXT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "trip_id": current.trip_id,
            "base_revision": current.revision,
            "route": {
                "trip": _compact_trip(
                    current.trip_document["trip"],
                    include_details=True,
                ),
                "day_count": len(ordered),
                "stop_count": total_stops,
                "days": [],
                "days_truncated": False,
                "stops_truncated": False,
            },
            "instructions": {
                "purpose": "Read-only planning context for external assistants",
                "changeset_kind": "roadplanner_changeset",
                "do_not_edit_canonical_files": True,
                "include_trip_id_and_base_revision_in_changeset": True,
            },
        }
        route = context["route"]
        used_bytes = len(_json_bytes(context))
        represented_stops = 0

        for sequence, document in enumerate(ordered, start=1):
            if len(route["days"]) >= MAX_CONTEXT_DAYS:
                route["days_truncated"] = True
                break
            day = _compact_day(
                document,
                sequence=sequence,
                include_details=False,
            )
            day["stops"] = []
            day["stops_truncated"] = False
            day_bytes = len(_json_bytes(day)) + 64
            if used_bytes + day_bytes > MAX_CONTEXT_JSON_BYTES:
                route["days_truncated"] = True
                break
            route["days"].append(day)
            used_bytes += day_bytes

            for stop in canonical_order_stops(document["stops"]):
                if len(day["stops"]) >= MAX_CONTEXT_STOPS_PER_DAY:
                    day["stops_truncated"] = True
                    route["stops_truncated"] = True
                    break
                compact_stop = self._context_stop(stop)
                stop_bytes = len(_json_bytes(compact_stop)) + 32
                if used_bytes + stop_bytes > MAX_CONTEXT_JSON_BYTES:
                    day["stops_truncated"] = True
                    route["stops_truncated"] = True
                    route["days_truncated"] = sequence < len(ordered)
                    break
                day["stops"].append(compact_stop)
                represented_stops += 1
                used_bytes += stop_bytes
            if used_bytes >= MAX_CONTEXT_JSON_BYTES:
                break

        route["represented_day_count"] = len(route["days"])
        route["represented_stop_count"] = represented_stops
        if len(route["days"]) < len(ordered):
            route["days_truncated"] = True
        if represented_stops < total_stops:
            route["stops_truncated"] = True
        return context

    def _context_markdown(self, current: TripState) -> str:
        """Build a bounded human-readable context companion."""
        trip = current.trip_document["trip"]
        lines = [
            f"# {trip['title']}",
            "",
            f"Trip-ID: `{current.trip_id}`  ",
            f"Basis-Revision: `{current.revision}`  ",
            "",
        ]
        if trip.get("start_date") or trip.get("end_date"):
            lines.extend(
                [
                    f"Zeitraum: {trip.get('start_date') or '?'} bis "
                    f"{trip.get('end_date') or '?'}",
                    "",
                ]
            )

        truncated = False
        represented_stops = 0
        ordered = current.ordered_days()
        for sequence, document in enumerate(
            ordered[:MAX_CONTEXT_DAYS],
            start=1,
        ):
            day = document["day"]
            candidate = [
                f"## {sequence}. {day['title']} "
                f"({day.get('date') or 'ohne Datum'})",
            ]
            if day.get("start") or day.get("end"):
                candidate.append(
                    f"{day.get('start') or '?'} → {day.get('end') or '?'}"
                )
            if day.get("notes"):
                note = str(day["notes"])
                candidate.append(note[:1_500] + ("…" if len(note) > 1_500 else ""))
            details = day.get("details", {})
            if isinstance(details, dict):
                preferences = details.get("planning_preferences", [])
                if isinstance(preferences, list):
                    for preference in preferences[:20]:
                        if not isinstance(preference, dict):
                            continue
                        text = str(preference.get("text") or "").strip()
                        if text:
                            candidate.append(
                                "- Präferenz "
                                f"[`{preference.get('id', '?')}`]: {text[:1_000]}"
                            )
            for stop in canonical_order_stops(document["stops"])[:MAX_CONTEXT_STOPS_PER_DAY]:
                candidate.append(
                    f"- {stop['name']} [{stop['type']}] (`{stop['id']}`)"
                )
                represented_stops += 1
            if len(document["stops"]) > MAX_CONTEXT_STOPS_PER_DAY:
                candidate.append("- _Weitere Stopps nicht dargestellt._")
                truncated = True
            candidate.append("")
            candidate_text = "\n".join(candidate)
            existing_length = sum(len(line) + 1 for line in lines)
            if existing_length + len(candidate_text) > MAX_CONTEXT_MARKDOWN_CHARS:
                truncated = True
                break
            lines.extend(candidate)

        if len(ordered) > MAX_CONTEXT_DAYS:
            truncated = True
        if truncated:
            lines.extend(
                [
                    "_Der Kontext wurde für mobile Nutzung gekürzt. "
                    "Home Assistant enthält die vollständige Route._",
                    "",
                ]
            )
        lines.extend(
            [
                "---",
                "Erstelle Änderungen als roadplanner_changeset mit Trip-ID, "
                "Basis-Revision und gezielten Operationen. Diese Datei ist nur "
                "Lesekontext.",
                f"Dargestellte Stopps: {represented_stops}.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    def get_context_payload(self) -> dict[str, Any]:
        """Return bounded JSON context for an authenticated external bridge."""
        return self._context_payload(self._repository._load_state())

    def get_context_markdown(self) -> dict[str, Any]:
        """Return bounded Markdown context for an authenticated external bridge."""
        current = self._repository._load_state()
        context = self._context_payload(current)
        return {
            "trip_id": current.trip_id,
            "revision": current.revision,
            "content": self._context_markdown(current),
            "days_truncated": context["route"]["days_truncated"],
            "stops_truncated": context["route"]["stops_truncated"],
        }

    def write_context_best_effort(self, state: TripState) -> None:
        """Refresh derived context without making a canonical mutation fail."""
        try:
            self.write_context(state)
        except Exception as err:  # Derived export must never roll back canonical data.
            _LOGGER.warning("Roadplanner context export failed: %s", err)

    def write_context(self, state: TripState | None = None) -> dict[str, Any]:
        """Write bounded derived context files for Drive or OneDrive sync."""
        current = state or self._repository._load_state()
        outbox = self._repository.handoff_dir / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        context = self._context_payload(current)
        json_path = outbox / "roadplanner_context.json"
        _write_json_atomic(json_path, context)
        markdown_path = outbox / "roadplanner_context.md"
        _write_text_atomic(markdown_path, self._context_markdown(current))
        return {
            "trip_id": current.trip_id,
            "revision": current.revision,
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "days_truncated": context["route"]["days_truncated"],
            "stops_truncated": context["route"]["stops_truncated"],
        }
