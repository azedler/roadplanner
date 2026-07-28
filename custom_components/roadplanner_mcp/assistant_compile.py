"""Normalize a provider's raw "compile" JSON into the canonical operation
dialect, and build the bounded Roadbook context passed to the provider.

This sits between the assistant's conversational/basket layer and the final
per-operation sanitizer: it repairs kebab/camel-case aliases and new-day
temp-ID references from the model's compiled output into the shape the
strict ChangeSet-facing validation expects.
"""

from __future__ import annotations

from copy import deepcopy
import logging
import re
from typing import Any
from uuid import uuid4

from homeassistant.util import dt as dt_util

from .assistant_prompt import json_context
from .assistant_shared import (
    _ALLOWED_ENTITY_TYPES,
    _BASKET_ENTITY_ALIASES,
    _clean_text,
)
from .canonical_day import canonical_roadbook_stops, decorate_canonical_days
from .roadplanner import ValidationError
from .structured_output import StructuredOutputError, normalize_changes_mapping

_LOGGER = logging.getLogger(__name__)

MAX_CONTEXT_CHARACTERS = 180_000

_ALLOWED_ACTIONS = {"add", "update", "remove", "move"}
_ALLOWED_OPERATION_FIELDS = {
    "operation_id",
    "action",
    "entity_type",
    "entity_id",
    "day_id",
    "day_ref",
    "position",
    "changes",
    "reason",
    "place_query",
}
# Gemini may repeat ChangeSet envelope metadata inside individual operations,
# especially when the provider falls back to MIME-only JSON mode. These fields
# are server-controlled and must never be accepted from the model. They are
# therefore removed before validating the actual operation payload.
_SERVER_CONTROLLED_OPERATION_FIELDS = {
    "trip_id",
    "base_revision",
    "changeset_id",
    "created_at",
    "apply_mode",
    "kind",
    "version",
    "metadata",
}
_CHANGE_FIELDS_BY_ENTITY = {
    # Keep this compatibility schema aligned with changeset.py. The assistant
    # compiles into the canonical ChangeSet model, so all fields accepted by
    # that model must also survive the assistant validation layer.
    "trip": {
        "title",
        "status",
        "start_date",
        "end_date",
        "travelers",
        "vehicle",
        "preferences",
        "notes",
        "details",
    },
    "day": {
        "date",
        "title",
        "start",
        "end",
        "distance_km",
        "drive_minutes",
        "status",
        "notes",
        "details",
    },
    "stop": {
        "name",
        "type",
        "arrival_time",
        "departure_time",
        "location",
        "notes",
        "details",
    },
    "preference": {
        "category",
        "text",
        "status",
        "notes",
        "reason",
        "details",
    },
}
_ALLOWED_CHANGE_FIELDS = set().union(*_CHANGE_FIELDS_BY_ENTITY.values())
_ALLOWED_STOP_TYPES = {
    "start",
    "origin",
    "destination",
    "overnight",
    "campsite",
    "camping",
    "stellplatz",
    "wildcamp",
    "accommodation",
    "parking",
    "sightseeing",
    "attraction",
    "activity",
    "restaurant",
    "shopping",
    "fuel",
    "charging",
    "service",
    "water",
    "waste",
    "laundry",
    "ferry",
    "border",
    "break",
    "viewpoint",
    "fishing",
    "waypoint",
}


def _normalize_compiled_operation_aliases(
    raw: dict[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    """Normalize safe model aliases before strict operation validation.

    Gemini occasionally returns identifiers from the canonical ChangeSet dialect
    (for example ``stop_id``) even though the assistant compile schema uses the
    provider-neutral ``entity_id`` field.  The aliases below are losslessly
    translated and conflicts are rejected.  No unknown identifier is trusted:
    the normal Roadbook ID checks still run afterwards.
    """

    result = deepcopy(raw)

    def merge_key_alias(
        canonical: str,
        aliases: tuple[str, ...],
    ) -> None:
        """Merge spelling aliases into one canonical operation key.

        Gemini may fall back to MIME-only JSON and then emit kebab-case or
        camelCase keys despite the supplied response schema.  Only known,
        lossless aliases are accepted here.  Conflicting spellings remain a
        hard validation error so the server never guesses which target was
        intended.
        """

        canonical_present = canonical in result and result.get(canonical) not in (None, "")
        canonical_value = result.get(canonical)
        for alias in aliases:
            if alias not in result:
                continue
            alias_value = result.pop(alias)
            if alias_value in (None, ""):
                continue
            if canonical_present and canonical_value != alias_value:
                raise ValidationError(
                    f"Widersprüchliche Felder in Assistenten-Operation {index + 1}: "
                    f"{canonical}={canonical_value}, {alias}={alias_value}"
                )
            if not canonical_present:
                result[canonical] = alias_value
                canonical_value = alias_value
                canonical_present = True

    # Normalize only documented operation metadata.  Business data below
    # ``details`` is deliberately left untouched.
    operation_key_aliases: dict[str, tuple[str, ...]] = {
        "operation_id": ("operation-id", "operationId"),
        "entity_type": (
            "entity-type",
            "entityType",
            "object-type",
            "objectType",
            "entity",
        ),
        "entity_id": ("entity-id", "entityId"),
        "day_id": ("day-id", "dayId"),
        "day_ref": ("day-ref", "dayRef"),
        "place_query": ("place-query", "placeQuery"),
        "stop_id": ("stop-id", "stopId"),
        "stop_ref": ("stop-ref", "stopRef"),
        "preference_id": ("preference-id", "preferenceId"),
        "target_id": ("target-id", "targetId"),
        "client_id": ("client-id", "clientId"),
        "temp_id": ("temp-id", "tempId"),
        "source_day_id": ("source-day-id", "sourceDayId"),
        "source_stop_id": ("source-stop-id", "sourceStopId"),
        "trip_id": ("trip-id", "tripId"),
        "base_revision": ("base-revision", "baseRevision"),
        "changeset_id": (
            "changeset-id",
            "changesetId",
            "changeSetId",
        ),
        "created_at": ("created-at", "createdAt"),
        "apply_mode": ("apply-mode", "applyMode"),
    }
    for canonical, aliases in operation_key_aliases.items():
        merge_key_alias(canonical, aliases)

    def normalize_entity_hint(value: Any) -> tuple[str, str]:
        """Return canonical entity type and an optional stop subtype."""

        hint = _clean_text(value, maximum=100).casefold()
        if not hint:
            return "", ""
        canonical = _BASKET_ENTITY_ALIASES.get(hint, hint)
        stop_subtype = (
            hint
            if canonical == "stop"
            and hint in _ALLOWED_STOP_TYPES
            and hint != "stop"
            else ""
        )
        return canonical, stop_subtype

    # ``type`` is ambiguous in provider fallbacks.  At operation level it most
    # often means ``entity_type``.  If it contains a concrete stop subtype such
    # as ``parking`` or ``ferry``, preserve that semantic value in
    # ``changes.type`` while still normalizing the entity to ``stop``.
    entity_type, deferred_stop_type = normalize_entity_hint(result.get("entity_type"))
    if entity_type:
        result["entity_type"] = entity_type

    if "type" in result:
        raw_type_value = result.pop("type")
        type_entity, type_stop_subtype = normalize_entity_hint(raw_type_value)
        if type_entity and type_entity in _ALLOWED_ENTITY_TYPES:
            if entity_type and entity_type != type_entity:
                raise ValidationError(
                    f"Widersprüchlicher Assistententyp in Operation {index + 1}: "
                    f"entity_type={entity_type}, type={_clean_text(raw_type_value, maximum=100)}"
                )
            entity_type = type_entity
            result["entity_type"] = entity_type
            if type_stop_subtype:
                if deferred_stop_type and deferred_stop_type != type_stop_subtype:
                    raise ValidationError(
                        f"Widersprüchliche Stopptypen in Assistenten-Operation {index + 1}: "
                        f"{deferred_stop_type} != {type_stop_subtype}"
                    )
                deferred_stop_type = type_stop_subtype
        elif entity_type == "stop" and type_stop_subtype:
            if deferred_stop_type and deferred_stop_type != type_stop_subtype:
                raise ValidationError(
                    f"Widersprüchliche Stopptypen in Assistenten-Operation {index + 1}: "
                    f"{deferred_stop_type} != {type_stop_subtype}"
                )
            deferred_stop_type = type_stop_subtype
        elif raw_type_value not in (None, ""):
            # Keep truly unknown top-level values visible to strict validation.
            result["type"] = raw_type_value

    entity_type = str(result.get("entity_type") or "").casefold()
    action = str(result.get("action") or "").casefold()

    def merge_value(target: str, aliases: tuple[str, ...], *, maximum: int = 500) -> None:
        target_value = _clean_text(result.get(target), maximum=maximum)
        for alias in aliases:
            alias_value = _clean_text(result.pop(alias, None), maximum=maximum)
            if not alias_value:
                continue
            if target_value and target_value != alias_value:
                raise ValidationError(
                    f"Widersprüchliche Identifikatoren in Assistenten-Operation "
                    f"{index + 1}: {target}={target_value}, {alias}={alias_value}"
                )
            if not target_value:
                target_value = alias_value
        if target_value:
            result[target] = target_value

    def merge_object(target: str, aliases: tuple[str, ...]) -> None:
        merged: dict[str, Any] | None = None
        normalization_modes: list[str] = []

        def merge_candidate(label: str, candidate: Any) -> None:
            nonlocal merged
            if candidate is None:
                return
            try:
                normalized, mode = normalize_changes_mapping(
                    candidate,
                    allow_scalar_empty=action in {"remove", "move"},
                )
            except StructuredOutputError as err:
                raise ValidationError(
                    f"{label} in Assistenten-Operation {index + 1} konnte nicht "
                    "sicher als JSON-Objekt gelesen werden"
                ) from err
            if merged is None:
                merged = {}
            for key, value in normalized.items():
                if key in merged and merged[key] != value:
                    raise ValidationError(
                        "Widersprüchliche Änderungsdaten in Assistenten-Operation "
                        f"{index + 1}: {target}.{key}"
                    )
                merged[key] = value
            normalization_modes.append(f"{label}:{mode}")

        merge_candidate(target, result.get(target))
        for alias in aliases:
            merge_candidate(alias, result.pop(alias, None))
        if merged is not None:
            result[target] = merged
            if any(not mode.endswith(":object") for mode in normalization_modes):
                _LOGGER.debug(
                    "Normalized assistant operation %s changes (%s)",
                    index + 1,
                    ", ".join(normalization_modes),
                )

    # Accept the common canonical payload containers produced in MIME-only JSON
    # fallback mode, but keep the assistant's strict action/entity dialect.
    change_aliases = ["patch", "values"]
    if entity_type in {"day", "stop", "preference"}:
        change_aliases.append(entity_type)
    merge_object("changes", tuple(change_aliases))

    # ``id`` and client/temp IDs are harmless aliases for the assistant target.
    generic_aliases = ["id", "target_id"]
    if action == "add":
        generic_aliases.extend(["client_id", "temp_id"])
    merge_value("entity_id", tuple(generic_aliases), maximum=200)

    def merge_new_day_add_reference(
        *,
        day_id_value: Any = None,
        day_ref_value: Any = None,
    ) -> None:
        """Collapse invalid add-day parent references into the temp entity ID.

        Provider fallback output sometimes models a newly created day like a
        child object and emits both ``entity_id`` and ``day_ref``.  In the
        canonical Roadplanner dialect the new day's temporary reference belongs
        in ``entity_id``; ``day_ref`` is reserved for stops/preferences that
        point at that new day.  A particularly common variant copies the
        operation ID into ``entity_id`` and puts the actual temporary day
        reference into ``day_ref``.  That shape is losslessly repairable.
        """

        entity_value = _clean_text(result.get("entity_id"), maximum=200)
        operation_value = _clean_text(result.get("operation_id"), maximum=200)
        day_id_alias = _clean_text(day_id_value, maximum=200)
        day_ref_alias = _clean_text(day_ref_value, maximum=200)

        if day_id_alias and day_ref_alias and day_id_alias != day_ref_alias:
            raise ValidationError(
                "Widersprüchliche Tagesreferenzen in Assistenten-Operation "
                f"{index + 1}: day_id={day_id_alias}, day_ref={day_ref_alias}"
            )

        # ``day_ref`` is the strongest signal for a temporary new-day
        # reference.  ``day_id`` is accepted as a compatibility alias.
        preferred = day_ref_alias or day_id_alias

        def identifier_key(value: str) -> str:
            """Compare generated identifiers independent of '-'/'_' spelling."""

            return re.sub(r"[^a-z0-9]+", "", value.casefold())

        repeats_operation_id = bool(
            entity_value
            and operation_value
            and identifier_key(entity_value) == identifier_key(operation_value)
        )
        looks_like_operation_id = entity_value.casefold().startswith(
            ("op-", "op_", "operation-", "operation_")
        ) if entity_value else False
        looks_like_new_day_ref = preferred.casefold().startswith(
            ("new-day-", "new_day_", "tmp-day-", "tmp_day_", "day-ref-", "day_ref_")
        ) if preferred else False

        if preferred:
            if (
                not entity_value
                or entity_value == preferred
                or repeats_operation_id
                or (looks_like_operation_id and looks_like_new_day_ref)
            ):
                result["entity_id"] = preferred
                return
            raise ValidationError(
                "Widersprüchliche Identifikatoren in Assistenten-Operation "
                f"{index + 1}: entity_id={entity_value}, "
                f"day_ref/day_id={preferred}"
            )

        # An operation ID is not a valid object reference.  Clearing this
        # common provider mistake lets the server generate a proper new-day ID.
        if repeats_operation_id:
            result.pop("entity_id", None)

    if entity_type == "stop":
        merge_value("entity_id", ("stop_id", "stop_ref"), maximum=200)
    elif entity_type == "day":
        # For existing day operations day_id/day_ref are compatibility aliases
        # for the target day.  For add-day operations, however, day_ref belongs
        # only on child operations and is collapsed into the new day's
        # temporary entity_id.
        if action == "add":
            merge_new_day_add_reference(
                day_id_value=result.pop("day_id", None),
                day_ref_value=result.pop("day_ref", None),
            )
        else:
            merge_value("entity_id", ("day_id", "day_ref"), maximum=200)
    elif entity_type == "preference":
        merge_value("entity_id", ("preference_id",), maximum=200)

    changes = result.get("changes")
    if isinstance(changes, dict):
        def merge_change_key_alias(
            canonical: str,
            aliases: tuple[str, ...],
        ) -> None:
            canonical_present = canonical in changes and changes.get(canonical) not in (None, "")
            canonical_value = changes.get(canonical)
            for alias in aliases:
                if alias not in changes:
                    continue
                alias_value = changes.pop(alias)
                if alias_value in (None, ""):
                    continue
                if canonical_present and canonical_value != alias_value:
                    raise ValidationError(
                        "Widersprüchliche Änderungsfelder in Assistenten-Operation "
                        f"{index + 1}: changes.{canonical}={canonical_value}, "
                        f"changes.{alias}={alias_value}"
                    )
                if not canonical_present:
                    changes[canonical] = alias_value
                    canonical_value = alias_value
                    canonical_present = True

        # Normalize direct business fields and misplaced operation metadata, but
        # never recurse into free-form ``details`` content.
        change_key_aliases: dict[str, tuple[str, ...]] = {
            "start_date": ("start-date", "startDate"),
            "end_date": ("end-date", "endDate"),
            "distance_km": ("distance-km", "distanceKm"),
            "drive_minutes": ("drive-minutes", "driveMinutes"),
            "arrival_time": ("arrival-time", "arrivalTime"),
            "departure_time": ("departure-time", "departureTime"),
            "stop_type": ("stop-type", "stopType"),
            "day_date": ("day-date", "dayDate"),
            "entity_id": ("entity-id", "entityId"),
            "day_id": ("day-id", "dayId"),
            "day_ref": ("day-ref", "dayRef"),
            "place_query": ("place-query", "placeQuery"),
            "stop_id": ("stop-id", "stopId"),
            "stop_ref": ("stop-ref", "stopRef"),
            "preference_id": ("preference-id", "preferenceId"),
            "target_id": ("target-id", "targetId"),
            "client_id": ("client-id", "clientId"),
            "temp_id": ("temp-id", "tempId"),
            "source_day_id": ("source-day-id", "sourceDayId"),
            "source_stop_id": ("source-stop-id", "sourceStopId"),
        }
        for canonical, aliases in change_key_aliases.items():
            merge_change_key_alias(canonical, aliases)

        if "stop_type" in changes:
            stop_type_alias = _clean_text(changes.pop("stop_type"), maximum=100).casefold()
            existing_stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
            if existing_stop_type and stop_type_alias and existing_stop_type != stop_type_alias:
                raise ValidationError(
                    f"Widersprüchliche Stopptypen in Assistenten-Operation {index + 1}: "
                    f"changes.type={existing_stop_type}, changes.stop_type={stop_type_alias}"
                )
            if stop_type_alias and not existing_stop_type:
                changes["type"] = stop_type_alias

        if deferred_stop_type:
            existing_stop_type = _clean_text(changes.get("type"), maximum=100).casefold()
            if existing_stop_type and existing_stop_type != deferred_stop_type:
                raise ValidationError(
                    f"Widersprüchliche Stopptypen in Assistenten-Operation {index + 1}: "
                    f"changes.type={existing_stop_type}, type={deferred_stop_type}"
                )
            if not existing_stop_type:
                changes["type"] = deferred_stop_type

        if "day_date" in changes:
            day_date_alias = changes.pop("day_date")
            if "date" in changes and changes.get("date") not in (None, "") and changes.get("date") != day_date_alias:
                raise ValidationError(
                    f"Widersprüchliche Datumsfelder in Assistenten-Operation {index + 1}: "
                    f"changes.date={changes.get('date')}, changes.day_date={day_date_alias}"
                )
            if changes.get("date") in (None, "") and day_date_alias not in (None, ""):
                changes["date"] = day_date_alias

        def lift_nested_text(
            target: str,
            aliases: tuple[str, ...],
            *,
            maximum: int = 500,
            label: str | None = None,
        ) -> None:
            """Lift operation metadata that Gemini nested below ``changes``.

            MIME-only JSON fallbacks occasionally ignore the response schema and
            place identifiers next to the business fields.  These values are not
            changes to the entity itself.  Move only lossless metadata aliases and
            reject conflicting values instead of guessing.
            """

            target_value = _clean_text(result.get(target), maximum=maximum)
            for alias in aliases:
                nested_value = _clean_text(changes.pop(alias, None), maximum=maximum)
                if not nested_value:
                    continue
                if target_value and target_value != nested_value:
                    display = label or target
                    raise ValidationError(
                        "Widersprüchliche Metadaten in Assistenten-Operation "
                        f"{index + 1}: {display}={target_value}, "
                        f"changes.{alias}={nested_value}"
                    )
                if not target_value:
                    target_value = nested_value
            if target_value:
                result[target] = target_value

        # Target identifiers belong to the operation, never to ``changes``.
        generic_nested_ids = ["entity_id", "id", "target_id"]
        if action == "add":
            generic_nested_ids.extend(["client_id", "temp_id"])
        lift_nested_text("entity_id", tuple(generic_nested_ids), maximum=200)

        if entity_type == "stop":
            lift_nested_text(
                "entity_id",
                ("stop_id", "stop_ref", "source_stop_id"),
                maximum=200,
            )
            lift_nested_text(
                "day_id",
                ("day_id", "source_day_id"),
                maximum=200,
            )
            lift_nested_text("day_ref", ("day_ref",), maximum=200)
        elif entity_type == "day":
            if action == "add":
                # A newly created day cannot itself have a parent day.  Treat
                # nested day_id/day_ref as compatibility aliases for the new
                # day's temporary entity_id.  This also repairs the common
                # entity_id=operation_id + day_ref=new-day-* provider shape.
                merge_new_day_add_reference(
                    day_id_value=changes.pop("day_id", None),
                    day_ref_value=changes.pop("day_ref", None),
                )
            else:
                # For an existing day operation a nested day_id/day_ref
                # identifies the day itself, exactly like top-level aliases.
                lift_nested_text(
                    "entity_id",
                    ("day_id", "day_ref"),
                    maximum=200,
                    label="entity_id",
                )
        elif entity_type == "preference":
            lift_nested_text(
                "entity_id",
                ("preference_id",),
                maximum=200,
            )
            lift_nested_text("day_id", ("day_id",), maximum=200)
            lift_nested_text("day_ref", ("day_ref",), maximum=200)

        # Geocoding search strings and list positions are operation metadata.
        lift_nested_text(
            "place_query",
            ("place_query",),
            maximum=500,
            label="place_query",
        )

        # ``changes.location`` is never trusted from the model directly - only
        # the server-side geocoding plugin may populate it, from a confirmed
        # place_query. A JSON-mode fallback that ignores the response schema
        # can still put a raw place name/address (or even a hand-built
        # object) straight into changes.location; salvage any text it holds
        # into place_query (unless one is already set) and drop the rest,
        # instead of letting an untyped value reach the ChangeSet and fail
        # validation deep inside execute_changeset.
        if entity_type == "stop" and "location" in changes:
            stray_location = changes.pop("location")
            location_text = ""
            if isinstance(stray_location, str):
                location_text = stray_location.strip()
            elif isinstance(stray_location, dict):
                for key in ("label", "address", "name"):
                    candidate = stray_location.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        location_text = candidate.strip()
                        break
            if location_text and not _clean_text(result.get("place_query"), maximum=500):
                result["place_query"] = location_text[:500]

        # ``changes.text`` is only a valid field for entity_type=preference
        # (its free-text content). A JSON-mode fallback that ignores the
        # response schema can still put descriptive text meant as a stop's/
        # day's/trip's notes under ``text`` instead - reject outright would
        # discard real user-relevant content the model did manage to extract
        # (e.g. booking facts from a resolved link); salvage it into notes
        # instead, appending to any notes already present.
        if entity_type != "preference" and "text" in changes:
            stray_text = _clean_text(changes.pop("text"), maximum=8_000)
            if stray_text:
                existing_notes = _clean_text(changes.get("notes"), maximum=8_000)
                changes["notes"] = (
                    f"{existing_notes}\n{stray_text}" if existing_notes else stray_text
                )[:8_000]

        nested_position = changes.pop("position", None)
        if nested_position not in (None, ""):
            if isinstance(nested_position, bool):
                raise ValidationError(
                    f"changes.position in Assistenten-Operation {index + 1} "
                    "muss eine positive Ganzzahl sein"
                )
            if isinstance(nested_position, str) and nested_position.strip().isdigit():
                nested_position = int(nested_position.strip())
            if not isinstance(nested_position, int) or nested_position <= 0:
                raise ValidationError(
                    f"changes.position in Assistenten-Operation {index + 1} "
                    "muss eine positive Ganzzahl sein"
                )
            top_level_position = result.get("position")
            if top_level_position not in (None, ""):
                if isinstance(top_level_position, str) and top_level_position.strip().isdigit():
                    top_level_position = int(top_level_position.strip())
                if top_level_position != nested_position:
                    raise ValidationError(
                        "Widersprüchliche Positionen in Assistenten-Operation "
                        f"{index + 1}: position={top_level_position}, "
                        f"changes.position={nested_position}"
                    )
            result["position"] = nested_position

    return result


def _prepare_compiled_operation_batch(
    raw_operations: list[Any],
) -> tuple[list[Any], set[str]]:
    """Normalize a compiled operation batch and resolve new-day references.

    Gemini fallback output can use an add-day operation's ``operation_id`` as
    ``entity_id`` while placing the intended temporary day reference in
    ``day_ref``.  Child stops can then reference either spelling, and some
    outputs use ``day_id`` even though the parent day is new.  This helper
    assigns one canonical temporary entity ID per new day and rewrites only
    references that came from a concrete add-day operation in the same batch.
    """

    original_raw_operations: list[Any] = deepcopy(raw_operations)
    prepared_raw_operations: list[Any] = []
    for index, raw in enumerate(original_raw_operations):
        if isinstance(raw, dict):
            prepared_raw_operations.append(
                _normalize_compiled_operation_aliases(raw, index=index)
            )
        else:
            prepared_raw_operations.append(raw)

    new_day_refs: set[str] = set()
    new_day_aliases: dict[str, str] = {}

    def register_new_day_alias(alias: Any, canonical: str) -> None:
        alias_text = _clean_text(alias, maximum=200)
        if not alias_text:
            return
        existing = new_day_aliases.get(alias_text)
        if existing and existing != canonical:
            raise ValidationError(
                "Dieselbe temporäre Tagesreferenz wurde für mehrere neue "
                f"Tage verwendet: {alias_text}"
            )
        new_day_aliases[alias_text] = canonical

    for index, raw in enumerate(prepared_raw_operations):
        if not isinstance(raw, dict):
            continue
        if (
            str(raw.get("entity_type") or "").casefold() == "day"
            and str(raw.get("action") or "").casefold() == "add"
        ):
            entity_id = _clean_text(raw.get("entity_id"), maximum=200)
            if not entity_id:
                entity_id = f"new-day-{index + 1}-{uuid4().hex[:8]}"
                raw["entity_id"] = entity_id
            new_day_refs.add(entity_id)
            register_new_day_alias(entity_id, entity_id)
            register_new_day_alias(raw.get("operation_id"), entity_id)

            # Preserve every known spelling from the original model response
            # as an alias for child stop/preference operations.  The canonical
            # add-day operation itself keeps only entity_id.
            source = original_raw_operations[index]
            if isinstance(source, dict):
                for key in (
                    "entity_id",
                    "entity-id",
                    "entityId",
                    "day_id",
                    "day-id",
                    "dayId",
                    "day_ref",
                    "day-ref",
                    "dayRef",
                    "operation_id",
                    "operation-id",
                    "operationId",
                ):
                    register_new_day_alias(source.get(key), entity_id)
                nested = source.get("changes")
                if isinstance(nested, dict):
                    for key in (
                        "entity_id",
                        "entity-id",
                        "entityId",
                        "day_id",
                        "day-id",
                        "dayId",
                        "day_ref",
                        "day-ref",
                        "dayRef",
                    ):
                        register_new_day_alias(nested.get(key), entity_id)

    # Child operations may reference a newly added day through the wrong field
    # (day_id) or through the day operation's operation_id.  Rewrite only
    # aliases registered from an add-day operation in this same batch.
    for raw in prepared_raw_operations:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("entity_type") or "").casefold() not in {
            "stop",
            "preference",
        }:
            continue
        day_ref = _clean_text(raw.get("day_ref"), maximum=200)
        day_id = _clean_text(raw.get("day_id"), maximum=200)
        mapped_ref = new_day_aliases.get(day_ref) if day_ref else None
        mapped_id = new_day_aliases.get(day_id) if day_id else None
        if mapped_ref:
            raw["day_ref"] = mapped_ref
        if mapped_id:
            if mapped_ref and mapped_ref != mapped_id:
                raise ValidationError(
                    "Widersprüchliche Referenzen auf einen neuen "
                    f"Reisetag: day_id={day_id}, day_ref={day_ref}"
                )
            raw.pop("day_id", None)
            raw["day_ref"] = mapped_id

    return prepared_raw_operations, new_day_refs


def _normalize_compiled_day_reference(
    operation: dict[str, Any],
    *,
    day_ids: set[str],
    new_day_refs: set[str],
) -> tuple[str, str]:
    """Normalize a model's day_id/day_ref field without guessing by prefix.

    Existing Roadbook IDs belong in ``day_id``. Temporary IDs created by an
    add-day operation in the same compiled batch belong in ``day_ref``. The
    rewrite is allowed only when the exact value is present in one authoritative
    catalog; unknown and contradictory references remain validation errors.
    """

    day_id = _clean_text(operation.get("day_id"), maximum=200)
    day_ref = _clean_text(operation.get("day_ref"), maximum=200)

    if day_id and day_ref:
        if day_id != day_ref:
            raise ValidationError(
                "Widersprüchliche Tagesreferenzen: "
                f"day_id={day_id}, day_ref={day_ref}"
            )
        if day_id in day_ids:
            operation["day_id"] = day_id
            operation.pop("day_ref", None)
            return day_id, ""
        if day_id in new_day_refs:
            operation.pop("day_id", None)
            operation["day_ref"] = day_id
            return "", day_id
        # Keep one field so the established unknown-ID validation below emits
        # the precise, user-facing error instead of silently accepting it.
        operation.pop("day_ref", None)
        return day_id, ""

    if day_ref and day_ref in day_ids:
        operation.pop("day_ref", None)
        operation["day_id"] = day_ref
        return day_ref, ""
    if day_id and day_id in new_day_refs:
        operation.pop("day_id", None)
        operation["day_ref"] = day_id
        return "", day_id
    return day_id, day_ref


def _with_overnight_continuity(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Annotate the logical start using the shared canonical day service."""
    result = deepcopy(days)
    payload = {"days": result}
    decorate_canonical_days(payload)
    for day in result:
        canonical = day.get("canonical") if isinstance(day.get("canonical"), dict) else {}
        route_nodes = canonical.get("route_nodes") if isinstance(canonical.get("route_nodes"), list) else []
        inherited = route_nodes[0] if route_nodes and route_nodes[0].get("_inherited") else None
        day["inherited_start_stop"] = (
            {
                "source_day_id": inherited.get("_source_day_id"),
                "source_stop_id": inherited.get("id"),
                "name": inherited.get("name"),
                "type": inherited.get("type"),
                "location": deepcopy(inherited.get("location") or {}),
                "departure_time": inherited.get("departure_time"),
                "read_only": True,
            }
            if inherited
            else None
        )
        day["stops"] = canonical_roadbook_stops(day)
        day.pop("canonical", None)
    return result


def _bounded_context(payload: dict[str, Any]) -> dict[str, Any]:
    now = dt_util.now()
    today = now.date().isoformat()
    days = _with_overnight_continuity(list(payload.get("days", {}).get("days", [])))
    for day in days:
        day["is_today"] = day.get("date") == today
    context = {
        "context_version": 1,
        "generated_for_assistant_at": now.isoformat(),
        "local_date": today,
        "local_time": now.strftime("%H:%M"),
        "timezone": str(now.tzinfo or ""),
        "selected_trip_id": payload.get("selected_trip_id"),
        "active_trip_id": payload.get("active_trip_id"),
        "selected_is_active": bool(payload.get("selected_is_active")),
        "revision": payload.get("summary", {}).get("revision"),
        "trip": deepcopy(payload.get("summary", {}).get("trip") or {}),
        "days": days,
        "day_count": payload.get("summary", {}).get("day_count", len(days)),
        "stop_count": payload.get("summary", {}).get("stop_count", 0),
        "days_truncated": bool(payload.get("days", {}).get("has_more")),
    }
    encoded = json_context(context)
    if len(encoded) <= MAX_CONTEXT_CHARACTERS:
        return context

    # Keep IDs, dates, routing fields, and locations; trim long extension data.
    for day in context["days"]:
        notes = str(day.get("notes") or "")
        day["notes"] = notes[:2_000]
        details = day.get("details")
        if isinstance(details, dict):
            day["details"] = {
                key: value
                for key, value in details.items()
                if key in {"planning_preferences", "bookings"}
            }
        for stop in canonical_roadbook_stops(day):
            stop["notes"] = str(stop.get("notes") or "")[:1_200]
            details = stop.get("details")
            if isinstance(details, dict):
                stop["details"] = {
                    key: value
                    for key, value in details.items()
                    if key in {"booking", "bookings", "opening_hours", "dog", "geocoding"}
                }
    encoded = json_context(context)
    if len(encoded) > MAX_CONTEXT_CHARACTERS:
        context["context_truncated"] = True
        context["days"] = context["days"][:30]
    return context


OPERATION_CHANGES_SCHEMA: dict[str, Any] = {
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
        "notes": {"type": "string", "maxLength": 8_000},
        "name": {"type": "string", "maxLength": 500},
        "type": {"type": "string", "maxLength": 100},
        "arrival_time": {"type": "string", "maxLength": 20},
        "departure_time": {"type": "string", "maxLength": 20},
        "category": {"type": "string", "maxLength": 200},
        "text": {"type": "string", "maxLength": 2_000},
    },
    "additionalProperties": False,
}


COMPILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "maxLength": 500},
        "summary": {"type": "string", "maxLength": 5_000},
        "operations": {
            "type": "array",
            "minItems": 0,
            "maxItems": 100,
            "items": {
                "type": "object",
                "properties": {
                    "operation_id": {"type": "string", "maxLength": 200},
                    "action": {
                        "type": "string",
                        "enum": ["add", "update", "remove", "move"],
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["trip", "day", "stop", "preference"],
                    },
                    "entity_id": {"type": "string", "maxLength": 200},
                    "day_id": {"type": "string", "maxLength": 200},
                    "day_ref": {"type": "string", "maxLength": 200},
                    "position": {"type": "integer", "minimum": 1, "maximum": 500},
                    "changes": OPERATION_CHANGES_SCHEMA,
                    "reason": {"type": "string", "maxLength": 1_000},
                    "place_query": {"type": "string", "maxLength": 500},
                },
                "required": [
                    "operation_id",
                    "action",
                    "entity_type",
                    "changes",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
        "open_questions": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "maxLength": 2_000},
        },
        "assumptions": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "maxLength": 2_000},
        },
        "research_notes": {
            "type": "array",
            "maxItems": 30,
            "items": {"type": "string", "maxLength": 2_000},
        },
    },
    "required": [
        "title",
        "summary",
        "operations",
        "open_questions",
        "assumptions",
        "research_notes",
    ],
    "additionalProperties": False,
}
