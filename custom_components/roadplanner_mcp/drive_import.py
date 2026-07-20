"""Authenticated fixed HTTP endpoint for external ChangeSet imports."""

from __future__ import annotations

from collections import defaultdict, deque
from http import HTTPStatus
import hmac
import json
import logging
from time import monotonic
from typing import Any

from aiohttp import web
from aiohttp.web import Request, Response

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import get_url

from .const import (
    DOMAIN,
    DRIVE_IMPORT_PATH,
    DRIVE_IMPORT_RATE_LIMIT_REQUESTS,
    DRIVE_IMPORT_RATE_LIMIT_WINDOW_SECONDS,
    DRIVE_IMPORT_SECRET_HEADER,
    EVENT_HANDOFF_CONFLICT,
    EVENT_HANDOFF_RECEIVED,
    MAX_DRIVE_IMPORT_BYTES,
)
from .external_import import (
    drive_import_external_id,
    drive_import_metadata,
    normalize_drive_import_payload,
)
from .handoff import HandoffConflictError
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)
_DATA_VIEW_REGISTERED = f"{DOMAIN}_drive_import_view_registered"
_NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
}


def _json_response(
    payload: dict[str, Any],
    status: HTTPStatus,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    return web.json_response(
        payload,
        status=status,
        headers={**_NO_STORE_HEADERS, **(headers or {})},
    )


def _error(
    code: str,
    message: str,
    status: HTTPStatus,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    return _json_response(
        {"status": "error", "code": code, "message": message},
        status,
        headers=headers,
    )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Doppelter JSON-Schlüssel: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Ungültiger JSON-Zahlenwert: {value}")


def _runtime(hass: HomeAssistant) -> Any | None:
    for runtime in hass.data.get(DOMAIN, {}).values():
        if hasattr(runtime, "manager"):
            return runtime
    return None


class _RateLimiter:
    """Small in-memory abuse guard for the shared-secret endpoint."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> tuple[bool, int]:
        now = monotonic()
        threshold = now - DRIVE_IMPORT_RATE_LIMIT_WINDOW_SECONDS
        bucket = self._requests[key]
        while bucket and bucket[0] <= threshold:
            bucket.popleft()
        if len(bucket) >= DRIVE_IMPORT_RATE_LIMIT_REQUESTS:
            retry_after = max(
                1,
                int(
                    DRIVE_IMPORT_RATE_LIMIT_WINDOW_SECONDS
                    - (now - bucket[0])
                ),
            )
            return False, retry_after
        bucket.append(now)
        if len(self._requests) > 1_000:
            self._prune(threshold)
        return True, 0

    def _prune(self, threshold: float) -> None:
        stale = [
            key
            for key, bucket in self._requests.items()
            if not bucket or bucket[-1] <= threshold
        ]
        for key in stale:
            self._requests.pop(key, None)


class RoadplannerDriveImportView(HomeAssistantView):
    """Receive one validated external ChangeSet for manual review."""

    url = DRIVE_IMPORT_PATH
    name = "api:roadplanner:drive-import"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._rate_limiter = _RateLimiter()

    async def post(self, request: Request) -> Response:
        """Authenticate, validate, and enqueue one ChangeSet."""
        hass = self._hass
        remote = request.remote or "unknown"
        runtime = _runtime(hass)
        if runtime is None or not runtime.webhook_token:
            return _error(
                "bridge_unavailable",
                "Die externe Roadplanner-Bridge ist nicht aktiviert.",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        supplied_secret = request.headers.get(DRIVE_IMPORT_SECRET_HEADER, "")
        if not supplied_secret or not hmac.compare_digest(
            supplied_secret.encode("utf-8"),
            runtime.webhook_token.encode("utf-8"),
        ):
            allowed, retry_after = self._rate_limiter.allow(
                f"unauthorized:{remote}"
            )
            if not allowed:
                return _error(
                    "rate_limited",
                    "Zu viele Importversuche. Bitte später erneut versuchen.",
                    HTTPStatus.TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(retry_after)},
                )
            return _error(
                "unauthorized",
                "Authentifizierung fehlgeschlagen.",
                HTTPStatus.UNAUTHORIZED,
            )
        allowed, retry_after = self._rate_limiter.allow(
            f"authorized:{remote}"
        )
        if not allowed:
            return _error(
                "rate_limited",
                "Zu viele Importversuche. Bitte später erneut versuchen.",
                HTTPStatus.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
            )

        if request.content_type.casefold() != "application/json":
            return _error(
                "unsupported_media_type",
                "Content-Type muss application/json sein.",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
        length = request.content_length
        if length is not None and length > MAX_DRIVE_IMPORT_BYTES:
            return _error(
                "payload_too_large",
                "Der Request ist größer als 256 KiB.",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        body = await request.content.read(MAX_DRIVE_IMPORT_BYTES + 1)
        if len(body) > MAX_DRIVE_IMPORT_BYTES:
            return _error(
                "payload_too_large",
                "Der Request ist größer als 256 KiB.",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            decoded = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=_strict_json_object,
                parse_constant=_reject_json_constant,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ):
            return _error(
                "invalid_json",
                "Der Request enthält kein gültiges JSON.",
                HTTPStatus.BAD_REQUEST,
            )
        if not isinstance(decoded, dict):
            return _error(
                "json_object_required",
                "Das Transportobjekt muss ein JSON-Objekt sein.",
                HTTPStatus.BAD_REQUEST,
            )

        try:
            payload = normalize_drive_import_payload(decoded)
            changeset = payload["changeset"]
            source_changeset = payload["source_changeset"]
            drive_file = payload["drive_file"]
            title = str(
                source_changeset.get("title")
                or drive_file.get("name")
                or changeset.get("summary")
                or "Externe Roadplanner-Übergabe"
            )
            result = await runtime.manager.async_ingest_external_changeset(
                changeset=source_changeset,
                title=title,
                source="external_changeset_import",
                external_id=drive_import_external_id(payload),
                metadata=drive_import_metadata(payload),
                source_payload_sha256=payload["changeset_source_sha256"],
            )
        except HandoffConflictError as err:
            return _error(
                "changeset_id_conflict",
                str(err),
                HTTPStatus.CONFLICT,
            )
        except (ValidationError, RoadplannerError, TypeError, ValueError) as err:
            return _error(
                "invalid_payload",
                str(err),
                HTTPStatus.BAD_REQUEST,
            )
        except Exception:
            _LOGGER.exception("Unexpected Roadplanner external import error")
            return _error(
                "internal_error",
                "Die Übergabe konnte nicht verarbeitet werden.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

        handoff = result["handoff"]
        changeset_id = changeset["changeset_id"]
        if result["duplicate"]:
            return _json_response(
                {
                    "status": "already_imported",
                    "changeset_id": changeset_id,
                    "handoff_id": handoff["id"],
                    "inbox_status": _public_inbox_status(handoff.get("status")),
                },
                HTTPStatus.OK,
            )

        inbox_status = _public_inbox_status(handoff.get("status"))
        event_payload = {
            "id": handoff["id"],
            "changeset_id": changeset_id,
            "trip_id": changeset["trip_id"],
            "status": handoff.get("status"),
            "source": "external_changeset_import",
        }
        if handoff.get("status") == "conflict":
            hass.bus.async_fire(EVENT_HANDOFF_CONFLICT, event_payload)
        else:
            hass.bus.async_fire(EVENT_HANDOFF_RECEIVED, event_payload)

        response: dict[str, Any] = {
            "status": "accepted",
            "changeset_id": changeset_id,
            "handoff_id": handoff["id"],
            "inbox_status": inbox_status,
        }
        preview = result.get("preview") or {}
        if handoff.get("status") == "conflict":
            response.update(
                {
                    "expected_revision": preview.get("current_revision"),
                    "received_revision": changeset["base_revision"],
                }
            )
        return _json_response(response, HTTPStatus.ACCEPTED)


def _public_inbox_status(status: Any) -> str:
    if status == "conflict":
        return "revision_conflict"
    if status in {"pending", "review_required"}:
        return "pending_review"
    return str(status or "pending_review")


def async_drive_import_url(hass: HomeAssistant) -> str:
    """Return the cloud-preferred public URL for the fixed import endpoint."""
    base_url = get_url(
        hass,
        allow_internal=False,
        allow_external=True,
        allow_cloud=True,
        prefer_external=True,
        prefer_cloud=True,
        require_ssl=True,
    )
    return f"{base_url.rstrip('/')}{DRIVE_IMPORT_PATH}"


@callback
def async_register_drive_import_view(hass: HomeAssistant) -> None:
    """Register the fixed endpoint once for the Home Assistant process."""
    if hass.data.get(_DATA_VIEW_REGISTERED):
        return
    hass.http.register_view(RoadplannerDriveImportView(hass))
    hass.data[_DATA_VIEW_REGISTERED] = True
