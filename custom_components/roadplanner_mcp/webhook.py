"""Legacy authenticated context webhook and review-only ChangeSet import."""

from __future__ import annotations

from http import HTTPStatus
import json
import secrets
from typing import Any

from aiohttp import web
from aiohttp.web import Request, Response

from homeassistant.components import webhook
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import get_url

from .changeset import APPLY_MODE_REVIEW, normalize_changeset
from .const import (
    DOMAIN,
    EVENT_HANDOFF_CONFLICT,
    EVENT_HANDOFF_RECEIVED,
    MAX_WEBHOOK_BYTES,
)
from .handoff import HandoffConflictError, extract_changeset
from .manager import RoadplannerManager
from .roadplanner import RoadplannerError, ValidationError

HEADER_TOKEN = "X-Roadplanner-Token"
HEADER_TITLE = "X-Roadplanner-Title"
HEADER_SOURCE = "X-Roadplanner-Source"
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def async_register_handoff_webhook(
    hass: HomeAssistant,
    manager: RoadplannerManager,
    *,
    webhook_id: str,
    webhook_token: str,
) -> None:
    """Register the secret legacy context bridge and review-only importer."""

    async def handle(
        _hass: HomeAssistant,
        _webhook_id: str,
        request: Request,
    ) -> Response:
        return await _async_handle(
            hass,
            manager,
            request,
            expected_token=webhook_token,
        )

    webhook.async_register(
        hass,
        DOMAIN,
        "Roadplanner ChangeSet bridge",
        webhook_id,
        handle,
        local_only=False,
        allowed_methods={"GET", "POST"},
    )


def _json_error(message: str, status: HTTPStatus) -> Response:
    return web.json_response(
        {"ok": False, "error": message},
        status=status,
        headers=_NO_STORE_HEADERS,
    )


def _is_authorized(request: Request, expected_token: str) -> bool:
    supplied_token = request.headers.get(HEADER_TOKEN, "")
    return bool(supplied_token) and secrets.compare_digest(
        supplied_token,
        expected_token,
    )


async def _async_handle(
    hass: HomeAssistant,
    manager: RoadplannerManager,
    request: Request,
    *,
    expected_token: str,
) -> Response:
    if not _is_authorized(request, expected_token):
        return _json_error("unauthorized", HTTPStatus.UNAUTHORIZED)

    if request.method == "GET":
        return await _async_handle_context_get(manager, request)
    if request.method != "POST":
        return _json_error("method_not_allowed", HTTPStatus.METHOD_NOT_ALLOWED)
    return await _async_handle_handoff_post(hass, manager, request)


async def _async_handle_context_get(
    manager: RoadplannerManager,
    request: Request,
) -> Response:
    output_format = request.query.get("format", "json").strip().casefold()
    try:
        if output_format in {"markdown", "md"}:
            result = await manager.async_get_context_markdown()
            headers = {
                **_NO_STORE_HEADERS,
                "X-Roadplanner-Trip-Id": str(result["trip_id"]),
                "X-Roadplanner-Revision": str(result["revision"]),
            }
            return web.Response(
                text=result["content"],
                content_type="text/markdown",
                charset="utf-8",
                headers=headers,
            )
        if output_format != "json":
            return _json_error("unsupported_format", HTTPStatus.BAD_REQUEST)
        context = await manager.async_get_context_payload()
        return web.json_response(context, headers=_NO_STORE_HEADERS)
    except RoadplannerError as err:
        return _json_error(str(err), HTTPStatus.BAD_REQUEST)


def _handoff_kwargs(
    payload: dict[str, Any] | None,
    body: bytes,
    content_type: str,
    request: Request,
) -> dict[str, Any]:
    if payload is None:
        return {
            "content": body.decode("utf-8"),
            "title": request.headers.get(
                HEADER_TITLE,
                "Roadplanner-Übergabe",
            ),
            "source": request.headers.get(HEADER_SOURCE, "webhook"),
            "content_type": content_type,
        }

    if payload.get("kind") in {
        "roadplanner_changeset",
        "roadplanner_handoff",
    }:
        return {
            "changeset": payload,
            "title": payload.get("title", "Roadplanner-Übergabe"),
            "source": request.headers.get(HEADER_SOURCE, "webhook"),
            "content_type": "application/json",
        }

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValidationError("'metadata' muss ein JSON-Objekt sein")
    common = {
        "title": payload.get("title", "Roadplanner-Übergabe"),
        "source": payload.get("source", "webhook"),
        "content_type": payload.get("content_type", content_type),
        "external_id": payload.get("external_id"),
        "trip_id": payload.get("trip_id"),
        "base_revision": payload.get("base_revision"),
        "metadata": metadata,
    }
    raw_changeset = payload.get("changeset")
    if isinstance(raw_changeset, dict):
        common["changeset"] = raw_changeset
        common["content_type"] = "application/json"
        return common

    raw_content = payload.get("content")
    if isinstance(raw_content, dict):
        common["changeset"] = raw_content
        common["content_type"] = "application/json"
        return common
    if isinstance(raw_content, str):
        common["content"] = raw_content
        return common
    raise ValidationError("'changeset' oder 'content' fehlt oder ist ungültig")


async def _async_handle_handoff_post(
    hass: HomeAssistant,
    manager: RoadplannerManager,
    request: Request,
) -> Response:
    length = request.content_length
    if length is not None and length > MAX_WEBHOOK_BYTES:
        return _json_error(
            "payload_too_large",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )
    body = await request.content.read(MAX_WEBHOOK_BYTES + 1)
    if len(body) > MAX_WEBHOOK_BYTES:
        return _json_error(
            "payload_too_large",
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        )

    payload: dict[str, Any] | None = None
    content_type = request.headers.get("Content-Type", "text/plain").split(";", 1)[0]
    if "json" in content_type.casefold():
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            return _json_error("invalid_json", HTTPStatus.BAD_REQUEST)
        if not isinstance(decoded, dict):
            return _json_error("json_object_required", HTTPStatus.BAD_REQUEST)
        payload = decoded

    try:
        kwargs = _handoff_kwargs(payload, body, content_type, request)
        raw_changeset = kwargs.pop("changeset", None)
        if raw_changeset is None:
            raw_changeset = extract_changeset(kwargs.pop("content"))
        normalized = normalize_changeset(raw_changeset)
        if normalized["apply_mode"] != APPLY_MODE_REVIEW:
            raise ValidationError(
                "Externe Webhook-Importe müssen apply_mode='review' verwenden"
            )
        transport_trip_id = kwargs.pop("trip_id", None)
        if (
            transport_trip_id is not None
            and transport_trip_id != normalized["trip_id"]
        ):
            raise ValidationError(
                "trip_id der Transporthülle stimmt nicht mit dem ChangeSet überein"
            )
        transport_revision = kwargs.pop("base_revision", None)
        if (
            transport_revision is not None
            and transport_revision != normalized["base_revision"]
        ):
            raise ValidationError(
                "base_revision der Transporthülle stimmt nicht mit dem "
                "ChangeSet überein"
            )
        result = await manager.async_ingest_external_changeset(
            changeset=raw_changeset,
            title=kwargs.pop("title"),
            source=kwargs.pop("source"),
            external_id=kwargs.pop("external_id", None),
            metadata=kwargs.pop("metadata", {}),
        )
    except HandoffConflictError as err:
        return _json_error(str(err), HTTPStatus.CONFLICT)
    except (UnicodeDecodeError, TypeError, ValueError, RoadplannerError) as err:
        return _json_error(str(err), HTTPStatus.BAD_REQUEST)

    handoff = result["handoff"]
    if not result.get("duplicate"):
        event_payload = {
            "id": handoff["id"],
            "changeset_id": normalized["changeset_id"],
            "trip_id": normalized["trip_id"],
            "status": handoff.get("status"),
            "source": handoff.get("source"),
        }
        if handoff.get("status") == "conflict":
            hass.bus.async_fire(EVENT_HANDOFF_CONFLICT, event_payload)
        else:
            hass.bus.async_fire(EVENT_HANDOFF_RECEIVED, event_payload)
    return web.json_response(
        {
            "ok": True,
            "duplicate": bool(result.get("duplicate")),
            "handoff": handoff,
        },
        headers=_NO_STORE_HEADERS,
    )


def async_unregister_handoff_webhook(
    hass: HomeAssistant,
    webhook_id: str,
) -> None:
    webhook.async_unregister(hass, webhook_id)


def async_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return a cloud-preferred bridge URL with safe local fallbacks."""
    base_url = get_url(
        hass,
        allow_internal=True,
        allow_external=True,
        allow_cloud=True,
        prefer_external=True,
        prefer_cloud=True,
    )
    return f"{base_url.rstrip('/')}{webhook.async_generate_path(webhook_id)}"
