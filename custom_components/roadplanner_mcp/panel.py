"""Mobile-first Roadplanner panel and its authenticated WebSocket API."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

import uuid

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
from .currency_rates import EcbRateService, eur_total
from .destination_intelligence import _PARK4NIGHT_ID_RE, _google_maps_host
from .ffmpeg_runner import ffmpeg_available
from .google_maps_link import async_resolve_google_maps_place
from .page_images import async_fetch_page_place
from .system_check import async_run_system_check
from .frontend_static_http import async_register_frontend_static_view
from .pitch_routing import PitchRouteService
from .renderer_app_protocol import ACTION_RENDER_REMOTION_TEST, RendererProtocolError
from .roadplanner import RevisionConflictError, RoadplannerError, ValidationError
from .travel_integrity import build_travel_integrity

_LOGGER = logging.getLogger(__name__)

PANEL_COMPONENT_NAME = "roadplanner-panel"
PANEL_URL_PATH = "roadplanner-app"
PANEL_STATIC_URL = "/roadplanner_mcp_static"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/roadplanner-panel.js"

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
    "pitch_option_save",
    "pitch_option_delete",
    "pitch_option_set_status",
    "pitch_set_strategy",
    "pitch_option_activate",
    "pitch_update_preferences",
    "calculate_day_route",
    "calculate_trip_routes",
    "pitch_route_overview",
    "run_system_check",
    "export_trip_pdf",
    "trip_pdf_status",
    "export_trip_video",
    "trip_video_status",
    "generate_trip_summaries",
    "trip_summaries_status",
    "remotion_diagnose",
    "remotion_test_render",
    "remotion_status",
    "remotion_cancel",
    "renderer_app_environment",
    "renderer_app_status",
    "renderer_app_run",
    "renderer_app_render",
    "story_manifest",
    "story_set_override",
    "story_film_preview",
    "story_film_render",
    "renderer_app_trip_days",
    "renderer_app_trip_day",
    "renderer_app_job_status",
    "park4night_autofill_run",
    "plan_day_calendar_repair",
    "propose_day_calendar_repair",
    "scan_handoffs",
    "preview_handoff",
    "apply_handoff",
    "archive_handoff",
    "rebase_handoff",
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
    "assistant_prepare_trip_locations",
    "prepare_place_enrichment",
    "submit_place_enrichment",
    "park4night_lookup",
    "place_link_lookup",
    "assistant_test",
    "assistant_briefing",
    "assistant_diagnostics",
    "get_exchange_rates",
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
    "media_curate_stop",
    "media_curate_trip",
    "universal_import_analyze",
    "universal_import_transfer",
    "universal_import_discuss",
    "universal_import_discard",
    "crew_person_add",
    "crew_person_update",
    "crew_person_retire",
    "crew_person_reactivate",
    "crew_vehicle_add",
    "crew_vehicle_update",
    "crew_vehicle_retire",
    "crew_vehicle_reactivate",
}

_EDIT_ACTIONS = {
    # Writes generated text onto days, the trip and the crew records, so it
    # needs the same permission as any other edit.
    "generate_trip_summaries",
    # Starting a child process is not a read - it needs edit rights even
    # though it changes no trip data.
    "remotion_test_render",
    "remotion_cancel",
    "renderer_app_run",
    "renderer_app_render",
    # Writes a package of real photos into the shared directory. Reading
    # the day list is not an edit; handing the data over is.
    "renderer_app_trip_day",
    # Writes two fields onto a day, so it needs the same permission as any
    # other edit - even though it touches no travel fact.
    "story_set_override",
    # Writes a package of real photos into the shared directory.
    "story_film_render",
    "update_trip",
    "add_day",
    "update_day",
    "remove_day",
    "add_stop",
    "update_stop",
    "remove_stop",
    "pitch_option_save",
    "pitch_option_delete",
    "pitch_option_set_status",
    "pitch_set_strategy",
    "pitch_option_activate",
    "pitch_update_preferences",
    "calculate_day_route",
    "calculate_trip_routes",
    "pitch_route_overview",
    "search_destination_images",
    "refresh_destination_gallery",
    "save_destination_gallery",
    "delete_destination_gallery",
    "auto_populate_destination_galleries",
    "assistant_prepare_locations",
    "assistant_prepare_trip_locations",
    "prepare_place_enrichment",
    "submit_place_enrichment",
    "propose_day_calendar_repair",
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
    "media_curate_stop",
    "media_curate_trip",
    "universal_import_analyze",
    "universal_import_transfer",
    "universal_import_discuss",
    "universal_import_discard",
    "crew_person_add",
    "crew_person_update",
    "crew_person_retire",
    "crew_person_reactivate",
    "crew_vehicle_add",
    "crew_vehicle_update",
    "crew_vehicle_retire",
    "crew_vehicle_reactivate",
}
_APPROVAL_ACTIONS = {
    "set_active_trip",
    "scan_handoffs",
    "apply_handoff",
    "archive_handoff",
    "rebase_handoff",
}
_ADMIN_ACTIONS = {"create_backup", "assistant_diagnostics", "onedrive_configure", "onedrive_start_auth", "onedrive_poll_auth", "onedrive_disconnect"}
# These call an external AI provider and can run for a minute or more. A
# mobile browser/app backgrounded mid-call drops its WebSocket connection,
# and Home Assistant cancels whichever task was awaiting the (now dead)
# connection - previously that cancelled the actual provider call too, so
# the message was never answered at all, not just undelivered. Running them
# via hass.async_create_task + asyncio.shield lets the call keep going to
# completion server-side regardless of the connection's fate; the frontend
# already re-checks on reconnect whether the pending text got answered
# anyway (see assistant.js's _assistantFailureResolved).
_PROVIDER_CALL_ACTIONS = {
    "assistant_chat",
    "assistant_prepare",
    "assistant_test",
    "assistant_briefing",
    # Not an AI-provider call for the PDF's sake, but the video export does
    # call Gemini for narrative text AND runs a long ffmpeg encode - a
    # dropped mobile connection must not abort either.
    "export_trip_video",
    # Starts a background run that writes into the trip; the START must
    # survive a dropped connection or the user sees nothing happen.
    "generate_trip_summaries",
    # Spawns a child process and inspects the runtime - seconds at worst,
    # but a dropped connection must not orphan the probe.
    "remotion_diagnose",
    "remotion_test_render",
    # Downloads up to five photos and re-encodes them. That is tens of
    # seconds on a phone connection, and being cancelled halfway would
    # leave a half-written package in the shared directory.
    "renderer_app_trip_day",
    # The same, for a whole trip: up to ninety photos and minutes of work
    # that must survive a phone locking its screen.
    "story_film_render",
    # One bounded Gemini url_context read of a Park4Night page (stop form).
    "park4night_lookup",
    # Same bounded read for an arbitrary place link (enrichment dialog).
    "place_link_lookup",
    # Walks every live interface end to end, including one Gemini call -
    # the whole point is that it finishes even on a shaky connection.
    "run_system_check",
}
_ASSISTANT_ACTIONS = {
    "assistant_chat",
    "assistant_clear",
    "assistant_remove_draft",
    "assistant_update_draft",
    "assistant_prepare",
    "assistant_prepare_locations",
    "assistant_prepare_trip_locations",
    "prepare_place_enrichment",
    "submit_place_enrichment",
    "park4night_lookup",
    "place_link_lookup",
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


def _place_link_diagnosis(
    page_place: dict[str, Any], *, ai_asked: bool, ai_found: bool
) -> str:
    """Compact, human-readable account of what reading the link actually did.

    A link lookup happens on a phone, far from the Home Assistant log. When
    it comes back with less than expected, this one line is the whole story:
    what the page answered, how much of it was read, what was found in it,
    and what the AI reader made of it.
    """
    parts: list[str] = []
    status = str(page_place.get("read_status") or "")
    if not status:
        parts.append("Seite: nicht gelesen (Park4Night-Direktweg)")
    elif status == "ok":
        size = int(page_place.get("page_bytes") or 0)
        parts.append(f"Seite: gelesen, {size // 1024} kB")
        parts.append(
            "GPS auf der Seite: ja"
            if "latitude" in page_place
            else "GPS auf der Seite: nein"
        )
        parts.append("Name: " + ("ja" if page_place.get("name") else "nein"))
    else:
        parts.append(f"Seite: {status}")
    if ai_asked:
        parts.append("KI-Leser: " + ("Treffer" if ai_found else "kein Treffer"))
    else:
        parts.append("KI-Leser: nicht konfiguriert")
    return " · ".join(parts)


def _place_link_failure_message(
    page_place: dict[str, Any], *, lookup_available: bool
) -> str:
    """Say what actually went wrong with a shared place link.

    The old blanket text ("konnte nicht gelesen werden ODER enthält keine
    eindeutige GPS-Angabe") left the user with no idea which of the two it
    was - and therefore no idea what to do next.
    """
    status = str(page_place.get("read_status") or "")
    if status == "ok":
        detail = (
            "Die Seite wurde gelesen, nennt aber keine eindeutige GPS-Position. "
            "Bitte Koordinaten von Hand eintragen - oder einen Google-Maps-Link "
            "der Stelle teilen."
        )
    elif status.startswith("http_"):
        detail = (
            f"Die Seite hat die Anfrage abgewiesen ({status.replace('http_', 'HTTP ')}). "
            "Solche Seiten lassen sich oft nur im Browser öffnen - bitte die "
            "Koordinaten von dort übernehmen."
        )
    elif status == "timeout":
        detail = "Die Seite hat nicht rechtzeitig geantwortet. Bitte später erneut versuchen."
    elif status == "network_error":
        detail = "Die Seite war nicht erreichbar. Bitte später erneut versuchen."
    else:
        detail = "Die Seite konnte nicht gelesen werden."
    if not lookup_available:
        detail += (
            " Der Reisebegleiter (Gemini) ist nicht konfiguriert und konnte "
            "es nicht ersatzweise versuchen."
        )
    return detail


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

    if action == "assistant_prepare_trip_locations":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError(
                "Für die GPS-Vervollständigung wurde keine Reise ausgewählt"
            )
        return await runtime.assistant.async_add_trip_location_drafts(
            user_id=user_id,
            trip_id=trip_id,
        )

    if action == "park4night_lookup":
        text = str(data.get("text") or "").strip()
        match = _PARK4NIGHT_ID_RE.search(text)
        if match is None:
            raise ValidationError(
                "Kein Park4Night-Verweis gefunden - eine ID wie 'p4n 506374' "
                "oder ein park4night.com-Link wird benötigt"
            )
        lookup = runtime.experience.p4n_lookup
        if not lookup.available:
            raise ValidationError("Der Reisebegleiter (Gemini) ist nicht konfiguriert")
        url = f"https://park4night.com/lieu/{match.group('id')}/"
        result = await lookup.async_lookup(url, hint_name=text[:200])
        if result is None:
            raise ValidationError(
                "Die Park4Night-Seite konnte nicht gelesen werden oder enthält "
                "keine GPS-Angabe"
            )
        return {"result": result}

    if action == "place_link_lookup":
        url = str(data.get("url") or "").strip().rstrip(".,;:")
        hint = str(data.get("hint_name") or "")[:200]
        if not url.lower().startswith("https://"):
            raise ValidationError(
                "Bitte einen vollständigen Link angeben (beginnend mit https://)"
            )
        host = (urlparse(url).hostname or "").casefold()
        if _google_maps_host(host):
            # Google-Maps links resolve deterministically - no AI involved.
            resolved = await async_resolve_google_maps_place(hass, url)
            if not resolved:
                raise ValidationError(
                    "Der Google-Maps-Link konnte nicht aufgelöst werden"
                )
            result: dict[str, Any] = {
                "provider": "google_maps",
                "url": url,
                "name": str(resolved.get("name") or "")[:200],
            }
            if "latitude" in resolved:
                result["latitude"] = float(resolved["latitude"])
                result["longitude"] = float(resolved["longitude"])
            return {"result": result}
        lookup = runtime.experience.p4n_lookup
        p4n_match = _PARK4NIGHT_ID_RE.search(url)
        page_place: dict[str, Any] = {}
        if p4n_match is None:
            # Deterministic first: many place pages (naturkartan.se,
            # campsite websites) carry their position in JSON-LD/geo
            # metadata - no AI needed. The AI reader stays the fallback.
            page_place = await async_fetch_page_place(hass, url)
            if "latitude" in page_place:
                return {
                    "result": {
                        "provider": "shared_link",
                        "url": url,
                        "latitude": float(page_place["latitude"]),
                        "longitude": float(page_place["longitude"]),
                        "name": str(page_place.get("name") or hint or "")[:200],
                        "diagnosis": _place_link_diagnosis(
                            page_place, ai_asked=False, ai_found=False
                        ),
                    }
                }
        result = None
        if lookup.available:
            if p4n_match is not None:
                result = await lookup.async_lookup(
                    f"https://park4night.com/lieu/{p4n_match.group('id')}/",
                    hint_name=hint,
                )
            else:
                result = await lookup.async_lookup_page(url, hint_name=hint)
        elif p4n_match is not None:
            raise ValidationError("Der Reisebegleiter (Gemini) ist nicht konfiguriert")
        diagnosis = _place_link_diagnosis(
            page_place, ai_asked=lookup.available, ai_found=result is not None
        )
        if result is not None:
            return {"result": {**result, "diagnosis": diagnosis}}
        # Nothing produced a position. Whatever the page DID say still goes
        # back: the form prefills the name and only the coordinates stay
        # empty, instead of the user facing a red banner and a blank form
        # (live report with a shared naturkartan.se link).
        page_name = str(page_place.get("name") or "")[:200]
        if page_name:
            return {
                "result": {
                    "provider": "shared_link",
                    "url": url,
                    "name": page_name,
                    "diagnosis": diagnosis,
                }
            }
        raise ValidationError(
            _place_link_failure_message(
                page_place, lookup_available=lookup.available
            )
            + f" [{diagnosis}]"
        )

    if action == "prepare_place_enrichment":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError(
                "Für die Ortsvervollständigung wurde keine Reise ausgewählt"
            )
        return await runtime.experience.async_prepare_place_enrichment(
            user_id=user_id,
            trip_id=trip_id,
            day_id=str(data.get("day_id") or "").strip() or None,
            stop_id=str(data.get("stop_id") or "").strip() or None,
            limit=_optional_int(data.get("limit")) or 20,
            use_ai_cleanup=data.get("use_ai_cleanup") is True,
        )

    if action == "submit_place_enrichment":
        trip_id = str(data.get("trip_id") or "").strip()
        preview_id = str(data.get("preview_id") or "").strip()
        selections = data.get("selections")
        manual_entries = data.get("manual_entries")
        cleanup_confirmations = data.get("cleanup_confirmations")
        if not trip_id or not preview_id:
            raise ValidationError(
                "Reise und Ortsvorschau werden für die Übergabe benötigt"
            )
        if not isinstance(selections, dict):
            raise ValidationError("Die ausgewählten Orte sind unvollständig")
        if manual_entries is not None and not isinstance(manual_entries, dict):
            raise ValidationError("Die manuellen Ortsdaten sind ungültig")
        if cleanup_confirmations is not None and not isinstance(
            cleanup_confirmations, dict
        ):
            raise ValidationError("Die KI-Bestätigungen sind ungültig")
        safe_manual_entries = {
            str(key): value
            for key, value in (manual_entries or {}).items()
            if isinstance(value, dict)
        }
        safe_cleanup_confirmations = {
            str(key): value is True
            for key, value in (cleanup_confirmations or {}).items()
        }
        return await runtime.experience.async_submit_place_enrichment(
            user_id=user_id,
            actor=actor,
            trip_id=trip_id,
            preview_id=preview_id,
            selections={str(key): str(value) for key, value in selections.items()},
            manual_entries=safe_manual_entries,
            cleanup_confirmations=safe_cleanup_confirmations,
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

    if action == "media_curate_stop":
        trip_id = str(data.get("trip_id") or "").strip()
        day_id = str(data.get("day_id") or "").strip()
        stop_id = str(data.get("stop_id") or "").strip()
        if not trip_id or not day_id or not stop_id:
            raise ValidationError("Reise, Reisetag und Stopp werden für die Bildauswahl benötigt")
        return await runtime.experience.async_curate_stop_media(
            trip_id,
            day_id,
            stop_id,
            force=bool(data.get("force", False)),
        )

    if action == "media_curate_trip":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für die Bildauswahl wurde keine Reise ausgewählt")
        return await runtime.experience.async_auto_curate_media(
            trip_id,
            limit=_optional_int(data.get("limit")) or 3,
            force=bool(data.get("force", False)),
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

    if action == "get_exchange_rates":
        rates_key = f"{DOMAIN}_ecb_rates"
        service = hass.data.get(rates_key)
        if not isinstance(service, EcbRateService):
            service = EcbRateService(hass)
            hass.data[rates_key] = service
        rates = await service.async_get_rates()
        if rates is None:
            raise ValidationError(
                "Die EZB-Referenzkurse sind gerade nicht abrufbar - die "
                "Summen je Währung bleiben unverändert verfügbar."
            )
        totals = data.get("totals_by_currency")
        summary = (
            eur_total(totals, rates["rates"]) if isinstance(totals, dict) else None
        )
        return {"rates": rates, "eur_total": summary}

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
        result = await manager.async_add_stop(
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
        manager.schedule_route_refresh()
        return result

    if action == "update_stop":
        patch = dict(data.get("patch") or {})
        for field in ("arrival_time", "departure_time"):
            if field in patch:
                patch[field] = _none_if_blank(patch[field])
        if "location" in patch:
            patch["location"] = _location(patch["location"])
        if "details" in patch:
            patch["details"] = _details(patch["details"])
        result = await manager.async_update_stop(
            day_id=data.get("day_id"),
            stop_id=data.get("stop_id"),
            patch=patch,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            position=_optional_int(data.get("position")),
            expected_trip_id=data.get("expected_trip_id"),
        )
        # Only route-relevant edits schedule a refresh: GPS/order/transport
        # changes. A notes- or time-only edit never touches the route. The
        # refresh itself is additionally input-hash-guarded downstream.
        if (
            _optional_int(data.get("position")) is not None
            or any(field in patch for field in ("location", "details", "type"))
        ):
            manager.schedule_route_refresh()
        return result

    pitch_operations = {
        "pitch_option_save": "save_option",
        "pitch_option_delete": "delete_option",
        "pitch_option_set_status": "set_option_status",
        "pitch_set_strategy": "set_strategy",
        "pitch_option_activate": "activate_option",
    }
    if action in pitch_operations:
        operation = pitch_operations[action]
        result = await manager.async_mutate_overnight_plan(
            day_id=data.get("day_id"),
            operation=operation,
            payload=dict(data.get("payload") or {}),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            expected_trip_id=data.get("expected_trip_id"),
        )
        if operation == "activate_option":
            # Activating an option rewrites the overnight stop's GPS - the
            # day's route (and the next day's start) changed.
            manager.schedule_route_refresh()
        return result

    if action == "pitch_update_preferences":
        preferences = data.get("preferences")
        if not isinstance(preferences, dict):
            raise ValidationError("'preferences' muss ein JSON-Objekt sein")
        return await manager.async_update_pitch_preferences(
            preferences=preferences,
            actor=actor,
            expected_revision=data.get("expected_revision"),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "remove_stop":
        result = await manager.async_remove_stop(
            day_id=data.get("day_id"),
            stop_id=data.get("stop_id"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            expected_trip_id=data.get("expected_trip_id"),
        )
        manager.schedule_route_refresh()
        return result

    if action == "pitch_route_overview":
        day_id = str(data.get("day_id") or "").strip()
        if not day_id:
            raise ValidationError("Für die Routenübersicht fehlt der Reisetag")
        service_key = f"{DOMAIN}_pitch_routes"
        service = hass.data.get(service_key)
        if not isinstance(service, PitchRouteService):
            service = PitchRouteService(hass, manager)
            hass.data[service_key] = service
        return await service.async_overview(day_id)

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

    if action == "export_trip_pdf":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den PDF-Export wurde keine Reise ausgewählt")
        pdf_bytes = await runtime.trip_pdf.async_generate(trip_id)
        token = await runtime.trip_pdf.async_create_ticket(pdf_bytes, user_id=user_id)
        # The ticket expires in five minutes; the library copy keeps the
        # same PDF retrievable afterwards without rebuilding it.
        library_url = await runtime.trip_pdf.async_store_in_library(pdf_bytes)
        return {
            "download_url": f"/api/roadplanner/trip_pdf/{token}",
            "library_url": library_url,
        }

    if action == "trip_pdf_status":
        return {"status": await runtime.trip_pdf.async_status()}

    if action == "run_system_check":
        # Probes the live interfaces from inside this Home Assistant, where
        # the credentials, the network and the real data actually are.
        return await async_run_system_check(
            hass, runtime, trip_id=str(data.get("trip_id") or "").strip()
        )

    if action == "export_trip_video":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError("Für den Video-Export wurde keine Reise ausgewählt")
        style = str(data.get("style") or "highlight").strip()
        # Starts a BACKGROUND build and returns immediately - the render
        # takes minutes and an open request would die on every mobile
        # connection change. Progress is polled via trip_video_status.
        return {"status": runtime.trip_video.async_start(trip_id, style=style)}

    if action == "trip_video_status":
        return {"status": await runtime.trip_video.async_status()}

    if action == "generate_trip_summaries":
        trip_id = str(data.get("trip_id") or "").strip()
        if not trip_id:
            raise ValidationError(
                "Für die Zusammenfassungen wurde keine Reise ausgewählt"
            )
        scope = str(data.get("scope") or "all").strip()
        if scope not in ("all", "days", "people", "trip"):
            raise ValidationError(f"Unbekannter Umfang: {scope}")
        # A BACKGROUND run, like the video export: one Vision call per day
        # plus one per crew member takes minutes, and an open request would
        # die on the first mobile connection change.
        return {"status": runtime.trip_summaries.async_start(trip_id, scope=scope)}

    if action == "trip_summaries_status":
        return {"status": await runtime.trip_summaries.async_status()}

    if action == "remotion_diagnose":
        # READ-ONLY. Installs nothing, downloads nothing - a missing
        # runtime is the answer, not a problem to fix silently.
        return {"remotion_diagnosis": await runtime.remotion_spike.async_diagnose()}

    if action == "remotion_test_render":
        # The id only has to be unique and filename-safe; it is also the
        # output filename, so it never comes from the client.
        job_id = f"remotion_spike_{uuid.uuid4().hex[:16]}"
        return {"status": runtime.remotion_spike.async_start(job_id=job_id)}

    if action == "remotion_status":
        return {"status": await runtime.remotion_spike.async_status()}

    if action == "remotion_cancel":
        return {"status": await runtime.remotion_spike.async_cancel()}

    if action == "renderer_app_environment":
        # READ-ONLY probe. Apps only exist under Home Assistant OS or
        # Supervised; on Container/Core no app can ever be installed, and
        # that is a result to report rather than a fault to work around.
        return {"renderer_app_environment": await runtime.renderer_app.async_environment()}

    if action == "renderer_app_status":
        return {"renderer_app_status": await runtime.renderer_app.async_app_status()}

    if action == "renderer_app_run":
        # The job id is generated server-side and becomes the filename, so
        # nothing a client sends can reach the filesystem.
        return {"renderer_app_job": await runtime.renderer_app.async_submit_test_job()}

    if action == "renderer_app_render":
        # Same mechanism as the plain test job - the render takes about
        # twelve seconds, so it is submitted and polled rather than waited
        # on inside the websocket call.
        return {
            "renderer_app_job": await runtime.renderer_app.async_submit_test_job(
                message="Roadplanner Remotion Test",
                action=ACTION_RENDER_REMOTION_TEST,
            )
        }

    if action == "story_manifest":
        # Read-only: the described trip, as both exporters will one day see
        # it. Nothing is generated and nothing is written; a cached
        # manifest is returned unchanged while the roadbook has not moved.
        return {
            "story_manifest": await runtime.story_context.async_manifest(
                str(data.get("trip_id") or ""),
                force=data.get("force") is True,
            )
        }

    if action == "story_set_override":
        # The editorial layer. The client sends only the fields it changed;
        # the details patch is composed server-side so a stale browser
        # cannot replace a day's details wholesale.
        return await runtime.story_overrides.async_set(
            trip_id=str(data.get("trip_id") or ""),
            day_id=str(data.get("day_id") or ""),
            changes=dict(data.get("changes") or {}),
            expected_revision=data.get("expected_revision"),
            actor=actor,
        )

    if action == "story_film_preview":
        # What a film would contain, counted from the cached manifest.
        # Nothing is downloaded and nothing is written.
        return {
            "story_film_preview": await runtime.trip_film.async_preview(
                str(data.get("trip_id") or "")
            )
        }

    if action == "story_film_render":
        # The whole trip. Minutes of render, so it is submitted and polled
        # exactly like the day video rather than awaited here.
        try:
            return {"renderer_app_job": await runtime.trip_film.async_submit(
                str(data.get("trip_id") or "")
            )}
        except RendererProtocolError as err:
            raise ValidationError(str(err)) from err

    if action == "renderer_app_trip_days":
        # Which days could be exported, and which have no photo to export.
        # Counted from data already loaded - nothing is downloaded here.
        return {
            "renderer_app_trip_days": await runtime.trip_day_mini_export.async_days(
                str(data.get("trip_id") or "")
            )
        }

    if action == "renderer_app_trip_day":
        # The mini export: ONE day, minimised, into the shared directory.
        # Same submit-and-poll shape as the test render, because the render
        # takes far longer than a websocket call should.
        try:
            return {
                "renderer_app_job": await runtime.trip_day_mini_export.async_submit(
                    str(data.get("trip_id") or ""),
                    str(data.get("day_id") or ""),
                )
            }
        except RendererProtocolError as err:
            raise ValidationError(str(err)) from err

    if action == "renderer_app_job_status":
        job_id = str(data.get("job_id") or "")
        try:
            status = await runtime.renderer_app.async_job_status(job_id)
            result = (
                await runtime.renderer_app.async_result(job_id)
                if status.get("state") == "completed"
                else None
            )
        except RendererProtocolError as err:
            # A protocol violation is a report about the exchange folder,
            # not an internal failure - it belongs in front of the user.
            raise ValidationError(str(err)) from err
        return {"renderer_app_job": status, "renderer_app_result": result}

    if action == "park4night_autofill_run":
        # Reads the linked Park4Night pages and parks the coordinates as a
        # REVIEW handoff - never applied without confirmation.
        return {
            "status": await runtime.park4night_autofill.async_run(
                force=data.get("force") is True
            )
        }

    if action == "plan_day_calendar_repair":
        # Read-only: says what a repair WOULD do, changes nothing.
        return await manager.async_plan_day_calendar_repair(
            str(data.get("trip_id") or "").strip() or None
        )

    if action == "propose_day_calendar_repair":
        # Parks the repair under "Übergaben" - removing a day is
        # destructive and stays the traveller's decision.
        return await manager.async_propose_day_calendar_repair(
            str(data.get("trip_id") or "").strip() or None
        )

    if action == "scan_handoffs":
        return await manager.async_scan_handoffs()

    if action == "preview_handoff":
        return await manager.async_preview_handoff(
            data.get("handoff_id"),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "apply_handoff":
        result = await manager.async_apply_handoff(
            handoff_id=data.get("handoff_id"),
            actor=actor,
            expected_revision=data.get("expected_revision"),
            confirm_destructive=bool(data.get("confirm_destructive", False)),
            expected_trip_id=data.get("expected_trip_id"),
        )
        # An applied handoff can add/move stops across several days; the
        # input hash limits the refresh to the days that actually changed.
        manager.schedule_route_refresh()
        return result

    if action == "archive_handoff":
        return await manager.async_archive_handoff(
            handoff_id=data.get("handoff_id"),
            resolution=str(data.get("resolution") or "rejected"),
            note=str(data.get("note") or ""),
            expected_trip_id=data.get("expected_trip_id"),
        )

    if action == "rebase_handoff":
        return await manager.async_rebase_handoff(
            data.get("handoff_id"),
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

    if action == "crew_person_add":
        value = data.get("value")
        if not isinstance(value, dict):
            raise ValidationError("Personendaten müssen ein JSON-Objekt sein")
        return await runtime.crew.async_create_person(value=value)

    if action == "crew_person_update":
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Personenänderung muss ein JSON-Objekt sein")
        return await runtime.crew.async_update_person(
            person_id=str(data.get("person_id") or ""),
            patch=patch,
        )

    if action == "crew_person_retire":
        return await runtime.crew.async_set_person_active(
            person_id=str(data.get("person_id") or ""),
            active=False,
        )

    if action == "crew_person_reactivate":
        return await runtime.crew.async_set_person_active(
            person_id=str(data.get("person_id") or ""),
            active=True,
        )

    if action == "crew_vehicle_add":
        value = data.get("value")
        if not isinstance(value, dict):
            raise ValidationError("Fahrzeugdaten müssen ein JSON-Objekt sein")
        return await runtime.crew.async_create_vehicle(value=value)

    if action == "crew_vehicle_update":
        patch = data.get("patch")
        if not isinstance(patch, dict):
            raise ValidationError("Fahrzeugänderung muss ein JSON-Objekt sein")
        return await runtime.crew.async_update_vehicle(
            vehicle_id=str(data.get("vehicle_id") or ""),
            patch=patch,
        )

    if action == "crew_vehicle_retire":
        return await runtime.crew.async_set_vehicle_active(
            vehicle_id=str(data.get("vehicle_id") or ""),
            active=False,
        )

    if action == "crew_vehicle_reactivate":
        return await runtime.crew.async_set_vehicle_active(
            vehicle_id=str(data.get("vehicle_id") or ""),
            active=True,
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
        # crew_state has no dependency on which trip is selected, so it can
        # be fetched concurrently with the main payload instead of after it.
        payload, crew_state = await asyncio.gather(
            runtime.manager.async_get_panel_payload(msg.get("trip_id")),
            runtime.crew.async_panel_payload(),
        )
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
    # Portraits are kept as local files so a face never depends on OneDrive
    # still being reachable (live request: "Ich fände es auch sinnvoll wenn
    # die Bilder der Crew permanent lokal gespeichert werden"). Missing ones
    # are fetched once, here, and never again.
    portraits = getattr(runtime, "crew_portrait_service", None)
    if portraits is not None:
        try:
            crew_state = await portraits.async_refresh(crew_state, selected_trip_id)
        except Exception:  # noqa: BLE001 - a portrait must never fail the panel
            _LOGGER.debug("Crew portraits could not be refreshed", exc_info=True)
    assistant_state = runtime.assistant.state(
        _user_id(connection),
        selected_trip_id,
    )
    # archive_state and experience_state both only need selected_trip_id (and
    # experience_state additionally needs payload's own days), so once payload
    # has resolved they have no dependency on each other and can run together.
    if selected_trip_id:
        archive_state, experience_state = await asyncio.gather(
            runtime.travel_archive.async_panel_payload(selected_trip_id),
            runtime.experience.async_panel_payload(
                selected_trip_id,
                days=list(payload.get("days", {}).get("days", []) or []),
            ),
        )
    else:
        archive_state = {"documents": [], "expenses": [], "todos": [], "stats": {}, "by_day": {}, "by_stop": {}}
        experience_state = {"decisions": [], "media": [], "destination_galleries": {}, "presentation": {}, "stats": {}, "by_day": {}, "by_stop": {}, "vision": {}, "onedrive": runtime.experience.onedrive.status()}
    summary_state = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    integrity_state = build_travel_integrity(
        list(payload.get("days", {}).get("days", []) or []),
        destination_galleries=experience_state.get("destination_galleries"),
        media_by_stop=experience_state.get("by_stop"),
        route_metrics=summary_state.get("route_metrics"),
    )
    payload.update(
        {
            "integration_version": INTEGRATION_VERSION,
            "entry_id": next(iter(hass.data[DOMAIN])),
            "capabilities": capabilities,
            "assistant": assistant_state,
            "travel_archive": archive_state,
            "experience": experience_state,
            "crew": crew_state,
            "integrity": integrity_state,
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
                "destination_image_provider": (
                    "wikimedia_commons+openverse+google_places"
                    if runtime.image_provider_google_photos_enabled
                    else "wikimedia_commons+openverse"
                ),
                "destination_image_gallery_size": 3,
                "destination_image_auto_fill": True,
                "media_curation_mode": runtime.experience.media_curation_mode,
                "media_vision_enabled": runtime.experience.vision_enabled,
                "media_vision_max_candidates": runtime.experience.media_vision_max_candidates,
                "media_vision_max_highlights": runtime.experience.media_vision_max_highlights,
                "media_vision_daily_limit": runtime.experience.media_vision_daily_limit,
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
                "video_export_available": ffmpeg_available(),
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
        action_call = _execute_action(
            hass,
            connection,
            msg["action"],
            dict(msg.get("data") or {}),
        )
        if msg["action"] in _PROVIDER_CALL_ACTIONS:
            # A detached task survives this handler's own cancellation (e.g.
            # the connection dropping mid-call); shield only protects the
            # inner task from THIS await being cancelled, not from real
            # provider errors, which still propagate and are handled below.
            result = await asyncio.shield(hass.async_create_task(action_call))
        else:
            result = await action_call
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
    frontend_dir = Path(__file__).parent / "frontend"
    frontend_file = frontend_dir / "roadplanner-panel.js"
    if not frontend_file.is_file():
        raise FileNotFoundError(
            f"Roadplanner panel module is missing: {frontend_file}"
        )
    # The whole directory is served (not just the entry file) so that the
    # entry module can be split into ES module collaborators under
    # frontend/lib/ and frontend/features/ without touching this
    # registration again for every new file. Cache-busting on deploy is
    # handled by the "?v=" query parameter on module_url below, but that only
    # cache-busts the entry file itself - the entry's static imports of
    # ./features/*.js and ./lib/*.js carry no such parameter and would
    # otherwise be silently heuristically cached by the browser across a
    # version upgrade. A dedicated view (not the plain HA static-path helper)
    # sends an explicit Cache-Control on every file so that can't happen -
    # see frontend_static_http.py's module docstring for the concrete bug
    # this fixed.
    async_register_frontend_static_view(
        hass,
        url_path=PANEL_STATIC_URL,
        frontend_dir=frontend_dir,
    )
    _LOGGER.info(
        "Registered Roadplanner panel static directory %s at %s",
        frontend_dir,
        PANEL_STATIC_URL,
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
