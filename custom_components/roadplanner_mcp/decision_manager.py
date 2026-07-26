"""Generate AI-suggested decision cards from assistant replies, enrich each
option concurrently (geocoding/imagery/routing), and manage decision
lifecycle (select/transfer/archive/delete).

The panel-facing "experience" payload attached to every response is built by
the manager's own async_panel_payload; this collaborator only knows about a
get_panel_payload callback injected at construction, so it never has to
import the (larger, later-extracted) payload builder directly.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import logging
import secrets
from time import monotonic
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .assistant_provider import AssistantProvider
from .decision_logic import (
    DecisionBaselineError,
    compact_decision_days,
    ensure_current_plan_option,
)
from .destination_images import DestinationImageProvider
from .experience_helpers import _all_days, _clean, _coordinate, _stops
from .experience_store import ExperienceStore, new_id, utc_now_iso
from .geocoding import GeocodingProvider
from .manager import RoadplannerManager
from .roadplanner import RoadplannerError, ValidationError
from .routing import OSRMRoutingClient, route_input_hash

_LOGGER = logging.getLogger(__name__)

_DECISION_GEOCODE_TIMEOUT_SECONDS = 12.0
_DECISION_IMAGE_TIMEOUT_SECONDS = 10.0
_DECISION_ROUTE_TIMEOUT_SECONDS = 15.0

_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "question": {"type": "string"},
        "linked_day_id": {"type": ["string", "null"]},
        "options": {
            "type": "array",
            "minItems": 2,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "place_query": {"type": "string"},
                    "stop_type": {"type": "string"},
                    "pros": {"type": "array", "items": {"type": "string"}},
                    "cons": {"type": "array", "items": {"type": "string"}},
                    "estimated_cost": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": ["number", "null"]},
                            "currency": {"type": ["string", "null"]},
                            "note": {"type": ["string", "null"]},
                        },
                    },
                    "is_current_plan": {"type": "boolean"},
                    "change_type": {"type": "string"},
                    "existing_stop_id": {"type": ["string", "null"]},
                },
                "required": ["title", "summary", "place_query", "stop_type", "pros", "cons"],
            },
        },
    },
    "required": ["title", "question", "options"],
}

_DECISION_PROMPT = """Du erstellst eine lokale Roadplanner-Entscheidungsvorlage aus genau einer bereits sichtbaren Assistentenantwort.
Extrahiere ausschließlich die konkreten Optionen, die in der Antwort wirklich genannt wurden. Erfinde keine ungeklärten Preise oder Orte.
Jede Option benötigt einen kurzen Titel, eine knappe Zusammenfassung, einen geocodierbaren Orts-/Anbieternamen in place_query, einen Roadplanner-Stopp-Typ sowie höchstens vier Vor- und Nachteile.
Wenn die Frage sinngemäß lautet, ob der bestehende Plan beibehalten oder durch eine Alternative ersetzt werden soll, MUSS der aktuelle Roadbook-Stopp als eigene erste Option enthalten sein. Setze dann is_current_plan=true, change_type=keep_existing und existing_stop_id auf die vorhandene Stop-ID. Alternativen erhalten is_current_plan=false und change_type=replace_existing.
Wenn die Antwort einen Reisetag eindeutig nennt, verwende ausschließlich eine vorhandene day_id aus dem mitgelieferten Roadbook. Andernfalls linked_day_id=null.
Die mitgelieferten Tage enthalten kompakte vorhandene Stopps. Verwende ihre IDs nur, wenn der Name und der Kontext eindeutig übereinstimmen.
Antworte ausschließlich im vorgegebenen JSON-Schema."""


class DecisionManager:
    """Generate, enrich, and manage AI-suggested decision cards."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        *,
        assistant: Any,
        provider: AssistantProvider | None,
        geocoder: GeocodingProvider | None,
        router: OSRMRoutingClient,
        image_provider: DestinationImageProvider,
        get_panel_payload: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.assistant = assistant
        self.provider = provider
        self.geocoder = geocoder
        self.router = router
        self.image_provider = image_provider
        self._get_panel_payload = get_panel_payload

    async def async_create_decision_from_message(self, *, user_id: str, trip_id: str, message_id: str) -> dict[str, Any]:
        if self.provider is None or not self.provider.configured:
            raise ValidationError("Der Assistent ist nicht konfiguriert")
        request_id = f"decision-{secrets.token_hex(6)}"
        assistant_state = self.assistant.state(user_id, trip_id)
        message = next((item for item in assistant_state.get("messages", []) if str(item.get("id") or "") == message_id and item.get("role") == "assistant"), None)
        if message is None:
            raise ValidationError("Assistentenantwort nicht gefunden")
        payload = await self.manager.async_get_assistant_payload(trip_id)
        days = _all_days(payload)
        compact_days = compact_decision_days(days)
        try:
            result = await self.provider.async_generate_json_result(
                system_instruction=_DECISION_PROMPT,
                messages=[{"role": "user", "content": json.dumps({"assistant_message": message.get("content"), "local_date": dt_util.now().date().isoformat(), "available_days": compact_days}, ensure_ascii=False)}],
                schema=_DECISION_SCHEMA,
                enable_search=False,
                max_output_tokens=4096,
                temperature=0.05,
            )
        except RoadplannerError as err:
            raise ValidationError(f"{err} (Anfrage {request_id})") from err
        raw = result.value
        options_raw = raw.get("options") if isinstance(raw.get("options"), list) else []
        options: list[dict[str, Any]] = []
        linked_day_id = str(raw.get("linked_day_id") or "").strip() or None
        valid_day_ids = {str(day.get("id") or "") for day in days}
        if linked_day_id not in valid_day_ids:
            linked_day_id = None
        for index, option_raw in enumerate(options_raw[:4]):
            if not isinstance(option_raw, dict):
                continue
            options.append(
                {
                    "id": f"option-{index + 1}",
                    "title": _clean(option_raw.get("title"), 300) or f"Option {index + 1}",
                    "summary": _clean(option_raw.get("summary"), 2_000),
                    "place_query": _clean(option_raw.get("place_query"), 500),
                    "stop_type": _clean(option_raw.get("stop_type"), 100) or "waypoint",
                    "pros": [_clean(item, 300) for item in list(option_raw.get("pros") or [])[:4] if _clean(item, 300)],
                    "cons": [_clean(item, 300) for item in list(option_raw.get("cons") or [])[:4] if _clean(item, 300)],
                    "estimated_cost": option_raw.get("estimated_cost") if isinstance(option_raw.get("estimated_cost"), dict) else {},
                    "details": {},
                    "is_current_plan": bool(option_raw.get("is_current_plan", False)),
                    "change_type": _clean(option_raw.get("change_type"), 80) or "choose",
                    "existing_stop_id": _clean(option_raw.get("existing_stop_id"), 200) or None,
                }
            )
        try:
            options, linked_day_id, baseline_required, current_plan_option_id = ensure_current_plan_option(
                assistant_message=str(message.get("content") or ""),
                decision_title=_clean(raw.get("title"), 400),
                question=_clean(raw.get("question"), 1_000),
                linked_day_id=linked_day_id,
                days=days,
                options=options,
            )
        except DecisionBaselineError as err:
            raise ValidationError(f"{err} (Anfrage {request_id})") from err
        if len(options) < 2:
            raise ValidationError("In dieser Antwort konnten nicht mindestens zwei konkrete Optionen erkannt werden")
        experience_payload = await self._get_panel_payload(trip_id)
        media_by_id = {
            str(item.get("id") or ""): item
            for item in experience_payload.get("media", [])
            if isinstance(item, dict)
        }
        for option in options:
            stop_id = str(option.get("existing_stop_id") or "")
            if not stop_id:
                continue
            featured_ids = experience_payload.get("presentation", {}).get("stop_highlights", {}).get(stop_id)
            media_ids = (
                featured_ids
                if isinstance(featured_ids, list) and featured_ids
                else experience_payload.get("by_stop", {}).get(stop_id, [])
            )
            own_media = [
                media_by_id[media_id]
                for media_id in media_ids
                if media_id in media_by_id
            ][:3]
            if own_media:
                option["images"] = [
                    {
                        "id": f"media-{str(item.get('id') or '')}",
                        "media_id": str(item.get("id") or ""),
                        "provider": "onedrive",
                        "alt": item.get("caption") or item.get("name") or option.get("title"),
                        "attribution": "Eigenes Reisefoto",
                    }
                    for item in own_media
                    if item.get("thumbnail_url")
                ]
            else:
                gallery = experience_payload.get("destination_galleries", {}).get(stop_id)
                if isinstance(gallery, dict):
                    gallery_images = list(gallery.get("images") or [])[:3]
                    if gallery_images:
                        option["images"] = deepcopy(gallery_images)
            if option.get("images"):
                option["image"] = deepcopy(option["images"][0])
        # Geocoding, image lookup and route enrichment are independent for each
        # option. Running the options concurrently avoids multiplying provider
        # latency by the number of slides. Every enrichment step is fail-open:
        # a missing image or route must never discard an otherwise usable choice.
        try:
            await asyncio.gather(
                *(self._enrich_option(option, linked_day_id, days) for option in options)
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # defensive decision boundary
            _LOGGER.exception("Unexpected decision enrichment failure (%s)", request_id)
            raise ValidationError(
                f"Die Entscheidungsoptionen konnten nicht sicher vorbereitet werden (Anfrage {request_id})."
            ) from err
        decision = await self.hass.async_add_executor_job(
            self.store.create_decision,
            trip_id,
            {
                "id": new_id("decision"),
                "title": _clean(raw.get("title"), 400) or "Entscheidung",
                "question": _clean(raw.get("question"), 1_000),
                "status": "open",
                "linked_day_id": linked_day_id,
                "source_message_id": message_id,
                "baseline_required": baseline_required,
                "current_plan_option_id": current_plan_option_id,
                "options": options,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        return {"decision": decision, "experience": await self._get_panel_payload(trip_id)}

    async def _enrich_option(self, option: dict[str, Any], linked_day_id: str | None, days: list[dict[str, Any]]) -> None:
        """Enrich one decision option without making the decision depend on it."""
        started = monotonic()
        details = option.setdefault("details", {})
        query = str(option.get("place_query") or "").strip()

        async def resolve_location() -> tuple[Any, list[Any]]:
            if _coordinate(option.get("location")) is not None:
                return None, []
            if not query or not self.geocoder or not self.geocoder.enabled:
                return None, []
            async with asyncio.timeout(_DECISION_GEOCODE_TIMEOUT_SECONDS):
                return await self.geocoder.async_resolve(query, language="de")

        async def resolve_image() -> dict[str, Any] | None:
            existing = option.get("image") if isinstance(option.get("image"), dict) else {}
            existing_images = [
                item for item in list(option.get("images") or [])[:3]
                if isinstance(item, dict) and (item.get("image_url") or item.get("media_id"))
            ]
            if existing.get("image_url") or existing.get("media_id"):
                return {
                    "primary": existing,
                    "images": existing_images or [existing],
                    "provider_errors": {},
                }
            if not query:
                return None
            async with asyncio.timeout(_DECISION_IMAGE_TIMEOUT_SECONDS):
                location = option.get("location") if isinstance(option.get("location"), dict) else {}
                images = await self.image_provider.async_search(
                    query,
                    limit=3,
                    latitude=location.get("latitude"),
                    longitude=location.get("longitude"),
                )
                if images.get("results"):
                    return {
                        "primary": images["results"][0],
                        "images": images["results"][:3],
                        "provider_errors": images.get("provider_errors") or {},
                    }
                return {
                    "primary": None,
                    "images": [],
                    "provider_errors": images.get("provider_errors") or {},
                }

        location_result, image_result = await asyncio.gather(
            resolve_location(),
            resolve_image(),
            return_exceptions=True,
        )

        if isinstance(location_result, BaseException):
            if isinstance(location_result, asyncio.CancelledError):
                raise location_result
            if isinstance(location_result, TimeoutError):
                details["geocoding_error"] = "Ortsauflösung hat das Zeitlimit überschritten"
            elif isinstance(location_result, RoadplannerError):
                details["geocoding_error"] = str(location_result)
            else:
                _LOGGER.warning(
                    "Decision option geocoding failed for %s: %s",
                    option.get("id"),
                    type(location_result).__name__,
                )
                details["geocoding_error"] = "Ortsauflösung ist vorübergehend fehlgeschlagen"
        else:
            best, candidates = location_result
            if best is not None:
                option["location"] = best.as_location()
                details["geocoding"] = best.as_provenance()
            elif candidates:
                details["geocoding_candidates"] = [
                    {
                        "location": candidate.as_location(),
                        "provenance": candidate.as_provenance(),
                    }
                    for candidate in candidates[:3]
                ]

        if isinstance(image_result, BaseException):
            if isinstance(image_result, asyncio.CancelledError):
                raise image_result
            if isinstance(image_result, TimeoutError):
                details["image_error"] = "Bildsuche hat das Zeitlimit überschritten"
            elif isinstance(image_result, RoadplannerError):
                details["image_error"] = str(image_result)
            else:
                _LOGGER.warning(
                    "Decision option image lookup failed for %s: %s",
                    option.get("id"),
                    type(image_result).__name__,
                )
                details["image_error"] = "Bildsuche ist vorübergehend fehlgeschlagen"
        elif image_result is not None:
            option["images"] = list(image_result.get("images") or [])[:3]
            if image_result.get("primary") is not None:
                option["image"] = image_result["primary"]
            if image_result.get("provider_errors"):
                details["image_provider_errors"] = image_result["provider_errors"]

        coord = _coordinate(option.get("location"))
        if coord is not None and linked_day_id and self.router.configured:
            day_index = next(
                (
                    index
                    for index, day in enumerate(days)
                    if str(day.get("id") or "") == linked_day_id
                ),
                None,
            )
            if day_index is not None:
                origin = self._day_origin(days, day_index)
                onward = self._day_onward(days, day_index)
                if origin is not None:
                    try:
                        points = [
                            {"latitude": origin[0], "longitude": origin[1]},
                            {"latitude": coord[0], "longitude": coord[1]},
                        ]
                        if onward is not None:
                            points.append(
                                {"latitude": onward[0], "longitude": onward[1]}
                            )
                        async with asyncio.timeout(_DECISION_ROUTE_TIMEOUT_SECONDS):
                            result = await self.router.async_calculate(
                                points,
                                input_hash=route_input_hash(
                                    points, self.router.profile
                                ),
                            )
                        option["route_metrics"] = {
                            "distance_km": round(
                                float(result.get("distance_m") or 0) / 1000, 1
                            ),
                            "drive_minutes": round(
                                float(result.get("duration_s") or 0) / 60
                            ),
                            "point_count": len(points),
                        }
                    except TimeoutError:
                        details["routing_error"] = (
                            "Routenberechnung hat das Zeitlimit überschritten"
                        )
                    except RoadplannerError as err:
                        details["routing_error"] = str(err)
                    except Exception as err:  # defensive enrichment boundary
                        _LOGGER.warning(
                            "Decision option routing failed for %s: %s",
                            option.get("id"),
                            type(err).__name__,
                        )
                        details["routing_error"] = (
                            "Routenberechnung ist vorübergehend fehlgeschlagen"
                        )

        details["enrichment_duration_ms"] = int((monotonic() - started) * 1000)

    @staticmethod
    def _day_origin(days: list[dict[str, Any]], index: int) -> tuple[float, float] | None:
        day = days[index]
        for stop in _stops(day):
            coord = _coordinate(stop.get("location"))
            if coord is not None:
                return coord
        if index > 0:
            for stop in reversed(_stops(days[index - 1])):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    return coord
        return None

    @staticmethod
    def _day_onward(days: list[dict[str, Any]], index: int) -> tuple[float, float] | None:
        day = days[index]
        for stop in reversed(_stops(day)):
            coord = _coordinate(stop.get("location"))
            if coord is not None:
                return coord
        if index + 1 < len(days):
            for stop in _stops(days[index + 1]):
                coord = _coordinate(stop.get("location"))
                if coord is not None:
                    return coord
        return None

    async def async_select_decision(self, trip_id: str, decision_id: str, option_id: str) -> dict[str, Any]:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        current = next((item for item in state["decisions"] if item.get("id") == decision_id), None)
        if current is None:
            raise ValidationError("Entscheidung nicht gefunden")
        if not any(item.get("id") == option_id for item in current.get("options", [])):
            raise ValidationError("Entscheidungsoption nicht gefunden")
        decision = await self.hass.async_add_executor_job(
            self.store.update_decision,
            trip_id,
            decision_id,
            {"selected_option_id": option_id, "status": "selected"},
        )
        return {"decision": decision, "experience": await self._get_panel_payload(trip_id)}

    async def async_transfer_decision(self, *, user_id: str, trip_id: str, decision_id: str) -> dict[str, Any]:
        state = await self.hass.async_add_executor_job(self.store.load, trip_id)
        decision = next((item for item in state["decisions"] if item.get("id") == decision_id), None)
        if decision is None:
            raise ValidationError("Entscheidung nicht gefunden")
        selected_id = decision.get("selected_option_id")
        option = next((item for item in decision.get("options", []) if item.get("id") == selected_id), None)
        if option is None:
            raise ValidationError("Bitte zuerst eine Option auswählen")
        if bool(option.get("is_current_plan")) or str(option.get("change_type") or "") == "keep_existing":
            updated = await self.hass.async_add_executor_job(
                self.store.update_decision,
                trip_id,
                decision_id,
                {"status": "selected"},
            )
            return {
                "decision": updated,
                "kept_existing": True,
                "assistant": self.assistant.state(user_id, trip_id),
                "experience": await self._get_panel_payload(trip_id),
            }
        result = await self.assistant.async_add_decision_draft(user_id=user_id, trip_id=trip_id, decision=decision, option=option)
        draft = result.get("draft") or {}
        updated = await self.hass.async_add_executor_job(self.store.update_decision, trip_id, decision_id, {"status": "transferred", "transferred_draft_id": draft.get("id")})
        return {"decision": updated, "assistant": result.get("assistant"), "experience": await self._get_panel_payload(trip_id)}

    async def async_archive_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        decision = await self.hass.async_add_executor_job(self.store.update_decision, trip_id, decision_id, {"status": "archived"})
        return {"decision": decision, "experience": await self._get_panel_payload(trip_id)}

    async def async_delete_decision(self, trip_id: str, decision_id: str) -> dict[str, Any]:
        await self.hass.async_add_executor_job(self.store.delete_decision, trip_id, decision_id)
        return {"ok": True, "experience": await self._get_panel_payload(trip_id)}
