"""Conversational, review-only Roadplanner assistant.

The assistant reads the selected trip directly from Home Assistant on every
request. Chat and draft changes are stored only in memory and are isolated by
Home Assistant user and trip. Pressing "Änderungen prüfen" compiles the draft
against the latest active-trip revision and places a normal ChangeSet in the
existing review inbox. It never applies changes automatically.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import logging
import re
import time
from typing import Any
from uuid import uuid4

from homeassistant.util import dt as dt_util

from .assistant_context import AssistantContextBuilder
from .canonical_day import (
    canonical_day_stops,
    canonical_roadbook_stops,
    location_status,
)
from .assistant_plugins import (
    AssistantPluginRegistry,
    GeocodingAssistantPlugin,
)
from .assistant_prompt import (
    AUTONOMY_INSTRUCTIONS,
    CHAT_SYSTEM_PROMPT,
    COMPILE_SYSTEM_PROMPT,
    COPILOT_SYSTEM_PROMPT,
    PROVIDER_TEST_SYSTEM_PROMPT,
    json_context,
)
from .assistant_provider import (
    AssistantJsonResult,
    AssistantProvider,
    AssistantTextResult,
)
from .assistant_compile import (
    COMPILE_SCHEMA,
    _ALLOWED_ACTIONS,
    _ALLOWED_CHANGE_FIELDS,
    _ALLOWED_OPERATION_FIELDS,
    _ALLOWED_STOP_TYPES,
    _CHANGE_FIELDS_BY_ENTITY,
    _SERVER_CONTROLLED_OPERATION_FIELDS,
    _bounded_context,
    _normalize_compiled_day_reference,
    _normalize_compiled_operation_aliases,
    _prepare_compiled_operation_batch,
)
from .assistant_shared import (
    OVERNIGHT_STOP_TYPES,
    _ALLOWED_ENTITY_TYPES,
    _BASKET_ENTITY_ALIASES,
    _GERMAN_DATE_IN_TEXT,
    _ISO_DATE_IN_TEXT,
    _clean_reply,
    _clean_text,
    _draft_identity,
    _identifier,
    _normalize_text_items,
    _text_fingerprint,
    _utc_now_iso,
)
from .geocoding import GeocodingError, NominatimGeocoder
from .manager import RoadplannerManager
from .roadplanner import RoadplannerError, ValidationError
from .structured_output import StructuredOutputError, normalize_changes_mapping

_LOGGER = logging.getLogger(__name__)

MAX_USER_TEXT = 12_000
MAX_SESSION_MESSAGES = 80
MAX_MEMORY_SUMMARY_CHARACTERS = 12_000
MAX_DIAGNOSTIC_RECORDS = 25
MAX_BASKET_ITEMS = 50
MAX_REQUEST_CACHE = 12
MIN_CHAT_INTERVAL_SECONDS = 1.0

_PAST_OVERNIGHT_MARKERS = (
    "letzte nacht",
    "letzten nacht",
    "vergangene nacht",
    "gestern nacht",
    "gestern übernachtet",
    "gestern uebernachtet",
    "hier geschlafen",
    "heute hier geschlafen",
    "tatsächlicher übernachtungsort",
    "tatsaechlicher uebernachtungsort",
    "actual overnight",
    "last night",
)

_CURRENT_OVERNIGHT_MARKERS = (
    "heute nacht",
    "für heute nacht",
    "fuer heute nacht",
    "heute übernachten",
    "heute uebernachten",
    "heute schlafen",
    "tonight",
)

_CURRENT_DAY_MARKERS = ("heute", "today", "aktuell", "jetzt")
_PREVIOUS_DAY_MARKERS = ("gestern", "yesterday")
_NEXT_DAY_MARKERS = ("morgen", "tomorrow")

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


CHAT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "maxLength": 30_000},
        "basket_delta": BASKET_SCHEMA,
    },
    "required": ["reply", "basket_delta"],
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


class RoadplannerAssistant:
    """Natural-language travel assistant with a review-only change basket."""

    def __init__(
        self,
        manager: RoadplannerManager,
        *,
        provider: AssistantProvider | None,
        geocoder: NominatimGeocoder | None,
        enable_research: bool = True,
        max_history: int = 24,
        autonomy_level: str = "change_basket",
        copilot_enabled: bool = True,
        copilot_auto_briefing: bool = False,
        debug_enabled: bool = False,
        language: str = "de",
        travel_archive: Any | None = None,
    ) -> None:
        self.manager = manager
        self.provider = provider
        self.geocoder = geocoder
        self.enable_research = bool(enable_research)
        self.language = language or "de"
        self.autonomy_level = (
            autonomy_level
            if autonomy_level in AUTONOMY_INSTRUCTIONS
            else "change_basket"
        )
        self.copilot_enabled = bool(copilot_enabled)
        self.copilot_auto_briefing = bool(copilot_auto_briefing)
        self.debug_enabled = bool(debug_enabled)
        self.travel_archive = travel_archive
        self.sessions = AssistantSessionStore(max_history=max_history)
        self.context_builder = AssistantContextBuilder()
        self.plugins = AssistantPluginRegistry()
        if geocoder is not None:
            self.plugins.register(
                GeocodingAssistantPlugin(geocoder, language=self.language)
            )

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.provider.configured)

    @property
    def provider_name(self) -> str | None:
        return self.provider.name if self.provider else None

    @property
    def model(self) -> str | None:
        return self.provider.model if self.provider else None

    def _provider_health(self) -> dict[str, Any]:
        provider = self.provider
        health = getattr(provider, "health_snapshot", None) if provider else None
        if callable(health):
            try:
                value = health()
                return value if isinstance(value, dict) else {}
            except Exception:  # pragma: no cover - provider diagnostic boundary
                return {}
        return {}

    def state(self, user_id: str, trip_id: str) -> dict[str, Any]:
        state = self.sessions.snapshot(user_id, trip_id)
        today = dt_util.now().date().isoformat()
        state.update(
            {
                "configured": self.configured,
                "provider": self.provider_name,
                "model": self.model,
                "research_enabled": self.enable_research,
                "autonomy_level": self.autonomy_level,
                "change_basket_enabled": self.autonomy_level == "change_basket",
                "copilot_enabled": self.copilot_enabled,
                "copilot_auto_briefing": self.copilot_auto_briefing,
                "briefing_due": bool(
                    self.copilot_enabled
                    and self.copilot_auto_briefing
                    and state.get("last_briefing_date") != today
                ),
                "debug_enabled": self.debug_enabled,
                "geocoding_enabled": bool(self.geocoder and self.geocoder.enabled),
                "plugins": self.plugins.descriptors(),
                "provider_health": self._provider_health(),
            }
        )
        return state

    def _provider(self) -> AssistantProvider:
        if self.provider is None or not self.provider.configured:
            raise ValidationError(
                "Der Roadplanner-Assistent ist noch nicht eingerichtet. "
                "Bitte in den Integrationsoptionen einen Gemini API-Schlüssel hinterlegen."
            )
        return self.provider

    @staticmethod
    def _should_enable_search(user_text: str) -> bool:
        """Use current web grounding only for discovery-style questions."""
        text = " ".join(str(user_text or "").casefold().split())
        if not text:
            return False
        phrases = (
            "recherch",
            "suche ",
            "finde ",
            "empfiehl",
            "empfehl",
            "top 3",
            "drei option",
            "in der nähe",
            "auf dem weg",
            "geöffnet",
            "öffnungszeit",
            "verfügbarkeit",
            "aktuelle preis",
            "aktueller preis",
            "wetter",
            "verkehr",
            "welche restaurants",
            "welcher stellplatz",
            "welche stellplätze",
            "welcher campingplatz",
            "welche campingplätze",
            "wo können wir essen",
            "wo können wir übernachten",
            "was gibt es",
        )
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def _chat_messages(session: AssistantSession, user_text: str) -> list[dict[str, str]]:
        messages = [
            {
                "role": "assistant" if item.get("role") == "assistant" else "user",
                "content": str(item.get("content") or ""),
            }
            for item in session.messages
            if item.get("kind") in {"message", "briefing"}
        ]
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def _memory_instruction(session: AssistantSession) -> str:
        if not session.memory_summary:
            return "Keine komprimierte frühere Unterhaltung vorhanden."
        return (
            "Frühere Unterhaltung wurde lokal komprimiert. Sie ist nur Gesprächshilfe "
            "und niemals stärker als das Roadbook:\n" + session.memory_summary
        )

    async def _load_trip_payload(self, trip_id: str) -> dict[str, Any]:
        """Load the current trip plus confirmed travel-archive context."""
        loader = getattr(self.manager, "async_get_assistant_payload", None)
        if callable(loader):
            payload = await loader(trip_id)
        else:
            payload = await self.manager.async_get_panel_payload(trip_id)
        archive = self.travel_archive
        if archive is not None:
            payload = dict(payload)
            payload["travel_archive"] = await archive.async_assistant_context(trip_id)
        return payload

    async def _context_for_request(
        self,
        *,
        payload: dict[str, Any],
        purpose: str,
        user_text: str = "",
        basket: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        built = self.context_builder.build(
            payload,
            user_text=user_text,
            basket=basket,
            purpose=purpose,
        )
        fragments = await self.plugins.async_context_fragments(
            purpose=purpose,
            context=built.context,
            user_text=user_text,
        )
        if fragments:
            built.context["assistant_plugins"] = fragments
        return built.context, built.metadata

    def _record_diagnostic(
        self,
        session: AssistantSession,
        *,
        request_id: str,
        kind: str,
        status: str,
        started: float,
        context_metadata: dict[str, Any] | None = None,
        provider_diagnostics: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        basket_status: str | None = None,
        basket_outcome: dict[str, Any] | None = None,
        plugin_diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sessions.record_diagnostic(
            session,
            {
                "request_id": request_id,
                "kind": kind,
                "status": status,
                "created_at": _utc_now_iso(),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "context_metadata": dict(context_metadata or {}),
                "provider": dict(provider_diagnostics or {}),
                "usage": dict(usage or {}),
                "error": _clean_text(error, maximum=1_000) if error else None,
                "basket_status": basket_status,
                "basket_outcome": dict(basket_outcome or {}),
                "plugins": list(plugin_diagnostics or []),
            },
        )

    async def _generate_chat_response(
        self,
        *,
        session: AssistantSession,
        context: dict[str, Any],
        user_text: str,
    ) -> AssistantJsonResult:
        """Generate the visible reply and basket delta in one provider call."""
        provider = self._provider()
        system_instruction = (
            f"{CHAT_SYSTEM_PROMPT}\n\n"
            f"{AUTONOMY_INSTRUCTIONS[self.autonomy_level]}\n\n"
            f"CONVERSATION_MEMORY:\n{self._memory_instruction(session)}\n\n"
            "CURRENT_CHANGE_BASKET:\n"
            f"{json.dumps(session.basket, ensure_ascii=False, allow_nan=False)}\n\n"
            f"ROADBOOK_CONTEXT:\n{json_context(context)}"
        )
        return await provider.async_generate_json_result(
            system_instruction=system_instruction,
            messages=self._chat_messages(session, user_text),
            schema=CHAT_RESPONSE_SCHEMA,
            enable_search=(
                self.enable_research and self._should_enable_search(user_text)
            ),
            max_output_tokens=6_144,
            temperature=0.3,
        )

    async def async_chat(
        self,
        *,
        user_id: str,
        trip_id: str,
        text: str,
        client_request_id: str = "",
    ) -> dict[str, Any]:
        self._provider()
        text = str(text or "").strip()
        client_request_id = _clean_text(client_request_id, maximum=200)
        if not text:
            raise ValidationError("Bitte eine Nachricht eingeben")
        if len(text) > MAX_USER_TEXT:
            raise ValidationError(
                f"Eine Nachricht darf maximal {MAX_USER_TEXT} Zeichen enthalten"
            )
        lock = self.sessions.lock(user_id, trip_id)
        async with lock:
            session = self.sessions.session(user_id, trip_id)
            request_fingerprint = _text_fingerprint(text)
            cached = self.sessions.cached_result(
                session,
                client_request_id,
                request_fingerprint=request_fingerprint,
            )
            if cached is not None:
                cached["assistant"] = self.state(user_id, trip_id)
                cached["deduplicated"] = True
                return cached

            elapsed = time.monotonic() - session.last_chat_monotonic
            if session.last_chat_monotonic and elapsed < MIN_CHAT_INTERVAL_SECONDS:
                raise ValidationError(
                    "Bitte kurz warten, bevor du die nächste Nachricht sendest"
                )
            session.last_chat_monotonic = time.monotonic()
            request_id = f"chat-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            try:
                payload = await self._load_trip_payload(trip_id)
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="chat",
                    user_text=text,
                    basket=session.basket,
                )
                result = await self._generate_chat_response(
                    session=session,
                    context=context,
                    user_text=text,
                )
                reply = _clean_reply(result.value.get("reply"), maximum=30_000)
                if not reply:
                    raise ValidationError("Gemini hat keine lesbare Antwort geliefert")
                raw_delta = result.value.get("basket_delta")
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="chat",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:  # defensive provider/context boundary
                _LOGGER.exception(
                    "Unexpected Roadplanner assistant chat failure (%s)", request_id
                )
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="chat",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    "Der Assistent konnte die Anfrage nicht sicher verarbeiten. "
                    f"Bitte erneut versuchen (Anfrage {request_id})."
                ) from err

            if isinstance(raw_delta, dict):
                delta = raw_delta
            elif isinstance(raw_delta, list):
                delta = {
                    "add_or_update": raw_delta,
                    "remove_ids": [],
                    "note": "",
                }
            elif isinstance(raw_delta, str) and raw_delta.strip():
                delta = {
                    "add_or_update": [raw_delta],
                    "remove_ids": [],
                    "note": "",
                }
            else:
                delta = {}

            basket_update: dict[str, Any] = {
                "added": [],
                "updated": [],
                "removed_ids": [],
                "ignored_remove_ids": [],
                "rejected": [],
                "repaired": [],
                "note": "",
                "before_count": len(session.basket),
                "after_count": len(session.basket),
                "requested_count": 0,
                "added_count": 0,
                "updated_count": 0,
                "removed_count": 0,
                "ignored_remove_count": 0,
                "converted_remove_count": 0,
                "rejected_count": 0,
                "repaired_count": 0,
                "actual_change_count": 0,
                "changed": False,
                "delta_valid": isinstance(raw_delta, (dict, list, str)),
            }
            basket_status = "disabled"
            if self.autonomy_level == "change_basket":
                bounded_roadbook = _bounded_context(payload)
                delta, stale_remove_repaired = _repair_stale_remove_delta(
                    delta,
                    basket=session.basket,
                    roadbook_context=bounded_roadbook,
                    user_text=text,
                )
                basket_update = self.sessions.apply_delta(
                    session,
                    delta,
                    roadbook_context=bounded_roadbook,
                )
                if stale_remove_repaired:
                    repaired_items = list(basket_update.get("repaired", []))
                    repaired_items.append({
                        "kind": "delta",
                        "repairs": [
                            "veraltete remove_ids als Roadbook-Planungsabsicht erhalten"
                        ],
                    })
                    basket_update["repaired"] = repaired_items[:25]
                    basket_update["repaired_count"] = len(repaired_items)
                if basket_update.get("changed"):
                    basket_status = "changed"
                elif basket_update.get("rejected_count"):
                    basket_status = "invalid"
                else:
                    basket_status = "unchanged"

            delta_valid = bool(basket_update.get("delta_valid", True))
            reply, claim_removed = _strip_unverified_basket_claims(reply)
            if not reply:
                reply = "Ich habe deine Nachricht ausgewertet."

            actual_change_count = int(
                basket_update.get("actual_change_count") or 0
            )
            rejected_count = int(basket_update.get("rejected_count") or 0)
            repaired_count = int(basket_update.get("repaired_count") or 0)
            ignored_remove_count = int(
                basket_update.get("ignored_remove_count") or 0
            )
            after_count = int(basket_update.get("after_count") or 0)
            rejected_reasons = [
                _clean_text(item.get("reason"), maximum=240)
                for item in basket_update.get("rejected", [])
                if isinstance(item, dict) and item.get("reason")
            ][:3]
            basket_warning = ""

            should_show_basket_status = bool(
                actual_change_count
                or claim_removed
                or rejected_count
                or repaired_count
                or ignored_remove_count
                or not delta_valid
            )
            if should_show_basket_status:
                status_text = _basket_status_text(
                    actual_change_count, after_count
                )
                if repaired_count and actual_change_count:
                    noun = "Vorschlag" if repaired_count == 1 else "Vorschläge"
                    status_text += (
                        f" {repaired_count} {noun} wurden automatisch "
                        "in eine sichere Planungsabsicht vervollständigt."
                    )
                if ignored_remove_count == 1:
                    status_text += (
                        " Die angeforderte alte Vormerkung war bereits nicht mehr "
                        "im Korb; das Entfernen wurde als bereits erledigt behandelt."
                    )
                elif ignored_remove_count > 1:
                    status_text += (
                        f" {ignored_remove_count} angeforderte alte Vormerkungen waren "
                        "bereits nicht mehr im Korb; das Entfernen wurde als bereits "
                        "erledigt behandelt."
                    )
                if rejected_count:
                    details = "; ".join(rejected_reasons)
                    rejected_label = (
                        "1 Vorschlag konnte"
                        if rejected_count == 1
                        else f"{rejected_count} Vorschläge konnten"
                    )
                    status_text += (
                        f" {rejected_label} nicht erkannt werden"
                        f"{': ' + details if details else ''}."
                    )
                reply = f"{reply.rstrip()}\n\n{status_text}".strip()

            if (
                claim_removed
                and actual_change_count == 0
                and ignored_remove_count == 0
            ):
                basket_warning = (
                    "Gemini hat eine Vormerkung behauptet, aber der Server hat "
                    "keine verständliche Änderungsabsicht erkannt."
                )
            elif rejected_count:
                details = "; ".join(rejected_reasons)
                rejected_label = (
                    "1 Änderungsvorschlag konnte"
                    if rejected_count == 1
                    else f"{rejected_count} Änderungsvorschläge konnten"
                )
                basket_warning = (
                    f"{rejected_label} auch nach automatischer Vervollständigung "
                    "nicht übernommen werden"
                    f"{': ' + details if details else ''}."
                )
            elif not delta_valid and actual_change_count == 0:
                basket_warning = (
                    "Gemini lieferte keinen erkennbaren Änderungskorb. Die "
                    "Antwort wurde angezeigt, aber nichts vorgemerkt."
                )

            basket_outcome = {
                "status": basket_status,
                "before_count": int(basket_update.get("before_count") or 0),
                "after_count": after_count,
                "requested_count": int(
                    basket_update.get("requested_count") or 0
                ),
                "actual_change_count": actual_change_count,
                "added_count": int(basket_update.get("added_count") or 0),
                "updated_count": int(basket_update.get("updated_count") or 0),
                "removed_count": int(basket_update.get("removed_count") or 0),
                "rejected_count": rejected_count,
                "repaired_count": repaired_count,
                "rejected_reasons": rejected_reasons,
                "claim_corrected": claim_removed,
                "delta_valid": delta_valid,
            }

            self.sessions.append_message(session, role="user", content=text)
            assistant_message = self.sessions.append_message(
                session,
                role="assistant",
                content=reply,
                sources=[source.as_dict() for source in result.sources],
                metadata={
                    "basket_outcome": basket_outcome,
                    "basket_warning": basket_warning,
                },
            )

            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="chat",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
                basket_status=basket_status,
                basket_outcome=basket_outcome,
            )
            response = {
                "request_id": request_id,
                "client_request_id": client_request_id or None,
                "message": assistant_message,
                "basket_update": basket_update,
                "basket_outcome": basket_outcome,
                "basket_warning": basket_warning,
                "assistant": self.state(user_id, trip_id),
                "context_revision": context.get("revision"),
                "context_metadata": context_metadata,
                "model_version": result.model_version,
                "usage": result.usage,
                "provider_diagnostics": result.diagnostics,
                "logical_api_calls": 1,
                "deduplicated": False,
            }
            self.sessions.cache_result(
                session,
                client_request_id,
                response,
                request_fingerprint=request_fingerprint,
            )
            return response

    async def async_test(self, *, user_id: str, trip_id: str) -> dict[str, Any]:
        provider = self._provider()
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            request_id = f"test-{uuid4().hex[:12]}"
            started = time.monotonic()
            try:
                result = await provider.async_generate_text(
                    system_instruction=PROVIDER_TEST_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": "Verbindungstest"}],
                    enable_search=False,
                    max_output_tokens=64,
                    temperature=0.0,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="provider_test",
                    status="error",
                    started=started,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected assistant provider test failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="provider_test",
                    status="error",
                    started=started,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    f"Der Verbindungstest ist unerwartet fehlgeschlagen (Anfrage {request_id})."
                ) from err
            ok = result.text.strip().casefold().startswith("ok")
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="provider_test",
                status="ok" if ok else "unexpected_response",
                started=started,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
            )
            return {
                "ok": ok,
                "answer": result.text[:200],
                "request_id": request_id,
                "provider": self._provider_health(),
            }

    async def async_briefing(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        if not self.copilot_enabled:
            raise ValidationError("Der optionale Copilot ist deaktiviert")
        provider = self._provider()
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            request_id = f"brief-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            try:
                payload = await self._load_trip_payload(trip_id)
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="briefing",
                    user_text="Tagesbriefing",
                )
                result = await provider.async_generate_text(
                    system_instruction=(
                        f"{COPILOT_SYSTEM_PROMPT}\n\nROADBOOK_CONTEXT:\n{json_context(context)}"
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": "Erstelle jetzt das optionale Roadplanner-Tagesbriefing.",
                        }
                    ],
                    enable_search=self.enable_research,
                    max_output_tokens=2048,
                    temperature=0.25,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="briefing",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected copilot briefing failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="briefing",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    f"Das Tagesbriefing konnte nicht erstellt werden (Anfrage {request_id})."
                ) from err
            message = self.sessions.append_message(
                session,
                role="assistant",
                content=result.text,
                sources=[source.as_dict() for source in result.sources],
                kind="briefing",
            )
            session.last_briefing_date = dt_util.now().date().isoformat()
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="briefing",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
            )
            return {
                "request_id": request_id,
                "message": message,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_diagnostics(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            return {
                "provider": self._provider_health(),
                "session": {
                    "trip_id": trip_id,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "memory_summary_characters": len(session.memory_summary),
                    "total_message_count": session.total_message_count,
                    "recent_message_count": len(session.messages),
                    "compacted_message_count": session.compacted_message_count,
                    "compaction_count": session.compaction_count,
                    "basket_count": len(session.basket),
                    "request_cache_count": len(session.request_cache),
                    "usage": deepcopy(session.usage_totals),
                },
                "plugins": self.plugins.descriptors(),
                "records": deepcopy(session.diagnostics[-MAX_DIAGNOSTIC_RECORDS:]),
            }

    async def async_clear(self, *, user_id: str, trip_id: str) -> dict[str, Any]:
        async with self.sessions.lock(user_id, trip_id):
            self.sessions.clear(user_id, trip_id)
            return self.state(user_id, trip_id)

    @staticmethod
    def _build_location_drafts(
        payload: dict[str, Any],
        *,
        day_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Build review-only GPS completion drafts from canonical day data."""
        selected_day_ids = {str(item) for item in day_ids or set() if str(item)}
        drafts: list[dict[str, Any]] = []
        touched_day_ids: set[str] = set()
        seen_stops: set[tuple[str, str]] = set()
        for day in payload.get("days", {}).get("days", []):
            if not isinstance(day, dict):
                continue
            day_id = _clean_text(day.get("id"), maximum=200)
            if selected_day_ids and day_id not in selected_day_ids:
                continue
            for stop in canonical_day_stops(day):
                if not isinstance(stop, dict) or location_status(stop) == "resolved":
                    continue
                stop_id = _clean_text(stop.get("id"), maximum=200)
                name = _clean_text(stop.get("name"), maximum=500)
                owner_day_id = _clean_text(
                    stop.get("_source_day_id") if stop.get("_inherited") else day_id,
                    maximum=200,
                )
                if not stop_id or not name or not owner_day_id:
                    continue
                identity = (owner_day_id, stop_id)
                if identity in seen_stops:
                    continue
                seen_stops.add(identity)
                location = (
                    stop.get("location")
                    if isinstance(stop.get("location"), dict)
                    else {}
                )
                details = (
                    stop.get("details")
                    if isinstance(stop.get("details"), dict)
                    else {}
                )
                geocoding = (
                    details.get("geocoding")
                    if isinstance(details.get("geocoding"), dict)
                    else {}
                )
                query_parts = [
                    _clean_text(geocoding.get("query"), maximum=500),
                    name,
                    _clean_text(location.get("label"), maximum=300),
                    _clean_text(location.get("city"), maximum=200),
                    _clean_text(location.get("country_code"), maximum=20),
                ]
                query_values: list[str] = []
                seen_values: set[str] = set()
                for value in query_parts:
                    key = value.casefold()
                    if value and key not in seen_values:
                        seen_values.add(key)
                        query_values.append(value)
                place_query = ", ".join(query_values)[:500]
                if not place_query:
                    continue
                inherited_note = " (Start vom Vortag)" if stop.get("_inherited") else ""
                drafts.append(
                    {
                        "action": "update",
                        "entity_type": "stop",
                        "target_id": stop_id,
                        "day_id": owner_day_id,
                        "place_query": place_query,
                        "summary": f"GPS-Daten prüfen/ergänzen: {name}{inherited_note}",
                        "reason": (
                            "Der konkrete Roadbook-Stopp besitzt noch keine eindeutig "
                            "bestätigten GPS-Daten. Roadplanner soll den Ort serverseitig "
                            "auflösen und die Auswahl vor dem Speichern prüfen."
                        ),
                        "values": {},
                    }
                )
                touched_day_ids.add(owner_day_id)
        return drafts, touched_day_ids

    async def async_add_location_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
        day_id: str,
    ) -> dict[str, Any]:
        """Add review-only GPS completion drafts for one Roadbook day."""
        payload = await self._load_trip_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "GPS-Daten können nur für die aktive Reise ergänzt werden"
            )
        day = next(
            (
                item
                for item in payload.get("days", {}).get("days", [])
                if isinstance(item, dict) and str(item.get("id") or "") == str(day_id)
            ),
            None,
        )
        if day is None:
            raise ValidationError(f"Reisetag nicht gefunden: {day_id}")
        drafts, _touched_days = self._build_location_drafts(
            payload,
            day_ids={str(day_id)},
        )
        if not drafts:
            raise ValidationError(
                "Für diesen Reisetag wurden keine offenen GPS-Zuordnungen gefunden"
            )
        delta = {
            "add_or_update": drafts,
            "remove_ids": [],
            "note": f"GPS-Vervollständigung für {day.get('title') or day_id}",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(
                session,
                delta,
                roadbook_context=payload,
            )
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die GPS-Vervollständigung konnte nicht vorgemerkt werden"
                )
            return {
                "draft_count": int(applied.get("added_count") or 0)
                + int(applied.get("updated_count") or 0),
                "day_count": 1,
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_trip_location_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        """Add review-only GPS completion drafts for the selected trip."""
        payload = await self._load_trip_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "GPS-Daten können nur für die aktive Reise ergänzt werden"
            )
        drafts, touched_day_ids = self._build_location_drafts(payload)
        if not drafts:
            raise ValidationError(
                "Für diese Reise wurden keine offenen GPS-Zuordnungen gefunden"
            )
        delta = {
            "add_or_update": drafts,
            "remove_ids": [],
            "note": "GPS-Vervollständigung für die ausgewählte Reise",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(
                session,
                delta,
                roadbook_context=payload,
            )
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die GPS-Vervollständigung konnte nicht vorgemerkt werden"
                )
            return {
                "draft_count": int(applied.get("added_count") or 0)
                + int(applied.get("updated_count") or 0),
                "day_count": len(touched_day_ids),
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_decision_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        decision: dict[str, Any],
        option: dict[str, Any],
    ) -> dict[str, Any]:
        """Place one explicitly selected decision option in the change basket."""
        if not isinstance(decision, dict) or not isinstance(option, dict):
            raise ValidationError("Entscheidungsoption ist unvollständig")
        title = _clean_text(option.get("title"), maximum=500)
        if not title:
            raise ValidationError("Entscheidungsoption besitzt keinen Titel")
        place_query = _clean_text(option.get("place_query"), maximum=500)
        linked_day_id = _clean_text(decision.get("linked_day_id"), maximum=200)
        notes_parts = [
            _clean_text(option.get("summary"), maximum=2_000),
            "Vorteile: " + "; ".join(
                _clean_text(item, maximum=300)
                for item in list(option.get("pros") or [])[:4]
                if _clean_text(item, maximum=300)
            ),
            "Nachteile: " + "; ".join(
                _clean_text(item, maximum=300)
                for item in list(option.get("cons") or [])[:4]
                if _clean_text(item, maximum=300)
            ),
        ]
        notes = "\n".join(part for part in notes_parts if part and not part.endswith(": "))
        delta = {
            "add_or_update": [
                {
                    "action": "plan",
                    "entity_type": "stop",
                    "summary": f"Ausgewählte Option übernehmen: {title}",
                    "day_id": linked_day_id,
                    "place_query": place_query,
                    "reason": "Vom Benutzer in einer Roadplanner-Entscheidungsvorlage ausdrücklich ausgewählt.",
                    "values": {
                        "name": title,
                        "type": _clean_text(option.get("stop_type"), maximum=100) or "waypoint",
                        "notes": notes,
                    },
                }
            ],
            "remove_ids": [],
            "note": f"Aus Entscheidungsvorlage: {_clean_text(decision.get('title'), maximum=500)}",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(session, delta)
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(reason or "Die ausgewählte Option konnte nicht vorgemerkt werden")
            draft = (applied.get("added") or applied.get("updated") or [{}])[0]
            return {
                "draft": deepcopy(draft),
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_import_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
        delta: dict[str, Any],
        title: str,
        summary: str,
        document_id: str,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add one analyzed universal import to the volatile change basket.

        The imported file remains a private archive document.  Only the
        normalized coarse intentions are copied into the basket; final IDs,
        revision and ChangeSet metadata are still produced by Home Assistant
        when the user presses ``Änderungen prüfen``.
        """
        if not isinstance(delta, dict):
            raise ValidationError("Die Importanalyse enthält keinen gültigen Änderungskorb")
        clean_title = _clean_text(title, maximum=500) or "Importierte Reiseübergabe"
        clean_summary = _clean_text(summary, maximum=8_000)
        questions = [
            _clean_text(item, maximum=2_000)
            for item in list(open_questions or [])[:30]
            if _clean_text(item, maximum=2_000)
        ]
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(session, delta)
            if not applied.get("changed") and not session.basket:
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die importierte Übergabe enthält keine übernehmbaren Änderungen"
                )
            self.sessions.append_message(
                session,
                role="user",
                content=f"Datei als Übergabe importiert: {clean_title}",
                kind="attachment",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            response_parts = [
                f"Die Übergabe „{clean_title}“ wurde analysiert.",
                clean_summary,
                _basket_status_text(
                    int(applied.get("actual_change_count") or 0),
                    int(applied.get("after_count") or len(session.basket)),
                ),
            ]
            if questions:
                response_parts.append(
                    "Offene Punkte:\n" + "\n".join(f"- {item}" for item in questions)
                )
            message = self.sessions.append_message(
                session,
                role="assistant",
                content="\n\n".join(part for part in response_parts if part),
                kind="import",
                metadata={
                    "document_id": document_id,
                    "source": "universal_import",
                    "basket_outcome": applied,
                },
            )
            return {
                "message": message,
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_import_context(
        self,
        *,
        user_id: str,
        trip_id: str,
        title: str,
        summary: str,
        document_id: str,
        preview_items: list[dict[str, Any]] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Place an import summary in the conversation without changing the basket."""
        clean_title = _clean_text(title, maximum=500) or "Importierte Datei"
        clean_summary = _clean_text(summary, maximum=8_000)
        items = [
            _clean_text(item.get("title"), maximum=500)
            for item in list(preview_items or [])[:20]
            if isinstance(item, dict) and _clean_text(item.get("title"), maximum=500)
        ]
        questions = [
            _clean_text(item, maximum=2_000)
            for item in list(open_questions or [])[:20]
            if _clean_text(item, maximum=2_000)
        ]
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            self.sessions.append_message(
                session,
                role="user",
                content=f"Datei zum Besprechen angehängt: {clean_title}",
                kind="attachment",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            parts = [f"Ich habe die Datei „{clean_title}“ als Gesprächskontext erfasst.", clean_summary]
            if items:
                parts.append("Erkannte Inhalte:\n" + "\n".join(f"- {item}" for item in items))
            if questions:
                parts.append("Offene Punkte:\n" + "\n".join(f"- {item}" for item in questions))
            message = self.sessions.append_message(
                session,
                role="assistant",
                content="\n\n".join(part for part in parts if part),
                kind="import",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            return {"message": message, "assistant": self.state(user_id, trip_id)}

    async def async_remove_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        draft_id: str,
    ) -> dict[str, Any]:
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            raise ValidationError("Entwurfs-ID fehlt")
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            before = len(session.basket)
            session.basket = [item for item in session.basket if item.get("id") != draft_id]
            if len(session.basket) == before:
                raise ValidationError("Vorgemerkte Änderung wurde nicht gefunden")
            session.updated_at = _utc_now_iso()
            return self.state(user_id, trip_id)

    async def async_update_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        draft_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValidationError("Entwurfsänderung muss ein JSON-Objekt sein")
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            updated = self.sessions.update_draft(session, draft_id, patch)
            return {
                "draft": updated,
                "assistant": self.state(user_id, trip_id),
            }

    @staticmethod
    def _needs_research(basket: list[dict[str, Any]]) -> bool:
        return any(
            item.get("action") == "plan"
            or (
                item.get("entity_type") == "stop"
                and item.get("action") == "add"
                and not item.get("place_query")
            )
            for item in basket
        )

    async def _compile_operations(
        self,
        *,
        context: dict[str, Any],
        basket: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> AssistantJsonResult:
        """Compile and optionally research the basket in one provider call."""
        provider = self._provider()
        payload = {
            "roadbook_context": context,
            "draft_basket": basket,
            "research_allowed": bool(
                self.enable_research and self._needs_research(basket)
            ),
            "recent_conversation": [
                {
                    "role": message.get("role"),
                    "content": message.get("content"),
                }
                for message in messages[-12:]
                if message.get("kind") == "message"
            ],
        }
        return await provider.async_generate_json_result(
            system_instruction=COMPILE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, allow_nan=False),
                }
            ],
            schema=COMPILE_SCHEMA,
            enable_search=bool(
                self.enable_research and self._needs_research(basket)
            ),
            max_output_tokens=16_384,
            temperature=0.05,
        )

    @staticmethod
    def _known_ids(context: dict[str, Any]) -> tuple[set[str], dict[str, set[str]], set[str]]:
        catalog = context.get("id_catalog") if isinstance(context.get("id_catalog"), dict) else {}
        if catalog:
            day_ids = {str(value) for value in catalog.get("day_ids", []) if value}
            stop_ids = {
                str(day_id): {str(value) for value in values if value}
                for day_id, values in (catalog.get("stop_ids_by_day") or {}).items()
                if isinstance(values, list)
            }
            preference_ids = {
                str(value) for value in catalog.get("preference_ids", []) if value
            }
            return day_ids, stop_ids, preference_ids

        day_ids: set[str] = set()
        stop_ids: dict[str, set[str]] = {}
        preference_ids: set[str] = set()
        trip_details = context.get("trip", {}).get("details")
        if isinstance(trip_details, dict):
            preferences = trip_details.get("planning_preferences")
            if isinstance(preferences, list):
                preference_ids.update(
                    str(item.get("id"))
                    for item in preferences
                    if isinstance(item, dict) and item.get("id")
                )
        for day in context.get("days", []):
            day_id = str(day.get("id") or "")
            if not day_id:
                continue
            day_ids.add(day_id)
            stop_ids[day_id] = {
                str(stop.get("id"))
                for stop in canonical_roadbook_stops(day)
                if isinstance(stop, dict) and stop.get("id")
            }
            details = day.get("details")
            if isinstance(details, dict):
                preferences = details.get("planning_preferences")
                if isinstance(preferences, list):
                    preference_ids.update(
                        str(item.get("id"))
                        for item in preferences
                        if isinstance(item, dict) and item.get("id")
                    )
        return day_ids, stop_ids, preference_ids

    @staticmethod
    def _context_day_sequence(context: dict[str, Any]) -> list[dict[str, str]]:
        """Return the bounded trip day sequence used for deterministic day inference."""
        sequence: list[dict[str, str]] = []
        for raw in context.get("trip_index", []):
            if not isinstance(raw, dict):
                continue
            day_id = _clean_text(raw.get("id"), maximum=200)
            if not day_id:
                continue
            sequence.append(
                {
                    "id": day_id,
                    "date": _clean_text(raw.get("date"), maximum=20),
                }
            )
        if sequence:
            return sequence
        for raw in context.get("days", []):
            if not isinstance(raw, dict):
                continue
            day_id = _clean_text(raw.get("id"), maximum=200)
            if day_id:
                sequence.append(
                    {
                        "id": day_id,
                        "date": _clean_text(raw.get("date"), maximum=20),
                    }
                )
        return sequence

    @staticmethod
    def _relative_day_id(
        context: dict[str, Any],
        offset: int,
    ) -> str:
        sequence = RoadplannerAssistant._context_day_sequence(context)
        if not sequence:
            return ""
        scope = context.get("scope") if isinstance(context.get("scope"), dict) else {}
        current_id = _clean_text(scope.get("current_day_id"), maximum=200)
        current_index: int | None = None
        if current_id:
            current_index = next(
                (index for index, item in enumerate(sequence) if item["id"] == current_id),
                None,
            )
        if current_index is None:
            local_date = _clean_text(context.get("local_date"), maximum=20)
            current_index = next(
                (index for index, item in enumerate(sequence) if item["date"] == local_date),
                None,
            )
        if current_index is None:
            return ""
        target = current_index + offset
        if target < 0 or target >= len(sequence):
            return ""
        return sequence[target]["id"]

    @staticmethod
    def _day_id_for_date(context: dict[str, Any], requested: str) -> str:
        requested = _clean_text(requested, maximum=20)
        if not requested:
            return ""
        for item in RoadplannerAssistant._context_day_sequence(context):
            if item["date"] == requested:
                return item["id"]
        return ""

    @staticmethod
    def _operation_context_text(
        raw: dict[str, Any],
        *,
        basket: list[dict[str, Any]] | None,
    ) -> str:
        """Build a bounded semantic text used only for parent-day inference."""
        fragments: list[str] = []
        changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
        for value in (
            raw.get("reason"),
            raw.get("place_query"),
            changes.get("name"),
            changes.get("notes"),
            changes.get("type"),
        ):
            cleaned = _clean_text(value, maximum=2_000)
            if cleaned:
                fragments.append(cleaned)

        stop_items = [
            item
            for item in (basket or [])
            if isinstance(item, dict)
            and str(item.get("entity_type") or "").casefold() == "stop"
        ]
        raw_query = _clean_text(raw.get("place_query"), maximum=500).casefold()
        raw_name = _clean_text(changes.get("name"), maximum=500).casefold()
        matching: list[dict[str, Any]] = []
        if raw_query:
            matching = [
                item
                for item in stop_items
                if _clean_text(item.get("place_query"), maximum=500).casefold() == raw_query
            ]
        if not matching and raw_name:
            matching = [
                item
                for item in stop_items
                if raw_name
                in " ".join(
                    (
                        _clean_text(item.get("summary"), maximum=500),
                        _clean_text(
                            (item.get("values") or {}).get("name")
                            if isinstance(item.get("values"), dict)
                            else "",
                            maximum=500,
                        ),
                    )
                ).casefold()
            ]
        if not matching and len(stop_items) == 1:
            matching = stop_items

        for item in matching[:3]:
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            for value in (
                item.get("summary"),
                item.get("reason"),
                item.get("place_query"),
                values.get("name"),
                values.get("notes"),
                values.get("type"),
            ):
                cleaned = _clean_text(value, maximum=2_000)
                if cleaned:
                    fragments.append(cleaned)
        return " ".join(fragments).casefold()[:8_000]

    @staticmethod
    def _basket_parent_day(
        raw: dict[str, Any],
        *,
        context: dict[str, Any],
        basket: list[dict[str, Any]] | None,
    ) -> str:
        """Resolve an explicit parent day stored in the matching draft intent."""
        stop_items = [
            item
            for item in (basket or [])
            if isinstance(item, dict)
            and str(item.get("entity_type") or "").casefold() == "stop"
        ]
        changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
        raw_query = _clean_text(raw.get("place_query"), maximum=500).casefold()
        raw_name = _clean_text(changes.get("name"), maximum=500).casefold()
        matching: list[dict[str, Any]] = []
        if raw_query:
            matching = [
                item
                for item in stop_items
                if _clean_text(item.get("place_query"), maximum=500).casefold() == raw_query
            ]
        if not matching and raw_name:
            matching = [
                item
                for item in stop_items
                if raw_name
                in " ".join(
                    (
                        _clean_text(item.get("summary"), maximum=500),
                        _clean_text(
                            (item.get("values") or {}).get("name")
                            if isinstance(item.get("values"), dict)
                            else "",
                            maximum=500,
                        ),
                    )
                ).casefold()
            ]
        if not matching and len(stop_items) == 1:
            matching = stop_items
        if len(matching) != 1:
            return ""
        item = matching[0]
        day_id = _clean_text(item.get("day_id"), maximum=200)
        if day_id:
            return day_id
        return RoadplannerAssistant._day_id_for_date(
            context,
            _clean_text(item.get("day_date"), maximum=20),
        )

    @staticmethod
    def _infer_missing_stop_day_id(
        raw: dict[str, Any],
        *,
        context: dict[str, Any],
        basket: list[dict[str, Any]] | None,
        entity_id: str | None,
        stop_ids: dict[str, set[str]],
    ) -> str:
        """Infer a missing stop parent conservatively from authoritative context."""
        # Existing stop IDs already uniquely identify their canonical parent day.
        if entity_id:
            owners = [day_id for day_id, ids in stop_ids.items() if entity_id in ids]
            if len(owners) == 1:
                return owners[0]

        explicit = RoadplannerAssistant._basket_parent_day(
            raw,
            context=context,
            basket=basket,
        )
        if explicit:
            return explicit

        text = RoadplannerAssistant._operation_context_text(raw, basket=basket)
        for match in _ISO_DATE_IN_TEXT.findall(text):
            day_id = RoadplannerAssistant._day_id_for_date(context, match)
            if day_id:
                return day_id
        for day_value, month_value, year_value in _GERMAN_DATE_IN_TEXT.findall(text):
            try:
                normalized = f"{int(year_value):04d}-{int(month_value):02d}-{int(day_value):02d}"
            except ValueError:
                continue
            day_id = RoadplannerAssistant._day_id_for_date(context, normalized)
            if day_id:
                return day_id

        changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
        stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
        has_past_overnight_marker = any(
            marker in text for marker in _PAST_OVERNIGHT_MARKERS
        )
        has_current_overnight_marker = any(
            marker in text for marker in _CURRENT_OVERNIGHT_MARKERS
        )
        is_overnight = (
            stop_type in OVERNIGHT_STOP_TYPES
            or has_past_overnight_marker
            or has_current_overnight_marker
        )
        if is_overnight and has_past_overnight_marker:
            previous_id = RoadplannerAssistant._relative_day_id(context, -1)
            if previous_id:
                return previous_id
        if is_overnight and has_current_overnight_marker:
            current_id = RoadplannerAssistant._relative_day_id(context, 0)
            if current_id:
                return current_id
        if any(marker in text for marker in _PREVIOUS_DAY_MARKERS):
            previous_id = RoadplannerAssistant._relative_day_id(context, -1)
            if previous_id:
                return previous_id
        if any(marker in text for marker in _NEXT_DAY_MARKERS):
            next_id = RoadplannerAssistant._relative_day_id(context, 1)
            if next_id:
                return next_id
        if any(marker in text for marker in _CURRENT_DAY_MARKERS):
            current_id = RoadplannerAssistant._relative_day_id(context, 0)
            if current_id:
                return current_id

        detailed_ids = [
            _clean_text(day.get("id"), maximum=200)
            for day in context.get("days", [])
            if isinstance(day, dict) and _clean_text(day.get("id"), maximum=200)
        ]
        if len(set(detailed_ids)) == 1:
            return detailed_ids[0]
        return ""

    @staticmethod
    def _day_detail(context: dict[str, Any], day_id: str) -> dict[str, Any] | None:
        for day in context.get("days", []):
            if isinstance(day, dict) and str(day.get("id") or "") == day_id:
                return day
        return None

    @staticmethod
    def _is_actual_past_overnight(
        raw: dict[str, Any],
        *,
        basket: list[dict[str, Any]] | None,
    ) -> bool:
        changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
        stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
        text = RoadplannerAssistant._operation_context_text(raw, basket=basket)
        has_past_marker = any(marker in text for marker in _PAST_OVERNIGHT_MARKERS)
        return has_past_marker and (
            stop_type in OVERNIGHT_STOP_TYPES or "übernacht" in text or "geschlafen" in text
        )

    @staticmethod
    def _sanitize_operation(
        raw: dict[str, Any],
        *,
        index: int,
        context: dict[str, Any],
        new_day_refs: set[str],
        basket: list[dict[str, Any]] | None = None,
        position_state: dict[str, list[dict[str, str]]] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValidationError(f"Assistenten-Operation {index + 1} ist kein Objekt")

        # Keep the current Home Assistant trip authoritative. A model-generated
        # trip_id is tolerated only as redundant compatibility metadata and is
        # checked before being discarded. All other envelope fields are always
        # discarded because Home Assistant creates them after validation.
        raw = _normalize_compiled_operation_aliases(
            raw,
            index=index,
        )
        supplied_trip_id = _clean_text(raw.pop("trip_id", None), maximum=200)
        trip = context.get("trip") if isinstance(context.get("trip"), dict) else {}
        canonical_trip_id = str(
            trip.get("id") or context.get("selected_trip_id") or ""
        )
        if supplied_trip_id and canonical_trip_id and supplied_trip_id != canonical_trip_id:
            raise ValidationError(
                "Die vom Assistenten gelieferte Reise-ID stimmt nicht mit der "
                f"aktiven Reise überein: {supplied_trip_id} != {canonical_trip_id}"
            )
        for field_name in _SERVER_CONTROLLED_OPERATION_FIELDS - {"trip_id"}:
            raw.pop(field_name, None)

        unknown = set(raw) - _ALLOWED_OPERATION_FIELDS
        if unknown:
            raise ValidationError(
                "Nicht erlaubte Felder in Assistenten-Operation: "
                + ", ".join(sorted(unknown))
            )
        action = str(raw.get("action") or "").casefold()
        entity_type = str(raw.get("entity_type") or "").casefold()
        if action not in _ALLOWED_ACTIONS:
            raise ValidationError(f"Nicht unterstützte Assistentenaktion: {action}")
        if entity_type not in _ALLOWED_ENTITY_TYPES:
            raise ValidationError(f"Nicht unterstützter Assistententyp: {entity_type}")
        try:
            changes, changes_mode = normalize_changes_mapping(
                raw.get("changes"),
                allow_scalar_empty=action in {"remove", "move"},
            )
        except StructuredOutputError as err:
            raise ValidationError(
                f"changes in Assistenten-Operation {index + 1} konnte nicht "
                "sicher als JSON-Objekt gelesen werden"
            ) from err
        if changes_mode != "object":
            _LOGGER.debug(
                "Normalized assistant operation %s changes during validation (%s)",
                index + 1,
                changes_mode,
            )
        unknown_changes = set(changes) - _ALLOWED_CHANGE_FIELDS
        if unknown_changes:
            raise ValidationError(
                "Nicht erlaubte Änderungen im Assistentenvorschlag: "
                + ", ".join(sorted(unknown_changes))
            )
        wrong_entity_changes = set(changes) - _CHANGE_FIELDS_BY_ENTITY[entity_type]
        if wrong_entity_changes:
            raise ValidationError(
                f"Nicht erlaubte Felder für {entity_type}: "
                + ", ".join(sorted(wrong_entity_changes))
            )
        operation: dict[str, Any] = {
            "operation_id": _clean_text(raw.get("operation_id"), maximum=200)
            or f"op-{index + 1}-{uuid4().hex[:10]}",
            "action": action,
            "entity_type": entity_type,
            "changes": _deep_without_none(deepcopy(changes)),
            "reason": _clean_text(raw.get("reason"), maximum=1_000)
            or "Im Roadplanner-Gespräch vom Benutzer vorgemerkt.",
        }
        for field in ("entity_id", "day_id", "day_ref", "place_query"):
            value = _clean_text(raw.get(field), maximum=500)
            if value:
                operation[field] = value
        position = raw.get("position")
        if isinstance(position, int) and not isinstance(position, bool) and position > 0:
            operation["position"] = position

        day_ids, stop_ids, preference_ids = RoadplannerAssistant._known_ids(context)
        entity_id = operation.get("entity_id")
        day_id = operation.get("day_id")
        day_ref = operation.get("day_ref")

        if entity_type == "trip":
            if action != "update":
                raise ValidationError("Reiseoperationen unterstützen nur update")
            if not operation["changes"]:
                raise ValidationError("Eine Reiseaktualisierung benötigt Änderungen")
            operation.pop("entity_id", None)
            operation.pop("day_id", None)
            operation.pop("day_ref", None)
            operation.pop("position", None)
            operation.pop("place_query", None)
        elif entity_type == "day":
            operation.pop("day_id", None)
            operation.pop("day_ref", None)
            operation.pop("place_query", None)
            if action == "add":
                if not entity_id:
                    operation["entity_id"] = f"new-day-{uuid4().hex[:12]}"
                new_day_refs.add(operation["entity_id"])
                if not operation["changes"]:
                    raise ValidationError("Ein neuer Reisetag benötigt Tagesdaten")
            elif not entity_id or entity_id not in day_ids:
                raise ValidationError(
                    f"Bestehende Tages-ID ist nicht im aktuellen Roadbook vorhanden: {entity_id or 'fehlt'}"
                )
        elif entity_type == "stop":
            day_id, day_ref = _normalize_compiled_day_reference(
                operation,
                day_ids=day_ids,
                new_day_refs=new_day_refs,
            )
            if day_id and day_ref:
                raise ValidationError("Eine Stoppoperation darf nicht day_id und day_ref mischen")
            if not day_id and not day_ref:
                inferred_day_id = RoadplannerAssistant._infer_missing_stop_day_id(
                    raw,
                    context=context,
                    basket=basket,
                    entity_id=entity_id,
                    stop_ids=stop_ids,
                )
                if inferred_day_id:
                    operation["day_id"] = inferred_day_id
                    day_id = inferred_day_id
                else:
                    raise ValidationError(
                        "Eine Stoppoperation benötigt day_id oder day_ref. "
                        "Der betroffene Reisetag konnte aus Datum, Gespräch und "
                        "aktuellem Roadbook nicht sicher abgeleitet werden."
                    )
            if day_id and day_id not in day_ids:
                raise ValidationError(f"Tages-ID der Stoppoperation ist unbekannt: {day_id}")
            if day_ref and day_ref not in new_day_refs:
                raise ValidationError(f"day_ref verweist nicht auf einen neuen Tag: {day_ref}")

            # "Wir haben hier geschlafen" describes the actual overnight point
            # that ended the previous travel day and starts the current one. If
            # that day already has exactly one canonical overnight stop, update
            # it instead of creating a duplicate.
            if (
                action == "add"
                and day_id
                and RoadplannerAssistant._is_actual_past_overnight(raw, basket=basket)
            ):
                day_detail = RoadplannerAssistant._day_detail(context, day_id)
                overnight_stops = [
                    stop
                    for stop in canonical_roadbook_stops(day_detail or {})
                    if isinstance(stop, dict)
                    and str(stop.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES
                    and stop.get("id")
                ]
                if len(overnight_stops) == 1:
                    action = "update"
                    operation["action"] = "update"
                    entity_id = str(overnight_stops[0]["id"])
                    operation["entity_id"] = entity_id
                    operation.pop("position", None)

            if action == "add":
                if not entity_id:
                    operation["entity_id"] = f"new-stop-{uuid4().hex[:12]}"
                if not _clean_text(operation["changes"].get("name"), maximum=500):
                    raise ValidationError("Ein neuer Stopp benötigt einen Namen")
                stop_type = _clean_text(
                    operation["changes"].get("type") or "waypoint",
                    maximum=100,
                ).casefold()
                if stop_type not in _ALLOWED_STOP_TYPES:
                    raise ValidationError(f"Nicht unterstützter Stopptyp: {stop_type}")
                operation["changes"]["type"] = stop_type
                if not operation.get("place_query"):
                    raise ValidationError(
                        "Ein neuer konkreter Stopp benötigt place_query für die GPS-Prüfung"
                    )
                if position_state is not None:
                    parent_ref = str(day_id or day_ref or "")
                    state = position_state.setdefault(parent_ref, [])
                    requested = operation.get("position")
                    if (
                        isinstance(requested, int)
                        and not isinstance(requested, bool)
                        and requested > 0
                    ):
                        insert_at = min(requested - 1, len(state))
                    elif stop_type in OVERNIGHT_STOP_TYPES:
                        insert_at = len(state)
                    else:
                        overnight_index = next(
                            (
                                state_index
                                for state_index, item in enumerate(state)
                                if str(item.get("type") or "").casefold() in OVERNIGHT_STOP_TYPES
                            ),
                            len(state),
                        )
                        insert_at = overnight_index
                    operation["position"] = insert_at + 1
                    state.insert(
                        insert_at,
                        {
                            "id": str(operation.get("entity_id") or ""),
                            "type": stop_type,
                        },
                    )
            else:
                if not day_id:
                    raise ValidationError("Bestehende Stopps müssen über day_id referenziert werden")
                if not entity_id or entity_id not in stop_ids.get(day_id, set()):
                    raise ValidationError(
                        f"Bestehende Stopp-ID ist nicht im aktuellen Reisetag vorhanden: {entity_id or 'fehlt'}"
                    )
                if "type" in operation["changes"]:
                    stop_type = _clean_text(
                        operation["changes"].get("type"),
                        maximum=100,
                    ).casefold()
                    if stop_type not in _ALLOWED_STOP_TYPES:
                        raise ValidationError(f"Nicht unterstützter Stopptyp: {stop_type}")
                    operation["changes"]["type"] = stop_type
                if position_state is not None and day_id:
                    state = position_state.setdefault(day_id, [])
                    current_index = next(
                        (
                            state_index
                            for state_index, item in enumerate(state)
                            if item.get("id") == str(entity_id or "")
                        ),
                        None,
                    )
                    if action == "remove":
                        if current_index is not None:
                            state.pop(current_index)
                    else:
                        item = (
                            state.pop(current_index)
                            if current_index is not None
                            else {"id": str(entity_id or ""), "type": "waypoint"}
                        )
                        if "type" in operation["changes"]:
                            item["type"] = str(operation["changes"]["type"])
                        requested = operation.get("position")
                        if (
                            isinstance(requested, int)
                            and not isinstance(requested, bool)
                            and requested > 0
                        ):
                            insert_at = min(requested - 1, len(state))
                        elif current_index is not None:
                            insert_at = min(current_index, len(state))
                        else:
                            insert_at = len(state)
                        state.insert(insert_at, item)
                        if action == "move" or "position" in operation:
                            operation["position"] = insert_at + 1
        else:  # preference
            operation.pop("position", None)
            operation.pop("place_query", None)
            day_id, day_ref = _normalize_compiled_day_reference(
                operation,
                day_ids=day_ids,
                new_day_refs=new_day_refs,
            )
            if day_id and day_ref:
                raise ValidationError("Eine Präferenz darf nicht day_id und day_ref mischen")
            if day_id and day_id not in day_ids:
                raise ValidationError(f"Tages-ID der Präferenz ist unbekannt: {day_id}")
            if day_ref and day_ref not in new_day_refs:
                raise ValidationError(f"day_ref der Präferenz ist unbekannt: {day_ref}")
            if action == "move":
                raise ValidationError("Präferenzen unterstützen move nicht")
            if action == "add":
                if not entity_id:
                    operation["entity_id"] = f"pref-{uuid4().hex[:12]}"
                if not _clean_text(operation["changes"].get("text"), maximum=2_000):
                    raise ValidationError("Eine neue Präferenz benötigt text")
            elif not entity_id or entity_id not in preference_ids:
                raise ValidationError(
                    f"Bestehende Präferenz-ID ist nicht im aktuellen Roadbook vorhanden: {entity_id or 'fehlt'}"
                )

        if (
            action == "update"
            and not operation["changes"]
            and "position" not in operation
            and not operation.get("place_query")
        ):
            raise ValidationError(
                "Eine Aktualisierung benötigt Änderungen, eine Position oder place_query"
            )
        if action in {"remove", "move"} and operation["changes"]:
            raise ValidationError("changes muss bei remove/move leer sein")
        return operation

    async def async_prepare_review(
        self,
        *,
        user_id: str,
        trip_id: str,
        actor: str,
    ) -> dict[str, Any]:
        self._provider()
        lock = self.sessions.lock(user_id, trip_id)
        async with lock:
            session = self.sessions.session(user_id, trip_id)
            if not session.basket:
                raise ValidationError("Der Änderungskorb ist leer")
            request_id = f"prepare-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            plugin_diagnostics: list[dict[str, Any]] = []
            try:
                payload = await self._load_trip_payload(trip_id)
                if not payload.get("selected_is_active"):
                    raise ValidationError(
                        "Änderungen können nur für die aktive Reise vorbereitet werden. "
                        "Bitte diese Reise zuerst aktivieren."
                    )
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="compile",
                    basket=session.basket,
                )
                compile_result = await self._compile_operations(
                    context=context,
                    basket=session.basket,
                    messages=session.messages,
                )
                compiled = compile_result.value
                raw_operations = compiled.get("operations")
                if not isinstance(raw_operations, list):
                    raise ValidationError("Der Assistent hat keine Operationsliste geliefert")
                operations: list[dict[str, Any]] = []

                prepared_raw_operations, new_day_refs = (
                    _prepare_compiled_operation_batch(raw_operations)
                )
                position_state: dict[str, list[dict[str, str]]] = {}
                for context_day in context.get("days", []):
                    if not isinstance(context_day, dict):
                        continue
                    context_day_id = _clean_text(context_day.get("id"), maximum=200)
                    if not context_day_id:
                        continue
                    position_state[context_day_id] = [
                        {
                            "id": _clean_text(stop.get("id"), maximum=200),
                            "type": _clean_text(stop.get("type"), maximum=100).casefold(),
                        }
                        for stop in canonical_roadbook_stops(context_day)
                        if isinstance(stop, dict)
                    ]
                for day_ref in new_day_refs:
                    position_state.setdefault(day_ref, [])
                for index, raw in enumerate(prepared_raw_operations):
                    operations.append(
                        self._sanitize_operation(
                            raw,
                            index=index,
                            context=context,
                            new_day_refs=new_day_refs,
                            basket=session.basket,
                            position_state=position_state,
                        )
                    )
                open_questions, open_questions_omitted = _normalize_text_items(
                    compiled.get("open_questions"),
                    maximum_items=100,
                    maximum_text=2_000,
                )
                assumptions, assumptions_omitted = _normalize_text_items(
                    compiled.get("assumptions"),
                    maximum_items=100,
                    maximum_text=2_000,
                )
                source_notes = [
                    f"Quelle: {source.title} – {source.url}"
                    for source in compile_result.sources
                ]
                research_notes, research_notes_omitted = _normalize_text_items(
                    [compiled.get("research_notes"), source_notes],
                    maximum_items=100,
                    maximum_text=2_000,
                )
                enriched = await self.plugins.async_enrich_operations(
                    operations=operations,
                    open_questions=open_questions,
                    context=context,
                )
                operations = enriched.operations
                open_questions, plugin_questions_omitted = _normalize_text_items(
                    enriched.open_questions,
                    maximum_items=100,
                    maximum_text=2_000,
                )
                open_questions_omitted += plugin_questions_omitted
                plugin_diagnostics = enriched.diagnostics
                normalization_omissions = {
                    "open_questions": open_questions_omitted,
                    "assumptions": assumptions_omitted,
                    "research_notes": research_notes_omitted,
                }
                normalization_omissions = {
                    key: value
                    for key, value in normalization_omissions.items()
                    if value
                }
                if normalization_omissions:
                    _LOGGER.warning(
                        "Assistant compile response %s exceeded text-list limits; "
                        "omitted entries: %s",
                        request_id,
                        normalization_omissions,
                    )
                if not operations:
                    reason = open_questions[0] if open_questions else "Keine sichere Änderung ableitbar"
                    raise ValidationError(
                        "Aus dem Änderungskorb konnte keine sicher ausführbare Operation erstellt werden: "
                        + reason
                    )

                revision = context.get("revision")
                trip = context.get("trip") if isinstance(context.get("trip"), dict) else {}
                canonical_trip_id = str(trip.get("id") or context.get("selected_trip_id") or "")
                if not canonical_trip_id or isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValidationError("Aktuelle Reise-ID oder Revision konnte nicht gelesen werden")
                changeset_id = str(uuid4())
                title = _clean_text(compiled.get("title"), maximum=500) or "Roadplanner-Assistent"
                summary = _clean_text(compiled.get("summary"), maximum=5_000)
                changeset: dict[str, Any] = {
                    "kind": "roadplanner_changeset",
                    "version": 1,
                    "changeset_id": changeset_id,
                    "trip_id": canonical_trip_id,
                    "base_revision": revision,
                    "created_at": _utc_now_iso(),
                    "title": title,
                    "summary": summary,
                    "apply_mode": "review",
                    "operations": operations,
                    "open_questions": open_questions,
                    "assumptions": assumptions,
                    "research_notes": research_notes,
                    "metadata": {
                        "created_by": "roadplanner_assistant",
                        "provider": self.provider_name,
                        "model": compile_result.diagnostics.get("model") or self.model,
                        "user_id": user_id,
                        "actor": actor,
                        "basket_item_ids": [item.get("id") for item in session.basket],
                        "request_id": request_id,
                        "plugins": [item.get("plugin") for item in plugin_diagnostics],
                        "text_list_omissions": normalization_omissions,
                    },
                }
                source_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "basket": session.basket,
                            "revision": revision,
                            "changeset": changeset,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
                ingest = await self.manager.async_ingest_external_changeset(
                    changeset=changeset,
                    title=title,
                    source="roadplanner_assistant",
                    external_id=changeset_id,
                    metadata={
                        "assistant": {
                            "provider": self.provider_name,
                            "model": compile_result.diagnostics.get("model") or self.model,
                            "user_id": user_id,
                            "actor": actor,
                            "review_only": True,
                            "request_id": request_id,
                        }
                    },
                    source_payload_sha256=source_digest,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="prepare_review",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                    plugin_diagnostics=plugin_diagnostics,
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected assistant review preparation failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="prepare_review",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                    plugin_diagnostics=plugin_diagnostics,
                )
                raise ValidationError(
                    "Der Änderungsentwurf konnte nicht sicher vorbereitet werden. "
                    f"Bitte erneut versuchen (Anfrage {request_id})."
                ) from err

            session.basket = []
            session.updated_at = _utc_now_iso()
            self.sessions.append_message(
                session,
                role="assistant",
                content=(
                    "Die vorgemerkten Änderungen wurden an die Änderungsübersicht "
                    "übergeben. Dort kannst du sie prüfen, übernehmen oder ablehnen. "
                    "Das Reisegespräch läuft weiter."
                ),
                kind="status",
            )
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="prepare_review",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=compile_result.diagnostics,
                usage=compile_result.usage,
                plugin_diagnostics=plugin_diagnostics,
            )
            return {
                "request_id": request_id,
                "changeset_id": changeset_id,
                "handoff": ingest.get("handoff"),
                "preview": ingest.get("preview"),
                "assistant": self.state(user_id, trip_id),
                "usage": compile_result.usage,
                "provider_diagnostics": compile_result.diagnostics,
                "logical_api_calls": 1,
            }

