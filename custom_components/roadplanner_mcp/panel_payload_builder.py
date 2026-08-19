"""Assemble the aggregated "experience" panel payload for Roadplanner.

Highest fan-in collaborator in the experience_manager.py decomposition: it
reads settings/status from every other collaborator plus geocoder/provider/
store/hass/manager directly, but nothing calls back into it, so its
dependencies are plain constructor parameters rather than callbacks.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import quote

from homeassistant.core import HomeAssistant

from .assistant_provider import AssistantProvider
from .destination_gallery_manager import DestinationGalleryManager
from .experience_helpers import _stops
from .experience_store import ExperienceStore, resolve_decision_media_references
from .geocoding import GeocodingProvider
from .google_photo_token_service import GooglePhotoTokenService
from .manager import RoadplannerManager
from .render_profiles import (
    PANEL_DEFAULT_RENDER_PROFILE,
    profile_choices,
    review_choices,
)
from .media_curation_manager import MediaCurationManager
from .media_intelligence import TRANSIT_ONLY_STOP_TYPES, build_media_presentation
from .media_library_manager import MediaLibraryManager
from .media_token_service import MediaTokenService
from .media_vision_curation import VisionCurationEngine
from .onedrive_media import OneDrivePersonalClient
from .roadplanner import RoadplannerError


class PanelPayloadBuilder:
    """Build the combined decisions/media/destination-gallery panel payload."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        onedrive: OneDrivePersonalClient,
        geocoder: GeocodingProvider | None,
        provider: AssistantProvider | None,
        *,
        media_tokens: MediaTokenService,
        google_photo_tokens: GooglePhotoTokenService | None = None,
        media_curation: MediaCurationManager,
        destination_gallery: DestinationGalleryManager,
        vision_curation: VisionCurationEngine,
        media_library: MediaLibraryManager,
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.onedrive = onedrive
        self.geocoder = geocoder
        self.provider = provider
        self._media_tokens = media_tokens
        self._google_photo_tokens = google_photo_tokens
        self._media_curation = media_curation
        self._destination_gallery = destination_gallery
        self._vision_curation = vision_curation
        self._media_library = media_library

    def _resolve_google_photo_urls(self, destination_galleries: dict[str, Any]) -> None:
        """Mint a fresh redirect URL for each saved Google-sourced image.

        A saved gallery persists only the durable `photo_name` reference
        (see destination_gallery_manager.py) - never a Google photo URL, since
        that would go stale. This builds a short-lived, signed redirect URL
        per payload send, exactly like media_tokens does above for OneDrive.
        """
        if self._google_photo_tokens is None:
            return
        for gallery in destination_galleries.values():
            if not isinstance(gallery, dict):
                continue
            for image in gallery.get("images") or []:
                if not isinstance(image, dict) or image.get("provider") != "google_places":
                    continue
                photo_name = str(image.get("photo_name") or "")
                if not photo_name:
                    continue
                token = self._google_photo_tokens.token(photo_name)
                url = f"/api/roadplanner/google_photo/{quote(photo_name, safe='/')}?token={token}"
                image["image_url"] = url
                image["thumbnail_url"] = url

    async def async_panel_payload(
        self, trip_id: str, *, days: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
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
        self._resolve_google_photo_urls(destination_galleries)
        media_curations = (
            state.get("media_curations")
            if isinstance(state.get("media_curations"), dict)
            else {}
        )
        if days is None:
            try:
                payload = await self.manager.async_get_assistant_payload(trip_id)
                days = list(payload.get("days", {}).get("days", []) or [])
            except RoadplannerError:
                days = []
        transit_stop_ids = frozenset(
            str(stop.get("id") or "")
            for day in days or []
            for stop in _stops(day)
            if str(stop.get("type") or "").casefold() in TRANSIT_ONLY_STOP_TYPES
        )
        presentation = build_media_presentation(
            media,
            limit=self._vision_curation.media_vision_max_highlights,
            curations=(
                media_curations
                if self._vision_curation.media_curation_mode == "hybrid"
                else {}
            ),
            transit_stop_ids=transit_stop_ids,
        )
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
                if (
                    planning_trip_cover is None
                    and isinstance(profile, dict)
                    and str(stop.get("id") or "") not in transit_stop_ids
                ):
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
            # The curation of each day, carried alongside the media
            # rather than folded into it: the film reads which photos
            # were chosen, the panel reads why, and neither has to
            # recompute the other's answer.
            "day_curations": deepcopy(state.get("day_curations") or {}),
            "destination_galleries": destination_galleries,
            "presentation": presentation,
            "by_day": by_day,
            "by_stop": by_stop,
            "destination_enrichment": deepcopy(self._destination_gallery.status),
            # In WHICH SIZES a film can be rendered, and which of them a
            # copy may be made in. Sent with the ordinary payload rather
            # than in answer to some action, because a choice that only
            # appears after pressing an unrelated button is a choice
            # nobody knows they have - and the film would quietly come
            # out in the default size forever.
            "render_profiles": profile_choices(PANEL_DEFAULT_RENDER_PROFILE),
            "review_profiles": review_choices(),
            "place_providers": self._place_provider_status(),
            "vision": {
                **deepcopy(self._media_curation.vision_status),
                "enabled": self._vision_curation.vision_enabled,
                "mode": self._vision_curation.media_curation_mode,
                "model": str(getattr(self.provider, "model", "") or "") or None,
                "max_candidates": self._vision_curation.media_vision_max_candidates,
                "max_highlights": self._vision_curation.media_vision_max_highlights,
                "daily_limit": self._vision_curation.media_vision_daily_limit,
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
                "folder_path": self._media_library.folder_path,
                "auto_sync": self._media_library.auto_sync,
                "auto_assign": self._media_library.auto_assign,
                "sync_interval_minutes": self._media_library.sync_interval_minutes,
                "recursive_subfolders": self._media_library.recursive_subfolders,
                "date_buffer_days": self._media_library.date_buffer_days,
                "max_items_per_run": self._media_library.max_items_per_run,
                "max_scan_seconds": self._media_library.max_scan_seconds,
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
