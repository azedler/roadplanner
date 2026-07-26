"""Deterministic, size-bounded JSON projection for model and UI responses.

No domain knowledge lives here - a single recursive helper used by several
downstream Roadplanner modules (routing metrics, trip/day/stop projections,
handoff context export) to keep responses within safe size limits.
"""

from __future__ import annotations

from typing import Any


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
