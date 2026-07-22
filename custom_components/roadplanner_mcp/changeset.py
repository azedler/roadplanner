"""Validated, provider-neutral Roadplanner ChangeSets.

A ChangeSet is an atomic list of targeted route mutations. It is intentionally
independent of Gemini, ChatGPT, Google Drive, and Home Assistant. External
systems may create the JSON document, but only Roadplanner validates and applies
it to the canonical trip files.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .roadplanner import (
    DAY_SCHEMA_VERSION,
    MAX_DAYS,
    MAX_STOPS_PER_DAY,
    TripNotFoundError,
    TripState,
    ValidationError,
    _stable_id,
    _validate_json_tree,
    _without_audit_fields,
    normalize_day_document,
    normalize_stop,
    normalize_trip_document,
    utc_now_iso,
    validate_identifier,
)
from .stop_ordering import reindex_explicit_positions

CHANGESET_KIND = "roadplanner_changeset"
CHANGESET_VERSION = 1
MAX_CHANGESET_BYTES = 512 * 1024
MAX_OPERATIONS = 100
MAX_PREFERENCES_PER_SCOPE = 100
MAX_LIST_ITEMS = 100
MAX_LIST_TEXT = 4_000
MAX_SUMMARY_TEXT = 20_000
APPLY_MODE_REVIEW = "review"
APPLY_MODE_AUTOMATIC = "automatic"
_ALLOWED_APPLY_MODES = {APPLY_MODE_REVIEW, APPLY_MODE_AUTOMATIC}

_TRIP_PATCH_FIELDS = {
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
_DAY_VALUE_FIELDS = {
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
_STOP_VALUE_FIELDS = {
    "name",
    "type",
    "arrival_time",
    "departure_time",
    "location",
    "notes",
    "details",
}
_PREFERENCE_VALUE_FIELDS = {
    "category",
    "text",
    "status",
    "notes",
    "reason",
    "details",
}
_DESTRUCTIVE_OPERATIONS = {
    "remove_day",
    "remove_stop",
    "remove_preference",
}
_OPERATION_NAMES = {
    "update_trip",
    "add_day",
    "update_day",
    "remove_day",
    "add_stop",
    "update_stop",
    "remove_stop",
    "add_preference",
    "update_preference",
    "remove_preference",
}

_ENTITY_ACTION_FIELDS = {
    "operation_id",
    "action",
    "entity_type",
    "entity_id",
    "day_id",
    "day_ref",
    "changes",
    "reason",
    "position",
}
_ENTITY_ACTIONS = {"add", "update", "remove", "delete", "move", "set"}
_ENTITY_TYPES = {"trip", "day", "stop", "preference"}
_OPERATION_ANNOTATION_FIELDS = {
    "operation_id",
    "reason",
    "source_action",
    "source_entity_type",
}


@dataclass(slots=True)
class ChangeSetExecution:
    """Result of applying a ChangeSet to an in-memory trip state."""

    candidate: TripState
    removed_files: list[str]
    operation_results: list[dict[str, Any]]
    id_map: dict[str, dict[str, str]]


def _string(
    value: Any,
    field_name: str,
    *,
    default: str = "",
    max_length: int = 100_000,
    allow_empty: bool = True,
) -> str:
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValidationError(f"'{field_name}' muss Text sein")
    result = value.strip() if not allow_empty else value
    if not allow_empty and not result:
        raise ValidationError(f"'{field_name}' darf nicht leer sein")
    if len(result) > max_length:
        raise ValidationError(
            f"'{field_name}' ist zu lang (maximal {max_length} Zeichen)"
        )
    return result


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"'{field_name}' muss eine nicht-negative Ganzzahl sein")
    return value


def _positive_int_or_none(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"'{field_name}' muss eine positive Ganzzahl sein")
    return value


def _optional_identifier(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return validate_identifier(value, field_name)


def _text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"'{field_name}' muss eine Liste sein")
    if len(value) > MAX_LIST_ITEMS:
        raise ValidationError(
            f"'{field_name}' darf maximal {MAX_LIST_ITEMS} Einträge enthalten"
        )
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(
            _string(
                item,
                f"{field_name}[{index}]",
                max_length=MAX_LIST_TEXT,
                allow_empty=False,
            )
        )
    return result


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    return _validate_json_tree(deepcopy(value), field_name)


def _check_allowed_fields(
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


def _one_reference(
    raw: dict[str, Any],
    *,
    id_key: str,
    ref_key: str,
    field_name: str,
) -> tuple[str | None, str | None]:
    object_id = _optional_identifier(raw.get(id_key), f"{field_name}.{id_key}")
    object_ref = _optional_identifier(raw.get(ref_key), f"{field_name}.{ref_key}")
    if object_id is None and object_ref is None:
        raise ValidationError(
            f"'{field_name}' benötigt '{id_key}' oder '{ref_key}'"
        )
    if object_id is not None and object_ref is not None:
        raise ValidationError(
            f"'{field_name}' darf nicht gleichzeitig '{id_key}' und "
            f"'{ref_key}' enthalten"
        )
    return object_id, object_ref


def _extract_value(
    raw: dict[str, Any],
    *,
    container_key: str,
    allowed_fields: set[str],
    aliases: dict[str, str] | None = None,
    excluded: set[str],
    field_name: str,
) -> dict[str, Any]:
    aliases = aliases or {}
    if container_key in raw:
        value = _object(raw[container_key], f"{field_name}.{container_key}")
        extras = set(raw) - excluded - {container_key}
        if extras:
            raise ValidationError(
                f"'{field_name}' mischt '{container_key}' mit flachen Feldern: "
                + ", ".join(sorted(extras))
            )
    else:
        value = {
            aliases.get(key, key): deepcopy(item)
            for key, item in raw.items()
            if key not in excluded
        }
    _check_allowed_fields(value, allowed_fields, f"{field_name}.{container_key}")
    return value


def _entity_action_position(
    raw: dict[str, Any],
    changes: dict[str, Any],
    field_name: str,
) -> int | None:
    position = raw.get("position")
    if "position" in changes:
        if position is not None and position != changes["position"]:
            raise ValidationError(
                f"'{field_name}.position' ist widersprüchlich"
            )
        position = changes.pop("position")
    return _positive_int_or_none(position, f"{field_name}.position")


def _entity_action_reference(
    raw: dict[str, Any],
    *,
    field_name: str,
) -> tuple[str | None, str | None]:
    day_id = _optional_identifier(raw.get("day_id"), f"{field_name}.day_id")
    day_ref = _optional_identifier(
        raw.get("day_ref"),
        f"{field_name}.day_ref",
    )
    if day_id is not None and day_ref is not None:
        raise ValidationError(
            f"'{field_name}' darf nicht day_id und day_ref enthalten"
        )
    return day_id, day_ref


def _operation_annotations(
    raw: dict[str, Any],
    field_name: str,
) -> dict[str, str]:
    """Normalize optional audit annotations without affecting execution."""
    annotations: dict[str, str] = {}
    operation_id = _optional_identifier(
        raw.get("operation_id"),
        f"{field_name}.operation_id",
    )
    if operation_id is not None:
        annotations["operation_id"] = operation_id
    for key, maximum in (
        ("reason", 4_000),
        ("source_action", 50),
        ("source_entity_type", 50),
    ):
        if raw.get(key) is None:
            continue
        annotations[key] = _string(
            raw[key],
            f"{field_name}.{key}",
            max_length=maximum,
            allow_empty=False,
        )
    return annotations


def _annotated_operation(
    operation: dict[str, Any],
    annotations: dict[str, str],
) -> dict[str, Any]:
    return {**operation, **annotations}


def _adapt_entity_operation(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Translate the external action/entity dialect into canonical operations."""
    field_name = f"operations[{index}]"
    _check_allowed_fields(raw, _ENTITY_ACTION_FIELDS, field_name)
    action = _string(
        raw.get("action"),
        f"{field_name}.action",
        max_length=50,
        allow_empty=False,
    ).casefold()
    entity_type = _string(
        raw.get("entity_type"),
        f"{field_name}.entity_type",
        max_length=50,
        allow_empty=False,
    ).casefold()
    if action not in _ENTITY_ACTIONS:
        raise ValidationError(
            f"Nicht unterstützte action in '{field_name}': {action}"
        )
    if entity_type not in _ENTITY_TYPES:
        raise ValidationError(
            f"Nicht unterstützter entity_type in '{field_name}': "
            f"{entity_type}"
        )

    action = {"delete": "remove", "set": "update"}.get(action, action)
    changes = _object(raw.get("changes", {}), f"{field_name}.changes")
    entity_id = _optional_identifier(
        raw.get("entity_id"),
        f"{field_name}.entity_id",
    )
    operation_id = _optional_identifier(
        raw.get("operation_id"),
        f"{field_name}.operation_id",
    )
    annotations = _operation_annotations(raw, field_name)
    annotations["source_action"] = action
    annotations["source_entity_type"] = entity_type
    day_id, day_ref = _entity_action_reference(raw, field_name=field_name)
    position = _entity_action_position(raw, changes, field_name)

    if entity_type == "trip":
        if action != "update":
            raise ValidationError(
                f"'{field_name}' unterstützt für trip nur action='update'"
            )
        return _annotated_operation(
            {"op": "update_trip", "patch": changes},
            annotations,
        )

    if entity_type == "day":
        if action == "add":
            client_id = entity_id or operation_id
            return _annotated_operation(
                {
                    "op": "add_day",
                    "client_id": client_id,
                    "position": position,
                    "day": changes,
                },
                annotations,
            )
        if entity_id is None:
            raise ValidationError(f"'{field_name}.entity_id' fehlt")
        if action == "update":
            return _annotated_operation(
                {
                    "op": "update_day",
                    "day_id": entity_id,
                    "patch": changes,
                    "position": position,
                },
                annotations,
            )
        if action == "move":
            if position is None:
                raise ValidationError(f"'{field_name}.position' fehlt")
            if changes:
                raise ValidationError(
                    f"'{field_name}.changes' muss bei move leer sein"
                )
            return _annotated_operation(
                {
                    "op": "update_day",
                    "day_id": entity_id,
                    "patch": {},
                    "position": position,
                },
                annotations,
            )
        remove_stops = changes.pop("remove_stops", False)
        if changes:
            raise ValidationError(
                f"Nicht erlaubte Felder in '{field_name}.changes': "
                + ", ".join(sorted(changes))
            )
        return _annotated_operation(
            {
                "op": "remove_day",
                "day_id": entity_id,
                "remove_stops": remove_stops,
            },
            annotations,
        )

    if entity_type == "stop":
        if day_id is None and day_ref is None:
            raise ValidationError(
                f"'{field_name}' benötigt day_id oder day_ref"
            )
        day_keys = {"day_id": day_id, "day_ref": day_ref}
        if action == "add":
            client_id = entity_id or operation_id
            return _annotated_operation(
                {
                    "op": "add_stop",
                    **day_keys,
                    "client_id": client_id,
                    "position": position,
                    "stop": changes,
                },
                annotations,
            )
        if entity_id is None:
            raise ValidationError(f"'{field_name}.entity_id' fehlt")
        if action in {"update", "move"}:
            if action == "move" and position is None:
                raise ValidationError(f"'{field_name}.position' fehlt")
            if action == "move" and changes:
                raise ValidationError(
                    f"'{field_name}.changes' muss bei move leer sein"
                )
            return _annotated_operation(
                {
                    "op": "update_stop",
                    **day_keys,
                    "stop_id": entity_id,
                    "patch": changes,
                    "position": position,
                },
                annotations,
            )
        if changes:
            raise ValidationError(
                f"'{field_name}.changes' muss bei remove leer sein"
            )
        return _annotated_operation(
            {
                "op": "remove_stop",
                **day_keys,
                "stop_id": entity_id,
            },
            annotations,
        )

    if action == "move":
        raise ValidationError(
            f"'{field_name}' unterstützt move nicht für preference"
        )
    if entity_id is None:
        raise ValidationError(f"'{field_name}.entity_id' fehlt")
    if "preference" in changes and "text" not in changes:
        changes["text"] = changes.pop("preference")
    if raw.get("reason") is not None and "reason" not in changes:
        changes["reason"] = _string(
            raw["reason"],
            f"{field_name}.reason",
            max_length=4_000,
        )
    operation = {
        "add": "add_preference",
        "update": "update_preference",
        "remove": "remove_preference",
    }[action]
    result: dict[str, Any] = {
        "op": operation,
        "preference_id": entity_id,
        "day_id": day_id,
        "day_ref": day_ref,
    }
    if action == "add":
        result["preference"] = changes
    elif action == "update":
        result["patch"] = changes
    elif changes:
        raise ValidationError(
            f"'{field_name}.changes' muss bei remove leer sein"
        )
    return _annotated_operation(result, annotations)


def _adapt_operation_dialect(raw: Any, index: int) -> Any:
    if not isinstance(raw, dict):
        return raw
    if "op" in raw:
        if "action" in raw or "entity_type" in raw:
            raise ValidationError(
                f"'operations[{index}]' mischt op- und action-Dialekt"
            )
        return raw
    if "action" in raw or "entity_type" in raw:
        return _adapt_entity_operation(raw, index)
    return raw


def _normalize_preference_value(
    value: Any,
    field_name: str,
    *,
    partial: bool,
) -> dict[str, Any]:
    result = _object(value, field_name)
    if "preference" in result and "text" not in result:
        result["text"] = result.pop("preference")
    _check_allowed_fields(result, _PREFERENCE_VALUE_FIELDS, field_name)
    if partial and not result:
        raise ValidationError(f"'{field_name}' darf nicht leer sein")
    if not partial and "text" not in result:
        raise ValidationError(f"'{field_name}.text' fehlt")

    normalized: dict[str, Any] = {}
    text_fields = {
        "category": (100, "general"),
        "text": (4_000, None),
        "status": (100, "confirmed"),
        "notes": (4_000, ""),
        "reason": (4_000, ""),
    }
    for key, (maximum, default) in text_fields.items():
        if key not in result:
            if not partial and default is not None:
                normalized[key] = default
            continue
        normalized[key] = _string(
            result[key],
            f"{field_name}.{key}",
            max_length=maximum,
            allow_empty=key not in {"category", "text", "status"},
        )
    if "details" in result:
        normalized["details"] = _object(
            result["details"],
            f"{field_name}.details",
        )
    elif not partial:
        normalized["details"] = {}
    return normalized


def _normalize_operation(raw: Any, index: int) -> dict[str, Any]:
    field_name = f"operations[{index}]"
    if not isinstance(raw, dict):
        raise ValidationError(f"'{field_name}' muss ein JSON-Objekt sein")
    annotations = _operation_annotations(raw, field_name)
    raw = {
        key: value
        for key, value in raw.items()
        if key not in _OPERATION_ANNOTATION_FIELDS
    }
    operation = _string(
        raw.get("op"),
        f"{field_name}.op",
        max_length=100,
        allow_empty=False,
    )
    if operation not in _OPERATION_NAMES:
        raise ValidationError(
            f"Nicht unterstützte Operation in '{field_name}': {operation}"
        )

    if operation == "update_trip":
        _check_allowed_fields(raw, {"op", "patch"}, field_name)
        patch = _object(raw.get("patch"), f"{field_name}.patch")
        _check_allowed_fields(patch, _TRIP_PATCH_FIELDS, f"{field_name}.patch")
        if not patch:
            raise ValidationError(f"'{field_name}.patch' darf nicht leer sein")
        return _annotated_operation(
            {"op": operation, "patch": patch},
            annotations,
        )

    if operation == "add_day":
        excluded = {"op", "client_id", "temp_id", "position"}
        value = _extract_value(
            raw,
            container_key="day",
            allowed_fields=_DAY_VALUE_FIELDS,
            aliases={"day_date": "date"},
            excluded=excluded,
            field_name=field_name,
        )
        if raw.get("client_id") is not None and raw.get("temp_id") is not None:
            raise ValidationError(
                f"'{field_name}' darf nicht client_id und temp_id enthalten"
            )
        client_id = raw.get("client_id", raw.get("temp_id"))
        client_id = _optional_identifier(client_id, f"{field_name}.client_id")
        return _annotated_operation(
            {
                "op": operation,
                "client_id": client_id,
                "position": _positive_int_or_none(
                    raw.get("position"),
                    f"{field_name}.position",
                ),
                "day": value,
            },
            annotations,
        )

    if operation in {"update_day", "remove_day"}:
        allowed = {
            "op",
            "day_id",
            "day_ref",
            "patch",
            "position",
            "remove_stops",
        }
        _check_allowed_fields(raw, allowed, field_name)
        day_id, day_ref = _one_reference(
            raw,
            id_key="day_id",
            ref_key="day_ref",
            field_name=field_name,
        )
        result: dict[str, Any] = {
            "op": operation,
            "day_id": day_id,
            "day_ref": day_ref,
        }
        if operation == "update_day":
            patch = _object(raw.get("patch", {}), f"{field_name}.patch")
            _check_allowed_fields(patch, _DAY_VALUE_FIELDS, f"{field_name}.patch")
            if not patch and raw.get("position") is None:
                raise ValidationError(
                    f"'{field_name}' benötigt patch oder position"
                )
            result["patch"] = patch
            result["position"] = _positive_int_or_none(
                raw.get("position"),
                f"{field_name}.position",
            )
        else:
            remove_stops = raw.get("remove_stops", False)
            if not isinstance(remove_stops, bool):
                raise ValidationError(
                    f"'{field_name}.remove_stops' muss boolesch sein"
                )
            result["remove_stops"] = remove_stops
        return _annotated_operation(result, annotations)

    if operation == "add_stop":
        excluded = {
            "op",
            "day_id",
            "day_ref",
            "client_id",
            "temp_id",
            "position",
        }
        _check_allowed_fields(
            raw,
            excluded | {"stop"} | _STOP_VALUE_FIELDS | {"stop_type"},
            field_name,
        )
        day_id, day_ref = _one_reference(
            raw,
            id_key="day_id",
            ref_key="day_ref",
            field_name=field_name,
        )
        value = _extract_value(
            raw,
            container_key="stop",
            allowed_fields=_STOP_VALUE_FIELDS,
            aliases={"stop_type": "type"},
            excluded=excluded,
            field_name=field_name,
        )
        if "name" not in value:
            raise ValidationError(f"'{field_name}.stop.name' fehlt")
        if raw.get("client_id") is not None and raw.get("temp_id") is not None:
            raise ValidationError(
                f"'{field_name}' darf nicht client_id und temp_id enthalten"
            )
        client_id = raw.get("client_id", raw.get("temp_id"))
        client_id = _optional_identifier(client_id, f"{field_name}.client_id")
        return _annotated_operation(
            {
                "op": operation,
                "day_id": day_id,
                "day_ref": day_ref,
                "client_id": client_id,
                "position": _positive_int_or_none(
                    raw.get("position"),
                    f"{field_name}.position",
                ),
                "stop": value,
            },
            annotations,
        )

    if operation in {
        "add_preference",
        "update_preference",
        "remove_preference",
    }:
        allowed = {
            "op",
            "preference_id",
            "day_id",
            "day_ref",
            "preference",
            "patch",
        }
        _check_allowed_fields(raw, allowed, field_name)
        preference_id = validate_identifier(
            raw.get("preference_id"),
            f"{field_name}.preference_id",
        )
        day_id = _optional_identifier(
            raw.get("day_id"),
            f"{field_name}.day_id",
        )
        day_ref = _optional_identifier(
            raw.get("day_ref"),
            f"{field_name}.day_ref",
        )
        if day_id is not None and day_ref is not None:
            raise ValidationError(
                f"'{field_name}' darf nicht day_id und day_ref enthalten"
            )
        result = {
            "op": operation,
            "preference_id": preference_id,
            "day_id": day_id,
            "day_ref": day_ref,
        }
        if operation == "add_preference":
            result["preference"] = _normalize_preference_value(
                raw.get("preference"),
                f"{field_name}.preference",
                partial=False,
            )
        elif operation == "update_preference":
            result["patch"] = _normalize_preference_value(
                raw.get("patch"),
                f"{field_name}.patch",
                partial=True,
            )
        return _annotated_operation(result, annotations)

    allowed = {
        "op",
        "day_id",
        "day_ref",
        "stop_id",
        "stop_ref",
        "patch",
        "position",
    }
    _check_allowed_fields(raw, allowed, field_name)
    day_id, day_ref = _one_reference(
        raw,
        id_key="day_id",
        ref_key="day_ref",
        field_name=field_name,
    )
    stop_id, stop_ref = _one_reference(
        raw,
        id_key="stop_id",
        ref_key="stop_ref",
        field_name=field_name,
    )
    result = {
        "op": operation,
        "day_id": day_id,
        "day_ref": day_ref,
        "stop_id": stop_id,
        "stop_ref": stop_ref,
    }
    if operation == "update_stop":
        patch = _object(raw.get("patch", {}), f"{field_name}.patch")
        if "stop_type" in patch and "type" not in patch:
            patch["type"] = patch.pop("stop_type")
        _check_allowed_fields(patch, _STOP_VALUE_FIELDS, f"{field_name}.patch")
        if not patch and raw.get("position") is None:
            raise ValidationError(f"'{field_name}' benötigt patch oder position")
        result["patch"] = patch
        result["position"] = _positive_int_or_none(
            raw.get("position"),
            f"{field_name}.position",
        )
    return _annotated_operation(result, annotations)


def _stable_changeset_id(value: dict[str, Any]) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"changeset-{hashlib.sha256(material).hexdigest()[:20]}"


def normalize_changeset(raw: Any) -> dict[str, Any]:
    """Return a strict canonical ChangeSet or raise ValidationError."""
    if not isinstance(raw, dict):
        raise ValidationError("ChangeSet muss ein JSON-Objekt sein")
    source = deepcopy(raw)
    kind = source.get("kind", CHANGESET_KIND)
    if kind == "roadplanner_handoff":
        kind = CHANGESET_KIND
    if kind != CHANGESET_KIND:
        raise ValidationError(
            f"'kind' muss '{CHANGESET_KIND}' sein"
        )
    version = source.get("version", CHANGESET_VERSION)
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != CHANGESET_VERSION
    ):
        raise ValidationError(
            f"Nicht unterstützte ChangeSet-Version: {version}"
        )

    allowed_top = {
        "kind",
        "version",
        "changeset_id",
        "id",
        "trip_id",
        "base_revision",
        "created_at",
        "title",
        "summary",
        "apply_mode",
        "requires_confirmation",
        "operations",
        "open_questions",
        "assumptions",
        "research_notes",
        "metadata",
        "destructive",
        "automatic_eligible",
    }
    _check_allowed_fields(source, allowed_top, "changeset")
    trip_id = validate_identifier(source.get("trip_id"), "trip_id")
    base_revision = _non_negative_int(
        source.get("base_revision"),
        "base_revision",
    )
    raw_operations = source.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ValidationError("'operations' muss eine nicht-leere Liste sein")
    if len(raw_operations) > MAX_OPERATIONS:
        raise ValidationError(
            f"Ein ChangeSet darf maximal {MAX_OPERATIONS} Operationen enthalten"
        )
    adapted_operations = [
        _adapt_operation_dialect(operation, index)
        for index, operation in enumerate(raw_operations)
    ]
    operations = [
        _normalize_operation(operation, index)
        for index, operation in enumerate(adapted_operations)
    ]
    operation_ids = [
        operation["operation_id"]
        for operation in operations
        if "operation_id" in operation
    ]
    if len(operation_ids) != len(set(operation_ids)):
        raise ValidationError("operation_id muss innerhalb eines ChangeSets eindeutig sein")

    apply_mode = source.get("apply_mode", APPLY_MODE_REVIEW)
    apply_mode = _string(
        apply_mode,
        "apply_mode",
        max_length=50,
        allow_empty=False,
    ).casefold()
    if apply_mode not in _ALLOWED_APPLY_MODES:
        raise ValidationError(
            "'apply_mode' muss 'review' oder 'automatic' sein"
        )
    requires_confirmation = source.get("requires_confirmation", False)
    if not isinstance(requires_confirmation, bool):
        raise ValidationError("'requires_confirmation' muss boolesch sein")
    if requires_confirmation:
        apply_mode = APPLY_MODE_REVIEW

    metadata = _object(source.get("metadata", {}), "metadata")
    normalized: dict[str, Any] = {
        "kind": CHANGESET_KIND,
        "version": CHANGESET_VERSION,
        "trip_id": trip_id,
        "base_revision": base_revision,
        "created_at": _string(
            source.get("created_at", ""),
            "created_at",
            max_length=100,
        ),
        "title": _string(
            source.get("title", "Roadplanner-Übergabe"),
            "title",
            max_length=500,
            allow_empty=False,
        ),
        "summary": _string(
            source.get("summary", ""),
            "summary",
            max_length=MAX_SUMMARY_TEXT,
        ),
        "apply_mode": apply_mode,
        "operations": operations,
        "open_questions": _text_list(
            source.get("open_questions"),
            "open_questions",
        ),
        "assumptions": _text_list(source.get("assumptions"), "assumptions"),
        "research_notes": _text_list(
            source.get("research_notes"),
            "research_notes",
        ),
        "metadata": metadata,
    }
    if (
        source.get("changeset_id") is not None
        and source.get("id") is not None
        and source["changeset_id"] != source["id"]
    ):
        raise ValidationError("'changeset_id' und 'id' widersprechen sich")
    requested_id = source.get("changeset_id", source.get("id"))
    normalized["changeset_id"] = (
        validate_identifier(requested_id, "changeset_id")
        if requested_id is not None
        else _stable_changeset_id(normalized)
    )
    normalized["destructive"] = any(
        operation["op"] in _DESTRUCTIVE_OPERATIONS
        for operation in operations
    )
    normalized["automatic_eligible"] = (
        normalized["apply_mode"] == APPLY_MODE_AUTOMATIC
        and not normalized["open_questions"]
    )

    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > MAX_CHANGESET_BYTES:
        raise ValidationError("ChangeSet ist größer als 512 KiB")
    return normalized


def _insert_index(position: int | None, current_length: int) -> int:
    if position is None:
        return current_length
    return min(position - 1, current_length)


def _resolve_day_id(
    operation: dict[str, Any],
    candidate: TripState,
    day_refs: dict[str, str],
) -> str:
    day_id = operation.get("day_id")
    if day_id is None:
        day_ref = operation.get("day_ref")
        day_id = day_refs.get(day_ref, day_ref)
    assert isinstance(day_id, str)
    if day_id not in candidate.day_documents:
        raise TripNotFoundError(f"Reisetag nicht gefunden: {day_id}")
    return day_id


def _resolve_stop_id(
    operation: dict[str, Any],
    document: dict[str, Any],
    stop_refs: dict[str, str],
) -> tuple[str, int]:
    stop_id = operation.get("stop_id")
    if stop_id is None:
        stop_ref = operation.get("stop_ref")
        stop_id = stop_refs.get(stop_ref, stop_ref)
    assert isinstance(stop_id, str)
    index = next(
        (
            item_index
            for item_index, stop in enumerate(document["stops"])
            if stop["id"] == stop_id
        ),
        None,
    )
    if index is None:
        raise TripNotFoundError(f"Stopp nicht gefunden: {stop_id}")
    return stop_id, index


def _generated_entity_id(
    prefix: str,
    changeset_id: str,
    client_id: str | None,
    operation_index: int,
) -> str:
    if client_id is None:
        return _stable_id(
            prefix,
            {"changeset_id": changeset_id, "operation": operation_index},
        )
    return _stable_id(
        prefix,
        {"changeset_id": changeset_id, "client_id": client_id},
    )


def _operation_error(index: int, operation: str, err: Exception) -> ValidationError:
    return ValidationError(
        f"ChangeSet-Operation {index + 1} ('{operation}') ist ungültig: {err}"
    )


def _preference_target(
    operation: dict[str, Any],
    candidate: TripState,
    day_refs: dict[str, str],
) -> tuple[list[dict[str, Any]], str | None]:
    day_id = operation.get("day_id")
    day_ref = operation.get("day_ref")
    if day_id is None and day_ref is None:
        details = candidate.trip_document["trip"].setdefault("details", {})
        if not isinstance(details, dict):
            raise ValidationError("trip.details muss ein JSON-Objekt sein")
        preferences = details.setdefault("planning_preferences", [])
        if not isinstance(preferences, list):
            raise ValidationError(
                "trip.details.planning_preferences muss eine Liste sein"
            )
        return preferences, None

    resolved_day_id = _resolve_day_id(operation, candidate, day_refs)
    document = candidate.day_documents[resolved_day_id]
    details = document["day"].setdefault("details", {})
    if not isinstance(details, dict):
        raise ValidationError("day.details muss ein JSON-Objekt sein")
    preferences = details.setdefault("planning_preferences", [])
    if not isinstance(preferences, list):
        raise ValidationError(
            "day.details.planning_preferences muss eine Liste sein"
        )
    return preferences, resolved_day_id


def _preference_index(
    preferences: list[dict[str, Any]],
    preference_id: str,
) -> int | None:
    if len(preferences) > MAX_PREFERENCES_PER_SCOPE:
        raise ValidationError(
            "planning_preferences enthält zu viele Einträge"
        )
    seen_ids: set[str] = set()
    result: int | None = None
    for index, preference in enumerate(preferences):
        if not isinstance(preference, dict):
            raise ValidationError(
                "planning_preferences darf nur JSON-Objekte enthalten"
            )
        current_id = validate_identifier(
            preference.get("id"),
            f"planning_preferences[{index}].id",
        )
        if current_id in seen_ids:
            raise ValidationError(
                f"Doppelte Planungspräferenz-ID: {current_id}"
            )
        seen_ids.add(current_id)
        if current_id == preference_id:
            result = index
    return result


def _stored_preference(
    preference_id: str,
    value: dict[str, Any],
    *,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    normalized = _normalize_preference_value(
        value,
        "preference",
        partial=False,
    )
    return {
        "id": preference_id,
        **normalized,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def execute_changeset(
    previous: TripState,
    changeset: dict[str, Any],
) -> ChangeSetExecution:
    """Apply a normalized ChangeSet to a clone without writing files."""
    candidate = previous.clone()
    removed_files: list[str] = []
    operation_results: list[dict[str, Any]] = []
    day_refs: dict[str, str] = {}
    stop_refs: dict[str, str] = {}
    existing_day_ids = set(candidate.day_documents)
    existing_stop_ids = {
        stop["id"]
        for document in candidate.day_documents.values()
        for stop in document["stops"]
    }
    now = utc_now_iso()

    for index, operation in enumerate(changeset["operations"]):
        operation_name = operation["op"]
        try:
            result: dict[str, Any] = {
                "index": index + 1,
                "op": operation_name,
            }
            for annotation in _OPERATION_ANNOTATION_FIELDS:
                if annotation in operation:
                    result[annotation] = operation[annotation]
            if operation_name == "update_trip":
                candidate.trip_document["trip"].update(
                    deepcopy(operation["patch"])
                )
                candidate.trip_document = normalize_trip_document(
                    candidate.trip_document,
                    expected_trip_id=previous.trip_id,
                    fallback_timestamp=(
                        previous.trip_document["metadata"]["created_at"]
                    ),
                )

            elif operation_name == "add_day":
                if len(candidate.trip_document["days"]) >= MAX_DAYS:
                    raise ValidationError(
                        f"Maximal {MAX_DAYS} Reisetage werden unterstützt"
                    )
                client_id = operation.get("client_id")
                if client_id is not None and client_id in day_refs:
                    raise ValidationError(
                        f"Doppelte client_id für Reisetag: {client_id}"
                    )
                day_id = _generated_entity_id(
                    "day",
                    changeset["changeset_id"],
                    client_id,
                    index,
                )
                if day_id in existing_day_ids:
                    raise ValidationError(
                        f"Erzeugte Tages-ID existiert bereits: {day_id}"
                    )
                value = deepcopy(operation["day"])
                raw_document = {
                    "schema_version": DAY_SCHEMA_VERSION,
                    "day": {
                        "id": day_id,
                        "date": value.get("date"),
                        "title": value.get("title")
                        or (
                            f"Tag {value['date']}"
                            if value.get("date")
                            else "Neuer Reisetag"
                        ),
                        "start": value.get("start", ""),
                        "end": value.get("end", ""),
                        "distance_km": value.get("distance_km"),
                        "drive_minutes": value.get("drive_minutes"),
                        "status": value.get("status", "planned"),
                        "notes": value.get("notes", ""),
                        "details": value.get("details", {}),
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
                refs = candidate.trip_document["days"]
                insert_at = _insert_index(operation.get("position"), len(refs))
                refs.insert(
                    insert_at,
                    {"id": day_id, "file": f"days/{day_id}.json"},
                )
                existing_day_ids.add(day_id)
                if client_id is not None:
                    day_refs[client_id] = day_id
                result.update({"day_id": day_id, "position": insert_at + 1})

            elif operation_name == "update_day":
                day_id = _resolve_day_id(operation, candidate, day_refs)
                document = candidate.day_documents[day_id]
                before = _without_audit_fields(document["day"])
                document["day"].update(deepcopy(operation["patch"]))
                normalized = normalize_day_document(
                    document,
                    fallback_id=day_id,
                    fallback_timestamp=document["day"]["created_at"],
                )
                if _without_audit_fields(normalized["day"]) != before:
                    normalized["day"]["updated_at"] = now
                candidate.day_documents[day_id] = normalized
                position = operation.get("position")
                if position is not None:
                    refs = candidate.trip_document["days"]
                    old_index = next(
                        item_index
                        for item_index, ref in enumerate(refs)
                        if ref["id"] == day_id
                    )
                    ref = refs.pop(old_index)
                    refs.insert(_insert_index(position, len(refs)), ref)
                result["day_id"] = day_id

            elif operation_name == "remove_day":
                day_id = _resolve_day_id(operation, candidate, day_refs)
                document = candidate.day_documents[day_id]
                if document["stops"] and not operation["remove_stops"]:
                    raise ValidationError(
                        "Der Reisetag enthält Stopps. Zum Löschen muss "
                        "remove_stops=true gesetzt sein."
                    )
                ref = next(
                    ref
                    for ref in candidate.trip_document["days"]
                    if ref["id"] == day_id
                )
                candidate.trip_document["days"] = [
                    item
                    for item in candidate.trip_document["days"]
                    if item["id"] != day_id
                ]
                removed = candidate.day_documents.pop(day_id)
                removed_files.append(ref["file"])
                existing_day_ids.discard(day_id)
                for stop in removed["stops"]:
                    existing_stop_ids.discard(stop["id"])
                result.update(
                    {
                        "day_id": day_id,
                        "removed_stop_count": len(removed["stops"]),
                    }
                )

            elif operation_name == "add_stop":
                day_id = _resolve_day_id(operation, candidate, day_refs)
                document = candidate.day_documents[day_id]
                if len(document["stops"]) >= MAX_STOPS_PER_DAY:
                    raise ValidationError(
                        "Ein Reisetag darf maximal "
                        f"{MAX_STOPS_PER_DAY} Stopps enthalten"
                    )
                client_id = operation.get("client_id")
                if client_id is not None and client_id in stop_refs:
                    raise ValidationError(
                        f"Doppelte client_id für Stopp: {client_id}"
                    )
                stop_id = _generated_entity_id(
                    "stop",
                    changeset["changeset_id"],
                    client_id,
                    index,
                )
                if stop_id in existing_stop_ids:
                    raise ValidationError(
                        f"Erzeugte Stopp-ID existiert bereits: {stop_id}"
                    )
                value = deepcopy(operation["stop"])
                raw_stop = {
                    "id": stop_id,
                    "name": value.get("name"),
                    "type": value.get("type", "waypoint"),
                    "arrival_time": value.get("arrival_time"),
                    "departure_time": value.get("departure_time"),
                    "location": value.get("location", {}),
                    "notes": value.get("notes", ""),
                    "details": value.get("details", {}),
                    "created_at": now,
                    "updated_at": now,
                }
                stop = normalize_stop(
                    raw_stop,
                    index=len(document["stops"]),
                    fallback_timestamp=now,
                )
                insert_at = _insert_index(
                    operation.get("position"),
                    len(document["stops"]),
                )
                document["stops"].insert(insert_at, stop)
                reindex_explicit_positions(document["stops"])
                document["day"]["updated_at"] = now
                existing_stop_ids.add(stop_id)
                if client_id is not None:
                    stop_refs[client_id] = stop_id
                result.update(
                    {
                        "day_id": day_id,
                        "stop_id": stop_id,
                        "position": insert_at + 1,
                    }
                )

            elif operation_name == "update_stop":
                day_id = _resolve_day_id(operation, candidate, day_refs)
                document = candidate.day_documents[day_id]
                stop_id, old_index = _resolve_stop_id(
                    operation,
                    document,
                    stop_refs,
                )
                raw_stop = deepcopy(document["stops"][old_index])
                before = _without_audit_fields(raw_stop)
                raw_stop.update(deepcopy(operation["patch"]))
                raw_stop["id"] = stop_id
                normalized = normalize_stop(
                    raw_stop,
                    index=old_index,
                    fallback_timestamp=raw_stop["created_at"],
                )
                changed_fields = _without_audit_fields(normalized) != before
                if changed_fields:
                    normalized["updated_at"] = now
                document["stops"][old_index] = normalized
                position = operation.get("position")
                if position is not None:
                    moved = document["stops"].pop(old_index)
                    document["stops"].insert(
                        _insert_index(position, len(document["stops"])),
                        moved,
                    )
                reindex_explicit_positions(document["stops"])
                if changed_fields or position is not None:
                    document["day"]["updated_at"] = now
                result.update({"day_id": day_id, "stop_id": stop_id})

            elif operation_name == "remove_stop":
                day_id = _resolve_day_id(operation, candidate, day_refs)
                document = candidate.day_documents[day_id]
                stop_id, old_index = _resolve_stop_id(
                    operation,
                    document,
                    stop_refs,
                )
                document["stops"].pop(old_index)
                reindex_explicit_positions(document["stops"])
                document["day"]["updated_at"] = now
                existing_stop_ids.discard(stop_id)
                result.update({"day_id": day_id, "stop_id": stop_id})

            elif operation_name in {
                "add_preference",
                "update_preference",
                "remove_preference",
            }:
                preference_changed = False
                preferences, day_id = _preference_target(
                    operation,
                    candidate,
                    day_refs,
                )
                preference_id = operation["preference_id"]
                existing_index = _preference_index(
                    preferences,
                    preference_id,
                )
                if operation_name == "add_preference":
                    if existing_index is not None:
                        raise ValidationError(
                            "Planungspräferenz existiert bereits: "
                            f"{preference_id}"
                        )
                    if len(preferences) >= MAX_PREFERENCES_PER_SCOPE:
                        raise ValidationError(
                            "Maximal 100 Planungspräferenzen pro Ebene werden "
                            "unterstützt"
                        )
                    preferences.append(
                        _stored_preference(
                            preference_id,
                            operation["preference"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    preference_changed = True
                elif operation_name == "update_preference":
                    if existing_index is None:
                        raise TripNotFoundError(
                            "Planungspräferenz nicht gefunden: "
                            f"{preference_id}"
                        )
                    existing = preferences[existing_index]
                    existing_created = str(existing.get("created_at") or now)
                    before = {
                        key: deepcopy(value)
                        for key, value in existing.items()
                        if key not in {"id", "created_at", "updated_at"}
                    }
                    merged = {**before, **deepcopy(operation["patch"])}
                    normalized_business = _normalize_preference_value(
                        merged,
                        "preference",
                        partial=False,
                    )
                    preference_changed = normalized_business != before
                    updated_at = (
                        now
                        if preference_changed
                        else str(existing.get("updated_at") or existing_created)
                    )
                    preferences[existing_index] = _stored_preference(
                        preference_id,
                        normalized_business,
                        created_at=existing_created,
                        updated_at=updated_at,
                    )
                else:
                    if existing_index is None:
                        raise TripNotFoundError(
                            "Planungspräferenz nicht gefunden: "
                            f"{preference_id}"
                        )
                    preferences.pop(existing_index)
                    preference_changed = True
                if day_id is not None and preference_changed:
                    candidate.day_documents[day_id]["day"]["updated_at"] = now
                result.update(
                    {
                        "preference_id": preference_id,
                        "scope": "day" if day_id is not None else "trip",
                    }
                )
                if day_id is not None:
                    result["day_id"] = day_id

            operation_results.append(result)
        except (TripNotFoundError, ValidationError) as err:
            raise _operation_error(index, operation_name, err) from err

    candidate.trip_document = normalize_trip_document(
        candidate.trip_document,
        expected_trip_id=previous.trip_id,
        fallback_timestamp=previous.trip_document["metadata"]["created_at"],
    )
    normalized_days: dict[str, dict[str, Any]] = {}
    seen_stop_ids: set[str] = set()
    for ref in candidate.trip_document["days"]:
        day_id = ref["id"]
        document = candidate.day_documents.get(day_id)
        if document is None:
            raise ValidationError(
                f"ChangeSet hinterlässt fehlende Tagesdaten für {day_id}"
            )
        normalized = normalize_day_document(
            document,
            fallback_id=day_id,
            fallback_timestamp=candidate.trip_document["metadata"]["created_at"],
        )
        for stop in normalized["stops"]:
            if stop["id"] in seen_stop_ids:
                raise ValidationError(
                    f"ChangeSet erzeugt doppelte Stopp-ID: {stop['id']}"
                )
            seen_stop_ids.add(stop["id"])
        normalized_days[day_id] = normalized
    candidate.day_documents = normalized_days

    return ChangeSetExecution(
        candidate=candidate,
        removed_files=sorted(set(removed_files)),
        operation_results=operation_results,
        id_map={"days": day_refs, "stops": stop_refs},
    )


def changeset_summary(changeset: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, stable description for UI, services, and sensors."""
    operation_counts: dict[str, int] = {}
    for operation in changeset["operations"]:
        name = operation["op"]
        operation_counts[name] = operation_counts.get(name, 0) + 1
    return {
        "changeset_id": changeset["changeset_id"],
        "trip_id": changeset["trip_id"],
        "base_revision": changeset["base_revision"],
        "title": changeset["title"],
        "summary": changeset["summary"],
        "apply_mode": changeset["apply_mode"],
        "automatic_eligible": changeset["automatic_eligible"],
        "destructive": changeset["destructive"],
        "operation_count": len(changeset["operations"]),
        "operation_counts": operation_counts,
        "open_question_count": len(changeset["open_questions"]),
    }
