"""The one place that turns a recording into stored film moments.

Everything that touches the world lives here: downloading a video, cutting
proxies, calling the provider, writing the store. The decisions do not -
they are in `video_orchestration`, which cannot call anything, so the
expensive parts can be reasoned about and tested apart from the parts
that cost money.

The order matters and is the whole design:

    stored answer?  ->  yes: nothing happens, nothing is spent
                        no:  original -> analysis proxy -> provider
                             -> validated -> placed on the recording's
                             timeline -> stored

**A render never enters this file.** The film reads stored segments; if
there are none it has no clips, which is a normal film and not an error.
The only entry points are a person pressing a button that says what it
costs.

What leaves the house
---------------------

One downscaled, silent window per call, cut from a copy of the original
that is deleted as soon as the proxies exist. No day, no place, no name,
no date - the model is shown a clip and asked when the good part is,
because everything it is told is something it can hand back as if it had
seen it.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
from typing import Any

from homeassistant.core import HomeAssistant

from .assistant_provider import AssistantVideoInput
from .experience_store import ExperienceStore, utc_now_iso
from .roadplanner import RoadplannerError, ValidationError
from .video_analysis import (
    ANALYSIS_VERSION,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_request,
    parse_analysis,
)
from .video_asset import VideoAssetError
from .video_orchestration import (
    MAX_ANALYSES_PER_RUN,
    analysis_plan,
    merge_overlapping,
    normalise_segment,
)
from .video_prefilter import select_candidates
from .video_proxy import (
    VideoProxyError,
    async_cut_analysis_proxy,
    async_cut_render_proxy,
)

_LOGGER = logging.getLogger(__name__)

# Where the temporary original and the proxies live while they are needed.
# Under /share because that is the one place both the integration and the
# renderer can see, and cleaned aggressively because a family's original
# footage sitting in a working directory is not a working directory, it
# is a copy of their holiday.
WORK_DIRNAME = "roadplanner-video-work"

# The schema version the cache key is built with. Bumping this is how a
# changed contract invalidates old answers - the only correct way, since
# an answer to a different question must never be reused.
SCHEMA_VERSION = ANALYSIS_VERSION


class VideoCurationService:
    """Analyse a trip's videos once, deliberately, and remember the answer."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        provider: Any,
        *,
        share_root: Path,
        media_source: Any = None,
    ) -> None:
        self.hass = hass
        self.store = store
        self._provider = provider
        self._share_root = Path(share_root)
        # Whatever can turn a stored media record into a download URL.
        # Injected rather than imported so a test can hand over a local
        # file and never touch a network.
        self._media_source = media_source

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._provider, "video_analysis_enabled", False))

    # --- free, read-only -------------------------------------------------

    def offer(self, trip_id: str, *, force: bool = False) -> dict[str, Any]:
        """What an analysis run would do, and what it would cost. Free.

        Read-only by construction and separate from the run for one
        reason: nobody should discover the price after paying it. The
        numbers here come from the same planner the run uses, so what is
        promised is what happens.
        """
        state = self.store.load(trip_id)
        videos = [
            item
            for item in state.get("media") or []
            if isinstance(item, dict) and str(item.get("media_type") or "") == "video"
        ]
        candidates, rejected = select_candidates(videos)
        stored = state.get("video_analyses") or {}
        plan = analysis_plan(
            candidates,
            stored,
            model=self._model_name(),
            schema_version=SCHEMA_VERSION,
            force=force,
        )
        return {
            "videos_found": len(videos),
            "technically_usable": len({str(entry.get("id")) for entry in candidates}),
            "skipped": [
                {
                    "media_id": str(entry.get("id") or ""),
                    "name": str(entry.get("name") or ""),
                    "reason": str(entry.get("skipped_reason") or ""),
                }
                for entry in rejected
            ],
            "windows_new": plan["new_count"],
            "windows_cached": plan["cached_count"],
            "over_limit": plan["over_limit"],
            "analysis_seconds": plan["analysis_seconds"],
            "estimated_tokens": plan["estimated_tokens"],
            "estimated_eur": plan["estimated_eur"],
            "enabled": self.enabled,
            "segments_stored": sum(
                len((entry or {}).get("segments") or [])
                for entry in stored.values()
                if isinstance(entry, dict)
            ),
        }

    def segments_by_day(self, trip_id: str) -> dict[str, list[dict[str, Any]]]:
        """Every stored moment, grouped by the day it belongs to. Free.

        This is what the film reads. It calls nothing, so a render can
        never produce a bill - and a trip whose videos were never analysed
        simply returns nothing.
        """
        state = self.store.load(trip_id)
        by_media = {
            str(item.get("id") or ""): item
            for item in state.get("media") or []
            if isinstance(item, dict)
        }
        found: dict[str, list[dict[str, Any]]] = {}
        for entry in (state.get("video_analyses") or {}).values():
            if not isinstance(entry, dict):
                continue
            media_id = str(entry.get("media_id") or "")
            record = by_media.get(media_id)
            if record is None:
                # The recording is gone from the library; its answer is
                # about something nobody can play any more.
                continue
            day_id = str(record.get("linked_day_id") or "")
            if not day_id:
                continue
            for segment in entry.get("segments") or []:
                if isinstance(segment, dict):
                    found.setdefault(day_id, []).append(
                        {**segment, "media_id": media_id}
                    )
        return {
            day_id: merge_overlapping(segments) for day_id, segments in found.items()
        }

    # --- the paid run ----------------------------------------------------

    async def async_analyze(
        self, trip_id: str, *, force: bool = False, limit: int = MAX_ANALYSES_PER_RUN
    ) -> dict[str, Any]:
        """Look at the windows that have no answer yet. Costs money.

        Reached only from a button that showed the price first. Every
        window is independent: one failure is one window, never the run,
        because a family's ten recordings should not be lost to the
        eleventh being unreadable.
        """
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Videoanalyse wurde keine Reise ausgewählt")
        if not self.enabled:
            raise ValidationError(
                "Die KI-Videoanalyse ist ausgeschaltet - sie überträgt Ausschnitte "
                "an Google und wird deshalb bewusst eingeschaltet"
            )

        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        videos = [
            item
            for item in state.get("media") or []
            if isinstance(item, dict) and str(item.get("media_type") or "") == "video"
        ]
        candidates, _rejected = select_candidates(videos)
        plan = analysis_plan(
            candidates,
            state.get("video_analyses") or {},
            model=self._model_name(),
            schema_version=SCHEMA_VERSION,
            force=force,
            limit=limit,
        )

        work = self._share_root / WORK_DIRNAME
        analysed = 0
        failed: list[dict[str, Any]] = []
        segments_found = 0
        # One download per RECORDING, not per window: a four-minute clip
        # offered as three windows is fetched once and cut three times.
        originals: dict[str, Path] = {}
        try:
            for window in plan["new"]:
                media_id = str(window.get("id") or "")
                try:
                    source = originals.get(media_id)
                    if source is None:
                        source = await self._async_fetch_original(window, work)
                        originals[media_id] = source
                    record = await self._async_analyze_window(
                            trip_id, window, source, work
                        )
                except (RoadplannerError, VideoProxyError, VideoAssetError, OSError) as err:
                    _LOGGER.warning("Videoanalyse fehlgeschlagen für %s: %s", media_id, err)
                    failed.append({"media_id": media_id, "reason": str(err)[:200]})
                    continue
                analysed += 1
                segments_found += len(record.get("segments") or [])
        finally:
            # The originals go, always. They were only ever here to be cut.
            shutil.rmtree(work, ignore_errors=True)

        return {
            "analysed": analysed,
            "cached": plan["cached_count"],
            "skipped": plan["over_limit"],
            "failed": failed,
            "segments": segments_found,
        }

    # --- the parts that touch the world ---------------------------------

    async def _async_fetch_original(self, window: dict[str, Any], work: Path) -> Path:
        """A copy of the recording, on disk, for as long as it takes to cut."""
        if self._media_source is None:
            raise ValidationError("Für Videos ist keine Medienquelle verbunden")
        target = work / f"{window.get('id')}.src"
        await self.hass.async_add_executor_job(target.parent.mkdir, True, True)
        await self._media_source.async_download_to(window, target)
        if not target.exists() or target.stat().st_size <= 0:
            raise ValidationError("Das Video konnte nicht geladen werden")
        return target

    async def _async_analyze_window(
        self, trip_id: str, window: dict[str, Any], source: Path, work: Path
    ) -> dict[str, Any]:
        """One window: cut it small, ask, validate, place, store."""
        start = float(window.get("segment_start") or 0.0)
        end = float(window.get("segment_end") or 0.0)
        key = str(window.get("cache_key") or "")
        proxy = work / f"{key[:32]}.proxy.mp4"
        await async_cut_analysis_proxy(source, proxy, start=start, end=end)

        request = build_request(window, start, end)
        result = await self._provider.async_analyze_video(
            system_instruction=SYSTEM_PROMPT,
            prompt=request["prompt"],
            video=AssistantVideoInput(
                video_id=str(window.get("id") or ""),
                data=proxy.read_bytes(),
                mime_type="video/mp4",
                start_offset=0.0,
                end_offset=max(0.0, end - start),
            ),
            schema=RESPONSE_SCHEMA,
        )

        # Refused rather than repaired, twice: the contract first, then
        # the recording's own bounds.
        # `value`, not `data`. The provider results carry `value`, and
        # reading `data` is a mistake this project has now made three
        # times - twice silently, because the fake in the test had been
        # given the shape its author assumed. A contract test greps for
        # it; it caught this one before it ever ran.
        parsed = parse_analysis(
            getattr(result, "value", None) or {}, window_seconds=max(0.0, end - start)
        )
        segments: list[dict[str, Any]] = []
        if parsed is not None:
            placed = normalise_segment(
                parsed,
                window_start=start,
                duration_seconds=float(window.get("duration_seconds") or 0.0),
            )
            if placed is not None:
                segments.append(placed)

        record = {
            "media_id": str(window.get("id") or ""),
            "window_start": round(start, 3),
            "window_end": round(end, 3),
            "segments": segments,
            "analysis_version": ANALYSIS_VERSION,
            "model": self._model_name(),
            "analysed_at": utc_now_iso(),
        }
        await self.hass.async_add_executor_job(
            self.store.save_video_analysis, trip_id, key, record
        )
        return record

    async def async_render_proxy(
        self, source: Path, target: Path, *, start: float, end: float
    ) -> int:
        """The piece the film plays. Free - ffmpeg only, no provider."""
        return await async_cut_render_proxy(source, target, start=start, end=end)

    def _model_name(self) -> str:
        return str(getattr(self._provider, "video_model", "") or "unknown")


__all__ = ["SCHEMA_VERSION", "VideoCurationService", "WORK_DIRNAME"]
