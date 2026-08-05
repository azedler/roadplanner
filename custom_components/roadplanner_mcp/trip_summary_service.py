"""Generate and STORE the trip's written summaries: per day, per person, whole trip.

Live request: "Außerdem würde ich gern zu jeder Person eine kleine lustige
Zusammenfassung haben. Erstellt von der ki aus den Bildern. Dann zu jedem
Tag eine Zusammenfassung was wir da gemacht haben und natürlich für die
gesamte Reise eine Zusammenfassung."

Two decisions shape this module.

**Stored, not recomputed.** A day summary costs one Vision call with up to
nine photos; a 23-day trip is 23 of them plus one per crew member. Doing
that inside every PDF export would make the export take minutes and burn
the daily quota on text that barely changes between two exports. The
results are written into ``day.details.ai_summary`` / ``trip.details``/ the
crew person record, so the PDF just reads them, and a wrong sentence can be
corrected by hand instead of only re-rolled.

**A background job, not an inline action.** Same reasoning as the video
export (see trip_video_export.py): a request held open for minutes dies on
the first mobile connection change. The panel starts the run and polls.

Everything fails open per item: a day whose Vision call fails keeps its old
summary (or none) and the run continues. A partially written set of
summaries is strictly better than an aborted run that stored nothing.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import AssistantImageInput
from .canonical_day import canonical_roadbook_stops
from .experience_store import utc_now_iso
from .roadplanner import RoadplannerError, ValidationError
from .trip_export_photos import async_fetch_media_photo, crop_photo
from .trip_summaries import (
    DAY_SUMMARY_SYSTEM_PROMPT,
    DAY_SUMMARY_TEXT_ONLY_PROMPT,
    PERSON_SUMMARY_SYSTEM_PROMPT,
    PERSON_SUMMARY_TEXT_PROMPT,
    SUMMARY_DETAIL_KEY,
    SUMMARY_SCHEMA,
    TRIP_SUMMARY_SYSTEM_PROMPT,
    day_facts,
    normalize_summary,
    person_facts,
    trip_facts,
)

_LOGGER = logging.getLogger(__name__)

MAX_DAY_SUMMARY_CHARS = 600
MAX_TRIP_SUMMARY_CHARS = 1_200
MAX_PERSON_SUMMARY_CHARS = 400

# Gemini accepts at most 15 images per call; leave room for the reference
# portrait and stay well inside the total-byte budget.
_MAX_DAY_PHOTOS = 8
_MAX_PERSON_PHOTOS = 10


class TripSummaryService:
    """Generate the written summaries for one trip and store them."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        experience: Any,
        crew: Any,
        provider: Any,
        *,
        media_cache: Any = None,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.experience = experience
        self.crew = crew
        self.provider = provider
        self.media_cache = media_cache
        self._status: dict[str, Any] = {"state": "idle"}
        self._task: Any = None
        self._revision = 0

    # --- status / lifecycle ------------------------------------------

    def async_start(self, trip_id: str, *, scope: str = "all") -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise ValidationError(
                "Es läuft bereits eine Zusammenfassung - Status siehe "
                "Gesamtroute; bitte warten, bis sie fertig ist"
            )
        if self.provider is None or not getattr(self.provider, "configured", False):
            raise RoadplannerError(
                "Für die Zusammenfassungen ist kein Reisebegleiter (Gemini) "
                "konfiguriert"
            )
        self._status = {
            "state": "running",
            "scope": scope,
            "stage": "Vorbereitung",
            "started_at": utc_now_iso(),
            "written": {"days": 0, "people": 0, "trip": 0},
        }
        self._task = self.hass.async_create_task(self._async_run(trip_id, scope))
        return dict(self._status)

    async def async_status(self) -> dict[str, Any]:
        return dict(self._status)

    def _set_stage(self, stage: str) -> None:
        if self._status.get("state") == "running":
            self._status["stage"] = stage

    def _count(self, key: str) -> None:
        written = self._status.get("written")
        if isinstance(written, dict):
            written[key] = int(written.get(key, 0)) + 1

    async def _async_run(self, trip_id: str, scope: str) -> None:
        try:
            await self.async_generate(trip_id, scope=scope)
        except (RoadplannerError, ValidationError) as err:
            self._status.update(
                {
                    "state": "error",
                    "stage": "Fehlgeschlagen",
                    "error": str(err)[:500],
                    "finished_at": utc_now_iso(),
                }
            )
            _LOGGER.warning("Trip summary generation failed: %s", err)
        except asyncio.CancelledError:
            # BaseException, so a plain "except Exception" would leave the
            # status on "running" forever - the same trap the video export
            # fell into.
            self._status.update(
                {
                    "state": "error",
                    "stage": "Abgebrochen",
                    "error": "Die Erstellung wurde abgebrochen - bitte neu starten",
                    "finished_at": utc_now_iso(),
                }
            )
            raise
        except Exception as err:  # noqa: BLE001 - the status must always resolve
            self._status.update(
                {
                    "state": "error",
                    "stage": "Fehlgeschlagen",
                    "error": f"Unerwarteter Fehler ({type(err).__name__})",
                    "finished_at": utc_now_iso(),
                }
            )
            _LOGGER.exception("Trip summary generation crashed")
        else:
            self._status.update(
                {"state": "ready", "stage": "Fertig", "finished_at": utc_now_iso()}
            )

    # --- generation ---------------------------------------------------

    async def async_generate(self, trip_id: str, *, scope: str = "all") -> dict[str, Any]:
        """Write every requested summary; return what was written."""
        payload = await self.manager.async_get_assistant_payload(trip_id)
        summary_block = payload.get("summary") or {}
        trip = summary_block.get("trip") or {}
        days_raw = [
            day for day in (payload.get("days") or {}).get("days") or []
            if isinstance(day, dict)
        ]
        self._revision = int(summary_block.get("revision") or 0)
        media_by_stop, media_by_day, all_media = await self._async_media_index(
            trip_id, days_raw
        )
        session = async_get_clientsession(self.hass)

        written_days: list[tuple[str, str]] = []
        if scope in ("all", "days"):
            for index, day in enumerate(days_raw, start=1):
                self._set_stage(f"Tag {index} von {len(days_raw)}")
                text = await self._async_day_summary(
                    session, trip_id, day, media_by_stop, media_by_day
                )
                if text:
                    try:
                        await self._async_store_day_summary(trip_id, day, text)
                    except ValidationError:
                        # The trip moved on under us. Skip this one day and
                        # keep going - a partial set beats an aborted run.
                        pass
                    else:
                        self._count("days")
                written_days.append((str(day.get("title") or ""), text))
        else:
            written_days = [
                (
                    str(day.get("title") or ""),
                    str(
                        (day.get("details") or {}).get(SUMMARY_DETAIL_KEY)
                        if isinstance(day.get("details"), dict)
                        else ""
                    ),
                )
                for day in days_raw
            ]

        if scope in ("all", "people"):
            self._set_stage("Crew")
            await self._async_person_summaries(
                session, trip_id, days_raw, media_by_stop, media_by_day, all_media
            )

        if scope in ("all", "trip"):
            self._set_stage("Gesamte Reise")
            text = await self._async_trip_summary(trip, written_days, summary_block)
            if text:
                try:
                    await self._async_store_trip_summary(trip_id, text)
                except ValidationError:
                    pass
                else:
                    self._count("trip")
        return dict(self._status.get("written") or {})

    async def _async_media_index(
        self, trip_id: str, days_raw: list[dict[str, Any]]
    ) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        """Index the trip's own media the same way the PDF export does."""
        by_stop: dict[str, list[dict[str, Any]]] = {}
        by_day: dict[str, list[dict[str, Any]]] = {}
        try:
            state = await self.experience.async_panel_payload(trip_id, days=days_raw)
        except (RoadplannerError, AttributeError, TypeError) as err:
            _LOGGER.debug("Summary media index unavailable: %s", type(err).__name__)
            return by_stop, by_day, []
        all_media = [
            item for item in (state or {}).get("media") or [] if isinstance(item, dict)
        ]
        for item in all_media:
            stop_id = str(item.get("linked_stop_id") or "")
            if stop_id:
                by_stop.setdefault(stop_id, []).append(item)
            day_id = str(item.get("linked_day_id") or "")
            if day_id:
                by_day.setdefault(day_id, []).append(item)
        return by_stop, by_day, all_media

    async def _async_day_photos(
        self,
        session: Any,
        trip_id: str,
        day: dict[str, Any],
        media_by_stop: dict[str, list[dict[str, Any]]],
        media_by_day: dict[str, list[dict[str, Any]]],
        *,
        limit: int,
    ) -> list[bytes]:
        from .media_intelligence import select_media_highlights

        stops = [s for s in canonical_roadbook_stops(day) if isinstance(s, dict)]
        pool: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidates in (
            [
                item
                for stop in stops
                for item in media_by_stop.get(str(stop.get("id") or ""), [])
            ],
            media_by_day.get(str(day.get("id") or "")) or [],
        ):
            for item in candidates:
                identifier = str(item.get("id") or "")
                if identifier and identifier in seen:
                    continue
                seen.add(identifier)
                pool.append(item)
        if not pool:
            return []
        # The SAME curation the panel and the exports use, so the model sees
        # the same handful of pictures the user considers representative.
        highlights, _stats = select_media_highlights(pool, limit=limit)
        photos: list[bytes] = []
        for item in highlights:
            photo = await async_fetch_media_photo(
                session,
                self.experience,
                trip_id,
                item,
                cache=self.media_cache,
                hass=self.hass,
            )
            if photo:
                photos.append(photo)
        return photos

    async def _async_day_summary(
        self,
        session: Any,
        trip_id: str,
        day: dict[str, Any],
        media_by_stop: dict[str, list[dict[str, Any]]],
        media_by_day: dict[str, list[dict[str, Any]]],
    ) -> str:
        stops = [s for s in canonical_roadbook_stops(day) if isinstance(s, dict)]
        facts = day_facts(day, stops)
        photos = await self._async_day_photos(
            session, trip_id, day, media_by_stop, media_by_day, limit=_MAX_DAY_PHOTOS
        )
        if photos and callable(getattr(self.provider, "async_analyze_images", None)):
            images = [
                AssistantImageInput(
                    image_id=f"foto-{index + 1}",
                    data=photo,
                    mime_type="image/jpeg",
                    label=f"Foto {index + 1} dieses Reisetags",
                )
                for index, photo in enumerate(photos)
            ]
            try:
                result = await self.provider.async_analyze_images(
                    system_instruction=DAY_SUMMARY_SYSTEM_PROMPT,
                    prompt=f"Planungsdaten des Tages:\n{facts}",
                    images=images,
                    schema=SUMMARY_SCHEMA,
                    max_output_tokens=500,
                )
            except RoadplannerError as err:
                _LOGGER.debug("Day summary vision failed: %s", type(err).__name__)
            else:
                text = normalize_summary(
                    _summary_of(result), max_chars=MAX_DAY_SUMMARY_CHARS
                )
                if text:
                    return text
        # No photos, or Vision declined: the planned facts alone still say
        # where the day went - with a prompt that does not pretend more.
        try:
            result = await self.provider.async_generate_text(
                system_instruction=DAY_SUMMARY_TEXT_ONLY_PROMPT,
                messages=[{"role": "user", "content": facts}],
                enable_search=False,
                max_output_tokens=260,
                temperature=0.5,
            )
        except RoadplannerError as err:
            _LOGGER.debug("Day summary text failed: %s", type(err).__name__)
            return ""
        return normalize_summary(
            getattr(result, "text", ""), max_chars=MAX_DAY_SUMMARY_CHARS
        )

    async def _async_trip_summary(
        self,
        trip: dict[str, Any],
        day_summaries: list[tuple[str, str]],
        summary_block: dict[str, Any],
    ) -> str:
        facts = trip_facts(
            trip,
            day_summaries=day_summaries,
            total_distance_km=_as_float(summary_block.get("total_distance_km")),
            country_count=int(summary_block.get("country_count") or 0),
        )
        try:
            result = await self.provider.async_generate_text(
                system_instruction=TRIP_SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": facts}],
                enable_search=False,
                max_output_tokens=700,
                temperature=0.5,
            )
        except RoadplannerError as err:
            _LOGGER.debug("Trip summary failed: %s", type(err).__name__)
            return ""
        return normalize_summary(
            getattr(result, "text", ""), max_chars=MAX_TRIP_SUMMARY_CHARS
        )

    async def _async_person_summaries(
        self,
        session: Any,
        trip_id: str,
        days_raw: list[dict[str, Any]],
        media_by_stop: dict[str, list[dict[str, Any]]],
        media_by_day: dict[str, list[dict[str, Any]]],
        all_media: list[dict[str, Any]],
    ) -> None:
        try:
            registry = await self.crew.async_panel_payload()
        except (RoadplannerError, AttributeError, TypeError) as err:
            _LOGGER.debug("Crew registry unavailable: %s", type(err).__name__)
            return
        people = [
            person
            for person in (registry or {}).get("people") or []
            if isinstance(person, dict) and person.get("active", True)
        ]
        if not people:
            return
        # One pooled set of trip photos for everyone: the reference portrait
        # is what tells the people apart, not a per-person photo set.
        pooled: list[bytes] = []
        for day in days_raw:
            if len(pooled) >= _MAX_PERSON_PHOTOS:
                break
            pooled.extend(
                await self._async_day_photos(
                    session, trip_id, day, media_by_stop, media_by_day, limit=2
                )
            )
        pooled = pooled[:_MAX_PERSON_PHOTOS]
        for person in people:
            name = str(person.get("name") or "").strip()
            if not name:
                continue
            self._set_stage(f"Crew: {name}")
            text = await self._async_person_summary(
                session, trip_id, person, pooled, all_media
            )
            if not text:
                continue
            try:
                await self.crew.async_update_person(
                    person_id=str(person.get("id") or ""),
                    patch={"summary": text, "summary_generated_at": utc_now_iso()},
                )
            except (RoadplannerError, ValidationError) as err:
                _LOGGER.debug("Storing person summary failed: %s", err)
                continue
            self._count("people")

    async def _async_person_summary(
        self,
        session: Any,
        trip_id: str,
        person: dict[str, Any],
        pooled_photos: list[bytes],
        all_media: list[dict[str, Any]],
    ) -> str:
        name = str(person.get("name") or "").strip()
        reference = await self._async_reference_photo(session, trip_id, person)
        if (
            reference
            and pooled_photos
            and callable(getattr(self.provider, "async_analyze_images", None))
        ):
            images = [
                AssistantImageInput(
                    image_id="referenz",
                    data=reference,
                    mime_type="image/jpeg",
                    label=f"Referenzfoto: Das ist {name}",
                )
            ]
            images.extend(
                AssistantImageInput(
                    image_id=f"foto-{index + 1}",
                    data=photo,
                    mime_type="image/jpeg",
                    label=f"Reisefoto {index + 1}",
                )
                for index, photo in enumerate(pooled_photos)
            )
            try:
                result = await self.provider.async_analyze_images(
                    system_instruction=PERSON_SUMMARY_SYSTEM_PROMPT,
                    prompt=f"Was hat {name} auf dieser Reise erlebt?",
                    images=images,
                    schema=SUMMARY_SCHEMA,
                    max_output_tokens=400,
                )
            except RoadplannerError as err:
                _LOGGER.debug("Person summary vision failed: %s", type(err).__name__)
            else:
                text = normalize_summary(
                    _summary_of(result), max_chars=MAX_PERSON_SUMMARY_CHARS
                )
                if text:
                    return text
        # Without a reference portrait nobody can be told apart on a group
        # photo, so the captions are the only honest source left.
        captions = _captions_mentioning(all_media, name)
        if not captions:
            return ""
        try:
            result = await self.provider.async_generate_text(
                system_instruction=PERSON_SUMMARY_TEXT_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": person_facts(
                            name, str(person.get("note") or ""), captions
                        ),
                    }
                ],
                enable_search=False,
                max_output_tokens=220,
                temperature=0.5,
            )
        except RoadplannerError as err:
            _LOGGER.debug("Person summary text failed: %s", type(err).__name__)
            return ""
        return normalize_summary(
            getattr(result, "text", ""), max_chars=MAX_PERSON_SUMMARY_CHARS
        )

    async def _async_reference_photo(
        self, session: Any, trip_id: str, person: dict[str, Any]
    ) -> bytes | None:
        media_id = str(person.get("reference_media_id") or "")
        if not media_id:
            return None
        photo = await async_fetch_media_photo(
            session,
            self.experience,
            trip_id,
            {"id": media_id},
            cache=self.media_cache,
            hass=self.hass,
        )
        if not photo:
            return None
        # On a group picture only the chosen face region identifies anyone.
        return crop_photo(photo, person.get("reference_crop"))

    # --- storage ------------------------------------------------------

    async def _async_store_day_summary(
        self, trip_id: str, day: dict[str, Any], text: str
    ) -> None:
        details = deepcopy(day.get("details")) if isinstance(day.get("details"), dict) else {}
        details[SUMMARY_DETAIL_KEY] = text
        details[f"{SUMMARY_DETAIL_KEY}_generated_at"] = utc_now_iso()
        await self._async_patch(
            self.manager.async_update_day,
            day_id=str(day.get("id") or ""),
            patch={"details": details},
            expected_trip_id=trip_id,
        )

    async def _async_store_trip_summary(self, trip_id: str, text: str) -> None:
        payload = await self.manager.async_get_assistant_payload(trip_id)
        block = payload.get("summary") or {}
        self._revision = int(block.get("revision") or self._revision)
        trip = block.get("trip") or {}
        details = deepcopy(trip.get("details")) if isinstance(trip.get("details"), dict) else {}
        details[SUMMARY_DETAIL_KEY] = text
        details[f"{SUMMARY_DETAIL_KEY}_generated_at"] = utc_now_iso()
        await self._async_patch(
            self.manager.async_update_trip,
            patch={"details": details},
            expected_trip_id=trip_id,
        )

    async def _async_patch(self, method: Any, **kwargs: Any) -> None:
        """Write one patch, carrying the revision forward between writes.

        The run takes minutes and writes once per day, so a revision read
        once at the start is stale by the second write. Each mutation
        returns the revision it produced; that is what the next one builds
        on. A conflict means the user edited the trip meanwhile - the
        summary is then simply skipped rather than overwriting their edit.
        """
        try:
            result = await method(
                actor="roadplanner_summary",
                expected_revision=self._revision,
                **kwargs,
            )
        except ValidationError as err:
            _LOGGER.debug("Summary patch rejected: %s", err)
            self._revision = await self._async_current_revision(
                str(kwargs.get("expected_trip_id") or "")
            )
            raise
        revision = result.get("revision") if isinstance(result, dict) else None
        if isinstance(revision, int):
            self._revision = revision

    async def _async_current_revision(self, trip_id: str) -> int:
        if not trip_id:
            return self._revision
        try:
            payload = await self.manager.async_get_assistant_payload(trip_id)
        except (RoadplannerError, ValidationError):
            return self._revision
        return int((payload.get("summary") or {}).get("revision") or self._revision)


def _summary_of(result: Any) -> str:
    data = getattr(result, "data", None)
    if isinstance(data, dict) and "summary" in data:
        return str(data.get("summary") or "")
    return str(getattr(result, "text", "") or "")


def _captions_mentioning(all_media: list[dict[str, Any]], name: str) -> list[str]:
    """Captions that name this person - the only link media carries today.

    Media items have no person tag; a caption mentioning the name is the
    one honest association available without a reference portrait.
    """
    token = " ".join(str(name or "").split())
    if len(token) < 2:
        return []
    pattern = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
    captions: list[str] = []
    for item in all_media:
        caption = str(item.get("caption") or "").strip()
        if caption and pattern.search(caption):
            captions.append(caption)
    return captions[:20]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
