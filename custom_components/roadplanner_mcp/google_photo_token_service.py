"""Sign and validate short-lived HMAC tokens for Google Places photo redirects.

Mirrors media_token_service.py's pattern for OneDrive media: a saved
destination gallery persists only the durable Google `photo_name` resource
reference, never the photo's actual URL or bytes (Google's Places API terms
do not allow long-term storage of the photo itself). Each browser request
re-resolves a fresh, short-lived URL through this service and the
integration never retains the resolved bytes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import secrets

from .google_places import GooglePlacesClient
from .roadplanner import ValidationError

_GOOGLE_PHOTO_TOKEN_TTL_SECONDS = 60 * 60


class GooglePhotoTokenService:
    """Issue and validate signed, short-lived Google photo redirect tokens."""

    def __init__(self, google_places: GooglePlacesClient) -> None:
        self._google_places = google_places
        self._token_secret = secrets.token_bytes(32)

    def token(self, photo_name: str) -> str:
        expires = int(datetime.now(timezone.utc).timestamp()) + _GOOGLE_PHOTO_TOKEN_TTL_SECONDS
        payload = f"{photo_name}|{expires}"
        signature = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def validate_token(self, photo_name: str, token: str) -> bool:
        try:
            expires_text, signature = token.split(".", 1)
            expires = int(expires_text)
        except (ValueError, AttributeError):
            return False
        if expires < int(datetime.now(timezone.utc).timestamp()):
            return False
        payload = f"{photo_name}|{expires}"
        expected = hmac.new(self._token_secret, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)

    async def async_redirect_url(self, photo_name: str) -> str:
        target = await self._google_places.async_resolve_photo_url(photo_name)
        if not target:
            raise ValidationError("Google-Foto ist nicht mehr verfügbar")
        return target


__all__ = ["GooglePhotoTokenService"]
