"""Pure text/ID sanitization helpers and constants shared across the
assistant's basket, compile and orchestration modules.

Nothing here depends on Home Assistant, the provider, or any other
Roadplanner module - these are leaf-level primitives.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

OVERNIGHT_STOP_TYPES = {
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
}

_ISO_DATE_IN_TEXT = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_GERMAN_DATE_IN_TEXT = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})\b")

_ALLOWED_ENTITY_TYPES = {"trip", "day", "stop", "preference"}

_BASKET_ENTITY_ALIASES = {
    "trip": "trip",
    "route": "trip",
    "itinerary": "trip",
    "journey": "trip",
    "travel": "trip",
    "roadbook": "trip",
    "project": "trip",
    "vehicle": "trip",
    "crew": "trip",
    "traveler": "trip",
    "travellers": "trip",
    "task": "trip",
    "todo": "trip",
    "day": "day",
    "stage": "day",
    "leg": "day",
    "date": "day",
    "stop": "stop",
    "place": "stop",
    "poi": "stop",
    "booking": "stop",
    "transport": "stop",
    "activity": "stop",
    "overnight": "stop",
    "accommodation": "stop",
    "campsite": "stop",
    "camping": "stop",
    "stellplatz": "stop",
    "restaurant": "stop",
    "parking": "stop",
    "sightseeing": "stop",
    "attraction": "stop",
    "ferry": "stop",
    "preference": "preference",
    "constraint": "preference",
    "rule": "preference",
    "setting": "preference",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _draft_identity(value: dict[str, Any]) -> str:
    """Return a stable semantic identity for one volatile draft item."""
    material = {
        "action": str(value.get("action") or "").casefold(),
        "entity_type": str(value.get("entity_type") or "").casefold(),
        "target_id": str(value.get("target_id") or "").casefold(),
        "day_id": str(value.get("day_id") or "").casefold(),
        "day_date": str(value.get("day_date") or "").casefold(),
        "place_query": " ".join(str(value.get("place_query") or "").casefold().split()),
        "summary": " ".join(str(value.get("summary") or "").casefold().split()),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text_fingerprint(value: str) -> str:
    """Return a stable fingerprint for an idempotent client request."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _clean_text(value: Any, *, maximum: int = 20_000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _normalize_text_items(
    value: Any,
    *,
    maximum_items: int = 100,
    maximum_text: int = 2_000,
) -> tuple[list[str], int]:
    """Return a bounded, de-duplicated list of human-readable strings.

    Gemini can ignore the response schema in MIME-only compatibility mode and
    return a single string, nested arrays, or small note objects instead of the
    requested string array. Iterating a string directly would turn every
    character into one list item and can exceed the ChangeSet limit. This
    normalizer accepts those harmless dialect differences while retaining the
    strict ChangeSet limit.
    """

    flattened: list[Any] = []

    def collect(item: Any) -> None:
        if item is None:
            return
        if isinstance(item, str):
            flattened.append(item)
            return
        if isinstance(item, (list, tuple, set)):
            for child in item:
                collect(child)
            return
        if isinstance(item, dict):
            for key in (
                "text",
                "note",
                "question",
                "assumption",
                "summary",
                "title",
                "description",
            ):
                if item.get(key) is not None:
                    collect(item.get(key))
                    return
            try:
                flattened.append(
                    json.dumps(
                        item,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                )
            except (TypeError, ValueError):
                flattened.append(str(item))
            return
        flattened.append(str(item))

    collect(value)
    result: list[str] = []
    seen: set[str] = set()
    for item in flattened:
        cleaned = _clean_text(item, maximum=maximum_text)
        if not cleaned:
            continue
        identity = cleaned.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(cleaned)

    omitted = max(0, len(result) - maximum_items)
    if omitted:
        result = result[:maximum_items]
    return result, omitted


def _clean_reply(value: Any, *, maximum: int = 30_000) -> str:
    """Normalize a conversational reply while preserving readable paragraphs."""
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()).strip() for line in raw.split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if cleaned and not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False
    return "\n".join(cleaned).strip()[:maximum]
