"""Turn one compiled+alias-normalized assistant operation into a strictly
validated ChangeSet operation.

This is the final gate before an operation is accepted into a ChangeSet:
resolve day/stop IDs from context and relative-day/overnight-continuity
markers in free text, then validate the operation envelope, allowed fields,
and stop-ordering position bookkeeping. Nothing here talks to a provider.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any
from uuid import uuid4

from .assistant_basket import _deep_without_none
from .assistant_compile import (
    _ALLOWED_ACTIONS,
    _ALLOWED_CHANGE_FIELDS,
    _ALLOWED_OPERATION_FIELDS,
    _ALLOWED_STOP_TYPES,
    _CHANGE_FIELDS_BY_ENTITY,
    _SERVER_CONTROLLED_OPERATION_FIELDS,
    _normalize_compiled_day_reference,
    _normalize_compiled_operation_aliases,
)
from .assistant_shared import (
    OVERNIGHT_STOP_TYPES,
    _ALLOWED_ENTITY_TYPES,
    _GERMAN_DATE_IN_TEXT,
    _ISO_DATE_IN_TEXT,
    _clean_text,
)
from .canonical_day import canonical_roadbook_stops
from .roadplanner import ValidationError
from .structured_output import StructuredOutputError, normalize_changes_mapping

_LOGGER = logging.getLogger(__name__)

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

def _relative_day_id(
    context: dict[str, Any],
    offset: int,
) -> str:
    sequence = _context_day_sequence(context)
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

def _day_id_for_date(context: dict[str, Any], requested: str) -> str:
    requested = _clean_text(requested, maximum=20)
    if not requested:
        return ""
    for item in _context_day_sequence(context):
        if item["date"] == requested:
            return item["id"]
    return ""

def _operation_context_text(
    raw: dict[str, Any],
    *,
    basket: list[dict[str, Any]] | None,
    strict: bool = False,
) -> str:
    """Build a bounded semantic text used for parent-day/overnight inference.

    When no basket item can be matched to this operation by place_query or
    name, and the basket happens to hold exactly one stop item, that lone
    item's text is used as a last-resort hint (`strict=False`) - reasonable
    for inferring which *day* an operation belongs to, where a wrong guess is
    low-stakes and reviewable. It must NOT feed decisions that silently
    rewrite an unrelated, already-existing stop (`strict=True`): with two
    basket-unrelated compiled operations and a lone differently-themed basket
    item, the lone-item fallback could otherwise attribute e.g. "gestern
    Nacht hier übernachtet" text to a completely different new stop, making
    that stop's `add` silently become an `update` of the existing overnight
    entry and overwrite its name/notes.
    """
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
    if not matching and not strict and len(stop_items) == 1:
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
    return _day_id_for_date(
        context,
        _clean_text(item.get("day_date"), maximum=20),
    )

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

    explicit = _basket_parent_day(
        raw,
        context=context,
        basket=basket,
    )
    if explicit:
        return explicit

    text = _operation_context_text(raw, basket=basket)
    for match in _ISO_DATE_IN_TEXT.findall(text):
        day_id = _day_id_for_date(context, match)
        if day_id:
            return day_id
    for day_value, month_value, year_value in _GERMAN_DATE_IN_TEXT.findall(text):
        try:
            normalized = f"{int(year_value):04d}-{int(month_value):02d}-{int(day_value):02d}"
        except ValueError:
            continue
        day_id = _day_id_for_date(context, normalized)
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
        previous_id = _relative_day_id(context, -1)
        if previous_id:
            return previous_id
    if is_overnight and has_current_overnight_marker:
        current_id = _relative_day_id(context, 0)
        if current_id:
            return current_id
    if any(marker in text for marker in _PREVIOUS_DAY_MARKERS):
        previous_id = _relative_day_id(context, -1)
        if previous_id:
            return previous_id
    if any(marker in text for marker in _NEXT_DAY_MARKERS):
        next_id = _relative_day_id(context, 1)
        if next_id:
            return next_id
    if any(marker in text for marker in _CURRENT_DAY_MARKERS):
        current_id = _relative_day_id(context, 0)
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

def _day_detail(context: dict[str, Any], day_id: str) -> dict[str, Any] | None:
    for day in context.get("days", []):
        if isinstance(day, dict) and str(day.get("id") or "") == day_id:
            return day
    return None

def _is_actual_past_overnight(
    raw: dict[str, Any],
    *,
    basket: list[dict[str, Any]] | None,
) -> bool:
    """Whether this operation genuinely describes last night's overnight.

    Deciding "yes" silently converts a new stop `add` into an `update` of an
    existing overnight entry (see the call site), so this must never rely on
    the lone-basket-item fallback - only an operation whose own text, or a
    basket item actually matched to it by place_query/name, mentions a past
    overnight marker qualifies.
    """
    changes = raw.get("changes") if isinstance(raw.get("changes"), dict) else {}
    stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
    text = _operation_context_text(raw, basket=basket, strict=True)
    has_past_marker = any(marker in text for marker in _PAST_OVERNIGHT_MARKERS)
    return has_past_marker and (
        stop_type in OVERNIGHT_STOP_TYPES or "übernacht" in text or "geschlafen" in text
    )

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

    day_ids, stop_ids, preference_ids = _known_ids(context)
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
            inferred_day_id = _infer_missing_stop_day_id(
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
            and _is_actual_past_overnight(raw, basket=basket)
        ):
            day_detail = _day_detail(context, day_id)
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
