"""Shared "local filter, then optional Gemini Vision selection" engine.

Used both by stop/trip photo curation (media_curation_manager.py) and by
destination-gallery curation (destination_gallery_manager.py). Local
deterministic filtering always runs first (see media_intelligence.py); Vision
is only applied to a bounded, already-reduced candidate set, and every
failure falls back to the local selection - Vision never blocks a result.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import AssistantImageInput, AssistantProvider
from .experience_helpers import _clean
from .experience_store import ExperienceStore, utc_now_iso
from .media_vision import (
    VISION_SELECTION_SCHEMA,
    build_vision_prompt,
    normalize_vision_selection,
    selection_fingerprint,
)
from .onedrive_media import OneDrivePersonalClient
from .roadplanner import RoadplannerError

_VISION_IMAGE_TIMEOUT_SECONDS = 12.0
_VISION_MAX_IMAGE_BYTES = 1_000_000
_VISION_MAX_TOTAL_BYTES = 8_000_000


class VisionCurationEngine:
    """Apply optional Gemini Vision selection to a locally prefiltered set."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        onedrive: OneDrivePersonalClient,
        provider: AssistantProvider | None,
        *,
        media_curation_mode: str = "local",
        media_vision_max_candidates: int = 12,
        media_vision_max_highlights: int = 5,
        media_vision_daily_limit: int = 5,
    ) -> None:
        self.hass = hass
        self.store = store
        self.onedrive = onedrive
        self.provider = provider
        self.media_curation_mode = (
            "hybrid" if str(media_curation_mode or "").casefold() == "hybrid" else "local"
        )
        self.media_vision_max_candidates = max(3, min(int(media_vision_max_candidates), 15))
        self.media_vision_max_highlights = max(1, min(int(media_vision_max_highlights), 8))
        self.media_vision_daily_limit = max(0, min(int(media_vision_daily_limit), 50))

    @property
    def vision_enabled(self) -> bool:
        """Return whether opt-in hybrid Vision curation can run."""
        return bool(
            self.media_curation_mode == "hybrid"
            and self.provider is not None
            and self.provider.configured
            and callable(getattr(self.provider, "async_analyze_images", None))
        )

    @staticmethod
    def _vision_context(day: dict[str, Any], stop: dict[str, Any]) -> dict[str, Any]:
        location = stop.get("location") if isinstance(stop.get("location"), dict) else {}
        place_parts = [
            _clean(location.get("label") or location.get("address"), 600),
            _clean(location.get("city") or stop.get("place") or stop.get("city"), 300),
            _clean(location.get("country") or location.get("country_code") or stop.get("country"), 200),
        ]
        description = " ".join(
            part
            for part in (
                _clean(stop.get("notes"), 1_000),
                _clean(stop.get("description"), 1_000),
                _clean(day.get("title") or day.get("summary"), 500),
            )
            if part
        )
        return {
            "day_id": str(day.get("id") or ""),
            "day_date": str(day.get("date") or ""),
            "stop_id": str(stop.get("id") or ""),
            "stop_name": _clean(stop.get("name"), 500),
            "category": _clean(stop.get("type") or stop.get("category"), 200),
            "place": ", ".join(part for part in place_parts if part),
            "description": description[:2_000],
            "latitude": location.get("latitude", location.get("lat")),
            "longitude": location.get(
                "longitude", location.get("lon", location.get("lng"))
            ),
        }

    async def _async_fetch_vision_image(
        self,
        *,
        image_id: str,
        url: str,
        label: str,
    ) -> AssistantImageInput | None:
        """Fetch one bounded thumbnail for a semantic Vision call."""
        if not url or not str(url).startswith("https://"):
            return None
        session = async_get_clientsession(self.hass)
        headers = {
            "User-Agent": (
                "HomeAssistant-Roadplanner/"
                f"{getattr(self.provider, 'model', 'vision')} (media curation)"
            )
        }
        try:
            async with session.get(
                str(url),
                headers=headers,
                timeout=ClientTimeout(total=_VISION_IMAGE_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                mime_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
                if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                    return None
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > _VISION_MAX_IMAGE_BYTES:
                    return None
                data = await response.content.read(_VISION_MAX_IMAGE_BYTES + 1)
        except (ClientError, asyncio.TimeoutError, ValueError):
            return None
        if not data or len(data) > _VISION_MAX_IMAGE_BYTES:
            return None
        return AssistantImageInput(
            image_id=image_id,
            data=data,
            mime_type=mime_type,
            label=label,
        )

    async def _async_image_inputs(
        self,
        *,
        kind: str,
        candidates: list[dict[str, Any]],
    ) -> list[AssistantImageInput]:
        """Resolve bounded thumbnails after deterministic local preselection."""
        jobs: list[Any] = []
        for item in candidates[: self.media_vision_max_candidates]:
            image_id = str(item.get("id") or "").strip()
            if not image_id:
                continue
            if kind in {"travel", "trip"}:
                provider_item_id = str(item.get("provider_item_id") or "").strip()
                if not provider_item_id:
                    continue
                try:
                    url = await self.onedrive.async_thumbnail_url(provider_item_id, "large")
                except RoadplannerError:
                    continue
                label = " · ".join(
                    part
                    for part in (
                        _clean(item.get("name"), 300),
                        _clean(item.get("taken_at"), 100),
                        f"lokaler Score {item.get('selection_score')}"
                        if item.get("selection_score") is not None
                        else "",
                    )
                    if part
                )
            else:
                url = str(item.get("thumbnail_url") or item.get("image_url") or "")
                label = " · ".join(
                    part
                    for part in (
                        _clean(item.get("title") or item.get("alt"), 400),
                        _clean(item.get("provider"), 100),
                        _clean(item.get("license"), 100),
                        f"lokaler Score {item.get('selection_score')}"
                        if item.get("selection_score") is not None
                        else "",
                    )
                    if part
                )
            jobs.append(
                self._async_fetch_vision_image(
                    image_id=image_id,
                    url=url,
                    label=label,
                )
            )
        if not jobs:
            return []
        results = await asyncio.gather(*jobs, return_exceptions=True)
        inputs: list[AssistantImageInput] = []
        total = 0
        for result in results:
            if not isinstance(result, AssistantImageInput):
                continue
            if total + len(result.data) > _VISION_MAX_TOTAL_BYTES:
                break
            total += len(result.data)
            inputs.append(result)
        return inputs

    async def async_curate(
        self,
        *,
        trip_id: str,
        kind: str,
        day: dict[str, Any],
        stop: dict[str, Any],
        candidates: list[dict[str, Any]],
        existing: dict[str, Any] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Apply Vision only after local deterministic candidate reduction."""
        allowed_ids = [
            str(item.get("id") or "")
            for item in candidates[: self.media_vision_max_candidates]
            if str(item.get("id") or "")
        ]
        local_order = list(allowed_ids)
        context = self._vision_context(day, stop)
        model = str(getattr(self.provider, "model", "") or "")
        fingerprint = selection_fingerprint(
            kind=kind,
            context=context,
            candidates=candidates[: self.media_vision_max_candidates],
            model=model,
        )
        if (
            not force
            and isinstance(existing, dict)
            and existing.get("status") == "ready"
            and str(existing.get("fingerprint") or "") == fingerprint
        ):
            return deepcopy(existing)
        if (
            not force
            and kind == "trip"
            and isinstance(existing, dict)
            and existing.get("status") == "ready"
            and str(existing.get("cover_id") or "") in allowed_ids
        ):
            # A once-chosen trip cover is STICKY: it stays until the photo
            # disappears from the candidates or the user forces a fresh
            # evaluation. Without this, every synced photo batch (and every
            # model change) altered the fingerprint, Vision re-ran, and the
            # trip showed a different hero image again and again.
            return {**deepcopy(existing), "fingerprint": fingerprint}

        local_value = {
            "stop_id": str(stop.get("id") or ""),
            "kind": kind,
            "status": "local",
            "fingerprint": fingerprint,
            "selection_version": 1,
            "mode": "local",
            "model": None,
            "candidate_ids": allowed_ids,
            "cover_id": allowed_ids[0] if allowed_ids else None,
            "highlight_ids": allowed_ids[: self.media_vision_max_highlights],
            "rejected_ids": [],
            "reasons": {},
            "summary": "Lokale Vorauswahl nach Zuordnung, Qualität, Dubletten und Serien.",
            "usage": {},
            "selected_at": utc_now_iso(),
            "error": None,
        }
        if len(allowed_ids) < 2 or not self.vision_enabled:
            return local_value

        image_inputs = await self._async_image_inputs(kind=kind, candidates=candidates)
        if len(image_inputs) < 2:
            return {
                **local_value,
                "status": "local",
                "mode": "local_fallback",
                "error": "Zu wenige Bildvorschaudaten für die KI-Auswahl verfügbar",
            }
        input_ids = [item.image_id for item in image_inputs]
        reservation = await self.hass.async_add_executor_job(
            self.store.reserve_vision_call,
            trip_id,
            datetime.now(timezone.utc).date().isoformat(),
            self.media_vision_daily_limit,
        )
        if not reservation.get("reserved"):
            return {
                **local_value,
                "status": "quota_limited",
                "mode": "local_fallback",
                "error": "Tageslimit für KI-Bildauswahl erreicht",
                "usage": {"daily_limit": reservation},
            }

        system, prompt = build_vision_prompt(
            kind=kind,
            context=context,
            candidate_ids=input_ids,
            max_highlights=self.media_vision_max_highlights,
        )
        manual_cover_id = None
        if kind in {"travel", "trip"}:
            manual_field = "is_trip_cover" if kind == "trip" else "is_cover"
            manual_cover_id = next(
                (
                    str(item.get("id") or "")
                    for item in candidates
                    if item.get(manual_field)
                    and str(item.get("id") or "") in input_ids
                ),
                None,
            )
        try:
            result = await self.provider.async_analyze_images(
                system_instruction=system,
                prompt=prompt,
                images=image_inputs,
                schema=VISION_SELECTION_SCHEMA,
                max_output_tokens=3_072,
            )
            selection = normalize_vision_selection(
                result.value,
                allowed_ids=input_ids,
                local_order=[item for item in local_order if item in input_ids],
                max_highlights=self.media_vision_max_highlights,
                manual_cover_id=manual_cover_id,
            )
        except (RoadplannerError, asyncio.TimeoutError) as err:
            return {
                **local_value,
                "status": "error",
                "mode": "local_fallback",
                "error": str(err)[:1_000],
            }
        return {
            **local_value,
            "status": "ready",
            "mode": "hybrid_vision",
            "model": result.model_version or model or None,
            "candidate_ids": input_ids,
            "cover_id": selection["cover_id"],
            "highlight_ids": selection["highlight_ids"],
            "rejected_ids": selection["rejected_ids"],
            "reasons": selection["reasons"],
            "summary": selection["summary"],
            "usage": deepcopy(result.usage),
            "selected_at": utc_now_iso(),
            "error": None,
        }

