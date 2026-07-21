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
