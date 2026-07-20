"""Roadplanner storage, validation, migration, and domain logic.

The module intentionally has no Home Assistant imports. The canonical route is
split into a small trip index plus one JSON document per travel day:

    roadbook/active_trip.json
    roadbook/trips/<trip_id>/trip.json
    roadbook/trips/<trip_id>/days/<day_id>.json

Only these canonical files are managed. Other files in an existing roadbook are
left untouched.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from tempfile import NamedTemporaryFile
from typing import Any
import uuid

POINTER_SCHEMA_VERSION = 1
TRIP_SCHEMA_VERSION = 3
DAY_SCHEMA_VERSION = 1
HANDOFF_CONTEXT_SCHEMA_VERSION = 1

MAX_JSON_FILE_BYTES = 5 * 1024 * 1024
MAX_DAYS = 730
MAX_STOPS_PER_DAY = 500
MAX_STRING_LENGTH = 100_000
MAX_DETAILS_DEPTH = 12
MAX_DETAILS_ITEMS = 20_000
MAX_SUMMARY_DAYS = 60
MAX_SUMMARY_STOPS = 20
MAX_SEARCH_RESULTS = 50
MAX_COORDINATOR_DAYS = 120
MAX_COORDINATOR_STOPS = 200
MAX_CONTEXT_DAYS = 180
MAX_CONTEXT_STOPS_PER_DAY = 40
MAX_CONTEXT_JSON_BYTES = 3 * 1024 * 1024
MAX_CONTEXT_MARKDOWN_CHARS = 300_000

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ROUTING_DETAIL_KEY = "routing"
_FERRY_STOP_TYPES = frozenset({"ferry", "ferry_terminal", "terminal"})
_TRANSPORT_MODES = frozenset({"driving", "ferry", "break"})

_OVERNIGHT_STOP_TYPES = frozenset({
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
})
_LOGGER = logging.getLogger(__name__)


class RoadplannerError(Exception):
    """Base error for all Roadplanner operations."""


class TripNotFoundError(RoadplannerError):
    """Raised when an active or requested trip cannot be found."""


class ValidationError(RoadplannerError):
    """Raised when input or stored data is invalid."""


class StorageError(RoadplannerError):
    """Raised when canonical data cannot be read or written safely."""


class RevisionConflictError(RoadplannerError):
    """Raised when optimistic concurrency detects a stale write."""

    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(
            "Die Reise wurde zwischenzeitlich geändert: "
            f"erwartete Revision {expected}, aktuelle Revision {actual}. "
            "Reise neu laden und die Änderung auf dem aktuellen Stand wiederholen."
        )
        self.expected = expected
        self.actual = actual


class ConcurrentModificationError(RoadplannerError):
    """Raised when a day file changed without a matching revision update."""

    def __init__(self) -> None:
        super().__init__(
            "Mindestens eine Roadplanner-Datei wurde außerhalb der Integration "
            "geändert. Nutze zuerst 'adopt_external_changes' oder stelle eine "
            "Sicherung wieder her."
        )


def utc_now_iso() -> str:
    """Return a stable UTC timestamp without microseconds."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _stable_id(prefix: str, value: Any, index: int = 0) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    name = f"roadplanner:{prefix}:{index}:{material}"
    return f"{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, name).hex[:12]}"


def validate_identifier(value: Any, field_name: str) -> str:
    """Validate IDs and slugs used in filenames."""
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            f"'{field_name}' darf nur Buchstaben, Zahlen, '_' und '-' enthalten "
            "und muss 1 bis 128 Zeichen lang sein"
        )
    return value.strip()


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


def _validate_json_tree(value: Any, field_name: str) -> Any:
    """Reject overly deep or huge extension objects while preserving their data."""
    item_count = 0

    def walk(node: Any, depth: int) -> None:
        nonlocal item_count
        item_count += 1
        if item_count > MAX_DETAILS_ITEMS:
            raise ValidationError(f"'{field_name}' enthält zu viele Werte")
        if depth > MAX_DETAILS_DEPTH:
            raise ValidationError(f"'{field_name}' ist zu tief verschachtelt")
        if node is None or isinstance(node, (bool, int)):
            return
        if isinstance(node, float):
            if node != node or node in (float("inf"), float("-inf")):
                raise ValidationError(f"'{field_name}' enthält ungültige Zahlen")
            return
        if isinstance(node, str):
            if len(node) > MAX_STRING_LENGTH:
                raise ValidationError(f"'{field_name}' enthält zu langen Text")
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
            return
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    raise ValidationError(f"'{field_name}' enthält Nicht-Text-Schlüssel")
                if len(key) > 500:
                    raise ValidationError(f"'{field_name}' enthält zu lange Schlüssel")
                walk(child, depth + 1)
            return
        raise ValidationError(f"'{field_name}' enthält nicht JSON-kompatible Werte")

    result = deepcopy(value)
    walk(result, 0)
    return result


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


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as err:
        raise ValidationError(f"Daten sind nicht JSON-kompatibel: {err}") from err


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_JSON_FILE_BYTES:
            raise ValidationError(f"JSON-Datei ist größer als 5 MiB: {path}")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle, parse_constant=_reject_json_constant)
    except FileNotFoundError as err:
        raise TripNotFoundError(f"Datei nicht gefunden: {path}") from err
    except (OSError, json.JSONDecodeError, ValueError) as err:
        raise StorageError(f"JSON-Datei kann nicht gelesen werden: {path}: {err}") from err
    if not isinstance(value, dict):
        raise ValidationError(f"JSON-Datei muss ein Objekt enthalten: {path}")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Ungültiger JSON-Zahlenwert: {value}")


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=False,
    )
    encoded_bytes = encoded.encode("utf-8")
    if len(encoded_bytes) > MAX_JSON_FILE_BYTES:
        raise ValidationError(f"JSON-Datei wäre größer als 5 MiB: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        _fsync_dir(path.parent)
    except (OSError, TypeError, ValueError) as err:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise StorageError(f"Datei kann nicht atomisch geschrieben werden: {path}") from err


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
        _fsync_dir(path.parent)
    except OSError as err:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise StorageError(f"Datei kann nicht atomisch geschrieben werden: {path}") from err


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
    return {
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


def _bounded_json_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 6,
    max_items: int = 100,
    max_string: int = 4_000,
) -> Any:
    """Return a deterministic, JSON-safe projection for model and UI responses."""
    if isinstance(value, str):
        return value if len(value) <= max_string else value[: max_string - 1] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= max_depth:
        return "<gekürzt: maximale Verschachtelung>"
    if isinstance(value, list):
        result = [
            _bounded_json_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            result.append(f"<gekürzt: {len(value) - max_items} weitere Werte>")
        return result
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= max_items:
                result["_roadplanner_truncated"] = len(value) - max_items
                break
            result[str(key)] = _bounded_json_value(
                child,
                depth=depth + 1,
                max_depth=max_depth,
                max_items=max_items,
                max_string=max_string,
            )
        return result
    return str(value)[:max_string]



def _stop_coordinate(stop: Any) -> tuple[float, float] | None:
    """Return a validated ``(latitude, longitude)`` pair for one stop."""
    if not isinstance(stop, dict):
        return None
    location = stop.get("location")
    if not isinstance(location, dict):
        return None
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get("longitude", location.get("lon", location.get("lng")))
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _is_overnight_stop(stop: Any) -> bool:
    return (
        isinstance(stop, dict)
        and str(stop.get("type") or "").casefold() in _OVERNIGHT_STOP_TYPES
    )


def _stop_transport(stop: Any) -> dict[str, Any]:
    if not isinstance(stop, dict):
        return {}
    details = stop.get("details")
    if not isinstance(details, dict):
        return {}
    transport = details.get("transport")
    return transport if isinstance(transport, dict) else {}


def _is_ferry_stop(stop: Any) -> bool:
    """Return whether a canonical stop explicitly represents a ferry terminal.

    Free text such as "Ziel hinter der Fähre" is deliberately insufficient:
    misclassifying it would reconnect a ferry leg as a straight or road segment.
    The stop type or structured transport metadata must identify the terminal.
    """
    if not isinstance(stop, dict):
        return False
    stop_type = str(stop.get("type") or "").casefold()
    if stop_type in _FERRY_STOP_TYPES:
        return True
    transport = _stop_transport(stop)
    return (
        str(transport.get("mode") or "").casefold() == "ferry"
        or str(transport.get("ferry_role") or "").casefold() in {"departure", "arrival"}
    )


def _ferry_role(stop: Any) -> str:
    transport = _stop_transport(stop)
    role = str(transport.get("ferry_role") or "").casefold().strip()
    if role in {"departure", "arrival"}:
        return role
    text = " ".join(
        str(value or "")
        for value in (stop.get("name"), stop.get("notes"))
    ).casefold() if isinstance(stop, dict) else ""
    if any(token in text for token in ("ankunft", "arrival", "ankunftsterminal")):
        return "arrival"
    if any(token in text for token in ("abfahrt", "departure", "abfahrtsterminal", "check-in")):
        return "departure"
    return ""


def _routing_leg_mode(source: Any, target: Any) -> tuple[str, str | None]:
    source_transport = _stop_transport(source)
    target_transport = _stop_transport(target)
    explicit = str(source_transport.get("mode_to_next") or "").casefold().strip()
    if explicit in _TRANSPORT_MODES:
        return explicit, None
    explicit_from = str(target_transport.get("mode_from_previous") or "").casefold().strip()
    if explicit_from in _TRANSPORT_MODES:
        return explicit_from, None

    source_ferry = _is_ferry_stop(source)
    target_ferry = _is_ferry_stop(target)
    if source_ferry and target_ferry:
        return "ferry", None
    if source_ferry:
        if _ferry_role(source) == "arrival":
            return "driving", None
        return (
            "break",
            "Fährabfahrt erkannt, aber ein eigener Ankunftsterminal-Stopp mit GPS fehlt.",
        )
    if target_ferry:
        # A road leg ending at a departure terminal is valid. If the target was
        # explicitly marked as an arrival terminal, the departure is missing.
        if _ferry_role(target) == "arrival":
            return (
                "break",
                "Fährankunft erkannt, aber ein eigener Abfahrtsterminal-Stopp mit GPS fehlt.",
            )
        return "driving", None
    return "driving", None


def _same_stop_place(first: Any, second: Any) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    if first.get("id") and first.get("id") == second.get("id"):
        return True
    first_coordinate = _stop_coordinate(first)
    second_coordinate = _stop_coordinate(second)
    if first_coordinate and second_coordinate:
        return (
            abs(first_coordinate[0] - second_coordinate[0]) < 0.00005
            and abs(first_coordinate[1] - second_coordinate[1]) < 0.00005
        )
    first_name = str(first.get("name") or "").strip().casefold()
    second_name = str(second.get("name") or "").strip().casefold()
    return bool(first_name and first_name == second_name)


def _effective_routing_stops(
    ordered: list[dict[str, Any]],
    index: int,
) -> list[dict[str, Any]]:
    """Return route stop wrappers including an inherited prior overnight stop."""
    document = ordered[index]
    day_id = document["day"]["id"]
    result = [
        {
            "stop": stop,
            "inherited": False,
            "source_day_id": day_id,
        }
        for stop in document["stops"]
    ]
    if index <= 0:
        return result
    previous = ordered[index - 1]
    overnight = previous["stops"][-1] if previous["stops"] else None
    if not _is_overnight_stop(overnight):
        return result
    if result and _same_stop_place(overnight, result[0]["stop"]):
        return result
    return [
        {
            "stop": overnight,
            "inherited": True,
            "source_day_id": previous["day"]["id"],
        },
        *result,
    ]


def _routing_summary(document: dict[str, Any]) -> dict[str, Any] | None:
    details = document["day"].get("details")
    if not isinstance(details, dict):
        return None
    routing = details.get(_ROUTING_DETAIL_KEY)
    if not isinstance(routing, dict):
        return None
    result: dict[str, Any] = {}
    for key in (
        "schema_version",
        "status",
        "provider",
        "road_provider",
        "profile",
        "endpoint_host",
        "calculated_at",
        "invalidated_at",
        "invalidated_reason",
        "input_hash",
        "point_count",
        "missing_stop_count",
        "distance_m",
        "duration_s",
        "ferry_distance_m",
        "ferry_duration_s",
        "total_movement_m",
        "gap_count",
        "ferry_segment_count",
        "managed_metrics",
    ):
        if key in routing:
            result[key] = deepcopy(routing[key])
    geometry = routing.get("geometry")
    if (
        isinstance(geometry, dict)
        and geometry.get("type") == "LineString"
        and isinstance(geometry.get("coordinates"), list)
        and len(geometry["coordinates"]) <= 5_000
    ):
        result["geometry"] = deepcopy(geometry)
    if isinstance(routing.get("legs"), list):
        result["legs"] = _bounded_json_value(
            routing["legs"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("missing_stops"), list):
        result["missing_stops"] = _bounded_json_value(
            routing["missing_stops"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("stop_refs"), list):
        result["stop_refs"] = _bounded_json_value(
            routing["stop_refs"],
            max_items=500,
            max_string=500,
        )
    if isinstance(routing.get("segments"), list):
        result["segments"] = _bounded_json_value(
            routing["segments"],
            max_items=200,
            max_string=500,
        )
    if isinstance(routing.get("warnings"), list):
        result["warnings"] = _bounded_json_value(
            routing["warnings"],
            max_items=100,
            max_string=500,
        )
    return result


def _invalidate_day_routing(document: dict[str, Any], reason: str) -> bool:
    """Mark one stored route stale and clear only provider-managed metrics."""
    day = document["day"]
    details = day.get("details")
    if not isinstance(details, dict):
        return False
    routing = details.get(_ROUTING_DETAIL_KEY)
    if not isinstance(routing, dict):
        return False
    changed = False
    now = utc_now_iso()
    if routing.get("managed_metrics"):
        if day.get("distance_km") is not None:
            routing.setdefault("previous_distance_km", day.get("distance_km"))
            day["distance_km"] = None
            changed = True
        if day.get("drive_minutes") is not None:
            routing.setdefault("previous_drive_minutes", day.get("drive_minutes"))
            day["drive_minutes"] = None
            changed = True
    desired_status = "stale" if routing.get("managed_metrics") else "manual_override"
    for key, value in (
        ("status", desired_status),
        ("invalidated_at", now),
        ("invalidated_reason", str(reason)[:500]),
        ("geometry_stale", True),
    ):
        if routing.get(key) != value:
            routing[key] = value
            changed = True
    if changed:
        details[_ROUTING_DETAIL_KEY] = routing
        day["details"] = details
        day["updated_at"] = now
    return changed


def _mark_manual_route_metrics(document: dict[str, Any]) -> None:
    """Document that manually edited metrics override prior provider results."""
    day = document["day"]
    details = day.get("details")
    if not isinstance(details, dict):
        details = {}
    previous = details.get(_ROUTING_DETAIL_KEY)
    routing = deepcopy(previous) if isinstance(previous, dict) else {"schema_version": 1}
    routing.update(
        {
            "status": "manual_override",
            "managed_metrics": False,
            "manual_override_at": utc_now_iso(),
            "distance_km": day.get("distance_km"),
            "drive_minutes": day.get("drive_minutes"),
            "geometry_stale": True,
        }
    )
    details[_ROUTING_DETAIL_KEY] = routing
    day["details"] = details


def _invalidate_all_routing(state: "TripState", reason: str) -> None:
    for document in state.ordered_days():
        _invalidate_day_routing(document, reason)


def _invalidate_day_and_next(
    state: "TripState",
    day_id: str,
    reason: str,
) -> None:
    refs = state.trip_document["days"]
    index = next((i for i, ref in enumerate(refs) if ref["id"] == day_id), None)
    if index is None:
        return
    _invalidate_day_routing(state.day_documents[day_id], reason)
    if index + 1 < len(refs):
        _invalidate_day_routing(
            state.day_documents[refs[index + 1]["id"]],
            f"previous_day_{reason}",
        )


def _route_stop_signature(document: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [
        (
            stop.get("id"),
            str(stop.get("type") or "").casefold(),
            _stop_coordinate(stop),
            str(_stop_transport(stop).get("mode_to_next") or ""),
            str(_stop_transport(stop).get("mode_from_previous") or ""),
            str(_stop_transport(stop).get("ferry_role") or ""),
        )
        for stop in document["stops"]
    ]


def _reconcile_routing_after_change(
    previous: "TripState",
    candidate: "TripState",
    operation: str,
) -> None:
    """Invalidate derived routes for all canonical mutation paths.

    Detection is deliberately separated from mutation. Invalidating one day can
    clear provider-managed metrics on the following day; a single-pass loop
    would then mistake those derived changes for a user-entered manual override.
    """
    if operation == "calculate_routes":
        return
    previous_ids = [ref["id"] for ref in previous.trip_document["days"]]
    candidate_ids = [ref["id"] for ref in candidate.trip_document["days"]]
    if previous_ids != candidate_ids:
        _invalidate_all_routing(candidate, "day_structure_changed")
        return

    manual_metric_day_ids: list[str] = []
    route_changed_day_ids: list[str] = []
    for day_id in candidate_ids:
        old_document = previous.day_documents[day_id]
        new_document = candidate.day_documents[day_id]
        old_day = old_document["day"]
        new_day = new_document["day"]
        if (
            old_day.get("distance_km") != new_day.get("distance_km")
            or old_day.get("drive_minutes") != new_day.get("drive_minutes")
        ):
            manual_metric_day_ids.append(day_id)
        if _route_stop_signature(old_document) != _route_stop_signature(new_document):
            route_changed_day_ids.append(day_id)

    for day_id in manual_metric_day_ids:
        _mark_manual_route_metrics(candidate.day_documents[day_id])
    for day_id in route_changed_day_ids:
        _invalidate_day_and_next(candidate, day_id, "route_stops_changed")


def _trip_route_metrics(state: "TripState") -> dict[str, Any]:
    ordered = state.ordered_days()
    total_distance = 0.0
    total_minutes = 0
    total_ferry_distance = 0.0
    total_ferry_minutes = 0
    ferry_segment_count = 0
    routing_gap_count = 0
    days_with_distance = 0
    days_with_duration = 0
    calculated_days = 0
    partial_days = 0
    stale_days = 0
    manual_days = 0
    candidate_days = 0
    missing_coordinate_days: list[str] = []
    unrouted_day_ids: list[str] = []
    for index, document in enumerate(ordered):
        day = document["day"]
        distance = day.get("distance_km")
        duration = day.get("drive_minutes")
        if isinstance(distance, (int, float)) and not isinstance(distance, bool):
            total_distance += float(distance)
            days_with_distance += 1
        if isinstance(duration, int) and not isinstance(duration, bool):
            total_minutes += int(duration)
            days_with_duration += 1
        routing = _routing_summary(document) or {}
        ferry_distance_m = routing.get("ferry_distance_m")
        ferry_duration_s = routing.get("ferry_duration_s")
        if isinstance(ferry_distance_m, (int, float)) and not isinstance(ferry_distance_m, bool):
            total_ferry_distance += float(ferry_distance_m) / 1000.0
        if isinstance(ferry_duration_s, (int, float)) and not isinstance(ferry_duration_s, bool):
            total_ferry_minutes += max(0, int(round(float(ferry_duration_s) / 60.0)))
        ferry_segment_count += int(routing.get("ferry_segment_count") or 0)
        routing_gap_count += int(routing.get("gap_count") or 0)
        status = str(routing.get("status") or "")
        if status == "calculated":
            calculated_days += 1
        elif status == "partial":
            partial_days += 1
        elif status == "stale":
            stale_days += 1
        elif status == "manual_override":
            manual_days += 1
        effective = _effective_routing_stops(ordered, index)
        if len(effective) >= 2:
            candidate_days += 1
            coordinate_count = sum(
                1 for item in effective if _stop_coordinate(item["stop"])
            )
            if coordinate_count < 2:
                missing_coordinate_days.append(day["id"])
            if distance is None or duration is None:
                unrouted_day_ids.append(day["id"])
    if candidate_days == 0:
        status = "not_required"
    elif not days_with_distance and not days_with_duration:
        status = "not_calculated"
    elif (
        not unrouted_day_ids
        and not stale_days
        and not partial_days
        and not missing_coordinate_days
    ):
        status = "complete"
    else:
        status = "partial"
    return {
        "status": status,
        "total_distance_km": round(total_distance, 1) if days_with_distance else None,
        "total_drive_minutes": total_minutes if days_with_duration else None,
        "total_ferry_distance_km": round(total_ferry_distance, 1) if total_ferry_distance else None,
        "total_ferry_minutes": total_ferry_minutes if total_ferry_minutes else None,
        "total_movement_km": round(total_distance + total_ferry_distance, 1) if (days_with_distance or total_ferry_distance) else None,
        "ferry_segment_count": ferry_segment_count,
        "routing_gap_count": routing_gap_count,
        "day_count": len(ordered),
        "route_candidate_day_count": candidate_days,
        "days_with_distance": days_with_distance,
        "days_with_drive_time": days_with_duration,
        "calculated_day_count": calculated_days,
        "partial_day_count": partial_days,
        "stale_day_count": stale_days,
        "manual_day_count": manual_days,
        "unrouted_day_count": len(unrouted_day_ids),
        "unrouted_day_ids": unrouted_day_ids[:180],
        "missing_coordinate_day_count": len(missing_coordinate_days),
        "missing_coordinate_day_ids": missing_coordinate_days[:180],
    }

def _compact_trip(
    trip: dict[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    result = {
        "id": trip["id"],
        "title": trip["title"],
        "status": trip["status"],
        "start_date": trip.get("start_date"),
        "end_date": trip.get("end_date"),
        "travelers": _bounded_json_value(
            trip.get("travelers", []),
            max_items=50,
            max_string=1_000,
        ),
        "vehicle": _bounded_json_value(
            trip.get("vehicle", {}),
            max_items=50,
            max_string=1_000,
        ),
        "preferences": _bounded_json_value(
            trip.get("preferences", {}),
            max_items=100,
            max_string=1_000,
        ),
        "notes": _bounded_json_value(trip.get("notes", ""), max_string=4_000),
    }
    details = trip.get("details", {})
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _compact_day(
    document: dict[str, Any],
    *,
    sequence: int,
    include_details: bool,
) -> dict[str, Any]:
    raw = document["day"]
    result = {
        "id": raw["id"],
        "sequence": sequence,
        "date": raw.get("date"),
        "title": raw["title"],
        "start": raw.get("start"),
        "end": raw.get("end"),
        "distance_km": raw.get("distance_km"),
        "drive_minutes": raw.get("drive_minutes"),
        "status": raw.get("status"),
        "notes": _bounded_json_value(raw.get("notes", ""), max_string=4_000),
        "stop_count": len(document["stops"]),
        "routing": _routing_summary(document),
    }
    details = raw.get("details", {})
    if isinstance(details, dict):
        planning_preferences = details.get("planning_preferences")
        if isinstance(planning_preferences, list) and planning_preferences:
            result["planning_preferences"] = _bounded_json_value(
                planning_preferences,
                max_items=20,
                max_string=2_000,
            )
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _compact_stop(
    stop: dict[str, Any],
    *,
    include_details: bool,
) -> dict[str, Any]:
    result = {
        "id": stop["id"],
        "name": stop["name"],
        "type": stop["type"],
        "arrival_time": stop.get("arrival_time"),
        "departure_time": stop.get("departure_time"),
        "location": _bounded_json_value(
            stop.get("location", {}),
            max_items=50,
            max_string=2_000,
        ),
        "notes": _bounded_json_value(stop.get("notes", ""), max_string=4_000),
    }
    details = stop.get("details", {})
    if include_details:
        result["details"] = _bounded_json_value(
            details,
            max_items=100,
            max_string=4_000,
        )
    elif isinstance(details, dict) and details:
        result["detail_sections"] = sorted(str(key) for key in details)[:100]
    return result


def _media_from_details(details: Any) -> dict[str, str] | None:
    """Extract the optional media object used by the frontend."""
    if not isinstance(details, dict):
        return None
    media = details.get("media", details)
    if not isinstance(media, dict):
        return None
    image_url = media.get("image_url") or media.get("url")
    if not isinstance(image_url, str) or not image_url.strip():
        return None
    result = {"image_url": image_url.strip()}
    for target, source in (
        ("alt", "alt"),
        ("attribution", "attribution"),
        ("source_url", "source_url"),
        ("provider", "provider"),
    ):
        value = media.get(source)
        if isinstance(value, str) and value.strip():
            result[target] = value.strip()[:1_000]
    return result


def _first_trip_media(state: "TripState") -> dict[str, str] | None:
    """Return a cover image from trip, day, or stop details."""
    media = _media_from_details(state.trip_document["trip"].get("details"))
    if media is not None:
        return media
    for document in state.ordered_days():
        media = _media_from_details(document["day"].get("details"))
        if media is not None:
            return media
        for stop in document["stops"]:
            media = _media_from_details(stop.get("details"))
            if media is not None:
                return media
    return None


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
                document["stops"],
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



@dataclass(slots=True)
class RoadplannerStore:
    """Synchronous repository for split Roadplanner JSON documents."""

    roadbook_dir: Path
    backup_dir: Path
    handoff_dir: Path
    backup_count: int = 20

    @property
    def pointer_path(self) -> Path:
        return self.roadbook_dir / "active_trip.json"

    @property
    def trips_dir(self) -> Path:
        return self.roadbook_dir / "trips"

    def initialize(self, *, create_if_missing: bool = True) -> dict[str, Any]:
        """Initialize canonical split files without changing the active pointer."""
        self.roadbook_dir.mkdir(parents=True, exist_ok=True)
        self.trips_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.handoff_dir.mkdir(parents=True, exist_ok=True)

        if not self.pointer_path.exists():
            if not create_if_missing:
                raise TripNotFoundError(
                    f"Bereits initialisierter Roadplanner-Zeiger fehlt: {self.pointer_path}"
                )
            now = utc_now_iso()
            _write_json_atomic(
                self.pointer_path,
                {
                    "schema_version": POINTER_SCHEMA_VERSION,
                    "active_trip": "new-trip",
                    "last_opened": now,
                },
            )

        pointer = self._load_pointer()
        trip_id = pointer["active_trip"]
        trip_dir = self._trip_dir(trip_id)
        days_dir = trip_dir / "days"
        trip_dir.mkdir(parents=True, exist_ok=True)
        days_dir.mkdir(parents=True, exist_ok=True)
        self._recover_transaction(trip_id)

        trip_path = trip_dir / "trip.json"
        if not trip_path.exists():
            now = utc_now_iso()
            day_documents: dict[str, dict[str, Any]] = {}
            refs: list[dict[str, str]] = []
            for path in sorted(days_dir.glob("*.json")):
                fallback_id = validate_identifier(path.stem, "day filename")
                document = normalize_day_document(
                    _read_json(path),
                    fallback_id=fallback_id,
                    fallback_timestamp=now,
                )
                day_id = document["day"]["id"]
                if day_id in day_documents:
                    raise ValidationError(f"Doppelte Tages-ID beim Import: {day_id}")
                day_documents[day_id] = document
                refs.append({"id": day_id, "file": f"days/{path.name}"})
            refs.sort(
                key=lambda ref: (
                    day_documents[ref["id"]]["day"].get("date") or "9999-12-31",
                    ref["file"],
                )
            )
            trip_document = self._default_trip_document(trip_id, refs, now)
            state = TripState(pointer, trip_document, day_documents, [])
            state.trip_document["metadata"]["content_hash"] = state.content_hash()
            if any(days_dir.glob("*.json")):
                self._create_snapshot(trip_id, "initial-migration")
            _write_json_atomic(trip_path, state.trip_document)

        raw_trip = _read_json(trip_path)
        raw_trip_schema = raw_trip.get("schema_version", 1)
        legacy_trip_layout = (
            not isinstance(raw_trip.get("trip"), dict)
            or not isinstance(raw_trip_schema, int)
            or isinstance(raw_trip_schema, bool)
            or raw_trip_schema < TRIP_SCHEMA_VERSION
        )
        state = self._load_state(trip_id=trip_id, validate_hash=False)
        if legacy_trip_layout and state.unmanaged_day_files:
            self._index_legacy_unmanaged_days(state)
        normalized_hash = state.content_hash()
        stored_hash = state.trip_document["metadata"].get("content_hash")
        needs_migration = (
            raw_trip != state.trip_document
            or stored_hash != normalized_hash
            or any(
                _read_json(self._day_path(trip_id, ref["file"]))
                != state.day_documents[ref["id"]]
                for ref in state.trip_document["days"]
            )
        )
        if needs_migration:
            snapshot = self._create_snapshot(trip_id, "schema-migration")
            previous_revision = state.revision
            now = utc_now_iso()
            state.trip_document["metadata"].update(
                {
                    "revision": max(previous_revision, 0) + 1,
                    "updated_at": now,
                    "updated_by": "migration",
                    "last_operation": "schema_migration",
                }
            )
            state.trip_document["metadata"]["content_hash"] = state.content_hash()
            self._write_state_transaction(
                state,
                snapshot=snapshot,
                operation="schema_migration",
                removed_files=[],
            )
        else:
            self._assert_content_hash(state)

        final_state = self._load_state(trip_id=trip_id, validate_hash=True)
        self._write_context_best_effort(final_state)
        return final_state.coordinator_payload()

    def _index_legacy_unmanaged_days(self, state: TripState) -> None:
        """Index legacy day files that were not referenced by an old trip file."""
        existing_day_ids = set(state.day_documents)
        existing_stop_ids = {
            stop["id"]
            for document in state.day_documents.values()
            for stop in document["stops"]
        }
        discovered: list[tuple[str, dict[str, Any]]] = []
        for relative_file in state.unmanaged_day_files:
            path = self._day_path(state.trip_id, relative_file)
            fallback_id = validate_identifier(path.stem, "day filename")
            document = normalize_day_document(
                _read_json(path),
                fallback_id=fallback_id,
                fallback_timestamp=state.trip_document["metadata"]["created_at"],
            )
            day_id = document["day"]["id"]
            if day_id in existing_day_ids:
                raise ValidationError(
                    f"Doppelte Tages-ID beim Legacy-Import: {day_id}"
                )
            for stop in document["stops"]:
                if stop["id"] in existing_stop_ids:
                    raise ValidationError(
                        f"Doppelte Stopp-ID beim Legacy-Import: {stop['id']}"
                    )
                existing_stop_ids.add(stop["id"])
            existing_day_ids.add(day_id)
            discovered.append((relative_file, document))

        discovered.sort(
            key=lambda item: (
                item[1]["day"].get("date") or "9999-12-31",
                item[0],
            )
        )
        for relative_file, document in discovered:
            day_id = document["day"]["id"]
            state.trip_document["days"].append(
                {"id": day_id, "file": relative_file}
            )
            state.day_documents[day_id] = document
        state.unmanaged_day_files = []

    def _default_trip_document(
        self,
        trip_id: str,
        refs: list[dict[str, str]],
        now: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": TRIP_SCHEMA_VERSION,
            "trip": {
                "id": trip_id,
                "title": trip_id.replace("-", " ").replace("_", " ").title(),
                "status": "planning",
                "start_date": None,
                "end_date": None,
                "travelers": [],
                "vehicle": {},
                "preferences": {},
                "notes": "",
                "details": {},
            },
            "days": refs,
            "metadata": {
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "updated_by": "initialization",
                "last_operation": "initialization",
            },
        }

    def _load_pointer(self) -> dict[str, Any]:
        raw = _read_json(self.pointer_path)
        raw_schema = raw.get("schema_version", 1)
        if (
            isinstance(raw_schema, bool)
            or not isinstance(raw_schema, int)
            or raw_schema < 1
            or raw_schema > POINTER_SCHEMA_VERSION
        ):
            raise ValidationError("Nicht unterstützte schema_version im Reisezeiger")
        active_trip = validate_identifier(raw.get("active_trip"), "active_trip")
        result = deepcopy(raw)
        result["schema_version"] = POINTER_SCHEMA_VERSION
        result["active_trip"] = active_trip
        if "last_opened" in result:
            result["last_opened"] = _ensure_string(
                result["last_opened"],
                "last_opened",
                allow_empty=False,
                max_length=100,
            )
        return result

    def _trip_dir(self, trip_id: str) -> Path:
        validate_identifier(trip_id, "trip_id")
        return self.trips_dir / trip_id

    def _day_path(self, trip_id: str, relative_file: str) -> Path:
        safe_file = _safe_day_file(relative_file, "day.file")
        trip_dir = self._trip_dir(trip_id).resolve(strict=False)
        path = (trip_dir / PurePosixPath(safe_file)).resolve(strict=False)
        try:
            path.relative_to(trip_dir)
        except ValueError as err:
            raise ValidationError("Tagespfad verlässt den Reiseordner") from err
        return path

    def _load_state(
        self,
        *,
        trip_id: str | None = None,
        validate_hash: bool = True,
        recover: bool = True,
    ) -> TripState:
        pointer = self._load_pointer()
        selected_trip = trip_id or pointer["active_trip"]
        selected_trip = validate_identifier(selected_trip, "trip_id")
        if recover:
            self._recover_transaction(selected_trip)
        trip_path = self._trip_dir(selected_trip) / "trip.json"
        if not trip_path.exists():
            raise TripNotFoundError(f"trip.json fehlt für Reise '{selected_trip}'")
        raw_trip = _read_json(trip_path)
        fallback_timestamp = utc_now_iso()
        trip_document = normalize_trip_document(
            raw_trip,
            expected_trip_id=selected_trip,
            fallback_timestamp=fallback_timestamp,
        )
        day_documents: dict[str, dict[str, Any]] = {}
        referenced_files: set[str] = set()
        seen_trip_stop_ids: set[str] = set()
        for ref in trip_document["days"]:
            path = self._day_path(selected_trip, ref["file"])
            if not path.exists():
                raise TripNotFoundError(
                    f"Tagesdatei für '{ref['id']}' fehlt: {path}"
                )
            document = normalize_day_document(
                _read_json(path),
                fallback_id=ref["id"],
                fallback_timestamp=trip_document["metadata"]["created_at"],
            )
            if document["day"]["id"] != ref["id"]:
                raise ValidationError(
                    f"Tages-ID in {ref['file']} passt nicht zum Index: "
                    f"{document['day']['id']} != {ref['id']}"
                )
            for stop in document["stops"]:
                if stop["id"] in seen_trip_stop_ids:
                    raise ValidationError(
                        f"Doppelte Stopp-ID in Reise {selected_trip}: {stop['id']}"
                    )
                seen_trip_stop_ids.add(stop["id"])
            day_documents[ref["id"]] = document
            referenced_files.add(ref["file"])

        days_dir = self._trip_dir(selected_trip) / "days"
        unmanaged = sorted(
            f"days/{path.name}"
            for path in days_dir.glob("*.json")
            if f"days/{path.name}" not in referenced_files
        )
        state = TripState(pointer, trip_document, day_documents, unmanaged)
        if validate_hash:
            self._assert_content_hash(state)
        return state

    def _assert_content_hash(self, state: TripState) -> None:
        stored = state.trip_document["metadata"].get("content_hash")
        actual = state.content_hash()
        if stored != actual:
            raise ConcurrentModificationError()

    def load_trip(self) -> dict[str, Any]:
        """Return the complete active trip without modifying any file."""
        return self._load_state().combined_export()

    def load_coordinator_payload(self) -> dict[str, Any]:
        """Return bounded entity data without side effects."""
        return self._load_state().coordinator_payload()

    def get_trip_summary(
        self,
        *,
        trip_id: str | None = None,
        today: date | None = None,
    ) -> dict[str, Any]:
        state = self._load_state(trip_id=trip_id)
        ordered = state.ordered_days()
        current_date = today or date.today()
        next_day_document = next(
            (
                document
                for document in ordered
                if document["day"].get("date")
                and date.fromisoformat(document["day"]["date"]) >= current_date
            ),
            None,
        )
        if next_day_document is None:
            next_day_document = next(
                (
                    document
                    for document in ordered
                    if not document["day"].get("date")
                ),
                None,
            )
        summary_days = [
            _compact_day(
                document,
                sequence=sequence,
                include_details=False,
            )
            for sequence, document in enumerate(
                ordered[:MAX_SUMMARY_DAYS],
                start=1,
            )
        ]
        stop_count = sum(len(document["stops"]) for document in ordered)
        route_metrics = _trip_route_metrics(state)
        trip = _compact_trip(
            state.trip_document["trip"],
            include_details=True,
        )
        next_day = None
        if next_day_document is not None:
            sequence = ordered.index(next_day_document) + 1
            next_day = _compact_day(
                next_day_document,
                sequence=sequence,
                include_details=False,
            )
        return {
            "trip": trip,
            "revision": state.revision,
            "day_count": len(ordered),
            "stop_count": stop_count,
            "total_distance_km": route_metrics["total_distance_km"],
            "total_drive_minutes": route_metrics["total_drive_minutes"],
            "route_metrics": route_metrics,
            "next_day": next_day,
            "days": summary_days,
            "days_truncated": len(ordered) > MAX_SUMMARY_DAYS,
            "pending_unmanaged_day_files": state.unmanaged_day_files[:100],
            "summary": (
                f"{trip['title']}: {len(ordered)} Reisetage, "
                f"{stop_count} Stopps, Revision {state.revision}."
            ),
        }

    def get_days(
        self,
        *,
        trip_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
        include_stops: bool = False,
    ) -> dict[str, Any]:
        state = self._load_state(trip_id=trip_id)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValidationError("'offset' muss eine nicht-negative Ganzzahl sein")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 60
        ):
            raise ValidationError("'limit' muss zwischen 1 und 60 liegen")
        ordered = state.ordered_days()
        selected = ordered[offset : offset + limit]
        result_days: list[dict[str, Any]] = []
        for sequence, document in enumerate(selected, start=offset + 1):
            day = _compact_day(
                document,
                sequence=sequence,
                include_details=True,
            )
            if include_stops:
                day["stops"] = [
                    _compact_stop(stop, include_details=True)
                    for stop in document["stops"][:MAX_SUMMARY_STOPS]
                ]
                day["stops_truncated"] = (
                    len(document["stops"]) > MAX_SUMMARY_STOPS
                )
            result_days.append(day)
        return {
            "revision": state.revision,
            "offset": offset,
            "limit": limit,
            "total": len(ordered),
            "days": result_days,
            "has_more": offset + len(selected) < len(ordered),
        }

    def get_day(
        self,
        *,
        day_id: str,
        trip_id: str | None = None,
        stop_offset: int = 0,
        stop_limit: int = 50,
    ) -> dict[str, Any]:
        state = self._load_state(trip_id=trip_id)
        day_id = validate_identifier(day_id, "day_id")
        document = state.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if (
            isinstance(stop_offset, bool)
            or not isinstance(stop_offset, int)
            or stop_offset < 0
        ):
            raise ValidationError("'stop_offset' muss nicht-negativ sein")
        if (
            isinstance(stop_limit, bool)
            or not isinstance(stop_limit, int)
            or not 1 <= stop_limit <= 100
        ):
            raise ValidationError("'stop_limit' muss zwischen 1 und 100 liegen")
        stops = document["stops"]
        selected = stops[stop_offset : stop_offset + stop_limit]
        sequence = next(
            index
            for index, ref in enumerate(state.trip_document["days"], start=1)
            if ref["id"] == day_id
        )
        return {
            "revision": state.revision,
            "day": _compact_day(
                document,
                sequence=sequence,
                include_details=True,
            ),
            "stops": [
                _compact_stop(stop, include_details=True)
                for stop in selected
            ],
            "stop_offset": stop_offset,
            "stop_limit": stop_limit,
            "stop_total": len(stops),
            "has_more_stops": stop_offset + len(selected) < len(stops),
        }

    def search_stops(
        self,
        *,
        query: str | None = None,
        trip_id: str | None = None,
        stop_type: str | None = None,
        day_id: str | None = None,
        day_date: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        state = self._load_state(trip_id=trip_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_SEARCH_RESULTS
        ):
            raise ValidationError(
                f"'limit' muss zwischen 1 und {MAX_SEARCH_RESULTS} liegen"
            )
        query_text = (query or "").casefold().strip()
        if day_id is not None:
            day_id = validate_identifier(day_id, "day_id")
        normalized_date = _ensure_optional_date(day_date, "day_date")
        results: list[dict[str, Any]] = []
        total_matches = 0
        for day_sequence, document in enumerate(state.ordered_days(), start=1):
            day = document["day"]
            if day_id is not None and day["id"] != day_id:
                continue
            if normalized_date is not None and day.get("date") != normalized_date:
                continue
            for stop_sequence, stop in enumerate(document["stops"], start=1):
                if stop_type is not None and stop["type"] != stop_type:
                    continue
                searchable = json.dumps(
                    {
                        "name": stop["name"],
                        "notes": stop["notes"],
                        "location": stop["location"],
                        "details": stop["details"],
                    },
                    ensure_ascii=False,
                    default=str,
                ).casefold()
                if query_text and query_text not in searchable:
                    continue
                total_matches += 1
                if len(results) < limit:
                    result = _compact_stop(stop, include_details=True)
                    result.update(
                        {
                            "day_id": day["id"],
                            "day_date": day.get("date"),
                            "day_title": day["title"],
                            "day_sequence": day_sequence,
                            "stop_sequence": stop_sequence,
                        }
                    )
                    results.append(result)
        return {
            "revision": state.revision,
            "count": len(results),
            "total_matches": total_matches,
            "truncated": total_matches > len(results),
            "stops": results,
        }

    def list_trips(self) -> dict[str, Any]:
        """List all valid trip folders with bounded card metadata."""
        pointer = self._load_pointer()
        trips: list[dict[str, Any]] = []
        if not self.trips_dir.exists():
            return {"active_trip": pointer["active_trip"], "trips": []}
        for path in sorted(self.trips_dir.iterdir()):
            if not path.is_dir() or not _ID_PATTERN.fullmatch(path.name):
                continue
            trip_path = path / "trip.json"
            if not trip_path.exists():
                continue
            try:
                state = self._load_state(
                    trip_id=path.name,
                    validate_hash=True,
                )
            except RoadplannerError as err:
                trips.append(
                    {
                        "id": path.name,
                        "valid": False,
                        "active": path.name == pointer["active_trip"],
                        "error": str(err)[:500],
                    }
                )
                continue
            ordered = state.ordered_days()
            stop_count = sum(len(document["stops"]) for document in ordered)
            route_metrics = _trip_route_metrics(state)
            trip = state.trip_document["trip"]
            cover_image = _first_trip_media(state)
            trips.append(
                {
                    "id": path.name,
                    "title": trip["title"],
                    "status": trip.get("status"),
                    "start_date": trip.get("start_date"),
                    "end_date": trip.get("end_date"),
                    "revision": state.revision,
                    "day_count": len(ordered),
                    "stop_count": stop_count,
                    "total_distance_km": route_metrics["total_distance_km"],
                    "total_drive_minutes": route_metrics["total_drive_minutes"],
                    "route_metrics": route_metrics,
                    "cover_image": cover_image,
                    "active": path.name == pointer["active_trip"],
                    "valid": True,
                }
            )
        return {"active_trip": pointer["active_trip"], "trips": trips}


    def get_routing_plan(
        self,
        *,
        trip_id: str | None = None,
        day_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a revision-consistent, provider-neutral routing work plan.

        Route metrics are canonical writes and therefore follow the same
        active-trip rule as all other mutations. Reject a non-active trip
        before any external routing requests are started.
        """
        pointer = self._load_pointer()
        selected_trip_id = trip_id or pointer["active_trip"]
        if selected_trip_id != pointer["active_trip"]:
            raise ValidationError(
                "Straßenrouten können nur für die aktive Reise berechnet werden"
            )
        state = self._load_state(trip_id=selected_trip_id)
        requested: set[str] | None = None
        if day_ids is not None:
            requested = {
                validate_identifier(day_id, "day_id")
                for day_id in day_ids
            }
            missing = requested - set(state.day_documents)
            if missing:
                raise TripNotFoundError(
                    "Reisetag nicht gefunden: " + ", ".join(sorted(missing))
                )
        ordered = state.ordered_days()
        days: list[dict[str, Any]] = []
        for index, document in enumerate(ordered):
            day_id = document["day"]["id"]
            if requested is not None and day_id not in requested:
                continue
            effective = _effective_routing_stops(ordered, index)
            points: list[dict[str, Any]] = []
            missing_stops: list[dict[str, Any]] = []
            effective_entries: list[dict[str, Any]] = []
            for effective_index, item in enumerate(effective):
                stop = item["stop"]
                coordinate = _stop_coordinate(stop)
                reference = {
                    "day_id": day_id,
                    "source_day_id": item["source_day_id"],
                    "stop_id": stop.get("id"),
                    "name": stop.get("name"),
                    "type": stop.get("type"),
                    "inherited": bool(item["inherited"]),
                }
                effective_entries.append(
                    {
                        "effective_index": effective_index,
                        "stop": stop,
                        "coordinate": coordinate,
                        "reference": reference,
                    }
                )
                if coordinate is None:
                    missing_stops.append(reference)
                    continue
                points.append(
                    {
                        **reference,
                        "latitude": coordinate[0],
                        "longitude": coordinate[1],
                        "_effective_index": effective_index,
                    }
                )

            route_warnings: list[str] = []
            for point_index in range(max(0, len(points) - 1)):
                source_point = points[point_index]
                target_point = points[point_index + 1]
                source_index = int(source_point["_effective_index"])
                target_index = int(target_point["_effective_index"])
                if target_index != source_index + 1:
                    mode = "break"
                    reason = "Mindestens ein dazwischenliegender Stopp besitzt noch keine GPS-Daten."
                else:
                    source_stop = effective_entries[source_index]["stop"]
                    target_stop = effective_entries[target_index]["stop"]
                    mode, reason = _routing_leg_mode(source_stop, target_stop)
                source_point["mode_to_next"] = mode
                if reason:
                    source_point["mode_reason"] = reason
                    if reason not in route_warnings:
                        route_warnings.append(reason)
            for point in points:
                point.pop("_effective_index", None)

            days.append(
                {
                    "day_id": day_id,
                    "sequence": index + 1,
                    "date": document["day"].get("date"),
                    "title": document["day"].get("title"),
                    "effective_stop_count": len(effective),
                    "point_count": len(points),
                    "points": points,
                    "missing_stops": missing_stops,
                    "route_warnings": route_warnings,
                    "existing_routing": _routing_summary(document),
                }
            )
        return {
            "trip_id": state.trip_id,
            "revision": state.revision,
            "days": days,
            "route_metrics": _trip_route_metrics(state),
        }

    def apply_routing_results(
        self,
        *,
        results: list[dict[str, Any]],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one or more derived routes atomically in one revision."""
        if not isinstance(results, list) or not results:
            raise ValidationError("Es wurden keine Routenberechnungen übergeben")
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        candidate = previous.clone()
        applied: list[dict[str, Any]] = []
        seen: set[str] = set()
        now = utc_now_iso()
        for item in results:
            if not isinstance(item, dict):
                raise ValidationError("Routing-Ergebnis muss ein JSON-Objekt sein")
            day_id = validate_identifier(item.get("day_id"), "day_id")
            if day_id in seen:
                raise ValidationError(f"Doppeltes Routing-Ergebnis für {day_id}")
            seen.add(day_id)
            document = candidate.day_documents.get(day_id)
            if document is None:
                raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
            routing = item.get("routing")
            if not isinstance(routing, dict):
                raise ValidationError(f"Routing-Ergebnis für {day_id} fehlt")
            distance_m = routing.get("distance_m")
            duration_s = routing.get("duration_s")
            if (
                isinstance(distance_m, bool)
                or isinstance(duration_s, bool)
                or not isinstance(distance_m, (int, float))
                or not isinstance(duration_s, (int, float))
                or distance_m < 0
                or duration_s < 0
            ):
                raise ValidationError(f"Ungültige Routing-Metrik für {day_id}")
            missing_stops = item.get("missing_stops", [])
            if not isinstance(missing_stops, list):
                raise ValidationError("missing_stops muss eine Liste sein")
            normalized_routing = deepcopy(routing)
            requested_status = str(normalized_routing.get("status") or "calculated")
            if requested_status not in {"calculated", "partial"}:
                requested_status = "calculated"
            if missing_stops or int(normalized_routing.get("gap_count") or 0) > 0:
                requested_status = "partial"
            normalized_routing.update(
                {
                    "schema_version": max(1, int(normalized_routing.get("schema_version") or 1)),
                    "status": requested_status,
                    "missing_stop_count": len(missing_stops),
                    "missing_stops": _bounded_json_value(
                        missing_stops,
                        max_items=500,
                        max_string=500,
                    ),
                    "managed_metrics": True,
                    "geometry_stale": False,
                }
            )
            normalized_routing.pop("invalidated_at", None)
            normalized_routing.pop("invalidated_reason", None)
            details = document["day"].get("details")
            if not isinstance(details, dict):
                details = {}
            details[_ROUTING_DETAIL_KEY] = normalized_routing
            document["day"]["details"] = details
            document["day"]["distance_km"] = round(float(distance_m) / 1000.0, 1)
            document["day"]["drive_minutes"] = max(0, int(round(float(duration_s) / 60.0)))
            document["day"]["updated_at"] = now
            applied.append(
                {
                    "day_id": day_id,
                    "status": normalized_routing["status"],
                    "distance_km": document["day"]["distance_km"],
                    "drive_minutes": document["day"]["drive_minutes"],
                    "point_count": normalized_routing.get("point_count"),
                    "missing_stop_count": len(missing_stops),
                    "ferry_distance_km": round(float(normalized_routing.get("ferry_distance_m") or 0.0) / 1000.0, 1),
                    "gap_count": int(normalized_routing.get("gap_count") or 0),
                }
            )
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="calculate_routes",
            removed_files=[],
        )
        verified = self._load_state()
        result["routing_results"] = applied
        result["route_metrics"] = _trip_route_metrics(verified)
        return result

    def set_active_trip(
        self,
        *,
        trip_id: str,
        expected_active_trip: str | None = None,
    ) -> dict[str, Any]:
        trip_id = validate_identifier(trip_id, "trip_id")
        pointer = self._load_pointer()
        if (
            expected_active_trip is not None
            and pointer["active_trip"] != expected_active_trip
        ):
            raise ValidationError(
                "Die aktive Reise wurde zwischenzeitlich gewechselt: "
                f"{pointer['active_trip']}"
            )
        target = self._load_state(trip_id=trip_id, validate_hash=True)
        if pointer["active_trip"] == trip_id:
            return {
                "changed": False,
                "active_trip": trip_id,
                "trip": target.coordinator_payload(),
            }
        snapshot = self._create_snapshot(pointer["active_trip"], "before-trip-switch")
        new_pointer = deepcopy(pointer)
        new_pointer["active_trip"] = trip_id
        new_pointer["last_opened"] = utc_now_iso()
        try:
            _write_json_atomic(self.pointer_path, new_pointer)
            verified = self._load_state(trip_id=trip_id, validate_hash=True)
        except Exception:
            self._restore_snapshot(snapshot)
            raise
        self._write_context_best_effort(verified)
        return {
            "changed": True,
            "active_trip": trip_id,
            "trip": verified.coordinator_payload(),
        }

    def update_trip(
        self,
        *,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
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
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Reisefelder: " + ", ".join(sorted(unknown))
            )
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        candidate = previous.clone()
        candidate.trip_document["trip"].update(deepcopy(patch))
        candidate.trip_document = normalize_trip_document(
            candidate.trip_document,
            expected_trip_id=previous.trip_id,
            fallback_timestamp=previous.trip_document["metadata"]["created_at"],
        )
        return self._commit(
            previous,
            candidate,
            actor=actor,
            operation="update_trip",
            removed_files=[],
        )

    def add_day(
        self,
        *,
        actor: str,
        expected_revision: int,
        day_date: str | None = None,
        title: str | None = None,
        start: str = "",
        end: str = "",
        distance_km: int | float | None = None,
        drive_minutes: int | None = None,
        status: str = "planned",
        notes: str = "",
        details: dict[str, Any] | None = None,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        candidate = previous.clone()
        if len(candidate.trip_document["days"]) >= MAX_DAYS:
            raise ValidationError(f"Maximal {MAX_DAYS} Reisetage werden unterstützt")
        now = utc_now_iso()
        day_id = _new_id("day")
        raw_document = {
            "schema_version": DAY_SCHEMA_VERSION,
            "day": {
                "id": day_id,
                "date": day_date,
                "title": title or (f"Tag {day_date}" if day_date else "Neuer Reisetag"),
                "start": start,
                "end": end,
                "distance_km": distance_km,
                "drive_minutes": drive_minutes,
                "status": status,
                "notes": notes,
                "details": details or {},
                "created_at": now,
                "updated_at": now,
            },
            "stops": [],
        }
        document = normalize_day_document(
            raw_document,
            fallback_id=day_id,
            fallback_timestamp=now,
        )
        candidate.day_documents[day_id] = document
        ref = {"id": day_id, "file": f"days/{day_id}.json"}
        refs = candidate.trip_document["days"]
        insert_at = self._insert_index(position, len(refs))
        refs.insert(insert_at, ref)
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="add_day",
            removed_files=[],
        )
        result["day"] = deepcopy(document["day"])
        result["position"] = insert_at + 1
        return result

    def update_day(
        self,
        *,
        day_id: str,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
            "date",
            "title",
            "start",
            "end",
            "distance_km",
            "drive_minutes",
            "status",
            "notes",
            "details",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Tagesfelder: " + ", ".join(sorted(unknown))
            )
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        if day_id not in previous.day_documents:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        candidate = previous.clone()
        document = candidate.day_documents[day_id]
        before = _without_audit_fields(document["day"])
        document["day"].update(deepcopy(patch))
        normalized = normalize_day_document(
            document,
            fallback_id=day_id,
            fallback_timestamp=document["day"]["created_at"],
        )
        if _without_audit_fields(normalized["day"]) != before:
            normalized["day"]["updated_at"] = utc_now_iso()
        candidate.day_documents[day_id] = normalized
        if position is not None:
            refs = candidate.trip_document["days"]
            old_index = next(i for i, ref in enumerate(refs) if ref["id"] == day_id)
            ref = refs.pop(old_index)
            refs.insert(self._insert_index(position, len(refs)), ref)
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="update_day",
            removed_files=[],
        )
        result["day"] = deepcopy(normalized["day"])
        return result

    def remove_day(
        self,
        *,
        day_id: str,
        actor: str,
        expected_revision: int,
        remove_stops: bool = False,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if document["stops"] and not remove_stops:
            raise ValidationError(
                "Der Reisetag enthält Stopps. Zum Löschen 'remove_stops=true' setzen."
            )
        candidate = previous.clone()
        ref = next(
            ref for ref in candidate.trip_document["days"] if ref["id"] == day_id
        )
        candidate.trip_document["days"] = [
            item for item in candidate.trip_document["days"] if item["id"] != day_id
        ]
        removed = candidate.day_documents.pop(day_id)
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="remove_day",
            removed_files=[ref["file"]],
        )
        result["removed_day"] = deepcopy(removed["day"])
        result["removed_stop_count"] = len(removed["stops"])
        return result

    def add_stop(
        self,
        *,
        day_id: str,
        name: str,
        actor: str,
        expected_revision: int,
        stop_type: str = "waypoint",
        arrival_time: str | None = None,
        departure_time: str | None = None,
        location: dict[str, Any] | None = None,
        notes: str = "",
        details: dict[str, Any] | None = None,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        if len(document["stops"]) >= MAX_STOPS_PER_DAY:
            raise ValidationError(
                f"Ein Reisetag darf maximal {MAX_STOPS_PER_DAY} Stopps enthalten"
            )
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        now = utc_now_iso()
        stop_id = _new_id("stop")
        stop = normalize_stop(
            {
                "id": stop_id,
                "name": name,
                "type": stop_type,
                "arrival_time": arrival_time,
                "departure_time": departure_time,
                "location": location or {},
                "notes": notes,
                "details": details or {},
                "created_at": now,
                "updated_at": now,
            },
            index=len(target["stops"]),
            fallback_timestamp=now,
        )
        insert_at = self._insert_index(position, len(target["stops"]))
        target["stops"].insert(insert_at, stop)
        target["day"]["updated_at"] = now
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="add_stop",
            removed_files=[],
        )
        result["stop"] = deepcopy(stop)
        result["day_id"] = day_id
        result["position"] = insert_at + 1
        return result

    def update_stop(
        self,
        *,
        day_id: str,
        stop_id: str,
        patch: dict[str, Any],
        actor: str,
        expected_revision: int,
        position: int | None = None,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        stop_id = validate_identifier(stop_id, "stop_id")
        if not isinstance(patch, dict):
            raise ValidationError("'patch' muss ein JSON-Objekt sein")
        allowed = {
            "name",
            "type",
            "arrival_time",
            "departure_time",
            "location",
            "notes",
            "details",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Stoppfelder: " + ", ".join(sorted(unknown))
            )
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        old_index = next(
            (i for i, stop in enumerate(document["stops"]) if stop["id"] == stop_id),
            None,
        )
        if old_index is None:
            raise TripNotFoundError(f"Stopp nicht gefunden: {stop_id}")
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        raw_stop = deepcopy(target["stops"][old_index])
        before = _without_audit_fields(raw_stop)
        raw_stop.update(deepcopy(patch))
        raw_stop["id"] = stop_id
        normalized = normalize_stop(
            raw_stop,
            index=old_index,
            fallback_timestamp=raw_stop["created_at"],
        )
        changed_fields = _without_audit_fields(normalized) != before
        if changed_fields:
            normalized["updated_at"] = utc_now_iso()
        target["stops"][old_index] = normalized
        if position is not None:
            moved = target["stops"].pop(old_index)
            target["stops"].insert(
                self._insert_index(position, len(target["stops"])),
                moved,
            )
        if changed_fields or position is not None:
            target["day"]["updated_at"] = utc_now_iso()
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="update_stop",
            removed_files=[],
        )
        result["stop"] = deepcopy(normalized)
        result["day_id"] = day_id
        return result

    def remove_stop(
        self,
        *,
        day_id: str,
        stop_id: str,
        actor: str,
        expected_revision: int,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        day_id = validate_identifier(day_id, "day_id")
        stop_id = validate_identifier(stop_id, "stop_id")
        previous = self._load_state()
        self._check_expected_trip(previous, expected_trip_id)
        self._check_revision(previous, expected_revision)
        document = previous.day_documents.get(day_id)
        if document is None:
            raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
        old_index = next(
            (i for i, stop in enumerate(document["stops"]) if stop["id"] == stop_id),
            None,
        )
        if old_index is None:
            raise TripNotFoundError(f"Stopp nicht gefunden: {stop_id}")
        candidate = previous.clone()
        target = candidate.day_documents[day_id]
        removed = target["stops"].pop(old_index)
        target["day"]["updated_at"] = utc_now_iso()
        result = self._commit(
            previous,
            candidate,
            actor=actor,
            operation="remove_stop",
            removed_files=[],
        )
        result["removed_stop"] = deepcopy(removed)
        result["day_id"] = day_id
        return result

    @staticmethod
    def _check_expected_trip(
        state: TripState,
        expected_trip_id: str | None,
    ) -> None:
        if expected_trip_id is None:
            return
        expected_trip_id = validate_identifier(
            expected_trip_id,
            "expected_trip_id",
        )
        if state.trip_id != expected_trip_id:
            raise ValidationError(
                "Die ausgewählte Reise ist nicht mehr aktiv: "
                f"{expected_trip_id} != {state.trip_id}"
            )

    def _check_revision(self, state: TripState, expected_revision: int) -> None:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise ValidationError("'expected_revision' muss nicht-negativ sein")
        if expected_revision != state.revision:
            raise RevisionConflictError(expected_revision, state.revision)

    @staticmethod
    def _insert_index(position: int | None, current_length: int) -> int:
        if position is None:
            return current_length
        if isinstance(position, bool) or not isinstance(position, int) or position < 1:
            raise ValidationError("'position' muss eine positive Ganzzahl sein")
        return min(position - 1, current_length)

    def _commit(
        self,
        previous: TripState,
        candidate: TripState,
        *,
        actor: str,
        operation: str,
        removed_files: list[str],
    ) -> dict[str, Any]:
        _reconcile_routing_after_change(previous, candidate, operation)
        if previous.business_value() == candidate.business_value():
            return {
                "changed": False,
                "revision": previous.revision,
                "trip": previous.coordinator_payload(),
            }
        now = utc_now_iso()
        candidate.trip_document["metadata"].update(
            {
                "revision": previous.revision + 1,
                "updated_at": now,
                "updated_by": (actor or "unknown")[:200],
                "last_operation": operation,
            }
        )
        candidate.trip_document["metadata"]["content_hash"] = candidate.content_hash()
        snapshot = self._create_snapshot(previous.trip_id, operation)
        self._write_state_transaction(
            candidate,
            snapshot=snapshot,
            operation=operation,
            removed_files=removed_files,
        )
        verified = self._load_state(trip_id=previous.trip_id, validate_hash=True)
        self._write_context_best_effort(verified)
        return {
            "changed": True,
            "revision": verified.revision,
            "trip": verified.coordinator_payload(),
        }

    def _transaction_marker_path(self, trip_id: str) -> Path:
        return self._trip_dir(trip_id) / ".roadplanner_transaction.json"

    def _write_state_transaction(
        self,
        state: TripState,
        *,
        snapshot: Path,
        operation: str,
        removed_files: list[str],
    ) -> None:
        marker_path = self._transaction_marker_path(state.trip_id)
        try:
            relative_snapshot = snapshot.resolve().relative_to(self.backup_dir.resolve())
        except ValueError as err:
            raise StorageError("Sicherung liegt außerhalb des Backup-Verzeichnisses") from err
        marker = {
            "schema_version": 1,
            "trip_id": state.trip_id,
            "target_revision": state.revision,
            "snapshot": relative_snapshot.as_posix(),
            "operation": operation,
            "removed_files": [_safe_day_file(path, "removed_file") for path in removed_files],
            "created_at": utc_now_iso(),
        }
        _write_json_atomic(marker_path, marker)
        try:
            for ref in state.trip_document["days"]:
                _write_json_atomic(
                    self._day_path(state.trip_id, ref["file"]),
                    state.day_documents[ref["id"]],
                )
            _write_json_atomic(
                self._trip_dir(state.trip_id) / "trip.json",
                state.trip_document,
            )
            for relative_file in removed_files:
                self._day_path(state.trip_id, relative_file).unlink(missing_ok=True)
            marker_path.unlink(missing_ok=True)
            _fsync_dir(marker_path.parent)
            self._prune_backups()
        except Exception as err:
            try:
                self._recover_transaction(state.trip_id)
            except Exception as recovery_err:
                raise StorageError(
                    "Schreibvorgang und automatische Wiederherstellung sind "
                    f"fehlgeschlagen: {recovery_err}"
                ) from err
            raise StorageError(
                "Schreibvorgang fehlgeschlagen; die vorherige Sicherung wurde "
                "wiederhergestellt"
            ) from err

    def _recover_transaction(self, trip_id: str) -> None:
        marker_path = self._transaction_marker_path(trip_id)
        if not marker_path.exists():
            return
        marker = _read_json(marker_path)
        target_revision = _ensure_non_negative_int(
            marker.get("target_revision"),
            "transaction.target_revision",
        )
        completed = False
        try:
            state = self._load_state(
                trip_id=trip_id,
                validate_hash=True,
                recover=False,
            )
            completed = state.revision == target_revision
        except RoadplannerError:
            completed = False
        if completed:
            for relative_file in marker.get("removed_files", []):
                self._day_path(trip_id, relative_file).unlink(missing_ok=True)
            marker_path.unlink(missing_ok=True)
            _fsync_dir(marker_path.parent)
            return
        snapshot_value = marker.get("snapshot")
        if not isinstance(snapshot_value, str):
            raise StorageError("Transaktionsmarker enthält keine Sicherung")
        snapshot = (self.backup_dir / snapshot_value).resolve(strict=False)
        try:
            snapshot.relative_to(self.backup_dir.resolve())
        except ValueError as err:
            raise StorageError("Ungültiger Sicherungspfad im Transaktionsmarker") from err
        self._restore_snapshot(snapshot)
        marker_path.unlink(missing_ok=True)
        _fsync_dir(marker_path.parent)

    def _create_snapshot(self, trip_id: str, reason: str) -> Path:
        safe_reason = re.sub(r"[^A-Za-z0-9_-]+", "-", reason).strip("-") or "backup"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        snapshot = (
            self.backup_dir
            / trip_id
            / f"{timestamp}-{safe_reason}-{uuid.uuid4().hex[:8]}"
        )
        snapshot.mkdir(parents=True, exist_ok=False)
        trip_dir = self._trip_dir(trip_id)
        manifest = {
            "schema_version": 1,
            "trip_id": trip_id,
            "created_at": utc_now_iso(),
            "reason": reason,
            "pointer_exists": self.pointer_path.exists(),
            "trip_exists": (trip_dir / "trip.json").exists(),
            "day_files": [],
        }
        if self.pointer_path.exists():
            shutil.copy2(self.pointer_path, snapshot / "active_trip.json")
        if (trip_dir / "trip.json").exists():
            shutil.copy2(trip_dir / "trip.json", snapshot / "trip.json")
        snapshot_days = snapshot / "days"
        snapshot_days.mkdir()
        days_dir = trip_dir / "days"
        if days_dir.exists():
            for source in sorted(days_dir.glob("*.json")):
                shutil.copy2(source, snapshot_days / source.name)
                manifest["day_files"].append(source.name)
        _write_json_atomic(snapshot / "manifest.json", manifest)
        self._prune_backups()
        return snapshot

    def create_backup(self, reason: str = "manual") -> dict[str, Any]:
        state = self._load_state()
        path = self._create_snapshot(state.trip_id, reason)
        return {
            "created": True,
            "trip_id": state.trip_id,
            "revision": state.revision,
            "backup_path": str(path),
        }

    def _restore_snapshot(self, snapshot: Path) -> None:
        manifest_path = snapshot / "manifest.json"
        if not manifest_path.exists():
            raise StorageError(f"Sicherungsmanifest fehlt: {snapshot}")
        manifest = _read_json(manifest_path)
        trip_id = validate_identifier(manifest.get("trip_id"), "backup.trip_id")
        trip_dir = self._trip_dir(trip_id)
        days_dir = trip_dir / "days"
        trip_dir.mkdir(parents=True, exist_ok=True)
        days_dir.mkdir(parents=True, exist_ok=True)

        if manifest.get("pointer_exists"):
            shutil.copy2(snapshot / "active_trip.json", self.pointer_path)
        if manifest.get("trip_exists"):
            shutil.copy2(snapshot / "trip.json", trip_dir / "trip.json")
        else:
            (trip_dir / "trip.json").unlink(missing_ok=True)
        for path in days_dir.glob("*.json"):
            path.unlink()
        for name in manifest.get("day_files", []):
            safe_name = Path(_safe_day_file(f"days/{name}", "backup.day_file")).name
            shutil.copy2(snapshot / "days" / safe_name, days_dir / safe_name)
        _fsync_dir(days_dir)
        _fsync_dir(trip_dir)
        _fsync_dir(self.pointer_path.parent)

    def _prune_backups(self) -> None:
        if self.backup_count < 1 or not self.backup_dir.exists():
            return
        snapshots = sorted(
            (
                path
                for path in self.backup_dir.glob("*/*")
                if path.is_dir() and (path / "manifest.json").exists()
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in snapshots[self.backup_count :]:
            shutil.rmtree(path, ignore_errors=True)

    def adopt_external_changes(
        self,
        *,
        actor: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        state = self._load_state(validate_hash=False)
        self._check_revision(state, expected_revision)
        actual_hash = state.content_hash()
        if state.trip_document["metadata"].get("content_hash") == actual_hash:
            return {
                "changed": False,
                "revision": state.revision,
                "trip": state.coordinator_payload(),
            }
        snapshot = self._create_snapshot(state.trip_id, "adopt-external-changes")
        now = utc_now_iso()
        state.trip_document["metadata"].update(
            {
                "revision": state.revision + 1,
                "updated_at": now,
                "updated_by": (actor or "unknown")[:200],
                "last_operation": "adopt_external_changes",
                "content_hash": actual_hash,
            }
        )
        self._write_state_transaction(
            state,
            snapshot=snapshot,
            operation="adopt_external_changes",
            removed_files=[],
        )
        verified = self._load_state()
        self._write_context_best_effort(verified)
        return {
            "changed": True,
            "revision": verified.revision,
            "trip": verified.coordinator_payload(),
        }

    def preview_changeset(self, changeset: dict[str, Any]) -> dict[str, Any]:
        """Validate a ChangeSet against the active trip without writing files."""
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        current = self._load_state()
        summary = changeset_summary(normalized)
        response: dict[str, Any] = {
            **summary,
            "current_trip_id": current.trip_id,
            "current_revision": current.revision,
            "applicable": False,
            "would_change": False,
        }
        if normalized["trip_id"] != current.trip_id:
            response.update(
                {
                    "status": "wrong_trip",
                    "reason": (
                        "ChangeSet gehört zur Reise "
                        f"{normalized['trip_id']}, aktiv ist {current.trip_id}."
                    ),
                }
            )
            return response
        if normalized["base_revision"] != current.revision:
            response.update(
                {
                    "status": "revision_conflict",
                    "reason": (
                        "ChangeSet basiert auf Revision "
                        f"{normalized['base_revision']}, aktuell ist "
                        f"{current.revision}."
                    ),
                }
            )
            return response

        execution = execute_changeset(current, normalized)
        would_change = (
            current.business_value() != execution.candidate.business_value()
        )
        response.update(
            {
                "status": "ready",
                "applicable": True,
                "would_change": would_change,
                "target_revision": current.revision + (1 if would_change else 0),
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        return response

    def inspect_changeset_for_import(
        self,
        changeset: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate an external ChangeSet deeply, including stale revisions.

        A stale base revision remains a review conflict, but all referenced
        entities and operation payloads are still checked against the current
        active trip before the ChangeSet is admitted to the inbox.
        """
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        current = self._load_state()
        summary = changeset_summary(normalized)
        response: dict[str, Any] = {
            **summary,
            "current_trip_id": current.trip_id,
            "current_revision": current.revision,
            "applicable": False,
            "would_change": False,
        }
        if normalized["trip_id"] != current.trip_id:
            response.update(
                {
                    "status": "wrong_trip",
                    "reason": (
                        "ChangeSet gehört zur Reise "
                        f"{normalized['trip_id']}, aktiv ist {current.trip_id}."
                    ),
                }
            )
            return response

        execution = execute_changeset(current, normalized)
        would_change = (
            current.business_value() != execution.candidate.business_value()
        )
        response.update(
            {
                "would_change": would_change,
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        if normalized["base_revision"] != current.revision:
            response.update(
                {
                    "status": "revision_conflict",
                    "reason": (
                        "ChangeSet basiert auf Revision "
                        f"{normalized['base_revision']}, aktuell ist "
                        f"{current.revision}."
                    ),
                }
            )
            return response

        response.update(
            {
                "status": "ready",
                "applicable": True,
                "target_revision": current.revision + (1 if would_change else 0),
            }
        )
        return response

    def apply_changeset(
        self,
        *,
        changeset: dict[str, Any],
        actor: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Apply all ChangeSet operations atomically in one route revision."""
        from .changeset import (
            changeset_summary,
            execute_changeset,
            normalize_changeset,
        )

        normalized = normalize_changeset(changeset)
        previous = self._load_state()
        if normalized["trip_id"] != previous.trip_id:
            raise ValidationError(
                "ChangeSet gehört zur Reise "
                f"'{normalized['trip_id']}', aktiv ist '{previous.trip_id}'"
            )
        if expected_revision is not None:
            self._check_revision(previous, expected_revision)
            if expected_revision != normalized["base_revision"]:
                raise ValidationError(
                    "expected_revision stimmt nicht mit base_revision des "
                    "ChangeSets überein"
                )
        self._check_revision(previous, normalized["base_revision"])
        execution = execute_changeset(previous, normalized)
        result = self._commit(
            previous,
            execution.candidate,
            actor=actor,
            operation="apply_changeset",
            removed_files=execution.removed_files,
        )
        result.update(
            {
                **changeset_summary(normalized),
                "revision_before": previous.revision,
                "revision_after": result["revision"],
                "operation_results": execution.operation_results,
                "id_map": execution.id_map,
            }
        )
        return result

    def export_trip(self) -> dict[str, Any]:
        state = self._load_state()
        return {
            "trip_id": state.trip_id,
            "revision": state.revision,
            "trip_json": json.dumps(
                state.combined_export(),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
        }

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

            for stop in document["stops"]:
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
            for stop in document["stops"][:MAX_CONTEXT_STOPS_PER_DAY]:
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
        return self._context_payload(self._load_state())

    def get_context_markdown(self) -> dict[str, Any]:
        """Return bounded Markdown context for an authenticated external bridge."""
        current = self._load_state()
        context = self._context_payload(current)
        return {
            "trip_id": current.trip_id,
            "revision": current.revision,
            "content": self._context_markdown(current),
            "days_truncated": context["route"]["days_truncated"],
            "stops_truncated": context["route"]["stops_truncated"],
        }

    def _write_context_best_effort(self, state: TripState) -> None:
        """Refresh derived context without making a canonical mutation fail."""
        try:
            self.write_context(state)
        except Exception as err:  # Derived export must never roll back canonical data.
            _LOGGER.warning("Roadplanner context export failed: %s", err)

    def write_context(self, state: TripState | None = None) -> dict[str, Any]:
        """Write bounded derived context files for Drive or OneDrive sync."""
        current = state or self._load_state()
        outbox = self.handoff_dir / "outbox"
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

