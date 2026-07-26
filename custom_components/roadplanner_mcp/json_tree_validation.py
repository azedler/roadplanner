"""Recursive bounded JSON-tree validation for Roadplanner's "details" extension objects."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .json_io import ValidationError

MAX_STRING_LENGTH = 100_000
MAX_DETAILS_DEPTH = 12
MAX_DETAILS_ITEMS = 20_000


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
