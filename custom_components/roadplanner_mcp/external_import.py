"""Pure validation helpers for provider-neutral external ChangeSet imports."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any

from .changeset import (
    APPLY_MODE_REVIEW,
    CHANGESET_KIND,
    CHANGESET_VERSION,
    normalize_changeset,
)
from .roadplanner import ValidationError, _validate_json_tree

DRIVE_IMPORT_KIND = "roadplanner_drive_import"
DRIVE_IMPORT_VERSION = 1

_ALLOWED_ROOT_FIELDS = {
    "kind",
    "version",
    "sent_at",
    "drive_file",
    "changeset",
    "producer",
    "transport",
    "remote_request_id",
}
_ALLOWED_DRIVE_FILE_FIELDS = {"id", "name"}
_REQUIRED_CHANGESET_FIELDS = {
    "kind",
    "version",
    "changeset_id",
    "trip_id",
    "base_revision",
    "created_at",
    "apply_mode",
    "operations",
    "open_questions",
}


def _strict_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    return _validate_json_tree(deepcopy(value), field_name)


def _allowed_fields(
    value: dict[str, Any],
    allowed: set[str],
    field_name: str,
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValidationError(
            f"Nicht erlaubte Felder in '{field_name}': "
            + ", ".join(sorted(unknown))
        )


def _text(
    value: Any,
    field_name: str,
    *,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss Text sein")
    result = value.strip()
    if not allow_empty and not result:
        raise ValidationError(f"'{field_name}' darf nicht leer sein")
    if len(result) > maximum:
        raise ValidationError(
            f"'{field_name}' ist zu lang (maximal {maximum} Zeichen)"
        )
    return result


def _timestamp(value: Any, field_name: str) -> str:
    result = _text(value, field_name, maximum=100)
    candidate = result[:-1] + "+00:00" if result.endswith("Z") else result
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as err:
        raise ValidationError(
            f"'{field_name}' muss ein gültiger ISO-8601-Zeitstempel sein"
        ) from err
    if parsed.tzinfo is None:
        raise ValidationError(
            f"'{field_name}' muss eine Zeitzone enthalten"
        )
    return result


def _stable_json_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _changeset_source_format(value: dict[str, Any]) -> str:
    operations = value.get("operations")
    if isinstance(operations, list) and any(
        isinstance(operation, dict)
        and ("action" in operation or "entity_type" in operation)
        for operation in operations
    ):
        return "entity_action_v1"
    return "canonical_v1"


def normalize_drive_import_payload(raw: Any) -> dict[str, Any]:
    """Validate one external transport envelope and normalize its ChangeSet."""
    source = _strict_object(raw, "drive_import")
    _allowed_fields(source, _ALLOWED_ROOT_FIELDS, "drive_import")

    if source.get("kind") != DRIVE_IMPORT_KIND:
        raise ValidationError(
            f"'kind' muss '{DRIVE_IMPORT_KIND}' sein"
        )
    version = source.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != DRIVE_IMPORT_VERSION
    ):
        raise ValidationError(
            f"Nicht unterstützte Drive-Import-Version: {version}"
        )

    sent_at = _timestamp(source.get("sent_at"), "sent_at")
    raw_drive_file = source.get("drive_file")
    drive_file: dict[str, str] = {}
    if raw_drive_file is not None:
        drive_file_source = _strict_object(raw_drive_file, "drive_file")
        _allowed_fields(
            drive_file_source,
            _ALLOWED_DRIVE_FILE_FIELDS,
            "drive_file",
        )
        if drive_file_source.get("id") is not None:
            drive_file["id"] = _text(
                drive_file_source["id"],
                "drive_file.id",
                maximum=500,
            )
        if drive_file_source.get("name") is not None:
            drive_file["name"] = _text(
                drive_file_source["name"],
                "drive_file.name",
                maximum=500,
            )

    source_changeset = _strict_object(source.get("changeset"), "changeset")
    missing = sorted(_REQUIRED_CHANGESET_FIELDS - set(source_changeset))
    if missing:
        raise ValidationError(
            "Pflichtfelder im externen ChangeSet fehlen: "
            + ", ".join(missing)
        )
    if source_changeset.get("kind") != CHANGESET_KIND:
        raise ValidationError(f"'changeset.kind' muss '{CHANGESET_KIND}' sein")
    if source_changeset.get("version") != CHANGESET_VERSION:
        raise ValidationError(
            f"'changeset.version' muss {CHANGESET_VERSION} sein"
        )
    if source_changeset.get("apply_mode") != APPLY_MODE_REVIEW:
        raise ValidationError(
            "Externe Drive-Importe müssen apply_mode='review' verwenden"
        )

    changeset = normalize_changeset(source_changeset)
    changeset["created_at"] = _timestamp(
        changeset.get("created_at"),
        "changeset.created_at",
    )
    if changeset["apply_mode"] != APPLY_MODE_REVIEW:
        raise ValidationError(
            "Externe Drive-Importe müssen apply_mode='review' verwenden"
        )

    producer = _text(
        source.get("producer", "gemini"),
        "producer",
        maximum=100,
    )
    transport = _text(
        source.get("transport", "google_apps_script"),
        "transport",
        maximum=100,
    )
    remote_request_id = source.get("remote_request_id")
    if remote_request_id is not None:
        remote_request_id = _text(
            remote_request_id,
            "remote_request_id",
            maximum=500,
        )

    return {
        "kind": DRIVE_IMPORT_KIND,
        "version": DRIVE_IMPORT_VERSION,
        "sent_at": sent_at,
        "drive_file": drive_file,
        "changeset": changeset,
        "source_changeset": source_changeset,
        "changeset_source_sha256": _stable_json_sha256(source_changeset),
        "changeset_source_format": _changeset_source_format(source_changeset),
        "producer": producer,
        "transport": transport,
        "remote_request_id": remote_request_id,
    }


def drive_import_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Build bounded transport metadata for the existing handoff envelope."""
    drive_file = payload["drive_file"]
    metadata: dict[str, Any] = {
        "source": "external_changeset_import",
        "producer": payload["producer"],
        "transport": payload["transport"],
        "sent_at": payload["sent_at"],
        "changeset_source_format": payload["changeset_source_format"],
        "changeset_source_sha256": payload["changeset_source_sha256"],
    }
    if drive_file.get("id"):
        metadata["drive_file_id"] = drive_file["id"]
    if drive_file.get("name"):
        metadata["drive_file_name"] = drive_file["name"]
    if payload.get("remote_request_id"):
        metadata["remote_request_id"] = payload["remote_request_id"]
    return metadata


def drive_import_external_id(payload: dict[str, Any]) -> str:
    """Return a stable transport id while ChangeSet id remains authoritative."""
    drive_file = payload["drive_file"]
    if drive_file.get("id"):
        return f"drive:{drive_file['id']}"
    if payload.get("remote_request_id"):
        return f"request:{payload['remote_request_id']}"
    return f"changeset:{payload['changeset']['changeset_id']}"
