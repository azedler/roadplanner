"""Per-stop and trip photo album curation on top of the shared Vision engine.

Owns the auto-curation lock/status lifecycle (background batch runs across a
trip); the actual local-filter/Vision-selection work for one item is
delegated to VisionCurationEngine.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .experience_helpers import _all_days, _day_date, _find_stop, _stops
from .experience_store import ExperienceStore, utc_now_iso
from .manager import RoadplannerManager
from .media_intelligence import select_media_highlights, select_trip_cover_candidates
from .media_vision import selection_fingerprint
from .media_vision_curation import VisionCurationEngine
from .roadplanner import ValidationError

_VISION_BACKGROUND_BATCH = 3


class MediaCurationManager:
    """Curate per-stop and trip-cover photo albums from the user's own photos."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        vision_curation: VisionCurationEngine,
        *,
        get_panel_payload: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self._vision_curation = vision_curation
        self._get_panel_payload = get_panel_payload
        self._vision_lock = asyncio.Lock()
        self._vision_status: dict[str, Any] = {
            "enabled": vision_curation.media_curation_mode == "hybrid",
            "state": "idle",
            "last_run_at": None,
            "last_trip_id": None,
            "processed": 0,
            "curated": 0,
            "fallbacks": 0,
            "error": None,
        }

    @property
    def vision_status(self) -> dict[str, Any]:
        return self._vision_status

    async def async_curate_stop_media(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Curate one stop's locally prefiltered OneDrive photos."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = _find_stop(days, day_id, stop_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [
            item
            for item in list(state.get("media") or [])
            if isinstance(item, dict) and str(item.get("linked_stop_id") or "") == stop_id
        ]
        local_candidates, _stats = select_media_highlights(
            media,
            limit=self._vision_curation.media_vision_max_candidates,
        )
        if not local_candidates:
            raise ValidationError("Für diesen Stopp sind noch keine eigenen Fotos vorhanden")
        curation = await self._vision_curation.async_curate(
            trip_id=trip_id,
            kind="travel",
            day=day,
            stop=stop,
            candidates=local_candidates,
            existing=(state.get("media_curations") or {}).get(stop_id),
            force=force,
        )
        stored = await self.hass.async_add_executor_job(
            self.store.upsert_media_curation,
            trip_id,
            curation,
        )
        return {
            "curation": stored,
            "experience": await self._get_panel_payload(trip_id, days=days),
        }

    async def async_curate_trip_media(
        self,
        trip_id: str,
        *,
        force: bool = False,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        """Curate a bounded trip-cover candidate set from strong own photos."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [
            item
            for item in list(state.get("media") or [])
            if isinstance(item, dict)
        ]
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        existing_trip_curation = (state.get("media_curations") or {}).get("trip-cover")
        candidates = select_trip_cover_candidates(
            media,
            limit=self._vision_curation.media_vision_max_candidates,
            trip_start=trip.get("start_date"),
            trip_end=trip.get("end_date"),
            sticky_cover_id=str(
                (existing_trip_curation or {}).get("cover_id") or ""
            )
            or None,
        )
        if not candidates:
            result: dict[str, Any] = {
                "curation": None,
                "candidate_count": 0,
                "reason": "Keine hinreichend sicher zugeordneten Reisefotos vorhanden",
            }
            if include_experience:
                result["experience"] = await self._get_panel_payload(trip_id)
            return result
        synthetic_day = {
            "id": "trip-cover-day",
            "date": trip.get("start_date"),
            "title": trip.get("title") or "Reise",
        }
        synthetic_stop = {
            "id": "trip-cover",
            "name": trip.get("title") or "Reise",
            "type": "trip",
            "notes": trip.get("notes") or "",
            "location": {},
        }
        curation = await self._vision_curation.async_curate(
            trip_id=trip_id,
            kind="trip",
            day=synthetic_day,
            stop=synthetic_stop,
            candidates=candidates,
            existing=existing_trip_curation,
            force=force,
        )
        stored = await self.hass.async_add_executor_job(
            self.store.upsert_media_curation,
            trip_id,
            curation,
        )
        result = {
            "curation": stored,
            "candidate_count": len(candidates),
        }
        if include_experience:
            result["experience"] = await self._get_panel_payload(trip_id)
        return result

    async def async_auto_curate_media(
        self,
        trip_id: str,
        *,
        limit: int = _VISION_BACKGROUND_BATCH,
        force: bool = False,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        """Curate a bounded number of stop albums without blocking the UI."""
        if not self._vision_curation.vision_enabled:
            result: dict[str, Any] = {"processed": 0, "curated": 0, "fallbacks": 0}
            if include_experience:
                result["experience"] = await self._get_panel_payload(trip_id)
            return result
        if self._vision_lock.locked():
            return {"processed": 0, "curated": 0, "fallbacks": 0, "busy": True}
        async with self._vision_lock:
            payload = await self.manager.async_get_assistant_payload(trip_id)
            days = _all_days(payload)
            state = await self.hass.async_add_executor_job(self.store.load, trip_id)
            media = [item for item in list(state.get("media") or []) if isinstance(item, dict)]
            by_stop: dict[str, list[dict[str, Any]]] = {}
            for item in media:
                stop_id = str(item.get("linked_stop_id") or "")
                if stop_id:
                    by_stop.setdefault(stop_id, []).append(item)
            curations = state.get("media_curations") if isinstance(state.get("media_curations"), dict) else {}
            today = dt_util.now().date()

            def priority(day: dict[str, Any]) -> tuple[int, int, str]:
                value = _day_date(day)
                if value == today:
                    return (0, 0, str(day.get("id") or ""))
                if value and value > today:
                    return (1, value.toordinal(), str(day.get("id") or ""))
                return (2, -(value.toordinal() if value else 0), str(day.get("id") or ""))

            selected: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]] = []
            for day in sorted(days, key=priority):
                for stop in _stops(day):
                    stop_id = str(stop.get("id") or "")
                    items = by_stop.get(stop_id, [])
                    if len(items) < 2:
                        continue
                    candidates, _stats = select_media_highlights(
                        items,
                        limit=self._vision_curation.media_vision_max_candidates,
                    )
                    fingerprint = selection_fingerprint(
                        kind="travel",
                        context=self._vision_curation._vision_context(day, stop),
                        candidates=candidates,
                        model=str(getattr(self._vision_curation.provider, "model", "") or ""),
                    )
                    existing = curations.get(stop_id)
                    if (
                        not force
                        and isinstance(existing, dict)
                        and existing.get("status") == "ready"
                        and str(existing.get("fingerprint") or "") == fingerprint
                    ):
                        continue
                    selected.append((day, stop, candidates))
                    if len(selected) >= max(1, min(int(limit), 10)):
                        break
                if len(selected) >= max(1, min(int(limit), 10)):
                    break

            self._vision_status.update(
                {
                    "state": "running",
                    "last_trip_id": trip_id,
                    "processed": 0,
                    "curated": 0,
                    "fallbacks": 0,
                    "error": None,
                }
            )
            curated = 0
            fallbacks = 0
            for day, stop, candidates in selected:
                stop_id = str(stop.get("id") or "")
                curation = await self._vision_curation.async_curate(
                    trip_id=trip_id,
                    kind="travel",
                    day=day,
                    stop=stop,
                    candidates=candidates,
                    existing=curations.get(stop_id),
                    force=force,
                )
                await self.hass.async_add_executor_job(
                    self.store.upsert_media_curation,
                    trip_id,
                    curation,
                )
                if curation.get("status") == "ready":
                    curated += 1
                else:
                    fallbacks += 1
            trip_cover_processed = 0
            summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
            trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
            trip_candidates = select_trip_cover_candidates(
                media,
                limit=self._vision_curation.media_vision_max_candidates,
                trip_start=trip.get("start_date"),
                trip_end=trip.get("end_date"),
                sticky_cover_id=str(
                    (curations.get("trip-cover") or {}).get("cover_id") or ""
                )
                or None,
            )
            if trip_candidates:
                synthetic_day = {
                    "id": "trip-cover-day",
                    "date": trip.get("start_date"),
                    "title": trip.get("title") or "Reise",
                }
                synthetic_stop = {
                    "id": "trip-cover",
                    "name": trip.get("title") or "Reise",
                    "type": "trip",
                    "notes": trip.get("notes") or "",
                    "location": {},
                }
                trip_curation = await self._vision_curation.async_curate(
                    trip_id=trip_id,
                    kind="trip",
                    day=synthetic_day,
                    stop=synthetic_stop,
                    candidates=trip_candidates,
                    existing=curations.get("trip-cover"),
                    force=force,
                )
                await self.hass.async_add_executor_job(
                    self.store.upsert_media_curation,
                    trip_id,
                    trip_curation,
                )
                trip_cover_processed = 1
                if trip_curation.get("status") == "ready":
                    curated += 1
                elif trip_curation.get("status") not in {"local"}:
                    fallbacks += 1
            total_processed = len(selected) + trip_cover_processed
            self._vision_status.update(
                {
                    "state": "idle",
                    "last_run_at": utc_now_iso(),
                    "last_trip_id": trip_id,
                    "processed": total_processed,
                    "curated": curated,
                    "fallbacks": fallbacks,
                    "trip_cover_processed": bool(trip_cover_processed),
                    "error": None,
                }
            )
            result = {
                "processed": total_processed,
                "curated": curated,
                "fallbacks": fallbacks,
                "trip_cover_processed": bool(trip_cover_processed),
            }
            if include_experience:
                result["experience"] = await self._get_panel_payload(trip_id, days=days)
            return result

