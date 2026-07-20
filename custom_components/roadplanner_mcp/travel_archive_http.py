"""Ticket-protected upload and download views for Roadplanner documents."""

from __future__ import annotations

from http import HTTPStatus
import logging
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

UPLOAD_URL = "/api/roadplanner/archive/upload/{token}"
DOWNLOAD_URL = "/api/roadplanner/archive/file/{token}"


def _runtime(hass: HomeAssistant):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise ValidationError("Roadplanner ist nicht geladen")
    return next(iter(runtimes.values()))


class RoadplannerArchiveUploadView(HomeAssistantView):
    """Consume a short-lived, single-use upload ticket."""

    url = UPLOAD_URL
    name = "api:roadplanner:archive:upload"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request, token: str) -> web.Response:
        try:
            runtime = _runtime(self.hass)
            ticket = await runtime.travel_archive.async_claim_upload_ticket(token)
            maximum_request = runtime.travel_archive.max_upload_bytes + 512 * 1024
            if request.content_length is not None and request.content_length > maximum_request:
                raise ValidationError("Die Datei überschreitet das konfigurierte Upload-Limit")
            request._client_max_size = maximum_request  # noqa: SLF001
            reader = await request.multipart()
            file_part = None
            while True:
                part = await reader.next()
                if part is None:
                    break
                if part.name == "file" and getattr(part, "filename", None):
                    file_part = part
                    break
            if file_part is None:
                raise ValidationError("Im Upload wurde keine Datei gefunden")

            temp_path: Path = await self.hass.async_add_executor_job(
                runtime.travel_archive.store.new_temp_path
            )
            size = 0
            handle = await self.hass.async_add_executor_job(temp_path.open, "wb")
            try:
                while True:
                    chunk = await file_part.read_chunk(size=1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > runtime.travel_archive.max_upload_bytes:
                        raise ValidationError(
                            "Die Datei überschreitet das konfigurierte Upload-Limit"
                        )
                    await self.hass.async_add_executor_job(handle.write, chunk)
                await self.hass.async_add_executor_job(handle.flush)
            finally:
                await self.hass.async_add_executor_job(handle.close)
            try:
                result = await runtime.travel_archive.async_finalize_upload(
                    ticket=ticket,
                    temp_path=temp_path,
                    original_filename=str(file_part.filename or "document"),
                    declared_mime=str(file_part.headers.get("Content-Type") or ""),
                )
            finally:
                await self.hass.async_add_executor_job(
                    lambda: temp_path.unlink(missing_ok=True)
                )
        except RoadplannerError as err:
            return self.json(
                {"ok": False, "error": str(err)},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except (ValueError, OSError, web.HTTPException) as err:
            _LOGGER.warning("Roadplanner document upload failed: %s", type(err).__name__)
            return self.json(
                {"ok": False, "error": "Dokument konnte nicht hochgeladen werden"},
                status_code=HTTPStatus.BAD_REQUEST,
            )
        except Exception:  # pragma: no cover - defensive HTTP boundary
            _LOGGER.exception("Unexpected Roadplanner document upload error")
            return self.json(
                {"ok": False, "error": "Dokument-Upload ist unerwartet fehlgeschlagen"},
                status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return self.json({"ok": True, "document": result})


class RoadplannerArchiveFileView(HomeAssistantView):
    """Serve one private document through a short-lived download ticket."""

    url = DOWNLOAD_URL
    name = "api:roadplanner:archive:file"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        try:
            runtime = _runtime(self.hass)
            path, document = await runtime.travel_archive.async_resolve_download_ticket(token)
        except RoadplannerError as err:
            raise web.HTTPUnauthorized(text=str(err)) from err
        filename = str(document.get("original_filename") or path.name)
        disposition = f"inline; filename*=UTF-8''{quote(filename, safe='')}"
        response = web.FileResponse(
            path,
            headers={
                "Content-Type": str(document.get("mime_type") or "application/octet-stream"),
                "Content-Disposition": disposition,
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
            },
        )
        return response


def async_register_travel_archive_views(hass: HomeAssistant) -> None:
    """Register archive HTTP views once per Home Assistant process."""
    marker = f"{DOMAIN}_travel_archive_views_registered"
    if hass.data.get(marker):
        return
    hass.http.register_view(RoadplannerArchiveUploadView(hass))
    hass.http.register_view(RoadplannerArchiveFileView(hass))
    hass.data[marker] = True
