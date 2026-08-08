"""Ordering music from Lyria, deliberately and at most once per brief.

Kept apart from the film exporter on purpose, and the separation is the
feature rather than tidiness. The exporter must have no way to reach a
paid call: not a fallback, not a "generate if missing", not a helpful
retry. It reads a folder of audio files. This service is the only thing
in Roadplanner that can put a new file in that folder, and it runs only
when somebody asked for it after being shown a price.

That the cache *is* the music folder is the other half of it. A generated
track lands beside whatever the user put there themselves, under a name
derived from the trip's brief - so re-rendering the same film finds it
the same way it finds any other track, through the ordinary "pick a
name" path. There is no second cache to keep in step with the first, and
no code anywhere that has to remember to look in two places.

Failure is always the same shape: no music, and a film that renders.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .roadplanner import ValidationError
from .trip_film_lyria import (
    LYRIA_TIMEOUT_SECONDS,
    LyriaError,
    audio_from_response,
    brief_from_trip,
    build_prompt,
    build_request,
    cache_key,
    cost_notice,
    extension_for,
    track_filename,
)
from .trip_film_music import DEFAULT_MUSIC_ROOT, MAX_TRACK_BYTES

_LOGGER = logging.getLogger(__name__)


class TripFilmMusicService:
    """Generate one soundtrack for one trip, and only when asked."""

    def __init__(
        self,
        hass: Any,
        story_context: Any,
        session_factory: Any,
        *,
        api_key_provider: Any,
        music_root: str | Path = DEFAULT_MUSIC_ROOT,
    ) -> None:
        self._hass = hass
        self._story_context = story_context
        self._session_factory = session_factory
        self._api_key_provider = api_key_provider
        self._root = Path(music_root)

    async def _async_brief(self, trip_id: str) -> tuple[dict[str, Any], str]:
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Musik fehlt die Reise-ID")
        manifest = await self._story_context.async_manifest(trip_id)
        brief = brief_from_trip(
            manifest.get("trip") or {}, manifest.get("narrative") or {}
        )
        return brief, build_prompt(brief)

    async def async_offer(self, trip_id: str) -> dict[str, Any]:
        """What generating would cost and what would be ordered.

        Reads nothing but the manifest and writes nothing. If a track for
        this brief already exists it says so, and then generating is not
        a decision anybody has to make again.
        """
        brief, prompt = await self._async_brief(trip_id)
        key = cache_key(brief, prompt)
        existing = await self._hass.async_add_executor_job(self._find_cached, key)
        notice = cost_notice(brief)
        notice.update(
            {
                "cached": bool(existing),
                "cached_name": existing or "",
                "available": bool(self._api_key_provider()),
            }
        )
        return notice

    async def async_generate(self, trip_id: str) -> dict[str, Any]:
        """Order one track. The only method here that can cost money.

        A cached track for the same brief short-circuits before any
        request is built, which is what makes a repeated attempt free -
        and what makes two films of the same trip sound the same.
        """
        brief, prompt = await self._async_brief(trip_id)
        key = cache_key(brief, prompt)
        existing = await self._hass.async_add_executor_job(self._find_cached, key)
        if existing:
            return {"name": existing, "generated": False, "cached": True}

        api_key = self._api_key_provider()
        if not api_key:
            raise ValidationError(
                "Für KI-Musik ist kein Google-Schlüssel konfiguriert"
            )

        url, body = build_request(prompt)
        try:
            session = self._session_factory()
            async with session.post(
                url,
                json=body,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                timeout=LYRIA_TIMEOUT_SECONDS,
            ) as response:
                if response.status >= 400:
                    detail = (await response.text())[:300]
                    raise LyriaError(
                        f"Lyria hat den Auftrag abgelehnt ({response.status}): {detail}"
                    )
                payload = await response.json(content_type=None)
        except LyriaError:
            raise
        except Exception as err:  # noqa: BLE001 - any transport failure reads the same
            _LOGGER.warning("Musikgenerierung fehlgeschlagen: %s", type(err).__name__)
            raise ValidationError(
                "Die Musik konnte nicht erzeugt werden. Der Film läuft ohne."
            ) from err

        found = audio_from_response(payload)
        if not found:
            raise ValidationError(
                "Lyria hat keine Audiodaten zurückgegeben. Der Film läuft ohne Musik."
            )
        blob, mime_type = found
        if len(blob) > MAX_TRACK_BYTES:
            raise ValidationError("Die erzeugte Musikdatei ist zu groß")
        name = track_filename(key, extension_for(mime_type))
        written = await self._hass.async_add_executor_job(self._store, name, blob)
        if not written:
            raise ValidationError("Die erzeugte Musik konnte nicht gespeichert werden")
        return {
            "name": name,
            "generated": True,
            "cached": False,
            "size_bytes": len(blob),
        }

    # --- blocking helpers, run in the executor --------------------------

    def _find_cached(self, key: str) -> str:
        """A track already generated for this brief, by name."""
        for extension in ("mp3", "wav"):
            try:
                candidate = self._root / track_filename(key, extension)
            except ValueError:
                return ""
            try:
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate.name
            except OSError:
                continue
        return ""

    def _store(self, name: str, data: bytes) -> str:
        """Write the track into the folder the film already reads."""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            target = self._root / name
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(target)
        except OSError as err:
            _LOGGER.warning("Erzeugte Musik nicht speicherbar: %s", err)
            return ""
        return name


__all__ = ["TripFilmMusicService"]
