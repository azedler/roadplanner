"""Mobile-first Roadplanner panel and its authenticated WebSocket API."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    DRIVE_IMPORT_PATH,
    EVENT_ROADPLANNER_UPDATED,
    INTEGRATION_VERSION,
    NAME,
    DEFAULT_ONEDRIVE_MAX_ITEMS_PER_RUN,
    DEFAULT_ONEDRIVE_MAX_SCAN_SECONDS,
    ROLE_APPROVER,
    ROLE_EDITOR,
    ROLE_VIEWER,
)
from .roadplanner import RevisionConflictError, RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT_NAME = "roadplanner-panel"
PANEL_URL_PATH = "roadplanner-app"
PANEL_STATIC_URL = "/roadplanner_mcp_static"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/roadplanner-panel-{INTEGRATION_VERSION}.js"

WS_GET_DATA = f"{DOMAIN}/panel/get_data"
WS_ACTION = f"{DOMAIN}/panel/action"

_ACTIONS = {
    "refresh",
    "set_active_trip",
    "update_trip",
    "add_day",
    "update_day",
    "remove_day",
    "add_stop",
    "update_stop",
    "remove_stop",
    "calculate_day_route",
    "calculate_trip_routes",
    "scan_handoffs",
    "preview_handoff",
    "apply_handoff",
    "archive_handoff",
    "create_backup",
    "search_destination_images",
    "refresh_destination_gallery",
    "save_destination_gallery",
    "delete_destination_gallery",
    "auto_populate_destination_galleries",
    "assistant_chat",
    "assistant_clear",
    "assistant_remove_draft",
    "assistant_update_draft",
    "assistant_prepare",
    "assistant_prepare_locations",
    "assistant_test",
    "assistant_briefing",
    "assistant_diagnostics",
    "archive_create_upload_ticket",
    "archive_create_download_ticket",
    "archive_analyze_document",
    "archive_confirm_document",
    "archive_update_document",
    "archive_discard_document",
    "archive_delete_document",
    "archive_create_expense",
    "archive_update_expense",
    "archive_delete_expense",
    "archive_create_todo",
    "archive_update_todo",
    "archive_delete_todo",
    "decision_create_from_message",
    "decision_select_option",
    "decision_transfer",
    "decision_archive",
    "decision_delete",
    "onedrive_configure",
    "onedrive_start_auth",
    "onedrive_poll_auth",
    "onedrive_disconnect",
    "onedrive_sync",
    "media_update_assignment",
    "media_delete",
    "universal_import_analyze",
    "universal_import_transfer",
    "universal_import_discuss",
    "universal_import_discard",
}

_EDIT_ACTIONS = {
    "update_trip",
    "add_day",
    "update_day",
    "remove_day",
    "add_stop",
    "update_stop",
    "remove_stop",
    "calculate_day_route",
    "calculate_trip_routes",
    "search_destination_images",
    "refresh_destination_gallery",
    "save_destination_gallery",
    "delete_destination_gallery",
    "auto_populate_destination_galleries",
    "assistant_prepare_locations",
    "archive_create_upload_ticket",
    "archive_analyze_document",
    "archive_confirm_document",
    "archive_update_document",
    "archive_discard_document",
    "archive_delete_document",
    "archive_create_expense",
    "archive_update_expense",
    "archive_delete_expense",
    "archive_create_todo",
    "archive_update_todo",
    "archive_delete_todo",
    "decision_create_from_message",
    "decision_select_option",
    "decision_transfer",
    "decision_archive",
    "decision_delete",
    "onedrive_sync",
    "media_update_assignment",
    "media_delete",
    "universal_import_analyze",
    "universal_import_transfer",
    "universal_import_discuss",
    "universal_import_discard",
}
_APPROVAL_ACTIONS = {
    "set_active_trip",
    "scan_handoffs",
    "apply_handoff",
    "archive_handoff",
}
_ADMIN_ACTIONS = {"create_backup", "assistant_diagnostics", "onedrive_configure", "onedrive_start_auth", "onedrive_poll_auth", "onedrive_disconnect"}
_ASSISTANT_ACTIONS = {
    "assistant_chat",
    "assistant_clear",
    "assistant_remove_draft",
    "assistant_update_draft",
    "assistant_prepare",
    "assistant_prepare_locations",
    "assistant_test",
    "assistant_briefing",
    "assistant_diagnostics",
    "decision_create_from_message",
    "decision_select_option",
    "decision_transfer",
    "decision_archive",
    "decision_delete",
    "universal_import_analyze",
    "universal_import_transfer",
    "universal_import_discuss",
    "universal_import_discard",
}


class PanelPermissionError(Exception):
    """Raised when a panel user cannot run an allow-listed action."""


def _runtime(hass: HomeAssistant) -> Any:
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


def _actor(connection: websocket_api.ActiveConnection) -> str:
    user = connection.user
    user_id = getattr(user, "id", None) or "unknown"
    user_name = getattr(user, "name", None)
    return f"panel:{user_name or user_id}"


def _user_id(connection: websocket_api.ActiveConnection) -> str:
    """Return the stable HA user identifier for volatile assistant sessions."""
    return str(getattr(connection.user, "id", None) or "unknown")


def _capabilities(
    connection: websocket_api.ActiveConnection,
    runtime: Any,
) -> dict[str, Any]:
    user = connection.user
    is_admin = bool(getattr(user, "is_admin", False))
    configured_role = str(getattr(runtime, "non_admin_role", ROLE_VIEWER))
    if configured_role not in {ROLE_VIEWER, ROLE_EDITOR, ROLE_APPROVER}:
        configured_role = ROLE_VIEWER
    role = "admin" if is_admin else configured_role
    return {
        "role": role,
        "can_view": True,
        "can_edit": is_admin or role in {ROLE_EDITOR, ROLE_APPROVER},
        "can_activate": is_admin or role == ROLE_APPROVER,
        "can_approve": is_admin or role == ROLE_APPROVER,
        "can_admin": is_admin,
        "can_assistant": is_admin or role in {ROLE_EDITOR, ROLE_APPROVER},
    }


def _require_action_permission(
    action: str,
    capabilities: dict[str, Any],
) -> None:
    if action in _ASSISTANT_ACTIONS and not capabilities["can_assistant"]:
        raise PanelPermissionError(
            "Der Roadplanner-Assistent erfordert Bearbeitungsrechte"
        )
    if action in _ADMIN_ACTIONS and not capabilities["can_admin"]:
        raise PanelPermissionError(
            "Diese Roadplanner-Systemaktion erfordert Administratorrechte"
        )
    if action in _APPROVAL_ACTIONS and not capabilities["can_approve"]:
        raise PanelPermissionError(
            "Das Übernehmen oder Ablehnen von Übergaben erfordert "
            "die Roadplanner-Freigaberolle"
        )
    if action in _EDIT_ACTIONS and not capabilities["can_edit"]:
        raise PanelPermissionError(
            "Dieses Konto besitzt im Roadplanner nur Leserechte"
        )


def _none_if_blank(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _optional_number(value: Any) -> int | float | None:
    value = _none_if_blank(value)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Numerischer Wert erwartet")
    return value


def _optional_int(value: Any) -> int | None:
    value = _none_if_blank(value)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("Ganzzahliger Wert erwartet")
    return value


def _details(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("'details' muss ein JSON-Objekt sein")
    return dict(value)


def _location(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValidationError("'location' muss ein JSON-Objekt sein")
    location = {
        key: child
        for key, child in value.items()
        if child not in (None, "")
    }
    latitude = location.get("latitude", location.get("lat"))
    longitude = location.get(
        "longitude",
        location.get("lon", location.get("lng")),
    )
    if latitude is None and longitude is None:
        return location
    if latitude is None or longitude is None:
        raise ValidationError(
            "Breiten- und Längengrad müssen gemeinsam angegeben werden"
        )
    if isinstance(latitude, bool) or not isinstance(latitude, (int, float)):
        raise ValidationError("Breitengrad muss numerisch sein")
    if isinstance(longitude, bool) or not isinstance(longitude, (int, float)):
        raise ValidationError("Längengrad muss numerisch sein")
    if not -90 <= latitude <= 90:
        raise ValidationError("Breitengrad muss zwischen -90 und 90 liegen")
    if not -180 <= longitude <= 180:
        raise ValidationError("Längengrad muss zwischen -180 und 180 liegen")
    location["latitude"] = float(latitude)
    location["longitude"] = float(longitude)
    for alias in ("lat", "lon", "lng"):
        location.pop(alias, None)
    return location


async def _execute_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    action: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    runtime = _runtime(hass)
    manager = runtime.manager
    actor = _actor(connection)
    user_id = _user_id(connection)

    if action == "assistant_chat":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_chat(
            user_id=user_id,
            trip_id=trip_id,
            text=str(data.get("text") or ""),
            client_request_id=str(data.get("client_request_id") or ""),
        )

    if action == "assistant_clear":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_clear(
            user_id=user_id,
            trip_id=trip_id,
        )

    if action == "assistant_remove_draft":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_remove_draft(
            user_id=user_id,
            trip_id=trip_id,
            draft_id=str(data.get("draft_id") or ""),
        )

    if action == "assistant_update_draft":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Entwurfsänderung muss ein JSON-Objekt sein")
        return await runtime.assistant.async_update_draft(
            user_id=user_id,
            trip_id=trip_id,
            draft_id=str(data.get("draft_id") or ""),
            patch=patch,
        )

    if action == "assistant_prepare":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_prepare_review(
            user_id=user_id,
            trip_id=trip_id,
            actor=actor,
        )

    if action == "assistant_prepare_locations":
        trip_id = str(data.get("trip_id") or "").strip()
        day_id = str(data.get("day_id") or "").strip()
        if not trip_id or not day_id:
            raise ValidationError(
                "Reise und Reisetag werden für die GPS-Vervollständigung benötigt"
            )
        return await runtime.assistant.async_add_location_drafts(
            user_id=user_id,
            trip_id=trip_id,
            day_id=day_id,
        )

    if action == "assistant_test":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_test(
            user_id=user_id,
            trip_id=trip_id,
        )

    if action == "assistant_briefing":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_briefing(
            user_id=user_id,
            trip_id=trip_id,
        )

    if action == "assistant_diagnostics":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Assistenten wurde keine Reise ausgewählt")
        return await runtime.assistant.async_diagnostics(
            user_id=user_id,
            trip_id=trip_id,
        )

    if action == "decision_create_from_message":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Entscheidung wurde keine Reise ausgewählt")
        return await runtime.experience.async_create_decision_from_message(
            user_id=user_id,
            trip_id=trip_id,
            message_id=str(data.get("message_id") or ""),
        )

    if action == "decision_select_option":
        return await runtime.experience.async_select_decision(
            str(data.get("trip_id") or ""),
            str(data.get("decision_id") or ""),
            str(data.get("option_id") or ""),
        )

    if action == "decision_transfer":
        return await runtime.experience.async_transfer_decision(
            user_id=user_id,
            trip_id=str(data.get("trip_id") or ""),
            decision_id=str(data.get("decision_id") or ""),
        )

    if action == "decision_archive":
        return await runtime.experience.async_archive_decision(
            str(data.get("trip_id") or ""),
            str(data.get("decision_id") or ""),
        )

    if action == "decision_delete":
        return await runtime.experience.async_delete_decision(
            str(data.get("trip_id") or ""),
            str(data.get("decision_id") or ""),
        )

    if action == "refresh_destination_gallery":
        return await runtime.experience.async_refresh_destination_gallery(
            str(data.get("trip_id") or ""),
            str(data.get("day_id") or ""),
            str(data.get("stop_id") or ""),
        )

    if action == "save_destination_gallery":
        images = data.get("images")
        if not isinstance(images, list):
            raise ValidationError("Die Bildergalerie ist unvollständig")
        return await runtime.experience.async_save_destination_gallery(
            str(data.get("trip_id") or ""),
            str(data.get("day_id") or ""),
            str(data.get("stop_id") or ""),
            [item for item in images if isinstance(item, dict)],
            str(data.get("primary_image_id") or "") or None,
        )

    if action == "delete_destination_gallery":
        return await runtime.experience.async_delete_destination_gallery(
            str(data.get("trip_id") or ""),
            str(data.get("stop_id") or ""),
        )

    if action == "auto_populate_destination_galleries":
        return await runtime.experience.async_auto_populate_destination_galleries(
            str(data.get("trip_id") or ""),
            limit=_optional_int(data.get("limit")) or 6,
        )

    if action == "onedrive_configure":
        return await runtime.experience.async_reconfigure_onedrive(
            client_id=str(data.get("client_id") or runtime.experience.onedrive.client_id or ""),
            folder_path=str(data.get("folder_path") or "Pictures/Camera Roll"),
            auto_sync=bool(data.get("auto_sync", True)),
            auto_assign=bool(data.get("auto_assign", True)),
            sync_interval_minutes=int(data.get("sync_interval_minutes") or 15),
            recursive_subfolders=bool(data.get("recursive_subfolders", True)),
            date_buffer_days=int(data.get("date_buffer_days") or 3),
            max_items_per_run=int(
                data.get("max_items_per_run") or DEFAULT_ONEDRIVE_MAX_ITEMS_PER_RUN
            ),
            max_scan_seconds=int(
                data.get("max_scan_seconds") or DEFAULT_ONEDRIVE_MAX_SCAN_SECONDS
            ),
        )

    if action == "onedrive_start_auth":
        return await runtime.experience.async_start_onedrive_auth()

    if action == "onedrive_poll_auth":
        return await runtime.experience.async_poll_onedrive_auth()

    if action == "onedrive_disconnect":
        return await runtime.experience.async_disconnect_onedrive()

    if action == "onedrive_sync":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Fotosynchronisierung wurde keine Reise ausgewählt")
        return await runtime.experience.async_sync_trip(
            trip_id, full_rescan=bool(data.get("full_rescan", False))
        )

    if action == "media_update_assignment":
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Fotozuordnung ist unvollständig")
        return {
            "media": await runtime.experience.async_update_media(
                str(data.get("trip_id") or ""),
                str(data.get("media_id") or ""),
                patch,
            ),
            "experience": await runtime.experience.async_panel_payload(
                str(data.get("trip_id") or "")
            ),
        }

    if action == "media_delete":
        result = await runtime.experience.async_delete_media(
            str(data.get("trip_id") or ""),
            str(data.get("media_id") or ""),
        )
        result["experience"] = await runtime.experience.async_panel_payload(
            str(data.get("trip_id") or "")
        )
        return result

    if action == "universal_import_analyze":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Universal Import wurde keine Reise ausgewählt")
        return await runtime.universal_import.async_analyze_document(
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            actor=actor,
        )

    if action == "universal_import_transfer":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Universal Import wurde keine Reise ausgewählt")
        return await runtime.universal_import.async_transfer(
            user_id=user_id,
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            actor=actor,
        )

    if action == "universal_import_discuss":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Universal Import wurde keine Reise ausgewählt")
        return await runtime.universal_import.async_discuss(
            user_id=user_id,
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
        )

    if action == "universal_import_discard":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Universal Import wurde keine Reise ausgewählt")
        return await runtime.universal_import.async_discard(
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            actor=actor,
        )

    if action == "archive_create_upload_ticket":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Dokument-Upload wurde keine Reise ausgewählt")
        return await runtime.travel_archive.async_create_upload_ticket(
            user_id=user_id,
            actor=actor,
            trip_id=trip_id,
            source=str(data.get("source") or "panel_upload"),
            keep_original=bool(data.get("keep_original", True)),
            links=data.get("links") if isinstance(data.get("links"), dict) else None,
        )

    if action == "archive_create_download_ticket":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für das Dokument wurde keine Reise ausgewählt")
        return await runtime.travel_archive.async_create_download_ticket(
            user_id=user_id,
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
        )

    if action == "archive_analyze_document":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Dokumentanalyse wurde keine Reise ausgewählt")
        return await runtime.travel_archive.async_analyze_document(
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            actor=actor,
        )

    if action == "archive_confirm_document":
        trip_id = str(data.get("trip_id") or "").strip()
        patch = data.get("patch")
        if not trip_id or not isinstance(patch, dict):
            raise ValidationError("Dokumentbestätigung ist unvollständig")
        return await runtime.travel_archive.async_confirm_document(
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            patch=patch,
            actor=actor,
        )

    if action == "archive_update_document":
        trip_id = str(data.get("trip_id") or "").strip()
        patch = data.get("patch")
        if not trip_id or not isinstance(patch, dict):
            raise ValidationError("Dokumentänderung ist unvollständig")
        return await runtime.travel_archive.async_update_document(
            trip_id=trip_id,
            document_id=str(data.get("document_id") or ""),
            patch=patch,
            actor=actor,
        )

    if action == "archive_discard_document":
        return await runtime.travel_archive.async_discard_document(
            trip_id=str(data.get("trip_id") or ""),
            document_id=str(data.get("document_id") or ""),
        )

    if action == "archive_delete_document":
        return await runtime.travel_archive.async_delete_document(
            trip_id=str(data.get("trip_id") or ""),
            document_id=str(data.get("document_id") or ""),
            delete_linked_records=bool(data.get("delete_linked_records", False)),
        )

    if action == "archive_create_expense":
        value = data.get("value")
        if not isinstance(value, dict):
            raise ValidationError("Ausgabe muss ein JSON-Objekt sein")
        return await runtime.travel_archive.async_create_expense(
            trip_id=str(data.get("trip_id") or ""),
            value=value,
            actor=actor,
        )

    if action == "archive_update_expense":
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Ausgabenänderung muss ein JSON-Objekt sein")
        return await runtime.travel_archive.async_update_expense(
            trip_id=str(data.get("trip_id") or ""),
            expense_id=str(data.get("expense_id") or ""),
            patch=patch,
            actor=actor,
        )

    if action == "archive_delete_expense":
        return await runtime.travel_archive.async_delete_expense(
            trip_id=str(data.get("trip_id") or ""),
            expense_id=str(data.get("expense_id") or ""),
        )

    if action == "archive_create_todo":
        value = data.get("value")
        if not isinstance(value, dict):
            raise ValidationError("Aufgabe muss ein JSON-Objekt sein")
        return await runtime.travel_archive.async_create_todo(
            trip_id=str(data.get("trip_id") or ""),
            value=value,
            actor=actor,
        )

    if action == "archive_update_todo":
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Aufgabenänderung muss ein JSON-Objekt sein")
        return await runtime.travel_archive.async_update_todo(
            trip_id=str(data.get("trip_id") or ""),
            todo_id=str(data.get("todo_id") or ""),
            patch=patch,
            actor=actor,
        )

    if action == "archive_delete_todo":
        return await runtime.travel_archive.async_delete_todo(
            trip_id=str(data.get("trip_id") or ""),
            todo_id=str(data.get("todo_id") or ""),
        )

    if action == "refresh":
        return {"refreshed": True}

    if action == "set_active_trip":
        return await manager.async_set_active_trip(
            trip_id=data.get("trip_id"),
            expected_active_trip=data.get("expected_active_trip"),
        )

    if action == "update_trip":
        patch = dict(data.get("patch") or {})
        for field in ("start_date", "end_date"):
            if field in patch:
                patch[field] = _none_if_blank(patch[field])
        return await manager.async_update_trip(
            patch=patch,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "add_day":
        return await manager.async_add_day(
            actor=actor,
            expected_revision=data.get("expected_revision"),
            day_date=_none_if_blank(data.get("day_date")),
            title=_none_if_blank(data.get("title")),
            start=str(data.get("start") or ""),
            end=str(data.get("end") or ""),
            distance_km=_optional_number(data.get("distance_km")),
            drive_minutes=_optional_int(data.get("drive_minutes")),
            status=str(data.get("status") or "planned"),
            notes=str(data.get("notes") or ""),
            details=_details(data.get("details")),
            position=_optional_int(data.get("position")),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "update_day":
        patch = dict(data.get("patch") or {})
        if "date" in patch:
            patch["date"] = _none_if_blank(patch["date"])
        if "distance_km" in patch:
            patch["distance_km"] = _optional_number(patch["distance_km"])
        if "drive_minutes" in patch:
            patch["drive_minutes"] = _optional_int(patch["drive_minutes"])
        if "details" in patch:
            patch["details"] = _details(patch["details"])
        return await manager.async_update_day(
            day_id=data.get("day_id"),
            patch=patch,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            position=_optional_int(data.get("position")),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "remove_day":
        return await manager.async_remove_day(
            day_id=data.get("day_id"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            remove_stops=bool(data.get("remove_stops", False)),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "add_stop":
        location = _location(data.get("location"))
        return await manager.async_add_stop(
            day_id=data.get("day_id"),
            name=data.get("name"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            stop_type=str(data.get("stop_type") or "waypoint"),
            arrival_time=_none_if_blank(data.get("arrival_time")),
            departure_time=_none_if_blank(data.get("departure_time")),
            location=location,
            notes=str(data.get("notes") or ""),
            details=_details(data.get("details")),
            position=_optional_int(data.get("position")),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "update_stop":
        patch = dict(data.get("patch") or {})
        for field in ("arrival_time", "departure_time"):
            if field in patch:
                patch[field] = _none_if_blank(patch[field])
        if "location" in patch:
            patch["location"] = _location(patch["location"])
        if "details" in patch:
            patch["details"] = _details(patch["details"])
        return await manager.async_update_stop(
            day_id=data.get("day_id"),
            stop_id=data.get("stop_id"),
            patch=patch,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            position=_optional_int(data.get("position")),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "remove_stop":
        return await manager.async_remove_stop(
            day_id=data.get("day_id"),
            stop_id=data.get("stop_id"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "calculate_day_route":
        trip_id = str(data.get("expected_trip_id") or data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Routenberechnung wurde keine Reise ausgewählt")
        return await manager.async_calculate_day_route(
            trip_id=trip_id,
            day_id=str(data.get("day_id") or ""),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            force=bool(data.get("force", False)),
        )

    if action == "calculate_trip_routes":
        trip_id = str(data.get("expected_trip_id") or data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Routenberechnung wurde keine Reise ausgewählt")
        return await manager.async_calculate_trip_routes(
            trip_id=trip_id,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            force=bool(data.get("force", False)),
        )

    if action == "scan_handoffs":
        return await manager.async_scan_handoffs()

    if action == "preview_handoff":
        return await manager.async_preview_handoff(
            data.get("handoff_id"),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "apply_handoff":
        return await manager.async_apply_handoff(
            handoff_id=data.get("handoff_id"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            confirm_destructive=bool(data.get("confirm_destructive", False)),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "archive_handoff":
        return await manager.async_archive_handoff(
            handoff_id=data.get("handoff_id"),
            resolution=str(data.get("resolution") or "rejected"),
            note=str(data.get("note") or ""),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "create_backup":
        return await manager.async_create_backup(
            str(data.get("reason") or "panel-manual")
        )

    if action == "search_destination_images":
        return await runtime.image_provider.async_search(
            str(data.get("query") or ""),
            limit=_optional_int(data.get("limit")) or 8,
            latitude=data.get("latitude"),
            longitude=data.get("longitude"),
        )

    raise ValidationError(f"Unbekannte Panel-Aktion: {action}")


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_GET_DATA,
        vol.Optional("trip_id"): str,
    }
)
@websocket_api.async_response
async def websocket_get_panel_data(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return a bounded snapshot for the Roadplanner panel."""
    try:
        runtime = _runtime(hass)
        payload = await runtime.manager.async_get_panel_payload(msg.get("trip_id"))
    except RoadplannerError as err:
        connection.send_error(msg["id"], "roadplanner_error", str(err))
        return
    except Exception:  # pragma: no cover - defensive Home Assistant boundary
        _LOGGER.exception("Unexpected error loading Roadplanner panel data")
        connection.send_error(
            msg["id"],
            "unknown_error",
            "Roadplanner-Daten konnten nicht geladen werden",
        )
        return

    user = connection.user
    capabilities = _capabilities(connection, runtime)
    selected_trip_id = str(payload.get("selected_trip_id") or "")
    assistant_state = runtime.assistant.state(
        _user_id(connection),
        selected_trip_id,
    )
    archive_state = (
        await runtime.travel_archive.async_panel_payload(selected_trip_id)
        if selected_trip_id
        else {"documents": [], "expenses": [], "todos": [], "stats": {}, "by_day": {}, "by_stop": {}}
    )
    experience_state = (
        await runtime.experience.async_panel_payload(
            selected_trip_id,
            days=list(payload.get("days", {}).get("days", []) or []),
        )
        if selected_trip_id
        else {"decisions": [], "media": [], "destination_galleries": {}, "presentation": {}, "stats": {}, "by_day": {}, "by_stop": {}, "onedrive": runtime.experience.onedrive.status()}
    )
    payload.update(
        {
            "integration_version": INTEGRATION_VERSION,
            "entry_id": next(iter(hass.data[DOMAIN])),
            "capabilities": capabilities,
            "assistant": assistant_state,
            "travel_archive": archive_state,
            "experience": experience_state,
            "user": {
                "id": getattr(user, "id", None),
                "name": getattr(user, "name", None),
                "is_admin": bool(getattr(user, "is_admin", False)),
            },
            "settings": {
                "auto_scan_handoffs": runtime.manager.auto_scan_handoffs,
                "auto_apply_changesets": runtime.manager.auto_apply_changesets,
                "allow_destructive_auto_apply": (
                    runtime.manager.allow_destructive_auto_apply
                ),
                "non_admin_role": runtime.non_admin_role,
                "handoff_webhook_enabled": bool(runtime.webhook_id),
                "drive_import_endpoint": (
                    DRIVE_IMPORT_PATH if runtime.webhook_token else None
                ),
                "destination_image_provider": "wikimedia_commons+openverse",
                "destination_image_gallery_size": 3,
                "destination_image_auto_fill": True,
                "assistant_configured": runtime.assistant.configured,
                "assistant_provider": runtime.assistant.provider_name,
                "assistant_model": runtime.assistant.model,
                "assistant_research_enabled": runtime.assistant.enable_research,
                "assistant_autonomy_level": runtime.assistant.autonomy_level,
                "assistant_copilot_enabled": runtime.assistant.copilot_enabled,
                "assistant_copilot_auto_briefing": runtime.assistant.copilot_auto_briefing,
                "assistant_debug_enabled": runtime.assistant.debug_enabled,
                "assistant_plugins": runtime.assistant.plugins.descriptors(),
                "assistant_geocoding_enabled": bool(
                    runtime.assistant.geocoder
                    and runtime.assistant.geocoder.enabled
                ),
                "routing_configured": runtime.router.configured,
                "routing_provider": runtime.router.name,
                "routing_profile": runtime.router.profile,
                "routing_health": runtime.router.health_snapshot(),
                "document_archive_configured": True,
                "document_analysis_enabled": runtime.travel_archive.analysis_enabled,
                "document_analysis_configured": runtime.travel_archive.configured,
                "document_max_upload_bytes": runtime.travel_archive.max_upload_bytes,
                "default_currency": runtime.travel_archive.default_currency,
                "onedrive_configured": bool(runtime.experience.onedrive.client_id),
                "onedrive_connected": runtime.experience.onedrive.connected,
                "onedrive_folder_path": runtime.experience.folder_path,
                "onedrive_auto_sync": runtime.experience.auto_sync,
                "onedrive_auto_assign": runtime.experience.auto_assign,
                "onedrive_sync_interval": runtime.experience.sync_interval_minutes,
                "onedrive_recursive": runtime.experience.recursive_subfolders,
                "onedrive_date_buffer_days": runtime.experience.date_buffer_days,
                "onedrive_max_items_per_run": runtime.experience.max_items_per_run,
                "onedrive_max_scan_seconds": runtime.experience.max_scan_seconds,
                "onedrive_settings_source": "photo_setup",
                "universal_import_configured": runtime.universal_import.configured,
            },
        }
    )
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ACTION,
        vol.Required("action"): vol.In(_ACTIONS),
        vol.Optional("data", default={}): dict,
    }
)
@websocket_api.async_response
async def websocket_panel_action(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Execute one explicitly allow-listed Roadplanner panel action."""
    try:
        runtime = _runtime(hass)
        capabilities = _capabilities(connection, runtime)
        _require_action_permission(msg["action"], capabilities)
        result = await _execute_action(
            hass,
            connection,
            msg["action"],
            dict(msg.get("data") or {}),
        )
    except PanelPermissionError as err:
        connection.send_error(msg["id"], "unauthorized", str(err))
        return
    except RevisionConflictError as err:
        connection.send_error(msg["id"], "revision_conflict", str(err))
        return
    except RoadplannerError as err:
        connection.send_error(msg["id"], "roadplanner_error", str(err))
        return
    except Exception:  # pragma: no cover - defensive Home Assistant boundary
        _LOGGER.exception("Unexpected Roadplanner panel action error")
        connection.send_error(
            msg["id"],
            "unknown_error",
            "Die Roadplanner-Aktion ist unerwartet fehlgeschlagen",
        )
        return
    if str(msg.get("action") or "").startswith(("archive_", "universal_import_")):
        hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {
                "entry_id": next(iter(hass.data.get(DOMAIN, {})), None),
                "archive_changed": True,
            },
        )
    connection.send_result(msg["id"], result)


async def async_setup_panel_support(hass: HomeAssistant) -> None:
    """Register the static module and WebSocket commands once per HA process."""
    frontend_file = Path(__file__).parent / "frontend" / "roadplanner-panel.js"
    if not frontend_file.is_file():
        raise FileNotFoundError(
            f"Roadplanner panel module is missing: {frontend_file}"
        )
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                PANEL_MODULE_URL,
                str(frontend_file),
                False,
            )
        ]
    )
    _LOGGER.info(
        "Registered Roadplanner panel module %s from %s",
        PANEL_MODULE_URL,
        frontend_file,
    )
    websocket_api.async_register_command(hass, websocket_get_panel_data)
    websocket_api.async_register_command(hass, websocket_panel_action)


async def async_register_frontend_panel(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Add the Roadplanner app to the Home Assistant sidebar."""
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(
            hass,
            PANEL_URL_PATH,
            warn_if_unknown=False,
        )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT_NAME,
        sidebar_title=NAME,
        sidebar_icon="mdi:map-marker-path",
        module_url=f"{PANEL_MODULE_URL}?v={INTEGRATION_VERSION}",
        embed_iframe=False,
        trust_external=False,
        require_admin=False,
        config={
            "entry_id": entry.entry_id,
            "version": INTEGRATION_VERSION,
            "event_type": EVENT_ROADPLANNER_UPDATED,
        },
    )


@callback
def async_remove_frontend_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel when the config entry unloads."""
    frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
