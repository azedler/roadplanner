"""OneDrive folder scan/delta-sync engine, geo/date-based photo-to-stop
auto-assignment, and media item CRUD.

async_sync_trip and _async_periodic_sync trigger optional Vision curation of
newly-synced media through an injected on_media_changed callback rather than
importing the curation collaborator directly, keeping the dependency
one-directional (experience_manager -> media_library_manager).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from functools import partial
import logging
from time import monotonic
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import EVENT_ROADPLANNER_UPDATED
from .experience_helpers import (
    _all_days,
    _coordinate,
    _day_date,
    _distance_m,
    _folder_date_hint,
    _folder_scan_decision,
    _hint_from_json,
    _hint_to_json,
    _join_display_path,
    _media_local_date,
    _parse_datetime,
    _provider_media,
    _stops,
    _trip_date_window,
)
from .experience_store import ExperienceStore, utc_now_iso
from .manager import RoadplannerManager
from .onedrive_media import OneDrivePersonalClient, normalize_onedrive_folder_path
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

# How close a photo has to be to a stop before Roadplanner assigns it
# without asking. Near enough is enough, and this number is unchanged:
# everything that was automatic before stays automatic.
_AUTOMATIC_RADIUS_M = 750.0

# The second way to be certain: not near, but *unambiguous*.
#
# A fixed radius asks the wrong question. It asks "is this photo close?"
# when the thing that decides whether a human can answer is "is there
# anything else it could be?". A wildlife park is bigger than 750 m, so
# 253 photographs taken 799-912 m from the only stop for twenty
# kilometres landed in "zu prüfen" - not because anyone was in doubt, but
# because a place was larger than a number. Meanwhile 700 m in a town
# with four stops within a kilometre is a genuine coin toss, and that one
# was being decided automatically.
#
# So the second rule looks at the runner-up. If the nearest stop is
# within reach and the next-nearest is far behind it, nobody would pick
# differently, and asking is a formality. Both a ratio and an absolute
# margin have to hold: the ratio is what makes "clearly the closest"
# meaningful at any scale, and the margin stops a few dozen metres of
# difference from counting as clarity.
#
# Twice as far, and at least 800 m further. Three times was the first
# number and it was too cautious to help: at 800 m it would demand the
# runner-up be 2.4 km away, and the campsite the same evening is often
# nearer than that. The cost of the two mistakes is not symmetric - a
# photo filed under the wrong stop of the *right day* is a small thing
# and can be corrected in one tap, while 253 photographs waiting for a
# click is what made this worth changing.
_CLEAR_RADIUS_M = 2_500.0
_CLEAR_SEPARATION = 2.0
_CLEAR_MARGIN_M = 800.0

_SUGGESTED_RADIUS_M = 5_000.0
_MEDIA_SYNC_STRATEGY_VERSION = 3
_INITIAL_SCAN_MODE = "initial_scan"
_DELTA_CATCHUP_MODE = "delta_catchup"
_DELTA_MODE = "delta"
_SCAN_PAGE_SIZE = 200
_DEFAULT_SCAN_TIME_BUDGET_SECONDS = 12


class MediaLibraryManager:
    """Own the OneDrive scan/delta-sync engine and media item CRUD."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        onedrive: OneDrivePersonalClient,
        *,
        folder_path: str,
        sync_interval_minutes: int,
        auto_sync: bool,
        auto_assign: bool,
        recursive_subfolders: bool = True,
        date_buffer_days: int = 3,
        max_items_per_run: int = 2000,
        max_scan_seconds: int = _DEFAULT_SCAN_TIME_BUDGET_SECONDS,
        on_media_changed: Callable[[str], Awaitable[None]],
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.onedrive = onedrive
        self.folder_path = normalize_onedrive_folder_path(
            folder_path or "Pictures/Camera Roll"
        )
        self.sync_interval_minutes = max(5, min(int(sync_interval_minutes), 1440))
        self.auto_sync = bool(auto_sync)
        self.auto_assign = bool(auto_assign)
        self.recursive_subfolders = bool(recursive_subfolders)
        self.date_buffer_days = max(0, min(int(date_buffer_days), 30))
        self.max_items_per_run = max(100, min(int(max_items_per_run), 5000))
        self.max_scan_seconds = max(3, min(int(max_scan_seconds), 60))
        self._sync_lock = asyncio.Lock()
        self._unsub_interval: Any = None
        self._on_media_changed = on_media_changed

    async def async_initialize(self) -> None:
        await self.hass.async_add_executor_job(self.store.initialize)
        await self.onedrive.async_initialize()
        stored = self.onedrive.stored_settings()
        if not stored:
            # 2.6.5 makes the in-panel OneDrive setup the single source of
            # truth. On the first start after upgrading, migrate the legacy
            # config-entry values into the private OneDrive settings store.
            migrated_max_items = (
                2000 if int(self.max_items_per_run or 0) == 250 else self.max_items_per_run
            )
            await self.onedrive.async_reconfigure(
                self.onedrive.client_id,
                settings={
                    "settings_version": 2,
                    "folder_path": self.folder_path,
                    "auto_sync": self.auto_sync,
                    "auto_assign": self.auto_assign,
                    "sync_interval_minutes": self.sync_interval_minutes,
                    "recursive_subfolders": self.recursive_subfolders,
                    "date_buffer_days": self.date_buffer_days,
                    "max_items_per_run": migrated_max_items,
                    "max_scan_seconds": self.max_scan_seconds,
                },
            )
            stored = self.onedrive.stored_settings()
        elif int(stored.get("settings_version") or 0) < 2:
            migrated = dict(stored)
            migrated["settings_version"] = 2
            migrated["folder_path"] = normalize_onedrive_folder_path(
                migrated.get("folder_path") or self.folder_path
            )
            if int(migrated.get("max_items_per_run") or 0) == 250:
                migrated["max_items_per_run"] = 2000
            migrated.setdefault("max_scan_seconds", self.max_scan_seconds)
            await self.onedrive.async_reconfigure(
                self.onedrive.client_id,
                settings=migrated,
            )
            stored = self.onedrive.stored_settings()
        if stored:
            self.folder_path = normalize_onedrive_folder_path(
                stored.get("folder_path") or self.folder_path
            )
            self.auto_sync = bool(stored.get("auto_sync", self.auto_sync))
            self.auto_assign = bool(stored.get("auto_assign", self.auto_assign))
            self.sync_interval_minutes = max(
                5,
                min(
                    int(stored.get("sync_interval_minutes") or self.sync_interval_minutes),
                    1440,
                ),
            )
            self.recursive_subfolders = bool(
                stored.get("recursive_subfolders", self.recursive_subfolders)
            )
            self.date_buffer_days = max(
                0,
                min(
                    int(stored.get("date_buffer_days", self.date_buffer_days)),
                    30,
                ),
            )
            self.max_items_per_run = max(
                100,
                min(
                    int(stored.get("max_items_per_run", self.max_items_per_run)),
                    5000,
                ),
            )
            self.max_scan_seconds = max(
                3,
                min(
                    int(stored.get("max_scan_seconds", self.max_scan_seconds)),
                    60,
                ),
            )
        self._reschedule_sync()

    async def async_shutdown(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None

    def _reschedule_sync(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self.auto_sync:
            self._unsub_interval = async_track_time_interval(
                self.hass,
                self._periodic_sync,
                timedelta(minutes=self.sync_interval_minutes),
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
        """Update OneDrive Personal settings from the in-panel setup wizard."""
        self.folder_path = normalize_onedrive_folder_path(
            folder_path or "Pictures/Camera Roll"
        )
        self.auto_sync = bool(auto_sync)
        self.auto_assign = bool(auto_assign)
        self.sync_interval_minutes = max(5, min(int(sync_interval_minutes), 1440))
        self.recursive_subfolders = bool(recursive_subfolders)
        self.date_buffer_days = max(0, min(int(date_buffer_days), 30))
        self.max_items_per_run = max(100, min(int(max_items_per_run), 5000))
        self.max_scan_seconds = max(3, min(int(max_scan_seconds), 60))
        await self.onedrive.async_reconfigure(
            client_id,
            settings={
                "settings_version": 2,
                "folder_path": self.folder_path,
                "auto_sync": self.auto_sync,
                "auto_assign": self.auto_assign,
                "sync_interval_minutes": self.sync_interval_minutes,
                "recursive_subfolders": self.recursive_subfolders,
                "date_buffer_days": self.date_buffer_days,
                "max_items_per_run": self.max_items_per_run,
                "max_scan_seconds": self.max_scan_seconds,
            },
        )
        self._reschedule_sync()
        return {
            **self.onedrive.status(),
            "folder_path": self.folder_path,
            "auto_sync": self.auto_sync,
            "auto_assign": self.auto_assign,
            "sync_interval_minutes": self.sync_interval_minutes,
            "recursive_subfolders": self.recursive_subfolders,
            "date_buffer_days": self.date_buffer_days,
            "max_items_per_run": self.max_items_per_run,
            "max_scan_seconds": self.max_scan_seconds,
        }

    @callback
    def _periodic_sync(self, _now: datetime) -> None:
        if not self.onedrive.connected:
            return
        self.hass.async_create_task(self._async_periodic_sync())

    async def _async_periodic_sync(self) -> None:
        try:
            result = await self.async_sync_all_trips()
        except RoadplannerError as err:
            _LOGGER.debug("Periodic OneDrive photo sync failed: %s", err)
            return
        changed_trips = [
            item
            for item in result.get("trips", [])
            if isinstance(item, dict)
            and (
                int(item.get("added") or 0)
                or int(item.get("updated") or 0)
                or int(item.get("removed") or 0)
            )
        ]
        if changed_trips:
            for item in changed_trips:
                trip_id = str(item.get("trip_id") or "")
                if not trip_id:
                    continue
                try:
                    await self._on_media_changed(trip_id)
                except (RoadplannerError, asyncio.TimeoutError):
                    pass
        if changed_trips:
            self.hass.bus.async_fire(
                EVENT_ROADPLANNER_UPDATED,
                {"experience_changed": True, "source": "onedrive_sync"},
            )

    async def async_start_onedrive_auth(self) -> dict[str, Any]:
        return await self.onedrive.async_start_device_authorization()

    async def async_poll_onedrive_auth(self) -> dict[str, Any]:
        return await self.onedrive.async_poll_device_authorization()

    async def async_disconnect_onedrive(self) -> dict[str, Any]:
        await self.onedrive.async_disconnect()
        return self.onedrive.status()

    async def async_sync_all_trips(self) -> dict[str, Any]:
        """Synchronize only the globally active trip in the background.

        Manual synchronization from the panel still targets the currently
        selected trip.  Limiting periodic work to the active trip avoids
        repeatedly traversing the same large camera archive for old trips.
        """
        trips = await self.manager.async_list_trips()
        active_trip = str(trips.get("active_trip") or "") if isinstance(trips, dict) else ""
        if not active_trip:
            return {"ok": True, "active_trip_only": True, "trips": []}
        try:
            result = await self.async_sync_trip(active_trip)
        except RoadplannerError as err:
            result = {"trip_id": active_trip, "ok": False, "error": str(err)}
        return {
            "ok": bool(result.get("ok", False)),
            "active_trip_only": True,
            "trips": [result],
        }

    async def _new_initial_scan_state(
        self,
        *,
        folder: dict[str, Any],
        range_key: str,
    ) -> dict[str, Any]:
        folder_id = str(folder.get("id") or "")
        if not folder_id:
            raise ValidationError("OneDrive-Ordner-ID fehlt")
        baseline_delta_link = await self.onedrive.async_latest_delta(folder_id)
        now = utc_now_iso()
        root_name = str(folder.get("name") or self.folder_path or "Fotoordner")
        root_path = normalize_onedrive_folder_path(
            self.folder_path or root_name,
            allow_empty=True,
        ) or root_name
        root_hint = _folder_date_hint(root_name)
        return {
            "strategy_version": _MEDIA_SYNC_STRATEGY_VERSION,
            "folder_id": folder_id,
            "folder_path": self.folder_path,
            "trip_date_range": range_key,
            "recursive_subfolders": self.recursive_subfolders,
            "date_buffer_days": self.date_buffer_days,
            "max_items_per_run": self.max_items_per_run,
            "mode": _INITIAL_SCAN_MODE,
            "baseline_delta_link": baseline_delta_link,
            "scan_queue": [
                {
                    "folder_id": folder_id,
                    "name": root_name,
                    "path": root_path,
                    "date_hint": _hint_to_json(root_hint),
                    "next_link": None,
                }
            ],
            "scan_queued_folder_ids": [folder_id],
            "scan_seen_ids": [],
            "scan_started_at": now,
            "scan_stats": {
                "runs": 0,
                "entries_examined": 0,
                "folders_examined": 0,
                "folders_discovered": 1,
                "folders_completed": 0,
                "folders_skipped": 0,
                "hidden_folders_skipped": 0,
                "dated_folders_skipped": 0,
                "photo_files_examined": 0,
                "relevant_photos": 0,
                "outside_window_skipped": 0,
                "without_date_skipped": 0,
                "non_image_skipped": 0,
                "current_folder": root_path,
                "last_run_duration_ms": 0,
                "last_run_limit_reason": None,
            },
        }

    @staticmethod
    def _scan_state_matches(
        sync_state: dict[str, Any],
        *,
        folder_id: str,
        range_key: str,
        recursive_subfolders: bool,
        date_buffer_days: int,
    ) -> bool:
        return (
            int(sync_state.get("strategy_version") or 0)
            == _MEDIA_SYNC_STRATEGY_VERSION
            and str(sync_state.get("folder_id") or "") == folder_id
            and str(sync_state.get("trip_date_range") or "") == range_key
            and bool(sync_state.get("recursive_subfolders", True))
            == bool(recursive_subfolders)
            and int(sync_state.get("date_buffer_days") or 0)
            == int(date_buffer_days)
            and str(sync_state.get("mode") or "")
            in {_INITIAL_SCAN_MODE, _DELTA_CATCHUP_MODE, _DELTA_MODE}
        )

    async def _initial_scan_batch(
        self,
        sync_state: dict[str, Any],
        *,
        window: tuple[date, date],
        days: list[dict[str, Any]],
    ) -> dict[str, Any]:
        queue = [
            dict(item)
            for item in list(sync_state.get("scan_queue") or [])
            if isinstance(item, dict)
        ]
        queued_ids = {
            str(item)
            for item in list(sync_state.get("scan_queued_folder_ids") or [])
            if str(item)
        }
        seen_ids = {
            str(item)
            for item in list(sync_state.get("scan_seen_ids") or [])
            if str(item)
        }
        stats = dict(sync_state.get("scan_stats") or {})
        stats["runs"] = int(stats.get("runs") or 0) + 1
        normalized: list[dict[str, Any]] = []
        processed_entries = 0
        started = monotonic()
        deadline = started + float(self.max_scan_seconds)
        limit_reason: str | None = None

        while queue:
            if processed_entries >= self.max_items_per_run:
                limit_reason = "entry_budget"
                break
            if monotonic() >= deadline:
                limit_reason = "time_budget"
                break

            current = queue[0]
            folder_id = str(current.get("folder_id") or "")
            if not folder_id:
                queue.pop(0)
                continue
            current_path = str(
                current.get("path") or current.get("name") or "Fotoordner"
            )
            current_hint = _hint_from_json(current.get("date_hint"))
            stats["current_folder"] = current_path
            remaining = max(1, self.max_items_per_run - processed_entries)
            page_size = max(1, min(_SCAN_PAGE_SIZE, remaining))
            result = await self.onedrive.async_list_children(
                folder_id,
                cursor_link=str(current.get("next_link") or "") or None,
                page_size=page_size,
            )
            items = (
                result.get("items")
                if isinstance(result.get("items"), list)
                else []
            )
            processed_entries += len(items)
            stats["entries_examined"] = int(stats.get("entries_examined") or 0) + len(items)

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                is_folder = isinstance(raw.get("folder"), dict) or isinstance(
                    raw.get("package"), dict
                )
                if is_folder:
                    stats["folders_examined"] = int(stats.get("folders_examined") or 0) + 1
                    child_id = str(raw.get("id") or "")
                    child_name = str(raw.get("name") or "Ordner")
                    include, reason, child_hint = _folder_scan_decision(
                        child_name,
                        window,
                        recursive=self.recursive_subfolders,
                        parent_hint=current_hint,
                    )
                    if include and child_id and child_id not in queued_ids:
                        child_path = _join_display_path(current_path, child_name)
                        queue.append(
                            {
                                "folder_id": child_id,
                                "name": child_name,
                                "path": child_path,
                                "date_hint": _hint_to_json(child_hint),
                                "next_link": None,
                            }
                        )
                        queued_ids.add(child_id)
                        stats["folders_discovered"] = int(
                            stats.get("folders_discovered") or 0
                        ) + 1
                    elif not include:
                        stats["folders_skipped"] = int(stats.get("folders_skipped") or 0) + 1
                        if reason == "hidden_or_generated":
                            stats["hidden_folders_skipped"] = int(
                                stats.get("hidden_folders_skipped") or 0
                            ) + 1
                        elif reason == "outside_trip_window":
                            stats["dated_folders_skipped"] = int(
                                stats.get("dated_folders_skipped") or 0
                            ) + 1
                    continue

                media = _provider_media(raw)
                if media is None:
                    stats["non_image_skipped"] = int(stats.get("non_image_skipped") or 0) + 1
                    continue
                stats["photo_files_examined"] = int(
                    stats.get("photo_files_examined") or 0
                ) + 1
                local_date = _media_local_date(media)
                if local_date is None:
                    stats["without_date_skipped"] = int(
                        stats.get("without_date_skipped") or 0
                    ) + 1
                    continue
                if not (window[0] <= local_date <= window[1]):
                    stats["outside_window_skipped"] = int(
                        stats.get("outside_window_skipped") or 0
                    ) + 1
                    continue
                provider_id = str(media.get("provider_item_id") or "")
                if not provider_id:
                    continue
                seen_ids.add(provider_id)
                if self.auto_assign:
                    media.update(self._assignment_for(media, days))
                normalized.append(media)
                stats["relevant_photos"] = int(stats.get("relevant_photos") or 0) + 1

            next_link = str(result.get("next_link") or "") or None
            if next_link:
                queue[0]["next_link"] = next_link
            else:
                queue.pop(0)
                stats["folders_completed"] = int(stats.get("folders_completed") or 0) + 1

        duration_ms = max(0, int((monotonic() - started) * 1000))
        stats["last_run_duration_ms"] = duration_ms
        stats["last_run_limit_reason"] = limit_reason
        sync_state["scan_queue"] = queue
        sync_state["scan_queued_folder_ids"] = sorted(queued_ids)
        sync_state["scan_seen_ids"] = sorted(seen_ids)
        sync_state["scan_stats"] = stats
        sync_state["last_run_entry_count"] = processed_entries
        sync_state["last_relevant_count"] = len(normalized)
        sync_state["last_sync_at"] = utc_now_iso()
        completed = not queue
        if completed:
            sync_state["mode"] = _DELTA_CATCHUP_MODE
            sync_state["next_link"] = str(sync_state.get("baseline_delta_link") or "") or None
            sync_state["initial_scan_completed_at"] = utc_now_iso()
        return {
            "normalized": normalized,
            "remove_ids": set(),
            "completed": completed,
            "resync": False,
            "finalize_initial": False,
        }

    def _normalize_delta_items(
        self,
        raw_items: list[dict[str, Any]],
        *,
        window: tuple[date, date],
        days: list[dict[str, Any]],
        seen_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        normalized: list[dict[str, Any]] = []
        remove_ids: set[str] = set()
        counters = {
            "delta_entries_examined": 0,
            "delta_relevant_photos": 0,
            "delta_outside_window": 0,
            "delta_deleted": 0,
            "delta_without_date": 0,
        }
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            counters["delta_entries_examined"] += 1
            provider_id = str(raw.get("id") or "")
            if raw.get("deleted"):
                if provider_id:
                    remove_ids.add(provider_id)
                    counters["delta_deleted"] += 1
                continue
            if isinstance(raw.get("folder"), dict) or isinstance(raw.get("package"), dict):
                continue
            media = _provider_media(raw)
            if media is None:
                # If a former image is converted or replaced by a non-image,
                # remove the stale Roadplanner reference when one exists.
                if provider_id and isinstance(raw.get("file"), dict):
                    remove_ids.add(provider_id)
                continue
            local_date = _media_local_date(media)
            if local_date is None:
                counters["delta_without_date"] += 1
                continue
            if not (window[0] <= local_date <= window[1]):
                if provider_id:
                    remove_ids.add(provider_id)
                counters["delta_outside_window"] += 1
                continue
            if provider_id and seen_ids is not None:
                seen_ids.add(provider_id)
            if self.auto_assign:
                media.update(self._assignment_for(media, days))
            normalized.append(media)
            counters["delta_relevant_photos"] += 1
        return {
            "normalized": normalized,
            "remove_ids": remove_ids,
            "counters": counters,
        }

    async def _delta_batch(
        self,
        sync_state: dict[str, Any],
        *,
        folder_id: str,
        window: tuple[date, date],
        days: list[dict[str, Any]],
    ) -> dict[str, Any]:
        mode = str(sync_state.get("mode") or _DELTA_MODE)
        cursor = str(sync_state.get("next_link") or "") or None
        if cursor is None:
            if mode == _DELTA_CATCHUP_MODE:
                cursor = str(sync_state.get("baseline_delta_link") or "") or None
            else:
                cursor = str(sync_state.get("delta_link") or "") or None
        if cursor is None:
            return {"resync": True, "normalized": [], "remove_ids": set(), "finalize_initial": False}

        delta = await self.onedrive.async_delta(
            folder_id,
            cursor_link=cursor,
            max_items=self.max_items_per_run,
        )
        if delta.get("resync"):
            return {"resync": True, "normalized": [], "remove_ids": set(), "finalize_initial": False}

        seen_ids = {
            str(item)
            for item in list(sync_state.get("scan_seen_ids") or [])
            if str(item)
        } if mode == _DELTA_CATCHUP_MODE else None
        processed = self._normalize_delta_items(
            [item for item in list(delta.get("items") or []) if isinstance(item, dict)],
            window=window,
            days=days,
            seen_ids=seen_ids,
        )
        next_link = str(delta.get("next_link") or "") or None
        final_delta = str(delta.get("delta_link") or "") or None
        if next_link:
            sync_state["next_link"] = next_link
        else:
            sync_state.pop("next_link", None)
            if final_delta:
                sync_state["delta_link"] = final_delta
        finalize_initial = mode == _DELTA_CATCHUP_MODE and not next_link
        if mode == _DELTA_CATCHUP_MODE:
            sync_state["scan_seen_ids"] = sorted(seen_ids or set())
            if finalize_initial:
                sync_state["mode"] = _DELTA_MODE
                sync_state["initial_scan_finalized_at"] = utc_now_iso()
                sync_state.pop("baseline_delta_link", None)
        sync_state["last_run_entry_count"] = len(delta.get("items") or [])
        sync_state["last_relevant_count"] = len(processed["normalized"])
        sync_state["last_sync_at"] = utc_now_iso()
        delta_stats = dict(sync_state.get("delta_stats") or {})
        for key, value in processed["counters"].items():
            delta_stats[key] = int(delta_stats.get(key) or 0) + int(value or 0)
        sync_state["delta_stats"] = delta_stats
        return {
            "resync": False,
            "normalized": processed["normalized"],
            "remove_ids": processed["remove_ids"],
            "finalize_initial": finalize_initial,
            "completed": not bool(next_link),
        }

    @staticmethod
    def _clean_scan_state(sync_state: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(sync_state)
        for key in (
            "scan_queue",
            "scan_queued_folder_ids",
            "scan_seen_ids",
            "baseline_delta_link",
        ):
            cleaned.pop(key, None)
        cleaned["mode"] = _DELTA_MODE
        return cleaned

    async def async_sync_trip(
        self, trip_id: str, *, full_rescan: bool = False
    ) -> dict[str, Any]:
        if not self.onedrive.connected:
            raise ValidationError("OneDrive ist nicht verbunden")
        async with self._sync_lock:
            payload = await self.manager.async_get_assistant_payload(trip_id)
            days = _all_days(payload)
            window = _trip_date_window(days, self.date_buffer_days)
            if window is None:
                return {
                    "ok": True,
                    "trip_id": trip_id,
                    "added": 0,
                    "updated": 0,
                    "removed": 0,
                    "total": 0,
                    "skipped": 0,
                    "message": "Die Reise besitzt noch keine datierten Reisetage.",
                }
            range_key = f"{window[0].isoformat()}..{window[1].isoformat()}"
            state = await self.hass.async_add_executor_job(self.store.load, trip_id)
            sync_state = dict(state.get("media_sync") or {})
            folder = await self.onedrive.async_resolve_folder(self.folder_path)
            folder_id = str(folder.get("id") or "")
            reset_scan = (
                full_rescan
                or not self._scan_state_matches(
                    sync_state,
                    folder_id=folder_id,
                    range_key=range_key,
                    recursive_subfolders=self.recursive_subfolders,
                    date_buffer_days=self.date_buffer_days,
                )
            )
            if reset_scan:
                sync_state = await self._new_initial_scan_state(
                    folder=folder,
                    range_key=range_key,
                )

            mode = str(sync_state.get("mode") or _INITIAL_SCAN_MODE)
            if mode == _INITIAL_SCAN_MODE:
                batch = await self._initial_scan_batch(
                    sync_state,
                    window=window,
                    days=days,
                )
            else:
                batch = await self._delta_batch(
                    sync_state,
                    folder_id=folder_id,
                    window=window,
                    days=days,
                )
                if batch.get("resync"):
                    sync_state = await self._new_initial_scan_state(
                        folder=folder,
                        range_key=range_key,
                    )
                    batch = await self._initial_scan_batch(
                        sync_state,
                        window=window,
                        days=days,
                    )
                    sync_state["resync_reason"] = "delta_cursor_expired"

            normalized = list(batch.get("normalized") or [])
            remove_ids = set(batch.get("remove_ids") or set())
            sync_state.update(
                {
                    "strategy_version": _MEDIA_SYNC_STRATEGY_VERSION,
                    "folder_id": folder_id,
                    "folder_path": self.folder_path,
                    "trip_date_range": range_key,
                    "recursive_subfolders": self.recursive_subfolders,
                    "date_buffer_days": self.date_buffer_days,
                    "max_items_per_run": self.max_items_per_run,
                    "max_scan_seconds": self.max_scan_seconds,
                    "truncated": str(sync_state.get("mode") or "") != _DELTA_MODE
                    or bool(sync_state.get("next_link")),
                }
            )

            removed = 0
            if remove_ids:
                removed += await self.hass.async_add_executor_job(
                    partial(
                        self.store.remove_media_by_provider_ids,
                        trip_id,
                        remove_ids,
                        sync_state=sync_state,
                    )
                )
            result = await self.hass.async_add_executor_job(
                partial(
                    self.store.upsert_media,
                    trip_id,
                    normalized,
                    sync_state=sync_state,
                )
            )

            if batch.get("finalize_initial"):
                current_state = await self.hass.async_add_executor_job(
                    self.store.load, trip_id
                )
                seen_ids = {
                    str(item)
                    for item in list(sync_state.get("scan_seen_ids") or [])
                    if str(item)
                }
                stale_ids = {
                    str(item.get("provider_item_id") or "")
                    for item in current_state.get("media", [])
                    if str(item.get("provider_item_id") or "")
                    and str(item.get("provider_item_id") or "") not in seen_ids
                }
                final_sync_state = self._clean_scan_state(sync_state)
                final_sync_state["truncated"] = False
                if stale_ids:
                    removed += await self.hass.async_add_executor_job(
                        partial(
                            self.store.remove_media_by_provider_ids,
                            trip_id,
                            stale_ids,
                            sync_state=final_sync_state,
                        )
                    )
                else:
                    await self.hass.async_add_executor_job(
                        partial(
                            self.store.upsert_media,
                            trip_id,
                            [],
                            sync_state=final_sync_state,
                        )
                    )
                sync_state = final_sync_state
                final_state = await self.hass.async_add_executor_job(
                    self.store.load, trip_id
                )
                result["total"] = len(final_state.get("media", []))

            scan_stats = dict(sync_state.get("scan_stats") or {})
            mode = str(sync_state.get("mode") or _DELTA_MODE)
            queue = list(sync_state.get("scan_queue") or [])
            scan_in_progress = mode != _DELTA_MODE or bool(sync_state.get("next_link"))
            if (
                int(result.get("added") or 0)
                or int(result.get("updated") or 0)
                or removed
            ):
                self.hass.async_create_task(self._on_media_changed(trip_id))

            progress = {
                "phase": mode,
                "folders_discovered": int(scan_stats.get("folders_discovered") or 0),
                "folders_examined": int(scan_stats.get("folders_examined") or 0),
                "folders_completed": int(scan_stats.get("folders_completed") or 0),
                "folders_remaining": len(queue),
                "folders_skipped": int(scan_stats.get("folders_skipped") or 0),
                "hidden_folders_skipped": int(scan_stats.get("hidden_folders_skipped") or 0),
                "dated_folders_skipped": int(scan_stats.get("dated_folders_skipped") or 0),
                "entries_examined": int(scan_stats.get("entries_examined") or 0),
                "photo_files_examined": int(scan_stats.get("photo_files_examined") or 0),
                "relevant_photos": int(scan_stats.get("relevant_photos") or 0),
                "outside_window_skipped": int(scan_stats.get("outside_window_skipped") or 0),
                "current_folder": scan_stats.get("current_folder"),
                "last_run_duration_ms": int(scan_stats.get("last_run_duration_ms") or 0),
                "last_run_limit_reason": scan_stats.get("last_run_limit_reason"),
            }
            return {
                "ok": True,
                "trip_id": trip_id,
                **result,
                "removed": removed,
                "skipped": int(scan_stats.get("outside_window_skipped") or 0),
                "folder": folder.get("name"),
                "truncated": scan_in_progress,
                "scan_in_progress": scan_in_progress,
                "sync_mode": mode,
                "trip_date_range": range_key,
                "progress": progress,
            }

    def _assignment_for(self, media: dict[str, Any], days: list[dict[str, Any]]) -> dict[str, Any]:
        taken = _parse_datetime(media.get("taken_at") or media.get("created_at"))
        if taken is None:
            return {"assignment_status": "unassigned", "confidence": 0.0}
        local_date = dt_util.as_local(taken).date()
        exact_days = [day for day in days if _day_date(day) == local_date]
        nearby_days = [
            day
            for day in days
            if (day_date := _day_date(day)) is not None
            and abs((day_date - local_date).days) <= 1
        ]
        media_coord = _coordinate(media.get("location"))
        if media_coord is None:
            if not exact_days:
                return {"assignment_status": "unassigned", "confidence": 0.0}
            day_id = str(exact_days[0].get("id") or "")
            return {
                "linked_day_id": day_id or None,
                "assignment_status": "suggested",
                "confidence": 0.55,
            }

        stop_candidates: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for day in nearby_days:
            for stop in _stops(day):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    stop_candidates.append((_distance_m(media_coord, coord), day, stop))
        if stop_candidates:
            stop_candidates.sort(key=lambda item: item[0])
            distance, day, stop = stop_candidates[0]
            day_id = str(day.get("id") or "")
            stop_id = str(stop.get("id") or "")
            same_day = _day_date(day) == local_date
            # The nearest thing it could otherwise be. Any other stop
            # counts, including one on a neighbouring day - a photograph
            # two hundred metres from tomorrow's campsite is ambiguous
            # about the day, and that is exactly the doubt worth keeping.
            runner_up = stop_candidates[1][0] if len(stop_candidates) > 1 else None
            alone = runner_up is None or (
                runner_up >= distance * _CLEAR_SEPARATION
                and runner_up - distance >= _CLEAR_MARGIN_M
            )
            if same_day and (
                distance <= _AUTOMATIC_RADIUS_M
                or (distance <= _CLEAR_RADIUS_M and alone)
            ):
                return {
                    "linked_day_id": day_id or None,
                    "linked_stop_id": stop_id or None,
                    "assignment_status": "automatic",
                    "confidence": round(max(0.75, 1 - distance / 3000), 4),
                    "distance_m": distance,
                }
            if distance <= _SUGGESTED_RADIUS_M:
                return {
                    "linked_day_id": day_id or None,
                    "linked_stop_id": stop_id or None,
                    "assignment_status": "suggested",
                    "confidence": round(max(0.45, 1 - distance / 10_000), 4),
                    "distance_m": distance,
                }
        if exact_days:
            return {
                "linked_day_id": str(exact_days[0].get("id") or "") or None,
                "assignment_status": "suggested",
                "confidence": 0.45,
            }
        return {"assignment_status": "unassigned", "confidence": 0.0}

    async def async_reassign_media(self, trip_id: str) -> dict[str, Any]:
        """Re-decide the stored assignments with today's rule.

        Nothing is fetched. The photographs, their coordinates and their
        timestamps are already here; only the question "which stop is
        this?" is asked again. That makes it cheap enough to offer as a
        button rather than as a full re-synchronisation of OneDrive - and
        it is the only way an improved rule reaches the trip somebody is
        looking at right now.

        Assignments made by hand are left alone. See `reassign_media`.
        """
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Neuzuordnung wurde keine Reise ausgewählt")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        result = await self.hass.async_add_executor_job(
            self.store.reassign_media, trip_id, lambda media: self._assignment_for(media, days)
        )
        _LOGGER.debug(
            "Fotozuordnung für %s neu berechnet: %s von %s geändert",
            trip_id,
            result.get("changed"),
            result.get("total"),
        )
        return result

    async def async_update_media(self, trip_id: str, media_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "linked_day_id",
            "linked_stop_id",
            "assignment_status",
            "caption",
            "is_cover",
            "is_day_cover",
            "is_trip_cover",
        }
        filtered = {key: value for key, value in patch.items() if key in allowed}
        if "linked_stop_id" in filtered and filtered.get("linked_stop_id"):
            payload = await self.manager.async_get_assistant_payload(trip_id)
            found_day = None
            for day in _all_days(payload):
                if any(str(stop.get("id") or "") == str(filtered["linked_stop_id"]) for stop in _stops(day)):
                    found_day = str(day.get("id") or "")
                    break
            if not found_day:
                raise ValidationError("Der ausgewählte Stopp existiert nicht mehr")
            filtered["linked_day_id"] = found_day
        if not filtered.get("linked_day_id"):
            filtered["linked_day_id"] = None
            filtered["linked_stop_id"] = None
            filtered["assignment_status"] = "unassigned"
        else:
            filtered.setdefault("assignment_status", "manual")
        return await self.hass.async_add_executor_job(self.store.update_media, trip_id, media_id, filtered)

    async def async_delete_media(self, trip_id: str, media_id: str) -> dict[str, Any]:
        await self.hass.async_add_executor_job(self.store.delete_media, trip_id, media_id)
        return {"ok": True}
