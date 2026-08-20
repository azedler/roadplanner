"""On-disk layout, schema migration on load, and transaction/snapshot/backup machinery.

Owns every low-level primitive the higher-level query/mutation/changeset
collaborators need to safely read and write the canonical split JSON
documents. ``_commit`` deliberately returns the verified post-write
``TripState`` (or ``None`` when nothing changed) instead of writing derived
handoff context itself - context export is a separate concern the caller
triggers, keeping this module free of any dependency on it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
import shutil
from typing import Any
import uuid

from .identifiers import validate_identifier
from .json_io import (
    ConcurrentModificationError,
    RevisionConflictError,
    RoadplannerError,
    StorageError,
    TripNotFoundError,
    ValidationError,
    _fsync_dir,
    _read_json,
    _write_json_atomic,
    utc_now_iso,
)
from .routing_helpers import _reconcile_routing_after_change
from .trip_documents import (
    POINTER_SCHEMA_VERSION,
    TRIP_SCHEMA_VERSION,
    _ensure_non_negative_int,
    _ensure_string,
    _safe_day_file,
    normalize_day_document,
    normalize_trip_document,
)
from .trip_state import TripState

#: Explicit transliteration table. NOT ``str.casefold()``: casefold turns
#: "ß" into "ss" as a side effect nobody sees in review, and this project
#: has already lost a matcher to exactly that. Here the mapping is the
#: point, so it is written down where it can be read.
_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ẞ": "ss"}
)
_SLUG_JUNK = re.compile(r"[^a-z0-9]+")


def _slug_from_title(title: str) -> str:
    """Filesystem-safe trip id from a human title; '' when nothing survives.

    Lowercase after transliterating (so Ä→ae, not Ä→ä→ae twice), every
    other non-alphanumeric run becomes one '-', and the result is capped
    well below ``validate_identifier``'s 128 so a collision suffix still
    fits. '..', '/', '\\' and NUL cannot survive: nothing outside
    ``[a-z0-9-]`` does.
    """
    slug = _SLUG_JUNK.sub("-", str(title).translate(_UMLAUT_MAP).lower())
    return slug.strip("-")[:100].strip("-")


def _new_trip_fallback_id() -> str:
    """For titles with no latin letters at all (e.g. fully cyrillic)."""
    return f"trip-{uuid.uuid4().hex[:12]}"


@dataclass(slots=True)
class TripRepository:
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

    def initialize_documents(self, *, create_if_missing: bool = True) -> TripState:
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

        return self._load_state(trip_id=trip_id, validate_hash=True)

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

    def create_trip_documents(
        self,
        *,
        title: str,
        actor: str,
        status: str = "planning",
        start_date: str | None = None,
        end_date: str | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a new, inactive trip on disk and return its document.

        Deliberately NOT built on ``_commit``: that path is a transaction
        over the ACTIVE trip (snapshot, marker, recovery, all keyed by
        ``previous.trip_id``) and creating a different, inactive trip
        through it would entangle two trips' state. This primitive writes
        exactly one new directory and removes it again if anything fails -
        a half-created trip folder would show up in ``list_trips`` and
        break ``set_active_trip`` with ``TripNotFoundError``.
        """
        if not isinstance(title, str) or not title.strip():
            raise ValidationError("Die neue Reise braucht einen Titel")
        title = title.strip()
        now = utc_now_iso()

        slug = _slug_from_title(title)
        base = slug or _new_trip_fallback_id()
        trip_id = base
        suffix = 2
        while (self.trips_dir / trip_id).exists():
            trip_id = f"{base}-{suffix}"
            suffix += 1
        # The slug generator is the first line of defence; this is the
        # last. An id that fails here must never touch the filesystem.
        trip_id = validate_identifier(trip_id, "trip_id")

        document = self._default_trip_document(trip_id, [], now)
        document["trip"]["title"] = title
        if status:
            document["trip"]["status"] = status
        document["trip"]["start_date"] = start_date
        document["trip"]["end_date"] = end_date
        document["trip"]["notes"] = notes or ""
        document["metadata"].update(
            {
                "updated_by": (actor or "unknown")[:200],
                "last_operation": "create_trip",
            }
        )
        # Normalize BEFORE anything exists on disk: a title or date the
        # schema rejects must fail without leaving a directory behind.
        document = normalize_trip_document(
            document,
            expected_trip_id=trip_id,
            fallback_timestamp=now,
        )
        pointer = self._load_pointer()
        state = TripState(pointer, document, {}, [])
        state.trip_document["metadata"]["content_hash"] = state.content_hash()

        trip_dir = self.trips_dir / trip_id
        created = False
        try:
            # exist_ok=False so a directory that appeared between the
            # collision check and here fails the mkdir - and the cleanup
            # below only ever removes a directory THIS call created.
            trip_dir.mkdir(parents=True, exist_ok=False)
            created = True
            (trip_dir / "days").mkdir()
            _write_json_atomic(trip_dir / "trip.json", state.trip_document)
            _fsync_dir(trip_dir)
        except Exception as err:
            if created:
                shutil.rmtree(trip_dir, ignore_errors=True)
            if isinstance(err, RoadplannerError):
                raise
            raise StorageError(
                f"Die neue Reise konnte nicht angelegt werden: {err}"
            ) from err
        return deepcopy(state.trip_document)

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
    ) -> tuple[dict[str, Any], TripState | None]:
        """Persist ``candidate`` if changed and return ``(result, verified)``.

        ``verified`` is ``None`` exactly when nothing changed, so a caller can
        decide whether to trigger a downstream context-export refresh without
        this repository knowing anything about context export.
        """
        _reconcile_routing_after_change(previous, candidate, operation)
        if previous.business_value() == candidate.business_value():
            return {
                "changed": False,
                "revision": previous.revision,
                "trip": previous.coordinator_payload(),
            }, None
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
        return {
            "changed": True,
            "revision": verified.revision,
            "trip": verified.coordinator_payload(),
        }, verified

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
