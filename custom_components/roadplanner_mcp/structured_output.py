"""Tolerant normalization for provider structured-output responses.

The assistant still validates the canonical Roadplanner contracts downstream.
This module only removes harmless provider presentation differences such as
Markdown fences, explanatory text around JSON, and a bare list returned for an
object schema whose primary field is an array.
"""

from __future__ import annotations

import json
import re
from typing import Any

_CODE_FENCE_RE = re.compile(
    r"```(?:json|javascript|js)?\s*(?P<body>.*?)\s*```",
    re.IGNORECASE | re.DOTALL,
)
_ARRAY_FIELD_PRIORITY = (
    "operations",
    "changes",
    "items",
    "options",
    "drafts",
    "add_or_update",
    "results",
)
_MAX_PARSE_DEPTH = 3


class StructuredOutputError(ValueError):
    """Raised when a provider response cannot become one JSON object."""


def _array_field(schema: dict[str, Any]) -> str | None:
    if not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    array_fields = [
        str(name)
        for name, definition in properties.items()
        if isinstance(definition, dict) and definition.get("type") == "array"
    ]
    if not array_fields:
        return None
    for name in _ARRAY_FIELD_PRIORITY:
        if name in array_fields:
            return name
    return array_fields[0] if len(array_fields) == 1 else None


def _candidate_texts(text: str) -> list[str]:
    stripped = text.strip().lstrip("\ufeff")
    candidates: list[str] = []
    if stripped:
        candidates.append(stripped)
    for match in _CODE_FENCE_RE.finditer(stripped):
        body = match.group("body").strip()
        if body and body not in candidates:
            candidates.append(body)
    return candidates


def _raw_values(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    values: list[Any] = []
    for candidate in _candidate_texts(text):
        try:
            values.append(json.loads(candidate))
            continue
        except (TypeError, ValueError):
            pass
        for index, character in enumerate(candidate):
            if character not in "[{\"":
                continue
            try:
                value, _end = decoder.raw_decode(candidate[index:])
            except (TypeError, ValueError):
                continue
            values.append(value)
            break
    return values


def _coerce_object(value: Any, schema: dict[str, Any], depth: int = 0) -> tuple[dict[str, Any], str]:
    if isinstance(value, dict):
        return value, "object"
    if depth >= _MAX_PARSE_DEPTH:
        raise StructuredOutputError("maximum structured-output normalization depth exceeded")
    if isinstance(value, str):
        for nested in _raw_values(value):
            try:
                normalized, mode = _coerce_object(nested, schema, depth + 1)
                return normalized, f"string_{mode}"
            except StructuredOutputError:
                continue
        raise StructuredOutputError("JSON string did not contain an object")
    if isinstance(value, list):
        field = _array_field(schema)
        if field:
            return {field: value}, f"list_wrapped_{field}"
        if len(value) == 1 and isinstance(value[0], dict):
            return value[0], "single_object_list"
        raise StructuredOutputError("JSON array cannot be mapped to the requested object schema")
    raise StructuredOutputError("provider returned neither an object nor a compatible array")



_CHANGE_CONTAINER_KEYS = ("changes", "patch", "values")
_CHANGE_FIELD_KEYS = ("field", "key", "path")
_CHANGE_VALUE_KEYS = ("value", "new_value", "newValue")
_OPERATION_WRAPPER_KEYS = {
    "action",
    "entity_type",
    "entity-type",
    "entityType",
    "operation_id",
    "operation-id",
    "operationId",
}
_OPERATION_NON_CHANGE_KEYS = {
    "action",
    "entity_type",
    "entity-type",
    "entityType",
    "operation_id",
    "operation-id",
    "operationId",
    "reason",
    "summary",
}
_OPERATION_METADATA_KEYS = {
    "entity_id",
    "entity-id",
    "entityId",
    "target_id",
    "target-id",
    "targetId",
    "stop_id",
    "stop-id",
    "stopId",
    "stop_ref",
    "stop-ref",
    "stopRef",
    "day_id",
    "day-id",
    "dayId",
    "day_ref",
    "day-ref",
    "dayRef",
    "preference_id",
    "preference-id",
    "preferenceId",
    "client_id",
    "client-id",
    "clientId",
    "temp_id",
    "temp-id",
    "tempId",
    "source_day_id",
    "source-day-id",
    "sourceDayId",
    "source_stop_id",
    "source-stop-id",
    "sourceStopId",
    "place_query",
    "place-query",
    "placeQuery",
    "position",
}
_MAX_CHANGE_DEPTH = 4


def _merge_change_mappings(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge one provider change fragment without silently choosing conflicts."""

    for key, value in source.items():
        name = str(key)
        if name in target and target[name] != value:
            raise StructuredOutputError(
                f"conflicting values for changes.{name}"
            )
        target[name] = value


def _change_field_record(value: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize one unambiguous field/value or simple JSON-Patch record."""

    field_name: str | None = None
    for key in _CHANGE_FIELD_KEYS:
        raw = value.get(key)
        if raw is None:
            continue
        candidate = str(raw).strip()
        if not candidate:
            continue
        if field_name and field_name != candidate:
            raise StructuredOutputError("conflicting change field names")
        field_name = candidate
    if not field_name:
        return None

    has_value = False
    field_value: Any = None
    for key in _CHANGE_VALUE_KEYS:
        if key not in value:
            continue
        candidate = value.get(key)
        if has_value and candidate != field_value:
            raise StructuredOutputError(
                f"conflicting values for change field {field_name}"
            )
        has_value = True
        field_value = candidate
    if not has_value:
        return None

    operation = str(value.get("op") or value.get("operation") or "").strip().casefold()
    if operation and operation not in {"add", "replace", "set", "update"}:
        raise StructuredOutputError(
            f"unsupported JSON-patch operation for changes: {operation}"
        )

    if field_name.startswith("/"):
        field_name = field_name[1:].replace("~1", "/").replace("~0", "~")
    if not field_name or "/" in field_name or "." in field_name:
        raise StructuredOutputError(
            "only top-level change fields can be normalized"
        )
    return {field_name: field_value}


def _coerce_changes_value(
    value: Any,
    *,
    allow_scalar_empty: bool,
    depth: int,
) -> tuple[dict[str, Any], str]:
    if depth > _MAX_CHANGE_DEPTH:
        raise StructuredOutputError(
            "maximum changes normalization depth exceeded"
        )

    if value is None:
        return {}, "null_empty"

    if isinstance(value, dict):
        field_record = _change_field_record(value)
        if field_record is not None:
            return field_record, "field_value_object"

        wrapper_keys = [key for key in _CHANGE_CONTAINER_KEYS if key in value]
        operation_wrapper = bool(set(value) & _OPERATION_WRAPPER_KEYS)
        if operation_wrapper and not wrapper_keys:
            raise StructuredOutputError(
                "operation object was nested where a changes object was expected"
            )

        if wrapper_keys:
            merged: dict[str, Any] = {}
            modes: list[str] = []
            for key in wrapper_keys:
                fragment, mode = _coerce_changes_value(
                    value.get(key),
                    allow_scalar_empty=allow_scalar_empty,
                    depth=depth + 1,
                )
                _merge_change_mappings(merged, fragment)
                modes.append(f"{key}_{mode}")

            # Preserve only fields that can safely be handled by the existing
            # assistant alias/lifting layer. Operation-control keys are ignored
            # because the outer operation remains authoritative.
            for key, child in value.items():
                if key in wrapper_keys or key in _OPERATION_NON_CHANGE_KEYS:
                    continue
                if operation_wrapper and key not in _OPERATION_METADATA_KEYS:
                    continue
                _merge_change_mappings(merged, {str(key): child})
            return merged, "wrapped_" + "+".join(modes)

        return {str(key): child for key, child in value.items()}, "object"

    if isinstance(value, list):
        if not value:
            return {}, "empty_list"
        operation_wrappers = [
            item
            for item in value
            if isinstance(item, dict) and bool(set(item) & _OPERATION_WRAPPER_KEYS)
        ]
        if len(value) > 1 and operation_wrappers:
            raise StructuredOutputError(
                "multiple operations were nested inside one changes field"
            )
        merged: dict[str, Any] = {}
        modes: list[str] = []
        for item in value:
            fragment, mode = _coerce_changes_value(
                item,
                allow_scalar_empty=allow_scalar_empty,
                depth=depth + 1,
            )
            _merge_change_mappings(merged, fragment)
            modes.append(mode)
        distinct_modes = "+".join(dict.fromkeys(modes))
        return merged, f"list_merged_{distinct_modes}"

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}, "empty_string"
        last_error: StructuredOutputError | None = None
        for nested in _raw_values(stripped):
            try:
                mapping, mode = _coerce_changes_value(
                    nested,
                    allow_scalar_empty=allow_scalar_empty,
                    depth=depth + 1,
                )
                return mapping, f"string_{mode}"
            except StructuredOutputError as err:
                last_error = err
        if allow_scalar_empty:
            return {}, "discarded_explanatory_string"
        raise last_error or StructuredOutputError(
            "changes string did not contain a JSON object"
        )

    if allow_scalar_empty:
        return {}, f"discarded_{type(value).__name__}"
    raise StructuredOutputError(
        f"changes value of type {type(value).__name__} cannot be normalized"
    )


def normalize_changes_mapping(
    value: Any,
    *,
    allow_scalar_empty: bool = False,
) -> tuple[dict[str, Any], str]:
    """Return one safe changes object plus the normalization mode.

    Gemini can ignore a response schema in compatibility mode and return a
    list of change fragments, a field/value list, a simple JSON-Patch list, or
    a JSON-encoded string. Roadplanner normalizes only lossless top-level
    representations. It never guesses domain values, object IDs, or locations.

    ``allow_scalar_empty`` is reserved for ``remove`` and ``move`` operations,
    where business changes are intentionally empty and explanatory provider
    text can be discarded safely.
    """

    return _coerce_changes_value(
        value,
        allow_scalar_empty=allow_scalar_empty,
        depth=0,
    )

def parse_structured_object(text: str, schema: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return one object plus the normalization mode used.

    The function never invents domain fields. A bare list is wrapped only when
    the target schema exposes one unambiguous array property.
    """
    if not isinstance(text, str) or not text.strip():
        raise StructuredOutputError("provider returned empty structured output")
    last_error: StructuredOutputError | None = None
    for value in _raw_values(text):
        try:
            return _coerce_object(value, schema)
        except StructuredOutputError as err:
            last_error = err
    raise last_error or StructuredOutputError("no JSON value found in provider output")
