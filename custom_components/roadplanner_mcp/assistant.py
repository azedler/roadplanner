"""Conversational, review-only Roadplanner assistant.

The assistant reads the selected trip directly from Home Assistant on every
request. Chat and draft changes are stored only in memory and are isolated by
Home Assistant user and trip. Pressing "Änderungen prüfen" compiles the draft
against the latest active-trip revision and places a normal ChangeSet in the
existing review inbox. It never applies changes automatically.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import logging
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from homeassistant.util import dt as dt_util

from .assistant_context import AssistantContextBuilder
from .canonical_day import (
    canonical_day_stops,
    location_status,
)
from .assistant_plugins import (
    AssistantPluginRegistry,
    GeocodingAssistantPlugin,
)
from .assistant_prompt import (
    AUTONOMY_INSTRUCTIONS,
    CHAT_SYSTEM_PROMPT,
    COMPILE_SYSTEM_PROMPT,
    COPILOT_SYSTEM_PROMPT,
    PROVIDER_TEST_SYSTEM_PROMPT,
    json_context,
)
from .assistant_provider import (
    AssistantJsonResult,
    AssistantProvider,
    AssistantTextResult,
)
from .assistant_basket import (
    BASKET_SCHEMA,
    MAX_DIAGNOSTIC_RECORDS,
    AssistantSession,
    AssistantSessionStore,
    _basket_status_text,
    _repair_stale_remove_delta,
    _strip_unverified_basket_claims,
)
from .assistant_compile import (
    COMPILE_SCHEMA,
    _bounded_context,
    _prepare_compiled_operation_batch,
)
from .assistant_operation_sanitizer import (
    NoOperationChange,
    _day_detail,
    _known_ids,
    _needs_research,
    _sanitize_operation,
    overnight_alternative_gap,
    seed_position_state,
)
from .destination_intelligence import _URL_RE, _google_maps_host
from .assistant_shared import (
    _clean_reply,
    _clean_text,
    _normalize_text_items,
    _text_fingerprint,
    _utc_now_iso,
)
from .geocoding import GeocodingError, NominatimGeocoder
from .google_maps_link import async_resolve_google_maps_place
from .page_images import async_resolve_shared_page_place
from .pitch_options import get_overnight_plan, merge_assistant_overnight_plan
from .manager import RoadplannerManager
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

MAX_USER_TEXT = 12_000
MIN_CHAT_INTERVAL_SECONDS = 1.0



CHAT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "maxLength": 30_000},
        "basket_delta": BASKET_SCHEMA,
    },
    "required": ["reply", "basket_delta"],
    "additionalProperties": False,
}



class RoadplannerAssistant:
    """Natural-language travel assistant with a review-only change basket."""

    def __init__(
        self,
        manager: RoadplannerManager,
        *,
        provider: AssistantProvider | None,
        geocoder: NominatimGeocoder | None,
        enable_research: bool = True,
        max_history: int = 24,
        autonomy_level: str = "change_basket",
        copilot_enabled: bool = True,
        copilot_auto_briefing: bool = False,
        debug_enabled: bool = False,
        language: str = "de",
        travel_archive: Any | None = None,
    ) -> None:
        self.manager = manager
        self.provider = provider
        self.geocoder = geocoder
        self.enable_research = bool(enable_research)
        self.language = language or "de"
        self.autonomy_level = (
            autonomy_level
            if autonomy_level in AUTONOMY_INSTRUCTIONS
            else "change_basket"
        )
        self.copilot_enabled = bool(copilot_enabled)
        self.copilot_auto_briefing = bool(copilot_auto_briefing)
        self.debug_enabled = bool(debug_enabled)
        self.travel_archive = travel_archive
        self.sessions = AssistantSessionStore(max_history=max_history)
        self.context_builder = AssistantContextBuilder()
        self.plugins = AssistantPluginRegistry()
        if geocoder is not None:
            self.plugins.register(
                GeocodingAssistantPlugin(geocoder, language=self.language)
            )

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.provider.configured)

    @property
    def provider_name(self) -> str | None:
        return self.provider.name if self.provider else None

    @property
    def model(self) -> str | None:
        return self.provider.model if self.provider else None

    def _provider_health(self) -> dict[str, Any]:
        provider = self.provider
        health = getattr(provider, "health_snapshot", None) if provider else None
        if callable(health):
            try:
                value = health()
                return value if isinstance(value, dict) else {}
            except Exception:  # pragma: no cover - provider diagnostic boundary
                return {}
        return {}

    def state(self, user_id: str, trip_id: str) -> dict[str, Any]:
        state = self.sessions.snapshot(user_id, trip_id)
        today = dt_util.now().date().isoformat()
        state.update(
            {
                "configured": self.configured,
                "provider": self.provider_name,
                "model": self.model,
                "research_enabled": self.enable_research,
                "autonomy_level": self.autonomy_level,
                "change_basket_enabled": self.autonomy_level == "change_basket",
                "copilot_enabled": self.copilot_enabled,
                "copilot_auto_briefing": self.copilot_auto_briefing,
                "briefing_due": bool(
                    self.copilot_enabled
                    and self.copilot_auto_briefing
                    and state.get("last_briefing_date") != today
                ),
                "debug_enabled": self.debug_enabled,
                "geocoding_enabled": bool(self.geocoder and self.geocoder.enabled),
                "plugins": self.plugins.descriptors(),
                "provider_health": self._provider_health(),
            }
        )
        return state

    def _provider(self) -> AssistantProvider:
        if self.provider is None or not self.provider.configured:
            raise ValidationError(
                "Der Roadplanner-Assistent ist noch nicht eingerichtet. "
                "Bitte in den Integrationsoptionen einen Gemini API-Schlüssel hinterlegen."
            )
        return self.provider

    @staticmethod
    def _should_enable_search(user_text: str) -> bool:
        """Use current web grounding only for discovery-style questions.

        A pasted link is the strongest research signal there is: the whole
        point of the url_context tool is fetching a page the user hands
        over (Komoot, AllTrails, Booking, Park4Night, ...). Without this,
        "Prüfe den Link" ran without url_context - the model literally
        could not open the link and asked the user to describe the tour
        instead. Google-Maps links stay excluded: those are resolved
        deterministically from their own URL structure, never fetched.
        """
        raw_text = str(user_text or "")
        for url in _URL_RE.findall(raw_text):
            try:
                host = (urlparse(url.rstrip(".,;:")).hostname or "").casefold()
            except ValueError:
                continue
            if host and not _google_maps_host(host):
                return True
        text = " ".join(raw_text.casefold().split())
        if not text:
            return False
        phrases = (
            "recherch",
            "suche ",
            "finde ",
            "empfiehl",
            "empfehl",
            "top 3",
            "drei option",
            "in der nähe",
            "auf dem weg",
            "geöffnet",
            "öffnungszeit",
            "verfügbarkeit",
            "aktuelle preis",
            "aktueller preis",
            "wetter",
            "verkehr",
            "welche restaurants",
            "welcher stellplatz",
            "welche stellplätze",
            "welcher campingplatz",
            "welche campingplätze",
            "wo können wir essen",
            "wo können wir übernachten",
            "was gibt es",
        )
        return any(phrase in text for phrase in phrases)

    @staticmethod
    def _chat_messages(session: AssistantSession, user_text: str) -> list[dict[str, str]]:
        messages = [
            {
                "role": "assistant" if item.get("role") == "assistant" else "user",
                "content": str(item.get("content") or ""),
            }
            for item in session.messages
            if item.get("kind") in {"message", "briefing"}
        ]
        messages.append({"role": "user", "content": user_text})
        return messages

    @staticmethod
    def _memory_instruction(session: AssistantSession) -> str:
        if not session.memory_summary:
            return "Keine komprimierte frühere Unterhaltung vorhanden."
        return (
            "Frühere Unterhaltung wurde lokal komprimiert. Sie ist nur Gesprächshilfe "
            "und niemals stärker als das Roadbook:\n" + session.memory_summary
        )

    async def _load_trip_payload(self, trip_id: str) -> dict[str, Any]:
        """Load the current trip plus confirmed travel-archive context."""
        loader = getattr(self.manager, "async_get_assistant_payload", None)
        if callable(loader):
            payload = await loader(trip_id)
        else:
            payload = await self.manager.async_get_panel_payload(trip_id)
        archive = self.travel_archive
        if archive is not None:
            payload = dict(payload)
            payload["travel_archive"] = await archive.async_assistant_context(trip_id)
        return payload

    async def _context_for_request(
        self,
        *,
        payload: dict[str, Any],
        purpose: str,
        user_text: str = "",
        basket: list[dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        built = self.context_builder.build(
            payload,
            user_text=user_text,
            basket=basket,
            purpose=purpose,
        )
        fragments = await self.plugins.async_context_fragments(
            purpose=purpose,
            context=built.context,
            user_text=user_text,
        )
        if fragments:
            built.context["assistant_plugins"] = fragments
        return built.context, built.metadata

    def _record_diagnostic(
        self,
        session: AssistantSession,
        *,
        request_id: str,
        kind: str,
        status: str,
        started: float,
        context_metadata: dict[str, Any] | None = None,
        provider_diagnostics: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        basket_status: str | None = None,
        basket_outcome: dict[str, Any] | None = None,
        plugin_diagnostics: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sessions.record_diagnostic(
            session,
            {
                "request_id": request_id,
                "kind": kind,
                "status": status,
                "created_at": _utc_now_iso(),
                "duration_ms": int((time.monotonic() - started) * 1000),
                "context_metadata": dict(context_metadata or {}),
                "provider": dict(provider_diagnostics or {}),
                "usage": dict(usage or {}),
                "error": _clean_text(error, maximum=1_000) if error else None,
                "basket_status": basket_status,
                "basket_outcome": dict(basket_outcome or {}),
                "plugins": list(plugin_diagnostics or []),
            },
        )

    async def _generate_chat_response(
        self,
        *,
        session: AssistantSession,
        context: dict[str, Any],
        user_text: str,
    ) -> AssistantJsonResult:
        """Generate the visible reply and basket delta in one provider call."""
        provider = self._provider()
        system_instruction = (
            f"{CHAT_SYSTEM_PROMPT}\n\n"
            f"{AUTONOMY_INSTRUCTIONS[self.autonomy_level]}\n\n"
            f"CONVERSATION_MEMORY:\n{self._memory_instruction(session)}\n\n"
            "CURRENT_CHANGE_BASKET:\n"
            f"{json.dumps(session.basket, ensure_ascii=False, allow_nan=False)}\n\n"
            f"ROADBOOK_CONTEXT:\n{json_context(context)}"
        )
        return await provider.async_generate_json_result(
            system_instruction=system_instruction,
            messages=self._chat_messages(session, user_text),
            schema=CHAT_RESPONSE_SCHEMA,
            enable_search=(
                self.enable_research and self._should_enable_search(user_text)
            ),
            max_output_tokens=6_144,
            temperature=0.3,
        )

    async def async_chat(
        self,
        *,
        user_id: str,
        trip_id: str,
        text: str,
        client_request_id: str = "",
    ) -> dict[str, Any]:
        self._provider()
        text = str(text or "").strip()
        client_request_id = _clean_text(client_request_id, maximum=200)
        if not text:
            raise ValidationError("Bitte eine Nachricht eingeben")
        if len(text) > MAX_USER_TEXT:
            raise ValidationError(
                f"Eine Nachricht darf maximal {MAX_USER_TEXT} Zeichen enthalten"
            )
        lock = self.sessions.lock(user_id, trip_id)
        async with lock:
            session = self.sessions.session(user_id, trip_id)
            request_fingerprint = _text_fingerprint(text)
            cached = self.sessions.cached_result(
                session,
                client_request_id,
                request_fingerprint=request_fingerprint,
            )
            if cached is not None:
                cached["assistant"] = self.state(user_id, trip_id)
                cached["deduplicated"] = True
                return cached

            elapsed = time.monotonic() - session.last_chat_monotonic
            if session.last_chat_monotonic and elapsed < MIN_CHAT_INTERVAL_SECONDS:
                raise ValidationError(
                    "Bitte kurz warten, bevor du die nächste Nachricht sendest"
                )
            session.last_chat_monotonic = time.monotonic()
            request_id = f"chat-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            try:
                payload = await self._load_trip_payload(trip_id)
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="chat",
                    user_text=text,
                    basket=session.basket,
                )
                result = await self._generate_chat_response(
                    session=session,
                    context=context,
                    user_text=text,
                )
                reply = _clean_reply(result.value.get("reply"), maximum=30_000)
                if not reply:
                    raise ValidationError("Gemini hat keine lesbare Antwort geliefert")
                raw_delta = result.value.get("basket_delta")
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="chat",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:  # defensive provider/context boundary
                _LOGGER.exception(
                    "Unexpected Roadplanner assistant chat failure (%s)", request_id
                )
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="chat",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    "Der Assistent konnte die Anfrage nicht sicher verarbeiten. "
                    f"Bitte erneut versuchen (Anfrage {request_id})."
                ) from err

            if isinstance(raw_delta, dict):
                delta = raw_delta
            elif isinstance(raw_delta, list):
                delta = {
                    "add_or_update": raw_delta,
                    "remove_ids": [],
                    "note": "",
                }
            elif isinstance(raw_delta, str) and raw_delta.strip():
                delta = {
                    "add_or_update": [raw_delta],
                    "remove_ids": [],
                    "note": "",
                }
            else:
                delta = {}

            basket_update: dict[str, Any] = {
                "added": [],
                "updated": [],
                "removed_ids": [],
                "ignored_remove_ids": [],
                "rejected": [],
                "repaired": [],
                "note": "",
                "before_count": len(session.basket),
                "after_count": len(session.basket),
                "requested_count": 0,
                "added_count": 0,
                "updated_count": 0,
                "removed_count": 0,
                "ignored_remove_count": 0,
                "converted_remove_count": 0,
                "rejected_count": 0,
                "repaired_count": 0,
                "actual_change_count": 0,
                "changed": False,
                "delta_valid": isinstance(raw_delta, (dict, list, str)),
            }
            basket_status = "disabled"
            if self.autonomy_level == "change_basket":
                bounded_roadbook = _bounded_context(payload)
                delta, stale_remove_repaired = _repair_stale_remove_delta(
                    delta,
                    basket=session.basket,
                    roadbook_context=bounded_roadbook,
                    user_text=text,
                )
                basket_update = self.sessions.apply_delta(
                    session,
                    delta,
                    roadbook_context=bounded_roadbook,
                )
                if stale_remove_repaired:
                    repaired_items = list(basket_update.get("repaired", []))
                    repaired_items.append({
                        "kind": "delta",
                        "repairs": [
                            "veraltete remove_ids als Roadbook-Planungsabsicht erhalten"
                        ],
                    })
                    basket_update["repaired"] = repaired_items[:25]
                    basket_update["repaired_count"] = len(repaired_items)
                if basket_update.get("changed"):
                    basket_status = "changed"
                elif basket_update.get("rejected_count"):
                    basket_status = "invalid"
                else:
                    basket_status = "unchanged"

            delta_valid = bool(basket_update.get("delta_valid", True))
            reply, claim_removed = _strip_unverified_basket_claims(reply)
            if not reply:
                reply = "Ich habe deine Nachricht ausgewertet."

            actual_change_count = int(
                basket_update.get("actual_change_count") or 0
            )
            rejected_count = int(basket_update.get("rejected_count") or 0)
            repaired_count = int(basket_update.get("repaired_count") or 0)
            ignored_remove_count = int(
                basket_update.get("ignored_remove_count") or 0
            )
            after_count = int(basket_update.get("after_count") or 0)
            rejected_reasons = [
                _clean_text(item.get("reason"), maximum=240)
                for item in basket_update.get("rejected", [])
                if isinstance(item, dict) and item.get("reason")
            ][:3]
            basket_warning = ""

            should_show_basket_status = bool(
                actual_change_count
                or claim_removed
                or rejected_count
                or repaired_count
                or ignored_remove_count
                or not delta_valid
            )
            if should_show_basket_status:
                status_text = _basket_status_text(
                    actual_change_count, after_count
                )
                if repaired_count and actual_change_count:
                    noun = "Vorschlag" if repaired_count == 1 else "Vorschläge"
                    status_text += (
                        f" {repaired_count} {noun} wurden automatisch "
                        "in eine sichere Planungsabsicht vervollständigt."
                    )
                if ignored_remove_count == 1:
                    status_text += (
                        " Die angeforderte alte Vormerkung war bereits nicht mehr "
                        "im Korb; das Entfernen wurde als bereits erledigt behandelt."
                    )
                elif ignored_remove_count > 1:
                    status_text += (
                        f" {ignored_remove_count} angeforderte alte Vormerkungen waren "
                        "bereits nicht mehr im Korb; das Entfernen wurde als bereits "
                        "erledigt behandelt."
                    )
                if rejected_count:
                    details = "; ".join(rejected_reasons)
                    rejected_label = (
                        "1 Vorschlag konnte"
                        if rejected_count == 1
                        else f"{rejected_count} Vorschläge konnten"
                    )
                    status_text += (
                        f" {rejected_label} nicht erkannt werden"
                        f"{': ' + details if details else ''}."
                    )
                reply = f"{reply.rstrip()}\n\n{status_text}".strip()

            if (
                claim_removed
                and actual_change_count == 0
                and ignored_remove_count == 0
            ):
                basket_warning = (
                    "Gemini hat eine Vormerkung behauptet, aber der Server hat "
                    "keine verständliche Änderungsabsicht erkannt."
                )
            elif rejected_count:
                details = "; ".join(rejected_reasons)
                rejected_label = (
                    "1 Änderungsvorschlag konnte"
                    if rejected_count == 1
                    else f"{rejected_count} Änderungsvorschläge konnten"
                )
                basket_warning = (
                    f"{rejected_label} auch nach automatischer Vervollständigung "
                    "nicht übernommen werden"
                    f"{': ' + details if details else ''}."
                )
            elif not delta_valid and actual_change_count == 0:
                basket_warning = (
                    "Gemini lieferte keinen erkennbaren Änderungskorb. Die "
                    "Antwort wurde angezeigt, aber nichts vorgemerkt."
                )

            basket_outcome = {
                "status": basket_status,
                "before_count": int(basket_update.get("before_count") or 0),
                "after_count": after_count,
                "requested_count": int(
                    basket_update.get("requested_count") or 0
                ),
                "actual_change_count": actual_change_count,
                "added_count": int(basket_update.get("added_count") or 0),
                "updated_count": int(basket_update.get("updated_count") or 0),
                "removed_count": int(basket_update.get("removed_count") or 0),
                "rejected_count": rejected_count,
                "repaired_count": repaired_count,
                "rejected_reasons": rejected_reasons,
                "claim_corrected": claim_removed,
                "delta_valid": delta_valid,
            }

            self.sessions.append_message(session, role="user", content=text)
            assistant_message = self.sessions.append_message(
                session,
                role="assistant",
                content=reply,
                sources=[source.as_dict() for source in result.sources],
                metadata={
                    "basket_outcome": basket_outcome,
                    "basket_warning": basket_warning,
                },
            )

            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="chat",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
                basket_status=basket_status,
                basket_outcome=basket_outcome,
            )
            response = {
                "request_id": request_id,
                "client_request_id": client_request_id or None,
                "message": assistant_message,
                "basket_update": basket_update,
                "basket_outcome": basket_outcome,
                "basket_warning": basket_warning,
                "assistant": self.state(user_id, trip_id),
                "context_revision": context.get("revision"),
                "context_metadata": context_metadata,
                "model_version": result.model_version,
                "usage": result.usage,
                "provider_diagnostics": result.diagnostics,
                "logical_api_calls": 1,
                "deduplicated": False,
            }
            self.sessions.cache_result(
                session,
                client_request_id,
                response,
                request_fingerprint=request_fingerprint,
            )
            return response

    async def async_test(self, *, user_id: str, trip_id: str) -> dict[str, Any]:
        provider = self._provider()
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            request_id = f"test-{uuid4().hex[:12]}"
            started = time.monotonic()
            try:
                result = await provider.async_generate_text(
                    system_instruction=PROVIDER_TEST_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": "Verbindungstest"}],
                    enable_search=False,
                    max_output_tokens=64,
                    temperature=0.0,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="provider_test",
                    status="error",
                    started=started,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected assistant provider test failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="provider_test",
                    status="error",
                    started=started,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    f"Der Verbindungstest ist unerwartet fehlgeschlagen (Anfrage {request_id})."
                ) from err
            ok = result.text.strip().casefold().startswith("ok")
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="provider_test",
                status="ok" if ok else "unexpected_response",
                started=started,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
            )
            return {
                "ok": ok,
                "answer": result.text[:200],
                "request_id": request_id,
                "provider": self._provider_health(),
            }

    async def async_briefing(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        if not self.copilot_enabled:
            raise ValidationError("Der optionale Copilot ist deaktiviert")
        provider = self._provider()
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            request_id = f"brief-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            try:
                payload = await self._load_trip_payload(trip_id)
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="briefing",
                    user_text="Tagesbriefing",
                )
                result = await provider.async_generate_text(
                    system_instruction=(
                        f"{COPILOT_SYSTEM_PROMPT}\n\nROADBOOK_CONTEXT:\n{json_context(context)}"
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": "Erstelle jetzt das optionale Roadplanner-Tagesbriefing.",
                        }
                    ],
                    enable_search=self.enable_research,
                    max_output_tokens=2048,
                    temperature=0.25,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="briefing",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected copilot briefing failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="briefing",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                )
                raise ValidationError(
                    f"Das Tagesbriefing konnte nicht erstellt werden (Anfrage {request_id})."
                ) from err
            message = self.sessions.append_message(
                session,
                role="assistant",
                content=result.text,
                sources=[source.as_dict() for source in result.sources],
                kind="briefing",
            )
            session.last_briefing_date = dt_util.now().date().isoformat()
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="briefing",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=result.diagnostics,
                usage=result.usage,
            )
            return {
                "request_id": request_id,
                "message": message,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_diagnostics(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            return {
                "provider": self._provider_health(),
                "session": {
                    "trip_id": trip_id,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "memory_summary_characters": len(session.memory_summary),
                    "total_message_count": session.total_message_count,
                    "recent_message_count": len(session.messages),
                    "compacted_message_count": session.compacted_message_count,
                    "compaction_count": session.compaction_count,
                    "basket_count": len(session.basket),
                    "request_cache_count": len(session.request_cache),
                    "usage": deepcopy(session.usage_totals),
                },
                "plugins": self.plugins.descriptors(),
                "records": deepcopy(session.diagnostics[-MAX_DIAGNOSTIC_RECORDS:]),
            }

    async def async_clear(self, *, user_id: str, trip_id: str) -> dict[str, Any]:
        async with self.sessions.lock(user_id, trip_id):
            self.sessions.clear(user_id, trip_id)
            return self.state(user_id, trip_id)

    @staticmethod
    def _build_location_drafts(
        payload: dict[str, Any],
        *,
        day_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Build review-only GPS completion drafts from canonical day data."""
        selected_day_ids = {str(item) for item in day_ids or set() if str(item)}
        drafts: list[dict[str, Any]] = []
        touched_day_ids: set[str] = set()
        seen_stops: set[tuple[str, str]] = set()
        for day in payload.get("days", {}).get("days", []):
            if not isinstance(day, dict):
                continue
            day_id = _clean_text(day.get("id"), maximum=200)
            if selected_day_ids and day_id not in selected_day_ids:
                continue
            for stop in canonical_day_stops(day):
                if not isinstance(stop, dict) or location_status(stop) == "resolved":
                    continue
                stop_id = _clean_text(stop.get("id"), maximum=200)
                name = _clean_text(stop.get("name"), maximum=500)
                owner_day_id = _clean_text(
                    stop.get("_source_day_id") if stop.get("_inherited") else day_id,
                    maximum=200,
                )
                if not stop_id or not name or not owner_day_id:
                    continue
                identity = (owner_day_id, stop_id)
                if identity in seen_stops:
                    continue
                seen_stops.add(identity)
                location = (
                    stop.get("location")
                    if isinstance(stop.get("location"), dict)
                    else {}
                )
                details = (
                    stop.get("details")
                    if isinstance(stop.get("details"), dict)
                    else {}
                )
                geocoding = (
                    details.get("geocoding")
                    if isinstance(details.get("geocoding"), dict)
                    else {}
                )
                query_parts = [
                    _clean_text(geocoding.get("query"), maximum=500),
                    name,
                    _clean_text(location.get("label"), maximum=300),
                    _clean_text(location.get("city"), maximum=200),
                    _clean_text(location.get("country_code"), maximum=20),
                ]
                query_values: list[str] = []
                seen_values: set[str] = set()
                for value in query_parts:
                    key = value.casefold()
                    if value and key not in seen_values:
                        seen_values.add(key)
                        query_values.append(value)
                place_query = ", ".join(query_values)[:500]
                if not place_query:
                    continue
                inherited_note = " (Start vom Vortag)" if stop.get("_inherited") else ""
                drafts.append(
                    {
                        "action": "update",
                        "entity_type": "stop",
                        "target_id": stop_id,
                        "day_id": owner_day_id,
                        "place_query": place_query,
                        "summary": f"GPS-Daten prüfen/ergänzen: {name}{inherited_note}",
                        "reason": (
                            "Der konkrete Roadbook-Stopp besitzt noch keine eindeutig "
                            "bestätigten GPS-Daten. Roadplanner soll den Ort serverseitig "
                            "auflösen und die Auswahl vor dem Speichern prüfen."
                        ),
                        "values": {},
                    }
                )
                touched_day_ids.add(owner_day_id)
        return drafts, touched_day_ids

    async def async_add_location_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
        day_id: str,
    ) -> dict[str, Any]:
        """Add review-only GPS completion drafts for one Roadbook day."""
        payload = await self._load_trip_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "GPS-Daten können nur für die aktive Reise ergänzt werden"
            )
        day = next(
            (
                item
                for item in payload.get("days", {}).get("days", [])
                if isinstance(item, dict) and str(item.get("id") or "") == str(day_id)
            ),
            None,
        )
        if day is None:
            raise ValidationError(f"Reisetag nicht gefunden: {day_id}")
        drafts, _touched_days = self._build_location_drafts(
            payload,
            day_ids={str(day_id)},
        )
        if not drafts:
            raise ValidationError(
                "Für diesen Reisetag wurden keine offenen GPS-Zuordnungen gefunden"
            )
        delta = {
            "add_or_update": drafts,
            "remove_ids": [],
            "note": f"GPS-Vervollständigung für {day.get('title') or day_id}",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(
                session,
                delta,
                roadbook_context=payload,
            )
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die GPS-Vervollständigung konnte nicht vorgemerkt werden"
                )
            return {
                "draft_count": int(applied.get("added_count") or 0)
                + int(applied.get("updated_count") or 0),
                "day_count": 1,
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_trip_location_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
    ) -> dict[str, Any]:
        """Add review-only GPS completion drafts for the selected trip."""
        payload = await self._load_trip_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "GPS-Daten können nur für die aktive Reise ergänzt werden"
            )
        drafts, touched_day_ids = self._build_location_drafts(payload)
        if not drafts:
            raise ValidationError(
                "Für diese Reise wurden keine offenen GPS-Zuordnungen gefunden"
            )
        delta = {
            "add_or_update": drafts,
            "remove_ids": [],
            "note": "GPS-Vervollständigung für die ausgewählte Reise",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(
                session,
                delta,
                roadbook_context=payload,
            )
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die GPS-Vervollständigung konnte nicht vorgemerkt werden"
                )
            return {
                "draft_count": int(applied.get("added_count") or 0)
                + int(applied.get("updated_count") or 0),
                "day_count": len(touched_day_ids),
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_decision_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        decision: dict[str, Any],
        option: dict[str, Any],
    ) -> dict[str, Any]:
        """Place one explicitly selected decision option in the change basket."""
        if not isinstance(decision, dict) or not isinstance(option, dict):
            raise ValidationError("Entscheidungsoption ist unvollständig")
        title = _clean_text(option.get("title"), maximum=500)
        if not title:
            raise ValidationError("Entscheidungsoption besitzt keinen Titel")
        place_query = _clean_text(option.get("place_query"), maximum=500)
        linked_day_id = _clean_text(decision.get("linked_day_id"), maximum=200)
        notes_parts = [
            _clean_text(option.get("summary"), maximum=2_000),
            "Vorteile: " + "; ".join(
                _clean_text(item, maximum=300)
                for item in list(option.get("pros") or [])[:4]
                if _clean_text(item, maximum=300)
            ),
            "Nachteile: " + "; ".join(
                _clean_text(item, maximum=300)
                for item in list(option.get("cons") or [])[:4]
                if _clean_text(item, maximum=300)
            ),
        ]
        notes = "\n".join(part for part in notes_parts if part and not part.endswith(": "))
        delta = {
            "add_or_update": [
                {
                    "action": "plan",
                    "entity_type": "stop",
                    "summary": f"Ausgewählte Option übernehmen: {title}",
                    "day_id": linked_day_id,
                    "place_query": place_query,
                    "reason": "Vom Benutzer in einer Roadplanner-Entscheidungsvorlage ausdrücklich ausgewählt.",
                    "values": {
                        "name": title,
                        "type": _clean_text(option.get("stop_type"), maximum=100) or "waypoint",
                        "notes": notes,
                    },
                }
            ],
            "remove_ids": [],
            "note": f"Aus Entscheidungsvorlage: {_clean_text(decision.get('title'), maximum=500)}",
        }
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(session, delta)
            if not applied.get("changed"):
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(reason or "Die ausgewählte Option konnte nicht vorgemerkt werden")
            draft = (applied.get("added") or applied.get("updated") or [{}])[0]
            return {
                "draft": deepcopy(draft),
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_import_drafts(
        self,
        *,
        user_id: str,
        trip_id: str,
        delta: dict[str, Any],
        title: str,
        summary: str,
        document_id: str,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Add one analyzed universal import to the volatile change basket.

        The imported file remains a private archive document.  Only the
        normalized coarse intentions are copied into the basket; final IDs,
        revision and ChangeSet metadata are still produced by Home Assistant
        when the user presses ``Änderungen prüfen``.
        """
        if not isinstance(delta, dict):
            raise ValidationError("Die Importanalyse enthält keinen gültigen Änderungskorb")
        clean_title = _clean_text(title, maximum=500) or "Importierte Reiseübergabe"
        clean_summary = _clean_text(summary, maximum=8_000)
        questions = [
            _clean_text(item, maximum=2_000)
            for item in list(open_questions or [])[:30]
            if _clean_text(item, maximum=2_000)
        ]
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            applied = self.sessions.apply_delta(session, delta)
            if not applied.get("changed") and not session.basket:
                reason = (applied.get("rejected") or [{}])[0].get("reason")
                raise ValidationError(
                    reason or "Die importierte Übergabe enthält keine übernehmbaren Änderungen"
                )
            self.sessions.append_message(
                session,
                role="user",
                content=f"Datei als Übergabe importiert: {clean_title}",
                kind="attachment",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            response_parts = [
                f"Die Übergabe „{clean_title}“ wurde analysiert.",
                clean_summary,
                _basket_status_text(
                    int(applied.get("actual_change_count") or 0),
                    int(applied.get("after_count") or len(session.basket)),
                ),
            ]
            if questions:
                response_parts.append(
                    "Offene Punkte:\n" + "\n".join(f"- {item}" for item in questions)
                )
            message = self.sessions.append_message(
                session,
                role="assistant",
                content="\n\n".join(part for part in response_parts if part),
                kind="import",
                metadata={
                    "document_id": document_id,
                    "source": "universal_import",
                    "basket_outcome": applied,
                },
            )
            return {
                "message": message,
                "basket_result": applied,
                "assistant": self.state(user_id, trip_id),
            }

    async def async_add_import_context(
        self,
        *,
        user_id: str,
        trip_id: str,
        title: str,
        summary: str,
        document_id: str,
        preview_items: list[dict[str, Any]] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Place an import summary in the conversation without changing the basket."""
        clean_title = _clean_text(title, maximum=500) or "Importierte Datei"
        clean_summary = _clean_text(summary, maximum=8_000)
        items = [
            _clean_text(item.get("title"), maximum=500)
            for item in list(preview_items or [])[:20]
            if isinstance(item, dict) and _clean_text(item.get("title"), maximum=500)
        ]
        questions = [
            _clean_text(item, maximum=2_000)
            for item in list(open_questions or [])[:20]
            if _clean_text(item, maximum=2_000)
        ]
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            self.sessions.append_message(
                session,
                role="user",
                content=f"Datei zum Besprechen angehängt: {clean_title}",
                kind="attachment",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            parts = [f"Ich habe die Datei „{clean_title}“ als Gesprächskontext erfasst.", clean_summary]
            if items:
                parts.append("Erkannte Inhalte:\n" + "\n".join(f"- {item}" for item in items))
            if questions:
                parts.append("Offene Punkte:\n" + "\n".join(f"- {item}" for item in questions))
            message = self.sessions.append_message(
                session,
                role="assistant",
                content="\n\n".join(part for part in parts if part),
                kind="import",
                metadata={"document_id": document_id, "source": "universal_import"},
            )
            return {"message": message, "assistant": self.state(user_id, trip_id)}

    async def async_remove_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        draft_id: str,
    ) -> dict[str, Any]:
        draft_id = str(draft_id or "").strip()
        if not draft_id:
            raise ValidationError("Entwurfs-ID fehlt")
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            before = len(session.basket)
            session.basket = [item for item in session.basket if item.get("id") != draft_id]
            if len(session.basket) == before:
                raise ValidationError("Vorgemerkte Änderung wurde nicht gefunden")
            session.updated_at = _utc_now_iso()
            return self.state(user_id, trip_id)

    async def async_update_draft(
        self,
        *,
        user_id: str,
        trip_id: str,
        draft_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValidationError("Entwurfsänderung muss ein JSON-Objekt sein")
        async with self.sessions.lock(user_id, trip_id):
            session = self.sessions.session(user_id, trip_id)
            updated = self.sessions.update_draft(session, draft_id, patch)
            return {
                "draft": updated,
                "assistant": self.state(user_id, trip_id),
            }

    async def _compile_operations(
        self,
        *,
        context: dict[str, Any],
        basket: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> AssistantJsonResult:
        """Compile and optionally research the basket in one provider call."""
        provider = self._provider()
        payload = {
            "roadbook_context": context,
            "draft_basket": basket,
            "research_allowed": bool(
                self.enable_research and _needs_research(basket)
            ),
            "recent_conversation": [
                {
                    "role": message.get("role"),
                    "content": message.get("content"),
                }
                for message in messages[-12:]
                if message.get("kind") == "message"
            ],
        }
        return await provider.async_generate_json_result(
            system_instruction=COMPILE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, allow_nan=False),
                }
            ],
            schema=COMPILE_SCHEMA,
            enable_search=bool(
                self.enable_research and _needs_research(basket)
            ),
            max_output_tokens=16_384,
            temperature=0.05,
        )


    async def _async_overnight_alternative_operation(
        self,
        operations: list[dict[str, Any]],
        *,
        session: AssistantSession,
        context: dict[str, Any],
        full_days: list[dict[str, Any]] | None,
    ) -> dict[str, Any] | None:
        """Park an overnight alternative the model failed to park itself.

        The user's request is unmistakable ("die Alternative
        Übernachtungsoption heute Nacht" plus a link), but the compiled
        draft carried no overnight option for the day - live report: the
        change applied and then nothing showed up under "Stellplätze". The
        option is built server-side from the decision text, with the link
        resolved exactly like any other link the user shares, and stays a
        reviewable backup: it is added to the day's options, never
        activated, and never replaces an existing plan.
        """
        day_ids, _stop_ids, _preference_ids = _known_ids(context)
        gap = overnight_alternative_gap(
            operations,
            basket=session.basket,
            day_ids=day_ids,
            current_day_id=str(
                context.get("scope", {}).get("current_day_id") or ""
            ),
        )
        if gap is None:
            return None
        day_id, candidates = gap
        for candidate in candidates:
            url = str(candidate.get("url") or "")
            if not url or candidate.get("location"):
                continue
            resolved = await async_resolve_google_maps_place(self.manager.hass, url)
            if not resolved:
                resolved = await async_resolve_shared_page_place(
                    self.manager.hass, url
                )
            if not resolved:
                continue
            if resolved.get("name"):
                candidate.setdefault("name", str(resolved["name"])[:200])
            if "latitude" in resolved:
                candidate["location"] = {
                    "latitude": float(resolved["latitude"]),
                    "longitude": float(resolved["longitude"]),
                }
                candidate.setdefault(
                    "place_query", str(resolved.get("place_query") or "")
                )
        existing_day = _day_detail(context, day_id, full_days=full_days)
        merged = merge_assistant_overnight_plan(
            existing_day, {"options": candidates}, now=_utc_now_iso()
        )
        before = len(get_overnight_plan(existing_day or {}).get("options") or [])
        if len(merged.get("options") or []) <= before:
            return None
        # The merge deliberately drops MODEL coordinates; these came from
        # the user's own link, so they are re-attached to their option.
        for candidate in candidates:
            location = candidate.get("location")
            if not isinstance(location, dict):
                continue
            for option in merged.get("options") or []:
                same_url = candidate.get("url") and (
                    (option.get("source") or {}).get("url") == candidate.get("url")
                )
                if same_url and not (option.get("location") or {}).get("latitude"):
                    option["location"] = dict(location)
                    break
        _LOGGER.debug(
            "Salvaged an overnight alternative for day %s from the decision text",
            day_id,
        )
        return {
            "operation_id": f"op-overnight-alt-{uuid4().hex[:10]}",
            "action": "update",
            "entity_type": "day",
            "entity_id": day_id,
            "changes": {"details": {"overnight_plan": merged}},
            "reason": (
                "Im Änderungskorb als Übernachtungs-Alternative genannt - "
                "als Stellplatz-Option hinterlegt, nicht aktiviert."
            ),
        }

    async def async_prepare_review(
        self,
        *,
        user_id: str,
        trip_id: str,
        actor: str,
    ) -> dict[str, Any]:
        self._provider()
        lock = self.sessions.lock(user_id, trip_id)
        async with lock:
            session = self.sessions.session(user_id, trip_id)
            if not session.basket:
                raise ValidationError("Der Änderungskorb ist leer")
            request_id = f"prepare-{uuid4().hex[:12]}"
            started = time.monotonic()
            context_metadata: dict[str, Any] = {}
            plugin_diagnostics: list[dict[str, Any]] = []
            try:
                payload = await self._load_trip_payload(trip_id)
                if not payload.get("selected_is_active"):
                    raise ValidationError(
                        "Änderungen können nur für die aktive Reise vorbereitet werden. "
                        "Bitte diese Reise zuerst aktivieren."
                    )
                context, context_metadata = await self._context_for_request(
                    payload=payload,
                    purpose="compile",
                    basket=session.basket,
                )
                compile_result = await self._compile_operations(
                    context=context,
                    basket=session.basket,
                    messages=session.messages,
                )
                compiled = compile_result.value
                raw_operations = compiled.get("operations")
                if not isinstance(raw_operations, list):
                    raise ValidationError("Der Assistent hat keine Operationsliste geliefert")
                operations: list[dict[str, Any]] = []
                skipped_no_ops: list[str] = []

                prepared_raw_operations, new_day_refs = (
                    _prepare_compiled_operation_batch(raw_operations)
                )
                # Seed from the FULL stored day list, not context["days"]:
                # the compile context is a bounded detail window, but ID
                # validation accepts any day of the trip. Seeding from the
                # window made out-of-window days look empty, forcing their
                # stops to position 1.
                stored_days = payload.get("days")
                position_state: dict[str, list[dict[str, str]]] = (
                    seed_position_state(
                        stored_days.get("days")
                        if isinstance(stored_days, dict)
                        else None
                    )
                )
                for day_ref in new_day_refs:
                    position_state.setdefault(day_ref, [])
                # Cross-operation bookkeeping for the whole batch: stops/days
                # added or removed earlier in the SAME draft, so later
                # operations referencing them are handled at sanitize time
                # (clear error / legal same-batch reference) instead of
                # blowing up the whole draft at changeset ingestion.
                batch_refs: dict[str, Any] = {}
                stored_days = payload.get("days")
                full_day_list = (
                    stored_days.get("days") if isinstance(stored_days, dict) else None
                )
                if not isinstance(full_day_list, list):
                    full_day_list = None
                for index, raw in enumerate(prepared_raw_operations):
                    place_query = raw.get("place_query") if isinstance(raw, dict) else None
                    resolved_from_user_link = False
                    resolved_origin = ""
                    resolved_poi_name = ""
                    if isinstance(place_query, str) and place_query:
                        resolved_place = await async_resolve_google_maps_place(
                            self.manager.hass, place_query
                        )
                        if resolved_place:
                            resolved_origin = "user_google_maps_link"
                        elif "https://" in place_query:
                            # Other shared place links (naturkartan.se,
                            # Park4Night, campsite websites ...) resolve via
                            # their page's own geo metadata - deterministic,
                            # no AI (live report: a naturkartan stop stayed
                            # "Ort fehlt" although the user shared the page).
                            resolved_place = await async_resolve_shared_page_place(
                                self.manager.hass, place_query
                            )
                            if resolved_place:
                                resolved_origin = "user_shared_link"
                        if resolved_place:
                            raw["place_query"] = str(resolved_place["place_query"])
                            resolved_from_user_link = True
                            resolved_poi_name = str(
                                resolved_place.get("name") or ""
                            ).strip()
                    try:
                        sanitized = _sanitize_operation(
                            raw,
                            index=index,
                            context=context,
                            new_day_refs=new_day_refs,
                            basket=session.basket,
                            position_state=position_state,
                            batch_refs=batch_refs,
                            full_days=full_day_list,
                        )
                    except NoOperationChange as empty:
                        # One empty echo must not kill the whole draft.
                        _LOGGER.debug(
                            "Skipped empty assistant operation %s in %s: %s",
                            index + 1,
                            request_id,
                            empty,
                        )
                        skipped_no_ops.append(empty.note)
                        continue
                    if resolved_from_user_link and sanitized.get("place_query"):
                        # Server-set AFTER sanitizing, so the model can never
                        # supply it: this pin came from a link the user
                        # shared - the geocoding plugin treats its
                        # coordinates like a manually confirmed map point.
                        sanitized["place_query_origin"] = resolved_origin
                        if (
                            resolved_poi_name
                            and sanitized.get("action") == "add"
                            and sanitized.get("entity_type") == "stop"
                            and isinstance(sanitized.get("changes"), dict)
                        ):
                            # A NEW stop created from a user-shared link
                            # adopts the POI's real name - the model tends
                            # to label it only generically ("Essen", live
                            # report). A differing model label is kept in
                            # the notes so no intent is lost. Existing
                            # stops are never renamed this way.
                            changes = sanitized["changes"]
                            model_name = str(changes.get("name") or "").strip()
                            if (
                                model_name.casefold()
                                != resolved_poi_name.casefold()
                            ):
                                changes["name"] = resolved_poi_name[:500]
                                if model_name:
                                    notes = str(
                                        changes.get("notes") or ""
                                    ).strip()
                                    if (
                                        model_name.casefold()
                                        not in notes.casefold()
                                    ):
                                        changes["notes"] = (
                                            f"{notes}\n{model_name}".strip()
                                            if notes
                                            else model_name
                                        )
                    operations.append(sanitized)
                salvaged_option = await self._async_overnight_alternative_operation(
                    operations,
                    session=session,
                    context=context,
                    full_days=full_day_list,
                )
                if salvaged_option is not None:
                    operations.append(salvaged_option)
                open_questions, open_questions_omitted = _normalize_text_items(
                    compiled.get("open_questions"),
                    maximum_items=100,
                    maximum_text=2_000,
                )
                assumptions, assumptions_omitted = _normalize_text_items(
                    [compiled.get("assumptions"), skipped_no_ops],
                    maximum_items=100,
                    maximum_text=2_000,
                )
                source_notes = [
                    f"Quelle: {source.title} – {source.url}"
                    for source in compile_result.sources
                ]
                research_notes, research_notes_omitted = _normalize_text_items(
                    [compiled.get("research_notes"), source_notes],
                    maximum_items=100,
                    maximum_text=2_000,
                )
                enriched = await self.plugins.async_enrich_operations(
                    operations=operations,
                    open_questions=open_questions,
                    context=context,
                )
                operations = enriched.operations
                open_questions, plugin_questions_omitted = _normalize_text_items(
                    enriched.open_questions,
                    maximum_items=100,
                    maximum_text=2_000,
                )
                open_questions_omitted += plugin_questions_omitted
                plugin_diagnostics = enriched.diagnostics
                normalization_omissions = {
                    "open_questions": open_questions_omitted,
                    "assumptions": assumptions_omitted,
                    "research_notes": research_notes_omitted,
                }
                normalization_omissions = {
                    key: value
                    for key, value in normalization_omissions.items()
                    if value
                }
                if normalization_omissions:
                    _LOGGER.warning(
                        "Assistant compile response %s exceeded text-list limits; "
                        "omitted entries: %s",
                        request_id,
                        normalization_omissions,
                    )
                if not operations:
                    if skipped_no_ops:
                        raise ValidationError(
                            "Der Assistent hat nur Aktualisierungen ohne konkrete "
                            "Änderung geliefert. Bitte formuliere im Änderungskorb, "
                            "was genau geändert werden soll (z. B. Name, Zeit, Ort)."
                        )
                    reason = open_questions[0] if open_questions else "Keine sichere Änderung ableitbar"
                    raise ValidationError(
                        "Aus dem Änderungskorb konnte keine sicher ausführbare Operation erstellt werden: "
                        + reason
                    )

                revision = context.get("revision")
                trip = context.get("trip") if isinstance(context.get("trip"), dict) else {}
                canonical_trip_id = str(trip.get("id") or context.get("selected_trip_id") or "")
                if not canonical_trip_id or isinstance(revision, bool) or not isinstance(revision, int):
                    raise ValidationError("Aktuelle Reise-ID oder Revision konnte nicht gelesen werden")
                changeset_id = str(uuid4())
                title = _clean_text(compiled.get("title"), maximum=500) or "Roadplanner-Assistent"
                summary = _clean_text(compiled.get("summary"), maximum=5_000)
                changeset: dict[str, Any] = {
                    "kind": "roadplanner_changeset",
                    "version": 1,
                    "changeset_id": changeset_id,
                    "trip_id": canonical_trip_id,
                    "base_revision": revision,
                    "created_at": _utc_now_iso(),
                    "title": title,
                    "summary": summary,
                    "apply_mode": "review",
                    "operations": operations,
                    "open_questions": open_questions,
                    "assumptions": assumptions,
                    "research_notes": research_notes,
                    "metadata": {
                        "created_by": "roadplanner_assistant",
                        "provider": self.provider_name,
                        "model": compile_result.diagnostics.get("model") or self.model,
                        "user_id": user_id,
                        "actor": actor,
                        "basket_item_ids": [item.get("id") for item in session.basket],
                        "request_id": request_id,
                        "plugins": [item.get("plugin") for item in plugin_diagnostics],
                        "text_list_omissions": normalization_omissions,
                    },
                }
                source_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "basket": session.basket,
                            "revision": revision,
                            "changeset": changeset,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode("utf-8")
                ).hexdigest()
                ingest = await self.manager.async_ingest_external_changeset(
                    changeset=changeset,
                    title=title,
                    source="roadplanner_assistant",
                    external_id=changeset_id,
                    metadata={
                        "assistant": {
                            "provider": self.provider_name,
                            "model": compile_result.diagnostics.get("model") or self.model,
                            "user_id": user_id,
                            "actor": actor,
                            "review_only": True,
                            "request_id": request_id,
                        }
                    },
                    source_payload_sha256=source_digest,
                )
            except RoadplannerError as err:
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="prepare_review",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=str(err),
                    plugin_diagnostics=plugin_diagnostics,
                )
                raise ValidationError(f"{err} (Anfrage {request_id})") from err
            except Exception as err:
                _LOGGER.exception("Unexpected assistant review preparation failure (%s)", request_id)
                self._record_diagnostic(
                    session,
                    request_id=request_id,
                    kind="prepare_review",
                    status="error",
                    started=started,
                    context_metadata=context_metadata,
                    error=type(err).__name__,
                    plugin_diagnostics=plugin_diagnostics,
                )
                raise ValidationError(
                    "Der Änderungsentwurf konnte nicht sicher vorbereitet werden. "
                    f"Bitte erneut versuchen (Anfrage {request_id})."
                ) from err

            session.basket = []
            session.updated_at = _utc_now_iso()
            self.sessions.append_message(
                session,
                role="assistant",
                content=(
                    "Die vorgemerkten Änderungen wurden an die Änderungsübersicht "
                    "übergeben. Dort kannst du sie prüfen, übernehmen oder ablehnen. "
                    "Das Reisegespräch läuft weiter."
                ),
                kind="status",
            )
            self._record_diagnostic(
                session,
                request_id=request_id,
                kind="prepare_review",
                status="ok",
                started=started,
                context_metadata=context_metadata,
                provider_diagnostics=compile_result.diagnostics,
                usage=compile_result.usage,
                plugin_diagnostics=plugin_diagnostics,
            )
            return {
                "request_id": request_id,
                "changeset_id": changeset_id,
                "handoff": ingest.get("handoff"),
                "preview": ingest.get("preview"),
                "assistant": self.state(user_id, trip_id),
                "usage": compile_result.usage,
                "provider_diagnostics": compile_result.diagnostics,
                "logical_api_calls": 1,
            }

