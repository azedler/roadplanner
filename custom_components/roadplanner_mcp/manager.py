"""Async Home Assistant facade for synchronous Roadplanner stores."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .handoff import HandoffStore
from .navigation import decorate_panel_navigation
from .roadplanner import (
    RevisionConflictError,
    RoadplannerError,
    RoadplannerStore,
    ValidationError,
)
from .routing import (
    OSRMRoutingClient,
    RoutingError,
    combine_route_segments,
    disconnected_route_segment,
    ferry_route_segment,
    route_input_hash,
    split_route_segments,
)

UpdateCallback = Callable[[dict[str, Any]], None]


class RoadplannerManager:
    """Serialize all file operations and keep Home Assistant responsive."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: RoadplannerStore,
        handoff_store: HandoffStore,
        *,
        router: OSRMRoutingClient,
        auto_scan_handoffs: bool = True,
        auto_apply_changesets: bool = False,
        allow_destructive_auto_apply: bool = False,
    ) -> None:
        self.hass = hass
        self.store = store
        self.handoff_store = handoff_store
        self.router = router
        self.auto_scan_handoffs = auto_scan_handoffs
        self.auto_apply_changesets = auto_apply_changesets
        self.allow_destructive_auto_apply = allow_destructive_auto_apply
        self._lock = asyncio.Lock()
        self._update_callback: UpdateCallback | None = None

    def set_update_callback(self, callback: UpdateCallback | None) -> None:
        self._update_callback = callback

    def _load_payload_sync(self) -> dict[str, Any]:
        payload = self.store.load_coordinator_payload()
        pending = self.handoff_store.list_pending(limit=100)
        payload["pending_handoff_count"] = pending["total"]
        payload["handoff_status_counts"] = pending["status_counts"]
        return payload

    def _auto_apply_sync(
        self,
        *,
        handoff_id: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "enabled": self.auto_apply_changesets,
            "applied": [],
            "review_required": [],
            "conflicts": [],
            "failed": [],
        }
        if not self.auto_apply_changesets:
            return result

        if handoff_id is not None:
            envelope = self.handoff_store.get_pending(handoff_id)
            candidates = [envelope] if envelope.get("status") == "pending" else []
        else:
            candidates = self.handoff_store.list_auto_applicable(limit=limit)

        for envelope in candidates:
            current_id = envelope["id"]
            if not envelope.get("automatic_eligible"):
                updated = self.handoff_store.update_pending(
                    handoff_id=current_id,
                    status="review_required",
                    error=(
                        "ChangeSet ist nicht für die automatische Übernahme "
                        "freigegeben oder enthält offene Fragen."
                    ),
                )
                result["review_required"].append(updated)
                continue
            if (
                envelope.get("destructive")
                and not self.allow_destructive_auto_apply
            ):
                updated = self.handoff_store.update_pending(
                    handoff_id=current_id,
                    status="review_required",
                    error=(
                        "ChangeSet enthält Löschoperationen. Automatische "
                        "destruktive Änderungen sind deaktiviert."
                    ),
                )
                result["review_required"].append(updated)
                continue
            try:
                apply_result = self.store.apply_changeset(
                    changeset=envelope["changeset"],
                    actor=f"changeset:{envelope.get('source', 'external')}",
                    expected_revision=envelope["base_revision"],
                )
            except RevisionConflictError as err:
                updated = self.handoff_store.update_pending(
                    handoff_id=current_id,
                    status="conflict",
                    error=str(err),
                )
                result["conflicts"].append(updated)
            except RoadplannerError as err:
                updated = self.handoff_store.update_pending(
                    handoff_id=current_id,
                    status="failed",
                    error=str(err),
                )
                result["failed"].append(updated)
            else:
                applied = self.handoff_store.mark_applied(
                    handoff_id=current_id,
                    result=apply_result,
                )
                result["applied"].append(applied)
        return result

    def _refresh_sync(self) -> dict[str, Any]:
        scan_result = (
            self.handoff_store.scan_inbox()
            if self.auto_scan_handoffs
            else {
                "imported_count": 0,
                "failed_count": 0,
                "imported": [],
                "failed": [],
            }
        )
        auto_apply_result = self._auto_apply_sync()
        payload = self._load_payload_sync()
        payload["handoff_scan_result"] = {
            "imported_count": scan_result["imported_count"],
            "failed_count": scan_result["failed_count"],
        }
        payload["handoff_auto_apply_result"] = {
            "applied_count": len(auto_apply_result["applied"]),
            "review_required_count": len(
                auto_apply_result["review_required"]
            ),
            "conflict_count": len(auto_apply_result["conflicts"]),
            "failed_count": len(auto_apply_result["failed"]),
        }
        return payload

    async def async_initialize(self, *, create_if_missing: bool) -> dict[str, Any]:
        async with self._lock:
            await self.hass.async_add_executor_job(self.handoff_store.initialize)
            return await self.hass.async_add_executor_job(
                partial(self.store.initialize, create_if_missing=create_if_missing)
            )

    async def async_refresh_payload(self) -> dict[str, Any]:
        """Scan the handoff folder, apply approved ChangeSets, and load state."""
        async with self._lock:
            return await self.hass.async_add_executor_job(self._refresh_sync)

    async def async_load_payload(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self._load_payload_sync)

    @staticmethod
    def _filter_handoffs_for_trip(
        handoffs: dict[str, Any],
        trip_id: str,
    ) -> dict[str, Any]:
        """Return the compact pending handoffs belonging to one trip."""
        selected = [
            item
            for item in handoffs.get("handoffs", [])
            if item.get("trip_id") == trip_id
        ]
        counts: dict[str, int] = {}
        for item in selected:
            status = str(item.get("status") or "pending")
            counts[status] = counts.get(status, 0) + 1
        return {
            "count": len(selected),
            "total": len(selected),
            "status_counts": counts,
            "handoffs": selected,
            "truncated": False,
        }

    def _panel_payload_sync(
        self,
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Return one consistent, bounded snapshot for the frontend panel."""
        trip_list = self.store.list_trips()
        selected_trip_id = trip_id or trip_list["active_trip"]
        for _attempt in range(2):
            summary = self.store.get_trip_summary(
                trip_id=selected_trip_id,
                today=dt_util.now().date(),
            )
            days = self.store.get_days(
                trip_id=selected_trip_id,
                offset=0,
                limit=60,
                include_stops=True,
            )
            decorate_panel_navigation(days)
            if summary["revision"] == days["revision"]:
                all_handoffs = self.handoff_store.list_pending(limit=100)
                return {
                    "summary": summary,
                    "days": days,
                    "handoffs": self._filter_handoffs_for_trip(
                        all_handoffs,
                        selected_trip_id,
                    ),
                    "trips": trip_list,
                    "selected_trip_id": selected_trip_id,
                    "active_trip_id": trip_list["active_trip"],
                    "selected_is_active": (
                        selected_trip_id == trip_list["active_trip"]
                    ),
                }
        raise ValidationError(
            "Die Route wurde während des Ladens extern verändert. "
            "Bitte erneut laden."
        )

    async def async_get_panel_payload(
        self,
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Return panel data while serializing it with all mutations."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self._panel_payload_sync, trip_id)
            )

    def _assistant_payload_sync(
        self,
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Return up to 180 revision-consistent travel days for the assistant."""
        trip_list = self.store.list_trips()
        selected_trip_id = trip_id or trip_list["active_trip"]
        for _attempt in range(2):
            summary = self.store.get_trip_summary(
                trip_id=selected_trip_id,
                today=dt_util.now().date(),
            )
            combined_days: list[dict[str, Any]] = []
            total = 0
            has_more = False
            page_revision = summary["revision"]
            consistent = True
            for offset in (0, 60, 120):
                page = self.store.get_days(
                    trip_id=selected_trip_id,
                    offset=offset,
                    limit=60,
                    include_stops=True,
                )
                if page["revision"] != summary["revision"]:
                    consistent = False
                    break
                page_revision = page["revision"]
                total = int(page.get("total", 0))
                combined_days.extend(page.get("days", []))
                has_more = bool(page.get("has_more"))
                if not has_more:
                    break
            else:
                has_more = total > len(combined_days)
            if consistent and page_revision == summary["revision"] and (combined_days or total == 0):
                days = {
                    "revision": summary["revision"],
                    "offset": 0,
                    "limit": 180,
                    "total": total,
                    "days": combined_days[:180],
                    "has_more": has_more or total > len(combined_days[:180]),
                }
                return {
                    "summary": summary,
                    "days": days,
                    "selected_trip_id": selected_trip_id,
                    "active_trip_id": trip_list["active_trip"],
                    "selected_is_active": selected_trip_id == trip_list["active_trip"],
                }
        raise ValidationError(
            "Die Route wurde während des Ladens extern verändert. Bitte erneut laden."
        )

    async def async_get_assistant_payload(
        self,
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """Return up to 180 trip days for question-aware assistant context."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self._assistant_payload_sync, trip_id)
            )

    async def async_load_trip(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.load_trip)

    async def async_get_trip_summary(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.store.get_trip_summary, today=dt_util.now().date())
            )

    async def async_get_days(self, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.store.get_days, **kwargs)
            )

    async def async_get_day(self, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.store.get_day, **kwargs)
            )

    async def async_search_stops(self, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.store.search_stops, **kwargs)
            )

    async def async_list_trips(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.list_trips)

    async def async_export_trip(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.export_trip)

    async def async_create_backup(self, reason: str = "manual") -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.store.create_backup, reason)
            )

    async def async_export_context(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.write_context)

    async def async_get_context_payload(self) -> dict[str, Any]:
        """Return bounded route context without writing a derived file."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self.store.get_context_payload
            )

    async def async_get_context_markdown(self) -> dict[str, Any]:
        """Return bounded Markdown context without writing a derived file."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self.store.get_context_markdown
            )


    async def _async_calculate_routes(
        self,
        *,
        trip_id: str | None,
        day_ids: list[str] | None,
        actor: str,
        expected_revision: int,
        force: bool,
        fail_if_single_error: bool,
    ) -> dict[str, Any]:
        """Calculate routes outside the store lock and commit them atomically."""
        if not self.router.configured:
            raise RoutingError(
                "Die Straßenroutenberechnung ist nicht aktiviert. "
                "Aktiviere sie in den Roadplanner-Optionen."
            )
        async with self._lock:
            plan = await self.hass.async_add_executor_job(
                partial(
                    self.store.get_routing_plan,
                    trip_id=trip_id,
                    day_ids=day_ids,
                )
            )
        if plan["revision"] != expected_revision:
            raise RevisionConflictError(expected_revision, plan["revision"])

        calculated: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for day in plan["days"]:
            points = day["points"]
            if len(points) < 2:
                skipped.append(
                    {
                        "day_id": day["day_id"],
                        "sequence": day["sequence"],
                        "reason": "Für diesen Tag sind weniger als zwei GPS-Punkte vorhanden.",
                        "point_count": len(points),
                        "missing_stop_count": len(day["missing_stops"]),
                    }
                )
                continue
            input_hash = route_input_hash(points, self.router.profile)
            existing = day.get("existing_routing") or {}
            existing_provider = existing.get("road_provider") or existing.get("provider")
            if (
                not force
                and existing.get("status") in {"calculated", "partial"}
                and existing.get("input_hash") == input_hash
                and existing_provider == self.router.name
                and existing.get("profile") == self.router.profile
            ):
                skipped.append(
                    {
                        "day_id": day["day_id"],
                        "sequence": day["sequence"],
                        "reason": "Die gespeicherte Route ist bereits aktuell.",
                        "point_count": len(points),
                        "missing_stop_count": len(day["missing_stops"]),
                    }
                )
                continue
            try:
                segment_results: list[dict[str, Any]] = []
                for segment in split_route_segments(points):
                    mode = segment.get("mode")
                    segment_points = segment.get("points") or []
                    if mode == "driving":
                        segment_hash = route_input_hash(segment_points, self.router.profile)
                        road_result = await self.router.async_calculate(
                            segment_points,
                            input_hash=segment_hash,
                        )
                        road_result["mode"] = "driving"
                        segment_results.append(road_result)
                    elif mode == "ferry":
                        segment_results.append(ferry_route_segment(segment_points))
                    else:
                        segment_results.append(disconnected_route_segment(segment))
                routing = combine_route_segments(
                    points=points,
                    segment_results=segment_results,
                    provider=self.router.name,
                    profile=self.router.profile,
                    endpoint_host=self.router.endpoint_host,
                    input_hash=input_hash,
                    warnings=day.get("route_warnings") or [],
                )
            except RoadplannerError as err:
                failure = {
                    "day_id": day["day_id"],
                    "sequence": day["sequence"],
                    "error": str(err)[:500],
                }
                failures.append(failure)
                if fail_if_single_error:
                    raise
                continue
            calculated.append(
                {
                    "day_id": day["day_id"],
                    "routing": routing,
                    "missing_stops": day["missing_stops"],
                }
            )

        if not calculated:
            if failures and fail_if_single_error:
                raise RoutingError(failures[0]["error"])
            return {
                "changed": False,
                "revision": plan["revision"],
                "trip_id": plan["trip_id"],
                "calculated": [],
                "skipped": skipped,
                "failures": failures,
                "route_metrics": plan["route_metrics"],
                "router": self.router.health_snapshot(),
            }

        async with self._lock:
            result = await self.hass.async_add_executor_job(
                partial(
                    self.store.apply_routing_results,
                    results=calculated,
                    actor=actor,
                    expected_revision=plan["revision"],
                    expected_trip_id=plan["trip_id"],
                )
            )
            payload = (
                await self.hass.async_add_executor_job(self._load_payload_sync)
                if result.get("changed")
                else None
            )
        if payload is not None and self._update_callback is not None:
            self._update_callback(payload)
        result.update(
            {
                "trip_id": plan["trip_id"],
                "calculated": result.pop("routing_results", []),
                "skipped": skipped,
                "failures": failures,
                "router": self.router.health_snapshot(),
            }
        )
        return result

    async def async_calculate_day_route(
        self,
        *,
        trip_id: str | None,
        day_id: str,
        actor: str,
        expected_revision: int,
        force: bool = False,
    ) -> dict[str, Any]:
        return await self._async_calculate_routes(
            trip_id=trip_id,
            day_ids=[day_id],
            actor=actor,
            expected_revision=expected_revision,
            force=force,
            fail_if_single_error=True,
        )

    async def async_calculate_trip_routes(
        self,
        *,
        trip_id: str | None,
        actor: str,
        expected_revision: int,
        force: bool = False,
    ) -> dict[str, Any]:
        return await self._async_calculate_routes(
            trip_id=trip_id,
            day_ids=None,
            actor=actor,
            expected_revision=expected_revision,
            force=force,
            fail_if_single_error=False,
        )

    async def async_set_active_trip(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.set_active_trip, **kwargs)

    async def async_update_trip(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.update_trip, **kwargs)

    async def async_add_day(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.add_day, **kwargs)

    async def async_update_day(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.update_day, **kwargs)

    async def async_remove_day(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.remove_day, **kwargs)

    async def async_add_stop(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.add_stop, **kwargs)

    async def async_update_stop(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.update_stop, **kwargs)

    async def async_remove_stop(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(self.store.remove_stop, **kwargs)

    async def async_adopt_external_changes(self, **kwargs: Any) -> dict[str, Any]:
        return await self._async_mutate(
            self.store.adopt_external_changes,
            **kwargs,
        )

    async def async_list_handoffs(self, *, limit: int = 50) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.handoff_store.list_pending, limit=limit)
            )

    async def async_get_handoff(self, handoff_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                partial(self.handoff_store.get, handoff_id)
            )

    async def async_get_handoff_folders(self) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self.handoff_store.folder_info
            )

    @staticmethod
    def _assert_handoff_trip(
        envelope: dict[str, Any],
        expected_trip_id: str | None,
    ) -> None:
        if expected_trip_id is None:
            return
        if envelope.get("trip_id") != expected_trip_id:
            raise ValidationError(
                "Die Übergabe gehört nicht zur ausgewählten Reise"
            )

    async def async_preview_handoff(
        self,
        handoff_id: str,
        *,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            envelope = await self.hass.async_add_executor_job(
                partial(self.handoff_store.get_pending, handoff_id)
            )
            self._assert_handoff_trip(envelope, expected_trip_id)
            preview = await self.hass.async_add_executor_job(
                partial(self.store.preview_changeset, envelope["changeset"])
            )
        return {"handoff_id": handoff_id, "preview": preview}

    async def async_ingest_handoff(self, **kwargs: Any) -> dict[str, Any]:
        async with self._lock:
            result = await self.hass.async_add_executor_job(
                partial(self.handoff_store.ingest, **kwargs)
            )
            if self.auto_apply_changesets and not result.get("duplicate"):
                await self.hass.async_add_executor_job(
                    partial(self._auto_apply_sync, handoff_id=result["id"])
                )
                result = await self.hass.async_add_executor_job(
                    partial(self.handoff_store.get, result["id"])
                )
                result = self.handoff_store.compact(result)
            payload = await self.hass.async_add_executor_job(
                self._load_payload_sync
            )
        if self._update_callback is not None:
            self._update_callback(payload)
        return result

    async def async_ingest_external_changeset(
        self,
        *,
        changeset: dict[str, Any],
        title: str,
        source: str,
        external_id: str | None,
        metadata: dict[str, Any],
        source_payload_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Validate and enqueue an external ChangeSet without auto-applying it."""
        async with self._lock:
            duplicate = await self.hass.async_add_executor_job(
                partial(
                    self.handoff_store.check_duplicate,
                    changeset=changeset,
                    source=source,
                    external_id=external_id,
                    source_payload_sha256=source_payload_sha256,
                )
            )
            if duplicate is not None:
                return {
                    "handoff": duplicate,
                    "preview": None,
                    "duplicate": True,
                }

            preview = await self.hass.async_add_executor_job(
                partial(self.store.inspect_changeset_for_import, changeset)
            )
            preview_status = preview.get("status")
            if preview_status == "wrong_trip":
                raise ValidationError(str(preview.get("reason")))
            initial_status = "review_required"
            initial_error = None
            if preview_status == "revision_conflict":
                initial_status = "conflict"
                initial_error = str(preview.get("reason"))
                metadata = {
                    **metadata,
                    "revision_conflict": {
                        "expected_revision": preview.get("current_revision"),
                        "received_revision": changeset.get("base_revision"),
                    },
                }
            elif preview_status != "ready":
                raise ValidationError(
                    str(preview.get("reason") or "ChangeSet ist nicht anwendbar")
                )

            result = await self.hass.async_add_executor_job(
                partial(
                    self.handoff_store.ingest,
                    changeset=changeset,
                    title=title,
                    source=source,
                    content_type="application/json",
                    external_id=external_id,
                    metadata=metadata,
                    source_payload_sha256=source_payload_sha256,
                    initial_status=initial_status,
                    initial_error=initial_error,
                )
            )
            payload = await self.hass.async_add_executor_job(
                self._load_payload_sync
            )
        if self._update_callback is not None:
            self._update_callback(payload)
        return {
            "handoff": result,
            "preview": preview,
            "duplicate": bool(result.get("duplicate")),
        }

    async def async_apply_handoff(
        self,
        *,
        handoff_id: str,
        actor: str,
        expected_revision: int,
        confirm_destructive: bool = False,
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            envelope = await self.hass.async_add_executor_job(
                partial(self.handoff_store.get_pending, handoff_id)
            )
            self._assert_handoff_trip(envelope, expected_trip_id)
            if envelope.get("destructive") and not confirm_destructive:
                raise ValidationError(
                    "ChangeSet enthält Löschoperationen. Zum Anwenden muss "
                    "confirm_destructive=true gesetzt sein."
                )
            apply_result = await self.hass.async_add_executor_job(
                partial(
                    self.store.apply_changeset,
                    changeset=envelope["changeset"],
                    actor=actor,
                    expected_revision=expected_revision,
                )
            )
            applied = await self.hass.async_add_executor_job(
                partial(
                    self.handoff_store.mark_applied,
                    handoff_id=handoff_id,
                    result=apply_result,
                )
            )
            payload = await self.hass.async_add_executor_job(
                self._load_payload_sync
            )
        if self._update_callback is not None:
            self._update_callback(payload)
        return {"handoff": applied, "apply_result": apply_result}

    async def async_archive_handoff(
        self,
        *,
        handoff_id: str,
        resolution: str,
        note: str = "",
        expected_trip_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            envelope = await self.hass.async_add_executor_job(
                partial(self.handoff_store.get_pending, handoff_id)
            )
            self._assert_handoff_trip(envelope, expected_trip_id)
            result = await self.hass.async_add_executor_job(
                partial(
                    self.handoff_store.archive,
                    handoff_id=handoff_id,
                    resolution=resolution,
                    note=note,
                )
            )
            payload = await self.hass.async_add_executor_job(
                self._load_payload_sync
            )
        if self._update_callback is not None:
            self._update_callback(payload)
        return result

    async def async_scan_handoffs(self) -> dict[str, Any]:
        async with self._lock:
            scan_result = await self.hass.async_add_executor_job(
                self.handoff_store.scan_inbox
            )
            auto_result = await self.hass.async_add_executor_job(
                self._auto_apply_sync
            )
            payload = await self.hass.async_add_executor_job(
                self._load_payload_sync
            )
        if self._update_callback is not None:
            self._update_callback(payload)
        return {"scan": scan_result, "auto_apply": auto_result}

    async def _async_mutate(
        self,
        method: Callable[..., dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        async with self._lock:
            result = await self.hass.async_add_executor_job(partial(method, **kwargs))
            payload = (
                await self.hass.async_add_executor_job(self._load_payload_sync)
                if result.get("changed")
                else None
            )
        if payload is not None and self._update_callback is not None:
            self._update_callback(payload)
        return result

    async def _push_current_state(self) -> None:
        if self._update_callback is None:
            return
        payload = await self.async_load_payload()
        self._update_callback(payload)
