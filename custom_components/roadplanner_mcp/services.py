"""Home Assistant action registration for Roadplanner."""

from __future__ import annotations

from collections.abc import Awaitable
import secrets
from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DRIVE_IMPORT_SECRET_HEADER,
    EVENT_ROADPLANNER_UPDATED,
    CONF_WEBHOOK_TOKEN,
    DOMAIN,
    MAX_DRIVE_IMPORT_BYTES,
    SERVICE_ADD_DAY,
    SERVICE_ADD_STOP,
    SERVICE_ADOPT_EXTERNAL_CHANGES,
    SERVICE_APPLY_HANDOFF,
    SERVICE_CALCULATE_DAY_ROUTE,
    SERVICE_CALCULATE_TRIP_ROUTES,
    SERVICE_ARCHIVE_HANDOFF,
    SERVICE_CREATE_BACKUP,
    SERVICE_CREATE_EXPENSE,
    SERVICE_CREATE_TODO,
    SERVICE_EXPORT_CONTEXT,
    SERVICE_EXPORT_TRIP,
    SERVICE_GET_DAY,
    SERVICE_GET_DAYS,
    SERVICE_GET_HANDOFF,
    SERVICE_GET_HANDOFF_FOLDERS,
    SERVICE_GET_HANDOFF_ENDPOINT,
    SERVICE_GET_TRIP_SUMMARY,
    SERVICE_INGEST_CHANGESET,
    SERVICE_INGEST_HANDOFF,
    SERVICE_LIST_HANDOFFS,
    SERVICE_PREVIEW_HANDOFF,
    SERVICE_LIST_TRIPS,
    SERVICE_RELOAD,
    SERVICE_REMOVE_DAY,
    SERVICE_REMOVE_STOP,
    SERVICE_ROTATE_HANDOFF_SECRET,
    SERVICE_SCAN_HANDOFFS,
    SERVICE_SEARCH_STOPS,
    SERVICE_SET_ACTIVE_TRIP,
    SERVICE_UPDATE_DAY,
    SERVICE_UPDATE_STOP,
    SERVICE_UPDATE_TRIP,
)
from .drive_import import async_drive_import_url
from .roadplanner import RoadplannerError
from .webhook import async_webhook_url

_NON_NEGATIVE_INT = vol.All(int, vol.Range(min=0))
_POSITIVE_INT = vol.All(int, vol.Range(min=1))


def _actor(call: ServiceCall) -> str:
    return f"service:{call.context.user_id or 'system'}"


def _runtime(hass: HomeAssistant) -> Any:
    runtimes = hass.data.get(DOMAIN)
    if not isinstance(runtimes, dict) or not runtimes:
        raise HomeAssistantError(
            "Roadplanner ist nicht geladen. Integration konfigurieren oder neu laden."
        )
    if len(runtimes) != 1:
        raise HomeAssistantError("Roadplanner hat unerwartet mehrere geladene Einträge")
    return next(iter(runtimes.values()))


async def _require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    if call.context.user_id is None:
        raise HomeAssistantError(
            "Diese Roadplanner-Aktion ist nur für Administratoren verfügbar."
        )
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError(
            "Diese Roadplanner-Aktion ist nur für Administratoren verfügbar."
        )


async def _service_call(operation: Awaitable[dict[str, Any]]) -> ServiceResponse:
    try:
        return await operation
    except RoadplannerError as err:
        raise HomeAssistantError(str(err)) from err


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register actions once so automations remain editable while unloaded."""
    if hass.services.has_service(DOMAIN, SERVICE_GET_TRIP_SUMMARY):
        return

    async def get_trip_summary(call: ServiceCall) -> ServiceResponse:
        return await _service_call(_runtime(hass).manager.async_get_trip_summary())

    async def get_days(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_get_days(
                offset=call.data.get("offset", 0),
                limit=call.data.get("limit", 20),
                include_stops=call.data.get("include_stops", False),
            )
        )

    async def get_day(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_get_day(
                day_id=call.data["day_id"],
                stop_offset=call.data.get("stop_offset", 0),
                stop_limit=call.data.get("stop_limit", 50),
            )
        )

    async def search_stops(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_search_stops(
                query=call.data.get("query"),
                stop_type=call.data.get("stop_type"),
                day_id=call.data.get("day_id"),
                day_date=call.data.get("day_date"),
                limit=call.data.get("limit", 20),
            )
        )

    async def list_trips(call: ServiceCall) -> ServiceResponse:
        return await _service_call(_runtime(hass).manager.async_list_trips())

    async def set_active_trip(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_set_active_trip(
                trip_id=call.data["trip_id"],
                expected_active_trip=call.data.get("expected_active_trip"),
            )
        )

    async def update_trip(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_update_trip(
                patch=call.data["patch"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
            )
        )

    async def add_day(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_add_day(
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                day_date=call.data.get("day_date"),
                title=call.data.get("title"),
                start=call.data.get("start", ""),
                end=call.data.get("end", ""),
                distance_km=call.data.get("distance_km"),
                drive_minutes=call.data.get("drive_minutes"),
                status=call.data.get("status", "planned"),
                notes=call.data.get("notes", ""),
                details=call.data.get("details"),
                position=call.data.get("position"),
            )
        )

    async def update_day(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_update_day(
                day_id=call.data["day_id"],
                patch=call.data["patch"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                position=call.data.get("position"),
            )
        )

    async def remove_day(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_remove_day(
                day_id=call.data["day_id"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                remove_stops=call.data.get("remove_stops", False),
            )
        )

    async def add_stop(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_add_stop(
                day_id=call.data["day_id"],
                name=call.data["name"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                stop_type=call.data.get("stop_type", "waypoint"),
                arrival_time=call.data.get("arrival_time"),
                departure_time=call.data.get("departure_time"),
                location=call.data.get("location"),
                notes=call.data.get("notes", ""),
                details=call.data.get("details"),
                position=call.data.get("position"),
            )
        )

    async def update_stop(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_update_stop(
                day_id=call.data["day_id"],
                stop_id=call.data["stop_id"],
                patch=call.data["patch"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                position=call.data.get("position"),
            )
        )

    async def remove_stop(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_remove_stop(
                day_id=call.data["day_id"],
                stop_id=call.data["stop_id"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
            )
        )

    async def calculate_day_route(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        return await _service_call(
            runtime.manager.async_calculate_day_route(
                trip_id=call.data.get("trip_id"),
                day_id=call.data["day_id"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                force=call.data.get("force", False),
            )
        )

    async def calculate_trip_routes(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        return await _service_call(
            runtime.manager.async_calculate_trip_routes(
                trip_id=call.data.get("trip_id"),
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                force=call.data.get("force", False),
            )
        )

    async def create_expense(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        panel = await runtime.manager.async_get_panel_payload(call.data.get("trip_id"))
        trip_id = str(panel.get("selected_trip_id") or "")
        result = await _service_call(
            runtime.travel_archive.async_create_expense(
                trip_id=trip_id,
                value={
                    "merchant": call.data.get("merchant", ""),
                    "category": call.data.get("category", "other"),
                    "amount": call.data["amount"],
                    "currency": call.data.get("currency") or runtime.travel_archive.default_currency,
                    "date": call.data.get("date"),
                    "status": call.data.get("status", "paid"),
                    "payment_method": call.data.get("payment_method", ""),
                    "notes": call.data.get("notes", ""),
                    "day_id": call.data.get("day_id"),
                    "stop_id": call.data.get("stop_id"),
                    "source": "service",
                },
                actor=_actor(call),
            )
        )
        hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {"entry_id": runtime.coordinator.config_entry.entry_id, "archive_changed": True},
        )
        return result

    async def create_todo(call: ServiceCall) -> ServiceResponse:
        runtime = _runtime(hass)
        panel = await runtime.manager.async_get_panel_payload(call.data.get("trip_id"))
        trip_id = str(panel.get("selected_trip_id") or "")
        result = await _service_call(
            runtime.travel_archive.async_create_todo(
                trip_id=trip_id,
                value={
                    "title": call.data["title"],
                    "due_at": call.data.get("due_at"),
                    "priority": call.data.get("priority", "normal"),
                    "status": call.data.get("status", "open"),
                    "notes": call.data.get("notes", ""),
                    "day_id": call.data.get("day_id"),
                    "stop_id": call.data.get("stop_id"),
                    "source": "service",
                },
                actor=_actor(call),
            )
        )
        hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {"entry_id": runtime.coordinator.config_entry.entry_id, "archive_changed": True},
        )
        return result

    async def export_trip(call: ServiceCall) -> ServiceResponse:
        return await _service_call(_runtime(hass).manager.async_export_trip())

    async def create_backup(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_create_backup(
                call.data.get("reason", "manual")
            )
        )

    async def adopt_external_changes(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_adopt_external_changes(
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
            )
        )

    async def reload(call: ServiceCall) -> ServiceResponse:
        coordinator = _runtime(hass).coordinator
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise HomeAssistantError("Roadplanner konnte nicht neu geladen werden")
        return {
            "revision": coordinator.data["metadata"]["revision"],
            "trip": coordinator.data,
        }

    async def list_handoffs(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_list_handoffs(
                limit=call.data.get("limit", 50)
            )
        )

    async def get_handoff(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_get_handoff(call.data["handoff_id"])
        )

    async def ingest_handoff(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_ingest_handoff(
                content=call.data["content"],
                title=call.data.get("title", "Roadplanner-Übergabe"),
                source=call.data.get("source", "service"),
                content_type=call.data.get("content_type", "text/markdown"),
                external_id=call.data.get("external_id"),
                trip_id=call.data.get("trip_id"),
                base_revision=call.data.get("base_revision"),
                metadata=call.data.get("metadata"),
            )
        )

    async def ingest_changeset(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_ingest_handoff(
                changeset=call.data["changeset"],
                title=call.data.get("title", "Roadplanner-Übergabe"),
                source=call.data.get("source", "service"),
                content_type="application/json",
                external_id=call.data.get("external_id"),
                metadata=call.data.get("metadata"),
            )
        )

    async def preview_handoff(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_preview_handoff(
                call.data["handoff_id"]
            )
        )

    async def apply_handoff(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_apply_handoff(
                handoff_id=call.data["handoff_id"],
                actor=_actor(call),
                expected_revision=call.data["expected_revision"],
                confirm_destructive=call.data.get(
                    "confirm_destructive",
                    False,
                ),
            )
        )

    async def get_handoff_folders(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_get_handoff_folders()
        )

    async def archive_handoff(call: ServiceCall) -> ServiceResponse:
        return await _service_call(
            _runtime(hass).manager.async_archive_handoff(
                handoff_id=call.data["handoff_id"],
                resolution=call.data.get("resolution", "rejected"),
                note=call.data.get("note", ""),
            )
        )

    async def scan_handoffs(call: ServiceCall) -> ServiceResponse:
        return await _service_call(_runtime(hass).manager.async_scan_handoffs())

    async def get_handoff_endpoint(call: ServiceCall) -> ServiceResponse:
        await _require_admin(hass, call)
        runtime = _runtime(hass)
        if not runtime.webhook_id or not runtime.webhook_token:
            raise HomeAssistantError(
                "Die externe Übergabe-Bridge ist in den Roadplanner-Optionen deaktiviert."
            )
        url = async_webhook_url(hass, runtime.webhook_id)
        drive_import_url = async_drive_import_url(hass)
        return {
            "drive_import_url": drive_import_url,
            "drive_import_secret": runtime.webhook_token,
            "drive_import_secret_header": DRIVE_IMPORT_SECRET_HEADER,
            "drive_import_method": "POST",
            "drive_import_content_type": "application/json",
            "drive_import_max_bytes": MAX_DRIVE_IMPORT_BYTES,
            "legacy_webhook_url": url,
            "legacy_webhook_token": runtime.webhook_token,
            "legacy_webhook_token_header": "X-Roadplanner-Token",
            "get_json": url,
            "get_markdown": f"{url}?format=markdown",
            "post_handoff": drive_import_url,
            "methods": ["POST"],
            "warning": "URL und Token wie Zugangsdaten behandeln.",
        }

    async def rotate_handoff_secret(call: ServiceCall) -> ServiceResponse:
        await _require_admin(hass, call)
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) != 1:
            raise HomeAssistantError(
                "Der Roadplanner-Konfigurationseintrag wurde nicht eindeutig gefunden."
            )
        entry = entries[0]
        new_secret = secrets.token_urlsafe(48)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_WEBHOOK_TOKEN: new_secret},
        )
        await hass.config_entries.async_reload(entry.entry_id)
        return {
            "drive_import_url": async_drive_import_url(hass),
            "drive_import_secret": new_secret,
            "drive_import_secret_header": DRIVE_IMPORT_SECRET_HEADER,
            "rotated": True,
            "warning": (
                "Das vorherige Secret ist ungültig. Die Apps-Script-"
                "Eigenschaften HA_DRIVE_IMPORT_SECRET und HA_CONTEXT_TOKEN "
                "sofort aktualisieren."
            ),
        }

    async def export_context(call: ServiceCall) -> ServiceResponse:
        return await _service_call(_runtime(hass).manager.async_export_context())

    registrations: tuple[tuple[str, Any, vol.Schema], ...] = (
        (SERVICE_GET_TRIP_SUMMARY, get_trip_summary, vol.Schema({})),
        (
            SERVICE_GET_DAYS,
            get_days,
            vol.Schema(
                {
                    vol.Optional("offset", default=0): _NON_NEGATIVE_INT,
                    vol.Optional("limit", default=20): vol.All(
                        int,
                        vol.Range(min=1, max=60),
                    ),
                    vol.Optional("include_stops", default=False): bool,
                }
            ),
        ),
        (
            SERVICE_GET_DAY,
            get_day,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Optional("stop_offset", default=0): _NON_NEGATIVE_INT,
                    vol.Optional("stop_limit", default=50): vol.All(
                        int,
                        vol.Range(min=1, max=100),
                    ),
                }
            ),
        ),
        (
            SERVICE_SEARCH_STOPS,
            search_stops,
            vol.Schema(
                {
                    vol.Optional("query"): str,
                    vol.Optional("stop_type"): str,
                    vol.Optional("day_id"): str,
                    vol.Optional("day_date"): str,
                    vol.Optional("limit", default=20): vol.All(
                        int,
                        vol.Range(min=1, max=50),
                    ),
                }
            ),
        ),
        (SERVICE_LIST_TRIPS, list_trips, vol.Schema({})),
        (
            SERVICE_SET_ACTIVE_TRIP,
            set_active_trip,
            vol.Schema(
                {
                    vol.Required("trip_id"): str,
                    vol.Optional("expected_active_trip"): str,
                }
            ),
        ),
        (
            SERVICE_UPDATE_TRIP,
            update_trip,
            vol.Schema(
                {
                    vol.Required("patch"): dict,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                }
            ),
        ),
        (
            SERVICE_ADD_DAY,
            add_day,
            vol.Schema(
                {
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("day_date"): str,
                    vol.Optional("title"): str,
                    vol.Optional("start"): str,
                    vol.Optional("end"): str,
                    vol.Optional("distance_km"): vol.Coerce(float),
                    vol.Optional("drive_minutes"): _NON_NEGATIVE_INT,
                    vol.Optional("status"): str,
                    vol.Optional("notes"): str,
                    vol.Optional("details"): dict,
                    vol.Optional("position"): _POSITIVE_INT,
                }
            ),
        ),
        (
            SERVICE_UPDATE_DAY,
            update_day,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("patch"): dict,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("position"): _POSITIVE_INT,
                }
            ),
        ),
        (
            SERVICE_REMOVE_DAY,
            remove_day,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("remove_stops", default=False): bool,
                }
            ),
        ),
        (
            SERVICE_ADD_STOP,
            add_stop,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("name"): str,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("stop_type"): str,
                    vol.Optional("arrival_time"): str,
                    vol.Optional("departure_time"): str,
                    vol.Optional("location"): dict,
                    vol.Optional("notes"): str,
                    vol.Optional("details"): dict,
                    vol.Optional("position"): _POSITIVE_INT,
                }
            ),
        ),
        (
            SERVICE_UPDATE_STOP,
            update_stop,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("stop_id"): str,
                    vol.Required("patch"): dict,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("position"): _POSITIVE_INT,
                }
            ),
        ),
        (
            SERVICE_REMOVE_STOP,
            remove_stop,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("stop_id"): str,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                }
            ),
        ),
        (
            SERVICE_CALCULATE_DAY_ROUTE,
            calculate_day_route,
            vol.Schema(
                {
                    vol.Required("day_id"): str,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("trip_id"): str,
                    vol.Optional("force", default=False): bool,
                }
            ),
        ),
        (
            SERVICE_CALCULATE_TRIP_ROUTES,
            calculate_trip_routes,
            vol.Schema(
                {
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("trip_id"): str,
                    vol.Optional("force", default=False): bool,
                }
            ),
        ),
        (
            SERVICE_CREATE_EXPENSE,
            create_expense,
            vol.Schema(
                {
                    vol.Required("amount"): vol.All(vol.Coerce(float), vol.Range(min=0)),
                    vol.Optional("merchant", default=""): str,
                    vol.Optional("category", default="other"): str,
                    vol.Optional("currency"): str,
                    vol.Optional("date"): str,
                    vol.Optional("status", default="paid"): str,
                    vol.Optional("payment_method", default=""): str,
                    vol.Optional("notes", default=""): str,
                    vol.Optional("trip_id"): str,
                    vol.Optional("day_id"): str,
                    vol.Optional("stop_id"): str,
                }
            ),
        ),
        (
            SERVICE_CREATE_TODO,
            create_todo,
            vol.Schema(
                {
                    vol.Required("title"): str,
                    vol.Optional("due_at"): str,
                    vol.Optional("priority", default="normal"): str,
                    vol.Optional("status", default="open"): str,
                    vol.Optional("notes", default=""): str,
                    vol.Optional("trip_id"): str,
                    vol.Optional("day_id"): str,
                    vol.Optional("stop_id"): str,
                }
            ),
        ),
        (SERVICE_EXPORT_TRIP, export_trip, vol.Schema({})),
        (
            SERVICE_CREATE_BACKUP,
            create_backup,
            vol.Schema({vol.Optional("reason", default="manual"): str}),
        ),
        (
            SERVICE_ADOPT_EXTERNAL_CHANGES,
            adopt_external_changes,
            vol.Schema(
                {vol.Required("expected_revision"): _NON_NEGATIVE_INT}
            ),
        ),
        (SERVICE_RELOAD, reload, vol.Schema({})),
        (
            SERVICE_LIST_HANDOFFS,
            list_handoffs,
            vol.Schema(
                {
                    vol.Optional("limit", default=50): vol.All(
                        int,
                        vol.Range(min=1, max=100),
                    )
                }
            ),
        ),
        (
            SERVICE_GET_HANDOFF,
            get_handoff,
            vol.Schema({vol.Required("handoff_id"): str}),
        ),
        (
            SERVICE_INGEST_HANDOFF,
            ingest_handoff,
            vol.Schema(
                {
                    vol.Required("content"): str,
                    vol.Optional("title"): str,
                    vol.Optional("source"): str,
                    vol.Optional("content_type"): str,
                    vol.Optional("external_id"): str,
                    vol.Optional("trip_id"): str,
                    vol.Optional("base_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("metadata"): dict,
                }
            ),
        ),
        (
            SERVICE_INGEST_CHANGESET,
            ingest_changeset,
            vol.Schema(
                {
                    vol.Required("changeset"): dict,
                    vol.Optional("title"): str,
                    vol.Optional("source"): str,
                    vol.Optional("external_id"): str,
                    vol.Optional("metadata"): dict,
                }
            ),
        ),
        (
            SERVICE_PREVIEW_HANDOFF,
            preview_handoff,
            vol.Schema({vol.Required("handoff_id"): str}),
        ),
        (
            SERVICE_APPLY_HANDOFF,
            apply_handoff,
            vol.Schema(
                {
                    vol.Required("handoff_id"): str,
                    vol.Required("expected_revision"): _NON_NEGATIVE_INT,
                    vol.Optional("confirm_destructive", default=False): bool,
                }
            ),
        ),
        (
            SERVICE_ARCHIVE_HANDOFF,
            archive_handoff,
            vol.Schema(
                {
                    vol.Required("handoff_id"): str,
                    vol.Optional("resolution"): str,
                    vol.Optional("note"): str,
                }
            ),
        ),
        (SERVICE_SCAN_HANDOFFS, scan_handoffs, vol.Schema({})),
        (
            SERVICE_GET_HANDOFF_FOLDERS,
            get_handoff_folders,
            vol.Schema({}),
        ),
        (SERVICE_GET_HANDOFF_ENDPOINT, get_handoff_endpoint, vol.Schema({})),
        (
            SERVICE_ROTATE_HANDOFF_SECRET,
            rotate_handoff_secret,
            vol.Schema({}),
        ),
        (SERVICE_EXPORT_CONTEXT, export_context, vol.Schema({})),
    )

    for service, handler, schema in registrations:
        hass.services.async_register(
            DOMAIN,
            service,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
