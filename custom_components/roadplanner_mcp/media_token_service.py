"""Sign and validate short-lived HMAC tokens for thumbnail/original media
redirect URLs, and resolve them against OneDrive.

Each service instance keeps its own random secret, so tokens do not survive
a Home Assistant restart - that is intentional, they are short-lived
(``_MEDIA_TOKEN_TTL_SECONDS``) and only used to authorize the panel's own
media redirect requests.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import secrets

from homeassistant.core import HomeAssistant

from .experience_store import ExperienceStore
from .onedrive_media import OneDrivePersonalClient
from .roadplanner import ValidationError

_MEDIA_TOKEN_TTL_SECONDS = 60 * 60


class MediaTokenService:
    """Issue and validate signed, short-lived media redirect tokens."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        onedrive: OneDrivePersonalClient,
    ) -> None:
        self.hass = hass
        self.store = store
        self.onedrive = onedrive
        self._token_secret = secrets.token_bytes(32)

    def token(self, trip_id: str, media_id: str, kind: str) -> str:
        expires = int(datetime.now(timezone.utc).timestamp()) + _MEDIA_TOKEN_TTL_SECONDS
        payload = f"{trip_id}|{media_id}|{kind}|{expires}"
        signature = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def validate_token(self, trip_id: str, media_id: str, kind: str, token: str) -> bool:
        try:
            expires_text, signature = token.split(".", 1)
            expires = int(expires_text)
        except (ValueError, AttributeError):
            return False
        if expires < int(datetime.now(timezone.utc).timestamp()):
            return False
        payload = f"{trip_id}|{media_id}|{kind}|{expires}"
        expected = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    async def async_media_redirect_url(
        self, trip_id: str, media_id: str, kind: str, *, size: str = "large"
    ) -> str:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = next((item for item in state["media"] if item.get("id") == media_id), None)
        if media is None:
            raise ValidationError("Foto nicht gefunden")
        if kind == "thumbnail":
            return await self.onedrive.async_thumbnail_url(
                str(media["provider_item_id"]), size
            )
        return await self.onedrive.async_download_url(str(media["provider_item_id"]))
