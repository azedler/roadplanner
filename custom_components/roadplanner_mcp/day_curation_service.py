"""Running the curation for a day: one look, cached, then arithmetic.

The expensive part of choosing photographs is looking at them, and it is
the only part worth paying for. So this service does it once per day, on
a pool that was widened first, and stores what came back. Everything
after - which pictures cover the day's subjects, how many the film shows,
what the person pinned - is arithmetic over that stored answer and costs
nothing to redo.

The cache key is the pool itself: the photographs' own hashes plus the
contract version. Adding a photograph to a day re-asks that day. Editing
the film budget, changing the story, re-rendering, or opening the panel
does not.

What leaves the house
---------------------

Downscaled thumbnails of the photographs in the pool, and nothing else.
No day, no place, no date, no names - not because they are secret, but
because everything the model is told it can hand back as if it had seen
it, and the roadbook already knows all of it for certain.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .experience_helpers import _all_days, canonical_roadbook_stops
from .experience_store import ExperienceStore, utc_now_iso
from .manager import RoadplannerManager
from .day_film_diagnosis import (
    VERDICT_CURATION,
    VERDICT_NO_MATERIAL,
    VERDICT_OK,
    VERDICT_SCENE_PLAN,
    diagnose_day,
)
from .media_curation import (
    CURATION_VERSION,
    candidate_pool,
    group_series,
    select_for_chapter,
    semantic_score,
    visual_brief,
)
from .media_curation_vision import (
    CURATION_VISION_VERSION,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    analysis_key,
    batches,
    build_prompt,
    parse_analyses,
)
from .media_intelligence import media_quality_score
from .story_context_builder import OVERRIDE_STORY_KEY
from .trip_summaries import SUMMARY_DETAIL_KEY
from .roadplanner import RoadplannerError, ValidationError
from .trip_film_plan import readable_place

_LOGGER = logging.getLogger(__name__)

# What one day may show at most. The film's own plan takes fewer than
# this for a quiet day; this is the ceiling, not the target.
MAX_SELECTED_PER_DAY = 14

# The daily ceiling this service reserves against, which is deliberately
# NOT the stop curation's.
#
# That limit exists because stop curation runs by itself when photographs
# arrive; five a day is a guard against a sync quietly spending money.
# This one only ever runs because somebody pressed a button that says
# what it costs, and a three-week trip needs roughly one call per day -
# so the guard designed for background work would stop a foreground job
# a fifth of the way in and leave twenty days uncurated with no obvious
# reason why.
DAY_CURATION_DAILY_LIMIT = 60

# How many days one call may actually look at.
#
# A three-week trip is roughly one model call per day, and doing all of
# them inside a single panel action takes minutes - long enough that the
# websocket gives up and the person is told "Connection lost" while the
# work carries on invisibly behind them. So a call does a few days and
# says how many are left; the panel asks again until nothing is.
#
# Cached days are free and do not count against this, so a resumed run
# skips everything already done and spends the batch on the rest.
DAYS_PER_CALL = 4


class DayCurationService:
    """Curate each day's photographs: pool, look, cover, choose."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        vision: Any,
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self._vision = vision

    @property
    def enabled(self) -> bool:
        return bool(getattr(self._vision, "vision_enabled", False))

    async def async_curate_trip(
        self,
        trip_id: str,
        *,
        force: bool = False,
        max_days: int | None = DAYS_PER_CALL,
        fresh_after: str | None = None,
    ) -> dict[str, Any]:
        """Curate a trip's days, a few paid ones per call.

        `max_days` bounds how many days may cost something in one call,
        not how many are looked at: a day whose answer is still cached is
        recomputed for free and never uses up the batch. `remaining` is
        what the panel loops on.

        `fresh_after` is what lets `force` survive that loop. Forcing
        every round unconditionally re-paid days 1-4 in every round - the
        first four days with photographs consumed the batch again and
        again, day 5 was never reached, and the daily limit went up in
        re-answering questions asked a minute earlier. So the first round
        is answered with this call's own start time (`run_marker`), later
        rounds send it back, and a day whose record is younger than the
        marker is this run's own finished work: recomputed for free, not
        forced again.
        """
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Bildauswahl wurde keine Reise ausgewählt")
        run_marker = utc_now_iso()
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [item for item in state.get("media") or [] if isinstance(item, dict)]
        stored = state.get("day_curations") or {}

        by_day: dict[str, list[dict[str, Any]]] = {}
        for item in media:
            day_id = str(item.get("linked_day_id") or "")
            if day_id and str(item.get("assignment_status") or "") in {"manual", "automatic"}:
                by_day.setdefault(day_id, []).append(item)

        results: list[dict[str, Any]] = []
        looked_at = 0
        paid_days = 0
        remaining = 0
        budget = int(max_days) if max_days else None
        for day in days:
            day_id = str(day.get("id") or "")
            if not day_id:
                continue
            existing = stored.get(day_id) if isinstance(stored, dict) else None
            day_force = force and not _curated_since(existing, fresh_after)
            if budget is not None and paid_days >= budget:
                # Out of batch. A day that would only be recomputed from
                # cache is still not "remaining" - only one that would
                # have to pay is, or the panel would loop forever over
                # days that never change.
                if self._would_pay(day, by_day.get(day_id, []), existing, force=day_force):
                    remaining += 1
                continue
            record = await self.async_curate_day(
                trip_id,
                day,
                by_day.get(day_id, []),
                existing=existing,
                force=day_force,
            )
            results.append(record)
            looked_at += int(record.get("analysed_count") or 0)
            if int(record.get("analysed_count") or 0):
                paid_days += 1
        return {
            "ok": True,
            "trip_id": trip_id,
            "run_marker": run_marker,
            "remaining": remaining,
            "day_count": len(results),
            "analysed_count": looked_at,
            "selected_count": sum(len(entry.get("media_ids") or []) for entry in results),
            "pool_count": sum(int(entry.get("pool_size") or 0) for entry in results),
            "photo_count": sum(int(entry.get("photo_count") or 0) for entry in results),
            "unmet": {
                entry["day_id"]: entry["coverage"]["unmet"]
                for entry in results
                if entry.get("coverage", {}).get("unmet")
            },
            "days": results,
        }

    def _would_pay(
        self,
        day: dict[str, Any],
        media: list[dict[str, Any]],
        existing: dict[str, Any] | None,
        *,
        force: bool,
    ) -> bool:
        """Would curating this day cost anything?

        Answered by rebuilding the pool and comparing its key, which is
        cheap arithmetic - the same work the real pass does before it
        decides whether to ask. Guessing instead ("it has photographs, so
        probably") is how a progress loop stops at 3 remaining forever.
        """
        if not media or not self.enabled:
            return False
        if force or not isinstance(existing, dict):
            return True
        stops = [
            {"name": readable_place(stop), "kind": str(stop.get("type") or "")}
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict)
        ]
        pool = candidate_pool(
            media,
            series=group_series(media),
            stop_count=len(stops),
            media_budget=MAX_SELECTED_PER_DAY,
            local_score=media_quality_score,
            pinned=[
                str(item.get("id"))
                for item in media
                if str(item.get("film_pin") or "") in {"show", "hero"}
            ],
            excluded=[
                str(item.get("id"))
                for item in media
                if str(item.get("film_pin") or "") == "exclude"
            ],
        )
        if not pool["media_ids"]:
            return False
        by_id = {str(item.get("id")): item for item in media}
        fingerprints = {
            media_id: str(
                by_id[media_id].get("file_hash") or by_id[media_id].get("provider_item_id") or ""
            )
            for media_id in pool["media_ids"]
            if media_id in by_id
        }
        if str(existing.get("analysis_key") or "") != analysis_key(
            pool["media_ids"], fingerprints
        ):
            return True
        # A matching key over an EMPTY answer is the leftover of a failed
        # look, and the real pass no longer accepts it as a cache hit - so
        # predicting "free" here would strand the progress loop on it.
        return not (existing.get("analyses") or {})

    async def async_curate_day(
        self,
        trip_id: str,
        day: dict[str, Any],
        media: list[dict[str, Any]],
        *,
        existing: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """One day: pool it, look at it once, then decide without paying."""
        day_id = str(day.get("id") or "")
        stops = [
            {"name": readable_place(stop), "kind": str(stop.get("type") or "")}
            for stop in canonical_roadbook_stops(day)
            if isinstance(stop, dict)
        ]
        # The day's own text, in the order somebody would trust it: what
        # was edited by hand, then what the editorial pass wrote, then
        # the stored summary. It never creates a requirement - it helps
        # answer one, which is what a stop called "Smålandet Markaryds
        # Älgsafari" cannot do on its own.
        details = day.get("details") if isinstance(day.get("details"), dict) else {}
        story = (
            str(details.get(OVERRIDE_STORY_KEY) or "")
            or str(details.get(SUMMARY_DETAIL_KEY) or "")
            or str(day.get("summary") or "")
        )
        brief = visual_brief(
            {
                "title": str(day.get("title") or ""),
                "story": story,
                "stops": stops,
            }
        )

        pinned = [
            str(item.get("id"))
            for item in media
            if str(item.get("film_pin") or "") in {"show", "hero"}
        ]
        excluded = [
            str(item.get("id")) for item in media if str(item.get("film_pin") or "") == "exclude"
        ]
        hero = next(
            (str(item.get("id")) for item in media if str(item.get("film_pin") or "") == "hero"),
            "",
        )

        series = group_series(media)
        pool = candidate_pool(
            media,
            series=series,
            stop_count=len(stops),
            media_budget=MAX_SELECTED_PER_DAY,
            local_score=media_quality_score,
            pinned=pinned,
            excluded=excluded,
        )
        series_by_media = {
            media_id: record["series_id"]
            for record in series
            for media_id in record.get("media_ids") or []
        }
        by_id = {str(item.get("id")): item for item in media}
        fingerprints = {
            media_id: str(by_id[media_id].get("file_hash") or by_id[media_id].get("provider_item_id") or "")
            for media_id in pool["media_ids"]
            if media_id in by_id
        }
        key = analysis_key(pool["media_ids"], fingerprints)

        analyses: dict[str, dict[str, Any]] = {}
        analysed = 0
        note = ""
        if (
            not force
            and isinstance(existing, dict)
            and str(existing.get("analysis_key") or "") == key
            # An empty stored answer under a matching key is what a failed
            # first look leaves behind, not an answer. Honouring it as one
            # pinned days at "analysiert 0" through every unforced run -
            # the record never changed, so it was never asked again.
            and ((existing.get("analyses") or {}) or not (pool["media_ids"] and self.enabled))
        ):
            analyses = {
                media_id: value
                for media_id, value in (existing.get("analyses") or {}).items()
                if isinstance(value, dict)
            }
            note = "unverändert, keine neue Analyse"
        elif pool["media_ids"] and self.enabled:
            analyses, analysed, note = await self._async_look(
                trip_id,
                [by_id[media_id] for media_id in pool["media_ids"] if media_id in by_id],
                # Every word of every requirement, not just its label:
                # a photograph can show "Wasserfall" while "Storforsen"
                # is a name no picture can confirm.
                checks=sorted(
                    {
                        token
                        for tokens in (brief.get("alternatives") or {}).values()
                        for token in tokens
                    }
                    or set(brief.get("must_cover") or [])
                ),
            )
            if not analysed and not analyses and isinstance(existing, dict):
                # A look that produced nothing (daily limit, transport,
                # too few thumbnails) is a failure, not an answer. Storing
                # it as one would erase what the last successful look knew
                # - which is how a quota that ran out mid-trip wiped whole
                # days. Keep the old answers for the pictures still in the
                # pool, and keep the OLD key, so the next run asks again
                # instead of trusting this.
                in_pool = set(pool["media_ids"])
                analyses = {
                    media_id: value
                    for media_id, value in (existing.get("analyses") or {}).items()
                    if isinstance(value, dict) and media_id in in_pool
                }
                key = str(existing.get("analysis_key") or "")
        elif pool["media_ids"]:
            note = "KI-Bildauswahl ist ausgeschaltet - lokale Reihenfolge"

        # No analyses at all still has to produce a usable day: the local
        # order is a weaker answer than a look, and a far better one than
        # an empty chapter.
        candidates = pool["media_ids"]
        if not analyses:
            candidates = sorted(
                candidates,
                key=lambda media_id: -float(media_quality_score(by_id.get(media_id, {})) or 0.0),
            )

        selection = select_for_chapter(
            candidates=candidates,
            analyses=analyses,
            brief=brief,
            series_by_media=series_by_media,
            target=MAX_SELECTED_PER_DAY,
            pinned=pinned,
            excluded=excluded,
        )
        record = {
            "day_id": day_id,
            "curation_version": CURATION_VERSION,
            "vision_version": CURATION_VISION_VERSION,
            "analysis_key": key,
            "curated_at": utc_now_iso(),
            "photo_count": pool["photo_count"],
            "pool_size": pool["pool_size"],
            "series_count": pool["series_count"],
            # What this run actually paid for, not how many answers are
            # on file. Conflating the two made a cached day consume the
            # batch, so a six-day trip curated the same two days forever
            # and the progress loop never ended.
            "analysed_count": analysed,
            "analysis_count": len(analyses),
            "note": note,
            "brief": brief,
            "media_ids": selection["media_ids"],
            "hero_media_id": hero or _best_hero(selection["media_ids"], analyses),
            "reasons": selection["reasons"],
            "coverage": selection["coverage"],
            "analyses": analyses,
            "pool_media_ids": pool["media_ids"],
            "rejected": pool["rejected"][:200],
            "pinned": pinned,
            "excluded": excluded,
        }
        if day_id:
            await self.hass.async_add_executor_job(
                self.store.save_day_curation, trip_id, day_id, record
            )
        return record

    async def async_diagnose(self, trip_id: str, day_id: str) -> dict[str, Any]:
        """Where this day's pictures were lost, stage by stage.

        Free and read-only: it reads what is already stored and counts.
        Exists because "an important place is barely in the film" has at
        least four causes needing opposite fixes, and picking between
        them by eye is how a special case for one place gets written.
        """
        trip_id = str(trip_id or "").strip()
        day_id = str(day_id or "").strip()
        if not trip_id or not day_id:
            raise ValidationError("Für die Diagnose fehlen Reise oder Tag")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        day = next(
            (
                entry
                for entry in _all_days(payload)
                if str(entry.get("id") or "") == day_id
            ),
            {},
        )
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        media = [
            item
            for item in state.get("media") or []
            if isinstance(item, dict) and str(item.get("linked_day_id") or "") == day_id
        ]
        curation = (state.get("day_curations") or {}).get(day_id)
        return diagnose_day(
            chapter={
                "chapter_id": day_id,
                "title": str(day.get("title") or ""),
                "importance": str(day.get("importance") or "normal"),
            },
            curation=curation,
            media=media,
            # What the film actually carried is the selection until a
            # renderer reports back; stated rather than guessed.
            film_media_ids=list((curation or {}).get("media_ids") or []) or None,
        )

    async def async_diagnose_trip(self, trip_id: str) -> dict[str, Any]:
        """The same diagnosis for every day of the trip, in one answer.

        One day tells you about one day. The question actually being
        asked - "is an important place under-represented, and where does
        that go wrong?" - is about a pattern, and a pattern needs the
        whole journey. It also means the person running it does not have
        to guess which day to look at.

        Free and read-only, like the single-day version: the payload and
        the stored state are loaded once and everything else is counting.
        """
        trip_id = str(trip_id or "").strip()
        if not trip_id:
            raise ValidationError("Für die Diagnose wurde keine Reise ausgewählt")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        curations = state.get("day_curations") or {}
        by_day: dict[str, list[dict[str, Any]]] = {}
        for item in state.get("media") or []:
            if not isinstance(item, dict):
                continue
            day = str(item.get("linked_day_id") or "")
            if day:
                by_day.setdefault(day, []).append(item)

        days: list[dict[str, Any]] = []
        for index, day in enumerate(_all_days(payload)):
            day_id = str(day.get("id") or "")
            if not day_id:
                continue
            found = diagnose_day(
                chapter={
                    "chapter_id": day_id,
                    "title": str(day.get("title") or ""),
                    "importance": str(day.get("importance") or "normal"),
                },
                curation=curations.get(day_id),
                media=by_day.get(day_id) or [],
                film_media_ids=list(
                    (curations.get(day_id) or {}).get("media_ids") or []
                )
                or None,
            )
            found["day_number"] = index + 1
            days.append(found)

        # The summary is what makes this readable at 23 days. A list of
        # twenty-three verdicts is a wall; "four days are weak, and here
        # is which stage" is an answer.
        weak = [entry for entry in days if entry["verdict"] != VERDICT_OK]
        return {
            "trip_id": trip_id,
            "day_count": len(days),
            "days": days,
            "weak_day_count": len(weak),
            "by_verdict": {
                verdict: sum(1 for entry in days if entry["verdict"] == verdict)
                for verdict in (
                    VERDICT_OK,
                    VERDICT_CURATION,
                    VERDICT_SCENE_PLAN,
                    VERDICT_NO_MATERIAL,
                )
            },
        }

    async def _async_look(
        self,
        trip_id: str,
        candidates: list[dict[str, Any]],
        *,
        checks: list[str] | None = None,
    ) -> tuple[dict[str, dict[str, Any]], int, str]:
        """One paid look at a day, split into batches that can compare."""
        analyses: dict[str, dict[str, Any]] = {}
        analysed = 0
        note = ""
        identifiers = [str(item.get("id")) for item in candidates]
        by_id = {str(item.get("id")): item for item in candidates}
        for group in batches(identifiers):
            reservation = await self.hass.async_add_executor_job(
                self.store.reserve_vision_call,
                trip_id,
                datetime.now(timezone.utc).date().isoformat(),
                DAY_CURATION_DAILY_LIMIT,
            )
            if not reservation.get("reserved"):
                note = "Tageslimit für die KI-Bildauswahl erreicht"
                break
            images = await self._vision.async_curation_images(
                [by_id[media_id] for media_id in group if media_id in by_id]
            )
            if len(images) < 2:
                note = "zu wenige Vorschaubilder abrufbar"
                continue
            try:
                result = await self._vision.provider.async_analyze_images(
                    system_instruction=SYSTEM_PROMPT,
                    prompt=build_prompt(len(images), checks),
                    images=images,
                    schema=RESPONSE_SCHEMA,
                    max_output_tokens=4_096,
                )
            except (RoadplannerError, asyncio.TimeoutError) as err:
                note = str(err)[:200]
                break
            parsed = parse_analyses(
                result.value,
                known_ids={item.image_id for item in images},
                checks=checks,
            )
            analyses.update(parsed)
            analysed += len(images)
        return analyses, analysed, note


def _curated_since(existing: Any, marker: str | None) -> bool:
    """Was this day's stored answer written at or after `marker`?

    `curated_at` and the marker both come from the server's own clock via
    `utc_now_iso`, so the comparison never involves a browser clock, and
    the fixed-width UTC format makes string order time order.
    """
    if not marker or not isinstance(existing, dict):
        return False
    return str(existing.get("curated_at") or "") >= str(marker)


def _best_hero(media_ids: list[str], analyses: dict[str, dict[str, Any]]) -> str:
    """The one picture that could stand for the day, if anything can."""
    flagged = [media_id for media_id in media_ids if (analyses.get(media_id) or {}).get("hero")]
    pool = flagged or media_ids
    if not pool:
        return ""
    return max(pool, key=lambda media_id: semantic_score(analyses.get(media_id) or {}))


def day_summary(record: dict[str, Any]) -> dict[str, Any]:
    """What the panel shows about a day, without the whole analysis blob."""
    record = record if isinstance(record, dict) else {}
    return {
        "day_id": record.get("day_id"),
        "photo_count": record.get("photo_count") or 0,
        "pool_size": record.get("pool_size") or 0,
        "selected_count": len(record.get("media_ids") or []),
        "series_count": record.get("series_count") or 0,
        "analysed_count": record.get("analysed_count") or 0,
        "analysis_count": record.get("analysis_count") or 0,
        "must_cover": (record.get("brief") or {}).get("must_cover") or [],
        "labels": (record.get("brief") or {}).get("labels") or {},
        "covered": (record.get("coverage") or {}).get("met") or [],
        "missing": (record.get("coverage") or {}).get("unmet") or [],
        "media_ids": record.get("media_ids") or [],
        "hero_media_id": record.get("hero_media_id") or "",
        "pinned": record.get("pinned") or [],
        "excluded": record.get("excluded") or [],
        "reasons": record.get("reasons") or {},
        "note": record.get("note") or "",
        "curated_at": record.get("curated_at") or "",
    }


__all__ = [
    "DAYS_PER_CALL",
    "DAY_CURATION_DAILY_LIMIT",
    "DayCurationService",
    "MAX_SELECTED_PER_DAY",
    "day_summary",
]
