"""Sign and validate short-lived HMAC tokens for thumbnail/original media
redirect URLs, and resolve them against OneDrive.

Each service instance keeps its own random secret, so tokens do not survive
a Home Assistant restart - that is intentional, they are short-lived
(``_MEDIA_TOKEN_TTL_SECONDS``) and only used to authorize the panel's own
media redirect requests.

Resolving a photo's provider URL costs one Microsoft Graph call each time,
so the resolved URL is memoized for a few minutes - far below its own
validity. The image bytes themselves keep flowing straight from the
provider CDN to the browser; Home Assistant never relays them.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from time import monotonic

from homeassistant.core import HomeAssistant

from .experience_store import ExperienceStore
from .onedrive_media import OneDrivePersonalClient
from .roadplanner import ValidationError

_MEDIA_TOKEN_TTL_SECONDS = 60 * 60
_URL_CACHE_TTL_SECONDS = 5 * 60
_URL_CACHE_MAX_ENTRIES = 2_000


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
        # Resolved provider URLs, memoized well below their own lifetime.
        self._url_cache: dict[tuple[str, str, str], tuple[str, float]] = {}

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

    def _cached_url(self, key: tuple[str, str, str]) -> str | None:
        entry = self._url_cache.get(key)
        if entry is None:
            return None
        url, expires = entry
        if expires <= monotonic():
            self._url_cache.pop(key, None)
            return None
        return url

    def _remember_url(self, key: tuple[str, str, str], url: str) -> None:
        if len(self._url_cache) >= _URL_CACHE_MAX_ENTRIES:
            # Cheap bounded eviction: drop the entry that expires first.
            oldest = min(self._url_cache, key=lambda item: self._url_cache[item][1])
            self._url_cache.pop(oldest, None)
        self._url_cache[key] = (url, monotonic() + _URL_CACHE_TTL_SECONDS)

    async def async_media_redirect_url(
        self, trip_id: str, media_id: str, kind: str, *, size: str = "large"
    ) -> str:
        """Resolve a photo's short-lived provider URL, briefly memoized.

        Every thumbnail the panel shows costs one Microsoft Graph call to
        resolve this URL - scrolling a trip with hundreds of memories used
        to mean hundreds of calls. The resolved URL is reused for a few
        minutes (far below its own validity), while the image bytes keep
        flowing straight from the provider CDN to the browser.
        """
        cache_key = (media_id, kind, size if kind == "thumbnail" else "original")
        cached = self._cached_url(cache_key)
        if cached:
            return cached
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = next((item for item in state["media"] if item.get("id") == media_id), None)
        if media is None:
            raise ValidationError("Foto nicht gefunden")
        if kind == "thumbnail":
            url = await self.onedrive.async_thumbnail_url(
                str(media["provider_item_id"]), size
            )
        else:
            url = await self.onedrive.async_download_url(str(media["provider_item_id"]))
        self._remember_url(cache_key, url)
        return url
