"""Validated in-memory representation of one active trip."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from typing import Any

from .json_io import _json_bytes
from .routing_helpers import _trip_route_metrics
from .stop_ordering import canonical_order_stops
from .trip_documents import TRIP_SCHEMA_VERSION, _business_day_document
from .trip_projections import _compact_day, _compact_stop, _compact_trip

MAX_COORDINATOR_DAYS = 120
MAX_COORDINATOR_STOPS = 200


@dataclass(slots=True)
class TripState:
    """Validated in-memory representation of one active trip."""

    pointer: dict[str, Any]
    trip_document: dict[str, Any]
    day_documents: dict[str, dict[str, Any]]
    unmanaged_day_files: list[str]

    @property
    def trip_id(self) -> str:
        return self.trip_document["trip"]["id"]

    @property
    def revision(self) -> int:
        return self.trip_document["metadata"]["revision"]

    def clone(self) -> "TripState":
        return TripState(
            pointer=deepcopy(self.pointer),
            trip_document=deepcopy(self.trip_document),
            day_documents=deepcopy(self.day_documents),
            unmanaged_day_files=list(self.unmanaged_day_files),
        )

    def ordered_days(self) -> list[dict[str, Any]]:
        return [self.day_documents[ref["id"]] for ref in self.trip_document["days"]]

    def business_value(self) -> dict[str, Any]:
        return {
            "trip": deepcopy(self.trip_document["trip"]),
            "days": deepcopy(self.trip_document["days"]),
            "day_documents": [
                _business_day_document(day)
                for day in self.ordered_days()
            ],
        }

    def content_hash(self) -> str:
        return hashlib.sha256(_json_bytes(self.business_value())).hexdigest()

    def combined_export(self) -> dict[str, Any]:
        return {
            "schema_version": TRIP_SCHEMA_VERSION,
            "pointer": deepcopy(self.pointer),
            "trip": deepcopy(self.trip_document["trip"]),
            "days": deepcopy(self.ordered_days()),
            "metadata": deepcopy(self.trip_document["metadata"]),
        }

    def coordinator_payload(self) -> dict[str, Any]:
        """Return a bounded projection suitable for entities and mobile UI."""
        ordered = self.ordered_days()
        total_stops = sum(len(document["stops"]) for document in ordered)
        days: list[dict[str, Any]] = []
        flat_stops: list[dict[str, Any]] = []
        for sequence, document in enumerate(ordered, start=1):
            if len(days) < MAX_COORDINATOR_DAYS:
                days.append(
                    _compact_day(
                        document,
                        sequence=sequence,
                        include_details=False,
                    )
                )
            for stop_sequence, raw_stop in enumerate(
                canonical_order_stops(document["stops"]),
                start=1,
            ):
                if len(flat_stops) >= MAX_COORDINATOR_STOPS:
                    break
                stop = _compact_stop(raw_stop, include_details=False)
                stop.update(
                    {
                        "day_id": document["day"]["id"],
                        "day_sequence": sequence,
                        "day_date": document["day"].get("date"),
                        "day_title": document["day"].get("title"),
                        "stop_sequence": stop_sequence,
                    }
                )
                flat_stops.append(stop)
        route_metrics = _trip_route_metrics(self)
        return {
            "trip": _compact_trip(
                self.trip_document["trip"],
                include_details=False,
            ),
            "metadata": deepcopy(self.trip_document["metadata"]),
            "day_count": len(ordered),
            "stop_count": total_stops,
            "total_distance_km": route_metrics["total_distance_km"],
            "total_drive_minutes": route_metrics["total_drive_minutes"],
            "route_metrics": route_metrics,
            "days": days,
            "stops": flat_stops,
            "days_truncated": len(ordered) > len(days),
            "stops_truncated": total_stops > len(flat_stops),
            "unmanaged_day_files": list(self.unmanaged_day_files[:100]),
            "unmanaged_day_files_truncated": len(self.unmanaged_day_files) > 100,
        }
