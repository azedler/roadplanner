"""The assistant's conversational "change basket": repair coarse provider
intents into basket items, and store/compact per-user/trip session state.

Nothing here talks to a provider directly - it normalizes basket-item drafts
(from chat replies or imports) and manages the volatile in-memory session
(messages, basket, diagnostics, request-idempotency cache) that the
orchestration layer in assistant.py reads and writes.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any

from .assistant_shared import (
    _ALLOWED_ENTITY_TYPES,
    _BASKET_ENTITY_ALIASES,
    _GERMAN_DATE_IN_TEXT,
    _ISO_DATE_IN_TEXT,
    _clean_reply,
    _clean_text,
    _draft_identity,
    _identifier,
    _utc_now_iso,
)
from .canonical_day import canonical_roadbook_stops
from .roadplanner import ValidationError

MAX_SESSION_MESSAGES = 80
MAX_MEMORY_SUMMARY_CHARACTERS = 12_000
MAX_DIAGNOSTIC_RECORDS = 25
MAX_BASKET_ITEMS = 50
MAX_REQUEST_CACHE = 12
_BASKET_MANAGEMENT_MARKERS = (
    "änderungskorb",
    "aenderungskorb",
    "vormerkung",
    "vorgemerkt",
    "entwurf löschen",
    "entwurf loeschen",
    "vorschlag aus dem korb",
    "draft",
)
_ROADBOOK_CHANGE_MARKERS = (
    "aus der planung",
    "aus dem plan",
    "nicht mehr",
    "stattdessen",
    "ersetzen",
    "ersetze",
    "entfernen",
    "entferne",
    "löschen",
    "loeschen",
    "streichen",
    "streich",
    "rausnehmen",
    "ändern",
    "aendern",
    "anpassen",
)
_STOP_CHANGE_MARKERS = (
    "stopp",
    "ziel",
    "stellplatz",
    "camping",
    "restaurant",
    "parkplatz",
    "fähre",
    "faehre",
    "fahrrad",
    "tour",
    "wander",
    "aktivität",
    "aktivitaet",
    "besichtigung",
    "übernacht",
    "uebernacht",
    "chalet",
    "see",
)
_DAY_CHANGE_MARKERS = (
    "tagesplan",
    "reisetag",
    "etappe",
    "morgen",
    "heute",
)
_PREFERENCE_CHANGE_MARKERS = (
    "präferenz",
    "praeferenz",
    "bevorzug",
    "regel",
    "grundsätzlich",
    "grundsaetzlich",
)



_BASKET_ACTION_ALIASES = {
    "add": "add",
    "create": "add",
    "insert": "add",
    "new": "add",
    "hinzufuegen": "add",
    "hinzufügen": "add",
    "update": "update",
    "edit": "update",
    "change": "update",
    "set": "update",
    "modify": "update",
    "aendern": "update",
    "ändern": "update",
    "remove": "remove",
    "delete": "remove",
    "discard": "remove",
    "loeschen": "remove",
    "löschen": "remove",
    "plan": "plan",
    "propose": "plan",
    "schedule": "plan",
    "prepare": "plan",
    "import": "plan",
    "apply": "plan",
    "commit": "plan",
    "takeover": "plan",
    "uebernehmen": "plan",
    "übernehmen": "plan",
    "entwurf": "plan",
    "planen": "plan",
}


def _first_nonempty_text(*values: Any, maximum: int = 2_000) -> str:
    for value in values:
        cleaned = _clean_text(value, maximum=maximum)
        if cleaned:
            return cleaned
    return ""


def _infer_basket_entity_type(raw: dict[str, Any], values: dict[str, Any]) -> str:
    type_hint = _clean_text(raw.get("type"), maximum=50).casefold()
    explicit = _clean_text(
        raw.get("entity_type")
        or raw.get("entity")
        or raw.get("scope")
        or raw.get("object_type")
        or (type_hint if type_hint in _BASKET_ENTITY_ALIASES else ""),
        maximum=50,
    ).casefold()
    if explicit:
        normalized = _BASKET_ENTITY_ALIASES.get(explicit)
        if normalized:
            return normalized

    if raw.get("place_query") or any(
        key in values
        for key in {
            "name",
            "type",
            "arrival_time",
            "departure_time",
        }
    ):
        return "stop"
    if raw.get("day_id") or raw.get("day_date") or any(
        key in values
        for key in {
            "date",
            "start",
            "end",
            "distance_km",
            "drive_minutes",
        }
    ):
        return "day"
    if any(key in values for key in {"category", "text"}):
        return "preference"
    return "trip"


def _normalize_basket_item(
    raw: Any,
    *,
    delta_note: str = "",
) -> tuple[dict[str, Any] | None, list[str]]:
    """Repair one coarse provider intent without inventing trip data.

    The basket stores conversational intentions, not final ChangeSet operations.
    Therefore a semantically useful but structurally incomplete provider item can
    safely be normalized to a generic plan intent and compiled against the live
    Roadbook only when the user presses ``Änderungen prüfen``.
    """

    repairs: list[str] = []
    if isinstance(raw, str):
        text = _clean_text(raw, maximum=2_000)
        if not text:
            return None, repairs
        raw = {
            "action": "plan",
            "entity_type": "trip",
            "summary": text,
            "reason": "Vom Benutzer im Gespräch als Planungsauftrag bestätigt.",
            "values": {"notes": text},
        }
        repairs.append("Textvorschlag in Planungsabsicht umgewandelt")
    if not isinstance(raw, dict):
        return None, repairs

    source = dict(raw)
    nested_values = source.get("values")
    if not isinstance(nested_values, dict):
        nested_values = source.get("changes")
        if isinstance(nested_values, dict):
            repairs.append("changes als values übernommen")
        else:
            nested_values = {}

    values = dict(nested_values)
    allowed_top_level_values = {
        "title",
        "status",
        "start_date",
        "end_date",
        "date",
        "start",
        "end",
        "distance_km",
        "drive_minutes",
        "notes",
        "name",
        "type",
        "arrival_time",
        "departure_time",
        "category",
        "text",
    }
    for key in allowed_top_level_values:
        if key not in values and source.get(key) not in (None, ""):
            values[key] = source.get(key)
            repairs.append(f"{key} in values verschoben")

    entity_type = _infer_basket_entity_type(source, values)
    type_hint = _clean_text(source.get("type"), maximum=50).casefold()
    explicit_entity = _clean_text(
        source.get("entity_type")
        or source.get("entity")
        or source.get("scope")
        or source.get("object_type")
        or (type_hint if type_hint in _BASKET_ENTITY_ALIASES else ""),
        maximum=50,
    ).casefold()
    if not explicit_entity:
        repairs.append(f"entity_type als {entity_type} abgeleitet")
    elif explicit_entity != entity_type:
        repairs.append(f"entity_type {explicit_entity} auf {entity_type} abgebildet")

    raw_action = _clean_text(
        source.get("action") or source.get("operation") or source.get("verb"),
        maximum=50,
    ).casefold()
    action = _BASKET_ACTION_ALIASES.get(raw_action)
    if not action:
        if source.get("target_id"):
            action = "update"
        elif entity_type == "stop" and (
            source.get("place_query") or values.get("name")
        ):
            action = "add"
        elif entity_type == "day" and values.get("date"):
            action = "add"
        elif entity_type == "preference" and values.get("text"):
            action = "add"
        else:
            action = "plan"
        repairs.append(f"action als {action} abgeleitet")
    elif raw_action != action:
        repairs.append(f"action {raw_action} auf {action} abgebildet")

    # An unresolvable remove intent must remain a plan intent until compilation
    # can compare it with the live Roadbook and ask a precise question.
    if action == "remove" and not source.get("target_id"):
        action = "plan"
        repairs.append("remove ohne Ziel als Planungsabsicht zurückgestellt")

    summary = _first_nonempty_text(
        source.get("summary"),
        source.get("title"),
        source.get("description"),
        source.get("intent"),
        source.get("change"),
        source.get("task"),
        source.get("reason"),
        values.get("text"),
        values.get("name"),
        values.get("notes"),
        source.get("place_query"),
        delta_note,
        maximum=500,
    )
    if not summary:
        return None, repairs
    if not _clean_text(source.get("summary"), maximum=500):
        repairs.append("Kurzbeschreibung aus vorhandenem Inhalt erzeugt")

    reason = _first_nonempty_text(
        source.get("reason"),
        source.get("rationale"),
        source.get("why"),
        delta_note,
        "Vom Benutzer im Gespräch als Änderung oder Planungsauftrag bestätigt.",
        maximum=1_000,
    )

    normalized: dict[str, Any] = {
        "action": action,
        "entity_type": entity_type,
        "summary": summary,
        "reason": reason,
        "values": values,
    }
    for key in {
        "id",
        "target_id",
        "day_id",
        "day_date",
        "position",
        "place_query",
    }:
        if source.get(key) not in (None, ""):
            normalized[key] = source.get(key)
    return normalized, repairs


def _roadbook_removal_draft(
    item_id: str,
    context: dict[str, Any] | None,
    *,
    delta_note: str = "",
) -> dict[str, Any] | None:
    """Translate an exact Roadbook ID from ``remove_ids`` into a safe draft.

    ``remove_ids`` belongs to the volatile change basket.  Gemini sometimes
    places a canonical Roadbook day/stop/preference ID there when the user
    asks to replace or delete an already stored plan.  If and only if the ID
    exists exactly in the current Roadbook catalog, convert it to an explicit
    ``action=remove`` basket item.  Unknown IDs remain harmless stale basket
    removals and are never guessed.
    """

    target = _clean_text(item_id, maximum=200)
    if not target or not isinstance(context, dict):
        return None

    catalog = context.get("id_catalog")
    catalog = catalog if isinstance(catalog, dict) else {}
    day_ids = {
        str(value)
        for value in catalog.get("day_ids", [])
        if value not in (None, "")
    }
    stop_owners: dict[str, str] = {}
    for day_id, values in (catalog.get("stop_ids_by_day") or {}).items():
        if not isinstance(values, list):
            continue
        for value in values:
            if value not in (None, ""):
                stop_owners[str(value)] = str(day_id)
    preference_ids = {
        str(value)
        for value in catalog.get("preference_ids", [])
        if value not in (None, "")
    }

    # Fallback for contexts created before the full ID catalog was available.
    day_titles: dict[str, str] = {}
    stop_names: dict[str, tuple[str, str]] = {}
    for day in context.get("days", []):
        if not isinstance(day, dict):
            continue
        day_id = _clean_text(day.get("id"), maximum=200)
        if not day_id:
            continue
        day_ids.add(day_id)
        day_titles[day_id] = _first_nonempty_text(
            day.get("title"), day.get("date"), day_id, maximum=300
        )
        for stop in canonical_roadbook_stops(day):
            if not isinstance(stop, dict):
                continue
            stop_id = _clean_text(stop.get("id"), maximum=200)
            if not stop_id:
                continue
            stop_owners.setdefault(stop_id, day_id)
            stop_names[stop_id] = (
                day_id,
                _first_nonempty_text(stop.get("name"), stop_id, maximum=300),
            )
        details = day.get("details")
        if isinstance(details, dict):
            for preference in details.get("planning_preferences", []):
                if isinstance(preference, dict) and preference.get("id"):
                    preference_ids.add(str(preference.get("id")))
    trip = context.get("trip")
    trip_details = trip.get("details") if isinstance(trip, dict) else None
    if isinstance(trip_details, dict):
        for preference in trip_details.get("planning_preferences", []):
            if isinstance(preference, dict) and preference.get("id"):
                preference_ids.add(str(preference.get("id")))

    reason = _first_nonempty_text(
        delta_note,
        "Vom Benutzer im Gespräch ausdrücklich zum Entfernen beziehungsweise Ersetzen bestätigt.",
        maximum=1_000,
    )
    if target in stop_owners:
        owner = stop_owners[target]
        name = stop_names.get(target, (owner, target))[1]
        return {
            "action": "remove",
            "entity_type": "stop",
            "target_id": target,
            "day_id": owner,
            "summary": f"Vorhandenen Stopp entfernen: {name}",
            "reason": reason,
            "values": {},
        }
    if target in day_ids:
        label = day_titles.get(target, target)
        return {
            "action": "remove",
            "entity_type": "day",
            "target_id": target,
            "summary": f"Vorhandenen Reisetag entfernen: {label}",
            "reason": reason,
            "values": {},
        }
    if target in preference_ids:
        return {
            "action": "remove",
            "entity_type": "preference",
            "target_id": target,
            "summary": "Vorhandene Reisepräferenz entfernen",
            "reason": reason,
            "values": {},
        }
    return None


def _fallback_day_date(value: str) -> str:
    """Extract one explicit date for a coarse replacement intent."""

    match = _ISO_DATE_IN_TEXT.search(value)
    if match:
        return match.group(1)
    match = _GERMAN_DATE_IN_TEXT.search(value)
    if not match:
        return ""
    day_value, month_value, year_value = match.groups()
    try:
        return f"{int(year_value):04d}-{int(month_value):02d}-{int(day_value):02d}"
    except ValueError:
        return ""


def _fallback_entity_type(value: str) -> str:
    """Choose only a broad basket scope; never invent a Roadbook ID."""

    lowered = " ".join(str(value or "").casefold().split())
    if any(marker in lowered for marker in _PREFERENCE_CHANGE_MARKERS):
        return "preference"
    if any(marker in lowered for marker in _STOP_CHANGE_MARKERS):
        return "stop"
    if any(marker in lowered for marker in _DAY_CHANGE_MARKERS) or re.search(
        r"\btag\s*\d+\b", lowered
    ):
        return "day"
    return "trip"


def _repair_stale_remove_delta(
    delta: dict[str, Any],
    *,
    basket: list[dict[str, Any]],
    roadbook_context: dict[str, Any] | None,
    user_text: str,
) -> tuple[dict[str, Any], bool]:
    """Preserve a Roadbook replacement hidden behind stale ``remove_ids``.

    ``remove_ids`` addresses only current volatile basket drafts.  After a Home
    Assistant restart Gemini can still refer to an old draft ID from the
    conversation while the basket is already empty.  If no current basket draft
    and no exact Roadbook ID match, a clearly stated replacement request is kept
    as one coarse ``plan`` intent.  The later review compilation resolves real
    IDs against the latest Roadbook and remains review-only.
    """

    if not isinstance(delta, dict):
        return {}, False
    repaired = deepcopy(delta)
    raw_ids = repaired.get("remove_ids", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list):
        return repaired, False
    requested: list[str] = []
    for raw in raw_ids:
        if not isinstance(raw, str):
            continue
        item_id = _clean_text(raw, maximum=200)
        if item_id and item_id not in requested:
            requested.append(item_id)
    if not requested:
        return repaired, False

    existing_ids = {
        _clean_text(item.get("id"), maximum=200)
        for item in basket
        if isinstance(item, dict) and _clean_text(item.get("id"), maximum=200)
    }
    target_ids = {
        _clean_text(item.get("target_id"), maximum=200)
        for item in basket
        if isinstance(item, dict)
        and _clean_text(item.get("target_id"), maximum=200)
    }
    if any(item_id in existing_ids or item_id in target_ids for item_id in requested):
        return repaired, False
    if any(
        _roadbook_removal_draft(item_id, roadbook_context) is not None
        for item_id in requested
    ):
        return repaired, False

    raw_items = repaired.get("add_or_update", [])
    if isinstance(raw_items, dict):
        has_items = bool(raw_items)
    elif isinstance(raw_items, list):
        has_items = bool(raw_items)
    elif isinstance(raw_items, str):
        has_items = bool(raw_items.strip())
    else:
        has_items = False
    if has_items:
        return repaired, False

    cleaned_user_text = _clean_text(user_text, maximum=5_000)
    lowered = cleaned_user_text.casefold()
    if not cleaned_user_text:
        return repaired, False
    if any(marker in lowered for marker in _BASKET_MANAGEMENT_MARKERS):
        return repaired, False
    if not any(marker in lowered for marker in _ROADBOOK_CHANGE_MARKERS):
        return repaired, False

    entity_type = _fallback_entity_type(cleaned_user_text)
    prefix = {
        "stop": "Bestehende Ziel- oder Stoppplanung ändern",
        "day": "Bestehenden Tagesplan ändern",
        "preference": "Bestehende Reisepräferenz ändern",
        "trip": "Bestehende Reiseplanung ändern",
    }[entity_type]
    fallback: dict[str, Any] = {
        "action": "plan",
        "entity_type": entity_type,
        "summary": _clean_text(f"{prefix}: {cleaned_user_text}", maximum=500),
        "reason": (
            "Der Benutzer hat eine bestehende Roadbook-Planung widerrufen, "
            "ersetzt oder konkret geändert."
        ),
        "values": {"notes": cleaned_user_text},
    }
    day_date = _fallback_day_date(cleaned_user_text)
    if day_date:
        fallback["day_date"] = day_date
    repaired["add_or_update"] = [fallback]
    note = _clean_text(repaired.get("note"), maximum=800)
    repair_note = (
        "Veraltete remove_ids wurden nicht als Roadbook-Löschung interpretiert; "
        "die Benutzerabsicht wurde als sichere Planungsabsicht erhalten."
    )
    repaired["note"] = f"{note} {repair_note}".strip()[:1_000]
    return repaired, True


_BASKET_STATUS_TARGETS = (
    "änderungskorb",
    "aenderungskorb",
    "roadbook",
    "offenen aufgaben",
    "planung",
)
_BASKET_STATUS_ACTIONS = (
    "vorgemerkt",
    "gepackt",
    "aufgenommen",
    "eingetragen",
    "notiert",
    "gespeichert",
    "übernommen",
    "uebernommen",
    "vorbereitet",
    "hinzugefügt",
    "hinzugefuegt",
    "entfernt",
    "geloescht",
    "gelöscht",
    "ersetzt",
    "angepasst",
    "geaendert",
    "geändert",
    "aus der planung genommen",
)
_NEGATIVE_BASKET_MARKERS = (
    "keine ",
    "kein ",
    "nicht ",
    "nichts ",
    "noch keine",
)


def _strip_unverified_basket_claims(value: str) -> tuple[str, bool]:
    """Remove provider claims about server-side basket persistence.

    Gemini only proposes a basket delta. Whether a draft survives validation
    and is stored is known exclusively by the Roadplanner server.
    """

    text = _clean_reply(value, maximum=30_000)
    if not text:
        return "", False

    fragments = re.split(r"(?<=[.!?])(?:\s+|\n+)", text)
    kept: list[str] = []
    removed = False
    for fragment in fragments:
        sentence = fragment.strip()
        if not sentence:
            continue
        lowered = " ".join(sentence.casefold().split())
        has_target = any(marker in lowered for marker in _BASKET_STATUS_TARGETS)
        has_action = any(marker in lowered for marker in _BASKET_STATUS_ACTIONS)
        negative = any(marker in lowered for marker in _NEGATIVE_BASKET_MARKERS)
        if has_target and has_action and not negative:
            removed = True
            continue
        kept.append(sentence)

    cleaned = " ".join(kept).strip()
    return cleaned, removed


def _basket_status_text(change_count: int, total_count: int) -> str:
    """Return the authoritative server-side basket status."""

    if change_count <= 0:
        return "Änderungskorb: Es wurde tatsächlich keine Änderung vorgemerkt."
    noun = "Änderung" if change_count == 1 else "Änderungen"
    return (
        f"Änderungskorb: {change_count} {noun} wurden tatsächlich verarbeitet. "
        f"Der Korb enthält jetzt {total_count}."
    )


def _deep_without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _deep_without_none(child)
            for key, child in value.items()
            if child is not None and child != ""
        }
    if isinstance(value, list):
        return [_deep_without_none(child) for child in value if child is not None]
    return value


BASKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "add_or_update": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "maxLength": 200,
                        "description": (
                            "Nur die exakte ID einer bereits in CURRENT_CHANGE_BASKET "
                            "vorhandenen Vormerkung, wenn diese konkretisiert wird."
                        ),
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "update", "remove", "plan"],
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["trip", "day", "stop", "preference"],
                    },
                    "summary": {"type": "string", "maxLength": 500},
                    "target_id": {
                        "type": "string",
                        "maxLength": 200,
                        "description": (
                            "Exakte bestehende Roadbook-ID für update/remove. Für neue "
                            "Objekte leer lassen."
                        ),
                    },
                    "day_id": {"type": "string", "maxLength": 200},
                    "day_date": {"type": "string", "maxLength": 20},
                    "position": {"type": "integer", "minimum": 1, "maximum": 500},
                    "place_query": {"type": "string", "maxLength": 500},
                    "reason": {"type": "string", "maxLength": 1_000},
                    "values": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "maxLength": 500},
                            "status": {"type": "string", "maxLength": 100},
                            "start_date": {"type": "string", "maxLength": 20},
                            "end_date": {"type": "string", "maxLength": 20},
                            "date": {"type": "string", "maxLength": 20},
                            "start": {"type": "string", "maxLength": 500},
                            "end": {"type": "string", "maxLength": 500},
                            "distance_km": {"type": "number", "minimum": 0},
                            "drive_minutes": {"type": "integer", "minimum": 0},
                            "notes": {"type": "string", "maxLength": 5_000},
                            "name": {"type": "string", "maxLength": 500},
                            "type": {"type": "string", "maxLength": 100},
                            "arrival_time": {"type": "string", "maxLength": 20},
                            "departure_time": {"type": "string", "maxLength": 20},
                            "category": {"type": "string", "maxLength": 200},
                            "text": {"type": "string", "maxLength": 2_000},
                        },
                        "additionalProperties": False,
                    },
                },
                "required": ["action", "entity_type", "summary", "reason", "values"],
                "additionalProperties": False,
            },
        },
        "remove_ids": {
            "type": "array",
            "maxItems": 20,
            "description": (
                "Ausschließlich exakte IDs aus CURRENT_CHANGE_BASKET, um eine "
                "flüchtige Vormerkung zu widerrufen. Niemals Roadbook-IDs, Namen "
                "oder erfundene IDs verwenden. Roadbook-Inhalte werden über "
                "add_or_update mit action=remove und target_id entfernt."
            ),
            "items": {"type": "string", "maxLength": 200},
        },
        "note": {"type": "string", "maxLength": 1_000},
    },
    "required": ["add_or_update", "remove_ids", "note"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class AssistantSession:
    """One volatile conversation, rolling memory, and draft basket."""

    user_id: str
    trip_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    basket: list[dict[str, Any]] = field(default_factory=list)
    memory_summary: str = ""
    total_message_count: int = 0
    compacted_message_count: int = 0
    compaction_count: int = 0
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    request_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_cache_fingerprints: dict[str, str] = field(default_factory=dict)
    request_cache_order: list[str] = field(default_factory=list)
    usage_totals: dict[str, int] = field(
        default_factory=lambda: {
            "logical_calls": 0,
            "prompt_tokens": 0,
            "candidate_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "thought_tokens": 0,
        }
    )
    last_briefing_date: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    last_chat_monotonic: float = 0.0


class AssistantSessionStore:
    """Volatile, trip- and user-scoped assistant state with rolling compaction."""

    def __init__(self, *, max_history: int = 24) -> None:
        self.max_history = max(8, min(int(max_history), MAX_SESSION_MESSAGES))
        self._sessions: dict[tuple[str, str], AssistantSession] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def _key(self, user_id: str, trip_id: str) -> tuple[str, str]:
        return (str(user_id or "unknown"), str(trip_id))

    def session(self, user_id: str, trip_id: str) -> AssistantSession:
        key = self._key(user_id, trip_id)
        session = self._sessions.get(key)
        if session is None:
            session = AssistantSession(user_id=key[0], trip_id=key[1])
            self._sessions[key] = session
        return session

    def lock(self, user_id: str, trip_id: str) -> asyncio.Lock:
        key = self._key(user_id, trip_id)
        return self._locks.setdefault(key, asyncio.Lock())

    def clear(self, user_id: str, trip_id: str) -> AssistantSession:
        key = self._key(user_id, trip_id)
        session = AssistantSession(user_id=key[0], trip_id=key[1])
        self._sessions[key] = session
        return session

    @staticmethod
    def _summary_line(message: dict[str, Any]) -> str:
        role = "Benutzer" if message.get("role") == "user" else "Roadplanner"
        content = _clean_text(message.get("content"), maximum=900)
        if not content:
            return ""
        return f"- {role}: {content}"

    def _compact(self, session: AssistantSession) -> None:
        """Compact older messages instead of silently discarding context."""
        if len(session.messages) <= self.max_history:
            return
        keep_recent = max(6, self.max_history // 2)
        compact_count = max(1, len(session.messages) - keep_recent)
        old = session.messages[:compact_count]
        session.messages = session.messages[compact_count:]
        lines = [self._summary_line(item) for item in old if item.get("kind") == "message"]
        lines = [line for line in lines if line]
        if lines:
            addition = "\n".join(lines)
            if session.memory_summary:
                combined = session.memory_summary.rstrip() + "\n" + addition
            else:
                combined = addition
            if len(combined) > MAX_MEMORY_SUMMARY_CHARACTERS:
                combined = (
                    "[Ältere Gesprächsteile gekürzt]\n"
                    + combined[-(MAX_MEMORY_SUMMARY_CHARACTERS - 36):]
                )
            session.memory_summary = combined
        session.compacted_message_count += len(old)
        session.compaction_count += 1

    def snapshot(self, user_id: str, trip_id: str) -> dict[str, Any]:
        session = self.session(user_id, trip_id)
        return {
            "trip_id": trip_id,
            "messages": deepcopy(session.messages),
            "basket": deepcopy(session.basket),
            "basket_count": len(session.basket),
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "persistent": False,
            "memory": {
                "summary_available": bool(session.memory_summary),
                "summary_characters": len(session.memory_summary),
                "recent_message_count": len(session.messages),
                "total_message_count": session.total_message_count,
                "compacted_message_count": session.compacted_message_count,
                "compaction_count": session.compaction_count,
                "max_recent_messages": self.max_history,
            },
            "last_briefing_date": session.last_briefing_date,
            "usage": deepcopy(session.usage_totals),
            "request_cache_count": len(session.request_cache),
        }

    def append_message(
        self,
        session: AssistantSession,
        *,
        role: str,
        content: str,
        sources: list[dict[str, str]] | None = None,
        kind: str = "message",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message = {
            "id": _identifier("msg"),
            "role": role,
            "content": str(content)[:30_000],
            "created_at": _utc_now_iso(),
            "sources": list(sources or [])[:12],
            "kind": kind,
            "metadata": deepcopy(metadata or {}),
        }
        session.messages.append(message)
        session.total_message_count += 1
        self._compact(session)
        session.updated_at = _utc_now_iso()
        return message

    def record_diagnostic(
        self,
        session: AssistantSession,
        record: dict[str, Any],
    ) -> None:
        sanitized = {
            key: deepcopy(value)
            for key, value in record.items()
            if key not in {"prompt", "context", "api_key"}
        }
        session.diagnostics.append(sanitized)
        if len(session.diagnostics) > MAX_DIAGNOSTIC_RECORDS:
            session.diagnostics = session.diagnostics[-MAX_DIAGNOSTIC_RECORDS:]
        usage = sanitized.get("usage") if isinstance(sanitized.get("usage"), dict) else {}
        provider = sanitized.get("provider") if isinstance(sanitized.get("provider"), dict) else {}
        if sanitized.get("status") == "ok" and (
            usage or provider.get("attempt_count") is not None
        ):
            session.usage_totals["logical_calls"] += 1
        if usage:
            token_map = {
                "promptTokenCount": "prompt_tokens",
                "candidatesTokenCount": "candidate_tokens",
                "totalTokenCount": "total_tokens",
                "cachedContentTokenCount": "cached_tokens",
                "thoughtsTokenCount": "thought_tokens",
            }
            for source, target in token_map.items():
                value = usage.get(source)
                if isinstance(value, int) and not isinstance(value, bool):
                    session.usage_totals[target] += max(0, value)
        session.updated_at = _utc_now_iso()

    def cached_result(
        self,
        session: AssistantSession,
        client_request_id: str,
        *,
        request_fingerprint: str = "",
    ) -> dict[str, Any] | None:
        request_id = _clean_text(client_request_id, maximum=200)
        if not request_id:
            return None
        value = session.request_cache.get(request_id)
        if value is None:
            return None
        previous_fingerprint = session.request_cache_fingerprints.get(request_id, "")
        if (
            request_fingerprint
            and previous_fingerprint
            and request_fingerprint != previous_fingerprint
        ):
            raise ValidationError(
                "Diese Assistenten-Anfrage-ID wurde bereits für eine andere "
                "Nachricht verwendet. Bitte die Nachricht neu senden."
            )
        return deepcopy(value) if isinstance(value, dict) else None

    def cache_result(
        self,
        session: AssistantSession,
        client_request_id: str,
        result: dict[str, Any],
        *,
        request_fingerprint: str = "",
    ) -> None:
        request_id = _clean_text(client_request_id, maximum=200)
        if not request_id:
            return
        if request_id not in session.request_cache:
            session.request_cache_order.append(request_id)
        session.request_cache[request_id] = deepcopy(result)
        if request_fingerprint:
            session.request_cache_fingerprints[request_id] = request_fingerprint
        while len(session.request_cache_order) > MAX_REQUEST_CACHE:
            expired = session.request_cache_order.pop(0)
            session.request_cache.pop(expired, None)
            session.request_cache_fingerprints.pop(expired, None)
        session.updated_at = _utc_now_iso()

    def update_draft(
        self,
        session: AssistantSession,
        draft_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        draft_id = _clean_text(draft_id, maximum=200)
        if not draft_id:
            raise ValidationError("Entwurfs-ID fehlt")
        index = next(
            (idx for idx, item in enumerate(session.basket) if item.get("id") == draft_id),
            None,
        )
        if index is None:
            raise ValidationError("Vorgemerkte Änderung wurde nicht gefunden")
        current = deepcopy(session.basket[index])
        allowed_scalar = {
            "summary": 500,
            "reason": 1_000,
            "target_id": 200,
            "day_id": 200,
            "day_date": 20,
            "place_query": 500,
        }
        for key, maximum in allowed_scalar.items():
            if key in patch:
                value = _clean_text(patch.get(key), maximum=maximum)
                if value:
                    current[key] = value
                else:
                    current.pop(key, None)
        if "position" in patch:
            position = patch.get("position")
            if position in (None, ""):
                current.pop("position", None)
            elif isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise ValidationError("Position muss eine positive Ganzzahl sein")
            else:
                current["position"] = min(position, 500)
        if "values" in patch:
            values = patch.get("values")
            if not isinstance(values, dict):
                raise ValidationError("Entwurfswerte müssen ein JSON-Objekt sein")
            allowed_values = set(
                BASKET_SCHEMA["properties"]["add_or_update"]["items"]
                ["properties"]["values"]["properties"]
            )
            cleaned_values: dict[str, Any] = {}
            for key, value in values.items():
                if key not in allowed_values or value in (None, ""):
                    continue
                if key in {"distance_km", "drive_minutes"}:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    if value < 0:
                        continue
                    cleaned_values[key] = (
                        int(value) if key == "drive_minutes" else float(value)
                    )
                else:
                    cleaned = _clean_text(
                        value, maximum=5_000 if key == "notes" else 2_000
                    )
                    if cleaned:
                        cleaned_values[key] = cleaned
            current["values"] = cleaned_values
        if not _clean_text(current.get("summary"), maximum=500):
            raise ValidationError("Eine Vormerkung benötigt eine Kurzbeschreibung")
        current["updated_at"] = _utc_now_iso()
        session.basket[index] = current
        session.updated_at = _utc_now_iso()
        return deepcopy(current)

    def apply_delta(
        self,
        session: AssistantSession,
        delta: dict[str, Any],
        *,
        roadbook_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply one provider delta and report what was actually accepted.

        Provider output is untrusted, but the basket deliberately stores coarse
        conversational intentions rather than final ChangeSet operations. A
        useful intent is therefore repaired locally whenever possible instead of
        being discarded merely because Gemini omitted a technical field. Final
        IDs, revisions and schema validation still happen only at review time.
        """

        before_count = len(session.basket)
        rejected: list[dict[str, Any]] = []
        repaired: list[dict[str, Any]] = []
        delta = delta if isinstance(delta, dict) else {}
        delta_note = _clean_text(delta.get("note"), maximum=1_000)

        raw_remove_ids = delta.get("remove_ids", [])
        if isinstance(raw_remove_ids, str):
            raw_remove_ids = [raw_remove_ids]
            repaired.append({
                "kind": "delta",
                "repairs": ["einzelne remove_id in Liste umgewandelt"],
            })
        elif not isinstance(raw_remove_ids, list):
            rejected.append({
                "kind": "delta",
                "reason": "remove_ids ist weder eine Liste noch eine ID",
            })
            raw_remove_ids = []
        requested_remove_ids = [
            _clean_text(item, maximum=200)
            for item in raw_remove_ids
            if isinstance(item, str) and _clean_text(item, maximum=200)
        ]
        existing_ids = {
            str(item.get("id"))
            for item in session.basket
            if isinstance(item, dict) and item.get("id")
        }
        actual_remove_ids = {
            item for item in requested_remove_ids if item in existing_ids
        }
        converted_remove_drafts: list[dict[str, Any]] = []
        ignored_remove_ids: list[str] = []
        for item_id in requested_remove_ids:
            if item_id in existing_ids:
                continue
            roadbook_draft = _roadbook_removal_draft(
                item_id,
                roadbook_context,
                delta_note=delta_note,
            )
            if roadbook_draft is not None:
                converted_remove_drafts.append(roadbook_draft)
                repaired.append({
                    "kind": "remove",
                    "id": item_id[:200],
                    "repairs": [
                        "Roadbook-ID aus remove_ids in action=remove mit target_id umgewandelt"
                    ],
                })
            else:
                # Removing an already absent volatile draft is idempotent.  It
                # must not reject otherwise valid additions from the same delta.
                ignored_remove_ids.append(item_id)

        basket = [
            item
            for item in session.basket
            if str(item.get("id") or "") not in actual_remove_ids
        ]
        by_id = {str(item.get("id")): index for index, item in enumerate(basket)}
        by_identity = {
            _draft_identity(item): index
            for index, item in enumerate(basket)
            if isinstance(item, dict)
        }
        added: list[dict[str, Any]] = []
        updated: list[dict[str, Any]] = []

        raw_items = delta.get("add_or_update", [])
        if raw_items == [] and any(
            key in delta
            for key in {
                "summary",
                "title",
                "description",
                "reason",
                "action",
                "entity_type",
                "values",
                "changes",
            }
        ):
            raw_items = [delta]
            repaired.append({
                "kind": "delta",
                "repairs": ["direkten Änderungsvorschlag erkannt"],
            })
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
            repaired.append({
                "kind": "delta",
                "repairs": ["einzelnen Änderungsvorschlag in Liste umgewandelt"],
            })
        elif not isinstance(raw_items, list):
            # Some JSON-only fallback responses return one intent directly at
            # basket_delta root. Preserve it if it contains meaningful fields.
            if any(
                key in delta
                for key in {
                    "summary",
                    "title",
                    "description",
                    "reason",
                    "action",
                    "entity_type",
                    "values",
                    "changes",
                }
            ):
                raw_items = [delta]
                repaired.append({
                    "kind": "delta",
                    "repairs": ["direkten Änderungsvorschlag erkannt"],
                })
            else:
                rejected.append({
                    "kind": "delta",
                    "reason": "add_or_update enthält keine erkennbare Änderung",
                })
                raw_items = []

        # A provider may use remove_ids for an exact canonical Roadbook ID.
        # Preserve the user's delete/replace intent as a normal basket item,
        # while avoiding duplicates if add_or_update already contains it.
        existing_remove_targets = {
            _clean_text(item.get("target_id"), maximum=200)
            for item in raw_items
            if isinstance(item, dict)
            and _BASKET_ACTION_ALIASES.get(
                _clean_text(
                    item.get("action")
                    or item.get("operation")
                    or item.get("verb"),
                    maximum=50,
                ).casefold()
            ) == "remove"
            and _clean_text(item.get("target_id"), maximum=200)
        }
        for converted in converted_remove_drafts:
            target_id = _clean_text(converted.get("target_id"), maximum=200)
            if target_id and target_id in existing_remove_targets:
                continue
            raw_items.append(converted)
            if target_id:
                existing_remove_targets.add(target_id)

        allowed_values = set(
            BASKET_SCHEMA["properties"]["add_or_update"]["items"]
            ["properties"]["values"]["properties"]
        )

        for raw_index, raw in enumerate(raw_items):
            normalized, repair_notes = _normalize_basket_item(
                raw,
                delta_note=delta_note,
            )
            if normalized is None:
                rejected.append({
                    "kind": "item",
                    "index": raw_index,
                    "reason": "Kein verständlicher Änderungsinhalt vorhanden",
                })
                continue
            if repair_notes:
                repaired.append({
                    "kind": "item",
                    "index": raw_index,
                    "repairs": repair_notes[:12],
                })

            action = _clean_text(normalized.get("action"), maximum=20).casefold()
            entity_type = _clean_text(
                normalized.get("entity_type"), maximum=30
            ).casefold()
            # These values are guaranteed by _normalize_basket_item. Keep the
            # defensive check because provider data is never trusted.
            if action not in {"add", "update", "remove", "plan"}:
                action = "plan"
                repaired.append({
                    "kind": "item",
                    "index": raw_index,
                    "repairs": ["unbekannte Aktion auf plan zurückgesetzt"],
                })
            if entity_type not in _ALLOWED_ENTITY_TYPES:
                entity_type = "trip"
                repaired.append({
                    "kind": "item",
                    "index": raw_index,
                    "repairs": ["unbekannten Bereich auf trip zurückgesetzt"],
                })

            item: dict[str, Any] = {
                "action": action,
                "entity_type": entity_type,
                "summary": _clean_text(normalized.get("summary"), maximum=500),
                "reason": _clean_text(normalized.get("reason"), maximum=1_000),
                "values": {},
            }
            for key, maximum in {
                "id": 200,
                "target_id": 200,
                "day_id": 200,
                "day_date": 20,
                "place_query": 500,
            }.items():
                value = _clean_text(normalized.get(key), maximum=maximum)
                if value:
                    item[key] = value
            position = normalized.get("position")
            if (
                isinstance(position, int)
                and not isinstance(position, bool)
                and position > 0
            ):
                item["position"] = min(position, 500)
            values = (
                normalized.get("values")
                if isinstance(normalized.get("values"), dict)
                else {}
            )
            for key, value in values.items():
                if key not in allowed_values or value in (None, ""):
                    continue
                if key in {"distance_km", "drive_minutes"}:
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        continue
                    if value < 0:
                        continue
                    item["values"][key] = (
                        int(value) if key == "drive_minutes" else float(value)
                    )
                else:
                    cleaned = _clean_text(
                        value, maximum=5_000 if key == "notes" else 2_000
                    )
                    if cleaned:
                        item["values"][key] = cleaned

            if not item["summary"]:
                rejected.append({
                    "kind": "item",
                    "index": raw_index,
                    "reason": "Kurzbeschreibung konnte nicht abgeleitet werden",
                })
                continue
            if not item["reason"]:
                item["reason"] = (
                    "Vom Benutzer im Gespräch als Änderung oder "
                    "Planungsauftrag bestätigt."
                )

            identity = _draft_identity(item)
            item_id = _clean_text(item.get("id"), maximum=200)
            existing_index = by_id.get(item_id) if item_id else None
            if existing_index is None:
                existing_index = by_identity.get(identity)
            if existing_index is not None:
                existing = basket[existing_index]
                item_id = str(existing.get("id") or item_id)
                item["created_at"] = existing.get("created_at") or _utc_now_iso()
            elif not item_id:
                item_id = f"draft-{identity[:20]}"
                item["created_at"] = _utc_now_iso()
            else:
                item["created_at"] = _utc_now_iso()
            item["id"] = item_id
            item["updated_at"] = _utc_now_iso()

            if existing_index is not None:
                basket[existing_index] = item
                by_id[item_id] = existing_index
                by_identity[identity] = existing_index
                updated.append(item)
            else:
                if len(basket) >= MAX_BASKET_ITEMS:
                    rejected.append({
                        "kind": "item",
                        "index": raw_index,
                        "reason": "Maximale Größe des Änderungskorbs erreicht",
                    })
                    continue
                index = len(basket)
                by_id[item_id] = index
                by_identity[identity] = index
                basket.append(item)
                added.append(item)

        session.basket = basket
        session.updated_at = _utc_now_iso()
        actual_change_count = len(added) + len(updated) + len(actual_remove_ids)
        requested_count = (
            len(raw_items) + len(actual_remove_ids) + len(ignored_remove_ids)
        )
        return {
            "added": deepcopy(added),
            "updated": deepcopy(updated),
            "removed_ids": sorted(actual_remove_ids),
            "ignored_remove_ids": sorted(set(ignored_remove_ids)),
            "rejected": deepcopy(rejected[:25]),
            "repaired": deepcopy(repaired[:25]),
            "note": delta_note,
            "before_count": before_count,
            "after_count": len(basket),
            "requested_count": requested_count,
            "added_count": len(added),
            "updated_count": len(updated),
            "removed_count": len(actual_remove_ids),
            "ignored_remove_count": len(set(ignored_remove_ids)),
            "converted_remove_count": len(converted_remove_drafts),
            "rejected_count": len(rejected),
            "repaired_count": len(repaired),
            "actual_change_count": actual_change_count,
            "changed": bool(actual_change_count),
            "delta_valid": not any(
                item.get("kind") == "delta" for item in rejected
            ),
        }


