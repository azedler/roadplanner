"""Dedicated Home Assistant LLM API and Roadplanner tools."""

from __future__ import annotations

from collections.abc import Awaitable
import json
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.llm import APIInstance, LLMContext, ToolInput
from homeassistant.util.json import JsonObjectType

from .const import LLM_API_ID, LLM_API_NAME
from .manager import RoadplannerManager
from .roadplanner import RoadplannerError, ValidationError

_POSITIVE_INT = vol.All(int, vol.Range(min=1))
_NON_NEGATIVE_INT = vol.All(int, vol.Range(min=0))
_DAY_LIMIT = vol.All(int, vol.Range(min=1, max=60))
_STOP_LIMIT = vol.All(int, vol.Range(min=1, max=100))
_SEARCH_LIMIT = vol.All(int, vol.Range(min=1, max=50))
_HANDOFF_LIMIT = vol.All(int, vol.Range(min=1, max=100))
_OPERATION_LIMIT = vol.All(int, vol.Range(min=1, max=50))

API_PROMPT = """Du bist der Reiseassistent für Roadplanner in Home Assistant.

Verbindliche Regeln:
1. Für Routendaten ausschließlich die bereitgestellten roadplanner_* Werkzeuge
   verwenden. Keine generischen Datei-, Such- oder Servicewerkzeuge verwenden.
2. Vor jeder Änderung zuerst roadplanner_get_trip_summary oder das betroffene
   Tagesdokument lesen. Die gelesene revision bei jeder Routen-Schreiboperation
   unverändert als expected_revision übergeben.
3. IDs niemals erfinden. day_id und stop_id nur aus den Leseergebnissen übernehmen.
4. Änderungen gezielt mit update_trip, add/update/remove_day und
   add/update/remove_stop ausführen. Es gibt absichtlich kein freies Komplett-
   Überschreiben der Reise.
5. Bei Revisionskonflikten erneut lesen, die Nutzerabsicht auf den aktuellen Stand
   übertragen und höchstens einmal erneut schreiben.
6. Externe Übergaben sind atomare Roadplanner-ChangeSets. Zuerst
   roadplanner_preview_handoff verwenden. Nur bei status=ready und nach einer
   erforderlichen Bestätigung roadplanner_apply_handoff aufrufen. Die einzelnen
   Operationen eines ChangeSets nicht nochmals mit CRUD-Werkzeugen ausführen.
7. Destruktive ChangeSets nur nach ausdrücklicher Bestätigung mit
   confirm_destructive=true anwenden. Revisionskonflikte niemals automatisch
   auf einen neueren Stand umdeuten.
8. *_json-Felder müssen valides JSON enthalten. Leere Datums- oder Zeitstrings
   löschen den jeweiligen Wert.
9. Nach Änderungen neue Revision und konkrete Änderungen knapp bestätigen.

Die kanonischen Dateien werden ausschließlich durch Home Assistant verwaltet.
"""


def _actor(llm_context: LLMContext) -> str:
    user_id = (
        getattr(llm_context.context, "user_id", None)
        if llm_context.context
        else None
    )
    return f"llm:{llm_context.platform}:{user_id or 'anonymous'}"


def _compact_mutation(result: dict[str, Any], *extra_keys: str) -> dict[str, Any]:
    response: dict[str, Any] = {
        "changed": bool(result.get("changed")),
    }
    if "revision" in result:
        response["revision"] = result["revision"]
    for key in extra_keys:
        if key in result:
            response[key] = result[key]
    return response


class RoadplannerTool(llm.Tool):
    """Base class with explicit argument validation and error conversion."""

    def __init__(self, manager: RoadplannerManager) -> None:
        self.manager = manager

    def validated_args(self, tool_input: ToolInput) -> dict[str, Any]:
        try:
            return self.parameters(tool_input.tool_args)
        except vol.Invalid as err:
            raise HomeAssistantError(f"Ungültige Werkzeugparameter: {err}") from err

    def parse_object(self, value: str, field_name: str) -> dict[str, Any]:
        if value == "":
            return {}
        try:
            result = json.loads(value)
        except (json.JSONDecodeError, RecursionError) as err:
            raise HomeAssistantError(
                f"Ungültiges JSON in '{field_name}': {err}"
            ) from err
        if not isinstance(result, dict):
            raise HomeAssistantError(f"'{field_name}' muss ein JSON-Objekt sein")
        return result

    def parse_list(self, value: str, field_name: str) -> list[Any]:
        if value == "":
            return []
        try:
            result = json.loads(value)
        except (json.JSONDecodeError, RecursionError) as err:
            raise HomeAssistantError(
                f"Ungültiges JSON in '{field_name}': {err}"
            ) from err
        if not isinstance(result, list):
            raise HomeAssistantError(f"'{field_name}' muss eine JSON-Liste sein")
        return result

    async def call_safely(
        self,
        operation: Awaitable[dict[str, Any]],
    ) -> JsonObjectType:
        try:
            return await operation
        except RoadplannerError as err:
            raise HomeAssistantError(str(err)) from err


class GetTripSummaryTool(RoadplannerTool):
    name = "roadplanner_get_trip_summary"
    description = (
        "Returns active trip header, current revision, day and stop counts, next day, "
        "and a compact list of up to 60 days. Use this before most planning edits."
    )
    parameters = vol.Schema({})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        self.validated_args(tool_input)
        return await self.call_safely(self.manager.async_get_trip_summary())


class GetDaysTool(RoadplannerTool):
    name = "roadplanner_get_days"
    description = (
        "Returns a paginated sequence of travel days. include_stops includes at most "
        "20 compact stops per returned day."
    )
    parameters = vol.Schema(
        {
            vol.Optional("offset", default=0): _NON_NEGATIVE_INT,
            vol.Optional("limit", default=20): _DAY_LIMIT,
            vol.Optional("include_stops", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(self.manager.async_get_days(**args))


class GetDayTool(RoadplannerTool):
    name = "roadplanner_get_day"
    description = (
        "Returns one travel day by real day_id and paginated stop details. Use this "
        "before changing that day or its stops."
    )
    parameters = vol.Schema(
        {
            vol.Required("day_id"): str,
            vol.Optional("stop_offset", default=0): _NON_NEGATIVE_INT,
            vol.Optional("stop_limit", default=50): _STOP_LIMIT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(self.manager.async_get_day(**args))


class SearchStopsTool(RoadplannerTool):
    name = "roadplanner_search_stops"
    description = (
        "Searches stop names, notes, locations, and details and returns real IDs, "
        "day context, and current revision."
    )
    parameters = vol.Schema(
        {
            vol.Optional("query"): str,
            vol.Optional("stop_type"): str,
            vol.Optional("day_id"): str,
            vol.Optional("day_date"): str,
            vol.Optional("limit", default=20): _SEARCH_LIMIT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(self.manager.async_search_stops(**args))


class UpdateTripTool(RoadplannerTool):
    name = "roadplanner_update_trip"
    description = (
        "Updates selected trip header fields. expected_revision is required. "
        "travelers_json is a list; vehicle_json, preferences_json, and details_json "
        "are complete JSON objects for those fields."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Optional("title"): str,
            vol.Optional("status"): str,
            vol.Optional("start_date"): str,
            vol.Optional("end_date"): str,
            vol.Optional("travelers_json"): str,
            vol.Optional("vehicle_json"): str,
            vol.Optional("preferences_json"): str,
            vol.Optional("notes"): str,
            vol.Optional("details_json"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        patch: dict[str, Any] = {}
        for key in ("title", "status", "start_date", "end_date", "notes"):
            if key in args:
                patch[key] = args[key]
        if "travelers_json" in args:
            patch["travelers"] = self.parse_list(
                args["travelers_json"],
                "travelers_json",
            )
        for json_key, target in (
            ("vehicle_json", "vehicle"),
            ("preferences_json", "preferences"),
            ("details_json", "details"),
        ):
            if json_key in args:
                patch[target] = self.parse_object(args[json_key], json_key)
        result = await self.call_safely(
            self.manager.async_update_trip(
                patch=patch,
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
            )
        )
        response = _compact_mutation(result)
        response["trip"] = result["trip"]["trip"]
        return response


class AddDayTool(RoadplannerTool):
    name = "roadplanner_add_day"
    description = (
        "Adds a travel day at an optional 1-based position. expected_revision is "
        "required. details_json must be an object."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Optional("day_date"): str,
            vol.Optional("title"): str,
            vol.Optional("start", default=""): str,
            vol.Optional("end", default=""): str,
            vol.Optional("distance_km"): vol.Coerce(float),
            vol.Optional("drive_minutes"): _NON_NEGATIVE_INT,
            vol.Optional("status", default="planned"): str,
            vol.Optional("notes", default=""): str,
            vol.Optional("details_json"): str,
            vol.Optional("position"): _POSITIVE_INT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        details = (
            self.parse_object(args["details_json"], "details_json")
            if "details_json" in args
            else None
        )
        result = await self.call_safely(
            self.manager.async_add_day(
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                day_date=args.get("day_date"),
                title=args.get("title"),
                start=args["start"],
                end=args["end"],
                distance_km=args.get("distance_km"),
                drive_minutes=args.get("drive_minutes"),
                status=args["status"],
                notes=args["notes"],
                details=details,
                position=args.get("position"),
            )
        )
        return _compact_mutation(result, "day", "position")


class UpdateDayTool(RoadplannerTool):
    name = "roadplanner_update_day"
    description = (
        "Updates or reorders one day by real day_id. expected_revision is required. "
        "Only supplied fields change; position is 1-based."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Required("day_id"): str,
            vol.Optional("day_date"): str,
            vol.Optional("title"): str,
            vol.Optional("start"): str,
            vol.Optional("end"): str,
            vol.Optional("distance_km"): vol.Coerce(float),
            vol.Optional("drive_minutes"): _NON_NEGATIVE_INT,
            vol.Optional("status"): str,
            vol.Optional("notes"): str,
            vol.Optional("details_json"): str,
            vol.Optional("position"): _POSITIVE_INT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        patch: dict[str, Any] = {}
        mapping = {
            "day_date": "date",
            "title": "title",
            "start": "start",
            "end": "end",
            "distance_km": "distance_km",
            "drive_minutes": "drive_minutes",
            "status": "status",
            "notes": "notes",
        }
        for source, target in mapping.items():
            if source in args:
                patch[target] = args[source]
        if "details_json" in args:
            patch["details"] = self.parse_object(
                args["details_json"],
                "details_json",
            )
        result = await self.call_safely(
            self.manager.async_update_day(
                day_id=args["day_id"],
                patch=patch,
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                position=args.get("position"),
            )
        )
        return _compact_mutation(result, "day")


class RemoveDayTool(RoadplannerTool):
    name = "roadplanner_remove_day"
    description = (
        "Deletes one day by real day_id. expected_revision is required. A day with "
        "stops is rejected unless remove_stops is explicitly true."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Required("day_id"): str,
            vol.Optional("remove_stops", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        result = await self.call_safely(
            self.manager.async_remove_day(
                day_id=args["day_id"],
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                remove_stops=args["remove_stops"],
            )
        )
        return _compact_mutation(
            result,
            "removed_day",
            "removed_stop_count",
        )


class AddStopTool(RoadplannerTool):
    name = "roadplanner_add_stop"
    description = (
        "Adds a stop to an existing day. expected_revision, day_id, and name are "
        "required. location_json and details_json must be objects."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Required("day_id"): str,
            vol.Required("name"): str,
            vol.Optional("stop_type", default="waypoint"): str,
            vol.Optional("arrival_time"): str,
            vol.Optional("departure_time"): str,
            vol.Optional("location_json"): str,
            vol.Optional("notes", default=""): str,
            vol.Optional("details_json"): str,
            vol.Optional("position"): _POSITIVE_INT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        result = await self.call_safely(
            self.manager.async_add_stop(
                day_id=args["day_id"],
                name=args["name"],
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                stop_type=args["stop_type"],
                arrival_time=args.get("arrival_time"),
                departure_time=args.get("departure_time"),
                location=(
                    self.parse_object(args["location_json"], "location_json")
                    if "location_json" in args
                    else None
                ),
                notes=args["notes"],
                details=(
                    self.parse_object(args["details_json"], "details_json")
                    if "details_json" in args
                    else None
                ),
                position=args.get("position"),
            )
        )
        return _compact_mutation(result, "stop", "day_id", "position")


class UpdateStopTool(RoadplannerTool):
    name = "roadplanner_update_stop"
    description = (
        "Updates or reorders a stop by real day_id and stop_id. expected_revision "
        "is required; only supplied fields change."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Required("day_id"): str,
            vol.Required("stop_id"): str,
            vol.Optional("name"): str,
            vol.Optional("stop_type"): str,
            vol.Optional("arrival_time"): str,
            vol.Optional("departure_time"): str,
            vol.Optional("location_json"): str,
            vol.Optional("notes"): str,
            vol.Optional("details_json"): str,
            vol.Optional("position"): _POSITIVE_INT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        patch: dict[str, Any] = {}
        for key in ("name", "arrival_time", "departure_time", "notes"):
            if key in args:
                patch[key] = args[key]
        if "stop_type" in args:
            patch["type"] = args["stop_type"]
        if "location_json" in args:
            patch["location"] = self.parse_object(
                args["location_json"],
                "location_json",
            )
        if "details_json" in args:
            patch["details"] = self.parse_object(
                args["details_json"],
                "details_json",
            )
        result = await self.call_safely(
            self.manager.async_update_stop(
                day_id=args["day_id"],
                stop_id=args["stop_id"],
                patch=patch,
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                position=args.get("position"),
            )
        )
        return _compact_mutation(result, "stop", "day_id")


class RemoveStopTool(RoadplannerTool):
    name = "roadplanner_remove_stop"
    description = (
        "Deletes one stop by real day_id and stop_id. expected_revision is required."
    )
    parameters = vol.Schema(
        {
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Required("day_id"): str,
            vol.Required("stop_id"): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        result = await self.call_safely(
            self.manager.async_remove_stop(
                day_id=args["day_id"],
                stop_id=args["stop_id"],
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
            )
        )
        return _compact_mutation(result, "removed_stop", "day_id")


class ListHandoffsTool(RoadplannerTool):
    name = "roadplanner_list_handoffs"
    description = (
        "Lists pending planning handoffs imported from Drive, OneDrive, other "
        "assistants, webhooks, or the private handoff folder."
    )
    parameters = vol.Schema(
        {vol.Optional("limit", default=20): _HANDOFF_LIMIT}
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(
            self.manager.async_list_handoffs(limit=args["limit"])
        )


class GetHandoffTool(RoadplannerTool):
    name = "roadplanner_get_handoff"
    description = (
        "Reads a validated Roadplanner ChangeSet from the handoff inbox. "
        "Operations are paginated and have not necessarily been applied."
    )
    parameters = vol.Schema(
        {
            vol.Required("handoff_id"): str,
            vol.Optional("operation_offset", default=0): _NON_NEGATIVE_INT,
            vol.Optional("operation_limit", default=20): _OPERATION_LIMIT,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        envelope = await self.call_safely(
            self.manager.async_get_handoff(args["handoff_id"])
        )
        changeset = dict(envelope["changeset"])
        operations = list(changeset.pop("operations"))
        offset = args["operation_offset"]
        limit = args["operation_limit"]
        selected = operations[offset : offset + limit]
        result = {
            key: value
            for key, value in envelope.items()
            if key not in {"changeset", "raw_content", "apply_result"}
        }
        result["changeset"] = {
            **changeset,
            "operations": selected,
            "operation_offset": offset,
            "operation_total": len(operations),
            "has_more_operations": offset + len(selected) < len(operations),
        }
        return result


class PreviewHandoffTool(RoadplannerTool):
    name = "roadplanner_preview_handoff"
    description = (
        "Validates a pending ChangeSet against the active trip without changing "
        "the route. Use this before applying any handoff."
    )
    parameters = vol.Schema({vol.Required("handoff_id"): str})

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(
            self.manager.async_preview_handoff(args["handoff_id"])
        )


class ApplyHandoffTool(RoadplannerTool):
    name = "roadplanner_apply_handoff"
    description = (
        "Atomically applies one validated ChangeSet in a single route revision. "
        "expected_revision must equal its base_revision and the current revision."
    )
    parameters = vol.Schema(
        {
            vol.Required("handoff_id"): str,
            vol.Required("expected_revision"): _NON_NEGATIVE_INT,
            vol.Optional("confirm_destructive", default=False): bool,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(
            self.manager.async_apply_handoff(
                handoff_id=args["handoff_id"],
                actor=_actor(llm_context),
                expected_revision=args["expected_revision"],
                confirm_destructive=args["confirm_destructive"],
            )
        )


class ArchiveHandoffTool(RoadplannerTool):
    name = "roadplanner_archive_handoff"
    description = (
        "Archives a pending handoff when it is rejected or deliberately deferred."
    )
    parameters = vol.Schema(
        {
            vol.Required("handoff_id"): str,
            vol.Optional("resolution", default="rejected"): str,
            vol.Optional("note", default=""): str,
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: ToolInput,
        llm_context: LLMContext,
    ) -> JsonObjectType:
        args = self.validated_args(tool_input)
        return await self.call_safely(
            self.manager.async_archive_handoff(**args)
        )


class RoadplannerAPI(llm.API):
    """Isolated LLM API containing only Roadplanner functionality."""

    def __init__(self, hass: HomeAssistant, manager: RoadplannerManager) -> None:
        super().__init__(hass=hass, id=LLM_API_ID, name=LLM_API_NAME)
        self.manager = manager

    async def async_get_api_instance(self, llm_context: LLMContext) -> APIInstance:
        tools: list[llm.Tool] = [
            GetTripSummaryTool(self.manager),
            GetDaysTool(self.manager),
            GetDayTool(self.manager),
            SearchStopsTool(self.manager),
            UpdateTripTool(self.manager),
            AddDayTool(self.manager),
            UpdateDayTool(self.manager),
            RemoveDayTool(self.manager),
            AddStopTool(self.manager),
            UpdateStopTool(self.manager),
            RemoveStopTool(self.manager),
            ListHandoffsTool(self.manager),
            GetHandoffTool(self.manager),
            PreviewHandoffTool(self.manager),
            ApplyHandoffTool(self.manager),
            ArchiveHandoffTool(self.manager),
        ]
        return APIInstance(
            api=self,
            api_prompt=API_PROMPT,
            llm_context=llm_context,
            tools=tools,
        )
