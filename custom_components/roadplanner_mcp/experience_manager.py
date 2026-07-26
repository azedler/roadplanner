"""Decision slides and OneDrive media albums for Roadplanner."""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant

from .assistant_provider import AssistantProvider
from .decision_manager import DecisionManager
from .destination_gallery_manager import DestinationGalleryManager
from .destination_images import DestinationImageProvider
from .experience_helpers import _stops
from .media_intelligence import build_media_presentation
from .experience_store import (
    ExperienceStore,
    resolve_decision_media_references,
)
from .geocoding import GeocodingProvider
from .manager import RoadplannerManager
from .media_curation_manager import MediaCurationManager
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
from .roadplanner import RoadplannerError
from .routing import OSRMRoutingClient

_LOGGER = logging.getLogger(__name__)

_DESTINATION_AUTO_BATCH = 6
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
        self._media_tokens = MediaTokenService(hass=hass, store=store, onedrive=onedrive)
        self._media_curation = MediaCurationManager(
            hass,
            store,
            manager,
            self._vision_curation,
            get_panel_payload=self.async_panel_payload,
        )
        self._destination_gallery = DestinationGalleryManager(
            hass,
            store,
            manager,
            image_provider,
            self._vision_curation,
            get_panel_payload=self.async_panel_payload,
            trigger_vision_curation=self._on_media_changed,
        )
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
        await self._destination_gallery.async_initialize()

    async def async_shutdown(self) -> None:
        await self._media_library.async_shutdown()
        await self._destination_gallery.async_shutdown()

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

    def validate_token(self, trip_id: str, media_id: str, kind: str, token: str) -> bool:
        return self._media_tokens.validate_token(trip_id, media_id, kind, token)

    async def async_curate_stop_media(
        self, trip_id: str, day_id: str, stop_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        return await self._media_curation.async_curate_stop_media(
            trip_id, day_id, stop_id, force=force
        )

    async def async_curate_trip_media(
        self, trip_id: str, *, force: bool = False, include_experience: bool = True
    ) -> dict[str, Any]:
        return await self._media_curation.async_curate_trip_media(
            trip_id, force=force, include_experience=include_experience
        )

    async def async_auto_curate_media(
        self,
        trip_id: str,
        *,
        limit: int = _VISION_BACKGROUND_BATCH,
        force: bool = False,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        return await self._media_curation.async_auto_curate_media(
            trip_id, limit=limit, force=force, include_experience=include_experience
        )

    async def async_media_redirect_url(self, trip_id: str, media_id: str, kind: str) -> str:
        return await self._media_tokens.async_media_redirect_url(trip_id, media_id, kind)

    async def async_panel_payload(self, trip_id: str, *, days: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not trip_id:
            return {
                "decisions": [],
                "media": [],
                "destination_galleries": {},
                "stats": {},
                "by_day": {},
                "by_stop": {},
                "vision": deepcopy(self._media_curation.vision_status),
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
            "destination_enrichment": deepcopy(self._destination_gallery.status),
            "place_providers": self._place_provider_status(),
            "vision": {
                **deepcopy(self._media_curation.vision_status),
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

    async def async_refresh_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        return await self._destination_gallery.async_refresh_destination_gallery(
            trip_id, day_id, stop_id
        )

    async def async_save_destination_gallery(
        self,
        trip_id: str,
        day_id: str,
        stop_id: str,
        images: list[dict[str, Any]],
        primary_image_id: str | None,
    ) -> dict[str, Any]:
        return await self._destination_gallery.async_save_destination_gallery(
            trip_id, day_id, stop_id, images, primary_image_id
        )

    async def async_delete_destination_gallery(
        self,
        trip_id: str,
        stop_id: str,
    ) -> dict[str, Any]:
        return await self._destination_gallery.async_delete_destination_gallery(trip_id, stop_id)

    async def async_auto_populate_destination_galleries(
        self,
        trip_id: str,
        *,
        limit: int = _DESTINATION_AUTO_BATCH,
        include_experience: bool = True,
    ) -> dict[str, Any]:
        return await self._destination_gallery.async_auto_populate_destination_galleries(
            trip_id, limit=limit, include_experience=include_experience
        )

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
