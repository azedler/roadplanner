"""Getting a picture of the camper into the film, without a provider.

The film can already draw a character - a small camper on the map, a
larger one in the opening. What it never had was a way for one to
*arrive*. An image model would be the obvious route and is deliberately
not this: it costs money on every attempt, it returns a different
vehicle each time, and the whole value of a character asset is that it
is the same vehicle in every film. So the first route is the one that
needs no provider at all: somebody uploads a drawing of their own
camper.

The two-step write is kept exactly as the store defines it. An upload
lands as a **candidate**; it becomes the asset films use only when
somebody has looked at it and said so. That is not ceremony - a wrong
picture, silently promoted, is a wrong camper in every film from then
on, and nothing about the file would say why.

What is never trusted
---------------------

The bytes. They arrive from a browser and are re-encoded through Pillow
before anything is stored: an upload that is not an image never becomes
a file, and one that is arrives as a normalised PNG at film size with
its transparent margin trimmed. The stored NAME is a digest of the
stored bytes, so nothing anybody typed reaches a path join.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from .character_assets import (
    CHARACTER_KINDS,
    CHARACTER_VARIANTS,
    MAX_ASSET_BYTES,
    asset_filename,
    fingerprint,
    shrink_asset,
)
from .roadplanner import ValidationError

_LOGGER = logging.getLogger(__name__)

# What may arrive before it has been re-encoded. Generous compared with
# the 260 KB an asset ends up as, because a phone camera's PNG is large
# and refusing it for its size would be refusing it for the wrong
# reason - but bounded, because it crosses a websocket.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


class CharacterAssetService:
    """Upload, look at, confirm or throw away a character picture."""

    def __init__(self, hass: Any, store: Any) -> None:
        self._hass = hass
        self._store = store

    async def async_state(self) -> dict[str, Any]:
        """Which characters the film has, and which are waiting.

        A candidate is returned WITH its bytes, as a data URL. That is
        deliberate: the alternative is a new HTTP route guarded by an
        unguessable filename, which is a bearer secret rather than
        authentication - and there is no reason to mint one for a
        picture the panel already has an authenticated channel to.
        """
        confirmed = await self._hass.async_add_executor_job(self._store.confirmed)
        pending = await self._hass.async_add_executor_job(self._pending)
        return {
            "kinds": list(CHARACTER_KINDS),
            "variants": list(CHARACTER_VARIANTS),
            "confirmed": confirmed,
            "pending": pending,
        }

    async def async_upload(self, kind: str, variant: str, data_url: str) -> dict[str, Any]:
        """Take a picture from the panel and put it in the waiting room."""
        kind = str(kind or "").strip()
        variant = str(variant or "").strip()
        if kind not in CHARACTER_KINDS:
            raise ValidationError("Unbekannte Figurenart")
        if variant not in CHARACTER_VARIANTS:
            raise ValidationError("Unbekannte Ansicht")
        raw = _decode_upload(data_url)
        if not raw:
            raise ValidationError("Das hochgeladene Bild konnte nicht gelesen werden")
        shrunk = await self._hass.async_add_executor_job(shrink_asset, raw)
        if not shrunk:
            raise ValidationError(
                "Das Bild ließ sich nicht verwenden. Erwartet wird ein PNG "
                "mit transparentem Hintergrund."
            )
        # The name is a digest of what was stored, so the same picture
        # uploaded twice is the same file rather than a second one.
        filename = asset_filename(kind, variant, fingerprint(shrunk))
        written = await self._hass.async_add_executor_job(
            self._write_candidate, filename, shrunk
        )
        if not written:
            raise ValidationError("Das Bild konnte nicht gespeichert werden")
        return {
            "filename": filename,
            "kind": kind,
            "variant": variant,
            "size_bytes": len(shrunk),
            "preview": _data_url(shrunk),
            # Said plainly, because the film will not show it yet and
            # "nothing happened" is the wrong conclusion to draw.
            "confirmed": False,
        }

    async def async_confirm(self, filename: str) -> dict[str, Any]:
        """The one step that lets a film draw it."""
        done = await self._hass.async_add_executor_job(
            self._store.confirm, str(filename or "")
        )
        if not done:
            raise ValidationError("Dieses Figurenbild wartet nicht auf Bestätigung")
        return await self.async_state()

    async def async_discard(self, filename: str) -> dict[str, Any]:
        await self._hass.async_add_executor_job(
            self._store.discard, str(filename or "")
        )
        return await self.async_state()

    # --- blocking helpers ------------------------------------------------

    def _write_candidate(self, filename: str, data: bytes) -> str:
        return self._store.write(filename, data, candidate=True)

    def _pending(self) -> list[dict[str, Any]]:
        """Candidates waiting to be looked at, with their pictures."""
        found: list[dict[str, Any]] = []
        # The store addresses candidates by asset name, so the waiting
        # room is enumerated from the folder rather than guessed.
        for name in self._store.candidates():
            data = self._store.read(name, candidate=True)
            if not data:
                continue
            kind, variant, _ = name.split("-", 2)
            found.append(
                {
                    "filename": name,
                    "kind": kind,
                    "variant": variant,
                    "size_bytes": len(data),
                    "preview": _data_url(data),
                }
            )
        return found


def _decode_upload(data_url: str) -> bytes:
    """Bytes from what the panel sends, or nothing.

    Accepts a data URL or bare base64. Never raises for bad input: an
    unreadable upload is a message to a person, not a traceback.
    """
    text = str(data_url or "").strip()
    if not text:
        return b""
    if text.startswith("data:"):
        _, _, text = text.partition(",")
    # A data URL of 8 MB of bytes is about 11 MB of text.
    if len(text) > MAX_UPLOAD_BYTES * 2:
        return b""
    try:
        raw = base64.b64decode(text, validate=False)
    except Exception:  # noqa: BLE001 - any malformed upload reads the same
        return b""
    return raw if 0 < len(raw) <= MAX_UPLOAD_BYTES else b""


def _data_url(data: bytes) -> str:
    """A stored asset, small enough to travel with the panel's answer."""
    if not data or len(data) > MAX_ASSET_BYTES:
        return ""
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


__all__ = ["MAX_UPLOAD_BYTES", "CharacterAssetService"]
