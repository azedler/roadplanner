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


def _size_of(path: Path) -> int:
    """Bytes on disk, or zero when there is nothing there."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


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
        video_analysis_enabled: bool = False,
    ) -> None:
        self.hass = hass
        self.store = store
        self._provider = provider
        # The opt-in, passed in rather than read off the provider. It was
        # read off the provider once: the assistant client has no such
        # attribute and never had one, so `getattr(provider, ..., False)`
        # answered "off" for every configuration, including the one where
        # the user had ticked the box. An absent answer rendered as a
        # state - and the option screen and the panel disagreed with no
        # error anywhere to explain it.
        self._video_analysis_enabled = bool(video_analysis_enabled)
        self._share_root = Path(share_root)
        # Whatever can turn a stored media record into a download URL.
        # Injected rather than imported so a test can hand over a local
        # file and never touch a network.
        self._media_source = media_source
        # Which trips have a run in flight. On the server, because that is
        # where the run is: a browser reload, a locked phone or a dropped
        # WebSocket all lose whatever the panel remembered, and the run
        # carries on regardless. A card that asks the server therefore
        # keeps telling the truth, and a second click is refused instead
        # of downloading every recording again and paying for the same
        # answers twice.
        self._running: set[str] = set()

    def is_running(self, trip_id: str) -> bool:
        return str(trip_id or "").strip() in self._running

    @property
    def media_source(self) -> Any:
        """Whatever can fetch a recording, or None when nothing can.

        Public because the film export needs it too, and it was reaching
        into `_media_source` to get it - which returns None on a
        configuration with no source and then raises AttributeError from
        inside a loop whose `except` lists three other types. One missing
        media source would have taken the whole film export down with it.
        """
        return self._media_source

    @property
    def enabled(self) -> bool:
        return self._video_analysis_enabled

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
            # Read from the server, so a reloaded page still knows.
            "running": self.is_running(trip_id),
            # And so does the last run's outcome, failures included.
            "last_run": state.get("video_run") or {},
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
        if trip_id in self._running:
            raise ValidationError(
                "Für diese Reise läuft bereits eine Videoanalyse - "
                "ein zweiter Lauf würde dieselben Aufnahmen erneut laden "
                "und dieselben Antworten ein zweites Mal bezahlen"
            )
        self._running.add(trip_id)
        try:
            return await self._async_analyze(trip_id, force=force, limit=limit)
        finally:
            self._running.discard(trip_id)

    async def _async_analyze(
        self, trip_id: str, *, force: bool, limit: int
    ) -> dict[str, Any]:
        """The run itself, once it is allowed to happen."""
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
        # Refused is not failed and not empty-handed: the model declined
        # to look. Counted apart so the card can say so - a clip that
        # silently never appears in the film is the absent answer this
        # project keeps rendering as a state.
        refused: list[dict[str, Any]] = []
        segments_found = 0
        # One download per RECORDING, not per window: a four-minute clip
        # offered as three windows is fetched once and cut three times.
        originals: dict[str, Path] = {}
        # Recordings whose download already failed once. A two-minute
        # recording is offered as three windows, and only SUCCESSES were
        # remembered - so a recording that could not be fetched was
        # fetched again for every one of its windows. With a download that
        # stalls rather than refuses, that is the stall timeout three
        # times over for one unusable file, and a run that looks stuck
        # while it is patiently repeating itself.
        unreachable: dict[str, str] = {}
        # How many windows still need each recording. A recording is
        # deleted the moment its last window is done, instead of every
        # original piling up until the run ends: seventeen phone videos
        # at a couple of hundred megabytes each is several gigabytes of
        # /share held at once, on a box that has a few to spare. The peak
        # is now one recording, not all of them.
        pending: dict[str, int] = {}
        for entry in plan["new"]:
            key_id = str(entry.get("id") or "")
            pending[key_id] = pending.get(key_id, 0) + 1
        try:
            total = len(plan["new"])
            # Logged per window, at info, because the alternative is what
            # this run actually looked like from outside: three minutes,
            # no counter moving, and nothing in the log - a run that is
            # working and a run that is wedged were the same picture. Only
            # failures were logged, so success was silence.
            _LOGGER.info(
                "Videoanalyse gestartet: %s Fenster, %s bereits beantwortet",
                total,
                plan["cached_count"],
            )
            for index, window in enumerate(plan["new"], start=1):
                media_id = str(window.get("id") or "")
                if media_id in unreachable:
                    failed.append(
                        {
                            "media_id": media_id,
                            "name": str(window.get("name") or ""),
                            "reason": unreachable[media_id],
                        }
                    )
                    await self._async_release(media_id, pending, originals)
                    continue
                try:
                    source = originals.get(media_id)
                    if source is None:
                        _LOGGER.info(
                            "Videoanalyse %s/%s: lade Aufnahme %s", index, total, media_id
                        )
                        try:
                            source = await self._async_fetch_original(window, work)
                        except (RoadplannerError, VideoAssetError, OSError) as err:
                            unreachable[media_id] = str(err)[:200]
                            raise
                        originals[media_id] = source
                    _LOGGER.info(
                        "Videoanalyse %s/%s: schneide und frage (%s)",
                        index,
                        total,
                        media_id,
                    )
                    record = await self._async_analyze_window(
                            trip_id, window, source, work
                        )
                except (RoadplannerError, VideoProxyError, VideoAssetError, OSError) as err:
                    _LOGGER.warning("Videoanalyse fehlgeschlagen für %s: %s", media_id, err)
                    failed.append(
                        {
                            "media_id": media_id,
                            "name": str(window.get("name") or ""),
                            "reason": str(err)[:200],
                        }
                    )
                    await self._async_release(media_id, pending, originals)
                    continue
                analysed += 1
                segments_found += len(record.get("segments") or [])
                await self._async_release(media_id, pending, originals)
                if record.get("refused"):
                    refused.append(
                        {
                            "media_id": media_id,
                            "name": str(window.get("name") or ""),
                            "reason": str(record.get("refusal_reason") or ""),
                        }
                    )
        finally:
            # The originals go, always. They were only ever here to be cut.
            # In the executor: this deletes hundreds of megabytes, and a
            # blocking unlink loop on the event loop stalls every other
            # thing Home Assistant is doing at that moment.
            await self.hass.async_add_executor_job(
                lambda: shutil.rmtree(work, ignore_errors=True)
            )
            _LOGGER.info(
                "Videoanalyse beendet: %s analysiert, %s fehlgeschlagen, %s Momente",
                analysed,
                len(failed),
                segments_found,
            )
            # Written in the `finally`, so a run that was interrupted still
            # leaves a record of what it managed and what it did not. The
            # summary used to exist only in the response to the click: a
            # dropped WebSocket or a reloaded page took every failure
            # reason with it, and the person who had just paid for the run
            # was left to ask a log file which recordings had not worked.
            await self.hass.async_add_executor_job(
                self.store.save_video_run,
                trip_id,
                {
                    "analysed": analysed,
                    "failed": failed,
                    "refused": refused,
                    "segments": segments_found,
                    "planned": len(plan["new"]),
                    "finished_at": utc_now_iso(),
                },
            )

        return {
            "analysed": analysed,
            "cached": plan["cached_count"],
            "skipped": plan["over_limit"],
            "failed": failed,
            "refused": refused,
            "segments": segments_found,
            # The same field the stored summary carries. It was missing
            # here, and the card - which reads whichever of the two it
            # has - rendered "0 von 0 analysiert" for a run that had
            # planned eleven. One shape, or the reader has to know which
            # source it got.
            "planned": len(plan["new"]),
        }

    # --- the parts that touch the world ---------------------------------

    async def _async_release(
        self,
        media_id: str,
        pending: dict[str, int],
        originals: dict[str, Path],
    ) -> None:
        """Delete a recording's copy once no window still needs it.

        Counted down rather than deleted after each window, because one
        recording is usually several windows and re-downloading it would
        trade disk for bandwidth and time. The `finally` still removes the
        whole directory - this only lowers the peak, which is the number
        that decides whether /share runs out halfway through a run.
        """
        left = pending.get(media_id, 0) - 1
        pending[media_id] = max(0, left)
        if left > 0:
            return
        source = originals.pop(media_id, None)
        if source is None:
            return
        await self.hass.async_add_executor_job(
            lambda: source.unlink(missing_ok=True)
        )

    async def _async_fetch_original(self, window: dict[str, Any], work: Path) -> Path:
        """A copy of the recording, on disk, for as long as it takes to cut."""
        if self._media_source is None:
            raise ValidationError("Für Videos ist keine Medienquelle verbunden")
        target = work / f"{window.get('id')}.src"
        # The directory is created by whoever writes the file. It used to
        # be created here with `mkdir(True, True)` - and `Path.mkdir` takes
        # (mode, parents, exist_ok), so that was mode 0o1: a directory
        # nobody may write to. It passed locally because the local run was
        # root, which ignores permission bits, and failed the moment CI ran
        # it as an ordinary user. Positional arguments to a function whose
        # first parameter is a mode are worth avoiding entirely.
        await self._media_source.async_download_to(window, target)
        written = await self.hass.async_add_executor_job(_size_of, target)
        if written <= 0:
            raise ValidationError("Das Video konnte nicht geladen werden")
        _LOGGER.info(
            "Aufnahme geladen: %s MB", round(written / (1024 * 1024), 1)
        )
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
        try:
            result = await self._provider.async_analyze_video(
                system_instruction=SYSTEM_PROMPT,
                prompt=request["prompt"],
                video=AssistantVideoInput(
                    video_id=str(window.get("id") or ""),
                    data=await self.hass.async_add_executor_job(proxy.read_bytes),
                    mime_type="video/mp4",
                    start_offset=0.0,
                    end_offset=max(0.0, end - start),
                ),
                schema=RESPONSE_SCHEMA,
            )
        except RoadplannerError as err:
            # A safety refusal is a settled answer about this clip, not a
            # transient failure: the same seconds of the same recording,
            # judged by the same model against the same policy, will be
            # refused again. Treated as a failure it was never stored, so
            # every future run planned it, downloaded the recording again
            # and asked again - for a family's own holiday footage, which
            # these filters refuse routinely and wrongly.
            #
            # Stored as "no moment here, and here is why", which is
            # exactly how an unreadable answer is already handled one
            # block down. `code` is read off the error and the class is
            # checked by a test, because a default that can lie is how
            # this project has shipped the same bug five times.
            if str(getattr(err, "code", "")) != "safety_block":
                raise
            _LOGGER.info(
                "Videoanalyse: %s wurde vom Modell abgelehnt (%s)", key[:12], err
            )
            record = {
                "media_id": str(window.get("id") or ""),
                "window_start": round(start, 3),
                "window_end": round(end, 3),
                "segments": [],
                "refused": "safety",
                "refusal_reason": str(err)[:200],
                "analysis_version": ANALYSIS_VERSION,
                "model": self._model_name(),
                "analysed_at": utc_now_iso(),
            }
            await self.hass.async_add_executor_job(
                self.store.save_video_analysis, trip_id, key, record
            )
            return record

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
