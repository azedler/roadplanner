"""Decision slides and OneDrive media albums for Roadplanner."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import logging
from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .assistant_provider import AssistantProvider
from .const import EVENT_ROADPLANNER_UPDATED
from .decision_manager import DecisionManager
from .destination_images import DestinationImageProvider
from .destination_intelligence import analyze_destination, destination_image_query
from .experience_helpers import (
    _all_days,
    _day_date,
    _parse_datetime,
    _stops,
)
from .media_intelligence import (
    build_media_presentation,
    select_media_highlights,
    select_trip_cover_candidates,
)
from .experience_store import (
    ExperienceStore,
    resolve_decision_media_references,
    utc_now_iso,
)
from .geocoding import GeocodingProvider
from .manager import RoadplannerManager
from .media_library_manager import (
    _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    MediaLibraryManager,
)
from .media_token_service import MediaTokenService
from .media_vision_curation import VisionCurationEngine
from .onedrive_media import OneDrivePersonalClient
from .place_cleanup import PlaceCleanupService
from .place_enrichment import PlaceEnrichmentService
from .place_enrichment_orchestrator import PlaceEnrichmentOrchestrator
from .media_vision import selection_fingerprint
from .roadplanner import RoadplannerError, ValidationError
from .routing import OSRMRoutingClient

_LOGGER = logging.getLogger(__name__)

_DESTINATION_GALLERY_SIZE = 3
_DESTINATION_AUTO_BATCH = 6
_DESTINATION_EMPTY_RETRY_SECONDS = 6 * 60 * 60
_DESTINATION_BACKGROUND_INTERVAL_MINUTES = 30
_DESTINATION_INITIAL_DELAY_SECONDS = 45
_DESTINATION_BACKGROUND_BATCH = 4
_VISION_BACKGROUND_BATCH = 3





class RoadplannerExperienceManager:
    """Coordinate decision cards and OneDrive Personal albums."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        onedrive: OneDrivePersonalClient,
        *,
        provider: AssistantProvider | None,
        assistant: Any,
        geocoder: GeocodingProvider | None,
        router: OSRMRoutingClient,
        image_provider: DestinationImageProvider,
        media_curation_mode: str = "local",
        media_vision_max_candidates: int = 12,
        media_vision_max_highlights: int = 5,
        media_vision_daily_limit: int = 5,
        folder_path: str,
        sync_interval_minutes: int,
        auto_sync: bool,
        auto_assign: bool,
        recursive_subfolders: bool = True,
        date_buffer_days: int = 3,
        max_items_per_run: int = 2000,
        max_scan_seconds: int = _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.onedrive = onedrive
        self.provider = provider
        self.assistant = assistant
        self.geocoder = geocoder
        self.router = router
        self.image_provider = image_provider
        self._vision_curation = VisionCurationEngine(
            hass,
            store,
            onedrive,
            provider,
            media_curation_mode=media_curation_mode,
            media_vision_max_candidates=media_vision_max_candidates,
            media_vision_max_highlights=media_vision_max_highlights,
            media_vision_daily_limit=media_vision_daily_limit,
        )
        self.place_enrichment = (
            PlaceEnrichmentService(
                geocoder,
                image_provider,
                cleanup_service=PlaceCleanupService(provider),
            )
            if geocoder is not None and geocoder.enabled
            else None
        )
        self._destination_enrichment_lock = asyncio.Lock()
        self._vision_lock = asyncio.Lock()
        self._unsub_destination_interval: Any = None
        self._unsub_destination_start: Any = None
        self._vision_status: dict[str, Any] = {
            "enabled": self._vision_curation.media_curation_mode == "hybrid",
            "state": "idle",
            "last_run_at": None,
            "last_trip_id": None,
            "processed": 0,
            "curated": 0,
            "fallbacks": 0,
            "error": None,
        }
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
        self._media_tokens = MediaTokenService(hass=hass, store=store, onedrive=onedrive)
        self._media_library = MediaLibraryManager(
            hass,
            store,
            manager,
            onedrive,
            folder_path=folder_path,
            sync_interval_minutes=sync_interval_minutes,
            auto_sync=auto_sync,
            auto_assign=auto_assign,
            recursive_subfolders=recursive_subfolders,
            date_buffer_days=date_buffer_days,
            max_items_per_run=max_items_per_run,
            max_scan_seconds=max_scan_seconds,
            on_media_changed=self._on_media_changed,
        )
        self._decisions = DecisionManager(
            hass,
            store,
            manager,
            assistant=assistant,
            provider=provider,
            geocoder=geocoder,
            router=router,
            image_provider=image_provider,
            get_panel_payload=self.async_panel_payload,
        )
        self._place_enrichment_orchestrator = PlaceEnrichmentOrchestrator(
            hass,
            store,
            manager,
            self.place_enrichment,
            get_panel_payload=self.async_panel_payload,
        )

    async def async_initialize(self) -> None:
        await self._media_library.async_initialize()
        self._reschedule_destination_enrichment()

    async def async_shutdown(self) -> None:
        await self._media_library.async_shutdown()
        if self._unsub_destination_interval:
            self._unsub_destination_interval()
            self._unsub_destination_interval = None
        if self._unsub_destination_start:
            self._unsub_destination_start()
            self._unsub_destination_start = None

    @property
    def folder_path(self) -> str:
        return self._media_library.folder_path

    @property
    def auto_sync(self) -> bool:
        return self._media_library.auto_sync

    @property
    def auto_assign(self) -> bool:
        return self._media_library.auto_assign

    @property
    def sync_interval_minutes(self) -> int:
        return self._media_library.sync_interval_minutes

    @property
    def recursive_subfolders(self) -> bool:
        return self._media_library.recursive_subfolders

    @property
    def date_buffer_days(self) -> int:
        return self._media_library.date_buffer_days

    @property
    def max_items_per_run(self) -> int:
        return self._media_library.max_items_per_run

    @property
    def max_scan_seconds(self) -> int:
        return self._media_library.max_scan_seconds

    @property
    def media_curation_mode(self) -> str:
        return self._vision_curation.media_curation_mode

    @property
    def vision_enabled(self) -> bool:
        return self._vision_curation.vision_enabled

    @property
    def media_vision_max_candidates(self) -> int:
        return self._vision_curation.media_vision_max_candidates

    @property
    def media_vision_max_highlights(self) -> int:
        return self._vision_curation.media_vision_max_highlights

    @property
    def media_vision_daily_limit(self) -> int:
        return self._vision_curation.media_vision_daily_limit

    async def _on_media_changed(self, trip_id: str) -> None:
        """Trigger optional Vision curation after new/changed synced media."""
        if not self.vision_enabled:
            return
        await self.async_auto_curate_media(
            trip_id,
            limit=_VISION_BACKGROUND_BATCH,
            include_experience=False,
        )

    async def async_reconfigure_onedrive(
        self,
        *,
        client_id: str,
        folder_path: str,
        auto_sync: bool,
        auto_assign: bool,
        sync_interval_minutes: int,
        recursive_subfolders: bool = True,
        date_buffer_days: int = 3,
        max_items_per_run: int = 2000,
        max_scan_seconds: int = _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    ) -> dict[str, Any]:
        return await self._media_library.async_reconfigure_onedrive(
            client_id=client_id,
            folder_path=folder_path,
            auto_sync=auto_sync,
            auto_assign=auto_assign,
            sync_interval_minutes=sync_interval_minutes,
            recursive_subfolders=recursive_subfolders,
            date_buffer_days=date_buffer_days,
            max_items_per_run=max_items_per_run,
            max_scan_seconds=max_scan_seconds,
        )

    async def async_start_onedrive_auth(self) -> dict[str, Any]:
        return await self._media_library.async_start_onedrive_auth()

    async def async_poll_onedrive_auth(self) -> dict[str, Any]:
        return await self._media_library.async_poll_onedrive_auth()

    async def async_disconnect_onedrive(self) -> dict[str, Any]:
        return await self._media_library.async_disconnect_onedrive()

    async def async_sync_trip(
        self, trip_id: str, *, full_rescan: bool = False
    ) -> dict[str, Any]:
        return await self._media_library.async_sync_trip(trip_id, full_rescan=full_rescan)

    async def async_sync_all_trips(self) -> dict[str, Any]:
        return await self._media_library.async_sync_all_trips()

    async def async_update_media(self, trip_id: str, media_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return await self._media_library.async_update_media(trip_id, media_id, patch)

    async def async_delete_media(self, trip_id: str, media_id: str) -> dict[str, Any]:
        return await self._media_library.async_delete_media(trip_id, media_id)

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
                if self.vision_enabled:
                    await self.async_auto_curate_media(
                        active_trip,
                        limit=_VISION_BACKGROUND_BATCH,
                        include_experience=False,
                    )
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



    def validate_token(self, trip_id: str, media_id: str, kind: str, token: str) -> bool:
        return self._media_tokens.validate_token(trip_id, media_id, kind, token)

    async def async_media_redirect_url(self, trip_id: str, media_id: str, kind: str) -> str:
        return await self._media_tokens.async_media_redirect_url(trip_id, media_id, kind)

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
        day, stop = self._find_stop(days, day_id, stop_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [
            item
            for item in list(state.get("media") or [])
            if isinstance(item, dict) and str(item.get("linked_stop_id") or "") == stop_id
        ]
        local_candidates, _stats = select_media_highlights(
            media,
            limit=self.media_vision_max_candidates,
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
            "experience": await self.async_panel_payload(trip_id, days=days),
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
        candidates = select_trip_cover_candidates(
            media,
            limit=self.media_vision_max_candidates,
        )
        if not candidates:
            result: dict[str, Any] = {
                "curation": None,
                "candidate_count": 0,
                "reason": "Keine hinreichend sicher zugeordneten Reisefotos vorhanden",
            }
            if include_experience:
                result["experience"] = await self.async_panel_payload(trip_id)
            return result
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
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
            existing=(state.get("media_curations") or {}).get("trip-cover"),
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
            result["experience"] = await self.async_panel_payload(trip_id)
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
        if not self.vision_enabled:
            result: dict[str, Any] = {"processed": 0, "curated": 0, "fallbacks": 0}
            if include_experience:
                result["experience"] = await self.async_panel_payload(trip_id)
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
                        limit=self.media_vision_max_candidates,
                    )
                    fingerprint = selection_fingerprint(
                        kind="travel",
                        context=self._vision_context(day, stop),
                        candidates=candidates,
                        model=str(getattr(self.provider, "model", "") or ""),
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
            trip_candidates = select_trip_cover_candidates(
                media,
                limit=self.media_vision_max_candidates,
            )
            if trip_candidates:
                summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
                trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
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
                result["experience"] = await self.async_panel_payload(trip_id, days=days)
            return result

    async def async_panel_payload(self, trip_id: str, *, days: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not trip_id:
            return {
                "decisions": [],
                "media": [],
                "destination_galleries": {},
                "stats": {},
                "by_day": {},
                "by_stop": {},
                "vision": deepcopy(self._vision_status),
                "place_providers": self._place_provider_status(),
                "onedrive": self.onedrive.status(),
            }
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media: list[dict[str, Any]] = []
        by_day: dict[str, list[str]] = {}
        by_stop: dict[str, list[str]] = {}
        for raw in state["media"]:
            item = deepcopy(raw)
            media_id = str(item["id"])
            item["thumbnail_url"] = f"/api/roadplanner/media/thumbnail/{quote(trip_id, safe='')}/{quote(media_id, safe='')}?token={self._media_tokens.token(trip_id, media_id, 'thumbnail')}"
            item["original_url"] = f"/api/roadplanner/media/original/{quote(trip_id, safe='')}/{quote(media_id, safe='')}?token={self._media_tokens.token(trip_id, media_id, 'original')}"
            media.append(item)
            if item.get("linked_day_id"):
                by_day.setdefault(str(item["linked_day_id"]), []).append(media_id)
            if item.get("linked_stop_id"):
                by_stop.setdefault(str(item["linked_stop_id"]), []).append(media_id)
        decisions = resolve_decision_media_references(state["decisions"], media)
        destination_galleries = deepcopy(state.get("destination_galleries") or {})
        media_curations = (
            state.get("media_curations")
            if isinstance(state.get("media_curations"), dict)
            else {}
        )
        presentation = build_media_presentation(
            media,
            limit=self.media_vision_max_highlights,
            curations=(media_curations if self.media_curation_mode == "hybrid" else {}),
        )
        if days is None:
            try:
                payload = await self.manager.async_get_assistant_payload(trip_id)
                days = list(payload.get("days", {}).get("days", []) or [])
            except RoadplannerError:
                days = []
        planning_day_covers: dict[str, dict[str, Any]] = {}
        planning_trip_cover: dict[str, Any] | None = None
        for day in days or []:
            day_id = str(day.get("id") or "")
            for stop in _stops(day):
                gallery = destination_galleries.get(str(stop.get("id") or ""))
                if not isinstance(gallery, dict):
                    continue
                images = list(gallery.get("images") or [])
                if not images:
                    continue
                primary_id = str(gallery.get("primary_image_id") or "")
                primary = next(
                    (
                        item
                        for item in images
                        if str(item.get("id") or "") == primary_id
                    ),
                    images[0],
                )
                if day_id and day_id not in presentation.get("day_covers", {}):
                    planning_day_covers.setdefault(day_id, deepcopy(primary))
                profile = (
                    stop.get("details", {}).get("place_profile")
                    if isinstance(stop.get("details"), dict)
                    else None
                )
                if planning_trip_cover is None and isinstance(profile, dict):
                    if profile.get("confirmed_at") or profile.get("verified"):
                        planning_trip_cover = deepcopy(primary)
                if day_id in planning_day_covers and planning_trip_cover is not None:
                    break
        presentation["planning_day_covers"] = planning_day_covers
        presentation["planning_trip_cover"] = planning_trip_cover
        if planning_trip_cover and not presentation.get("trip_cover"):
            presentation["display_source_by_trip"] = "planning_images"
        return {
            "decisions": decisions,
            "media": media,
            "destination_galleries": destination_galleries,
            "presentation": presentation,
            "by_day": by_day,
            "by_stop": by_stop,
            "destination_enrichment": deepcopy(self._destination_enrichment_status),
            "place_providers": self._place_provider_status(),
            "vision": {
                **deepcopy(self._vision_status),
                "enabled": self.vision_enabled,
                "mode": self.media_curation_mode,
                "model": str(getattr(self.provider, "model", "") or "") or None,
                "max_candidates": self.media_vision_max_candidates,
                "max_highlights": self.media_vision_max_highlights,
                "daily_limit": self.media_vision_daily_limit,
                "usage": deepcopy(state.get("vision_usage") or {}),
            },
            "stats": {
                "decision_count": len(decisions),
                "open_decision_count": sum(1 for item in decisions if item.get("status") in {"draft", "open"}),
                "media_count": len(media),
                "automatic_count": sum(1 for item in media if item.get("assignment_status") == "automatic"),
                "suggested_count": sum(1 for item in media if item.get("assignment_status") == "suggested"),
                "unassigned_count": sum(1 for item in media if not item.get("linked_day_id")),
                "destination_gallery_count": sum(
                    1 for item in destination_galleries.values()
                    if isinstance(item, dict) and item.get("images")
                ),
                "destination_gallery_error_count": sum(
                    1 for item in destination_galleries.values()
                    if isinstance(item, dict) and item.get("status") == "error"
                ),
                "media_duplicate_count": int(
                    presentation.get("curation", {}).get("duplicate_count", 0)
                ),
                "media_burst_suppressed_count": int(
                    presentation.get("curation", {}).get("burst_suppressed_count", 0)
                ),
                "featured_stop_count": int(
                    presentation.get("curation", {}).get("featured_stop_count", 0)
                ),
                "featured_day_count": int(
                    presentation.get("curation", {}).get("featured_day_count", 0)
                ),
                "vision_curated_stop_count": int(
                    presentation.get("curation", {}).get("vision_curated_stop_count", 0)
                ),
            },
            "onedrive": {
                **self.onedrive.status(),
                "folder_path": self.folder_path,
                "auto_sync": self.auto_sync,
                "auto_assign": self.auto_assign,
                "sync_interval_minutes": self.sync_interval_minutes,
                "recursive_subfolders": self.recursive_subfolders,
                "date_buffer_days": self.date_buffer_days,
                "max_items_per_run": self.max_items_per_run,
                "max_scan_seconds": self.max_scan_seconds,
                "settings_source": "photo_setup",
                "sync_scope": "active_trip",
                "sync_state": deepcopy(state.get("media_sync") or {}),
            },
        }

    def _place_provider_status(self) -> dict[str, Any]:
        """Return sanitized destination-provider diagnostics for the panel."""
        status = getattr(self.geocoder, "status", None)
        if isinstance(status, dict):
            return deepcopy(status)
        return {
            "enabled": bool(self.geocoder is not None and self.geocoder.enabled),
            "mode": "nominatim_only",
            "nominatim": {
                "enabled": bool(self.geocoder is not None and self.geocoder.enabled)
            },
            "google_places": {
                "enabled": False,
                "configured": False,
                "state": "not_configured",
                "requests_today": 0,
                "daily_limit": 0,
                "last_error": None,
            },
        }


    async def async_prepare_place_enrichment(
        self,
        *,
        user_id: str,
        trip_id: str,
        day_id: str | None = None,
        stop_id: str | None = None,
        limit: int = 20,
        use_ai_cleanup: bool = False,
    ) -> dict[str, Any]:
        """Return a reviewable full-place preview for incomplete stops."""
        return await self._place_enrichment_orchestrator.async_prepare_place_enrichment(
            user_id=user_id,
            trip_id=trip_id,
            day_id=day_id,
            stop_id=stop_id,
            limit=limit,
            use_ai_cleanup=use_ai_cleanup,
        )

    async def async_submit_place_enrichment(
        self,
        *,
        user_id: str,
        actor: str,
        trip_id: str,
        preview_id: str,
        selections: dict[str, str],
        manual_entries: dict[str, dict[str, Any]] | None = None,
        cleanup_confirmations: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Create one concrete, review-only ChangeSet from selected places."""
        return await self._place_enrichment_orchestrator.async_submit_place_enrichment(
            user_id=user_id,
            actor=actor,
            trip_id=trip_id,
            preview_id=preview_id,
            selections=selections,
            manual_entries=manual_entries,
            cleanup_confirmations=cleanup_confirmations,
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

    @staticmethod
    def _find_stop(
        days: list[dict[str, Any]],
        day_id: str,
        stop_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Resolve a stop even when the UI still carries its previous day ID."""
        for day in days:
            if str(day.get("id") or "") != day_id:
                continue
            for stop in _stops(day):
                if str(stop.get("id") or "") == stop_id:
                    return day, stop

        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for day in days:
            for stop in _stops(day):
                if str(stop.get("id") or "") == stop_id:
                    matches.append((day, stop))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValidationError(
                "Der ausgewählte Stopp ist mehreren Tagen zugeordnet. Bitte die Ansicht neu laden."
            )
        raise ValidationError("Der ausgewählte Stopp existiert nicht mehr")


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
        query = self._destination_query(day, stop)
        if not query:
            raise ValidationError("Für diesen Stopp fehlen Angaben für die Bildsuche")
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        search_limit = (
            self.media_vision_max_candidates
            if self.vision_enabled
            else _DESTINATION_GALLERY_SIZE
        )
        result = await self.image_provider.async_search(
            query,
            limit=search_limit,
            latitude=location.get("latitude", location.get("lat")),
            longitude=location.get("longitude", location.get("lon", location.get("lng"))),
        )
        candidates = list(result.get("results") or [])[:search_limit]
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
            images = [deepcopy(by_id[item]) for item in ordered_ids[:_DESTINATION_GALLERY_SIZE]]
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
        day, stop = self._find_stop(days, day_id, stop_id)
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
            "experience": await self.async_panel_payload(trip_id),
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
        day, stop = self._find_stop(days, day_id, stop_id)
        query = self._destination_query(day, stop)
        selected_images = list(images or [])[:_DESTINATION_GALLERY_SIZE]
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
            "experience": await self.async_panel_payload(trip_id),
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
            "experience": await self.async_panel_payload(trip_id),
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
                        and self.vision_enabled
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
                result["experience"] = await self.async_panel_payload(trip_id)
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
                    return {
                        "stop_id": str(stop.get("id") or ""),
                        "day_id": str(day.get("id") or ""),
                        "query": query,
                        "query_fingerprint": self._destination_query_fingerprint(day, stop, query),
                        "status": "error",
                        "images": [],
                        "primary_image_id": None,
                        "provider_errors": {"roadplanner": str(err)[:500]},
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
            response["experience"] = await self.async_panel_payload(trip_id)
        return response


    async def async_create_decision_from_message(
        self, *, user_id: str, trip_id: str, message_id: str
    ) -> dict[str, Any]:
        return await self._decisions.async_create_decision_from_message(
            user_id=user_id, trip_id=trip_id, message_id=message_id
        )

    async def async_select_decision(
        self, trip_id: str, decision_id: str, option_id: str
    ) -> dict[str, Any]:
        return await self._decisions.async_select_decision(trip_id, decision_id, option_id)

    async def async_transfer_decision(
        self, *, user_id: str, trip_id: str, decision_id: str
    ) -> dict[str, Any]:
        return await self._decisions.async_transfer_decision(
            user_id=user_id, trip_id=trip_id, decision_id=decision_id
        )

    async def async_archive_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        return await self._decisions.async_archive_decision(trip_id, decision_id)

    async def async_delete_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        return await self._decisions.async_delete_decision(trip_id, decision_id)
