"""Private ChangeSet inbox for cloud bridges and local handoff folders."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any
import uuid

from .changeset import changeset_summary, normalize_changeset
from .roadplanner import (
    StorageError,
    ValidationError,
    _read_json,
    _validate_json_tree,
    _write_json_atomic,
    utc_now_iso,
    validate_identifier,
)

HANDOFF_SCHEMA_VERSION = 2
MAX_HANDOFF_BYTES = 512 * 1024
MAX_HANDOFF_METADATA_BYTES = 64 * 1024
MAX_HANDOFF_RESULT_BYTES = 256 * 1024
MAX_HANDOFFS_RETURNED = 100
_ALLOWED_INBOX_SUFFIXES = {".json", ".md", ".txt"}
_ALLOWED_STATUSES = {
    "pending",
    "review_required",
    "conflict",
    "failed",
    "applied",
    "archived",
}
_PENDING_STATUSES = {"pending", "review_required", "conflict", "failed"}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class HandoffConflictError(ValidationError):
    """Raised when an idempotency key is reused with different content."""


def _safe_text(value: Any, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss Text sein")
    if len(value) > max_length:
        raise ValidationError(
            f"'{field_name}' ist zu lang (maximal {max_length} Zeichen)"
        )
    return value


def _validate_base_revision(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError("'base_revision' muss eine nicht-negative Ganzzahl sein")
    return value


def _validate_bounded_object(
    value: Any,
    field_name: str,
    maximum_bytes: int,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    result = _validate_json_tree(deepcopy(value), field_name)
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise ValidationError(
            f"'{field_name}' ist größer als {maximum_bytes // 1024} KiB"
        )
    return result


def _new_handoff_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"handoff-{timestamp}-{uuid.uuid4().hex[:10]}"


def _safe_filename(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return (result[:100] or "handoff") + ".json"


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _changeset_hash(changeset: dict[str, Any]) -> str:
    content = json.dumps(
        changeset,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _content_hash(content)


def _optional_sha256(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    result = _safe_text(value, field_name, 64).casefold()
    if _SHA256_PATTERN.fullmatch(result) is None:
        raise ValidationError(f"'{field_name}' muss eine SHA-256-Prüfsumme sein")
    return result


def _decode_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, RecursionError):
        value = None
    if isinstance(value, dict):
        return value

    for match in _JSON_FENCE.finditer(candidate):
        try:
            value = json.loads(match.group(1))
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(value, dict):
            return value

    decoder = json.JSONDecoder()
    for position, character in enumerate(candidate):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(candidate[position:])
        except (json.JSONDecodeError, RecursionError):
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_changeset(content: str) -> dict[str, Any]:
    """Extract and validate one ChangeSet from JSON, Markdown, or Google Docs text."""
    content = _safe_text(content, "content", MAX_HANDOFF_BYTES)
    if len(content.encode("utf-8")) > MAX_HANDOFF_BYTES:
        raise ValidationError("Übergabe ist größer als 512 KiB")
    value = _decode_json_object(content)
    if value is None:
        raise ValidationError(
            "Übergabe enthält kein gültiges Roadplanner-ChangeSet als JSON"
        )
    return normalize_changeset(value)


@dataclass(slots=True)
class HandoffStore:
    """Store validated ChangeSets without granting external file access."""

    base_dir: Path

    @property
    def inbox_dir(self) -> Path:
        return self.base_dir / "inbox"

    @property
    def pending_dir(self) -> Path:
        return self.base_dir / "pending"

    @property
    def applied_dir(self) -> Path:
        return self.base_dir / "applied"

    @property
    def archive_dir(self) -> Path:
        return self.base_dir / "archive"

    @property
    def processed_dir(self) -> Path:
        return self.base_dir / "processed"

    @property
    def failed_dir(self) -> Path:
        return self.base_dir / "failed"

    @property
    def outbox_dir(self) -> Path:
        return self.base_dir / "outbox"

    @property
    def drop_dir(self) -> Path:
        """Legacy 1.2.x local drop folder retained for migration."""
        return self.base_dir / "drop"

    @property
    def legacy_processed_dir(self) -> Path:
        return self.base_dir / "drop_processed"

    def initialize(self) -> None:
        for path in (
            self.inbox_dir,
            self.pending_dir,
            self.applied_dir,
            self.archive_dir,
            self.processed_dir,
            self.failed_dir,
            self.outbox_dir,
            self.drop_dir,
            self.legacy_processed_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def folder_info(self) -> dict[str, Any]:
        self.initialize()
        return {
            "base": str(self.base_dir),
            "inbox": str(self.inbox_dir),
            "pending": str(self.pending_dir),
            "applied": str(self.applied_dir),
            "archive": str(self.archive_dir),
            "processed": str(self.processed_dir),
            "failed": str(self.failed_dir),
            "outbox": str(self.outbox_dir),
            "accepted_extensions": sorted(_ALLOWED_INBOX_SUFFIXES),
        }

    def _envelope_directories(self) -> tuple[Path, ...]:
        return (self.pending_dir, self.applied_dir, self.archive_dir)

    def _find_duplicate(
        self,
        *,
        source: str,
        external_id: str | None,
        changeset_id: str,
        changeset_sha256: str,
        source_payload_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an idempotent retry or reject reused keys with new content."""
        for directory in self._envelope_directories():
            for path in directory.glob("*.json"):
                try:
                    envelope = self._validate_envelope(_read_json(path))
                except (StorageError, ValidationError):
                    continue
                matches_changeset = envelope.get("changeset_id") == changeset_id
                matches_external = (
                    external_id is not None
                    and envelope.get("source") == source
                    and envelope.get("external_id") == external_id
                )
                if matches_changeset or matches_external:
                    existing_source_hash = envelope.get(
                        "source_payload_sha256"
                    )
                    if (
                        source_payload_sha256 is not None
                        and existing_source_hash is not None
                    ):
                        content_changed = (
                            existing_source_hash != source_payload_sha256
                        )
                    else:
                        content_changed = (
                            envelope["changeset_sha256"] != changeset_sha256
                        )
                    if content_changed:
                        key_name = (
                            "changeset_id"
                            if matches_changeset
                            else "external_id"
                        )
                        raise HandoffConflictError(
                            f"{key_name} wurde bereits mit anderem Inhalt verwendet"
                        )
                    result = self._compact(envelope)
                    result["duplicate"] = True
                    result["duplicate_by"] = (
                        "changeset_id" if matches_changeset else "external_id"
                    )
                    return result
        return None

    def check_duplicate(
        self,
        *,
        changeset: dict[str, Any],
        source: str,
        external_id: str | None = None,
        source_payload_sha256: str | None = None,
    ) -> dict[str, Any] | None:
        """Check idempotency without creating an envelope."""
        self.initialize()
        normalized = normalize_changeset(changeset)
        source = _safe_text(source, "source", 100).strip() or "unknown"
        if external_id is not None:
            external_id = _safe_text(
                external_id,
                "external_id",
                500,
            ).strip() or None
        source_payload_sha256 = _optional_sha256(
            source_payload_sha256,
            "source_payload_sha256",
        )
        return self._find_duplicate(
            source=source,
            external_id=external_id,
            changeset_id=normalized["changeset_id"],
            changeset_sha256=_changeset_hash(normalized),
            source_payload_sha256=source_payload_sha256,
        )

    def ingest(
        self,
        *,
        content: str | None = None,
        changeset: dict[str, Any] | None = None,
        title: str = "Roadplanner-Übergabe",
        source: str = "manual",
        content_type: str = "application/json",
        external_id: str | None = None,
        trip_id: str | None = None,
        base_revision: int | None = None,
        metadata: dict[str, Any] | None = None,
        source_payload_sha256: str | None = None,
        initial_status: str = "pending",
        initial_error: str | None = None,
    ) -> dict[str, Any]:
        """Create a pending, validated ChangeSet envelope."""
        self.initialize()
        if changeset is not None and content is not None:
            raise ValidationError("Nur 'changeset' oder 'content' übergeben")
        if changeset is not None:
            normalized = normalize_changeset(changeset)
            raw_content = json.dumps(
                changeset,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        elif content is not None:
            raw_content = _safe_text(content, "content", MAX_HANDOFF_BYTES)
            if len(raw_content.encode("utf-8")) > MAX_HANDOFF_BYTES:
                raise ValidationError("Übergabe ist größer als 512 KiB")
            normalized = extract_changeset(raw_content)
        else:
            raise ValidationError("'changeset' oder 'content' ist erforderlich")

        title = _safe_text(title, "title", 500).strip()
        if not title or title == "Roadplanner-Übergabe":
            title = normalized["title"]
        source = _safe_text(source, "source", 100).strip() or "unknown"
        content_type = _safe_text(content_type, "content_type", 100)
        if trip_id is not None:
            trip_id = validate_identifier(trip_id, "trip_id")
            if trip_id != normalized["trip_id"]:
                raise ValidationError(
                    "trip_id der Transporthülle stimmt nicht mit dem ChangeSet überein"
                )
        if base_revision is not None:
            base_revision = _validate_base_revision(base_revision)
            if base_revision != normalized["base_revision"]:
                raise ValidationError(
                    "base_revision der Transporthülle stimmt nicht mit dem "
                    "ChangeSet überein"
                )
        if external_id is not None:
            external_id = _safe_text(external_id, "external_id", 500).strip()
            external_id = external_id or None
        source_payload_sha256 = _optional_sha256(
            source_payload_sha256,
            "source_payload_sha256",
        )
        changeset_sha256 = _changeset_hash(normalized)
        duplicate = self._find_duplicate(
            source=source,
            external_id=external_id,
            changeset_id=normalized["changeset_id"],
            changeset_sha256=changeset_sha256,
            source_payload_sha256=source_payload_sha256,
        )
        if duplicate is not None:
            return duplicate
        metadata = _validate_bounded_object(
            deepcopy(metadata or {}),
            "handoff.metadata",
            MAX_HANDOFF_METADATA_BYTES,
        )
        initial_status = _safe_text(
            initial_status,
            "initial_status",
            100,
        )
        if initial_status not in _PENDING_STATUSES:
            raise ValidationError("Ungültiger initial_status für Übergabe")
        if initial_error is not None:
            initial_error = _safe_text(
                initial_error,
                "initial_error",
                2_000,
            )

        handoff_id = _new_handoff_id()
        envelope = {
            "schema_version": HANDOFF_SCHEMA_VERSION,
            "id": handoff_id,
            "status": initial_status,
            "received_at": utc_now_iso(),
            "title": title,
            "source": source,
            "content_type": content_type,
            "external_id": external_id,
            "trip_id": normalized["trip_id"],
            "base_revision": normalized["base_revision"],
            "changeset_id": normalized["changeset_id"],
            "apply_mode": normalized["apply_mode"],
            "destructive": normalized["destructive"],
            "automatic_eligible": normalized["automatic_eligible"],
            "changeset": normalized,
            "changeset_sha256": changeset_sha256,
            "source_payload_sha256": source_payload_sha256,
            "raw_content": raw_content,
            "raw_content_sha256": _content_hash(raw_content),
            "metadata": metadata,
            "last_error": initial_error,
            "attempt_count": 0,
        }
        path = self.pending_dir / _safe_filename(handoff_id)
        _write_json_atomic(path, envelope)
        result = self._compact(envelope)
        result["duplicate"] = False
        result["duplicate_by"] = None
        return result

    def list_pending(self, *, limit: int = 50) -> dict[str, Any]:
        self.initialize()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValidationError("'limit' muss zwischen 1 und 100 liegen")
        envelopes: list[dict[str, Any]] = []
        for path in sorted(
            self.pending_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        ):
            try:
                envelope = self._validate_envelope(_read_json(path))
            except (StorageError, ValidationError):
                continue
            envelopes.append(self._compact(envelope))
        counts: dict[str, int] = {}
        for envelope in envelopes:
            status = str(envelope.get("status", "pending"))
            counts[status] = counts.get(status, 0) + 1
        return {
            "count": min(len(envelopes), limit),
            "total": len(envelopes),
            "status_counts": counts,
            "handoffs": envelopes[:limit],
            "truncated": len(envelopes) > limit,
        }

    def status_counts(self) -> dict[str, int]:
        result = self.list_pending(limit=MAX_HANDOFFS_RETURNED)
        counts = dict(result["status_counts"])
        counts["total"] = result["total"]
        return counts

    def list_auto_applicable(self, *, limit: int = 10) -> list[dict[str, Any]]:
        self.initialize()
        result: list[dict[str, Any]] = []
        for path in sorted(
            self.pending_dir.glob("*.json"),
            key=lambda item: item.stat().st_mtime,
        ):
            try:
                envelope = self._validate_envelope(_read_json(path))
            except (StorageError, ValidationError):
                continue
            if (
                envelope["status"] == "pending"
                and envelope["automatic_eligible"]
            ):
                result.append(envelope)
            if len(result) >= limit:
                break
        return result

    def get(self, handoff_id: str) -> dict[str, Any]:
        handoff_id = validate_identifier(handoff_id, "handoff_id")
        filename = _safe_filename(handoff_id)
        for directory in self._envelope_directories():
            path = directory / filename
            if path.exists():
                return self._validate_envelope(_read_json(path))
        raise ValidationError(f"Übergabe nicht gefunden: {handoff_id}")

    def get_pending(self, handoff_id: str) -> dict[str, Any]:
        """Return a handoff that can still be reviewed or applied."""
        handoff_id = validate_identifier(handoff_id, "handoff_id")
        path = self.pending_dir / _safe_filename(handoff_id)
        if not path.exists():
            raise ValidationError(
                f"Ausstehende Übergabe nicht gefunden: {handoff_id}"
            )
        envelope = self._validate_envelope(_read_json(path))
        if envelope["status"] not in _PENDING_STATUSES:
            raise ValidationError(
                f"Übergabe hat keinen bearbeitbaren Status: {envelope['status']}"
            )
        return envelope

    def update_pending(
        self,
        *,
        handoff_id: str,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        handoff_id = validate_identifier(handoff_id, "handoff_id")
        path = self.pending_dir / _safe_filename(handoff_id)
        if not path.exists():
            raise ValidationError(f"Ausstehende Übergabe nicht gefunden: {handoff_id}")
        envelope = self._validate_envelope(_read_json(path))
        status = _safe_text(status, "status", 100)
        if status not in _PENDING_STATUSES:
            raise ValidationError(f"Ungültiger ausstehender Status: {status}")
        envelope["status"] = status
        envelope["last_attempt_at"] = utc_now_iso()
        envelope["attempt_count"] = envelope.get("attempt_count", 0) + 1
        envelope["last_error"] = (
            _safe_text(error, "error", 2_000) if error is not None else None
        )
        if result is not None:
            envelope["last_result"] = _validate_bounded_object(
                result,
                "handoff.last_result",
                MAX_HANDOFF_RESULT_BYTES,
            )
        _write_json_atomic(path, envelope)
        return self._compact(envelope)

    def mark_applied(
        self,
        *,
        handoff_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        self.initialize()
        handoff_id = validate_identifier(handoff_id, "handoff_id")
        source = self.pending_dir / _safe_filename(handoff_id)
        if not source.exists():
            raise ValidationError(f"Ausstehende Übergabe nicht gefunden: {handoff_id}")
        envelope = self._validate_envelope(_read_json(source))
        envelope.update(
            {
                "status": "applied",
                "applied_at": utc_now_iso(),
                "last_error": None,
                "attempt_count": envelope.get("attempt_count", 0) + 1,
                "apply_result": _validate_bounded_object(
                    result,
                    "handoff.apply_result",
                    MAX_HANDOFF_RESULT_BYTES,
                ),
            }
        )
        destination = self.applied_dir / source.name
        _write_json_atomic(destination, envelope)
        source.unlink(missing_ok=True)
        return self._compact(envelope)

    def archive(
        self,
        *,
        handoff_id: str,
        resolution: str = "rejected",
        note: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        handoff_id = validate_identifier(handoff_id, "handoff_id")
        source = self.pending_dir / _safe_filename(handoff_id)
        if not source.exists():
            raise ValidationError(f"Ausstehende Übergabe nicht gefunden: {handoff_id}")
        envelope = self._validate_envelope(_read_json(source))
        envelope.update(
            {
                "status": "archived",
                "archived_at": utc_now_iso(),
                "resolution": _safe_text(resolution, "resolution", 100),
                "resolution_note": _safe_text(note, "note", 2_000),
            }
        )
        destination = self.archive_dir / source.name
        _write_json_atomic(destination, envelope)
        source.unlink(missing_ok=True)
        return self._compact(envelope)

    def scan_inbox(self) -> dict[str, Any]:
        """Import ChangeSet files from the private handoff folder."""
        self.initialize()
        imported: list[dict[str, Any]] = []
        failed: list[dict[str, str]] = []
        sources = (
            (self.inbox_dir, self.processed_dir),
            (self.drop_dir, self.legacy_processed_dir),
        )
        for source_dir, processed_dir in sources:
            for path in sorted(source_dir.iterdir()):
                if (
                    not path.is_file()
                    or path.suffix.casefold() not in _ALLOWED_INBOX_SUFFIXES
                ):
                    continue
                try:
                    if path.stat().st_size > MAX_HANDOFF_BYTES:
                        raise ValidationError("Datei ist größer als 512 KiB")
                    content = path.read_text(encoding="utf-8")
                    content_type = {
                        ".json": "application/json",
                        ".md": "text/markdown",
                        ".txt": "text/plain",
                    }[path.suffix.casefold()]
                    result = self.ingest(
                        content=content,
                        title=path.stem,
                        source="handoff-folder",
                        content_type=content_type,
                        external_id=f"{source_dir.name}/{path.name}",
                        metadata={"source_file": path.name},
                    )
                    self._move_unique(path, processed_dir)
                    imported.append(result)
                except (
                    OSError,
                    UnicodeError,
                    StorageError,
                    ValidationError,
                ) as err:
                    failed.append({"file": path.name, "error": str(err)[:500]})
                    self._quarantine_failed(path, str(err))
        return {
            "imported_count": len(imported),
            "failed_count": len(failed),
            "imported": imported,
            "failed": failed,
        }

    def scan_drop(self) -> dict[str, Any]:
        """Compatibility alias used by Roadplanner 1.2.x services."""
        return self.scan_inbox()

    def _move_unique(self, path: Path, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / path.name
        if destination.exists():
            destination = destination_dir / (
                f"{path.stem}-{uuid.uuid4().hex[:8]}{path.suffix}"
            )
        shutil.move(str(path), str(destination))
        return destination

    def _quarantine_failed(self, path: Path, error: str) -> None:
        if not path.exists():
            return
        try:
            destination = self._move_unique(path, self.failed_dir)
            _write_json_atomic(
                destination.with_suffix(destination.suffix + ".error.json"),
                {
                    "schema_version": 1,
                    "failed_at": utc_now_iso(),
                    "source_file": destination.name,
                    "error": error[:2_000],
                },
            )
        except (OSError, StorageError):
            return

    def _validate_envelope(self, raw: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError("Übergabedatei muss ein JSON-Objekt enthalten")
        result = deepcopy(raw)
        raw_schema = result.get("schema_version", 1)
        if (
            isinstance(raw_schema, bool)
            or not isinstance(raw_schema, int)
            or raw_schema < 1
            or raw_schema > HANDOFF_SCHEMA_VERSION
        ):
            raise ValidationError("Nicht unterstützte schema_version in Übergabe")

        if raw_schema == 1:
            content = _safe_text(
                result.get("content", ""),
                "content",
                MAX_HANDOFF_BYTES,
            )
            changeset = extract_changeset(content)
            result.update(
                {
                    "schema_version": HANDOFF_SCHEMA_VERSION,
                    "changeset": changeset,
                    "changeset_id": changeset["changeset_id"],
                    "trip_id": changeset["trip_id"],
                    "base_revision": changeset["base_revision"],
                    "apply_mode": changeset["apply_mode"],
                    "destructive": changeset["destructive"],
                    "automatic_eligible": changeset["automatic_eligible"],
                    "changeset_sha256": _changeset_hash(changeset),
                    "raw_content": content,
                    "raw_content_sha256": _content_hash(content),
                    "attempt_count": 0,
                    "last_error": None,
                }
            )

        result["id"] = validate_identifier(result.get("id"), "handoff.id")
        for field, maximum in (
            ("status", 100),
            ("received_at", 100),
            ("title", 500),
            ("source", 100),
            ("content_type", 100),
            ("raw_content", MAX_HANDOFF_BYTES),
        ):
            result[field] = _safe_text(result.get(field, ""), field, maximum)
        if len(result["raw_content"].encode("utf-8")) > MAX_HANDOFF_BYTES:
            raise ValidationError("Übergabe ist größer als 512 KiB")
        if result["status"] not in _ALLOWED_STATUSES:
            raise ValidationError(f"Unbekannter Übergabestatus: {result['status']}")
        expected_hash = _content_hash(result["raw_content"])
        stored_hash = result.get("raw_content_sha256")
        if stored_hash is not None and stored_hash != expected_hash:
            raise ValidationError("Prüfsumme des Übergabeinhalts stimmt nicht")
        result["raw_content_sha256"] = expected_hash
        result["trip_id"] = validate_identifier(result.get("trip_id"), "trip_id")
        result["changeset_id"] = validate_identifier(
            result.get("changeset_id"),
            "changeset_id",
        )
        result["base_revision"] = _validate_base_revision(
            result.get("base_revision")
        )
        if result.get("external_id") is not None:
            result["external_id"] = _safe_text(
                result["external_id"],
                "external_id",
                500,
            )
        changeset = normalize_changeset(result.get("changeset"))
        if changeset["trip_id"] != result["trip_id"]:
            raise ValidationError("Trip-ID in Übergabehülle und ChangeSet weicht ab")
        if changeset["base_revision"] != result["base_revision"]:
            raise ValidationError(
                "Basisrevision in Übergabehülle und ChangeSet weicht ab"
            )
        if changeset["changeset_id"] != result["changeset_id"]:
            raise ValidationError(
                "ChangeSet-ID in Übergabehülle und ChangeSet weicht ab"
            )
        result["changeset"] = changeset
        expected_changeset_hash = _changeset_hash(changeset)
        stored_changeset_hash = result.get("changeset_sha256")
        if (
            stored_changeset_hash is not None
            and stored_changeset_hash != expected_changeset_hash
        ):
            raise ValidationError("Prüfsumme des ChangeSets stimmt nicht")
        result["changeset_sha256"] = expected_changeset_hash
        result["source_payload_sha256"] = _optional_sha256(
            result.get("source_payload_sha256"),
            "source_payload_sha256",
        )
        result["apply_mode"] = changeset["apply_mode"]
        result["destructive"] = changeset["destructive"]
        result["automatic_eligible"] = changeset["automatic_eligible"]
        result["schema_version"] = HANDOFF_SCHEMA_VERSION
        result["metadata"] = _validate_bounded_object(
            result.get("metadata", {}),
            "handoff.metadata",
            MAX_HANDOFF_METADATA_BYTES,
        )
        attempt_count = result.get("attempt_count", 0)
        if (
            isinstance(attempt_count, bool)
            or not isinstance(attempt_count, int)
            or attempt_count < 0
        ):
            raise ValidationError("'attempt_count' muss nicht-negativ sein")
        result["attempt_count"] = attempt_count
        if result.get("last_error") is not None:
            result["last_error"] = _safe_text(
                result["last_error"],
                "last_error",
                2_000,
            )
        for field_name in ("last_result", "apply_result"):
            if field_name in result:
                result[field_name] = _validate_bounded_object(
                    result[field_name],
                    f"handoff.{field_name}",
                    MAX_HANDOFF_RESULT_BYTES,
                )
        return result

    def compact(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Return the bounded public view of an already validated envelope."""
        return self._compact(envelope)

    @staticmethod
    def _compact(envelope: dict[str, Any]) -> dict[str, Any]:
        summary = changeset_summary(envelope["changeset"])
        preview = summary["summary"].replace("\r", " ").replace("\n", " ").strip()
        if not preview:
            preview = summary["title"]
        if len(preview) > 300:
            preview = preview[:297] + "…"
        result = {
            "id": envelope["id"],
            "status": envelope.get("status"),
            "received_at": envelope.get("received_at"),
            "title": envelope.get("title"),
            "source": envelope.get("source"),
            "external_id": envelope.get("external_id"),
            "changeset_sha256": envelope.get("changeset_sha256"),
            "preview": preview,
            "attempt_count": envelope.get("attempt_count", 0),
            "last_error": envelope.get("last_error"),
            **summary,
        }
        for field in ("applied_at", "archived_at", "last_attempt_at"):
            if field in envelope:
                result[field] = envelope[field]
        return result
