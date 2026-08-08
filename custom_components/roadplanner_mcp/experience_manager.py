"""Decision slides and OneDrive media albums for Roadplanner."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import AssistantProvider
from .decision_manager import DecisionManager
from .destination_gallery_manager import DestinationGalleryManager
from .destination_images import DestinationImageProvider
from .experience_store import ExperienceStore
from .geocoding import GeocodingProvider
from .google_photo_token_service import GooglePhotoTokenService
from .manager import RoadplannerManager
from .media_curation_manager import MediaCurationManager
from .media_library_manager import (
    _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
    MediaLibraryManager,
)
from .media_token_service import MediaTokenService
from .media_vision_curation import VisionCurationEngine
from .onedrive_media import OneDrivePersonalClient
from .panel_payload_builder import PanelPayloadBuilder
from .park4night_lookup import Park4NightLookupService
from .place_cleanup import PlaceCleanupService
from .place_enrichment import PlaceEnrichmentService
from .place_enrichment_orchestrator import PlaceEnrichmentOrchestrator
from .roadplanner import ValidationError
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
        # Shared with the enrichment flow AND exposed directly so the stop
        # add/edit form can trigger the same page lookup. The session enables
        # the direct (AI-free) Park4Night page fetch.
        self.p4n_lookup = Park4NightLookupService(
            provider, session=async_get_clientsession(hass)
        )
        self.place_enrichment = (
            PlaceEnrichmentService(
                geocoder,
                image_provider,
                cleanup_service=PlaceCleanupService(provider),
                p4n_lookup=self.p4n_lookup,
            )
            if geocoder is not None and geocoder.enabled
            else None
        )
        self._media_tokens = MediaTokenService(hass=hass, store=store, onedrive=onedrive)
        self._google_photo_tokens = (
            GooglePhotoTokenService(image_provider.google_places)
            if image_provider.google_places is not None
            else None
        )
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
        self._panel_payload = PanelPayloadBuilder(
            hass,
            store,
            manager,
            onedrive,
            geocoder,
            provider,
            media_tokens=self._media_tokens,
            google_photo_tokens=self._google_photo_tokens,
            media_curation=self._media_curation,
            destination_gallery=self._destination_gallery,
            vision_curation=self._vision_curation,
            media_library=self._media_library,
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

    async def async_reassign_media(self, trip_id: str) -> dict[str, Any]:
        return await self._media_library.async_reassign_media(trip_id)

    async def async_delete_media(self, trip_id: str, media_id: str) -> dict[str, Any]:
        return await self._media_library.async_delete_media(trip_id, media_id)

    def validate_token(self, trip_id: str, media_id: str, kind: str, token: str) -> bool:
        return self._media_tokens.validate_token(trip_id, media_id, kind, token)

    def validate_google_photo_token(self, photo_name: str, token: str) -> bool:
        if self._google_photo_tokens is None:
            return False
        return self._google_photo_tokens.validate_token(photo_name, token)

    async def async_google_photo_redirect_url(self, photo_name: str) -> str:
        if self._google_photo_tokens is None:
            raise ValidationError("Google-Fotos sind nicht konfiguriert")
        return await self._google_photo_tokens.async_redirect_url(photo_name)

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

    async def async_media_redirect_url(
        self, trip_id: str, media_id: str, kind: str, *, size: str = "large"
    ) -> str:
        return await self._media_tokens.async_media_redirect_url(
            trip_id, media_id, kind, size=size
        )

    async def async_panel_payload(self, trip_id: str, *, days: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return await self._panel_payload.async_panel_payload(trip_id, days=days)


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
