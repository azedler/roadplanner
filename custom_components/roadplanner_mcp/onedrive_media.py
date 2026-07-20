"""Read-only Microsoft OneDrive Personal client for Roadplanner media sync."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time
from typing import Any
from uuid import UUID
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, ClientSession

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN, INTEGRATION_VERSION
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

_GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_AUTH_ROOT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
_SCOPES = "offline_access Files.Read User.Read"
_TOKEN_STORE_KEY = f"{DOMAIN}.onedrive_personal"
_TOKEN_STORE_VERSION = 1
_DRIVE_ITEM_SELECT = (
    "id,name,size,webUrl,createdDateTime,lastModifiedDateTime,"
    "file,fileSystemInfo,image,photo,location,parentReference,folder,package,deleted"
)

def normalize_onedrive_folder_path(
    value: Any,
    *,
    allow_empty: bool = False,
    default: str = "",
) -> str:
    """Return a safe canonical path relative to the OneDrive root.

    Older Roadplanner releases exposed the photo folder in two places.  The
    in-panel setup is now authoritative.  Adjacent duplicate components are
    collapsed to repair values such as ``Photos/Camera/Camera`` without
    changing otherwise legitimate nested paths.
    """

    raw = str(value or "").replace("\\", "/").strip()
    if not raw and default:
        raw = str(default).replace("\\", "/").strip()
    parts: list[str] = []
    for component in raw.split("/"):
        component = component.strip()
        if not component or component == ".":
            continue
        if component == ".." or "\x00" in component:
            raise ValidationError("Ungültiger OneDrive-Ordnerpfad")
        if parts and parts[-1].casefold() == component.casefold():
            continue
        parts.append(component)
    path = "/".join(parts)
    if not path and not allow_empty:
        raise ValidationError("OneDrive-Ordnerpfad fehlt")
    return path


class OneDriveError(RoadplannerError):
    """Raised for a sanitized OneDrive failure."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class OneDrivePersonalClient:
    """Minimal OAuth device-flow and Microsoft Graph client.

    Tokens are stored through Home Assistant's private storage helper. Only
    delegated read access is requested. The client never uploads, moves, or
    deletes files in OneDrive.
    """

    def __init__(self, hass: Any, *, client_id: str, entry_id: str = "default") -> None:
        self.hass = hass
        self.client_id = self.normalize_client_id(client_id)
        self._session: ClientSession = async_get_clientsession(hass)
        safe_entry_id = "".join(ch for ch in str(entry_id or "default") if ch.isalnum() or ch in "-_")[:128] or "default"
        self._store: Store[dict[str, Any]] = Store(
            hass, _TOKEN_STORE_VERSION, f"{_TOKEN_STORE_KEY}.{safe_entry_id}"
        )
        self._data: dict[str, Any] = {}
        self._pending: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    async def async_initialize(self) -> None:
        loaded = await self._store.async_load()
        self._data = loaded if isinstance(loaded, dict) else {}
        # The in-panel setup wizard is authoritative once it has stored an
        # application ID.  This prevents an older config-entry option from
        # silently reverting a later panel-based reconfiguration after restart.
        configured_client_id = str(
            self._data.get("configured_client_id") or ""
        ) or self.client_id
        self.client_id = self.normalize_client_id(configured_client_id)
        stored_token_client_id = str(self._data.get("client_id") or "")
        if stored_token_client_id and stored_token_client_id != self.client_id:
            # OAuth refresh tokens belong to the public-client registration
            # that issued them. Preserve local setup preferences, but never try
            # to reuse the token after the application ID changes.
            settings = self._data.get("settings") if isinstance(self._data.get("settings"), dict) else {}
            self._data = {
                "configured_client_id": self.client_id,
                "settings": settings,
            }
            await self._store.async_save(dict(self._data))
        elif self.client_id:
            self._data["configured_client_id"] = self.client_id
            await self._store.async_save(dict(self._data))

    @staticmethod
    def normalize_client_id(value: str) -> str:
        """Return a canonical Microsoft application ID or an empty string."""
        raw = str(value or "").strip()
        if not raw:
            return ""
        try:
            return str(UUID(raw))
        except (ValueError, AttributeError) as err:
            raise ValidationError(
                "Die Microsoft-Anwendungs-ID muss eine gültige GUID sein"
            ) from err

    async def async_reconfigure(
        self,
        client_id: str,
        *,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update the public-client application ID and local sync settings."""
        normalized = self.normalize_client_id(client_id)
        async with self._lock:
            previous = self.client_id
            current_settings = (
                dict(self._data.get("settings"))
                if isinstance(self._data.get("settings"), dict)
                else {}
            )
            if settings:
                current_settings.update(settings)
            if normalized != previous:
                self.client_id = normalized
                self._pending = None
                # Refresh tokens are bound to the app registration that issued
                # them. A changed client ID therefore requires authorization.
                self._data = {
                    "configured_client_id": normalized,
                    "settings": current_settings,
                }
            else:
                self._data["configured_client_id"] = normalized
                self._data["settings"] = current_settings
            self._last_error = None
            await self._store.async_save(dict(self._data))
            return self.status()

    def stored_settings(self) -> dict[str, Any]:
        value = self._data.get("settings")
        return dict(value) if isinstance(value, dict) else {}

    @property
    def connected(self) -> bool:
        return bool(self._data.get("refresh_token") or self._data.get("access_token"))

    def status(self) -> dict[str, Any]:
        pending = self._pending or {}
        return {
            "configured": bool(self.client_id),
            "requires_application_id": not bool(self.client_id),
            "client_id_hint": (f"…{self.client_id[-8:]}" if self.client_id else None),
            "connected": self.connected,
            "account_name": self._data.get("account_name"),
            "account_email": self._data.get("account_email"),
            "drive_id": self._data.get("drive_id"),
            "token_expires_at": self._data.get("expires_at"),
            "authorization_pending": bool(pending),
            "authorization_expires_at": pending.get("expires_at"),
            "last_error": self._last_error,
            "permissions": ["Files.Read", "User.Read", "offline_access"],
            "account_type": "onedrive_personal",
            "setup_settings": self.stored_settings(),
        }

    async def _save(self) -> None:
        await self._store.async_save(dict(self._data))

    async def async_disconnect(self) -> None:
        """Remove Microsoft tokens while preserving the local setup wizard values."""
        async with self._lock:
            settings = (
                dict(self._data.get("settings"))
                if isinstance(self._data.get("settings"), dict)
                else {}
            )
            self._data = {
                "configured_client_id": self.client_id,
                "settings": settings,
            }
            self._pending = None
            self._last_error = None
            await self._store.async_save(dict(self._data))

    async def async_start_device_authorization(self) -> dict[str, Any]:
        if not self.client_id:
            raise ValidationError(
                "Für OneDrive fehlt die Microsoft-Anwendungs-ID. Bitte zuerst im Roadplanner-Fotobereich auf ‚OneDrive einrichten‘ tippen."
            )
        data = {"client_id": self.client_id, "scope": _SCOPES}
        headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": f"HomeAssistant-Roadplanner/{INTEGRATION_VERSION}"}
        try:
            async with self._session.post(
                f"{_AUTH_ROOT}/devicecode",
                data=data,
                headers=headers,
                timeout=30,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200 or not isinstance(payload, dict):
                    raise OneDriveError("Microsoft konnte die OneDrive-Anmeldung nicht starten")
        except (ClientError, asyncio.TimeoutError, ValueError) as err:
            raise OneDriveError("Microsoft ist für die OneDrive-Anmeldung derzeit nicht erreichbar") from err
        device_code = str(payload.get("device_code") or "")
        user_code = str(payload.get("user_code") or "")
        verification_uri = str(payload.get("verification_uri_complete") or payload.get("verification_uri") or "")
        if not device_code or not user_code or not verification_uri:
            raise OneDriveError("Microsoft hat keinen vollständigen Gerätecode geliefert")
        expires_in = max(60, min(int(payload.get("expires_in") or 900), 1800))
        interval = max(3, min(int(payload.get("interval") or 5), 30))
        self._pending = {
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "message": str(payload.get("message") or ""),
            "interval": interval,
            "expires_monotonic": time.monotonic() + expires_in,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "last_poll_monotonic": 0.0,
        }
        return {
            "status": "pending",
            "user_code": user_code,
            "verification_uri": verification_uri,
            "message": self._pending["message"],
            "interval": interval,
            "expires_in": expires_in,
            "expires_at": self._pending["expires_at"],
        }

    async def async_poll_device_authorization(self) -> dict[str, Any]:
        pending = self._pending
        if not pending:
            if self.connected:
                return {"status": "connected", **self.status()}
            raise ValidationError("Es läuft keine OneDrive-Anmeldung")
        if time.monotonic() >= float(pending["expires_monotonic"]):
            self._pending = None
            raise ValidationError("Der OneDrive-Anmeldecode ist abgelaufen")
        wait = int(pending["interval"]) - (time.monotonic() - float(pending["last_poll_monotonic"]))
        if wait > 0:
            return {"status": "pending", "retry_after": round(wait, 1), "user_code": pending["user_code"], "verification_uri": pending["verification_uri"]}
        pending["last_poll_monotonic"] = time.monotonic()
        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": pending["device_code"],
        }
        try:
            async with self._session.post(f"{_AUTH_ROOT}/token", data=data, timeout=30) as response:
                payload = await response.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError) as err:
            raise OneDriveError("Microsoft ist für die OneDrive-Anmeldung derzeit nicht erreichbar") from err
        if response.status != 200:
            error = str(payload.get("error") or "") if isinstance(payload, dict) else ""
            description = str(payload.get("error_description") or "") if isinstance(payload, dict) else ""
            if error == "authorization_pending":
                return {"status": "pending", "retry_after": int(pending["interval"]), "user_code": pending["user_code"], "verification_uri": pending["verification_uri"]}
            if error == "slow_down":
                pending["interval"] = min(30, int(pending["interval"]) + 5)
                return {"status": "pending", "retry_after": int(pending["interval"]), "user_code": pending["user_code"], "verification_uri": pending["verification_uri"]}
            self._pending = None
            raise OneDriveError((description or "Die OneDrive-Anmeldung wurde abgelehnt")[:500])
        await self._accept_token_payload(payload)
        self._pending = None
        await self._load_profile()
        return {"status": "connected", **self.status()}

    async def _accept_token_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise OneDriveError("Microsoft hat kein gültiges Zugriffstoken geliefert")
        expires_in = max(60, int(payload.get("expires_in") or 3600))
        self._data.update(
            {
                "access_token": str(payload["access_token"]),
                "refresh_token": str(payload.get("refresh_token") or self._data.get("refresh_token") or ""),
                "token_type": str(payload.get("token_type") or "Bearer"),
                "scope": str(payload.get("scope") or _SCOPES),
                "expires_at_epoch": time.time() + expires_in - 60,
                "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "client_id": self.client_id,
            }
        )
        self._last_error = None
        await self._save()

    async def _ensure_access_token(self) -> str:
        if not self.client_id:
            raise ValidationError("Microsoft-Anwendungs-ID fehlt")
        access_token = str(self._data.get("access_token") or "")
        expires_at = float(self._data.get("expires_at_epoch") or 0)
        if access_token and time.time() < expires_at:
            return access_token
        refresh_token = str(self._data.get("refresh_token") or "")
        if not refresh_token:
            raise ValidationError("OneDrive ist nicht verbunden")
        async with self._lock:
            access_token = str(self._data.get("access_token") or "")
            expires_at = float(self._data.get("expires_at_epoch") or 0)
            if access_token and time.time() < expires_at:
                return access_token
            data = {
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": _SCOPES,
            }
            try:
                async with self._session.post(f"{_AUTH_ROOT}/token", data=data, timeout=30) as response:
                    payload = await response.json(content_type=None)
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                raise OneDriveError("OneDrive-Zugriff konnte nicht erneuert werden") from err
            if response.status != 200:
                self._last_error = "refresh_failed"
                raise OneDriveError("Die OneDrive-Verbindung muss erneut autorisiert werden")
            await self._accept_token_payload(payload)
            return str(self._data["access_token"])

    async def _graph_json(self, path_or_url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = path_or_url if path_or_url.startswith("https://") else f"{_GRAPH_ROOT}{path_or_url}"
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "graph.microsoft.com":
            raise ValidationError("Unsichere Microsoft-Graph-URL")
        token = await self._ensure_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": f"HomeAssistant-Roadplanner/{INTEGRATION_VERSION}"}
        try:
            async with self._session.get(url, params=params, headers=headers, timeout=60, allow_redirects=False) as response:
                payload = await response.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError) as err:
            raise OneDriveError("OneDrive ist derzeit nicht erreichbar") from err
        if response.status == 401:
            self._data["expires_at_epoch"] = 0
            token = await self._ensure_access_token()
            headers["Authorization"] = f"Bearer {token}"
            try:
                async with self._session.get(url, params=params, headers=headers, timeout=60, allow_redirects=False) as response:
                    payload = await response.json(content_type=None)
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                raise OneDriveError("OneDrive ist derzeit nicht erreichbar") from err
        if response.status != 200 or not isinstance(payload, dict):
            message = ""
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                message = str(payload["error"].get("message") or "")
            self._last_error = f"http_{response.status}"
            raise OneDriveError(
                (message or f"OneDrive hat die Anfrage abgelehnt (HTTP {response.status})")[:500],
                status=response.status,
            )
        self._last_error = None
        return payload

    async def _load_profile(self) -> None:
        profile = await self._graph_json("/me", params={"$select": "id,displayName,mail,userPrincipalName"})
        drive = await self._graph_json("/me/drive", params={"$select": "id,driveType,owner"})
        self._data.update(
            {
                "account_id": profile.get("id"),
                "account_name": profile.get("displayName"),
                "account_email": profile.get("mail") or profile.get("userPrincipalName"),
                "drive_id": drive.get("id"),
            }
        )
        await self._save()

    async def async_resolve_folder(self, folder_path: str) -> dict[str, Any]:
        path = normalize_onedrive_folder_path(folder_path, allow_empty=True)
        if not path:
            endpoint = "/me/drive/root"
        else:
            endpoint = f"/me/drive/root:/{quote(path, safe='/')}"
        payload = await self._graph_json(endpoint, params={"$select": "id,name,folder,parentReference,webUrl"})
        if not isinstance(payload.get("folder"), dict):
            raise ValidationError("Der konfigurierte OneDrive-Pfad ist kein Ordner")
        return payload

    async def async_list_children(
        self,
        folder_id: str,
        *,
        cursor_link: str | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        """Return one page of direct children for a folder.

        The caller controls recursive traversal and persists the returned
        ``next_link``.  Only metadata is read; file contents and thumbnails are
        not downloaded during discovery.
        """
        safe_id = quote(str(folder_id or ""), safe="")
        if not safe_id:
            raise ValidationError("OneDrive-Ordner-ID fehlt")
        size = max(1, min(int(page_size or 100), 200))
        url = cursor_link or f"{_GRAPH_ROOT}/me/drive/items/{safe_id}/children"
        params = None if cursor_link else {
            "$select": _DRIVE_ITEM_SELECT,
            "$top": str(size),
        }
        payload = await self._graph_json(url, params=params)
        raw_items = payload.get("value")
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        return {
            "items": items,
            "next_link": str(payload.get("@odata.nextLink") or "") or None,
        }

    async def async_latest_delta(self, folder_id: str) -> str:
        """Return a delta cursor representing the folder's current state.

        The cursor is captured *before* the selective recursive initial scan.
        Once that scan finishes, Roadplanner consumes changes since this cursor
        so that uploads made during the scan are not missed.
        """
        safe_id = quote(str(folder_id or ""), safe="")
        if not safe_id:
            raise ValidationError("OneDrive-Ordner-ID fehlt")
        url: str | None = f"{_GRAPH_ROOT}/me/drive/items/{safe_id}/delta"
        params: dict[str, str] | None = {
            "token": "latest",
            "$select": _DRIVE_ITEM_SELECT,
        }
        for _ in range(5):
            if not url:
                break
            payload = await self._graph_json(url, params=params)
            params = None
            delta_link = str(payload.get("@odata.deltaLink") or "")
            if delta_link:
                return delta_link
            url = str(payload.get("@odata.nextLink") or "") or None
        raise OneDriveError("Microsoft hat keinen Delta-Ausgangspunkt geliefert")

    async def async_delta(
        self,
        folder_id: str,
        *,
        cursor_link: str | None,
        max_items: int = 2000,
    ) -> dict[str, Any]:
        """Return one bounded recursive delta page and a resumable cursor.

        Microsoft Graph's delta feed tracks a driveItem and all descendants.
        Roadplanner intentionally consumes one page per run, which keeps Home
        Assistant responsive even for large camera archives.
        """
        safe_id = quote(str(folder_id or ""), safe="")
        if not safe_id:
            raise ValidationError("OneDrive-Ordner-ID fehlt")
        url = cursor_link or f"{_GRAPH_ROOT}/me/drive/items/{safe_id}/delta"
        params = None if cursor_link else {
            "$select": _DRIVE_ITEM_SELECT,
            "$top": str(max(1, min(int(max_items or 2000), 5000))),
        }
        try:
            payload = await self._graph_json(url, params=params)
        except OneDriveError as err:
            if cursor_link and err.status == 410:
                _LOGGER.info("OneDrive delta cursor expired; a selective rescan is required")
                return {
                    "items": [],
                    "next_link": None,
                    "delta_link": None,
                    "truncated": False,
                    "resync": True,
                }
            raise
        raw_items = payload.get("value")
        items = [item for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        next_link = str(payload.get("@odata.nextLink") or "") or None
        delta_link = str(payload.get("@odata.deltaLink") or "") or None
        return {
            "items": items,
            "next_link": next_link,
            "delta_link": delta_link,
            "truncated": bool(next_link),
            "resync": False,
        }

    async def async_thumbnail_url(self, item_id: str, size: str = "large") -> str:
        safe_id = quote(str(item_id or ""), safe="")
        size = size if size in {"small", "medium", "large"} else "large"
        payload = await self._graph_json(f"/me/drive/items/{safe_id}/thumbnails")
        value = payload.get("value")
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            raise ValidationError("Für dieses OneDrive-Foto ist kein Vorschaubild verfügbar")
        thumbnail = value[0].get(size)
        url = str(thumbnail.get("url") or "") if isinstance(thumbnail, dict) else ""
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationError("OneDrive hat keine sichere Vorschaubild-URL geliefert")
        return url

    async def async_download_url(self, item_id: str) -> str:
        safe_id = quote(str(item_id or ""), safe="")
        payload = await self._graph_json(f"/me/drive/items/{safe_id}")
        url = str(payload.get("@microsoft.graph.downloadUrl") or "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValidationError("OneDrive hat keine sichere Download-URL geliefert")
        return url
