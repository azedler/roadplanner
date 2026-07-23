"""Roadplanner Home Assistant integration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, llm
from homeassistant.helpers.typing import ConfigType

from .assistant import RoadplannerAssistant
from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_ARCHIVE_PATH,
    CONF_ASSISTANT_AUTONOMY_LEVEL,
    CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
    CONF_ASSISTANT_COPILOT_ENABLED,
    CONF_ASSISTANT_DEBUG_ENABLED,
    CONF_ASSISTANT_ENABLE_RESEARCH,
    CONF_ASSISTANT_MAX_HISTORY,
    CONF_ASSISTANT_MAX_QUEUE,
    CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
    CONF_ASSISTANT_PROVIDER,
    CONF_ASSISTANT_REQUEST_TIMEOUT,
    CONF_ASSISTANT_RETRY_ATTEMPTS,
    CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
    CONF_AUTO_APPLY_CHANGESETS,
    CONF_AUTO_SCAN_HANDOFFS,
    CONF_BACKUP_COUNT,
    CONF_BACKUP_PATH,
    CONF_ENABLE_HANDOFF_WEBHOOK,
    CONF_HANDOFF_PATH,
    CONF_GEMINI_API_KEY,
    CONF_GEMINI_FALLBACK_MODEL,
    CONF_GEMINI_MODEL,
    CONF_MEDIA_VISION_MAX_HIGHLIGHTS,
    CONF_MEDIA_VISION_MAX_CANDIDATES,
    CONF_MEDIA_VISION_DAILY_LIMIT,
    CONF_MEDIA_CURATION_MODE,
    CONF_GEOCODING_ENABLED,
    CONF_GEOCODING_URL,
    CONF_ROUTING_ENABLED,
    CONF_ROUTING_PROVIDER,
    CONF_ROUTING_URL,
    CONF_ROUTING_PROFILE,
    CONF_ROUTING_REQUEST_TIMEOUT,
    CONF_ROUTING_MIN_REQUEST_INTERVAL,
    CONF_DOCUMENT_MAX_UPLOAD_MB,
    CONF_DOCUMENT_ANALYSIS_ENABLED,
    CONF_DEFAULT_CURRENCY,
    CONF_ONEDRIVE_ENABLED,
    CONF_ONEDRIVE_CLIENT_ID,
    CONF_ONEDRIVE_PHOTO_FOLDER,
    CONF_ONEDRIVE_AUTO_SYNC,
    CONF_ONEDRIVE_SYNC_INTERVAL,
    CONF_ONEDRIVE_AUTO_ASSIGN,
    CONF_ONEDRIVE_RECURSIVE,
    CONF_ONEDRIVE_DATE_BUFFER_DAYS,
    CONF_ONEDRIVE_MAX_ITEMS_PER_RUN,
    CONF_ONEDRIVE_MAX_SCAN_SECONDS,
    CONF_NON_ADMIN_ROLE,
    CONF_INITIALIZED_PATH,
    CONF_REFRESH_INTERVAL,
    CONF_ROADBOOK_PATH,
    CONF_STORAGE_PATH,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_TOKEN,
    DEFAULT_ARCHIVE_PATH,
    DEFAULT_ALLOW_DESTRUCTIVE_AUTO_APPLY,
    DEFAULT_ASSISTANT_AUTONOMY_LEVEL,
    DEFAULT_ASSISTANT_COPILOT_AUTO_BRIEFING,
    DEFAULT_ASSISTANT_COPILOT_ENABLED,
    DEFAULT_ASSISTANT_DEBUG_ENABLED,
    DEFAULT_ASSISTANT_ENABLE_RESEARCH,
    DEFAULT_ASSISTANT_MAX_HISTORY,
    DEFAULT_ASSISTANT_MAX_QUEUE,
    DEFAULT_ASSISTANT_MIN_REQUEST_INTERVAL,
    DEFAULT_ASSISTANT_PROVIDER,
    DEFAULT_ASSISTANT_REQUEST_TIMEOUT,
    DEFAULT_ASSISTANT_RETRY_ATTEMPTS,
    DEFAULT_AUTO_APPLY_CHANGESETS,
    DEFAULT_AUTO_SCAN_HANDOFFS,
    DEFAULT_BACKUP_COUNT,
    DEFAULT_BACKUP_PATH,
    DEFAULT_ENABLE_HANDOFF_WEBHOOK,
    DEFAULT_HANDOFF_PATH,
    DEFAULT_GEMINI_API_KEY,
    DEFAULT_GEMINI_FALLBACK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_MEDIA_VISION_MAX_HIGHLIGHTS,
    DEFAULT_MEDIA_VISION_MAX_CANDIDATES,
    DEFAULT_MEDIA_VISION_DAILY_LIMIT,
    DEFAULT_MEDIA_CURATION_MODE,
    DEFAULT_GEOCODING_ENABLED,
    DEFAULT_GEOCODING_URL,
    DEFAULT_ROUTING_ENABLED,
    DEFAULT_ROUTING_PROVIDER,
    DEFAULT_ROUTING_URL,
    DEFAULT_ROUTING_PROFILE,
    DEFAULT_ROUTING_REQUEST_TIMEOUT,
    DEFAULT_ROUTING_MIN_REQUEST_INTERVAL,
    DEFAULT_DOCUMENT_MAX_UPLOAD_MB,
    DEFAULT_DOCUMENT_ANALYSIS_ENABLED,
    DEFAULT_DEFAULT_CURRENCY,
    DEFAULT_ONEDRIVE_ENABLED,
    DEFAULT_ONEDRIVE_CLIENT_ID,
    DEFAULT_ONEDRIVE_PHOTO_FOLDER,
    DEFAULT_ONEDRIVE_AUTO_SYNC,
    DEFAULT_ONEDRIVE_SYNC_INTERVAL,
    DEFAULT_ONEDRIVE_AUTO_ASSIGN,
    DEFAULT_ONEDRIVE_RECURSIVE,
    DEFAULT_ONEDRIVE_DATE_BUFFER_DAYS,
    DEFAULT_ONEDRIVE_MAX_ITEMS_PER_RUN,
    DEFAULT_ONEDRIVE_MAX_SCAN_SECONDS,
    DEFAULT_NON_ADMIN_ROLE,
    DEFAULT_REFRESH_INTERVAL,
    DEFAULT_ROADBOOK_PATH,
    DOMAIN,
    EVENT_ROADPLANNER_UPDATED,
)
from .coordinator import RoadplannerCoordinator
from .destination_images import DestinationImageProvider
from .experience_http import async_register_experience_views
from .experience_manager import RoadplannerExperienceManager
from .experience_store import ExperienceStore
from .gemini_client import GeminiClient
from .geocoding import NominatimGeocoder
from .drive_import import async_register_drive_import_view
from .handoff import HandoffStore
from .llm_api import RoadplannerAPI
from .manager import RoadplannerManager
from .onedrive_media import OneDrivePersonalClient
from .panel import (
    async_register_frontend_panel,
    async_remove_frontend_panel,
    async_setup_panel_support,
)
from .path_utils import resolve_config_path
from .roadplanner import RoadplannerStore
from .routing import OSRMRoutingClient
from .travel_archive import TravelArchiveStore
from .travel_archive_http import async_register_travel_archive_views
from .travel_archive_manager import TravelArchiveManager
from .universal_import_manager import UniversalImportManager
from .services import async_register_services
from .webhook import (
    async_register_handoff_webhook,
    async_unregister_handoff_webhook,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass(slots=True)
class RoadplannerRuntimeData:
    """Runtime objects shared by platforms, actions, webhook, and LLM tools."""

    manager: RoadplannerManager
    coordinator: RoadplannerCoordinator
    webhook_id: str | None
    webhook_token: str | None
    non_admin_role: str
    image_provider: DestinationImageProvider
    assistant: RoadplannerAssistant
    router: OSRMRoutingClient
    travel_archive: TravelArchiveManager
    experience: RoadplannerExperienceManager
    universal_import: UniversalImportManager


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration-wide actions independently of config-entry state."""
    hass.data.setdefault(DOMAIN, {})
    async_register_services(hass)
    async_register_drive_import_view(hass)
    async_register_travel_archive_views(hass)
    async_register_experience_views(hass)
    await async_setup_panel_support(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Roadplanner from a config entry."""
    options = {**entry.data, **entry.options}
    roadbook_relative = options.get(CONF_ROADBOOK_PATH, DEFAULT_ROADBOOK_PATH)
    backup_relative = options.get(CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH)
    handoff_relative = options.get(CONF_HANDOFF_PATH, DEFAULT_HANDOFF_PATH)
    archive_relative = options.get(CONF_ARCHIVE_PATH, DEFAULT_ARCHIVE_PATH)
    config_dir = hass.config.config_dir

    store = RoadplannerStore(
        roadbook_dir=resolve_config_path(config_dir, roadbook_relative),
        backup_dir=resolve_config_path(config_dir, backup_relative),
        handoff_dir=resolve_config_path(config_dir, handoff_relative),
        backup_count=options.get(CONF_BACKUP_COUNT, DEFAULT_BACKUP_COUNT),
    )
    handoff_store = HandoffStore(store.handoff_dir)
    routing_provider = str(
        options.get(CONF_ROUTING_PROVIDER, DEFAULT_ROUTING_PROVIDER)
    )
    if routing_provider != "osrm":
        routing_provider = "osrm"
    router = OSRMRoutingClient(
        hass,
        enabled=bool(options.get(CONF_ROUTING_ENABLED, DEFAULT_ROUTING_ENABLED)),
        base_url=str(options.get(CONF_ROUTING_URL, DEFAULT_ROUTING_URL)),
        profile=str(options.get(CONF_ROUTING_PROFILE, DEFAULT_ROUTING_PROFILE)),
        request_timeout=int(
            options.get(
                CONF_ROUTING_REQUEST_TIMEOUT,
                DEFAULT_ROUTING_REQUEST_TIMEOUT,
            )
        ),
        min_request_interval=float(
            options.get(
                CONF_ROUTING_MIN_REQUEST_INTERVAL,
                DEFAULT_ROUTING_MIN_REQUEST_INTERVAL,
            )
        ),
    )
    manager = RoadplannerManager(
        hass,
        store,
        handoff_store,
        router=router,
        auto_scan_handoffs=options.get(
            CONF_AUTO_SCAN_HANDOFFS,
            DEFAULT_AUTO_SCAN_HANDOFFS,
        ),
        auto_apply_changesets=options.get(
            CONF_AUTO_APPLY_CHANGESETS,
            DEFAULT_AUTO_APPLY_CHANGESETS,
        ),
        allow_destructive_auto_apply=options.get(
            CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
            DEFAULT_ALLOW_DESTRUCTIVE_AUTO_APPLY,
        ),
    )
    coordinator = RoadplannerCoordinator(
        hass,
        entry,
        manager,
        options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL),
    )

    create_if_missing = entry.data.get(CONF_INITIALIZED_PATH) != roadbook_relative
    await manager.async_initialize(create_if_missing=create_if_missing)
    await coordinator.async_config_entry_first_refresh()
    manager.set_update_callback(coordinator.async_set_updated_data)

    @callback
    def _notify_panel() -> None:
        payload = coordinator.data or {}
        metadata = payload.get("metadata", {})
        hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {
                "entry_id": entry.entry_id,
                "revision": metadata.get("revision"),
                "pending_handoff_count": payload.get(
                    "pending_handoff_count",
                    0,
                ),
            },
        )

    entry.async_on_unload(coordinator.async_add_listener(_notify_panel))

    webhook_id: str | None = None
    webhook_token: str | None = None
    webhook_registered = False
    if options.get(
        CONF_ENABLE_HANDOFF_WEBHOOK,
        DEFAULT_ENABLE_HANDOFF_WEBHOOK,
    ):
        webhook_id = entry.data.get(CONF_WEBHOOK_ID)
        webhook_token = entry.data.get(CONF_WEBHOOK_TOKEN)
        if not webhook_id or not webhook_token:
            webhook_id = secrets.token_hex(32)
            webhook_token = secrets.token_urlsafe(48)
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_WEBHOOK_ID: webhook_id,
                    CONF_WEBHOOK_TOKEN: webhook_token,
                },
            )
        assert webhook_id is not None
        assert webhook_token is not None
        async_register_handoff_webhook(
            hass,
            manager,
            webhook_id=webhook_id,
            webhook_token=webhook_token,
        )
        webhook_registered = True
        entry.async_on_unload(
            lambda: async_unregister_handoff_webhook(hass, webhook_id)
        )

    assistant_provider = str(
        options.get(CONF_ASSISTANT_PROVIDER, DEFAULT_ASSISTANT_PROVIDER)
    )
    provider = None
    if assistant_provider == "gemini":
        provider = GeminiClient(
            hass,
            api_key=str(options.get(CONF_GEMINI_API_KEY, DEFAULT_GEMINI_API_KEY)),
            model=str(options.get(CONF_GEMINI_MODEL, DEFAULT_GEMINI_MODEL)),
            fallback_model=str(
                options.get(CONF_GEMINI_FALLBACK_MODEL, DEFAULT_GEMINI_FALLBACK_MODEL)
            ),
            request_timeout=int(
                options.get(
                    CONF_ASSISTANT_REQUEST_TIMEOUT,
                    DEFAULT_ASSISTANT_REQUEST_TIMEOUT,
                )
            ),
            retry_attempts=int(
                options.get(
                    CONF_ASSISTANT_RETRY_ATTEMPTS,
                    DEFAULT_ASSISTANT_RETRY_ATTEMPTS,
                )
            ),
            min_request_interval=float(
                options.get(
                    CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
                    DEFAULT_ASSISTANT_MIN_REQUEST_INTERVAL,
                )
            ),
            max_queue=int(
                options.get(
                    CONF_ASSISTANT_MAX_QUEUE,
                    DEFAULT_ASSISTANT_MAX_QUEUE,
                )
            ),
        )
    archive_root = resolve_config_path(config_dir, archive_relative)
    archive_store = TravelArchiveStore(archive_root)
    travel_archive = TravelArchiveManager(
        hass,
        archive_store,
        manager,
        provider=provider,
        max_upload_bytes=int(
            options.get(
                CONF_DOCUMENT_MAX_UPLOAD_MB,
                DEFAULT_DOCUMENT_MAX_UPLOAD_MB,
            )
        ) * 1024 * 1024,
        analysis_enabled=bool(
            options.get(
                CONF_DOCUMENT_ANALYSIS_ENABLED,
                DEFAULT_DOCUMENT_ANALYSIS_ENABLED,
            )
        ),
        default_currency=str(
            options.get(CONF_DEFAULT_CURRENCY, DEFAULT_DEFAULT_CURRENCY)
        ),
    )
    await travel_archive.async_initialize()

    geocoder = NominatimGeocoder(
        hass,
        enabled=bool(
            options.get(CONF_GEOCODING_ENABLED, DEFAULT_GEOCODING_ENABLED)
        ),
        base_url=str(options.get(CONF_GEOCODING_URL, DEFAULT_GEOCODING_URL)),
    )
    assistant = RoadplannerAssistant(
        manager,
        provider=provider,
        geocoder=geocoder,
        enable_research=bool(
            options.get(
                CONF_ASSISTANT_ENABLE_RESEARCH,
                DEFAULT_ASSISTANT_ENABLE_RESEARCH,
            )
        ),
        max_history=int(
            options.get(CONF_ASSISTANT_MAX_HISTORY, DEFAULT_ASSISTANT_MAX_HISTORY)
        ),
        autonomy_level=str(
            options.get(
                CONF_ASSISTANT_AUTONOMY_LEVEL,
                DEFAULT_ASSISTANT_AUTONOMY_LEVEL,
            )
        ),
        copilot_enabled=bool(
            options.get(
                CONF_ASSISTANT_COPILOT_ENABLED,
                DEFAULT_ASSISTANT_COPILOT_ENABLED,
            )
        ),
        copilot_auto_briefing=bool(
            options.get(
                CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
                DEFAULT_ASSISTANT_COPILOT_AUTO_BRIEFING,
            )
        ),
        debug_enabled=bool(
            options.get(
                CONF_ASSISTANT_DEBUG_ENABLED,
                DEFAULT_ASSISTANT_DEBUG_ENABLED,
            )
        ),
        language=hass.config.language or "de",
        travel_archive=travel_archive,
    )

    image_provider = DestinationImageProvider(hass)
    onedrive = OneDrivePersonalClient(
        hass,
        client_id=str(
            options.get(CONF_ONEDRIVE_CLIENT_ID, DEFAULT_ONEDRIVE_CLIENT_ID)
        ),
        entry_id=entry.entry_id,
    )
    experience = RoadplannerExperienceManager(
        hass,
        ExperienceStore(archive_root / "experience"),
        manager,
        onedrive,
        provider=provider,
        assistant=assistant,
        geocoder=geocoder,
        router=router,
        image_provider=image_provider,
        media_curation_mode=str(
            options.get(CONF_MEDIA_CURATION_MODE, DEFAULT_MEDIA_CURATION_MODE)
        ),
        media_vision_max_candidates=int(
            options.get(
                CONF_MEDIA_VISION_MAX_CANDIDATES,
                DEFAULT_MEDIA_VISION_MAX_CANDIDATES,
            )
        ),
        media_vision_max_highlights=int(
            options.get(
                CONF_MEDIA_VISION_MAX_HIGHLIGHTS,
                DEFAULT_MEDIA_VISION_MAX_HIGHLIGHTS,
            )
        ),
        media_vision_daily_limit=int(
            options.get(
                CONF_MEDIA_VISION_DAILY_LIMIT,
                DEFAULT_MEDIA_VISION_DAILY_LIMIT,
            )
        ),
        folder_path=str(
            options.get(CONF_ONEDRIVE_PHOTO_FOLDER, DEFAULT_ONEDRIVE_PHOTO_FOLDER)
        ),
        sync_interval_minutes=int(
            options.get(CONF_ONEDRIVE_SYNC_INTERVAL, DEFAULT_ONEDRIVE_SYNC_INTERVAL)
        ),
        auto_sync=bool(
            options.get(CONF_ONEDRIVE_ENABLED, DEFAULT_ONEDRIVE_ENABLED)
            and options.get(CONF_ONEDRIVE_AUTO_SYNC, DEFAULT_ONEDRIVE_AUTO_SYNC)
        ),
        auto_assign=bool(
            options.get(CONF_ONEDRIVE_AUTO_ASSIGN, DEFAULT_ONEDRIVE_AUTO_ASSIGN)
        ),
        recursive_subfolders=bool(
            options.get(CONF_ONEDRIVE_RECURSIVE, DEFAULT_ONEDRIVE_RECURSIVE)
        ),
        date_buffer_days=int(
            options.get(
                CONF_ONEDRIVE_DATE_BUFFER_DAYS,
                DEFAULT_ONEDRIVE_DATE_BUFFER_DAYS,
            )
        ),
        max_items_per_run=int(
            options.get(
                CONF_ONEDRIVE_MAX_ITEMS_PER_RUN,
                DEFAULT_ONEDRIVE_MAX_ITEMS_PER_RUN,
            )
        ),
        max_scan_seconds=int(
            options.get(
                CONF_ONEDRIVE_MAX_SCAN_SECONDS,
                DEFAULT_ONEDRIVE_MAX_SCAN_SECONDS,
            )
        ),
    )
    await experience.async_initialize()

    universal_import = UniversalImportManager(
        hass,
        travel_archive,
        manager,
        assistant,
        provider=provider,
    )

    runtime = RoadplannerRuntimeData(
        manager=manager,
        coordinator=coordinator,
        webhook_id=webhook_id,
        webhook_token=webhook_token,
        non_admin_role=options.get(
            CONF_NON_ADMIN_ROLE,
            DEFAULT_NON_ADMIN_ROLE,
        ),
        image_provider=image_provider,
        assistant=assistant,
        router=router,
        travel_archive=travel_archive,
        experience=experience,
        universal_import=universal_import,
    )
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    if entry.data.get(CONF_INITIALIZED_PATH) != roadbook_relative:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_INITIALIZED_PATH: roadbook_relative},
        )

    unregister_api = None
    try:
        unregister_api = llm.async_register_api(
            hass,
            RoadplannerAPI(hass, manager),
        )
        await async_register_frontend_panel(hass, entry)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        manager.set_update_callback(None)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if unregister_api is not None:
            unregister_api()
        async_remove_frontend_panel(hass)
        if webhook_registered and webhook_id is not None:
            async_unregister_handoff_webhook(hass, webhook_id)
        raise

    entry.async_on_unload(unregister_api)
    entry.async_on_unload(lambda: async_remove_frontend_panel(hass))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Roadplanner platforms and runtime state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.manager.set_update_callback(None)
        await entry.runtime_data.experience.async_shutdown()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload Roadplanner after options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate legacy single-file config entries to the split-roadbook model."""
    if entry.version > CONFIG_ENTRY_VERSION:
        return False
    if entry.version == CONFIG_ENTRY_VERSION:
        return True

    effective = {**entry.data, **entry.options}
    roadbook_relative = effective.get(CONF_ROADBOOK_PATH)
    if not roadbook_relative:
        old_storage = effective.get(
            CONF_STORAGE_PATH,
            "www/roadbook/active_trip.json",
        )
        old_path = PurePosixPath(str(old_storage))
        roadbook_relative = (
            old_path.parent.as_posix()
            if old_path.name == "active_trip.json"
            else DEFAULT_ROADBOOK_PATH
        )

    data: dict[str, Any] = {
        CONF_ROADBOOK_PATH: roadbook_relative,
        CONF_BACKUP_PATH: effective.get(CONF_BACKUP_PATH, DEFAULT_BACKUP_PATH),
        CONF_HANDOFF_PATH: effective.get(CONF_HANDOFF_PATH, DEFAULT_HANDOFF_PATH),
        CONF_ARCHIVE_PATH: effective.get(CONF_ARCHIVE_PATH, DEFAULT_ARCHIVE_PATH),
        CONF_BACKUP_COUNT: effective.get(CONF_BACKUP_COUNT, DEFAULT_BACKUP_COUNT),
        CONF_REFRESH_INTERVAL: effective.get(
            CONF_REFRESH_INTERVAL,
            DEFAULT_REFRESH_INTERVAL,
        ),
        CONF_ENABLE_HANDOFF_WEBHOOK: effective.get(
            CONF_ENABLE_HANDOFF_WEBHOOK,
            DEFAULT_ENABLE_HANDOFF_WEBHOOK,
        ),
        CONF_AUTO_SCAN_HANDOFFS: effective.get(
            CONF_AUTO_SCAN_HANDOFFS,
            DEFAULT_AUTO_SCAN_HANDOFFS,
        ),
        CONF_AUTO_APPLY_CHANGESETS: effective.get(
            CONF_AUTO_APPLY_CHANGESETS,
            DEFAULT_AUTO_APPLY_CHANGESETS,
        ),
        CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY: effective.get(
            CONF_ALLOW_DESTRUCTIVE_AUTO_APPLY,
            DEFAULT_ALLOW_DESTRUCTIVE_AUTO_APPLY,
        ),
        CONF_NON_ADMIN_ROLE: effective.get(
            CONF_NON_ADMIN_ROLE,
            DEFAULT_NON_ADMIN_ROLE,
        ),
        CONF_ASSISTANT_PROVIDER: effective.get(
            CONF_ASSISTANT_PROVIDER,
            DEFAULT_ASSISTANT_PROVIDER,
        ),
        CONF_GEMINI_API_KEY: effective.get(
            CONF_GEMINI_API_KEY,
            DEFAULT_GEMINI_API_KEY,
        ),
        CONF_GEMINI_MODEL: effective.get(
            CONF_GEMINI_MODEL,
            DEFAULT_GEMINI_MODEL,
        ),
        CONF_GEMINI_FALLBACK_MODEL: effective.get(
            CONF_GEMINI_FALLBACK_MODEL,
            DEFAULT_GEMINI_FALLBACK_MODEL,
        ),
        CONF_ASSISTANT_REQUEST_TIMEOUT: effective.get(
            CONF_ASSISTANT_REQUEST_TIMEOUT,
            DEFAULT_ASSISTANT_REQUEST_TIMEOUT,
        ),
        CONF_ASSISTANT_RETRY_ATTEMPTS: effective.get(
            CONF_ASSISTANT_RETRY_ATTEMPTS,
            DEFAULT_ASSISTANT_RETRY_ATTEMPTS,
        ),
        CONF_ASSISTANT_MIN_REQUEST_INTERVAL: effective.get(
            CONF_ASSISTANT_MIN_REQUEST_INTERVAL,
            DEFAULT_ASSISTANT_MIN_REQUEST_INTERVAL,
        ),
        CONF_ASSISTANT_MAX_QUEUE: effective.get(
            CONF_ASSISTANT_MAX_QUEUE,
            DEFAULT_ASSISTANT_MAX_QUEUE,
        ),
        CONF_ASSISTANT_AUTONOMY_LEVEL: effective.get(
            CONF_ASSISTANT_AUTONOMY_LEVEL,
            DEFAULT_ASSISTANT_AUTONOMY_LEVEL,
        ),
        CONF_ASSISTANT_COPILOT_ENABLED: effective.get(
            CONF_ASSISTANT_COPILOT_ENABLED,
            DEFAULT_ASSISTANT_COPILOT_ENABLED,
        ),
        CONF_ASSISTANT_COPILOT_AUTO_BRIEFING: effective.get(
            CONF_ASSISTANT_COPILOT_AUTO_BRIEFING,
            DEFAULT_ASSISTANT_COPILOT_AUTO_BRIEFING,
        ),
        CONF_ASSISTANT_DEBUG_ENABLED: effective.get(
            CONF_ASSISTANT_DEBUG_ENABLED,
            DEFAULT_ASSISTANT_DEBUG_ENABLED,
        ),
        CONF_ASSISTANT_ENABLE_RESEARCH: effective.get(
            CONF_ASSISTANT_ENABLE_RESEARCH,
            DEFAULT_ASSISTANT_ENABLE_RESEARCH,
        ),
        CONF_ASSISTANT_MAX_HISTORY: effective.get(
            CONF_ASSISTANT_MAX_HISTORY,
            DEFAULT_ASSISTANT_MAX_HISTORY,
        ),
        CONF_GEOCODING_ENABLED: effective.get(
            CONF_GEOCODING_ENABLED,
            DEFAULT_GEOCODING_ENABLED,
        ),
        CONF_GEOCODING_URL: effective.get(
            CONF_GEOCODING_URL,
            DEFAULT_GEOCODING_URL,
        ),
        CONF_ROUTING_ENABLED: effective.get(
            CONF_ROUTING_ENABLED,
            DEFAULT_ROUTING_ENABLED,
        ),
        CONF_ROUTING_PROVIDER: effective.get(
            CONF_ROUTING_PROVIDER,
            DEFAULT_ROUTING_PROVIDER,
        ),
        CONF_ROUTING_URL: effective.get(
            CONF_ROUTING_URL,
            DEFAULT_ROUTING_URL,
        ),
        CONF_ROUTING_PROFILE: effective.get(
            CONF_ROUTING_PROFILE,
            DEFAULT_ROUTING_PROFILE,
        ),
        CONF_ROUTING_REQUEST_TIMEOUT: effective.get(
            CONF_ROUTING_REQUEST_TIMEOUT,
            DEFAULT_ROUTING_REQUEST_TIMEOUT,
        ),
        CONF_ROUTING_MIN_REQUEST_INTERVAL: effective.get(
            CONF_ROUTING_MIN_REQUEST_INTERVAL,
            DEFAULT_ROUTING_MIN_REQUEST_INTERVAL,
        ),
        CONF_DOCUMENT_MAX_UPLOAD_MB: effective.get(
            CONF_DOCUMENT_MAX_UPLOAD_MB,
            DEFAULT_DOCUMENT_MAX_UPLOAD_MB,
        ),
        CONF_DOCUMENT_ANALYSIS_ENABLED: effective.get(
            CONF_DOCUMENT_ANALYSIS_ENABLED,
            DEFAULT_DOCUMENT_ANALYSIS_ENABLED,
        ),
        CONF_DEFAULT_CURRENCY: effective.get(
            CONF_DEFAULT_CURRENCY,
            DEFAULT_DEFAULT_CURRENCY,
        ),
        CONF_ONEDRIVE_ENABLED: effective.get(
            CONF_ONEDRIVE_ENABLED, DEFAULT_ONEDRIVE_ENABLED
        ),
        CONF_ONEDRIVE_CLIENT_ID: effective.get(
            CONF_ONEDRIVE_CLIENT_ID, DEFAULT_ONEDRIVE_CLIENT_ID
        ),
        CONF_ONEDRIVE_PHOTO_FOLDER: effective.get(
            CONF_ONEDRIVE_PHOTO_FOLDER, DEFAULT_ONEDRIVE_PHOTO_FOLDER
        ),
        CONF_ONEDRIVE_AUTO_SYNC: effective.get(
            CONF_ONEDRIVE_AUTO_SYNC, DEFAULT_ONEDRIVE_AUTO_SYNC
        ),
        CONF_ONEDRIVE_SYNC_INTERVAL: effective.get(
            CONF_ONEDRIVE_SYNC_INTERVAL, DEFAULT_ONEDRIVE_SYNC_INTERVAL
        ),
        CONF_ONEDRIVE_AUTO_ASSIGN: effective.get(
            CONF_ONEDRIVE_AUTO_ASSIGN, DEFAULT_ONEDRIVE_AUTO_ASSIGN
        ),
        CONF_ONEDRIVE_RECURSIVE: effective.get(
            CONF_ONEDRIVE_RECURSIVE, DEFAULT_ONEDRIVE_RECURSIVE
        ),
        CONF_ONEDRIVE_DATE_BUFFER_DAYS: effective.get(
            CONF_ONEDRIVE_DATE_BUFFER_DAYS, DEFAULT_ONEDRIVE_DATE_BUFFER_DAYS
        ),
        CONF_ONEDRIVE_MAX_ITEMS_PER_RUN: effective.get(
            CONF_ONEDRIVE_MAX_ITEMS_PER_RUN, DEFAULT_ONEDRIVE_MAX_ITEMS_PER_RUN
        ),
        CONF_ONEDRIVE_MAX_SCAN_SECONDS: effective.get(
            CONF_ONEDRIVE_MAX_SCAN_SECONDS, DEFAULT_ONEDRIVE_MAX_SCAN_SECONDS
        ),
        CONF_WEBHOOK_ID: entry.data.get(CONF_WEBHOOK_ID) or secrets.token_hex(32),
        CONF_WEBHOOK_TOKEN: entry.data.get(CONF_WEBHOOK_TOKEN)
        or secrets.token_urlsafe(48),
    }
    pointer_path = resolve_config_path(
        hass.config.config_dir,
        roadbook_relative,
    ) / "active_trip.json"
    if pointer_path.exists():
        data[CONF_INITIALIZED_PATH] = roadbook_relative

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options={},
        unique_id=entry.unique_id or DOMAIN,
        version=CONFIG_ENTRY_VERSION,
    )
    return True
