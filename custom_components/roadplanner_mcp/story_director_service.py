"""Run the editing pass: read the trip, ask Gemini, keep the answer.

This is the only place that connects the pure director module to a real
provider and a real store. It exists so that the rules in
``story_director.py`` stay testable without a network and the caching
rules stay in one place instead of being re-decided at every call site.

The shape of a run
------------------

Two stages, and the split is the reason the result reads like one trip
rather than twenty-three unrelated diary entries:

1. **The arc.** One call with every day in view. It decides what the trip
   was about and how much each day is worth - a judgement that is a
   comparison, and comparisons need the alternatives present.
2. **The chapters.** The days in batches, each batch carrying the arc as
   context. Six per call: enough that the model can avoid repeating
   itself within a batch, few enough that the answer fits an output
   budget and one bad call costs six days rather than a whole trip.

A three-week trip is therefore five calls, not twenty-four.

What a failure does
-------------------

Nothing catastrophic, on purpose. If the arc call fails, the run stops
and the trip keeps the sentences it already had. If one chapter batch
fails, the other batches still land - a partly edited trip is better than
an abandoned one, and the manifest marks every chapter with its own
source, so the difference is visible rather than hidden.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .roadplanner import RoadplannerError, ValidationError
from .story_director import (
    ARC_SCHEMA,
    ARC_SYSTEM_PROMPT,
    CHAPTERS_SCHEMA,
    CHAPTER_SYSTEM_PROMPT,
    build_direction,
    chapter_batches,
    chapter_brief,
    merge_arc,
    merge_chapters,
    trip_brief,
)

_LOGGER = logging.getLogger(__name__)

# A trip large enough to make the cost worth thinking about twice. Past
# this the arc call alone stops fitting comfortably, and nobody has a
# hundred-day trip in Roadplanner yet - so this is a guard, not a policy.
MAX_DIRECTED_CHAPTERS = 60

ARC_MAX_TOKENS = 3000
CHAPTERS_MAX_TOKENS = 4000
# Warm rather than wild. The tone asked for is playful, and at 0.2 the
# model writes the same four sentences it wrote for the template.
TEMPERATURE = 0.55


def _as_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)


class StoryDirectorService:
    """Edit one trip's story, once per version of its material."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        provider: Any,
        story_context: Any,
        store: Any,
        crew: Any = None,
    ) -> None:
        self._hass = hass
        self._provider = provider
        self._story_context = story_context
        self._store = store
        self._crew = crew

    @property
    def available(self) -> bool:
        return bool(self._provider is not None and getattr(self._provider, "configured", False))

    async def async_status(self, trip_id: str) -> dict[str, Any]:
        """What the editor has done for this trip, without doing anything.

        Called whenever the panel opens, so it must not cost a single
        token. It compares the stored edit's context hash against the
        manifest's - which is exactly the check the run itself does, and
        the reason the panel can honestly say "up to date".
        """
        manifest = await self._story_context.async_manifest(trip_id)
        stored = await self._hass.async_add_executor_job(self._store.load, trip_id)
        context_hash = str(manifest.get("story_context_hash") or "")
        return {
            "available": self.available,
            "has_direction": bool(stored),
            "current": bool(stored and stored.get("story_context_hash") == context_hash),
            "directed_chapters": len(stored.get("chapters") or {}) if stored else 0,
            "has_narrative": bool((stored or {}).get("narrative")),
            "model": (stored or {}).get("model") or "",
            "calls": (stored or {}).get("calls") or 0,
            "chapter_count": len(manifest.get("chapters") or []),
            "story_sources": manifest.get("story_sources") or {},
        }

    async def async_run(self, trip_id: str, *, force: bool = False) -> dict[str, Any]:
        """Edit the trip. Reuses the stored pass when nothing has moved."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Reiseredaktion fehlt die Reise-ID")
        if not self.available:
            raise ValidationError(
                "Für die Reiseredaktion ist kein Gemini-Zugang eingerichtet"
            )

        manifest = await self._story_context.async_manifest(trip_id)
        chapters = list(manifest.get("chapters") or [])
        if not chapters:
            raise ValidationError("Diese Reise hat noch keine Kapitel")
        if len(chapters) > MAX_DIRECTED_CHAPTERS:
            raise ValidationError(
                f"Diese Reise hat {len(chapters)} Kapitel; die Redaktion ist auf "
                f"{MAX_DIRECTED_CHAPTERS} begrenzt"
            )

        context_hash = str(manifest.get("story_context_hash") or "")
        stored = await self._hass.async_add_executor_job(self._store.load, trip_id)
        if not force and stored and stored.get("story_context_hash") == context_hash:
            # The material has not moved, so neither would the answer.
            return {
                "reused": True,
                "calls": 0,
                "directed_chapters": len(stored.get("chapters") or {}),
                "story_context_hash": context_hash,
            }

        known_ids = {str(chapter.get("chapter_id") or "") for chapter in chapters}
        known_ids.discard("")
        stop_ids_by_chapter = {
            str(chapter.get("chapter_id") or ""): {
                str(stop.get("stop_id") or "")
                for stop in chapter.get("stops") or []
                if str(stop.get("stop_id") or "")
            }
            for chapter in chapters
        }

        calls = 0
        arc_payload = {
            "reise": trip_brief(manifest),
            "tage": [chapter_brief(chapter) for chapter in chapters],
        }
        try:
            arc_answer = await self._provider.async_generate_json_result(
                system_instruction=ARC_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": _as_text(arc_payload)}],
                schema=ARC_SCHEMA,
                enable_search=False,
                max_output_tokens=ARC_MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except RoadplannerError as err:
            _LOGGER.debug("Reisebogen fehlgeschlagen: %s", type(err).__name__)
            raise ValidationError(
                "Der Reisebogen konnte nicht erzeugt werden. Die vorhandenen Texte "
                "bleiben unverändert."
            ) from err
        calls += 1
        arc = merge_arc(getattr(arc_answer, "data", arc_answer), known_ids=known_ids)

        edited: dict[str, dict[str, Any]] = {}
        failed_batches = 0
        for batch in chapter_batches(chapters):
            payload = {
                "reisebogen": arc["narrative"],
                "rollen": {
                    str(chapter.get("chapter_id")): arc["weights"].get(
                        str(chapter.get("chapter_id")), {}
                    )
                    for chapter in batch
                },
                "tage": [chapter_brief(chapter) for chapter in batch],
            }
            try:
                answer = await self._provider.async_generate_json_result(
                    system_instruction=CHAPTER_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": _as_text(payload)}],
                    schema=CHAPTERS_SCHEMA,
                    enable_search=False,
                    max_output_tokens=CHAPTERS_MAX_TOKENS,
                    temperature=TEMPERATURE,
                )
            except RoadplannerError as err:
                # One lost batch is a few days without an edited text, and
                # the manifest says so per chapter. Losing the whole run
                # over it would throw away the calls that succeeded.
                failed_batches += 1
                _LOGGER.debug("Kapitelredaktion fehlgeschlagen: %s", type(err).__name__)
                continue
            calls += 1
            edited.update(
                merge_chapters(
                    getattr(answer, "data", answer),
                    known_ids=known_ids,
                    stop_ids_by_chapter=stop_ids_by_chapter,
                )
            )

        direction = build_direction(
            context_hash=context_hash,
            narrative=arc["narrative"],
            weights=arc["weights"],
            chapters=edited,
            model=str(getattr(self._provider, "model", "") or ""),
            calls=calls,
        )
        await self._hass.async_add_executor_job(self._store.write, trip_id, direction)
        # The builder is holding a manifest without this edit in it.
        self._story_context.invalidate(trip_id)
        _LOGGER.debug(
            "Reiseredaktion für %s: %s Aufrufe, %s Kapitel redigiert",
            trip_id,
            calls,
            len(edited),
        )
        return {
            "reused": False,
            "calls": calls,
            "directed_chapters": len(edited),
            "failed_batches": failed_batches,
            "story_context_hash": context_hash,
        }

    async def async_discard(self, trip_id: str) -> dict[str, Any]:
        """Throw the edit away and fall back to the automatic version."""
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Reiseredaktion fehlt die Reise-ID")
        removed = await self._hass.async_add_executor_job(self._store.delete, trip_id)
        self._story_context.invalidate(trip_id)
        return {"removed": bool(removed)}

    async def async_crew_profile(self) -> dict[str, Any] | None:
        """Display names and a vehicle name. Nothing else leaves the file.

        A story that never names anybody reads like a brochure, so this is
        worth having - but a crew record also holds notes, portraits and
        generated person summaries, and none of that has any business in
        a prompt. Copying the record wholesale would have been the easy
        version of this function and the wrong one.
        """
        if self._crew is None:
            return None
        try:
            registry = await self._crew.async_panel_payload()
        except (RoadplannerError, AttributeError, TypeError):
            return None
        people = [
            str(person.get("name") or "").strip()
            for person in registry.get("people") or []
            if isinstance(person, dict) and person.get("active", True)
        ]
        vehicles = [
            str(vehicle.get("name") or "").strip()
            for vehicle in registry.get("vehicles") or []
            if isinstance(vehicle, dict) and vehicle.get("active", True)
        ]
        people = [name for name in people if name]
        vehicles = [name for name in vehicles if name]
        if not people and not vehicles:
            return None
        return {"people": people, "vehicle": vehicles[0] if vehicles else ""}


__all__ = ["MAX_DIRECTED_CHAPTERS", "StoryDirectorService"]
