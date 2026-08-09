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
import math
from pathlib import Path
from typing import Any

from .roadplanner import ValidationError
from .trip_film_music_plan import (
    build_plan,
    cost_notice as plan_cost_notice,
    section_cache_key,
)
from .trip_film_lyria import (
    LYRIA_ESTIMATED_COST_USD,
    LYRIA_MODEL,
    LYRIA_PRICE_NOTE,
    LYRIA_TIMEOUT_SECONDS,
    LYRIA_TRACK_SECONDS,
    LyriaError,
    audio_from_response,
    build_request,
    extension_for,
    track_filename,
)
from .trip_film_music import DEFAULT_MUSIC_ROOT, MAX_TRACK_BYTES

_LOGGER = logging.getLogger(__name__)

# The film's length is an estimate: a photo that cannot be fetched
# shortens its day by a second or two. If the plan reacted to that, a
# re-render with one missing picture would be a second full soundtrack
# at full price. So the length is rounded UP to whole half-minutes
# before anything is planned - up, so the music is never shorter than
# the film, and coarse enough that ordinary drift changes nothing.
LENGTH_QUANTUM_SECONDS = 30.0


def quantized_seconds(film_seconds: float) -> float:
    """The length the soundtrack is planned for."""
    total = max(0.0, float(film_seconds or 0.0))
    if total <= 0:
        return 0.0
    return math.ceil(total / LENGTH_QUANTUM_SECONDS) * LENGTH_QUANTUM_SECONDS


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

    async def _async_plan(self, trip_id: str, film_seconds: float) -> dict[str, Any]:
        """The soundtrack this film would get, without ordering any of it."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Musik fehlt die Reise-ID")
        manifest = await self._story_context.async_manifest(trip_id)
        return build_plan(
            trip=manifest.get("trip") or {},
            narrative=manifest.get("narrative") or {},
            film_seconds=quantized_seconds(film_seconds),
            track_seconds=LYRIA_TRACK_SECONDS,
        )

    async def async_offer(
        self, trip_id: str, *, film_seconds: float = 0.0
    ) -> dict[str, Any]:
        """What generating would cost and what would be ordered.

        Reads the manifest and the music folder, writes nothing. A
        section already generated for this plan is named as cached, and
        "four sections, three already there, one new" is a materially
        different decision from "four new" - so it is the one shown.
        """
        plan = await self._async_plan(trip_id, film_seconds)
        sections = await self._async_section_state(plan)
        cached = sum(1 for entry in sections if entry["cached_name"])
        notice = plan_cost_notice(
            plan,
            model=LYRIA_MODEL,
            price_per_generation=LYRIA_ESTIMATED_COST_USD,
            cached=cached,
        )
        notice.update(
            {
                "plan": plan,
                "section_state": sections,
                "price_note": LYRIA_PRICE_NOTE,
                "available": bool(self._api_key_provider()),
            }
        )
        return notice

    async def _async_section_state(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        """For each section: its key, and the file already generated for it."""
        state: list[dict[str, Any]] = []
        for section in plan.get("sections") or []:
            key = section_cache_key(section, model=LYRIA_MODEL)
            existing = await self._hass.async_add_executor_job(self._find_cached, key)
            state.append(
                {
                    "section": section.get("section"),
                    "label": section.get("label"),
                    "seconds": section.get("seconds"),
                    "start_seconds": section.get("start_seconds"),
                    "fade_in_seconds": section.get("fade_in_seconds"),
                    "fade_out_seconds": section.get("fade_out_seconds"),
                    "key": key,
                    "cached_name": existing or "",
                }
            )
        return state

    async def async_timeline(
        self, trip_id: str, *, film_seconds: float = 0.0
    ) -> list[dict[str, Any]]:
        """What is already generated for this film, and when it plays.

        Read-only by construction, and that is the point: this is the
        one method the film exporter is given. It can see the tracks
        that exist; it has no way to order one. A section nobody paid
        for is simply absent from the timeline, and the film renders
        with the sections that are there.
        """
        plan = await self._async_plan(trip_id, film_seconds)
        return [
            {
                "name": entry["cached_name"],
                "start_seconds": entry["start_seconds"],
                "seconds": entry["seconds"],
                "fade_in_seconds": entry["fade_in_seconds"],
                "fade_out_seconds": entry["fade_out_seconds"],
            }
            for entry in await self._async_section_state(plan)
            if entry["cached_name"]
        ]

    async def async_generate(
        self, trip_id: str, *, film_seconds: float = 0.0
    ) -> dict[str, Any]:
        """Order the sections that are missing. The only paid method here.

        Sections already generated for this plan short-circuit before any
        request is built. That is what makes a re-render free and what
        makes two films of the same trip sound the same - and it is per
        section, so a film that grew by a minute regenerates the section
        whose length changed rather than the whole score.
        """
        plan = await self._async_plan(trip_id, film_seconds)
        state = await self._async_section_state(plan)
        missing = [entry for entry in state if not entry["cached_name"]]
        if missing and not self._api_key_provider():
            raise ValidationError(
                "Für KI-Musik ist kein Google-Schlüssel konfiguriert"
            )

        generated = 0
        for section, entry in zip(plan.get("sections") or [], state):
            if entry["cached_name"]:
                continue
            blob, mime_type = await self._async_order(str(section.get("prompt") or ""))
            name = track_filename(entry["key"], extension_for(mime_type))
            written = await self._hass.async_add_executor_job(self._store, name, blob)
            if not written:
                raise ValidationError("Die erzeugte Musik konnte nicht gespeichert werden")
            entry["cached_name"] = name
            generated += 1

        return {
            "plan": plan,
            "sections": state,
            "generated": generated,
            "reused": len(state) - generated,
            # What the renderer needs: which file plays when, and how it
            # arrives and leaves. Built here rather than in the renderer
            # so the timeline and the prices come from the same plan.
            "timeline": [
                {
                    "name": entry["cached_name"],
                    "start_seconds": entry["start_seconds"],
                    "seconds": entry["seconds"],
                    "fade_in_seconds": entry["fade_in_seconds"],
                    "fade_out_seconds": entry["fade_out_seconds"],
                }
                for entry in state
                if entry["cached_name"]
            ],
        }

    async def _async_order(self, prompt: str) -> tuple[bytes, str]:
        """One generation. Everything that can cost money passes here."""
        api_key = self._api_key_provider()
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
        return blob, mime_type

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
