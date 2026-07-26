"""Atomic, size-bounded JSON/text file I/O for Roadplanner's on-disk store.

No domain knowledge lives here - only the exception hierarchy every other
Roadplanner module raises/catches (defined here since this is the first,
dependency-free module in the roadplanner.py decomposition) and the
low-level file primitives that read/write the canonical JSON documents
safely (bounded size, atomic replace, fsync).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

MAX_JSON_FILE_BYTES = 5 * 1024 * 1024


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


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Ungültiger JSON-Zahlenwert: {value}")


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
