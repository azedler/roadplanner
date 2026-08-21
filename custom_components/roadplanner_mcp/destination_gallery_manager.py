"""AI-assisted planning-photo galleries per stop, with background auto-population.

Owns the destination-enrichment background-job lifecycle (periodic + initial
scheduling, lock/status); the shared local-filter/Vision-selection work is
delegated to VisionCurationEngine.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import EVENT_ROADPLANNER_UPDATED
from .destination_images import DestinationImageProvider
from .destination_intelligence import analyze_destination, destination_image_query
from .experience_helpers import _all_days, _day_date, _find_stop, _parse_datetime, _stops
from .experience_store import ExperienceStore, utc_now_iso
from .manager import RoadplannerManager
from .media_vision_curation import VisionCurationEngine
from .page_images import async_images_from_source_hints
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

_DESTINATION_GALLERY_SIZE = 3
_DESTINATION_AUTO_BATCH = 6
_DESTINATION_EMPTY_RETRY_SECONDS = 6 * 60 * 60
_DESTINATION_BACKGROUND_INTERVAL_MINUTES = 30
_DESTINATION_INITIAL_DELAY_SECONDS = 45
_DESTINATION_BACKGROUND_BATCH = 4


def _strip_ephemeral_google_url(image: dict[str, Any]) -> dict[str, Any]:
    """Drop a Google-sourced image's short-lived URL before persisting it.

    Google Places Photo URLs are not guaranteed to stay valid long-term, and
    Google's terms do not allow storing the photo itself. Only the durable
    `photo_name` reference survives to disk; `panel_payload_builder.py`
    rebuilds a fresh, signed redirect URL from it on every payload send.
    """
    if not isinstance(image, dict) or image.get("provider") != "google_places":
        return image
    if not str(image.get("photo_name") or ""):
        return image
    result = dict(image)
    result["image_url"] = None
    result["thumbnail_url"] = None
    result["original_url"] = None
    return result


class DestinationGalleryManager:
    """Build, refresh and background-populate per-stop planning-photo galleries."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        image_provider: DestinationImageProvider,
        vision_curation: VisionCurationEngine,
        *,
        get_panel_payload: Callable[[str], Awaitable[dict[str, Any]]],
        trigger_vision_curation: Callable[[str], Awaitable[None]],
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.image_provider = image_provider
        self._vision_curation = vision_curation
        self._get_panel_payload = get_panel_payload
        self._trigger_vision_curation = trigger_vision_curation
        self._destination_enrichment_lock = asyncio.Lock()
        self._unsub_destination_interval: Any = None
        self._unsub_destination_start: Any = None
        self._destination_enrichment_status: dict[str, Any] = {
            "enabled": True,
            "state": "idle",
            "last_run_at": None,
            "last_trip_id": None,
            "searched": 0,
            "updated": 0,
            "error": None,
            "interval_minutes": _DESTINATION_BACKGROUND_INTERVAL_MINUTES,
        }

    @property
    def status(self) -> dict[str, Any]:
        return self._destination_enrichment_status

    async def async_initialize(self) -> None:
        self._reschedule_destination_enrichment()

    async def async_shutdown(self) -> None:
        if self._unsub_destination_interval:
            self._unsub_destination_interval()
            self._unsub_destination_interval = None
        if self._unsub_destination_start:
            self._unsub_destination_start()
            self._unsub_destination_start = None

    def _reschedule_destination_enrichment(self) -> None:
        """Schedule bounded background planning-image enrichment."""
        if self._unsub_destination_interval:
            self._unsub_destination_interval()
            self._unsub_destination_interval = None
        if self._unsub_destination_start:
            self._unsub_destination_start()
            self._unsub_destination_start = None
        self._unsub_destination_interval = async_track_time_interval(
            self.hass,
            self._periodic_destination_enrichment,
            timedelta(minutes=_DESTINATION_BACKGROUND_INTERVAL_MINUTES),
        )
        self._unsub_destination_start = async_call_later(
            self.hass,
            _DESTINATION_INITIAL_DELAY_SECONDS,
            self._initial_destination_enrichment,
        )

    @callback
    def _initial_destination_enrichment(self, _now: datetime) -> None:
        self._unsub_destination_start = None
        self.hass.async_create_task(self._async_periodic_destination_enrichment())

    @callback
    def _periodic_destination_enrichment(self, _now: datetime) -> None:
        self.hass.async_create_task(self._async_periodic_destination_enrichment())

    async def _async_periodic_destination_enrichment(self) -> None:
        if self._destination_enrichment_lock.locked():
            return
        async with self._destination_enrichment_lock:
            trips = await self.manager.async_list_trips()
            active_trip = (
                str(trips.get("active_trip") or "")
                if isinstance(trips, dict)
                else ""
            )
            if not active_trip:
                return
            self._destination_enrichment_status.update(
                {
                    "state": "running",
                    "last_trip_id": active_trip,
                    "error": None,
                }
            )
            try:
                await self._trigger_vision_curation(active_trip)
                result = await self.async_auto_populate_destination_galleries(
                    active_trip,
                    limit=_DESTINATION_BACKGROUND_BATCH,
                    include_experience=False,
                )
            except (RoadplannerError, asyncio.TimeoutError) as err:
                self._destination_enrichment_status.update(
                    {
                        "state": "error",
                        "last_run_at": utc_now_iso(),
                        "searched": 0,
                        "updated": 0,
                        "error": str(err)[:500],
                    }
                )
                _LOGGER.debug("Background destination image enrichment failed: %s", err)
                return
            except Exception as err:  # noqa: BLE001 - background tasks must fail closed
                self._destination_enrichment_status.update(
                    {
                        "state": "error",
                        "last_run_at": utc_now_iso(),
                        "searched": 0,
                        "updated": 0,
                        "error": type(err).__name__,
                    }
                )
                _LOGGER.exception("Unexpected destination image enrichment failure")
                return
            self._destination_enrichment_status.update(
                {
                    "state": "idle",
                    "last_run_at": utc_now_iso(),
                    "searched": int(result.get("searched") or 0),
                    "updated": int(result.get("updated") or 0),
                    "error": None,
                }
            )
            if int(result.get("updated") or 0):
                self.hass.bus.async_fire(
                    EVENT_ROADPLANNER_UPDATED,
                    {
                        "experience_changed": True,
                        "source": "destination_image_enrichment",
                    },
                )

    @staticmethod
    def _destination_query(day: dict[str, Any], stop: dict[str, Any]) -> str:
        """Return a concise image query based on the stop identity/profile."""
        intent = analyze_destination(day, stop)
        return destination_image_query(day, stop, intent=intent)

    @staticmethod
    def _destination_query_fingerprint(
        day: dict[str, Any],
        stop: dict[str, Any],
        query: str,
    ) -> str:
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        value = {
            "day_id": day.get("id"),
            "stop_id": stop.get("id"),
            "query": query,
            "latitude": location.get("latitude", location.get("lat")),
            "longitude": location.get("longitude", location.get("lon", location.get("lng"))),
        }
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()

    async def _destination_gallery_for_stop(
        self,
        trip_id: str,
        day: dict[str, Any],
        stop: dict[str, Any],
        *,
        existing_gallery: dict[str, Any] | None = None,
        force_vision: bool = False,
    ) -> dict[str, Any]:
        """Build a locally ranked gallery, optionally semantically curated."""
        intent = analyze_destination(day, stop)
        query = destination_image_query(day, stop, intent=intent)
        if not query:
            raise ValidationError("Für diesen Stopp fehlen Angaben für die Bildsuche")
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        search_limit = (
            self._vision_curation.media_vision_max_candidates
            if self._vision_curation.vision_enabled
            else _DESTINATION_GALLERY_SIZE
        )
        # Photos from a page the user shared for this stop (Park4Night,
        # naturkartan, campsite website ...) come FIRST - they show the
        # actual place, not a lookalike from a generic search.
        shared_images = await async_images_from_source_hints(
            self.hass, intent.source_hints
        )
        result = await self.image_provider.async_search(
            query,
            limit=search_limit,
            latitude=location.get("latitude", location.get("lat")),
            longitude=location.get("longitude", location.get("lon", location.get("lng"))),
        )
        seen_urls = {
            str(image.get("image_url") or "") for image in shared_images
        }
        candidates = shared_images + [
            item
            for item in list(result.get("results") or [])
            if str(item.get("image_url") or "") not in seen_urls
        ]
        candidates = candidates[: max(search_limit, len(shared_images))]
        errors = dict(result.get("provider_errors") or {})
        existing_curation = (
            existing_gallery.get("curation")
            if isinstance(existing_gallery, dict)
            and isinstance(existing_gallery.get("curation"), dict)
            else None
        )
        curation: dict[str, Any] = {}
        images = candidates[:_DESTINATION_GALLERY_SIZE]
        if candidates:
            curation = await self._vision_curation.async_curate(
                trip_id=trip_id,
                kind="planning",
                day=day,
                stop=stop,
                candidates=candidates,
                existing=existing_curation,
                force=force_vision,
            )
            by_id = {
                str(item.get("id") or ""): item
                for item in candidates
                if str(item.get("id") or "")
            }
            ordered_ids = [
                str(item)
                for item in list(curation.get("highlight_ids") or [])
                if str(item) in by_id
            ]
            for item in candidates:
                image_id = str(item.get("id") or "")
                if image_id and image_id not in ordered_ids:
                    ordered_ids.append(image_id)
            images = [
                _strip_ephemeral_google_url(deepcopy(by_id[item]))
                for item in ordered_ids[:_DESTINATION_GALLERY_SIZE]
            ]
        if images and errors:
            status = "partial"
        elif images:
            status = "ready"
        elif errors:
            status = "error"
        else:
            status = "empty"
        primary = str(curation.get("cover_id") or "")
        if not any(str(item.get("id") or "") == primary for item in images):
            primary = str(images[0].get("id") or "") if images else ""
        return {
            "stop_id": str(stop.get("id") or ""),
            "day_id": str(day.get("id") or ""),
            "query": query,
            "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
            "status": status,
            "images": images,
            "primary_image_id": primary or None,
            "provider_errors": errors,
            "curation": curation,
            "attempted_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }

    async def async_refresh_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = _find_stop(days, day_id, stop_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        gallery = await self._destination_gallery_for_stop(
            trip_id,
            day,
            stop,
            existing_gallery=(state.get("destination_galleries") or {}).get(stop_id),
            force_vision=True,
        )
        await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            [gallery],
        )
        return {
            "gallery": gallery,
            "experience": await self._get_panel_payload(trip_id),
        }

    async def async_save_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
        images: list[dict[str, Any]],
        primary_image_id: str | None,
    ) -> dict[str, Any]:
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        day, stop = _find_stop(days, day_id, stop_id)
        query = self._destination_query(day, stop)
        selected_images = [
            _strip_ephemeral_google_url(item)
            for item in list(images or [])[:_DESTINATION_GALLERY_SIZE]
        ]
        selected_ids = [
            str(item.get("id") or "")
            for item in selected_images
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
        if primary_image_id and primary_image_id in selected_ids:
            selected_ids = [primary_image_id, *[item for item in selected_ids if item != primary_image_id]]
        gallery = {
            "stop_id": stop_id,
            "day_id": day_id,
            "query": query,
            "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
            "status": "ready" if selected_images else "empty",
            "images": selected_images,
            "primary_image_id": primary_image_id,
            "provider_errors": {},
            "curation": {
                "stop_id": stop_id,
                "kind": "planning",
                "status": "ready",
                "mode": "manual",
                "model": None,
                "candidate_ids": selected_ids,
                "cover_id": primary_image_id if primary_image_id in selected_ids else (selected_ids[0] if selected_ids else None),
                "highlight_ids": selected_ids,
                "rejected_ids": [],
                "reasons": {},
                "summary": "Vom Benutzer ausgewählte Planungsbilder.",
                "selected_at": utc_now_iso(),
                "error": None,
            },
            "attempted_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
        }
        await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            [gallery],
        )
        stored = (
            await self.hass.async_add_executor_job(self.store.load, trip_id)
        ).get("destination_galleries", {}).get(stop_id)
        return {
            "gallery": stored,
            "experience": await self._get_panel_payload(trip_id),
        }

    async def async_delete_destination_gallery(
        self,
        trip_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        await self.hass.async_add_executor_job(
            self.store.delete_destination_gallery,
            trip_id,
            stop_id,
        )
        return {
            "ok": True,
            "experience": await self._get_panel_payload(trip_id),
        }

    async def async_auto_populate_destination_galleries(
        self,
        trip_id: str,
        *,
        limit: int = _DESTINATION_AUTO_BATCH,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        """Populate missing planning galleries without replacing own travel photos."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        existing = dict(state.get("destination_galleries") or {})
        own_media_stop_ids = {
            str(item.get("linked_stop_id") or "")
            for item in list(state.get("media") or [])
            if isinstance(item, dict) and str(item.get("linked_stop_id") or "")
        }
        today = dt_util.now().date()

        def day_priority(day: dict[str, Any]) -> tuple[int, int, str]:
            value = _day_date(day)
            if value is None:
                return (3, 0, str(day.get("id") or ""))
            if value == today:
                return (0, value.toordinal(), str(day.get("id") or ""))
            if value > today:
                return (1, value.toordinal(), str(day.get("id") or ""))
            return (2, -value.toordinal(), str(day.get("id") or ""))

        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        now = dt_util.now()
        batch_limit = max(1, min(int(limit), 12))
        for day in sorted(days, key=day_priority):
            for stop in _stops(day):
                stop_id = str(stop.get("id") or "")
                if not stop_id or stop_id in own_media_stop_ids:
                    continue
                query = self._destination_query(day, stop)
                fingerprint = self._destination_query_fingerprint(day, stop, query)
                gallery = existing.get(stop_id)
                if isinstance(gallery, dict) and gallery.get("query_fingerprint") == fingerprint:
                    gallery_curation = (
                        gallery.get("curation")
                        if isinstance(gallery.get("curation"), dict)
                        else {}
                    )
                    needs_vision_refresh = bool(
                        gallery.get("images")
                        and self._vision_curation.vision_enabled
                        and not (
                            gallery_curation.get("status") == "ready"
                            and gallery_curation.get("mode") in {"hybrid_vision", "manual"}
                        )
                    )
                    if gallery.get("images") and not needs_vision_refresh:
                        continue
                    attempted = _parse_datetime(gallery.get("attempted_at"))
                    if (
                        not needs_vision_refresh
                        and attempted
                        and (now - attempted).total_seconds() < _DESTINATION_EMPTY_RETRY_SECONDS
                    ):
                        continue
                candidates.append((day, stop))
                if len(candidates) >= batch_limit:
                    break
            if len(candidates) >= batch_limit:
                break
        if not candidates:
            result: dict[str, Any] = {
                "searched": 0,
                "updated": 0,
            }
            if include_experience:
                result["experience"] = await self._get_panel_payload(trip_id)
            return result

        semaphore = asyncio.Semaphore(3)

        async def build(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self._destination_gallery_for_stop(
                        trip_id,
                        day,
                        stop,
                        existing_gallery=existing.get(str(stop.get("id") or "")),
                    )
                except asyncio.CancelledError:
                    raise
                except RoadplannerError as err:
                    query = self._destination_query(day, stop)
                    # A failed REFRESH must not delete what already
                    # worked. This returned an empty list, and the empty
                    # list was stored - so a stop that had planning
                    # pictures lost them the moment a provider was
                    # unreachable for a minute, and the day's cover went
                    # with them. The dialog promises the opposite in the
                    # same breath ("Die Stoppdaten bleiben vollständig
                    # erhalten"), which is what made it look like data
                    # loss rather than a failed fetch.
                    #
                    # The pictures stay, the error is reported beside
                    # them, and "partial" is the status that already
                    # means exactly that: something to show, and
                    # something that did not answer.
                    previous = existing.get(str(stop.get("id") or "")) or {}
                    kept = previous.get("images") if isinstance(previous, dict) else None
                    kept = kept if isinstance(kept, list) else []
                    return {
                        "stop_id": str(stop.get("id") or ""),
                        "day_id": str(day.get("id") or ""),
                        "query": query,
                        "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
                        "status": "partial" if kept else "error",
                        "images": deepcopy(kept),
                        "primary_image_id": (
                            previous.get("primary_image_id") if kept else None
                        ),
                        "provider_errors": {"roadplanner": str(err)[:500]},
                        "curation": deepcopy(previous.get("curation") or {}) if kept else {},
                        "attempted_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                    }

        galleries = await asyncio.gather(
            *(build(day, stop) for day, stop in candidates)
        )
        result = await self.hass.async_add_executor_job(
            self.store.upsert_destination_galleries,
            trip_id,
            list(galleries),
        )
        response: dict[str, Any] = {
            "searched": len(candidates),
            "updated": int(result.get("updated") or 0),
        }
        if include_experience:
            response["experience"] = await self._get_panel_payload(trip_id)
        return response
