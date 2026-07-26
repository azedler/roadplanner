"""Normalize canonical/legacy trip, day and stop JSON documents.

Owns the schema-version constants and field-level validators used only by
normalization; unknown legacy fields are preserved rather than dropped so an
older or externally edited document round-trips without data loss.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
from pathlib import PurePosixPath
import re
from typing import Any

from .identifiers import _stable_id, validate_identifier
from .json_io import ValidationError
from .json_tree_validation import MAX_STRING_LENGTH, _validate_json_tree
from .stop_ordering import normalize_stop_sequence

POINTER_SCHEMA_VERSION = 1
TRIP_SCHEMA_VERSION = 3
DAY_SCHEMA_VERSION = 1
HANDOFF_CONTEXT_SCHEMA_VERSION = 1

MAX_DAYS = 730
MAX_STOPS_PER_DAY = 500

_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def _ensure_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    return deepcopy(value)


def _ensure_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"'{field_name}' muss eine Liste sein")
    return deepcopy(value)


def _ensure_string(
    value: Any,
    field_name: str,
    *,
    default: str | None = None,
    allow_empty: bool = True,
    max_length: int = MAX_STRING_LENGTH,
) -> str:
    if value is None and default is not None:
        value = default
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss eine Zeichenkette sein")
    if not allow_empty and not value.strip():
        raise ValidationError(f"'{field_name}' darf nicht leer sein")
    if len(value) > max_length:
        raise ValidationError(
            f"'{field_name}' ist zu lang (maximal {max_length} Zeichen)"
        )
    return value


def _ensure_text(
    value: Any,
    field_name: str,
    *,
    default: str = "",
    allow_empty: bool = True,
    max_length: int = MAX_STRING_LENGTH,
) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except TypeError:
            text = str(value)
    if not allow_empty and not text.strip():
        raise ValidationError(f"'{field_name}' darf nicht leer sein")
    if len(text) > max_length:
        raise ValidationError(
            f"'{field_name}' ist zu lang (maximal {max_length} Zeichen)"
        )
    return text


def _ensure_optional_date(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss YYYY-MM-DD sein")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as err:
        raise ValidationError(
            f"'{field_name}' muss ein gültiges Datum YYYY-MM-DD sein"
        ) from err


def _ensure_optional_time(value: Any, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        raise ValidationError(f"'{field_name}' muss HH:MM sein")
    return value


def _ensure_non_negative_int(value: Any, field_name: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"'{field_name}' muss eine nicht-negative Ganzzahl sein")
    return value


def _ensure_positive_number(value: Any, field_name: str) -> int | float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValidationError(f"'{field_name}' muss eine nicht-negative Zahl sein")
    return value


def _ensure_optional_positive_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"'{field_name}' muss eine nicht-negative Ganzzahl sein")
    return value


def _validate_date_order(
    start: str | None,
    end: str | None,
    start_field: str,
    end_field: str,
) -> None:
    if start and end and date.fromisoformat(start) > date.fromisoformat(end):
        raise ValidationError(f"'{start_field}' darf nicht nach '{end_field}' liegen")


def _safe_day_file(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss ein relativer JSON-Pfad sein")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or len(path.parts) != 2
        or path.parts[0] != "days"
        or path.suffix.casefold() != ".json"
    ):
        raise ValidationError(
            f"'{field_name}' muss dem Muster days/<datei>.json entsprechen"
        )
    validate_identifier(path.stem, f"{field_name}.filename")
    return path.as_posix()


def _known_and_legacy_details(
    source: dict[str, Any],
    known_fields: set[str],
    existing: Any,
    field_name: str,
) -> dict[str, Any]:
    if existing is None:
        details: dict[str, Any] = {}
    elif isinstance(existing, dict):
        details = deepcopy(existing)
    else:
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    legacy = {
        key: deepcopy(value)
        for key, value in source.items()
        if key not in known_fields
    }
    if legacy:
        old = details.get("legacy")
        if isinstance(old, dict):
            details["legacy"] = {**legacy, **old}
        elif old is None:
            details["legacy"] = legacy
        else:
            details["legacy_source"] = legacy
    return _validate_json_tree(details, field_name)


def normalize_stop(
    raw_stop: dict[str, Any],
    *,
    index: int,
    fallback_timestamp: str,
) -> dict[str, Any]:
    """Normalize one stop while preserving unknown legacy fields in details."""
    if not isinstance(raw_stop, dict):
        raise ValidationError(f"Stopp {index + 1} muss ein JSON-Objekt sein")
    raw = deepcopy(raw_stop)
    stop_id = raw.get("id") or _stable_id("stop", raw, index)
    stop_id = validate_identifier(stop_id, f"stops[{index}].id")
    name = _ensure_string(
        raw.get("name", "Unbenannter Stopp"),
        f"stops[{index}].name",
        allow_empty=False,
        max_length=500,
    )
    known = {
        "id",
        "name",
        "type",
        "arrival_time",
        "departure_time",
        "position",
        "location",
        "notes",
        "details",
        "created_at",
        "updated_at",
    }
    details = _known_and_legacy_details(
        raw,
        known,
        raw.get("details"),
        f"stops[{index}].details",
    )
    created_at = _ensure_string(
        raw.get("created_at", fallback_timestamp),
        f"stops[{index}].created_at",
        allow_empty=False,
        max_length=100,
    )
    result = {
        "id": stop_id,
        "name": name,
        "type": _ensure_string(
            raw.get("type", "waypoint"),
            f"stops[{index}].type",
            allow_empty=False,
            max_length=100,
        ),
        "arrival_time": _ensure_optional_time(
            raw.get("arrival_time"),
            f"stops[{index}].arrival_time",
        ),
        "departure_time": _ensure_optional_time(
            raw.get("departure_time"),
            f"stops[{index}].departure_time",
        ),
        "location": _validate_json_tree(
            _ensure_object(raw.get("location", {}), f"stops[{index}].location"),
            f"stops[{index}].location",
        ),
        "notes": _ensure_string(
            raw.get("notes", ""),
            f"stops[{index}].notes",
        ),
        "details": details,
        "created_at": created_at,
        "updated_at": _ensure_string(
            raw.get("updated_at", created_at),
            f"stops[{index}].updated_at",
            allow_empty=False,
            max_length=100,
        ),
    }
    position = _ensure_optional_positive_int(
        raw.get("position"),
        f"stops[{index}].position",
    )
    if position is not None:
        result["position"] = position
    return result


def normalize_day_document(
    raw_document: dict[str, Any],
    *,
    fallback_id: str,
    fallback_timestamp: str,
) -> dict[str, Any]:
    """Normalize canonical or legacy day JSON."""
    if not isinstance(raw_document, dict):
        raise ValidationError("Tagesdatei muss ein JSON-Objekt enthalten")
    raw_schema = raw_document.get("schema_version", 1)
    if (
        isinstance(raw_schema, bool)
        or not isinstance(raw_schema, int)
        or raw_schema < 1
    ):
        raise ValidationError("Ungültige schema_version in Tagesdatei")
    if raw_schema > DAY_SCHEMA_VERSION:
        raise ValidationError(
            f"Tages-Schema {raw_schema} ist neuer als unterstützt "
            f"({DAY_SCHEMA_VERSION})"
        )
    canonical = isinstance(raw_document.get("day"), dict)
    raw_day = deepcopy(raw_document["day"] if canonical else raw_document)
    raw_stops = raw_document.get("stops", raw_day.pop("stops", []))
    if not isinstance(raw_stops, list):
        raise ValidationError("'stops' in einer Tagesdatei muss eine Liste sein")
    if len(raw_stops) > MAX_STOPS_PER_DAY:
        raise ValidationError(
            f"Ein Reisetag darf maximal {MAX_STOPS_PER_DAY} Stopps enthalten"
        )

    day_id = validate_identifier(
        raw_day.get("id") or fallback_id,
        "day.id",
    )
    day_date = _ensure_optional_date(raw_day.get("date"), "day.date")
    title_default = f"Tag {day_date}" if day_date else day_id
    known = {
        "id",
        "date",
        "title",
        "start",
        "end",
        "distance_km",
        "drive_minutes",
        "status",
        "notes",
        "details",
        "created_at",
        "updated_at",
    }
    details = _known_and_legacy_details(
        raw_day,
        known,
        raw_day.get("details"),
        "day.details",
    )
    if canonical:
        legacy_document = {
            key: deepcopy(value)
            for key, value in raw_document.items()
            if key not in {"schema_version", "day", "stops"}
        }
        if legacy_document:
            details["legacy_document"] = legacy_document
            details = _validate_json_tree(details, "day.details")
    created_at = _ensure_string(
        raw_day.get("created_at", fallback_timestamp),
        "day.created_at",
        allow_empty=False,
        max_length=100,
    )
    stops: list[dict[str, Any]] = []
    seen_stop_ids: set[str] = set()
    for index, raw_stop in enumerate(raw_stops):
        scoped_stop = deepcopy(raw_stop)
        if isinstance(scoped_stop, dict) and not scoped_stop.get("id"):
            scoped_stop["id"] = _stable_id(
                "stop",
                {"day_id": day_id, "stop": scoped_stop},
                index,
            )
        stop = normalize_stop(
            scoped_stop,
            index=index,
            fallback_timestamp=created_at,
        )
        if stop["id"] in seen_stop_ids:
            raise ValidationError(f"Doppelte Stopp-ID in Tag {day_id}: {stop['id']}")
        seen_stop_ids.add(stop["id"])
        stops.append(stop)

    # Roadplanner 3.1 treats the user-confirmed list order as authoritative for
    # legacy/incomplete days and normalizes them into a complete gap-free
    # position set. The existing storage migration/write path persists the
    # idempotent result without a destructive standalone migration.
    normalize_stop_sequence(stops)

    return {
        "schema_version": DAY_SCHEMA_VERSION,
        "day": {
            "id": day_id,
            "date": day_date,
            "title": _ensure_string(
                raw_day.get("title", title_default),
                "day.title",
                allow_empty=False,
                max_length=500,
            ),
            "start": _ensure_string(
                raw_day.get("start", ""),
                "day.start",
                max_length=500,
            ),
            "end": _ensure_string(
                raw_day.get("end", ""),
                "day.end",
                max_length=500,
            ),
            "distance_km": _ensure_positive_number(
                raw_day.get("distance_km"),
                "day.distance_km",
            ),
            "drive_minutes": _ensure_optional_positive_int(
                raw_day.get("drive_minutes"),
                "day.drive_minutes",
            ),
            "status": _ensure_string(
                raw_day.get("status", "planned"),
                "day.status",
                allow_empty=False,
                max_length=100,
            ),
            "notes": _ensure_text(raw_day.get("notes", ""), "day.notes"),
            "details": details,
            "created_at": created_at,
            "updated_at": _ensure_string(
                raw_day.get("updated_at", created_at),
                "day.updated_at",
                allow_empty=False,
                max_length=100,
            ),
        },
        "stops": stops,
    }


def normalize_trip_document(
    raw_document: dict[str, Any],
    *,
    expected_trip_id: str,
    fallback_timestamp: str,
) -> dict[str, Any]:
    """Normalize a trip index and validate all day references."""
    if not isinstance(raw_document, dict):
        raise ValidationError("trip.json muss ein JSON-Objekt enthalten")
    raw_schema = raw_document.get("schema_version", 1)
    if (
        isinstance(raw_schema, bool)
        or not isinstance(raw_schema, int)
        or raw_schema < 1
    ):
        raise ValidationError("Ungültige schema_version in trip.json")
    if raw_schema > TRIP_SCHEMA_VERSION:
        raise ValidationError(
            f"Trip-Schema {raw_schema} ist neuer als unterstützt "
            f"({TRIP_SCHEMA_VERSION})"
        )
    canonical = isinstance(raw_document.get("trip"), dict)
    if canonical:
        raw_trip = deepcopy(raw_document["trip"])
    else:
        raw_trip = {
            key: deepcopy(value)
            for key, value in raw_document.items()
            if key not in {"schema_version", "days", "metadata"}
        }
        raw_trip.setdefault("id", expected_trip_id)
        if "title" not in raw_trip and isinstance(raw_trip.get("name"), str):
            raw_trip["title"] = raw_trip["name"]
    trip_id = validate_identifier(raw_trip.get("id"), "trip.id")
    if trip_id != expected_trip_id:
        raise ValidationError(
            f"trip.id '{trip_id}' passt nicht zum aktiven Ordner "
            f"'{expected_trip_id}'"
        )
    start_date = _ensure_optional_date(raw_trip.get("start_date"), "trip.start_date")
    end_date = _ensure_optional_date(raw_trip.get("end_date"), "trip.end_date")
    _validate_date_order(start_date, end_date, "trip.start_date", "trip.end_date")
    known = {
        "id",
        "title",
        "status",
        "start_date",
        "end_date",
        "travelers",
        "vehicle",
        "preferences",
        "notes",
        "details",
    }
    trip_details = _known_and_legacy_details(
        raw_trip,
        known,
        raw_trip.get("details"),
        "trip.details",
    )
    legacy_document = {
        key: deepcopy(value)
        for key, value in raw_document.items()
        if canonical
        and key not in {"schema_version", "trip", "days", "metadata"}
    }
    if legacy_document:
        trip_details["legacy_document"] = legacy_document

    raw_refs = raw_document.get("days", [])
    if not isinstance(raw_refs, list):
        raise ValidationError("'days' in trip.json muss eine Liste sein")
    if len(raw_refs) > MAX_DAYS:
        raise ValidationError(f"Eine Reise darf maximal {MAX_DAYS} Tage enthalten")
    refs: list[dict[str, str]] = []
    legacy_refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_files: set[str] = set()
    for index, raw_ref in enumerate(raw_refs):
        if isinstance(raw_ref, str):
            raw_value = PurePosixPath(raw_ref)
            raw_name = raw_value.name
            if raw_name.casefold().endswith(".json"):
                day_id_value = PurePosixPath(raw_name).stem
                day_file_value = (
                    raw_ref
                    if len(raw_value.parts) == 2 and raw_value.parts[0] == "days"
                    else f"days/{raw_name}"
                )
            else:
                day_id_value = raw_ref
                day_file_value = f"days/{raw_ref}.json"
            ref_source: dict[str, Any] = {}
        elif isinstance(raw_ref, dict):
            ref_source = raw_ref
            raw_file = raw_ref.get("file")
            day_id_value = raw_ref.get("id") or raw_ref.get("day_id")
            if day_id_value is None and isinstance(raw_file, str):
                day_id_value = PurePosixPath(raw_file).stem
            day_file_value = raw_file or (
                f"days/{day_id_value}.json" if day_id_value is not None else None
            )
        else:
            raise ValidationError(
                f"days[{index}] muss eine ID oder ein JSON-Objekt sein"
            )
        day_id = validate_identifier(day_id_value, f"days[{index}].id")
        day_file = _safe_day_file(day_file_value, f"days[{index}].file")
        if day_id in seen_ids:
            raise ValidationError(f"Doppelte Tages-ID: {day_id}")
        if day_file in seen_files:
            raise ValidationError(f"Doppelte Tagesdatei: {day_file}")
        seen_ids.add(day_id)
        seen_files.add(day_file)
        refs.append({"id": day_id, "file": day_file})
        ref_legacy = {
            key: deepcopy(value)
            for key, value in ref_source.items()
            if key not in {"id", "day_id", "file"}
        }
        if ref_legacy:
            legacy_refs.append({"id": day_id, "values": ref_legacy})

    raw_metadata = raw_document.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ValidationError("'metadata' in trip.json muss ein Objekt sein")
    created_at = _ensure_string(
        raw_metadata.get("created_at", fallback_timestamp),
        "metadata.created_at",
        allow_empty=False,
        max_length=100,
    )
    metadata: dict[str, Any] = {
        "revision": _ensure_non_negative_int(
            raw_metadata.get("revision"),
            "metadata.revision",
            default=1,
        ),
        "created_at": created_at,
        "updated_at": _ensure_string(
            raw_metadata.get("updated_at", created_at),
            "metadata.updated_at",
            allow_empty=False,
            max_length=100,
        ),
        "updated_by": _ensure_string(
            raw_metadata.get("updated_by", "unknown"),
            "metadata.updated_by",
            allow_empty=False,
            max_length=200,
        ),
    }
    if raw_metadata.get("last_operation") is not None:
        metadata["last_operation"] = _ensure_string(
            raw_metadata.get("last_operation"),
            "metadata.last_operation",
            allow_empty=False,
            max_length=100,
        )
    if raw_metadata.get("content_hash") is not None:
        metadata["content_hash"] = _ensure_string(
            raw_metadata.get("content_hash"),
            "metadata.content_hash",
            allow_empty=False,
            max_length=128,
        )
    legacy_metadata = {
        key: deepcopy(value)
        for key, value in raw_metadata.items()
        if key not in {
            "revision",
            "created_at",
            "updated_at",
            "updated_by",
            "last_operation",
            "content_hash",
        }
    }
    if legacy_metadata:
        trip_details["legacy_metadata"] = legacy_metadata
    if legacy_refs:
        trip_details["legacy_day_references"] = legacy_refs
    trip_details = _validate_json_tree(trip_details, "trip.details")

    return {
        "schema_version": TRIP_SCHEMA_VERSION,
        "trip": {
            "id": trip_id,
            "title": _ensure_string(
                raw_trip.get("title", expected_trip_id.replace("-", " ").title()),
                "trip.title",
                allow_empty=False,
                max_length=500,
            ),
            "status": _ensure_string(
                raw_trip.get("status", "planning"),
                "trip.status",
                allow_empty=False,
                max_length=100,
            ),
            "start_date": start_date,
            "end_date": end_date,
            "travelers": _validate_json_tree(
                _ensure_list(raw_trip.get("travelers", []), "trip.travelers"),
                "trip.travelers",
            ),
            "vehicle": _validate_json_tree(
                _ensure_object(raw_trip.get("vehicle", {}), "trip.vehicle"),
                "trip.vehicle",
            ),
            "preferences": _validate_json_tree(
                _ensure_object(raw_trip.get("preferences", {}), "trip.preferences"),
                "trip.preferences",
            ),
            "notes": _ensure_text(raw_trip.get("notes", ""), "trip.notes"),
            "details": trip_details,
        },
        "days": refs,
        "metadata": metadata,
    }


def _without_audit_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Remove only Roadplanner audit fields from one known domain object."""
    result = deepcopy(value)
    result.pop("created_at", None)
    result.pop("updated_at", None)
    return result


def _business_day_document(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": document["schema_version"],
        "day": _without_audit_fields(document["day"]),
        "stops": [
            _without_audit_fields(stop)
            for stop in document["stops"]
        ],
    }
