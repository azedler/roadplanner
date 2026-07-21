"""Quota-aware Gemini REST client used by the Roadplanner assistant.

The API key is sent only in an HTTP header and is never embedded in URLs,
logs, prompts, ChangeSets, frontend payloads, or diagnostics. All logical
assistant calls share one bounded FIFO-style gate. This prevents bursts from
parallel users, applies a minimum spacing between requests, honours provider
cooldowns, retries transient failures, and can use a fallback model.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import random
import time
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse, ClientSession

from .const import INTEGRATION_VERSION

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .assistant_provider import (
    AssistantJsonResult,
    AssistantSource,
    AssistantTextResult,
)
from .roadplanner import RoadplannerError, ValidationError
from .structured_output import StructuredOutputError, parse_structured_object

_LOGGER = logging.getLogger(__name__)

_GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
_MAX_ERROR_BODY = 1_500
_TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
_DAILY_QUOTA_MARKERS = (
    "requestsperday",
    "requests per day",
    "per-day",
    "per day",
    "daily quota",
    "rpd",
)
BodyVariant = tuple[str, dict[str, Any]]
BodyFactory = Callable[[str], dict[str, Any] | list[BodyVariant]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


class GeminiApiError(RoadplannerError):
    """Raised for a sanitized Gemini API failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "gemini_error",
        status: int | None = None,
        retriable: bool = False,
        retry_after: float | None = None,
        model: str | None = None,
        allow_fallback: bool = False,
        provider_detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.retriable = retriable
        self.retry_after = retry_after
        self.model = model
        self.allow_fallback = allow_fallback
        self.provider_detail = provider_detail


class GeminiClient:
    """Provider-neutral Gemini generateContent client with quota protection."""

    def __init__(
        self,
        hass: Any,
        *,
        api_key: str,
        model: str,
        fallback_model: str = "gemini-2.5-flash",
        request_timeout: int = 75,
        retry_attempts: int = 2,
        min_request_interval: float = 2.0,
        max_queue: int = 8,
    ) -> None:
        self._session: ClientSession = async_get_clientsession(hass)
        self._api_key = api_key.strip()
        self._model = model.strip() or "gemini-3.5-flash"
        self._fallback_model = fallback_model.strip()
        if self._fallback_model == self._model:
            self._fallback_model = ""
        self._request_timeout = max(20, min(int(request_timeout), 180))
        self._retry_attempts = max(0, min(int(retry_attempts), 5))
        self._min_request_interval = max(0.5, min(float(min_request_interval), 15.0))
        self._max_queue = max(1, min(int(max_queue), 50))

        self._request_lock = asyncio.Lock()
        self._queued_requests = 0
        self._active_requests = 0
        self._last_dispatch_monotonic = 0.0
        self._last_completed_monotonic = 0.0
        self._cooldown_until_monotonic = 0.0
        self._cooldown_until_iso: str | None = None
        self._cooldown_reason: str | None = None
        self._preferred_request_modes: dict[str, str] = {}

        self._health: dict[str, Any] = {
            "total_calls": 0,
            "successful_calls": 0,
            "failed_calls": 0,
            "api_attempts": 0,
            "retried_calls": 0,
            "fallback_calls": 0,
            "rate_limited_calls": 0,
            "daily_quota_exhausted_calls": 0,
            "queue_rejected_calls": 0,
            "total_prompt_tokens": 0,
            "total_candidate_tokens": 0,
            "total_tokens": 0,
            "total_cached_tokens": 0,
            "total_thought_tokens": 0,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_code": None,
            "last_error_status": None,
            "last_model": None,
            "last_duration_ms": None,
            "last_attempt_count": 0,
            "last_retry_count": 0,
            "last_fallback_used": False,
            "last_queue_wait_ms": 0,
            "max_queue_depth_seen": 0,
            "last_usage": {},
            "compatibility_fallback_calls": 0,
            "last_compatibility_fallback_count": 0,
            "last_request_mode": None,
            "last_provider_error_detail": None,
        }

    @property
    def name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def fallback_model(self) -> str | None:
        return self._fallback_model or None

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    @staticmethod
    def _supports_structured_tools(model: str) -> bool:
        """Gemini 3 supports structured output together with built-in tools."""
        return str(model or "").casefold().startswith("gemini-3")

    def health_snapshot(self) -> dict[str, Any]:
        """Return a non-secret copy of provider health and queue information."""
        cooldown_remaining = max(
            0.0, self._cooldown_until_monotonic - time.monotonic()
        )
        return {
            **self._health,
            "configured": self.configured,
            "primary_model": self._model,
            "fallback_model": self.fallback_model,
            "request_timeout": self._request_timeout,
            "retry_attempts": self._retry_attempts,
            "min_request_interval": self._min_request_interval,
            "max_queue": self._max_queue,
            "queue_depth": self._queued_requests,
            "active_requests": self._active_requests,
            "cooldown_remaining_seconds": round(cooldown_remaining, 1),
            "cooldown_until": self._cooldown_until_iso if cooldown_remaining else None,
            "cooldown_reason": self._cooldown_reason if cooldown_remaining else None,
            "call_strategy": "single_call_chat_and_single_call_review",
        }

    @staticmethod
    def _endpoint(model: str) -> str:
        safe_model = quote(model, safe="-._")
        return f"{_GEMINI_API_ROOT}/{safe_model}:generateContent"

    @staticmethod
    def _contents(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user").casefold()
            role = "model" if role in {"assistant", "model"} else "user"
            text = str(message.get("content") or "").strip()
            if not text:
                continue
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append({"text": text})
            else:
                contents.append({"role": role, "parts": [{"text": text}]})
        if not contents:
            raise ValidationError("Für die Assistentenanfrage fehlt ein Text")
        return contents

    @staticmethod
    def _retry_after(response: ClientResponse) -> float | None:
        raw = str(response.headers.get("Retry-After") or "").strip()
        if not raw:
            return None
        try:
            return max(0.0, min(float(raw), 120.0))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                seconds = (parsed - datetime.now(timezone.utc)).total_seconds()
                return max(0.0, min(seconds, 120.0))
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _provider_error_detail(payload: Any, fallback: str = "") -> str:
        """Extract useful, sanitized Google error details without request data."""
        if not isinstance(payload, dict):
            return " ".join(str(fallback or "").split())[:_MAX_ERROR_BODY]
        error = payload.get("error")
        if not isinstance(error, dict):
            return " ".join(str(fallback or "").split())[:_MAX_ERROR_BODY]
        parts: list[str] = []
        message = str(error.get("message") or "").strip()
        if message:
            parts.append(message)
        details = error.get("details")
        if isinstance(details, list):
            for detail in details[:5]:
                if not isinstance(detail, dict):
                    continue
                violations = detail.get("fieldViolations")
                if isinstance(violations, list):
                    for item in violations[:8]:
                        if not isinstance(item, dict):
                            continue
                        field = str(item.get("field") or "").strip()
                        description = str(item.get("description") or "").strip()
                        text = ": ".join(value for value in (field, description) if value)
                        if text:
                            parts.append(text)
                quota = detail.get("violations")
                if isinstance(quota, list):
                    for item in quota[:8]:
                        if not isinstance(item, dict):
                            continue
                        subject = str(item.get("subject") or "").strip()
                        description = str(item.get("description") or "").strip()
                        text = ": ".join(value for value in (subject, description) if value)
                        if text:
                            parts.append(text)
        compact = " | ".join(" ".join(part.split()) for part in parts if part)
        return compact[:_MAX_ERROR_BODY]

    async def _error_from_response(
        self,
        response: ClientResponse,
        *,
        model: str,
    ) -> GeminiApiError:
        try:
            body = await response.text()
        except ClientError:
            body = ""
        payload: Any = None
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = None
        message = self._provider_error_detail(payload, body)
        retry_after = self._retry_after(response)

        if response.status in {401, 403}:
            return GeminiApiError(
                "Gemini hat den API-Schlüssel oder die Berechtigung abgelehnt. "
                "Bitte Roadplanner-Optionen prüfen.",
                code="authentication_failed",
                status=response.status,
                model=model,
            )
        if response.status == 429:
            lowered = message.casefold().replace("_", "")
            daily = any(marker in lowered for marker in _DAILY_QUOTA_MARKERS)
            if daily:
                return GeminiApiError(
                    "Das tägliche Gemini-Kontingent dieses Google-Projekts ist "
                    "ausgeschöpft. Bitte das Kontingent in AI Studio prüfen oder "
                    "nach dem Zurücksetzen erneut versuchen.",
                    code="daily_quota_exhausted",
                    status=response.status,
                    retriable=False,
                    retry_after=retry_after,
                    model=model,
                    allow_fallback=True,
                )
            return GeminiApiError(
                "Das Gemini-Rate-Limit ist vorübergehend erreicht. Roadplanner "
                "wartet kontrolliert und versucht die Anfrage erneut.",
                code="rate_limited",
                status=response.status,
                retriable=True,
                retry_after=retry_after if retry_after is not None else 20.0,
                model=model,
                allow_fallback=True,
            )
        if response.status == 400:
            visible = message or "Request contains an invalid argument."
            return GeminiApiError(
                "Gemini hat die Anfrage wegen eines inkompatiblen Parameters "
                f"abgelehnt ({visible})",
                code="invalid_request",
                status=response.status,
                model=model,
                provider_detail=visible,
            )
        if response.status in _TRANSIENT_STATUS or response.status >= 500:
            return GeminiApiError(
                "Gemini ist vorübergehend nicht verfügbar. Roadplanner wartet "
                "kontrolliert und versucht die Anfrage erneut.",
                code="temporarily_unavailable",
                status=response.status,
                retriable=True,
                retry_after=retry_after,
                model=model,
                allow_fallback=True,
            )
        suffix = f": {message}" if message else ""
        return GeminiApiError(
            f"Gemini-Aufruf ist mit HTTP {response.status} fehlgeschlagen{suffix}",
            code="http_error",
            status=response.status,
            model=model,
        )

    async def _post_once(
        self,
        body: dict[str, Any],
        *,
        model: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self._health["api_attempts"] += 1
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "x-goog-api-key": self._api_key,
            "User-Agent": f"HomeAssistant-Roadplanner/{INTEGRATION_VERSION}",
        }
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await self._session.post(
                    self._endpoint(model),
                    json=body,
                    headers=headers,
                    allow_redirects=False,
                )
                async with response:
                    if response.status < 200 or response.status >= 300:
                        raise await self._error_from_response(response, model=model)
                    payload = await response.json(content_type=None)
        except TimeoutError as err:
            raise GeminiApiError(
                "Gemini hat nicht rechtzeitig geantwortet. Roadplanner hat "
                "automatische Wiederholungen versucht.",
                code="timeout",
                retriable=True,
                model=model,
                allow_fallback=True,
            ) from err
        except GeminiApiError:
            raise
        except (ClientError, json.JSONDecodeError, TypeError, ValueError) as err:
            _LOGGER.debug("Gemini transport/JSON failure: %s", type(err).__name__)
            raise GeminiApiError(
                "Die Verbindung zu Gemini oder die Antwortverarbeitung ist fehlgeschlagen.",
                code="transport_error",
                retriable=True,
                model=model,
                allow_fallback=True,
            ) from err
        if not isinstance(payload, dict):
            raise GeminiApiError(
                "Gemini hat keine gültige Antwort geliefert",
                code="invalid_response",
                model=model,
            )
        return payload

    @staticmethod
    def _backoff(attempt_index: int, error: GeminiApiError) -> float:
        if error.retry_after is not None:
            return max(0.25, min(error.retry_after, 60.0))
        if error.code == "rate_limited":
            return min(10.0 * max(1, attempt_index), 45.0)
        base = min(1.5 * (2 ** max(0, attempt_index - 1)), 12.0)
        return base + random.uniform(0.0, 0.5)

    def _set_cooldown(self, error: GeminiApiError) -> None:
        if error.code == "daily_quota_exhausted":
            delay = 120.0
        elif error.code == "rate_limited":
            delay = error.retry_after if error.retry_after is not None else 20.0
        elif error.status in {500, 502, 503, 504}:
            delay = error.retry_after if error.retry_after is not None else 4.0
        else:
            return
        delay = max(1.0, min(float(delay), 120.0))
        target = time.monotonic() + delay
        if target <= self._cooldown_until_monotonic:
            return
        self._cooldown_until_monotonic = target
        self._cooldown_until_iso = (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self._cooldown_reason = error.code

    def _record_health(
        self,
        *,
        success: bool,
        diagnostics: dict[str, Any],
        usage: dict[str, Any] | None = None,
        error: GeminiApiError | None = None,
    ) -> None:
        self._health["total_calls"] += 1
        self._health["last_duration_ms"] = diagnostics.get("duration_ms")
        self._health["last_attempt_count"] = diagnostics.get("attempt_count", 0)
        self._health["last_retry_count"] = diagnostics.get("retry_count", 0)
        self._health["last_fallback_used"] = diagnostics.get("fallback_used", False)
        self._health["last_model"] = diagnostics.get("model")
        self._health["last_queue_wait_ms"] = diagnostics.get("queue_wait_ms", 0)
        compatibility_count = int(diagnostics.get("compatibility_fallback_count", 0) or 0)
        self._health["last_compatibility_fallback_count"] = compatibility_count
        self._health["last_request_mode"] = diagnostics.get("request_mode")
        if compatibility_count:
            self._health["compatibility_fallback_calls"] += 1
        if diagnostics.get("retry_count", 0):
            self._health["retried_calls"] += 1
        if diagnostics.get("fallback_used"):
            self._health["fallback_calls"] += 1

        usage = dict(usage or {})
        self._health["last_usage"] = usage
        token_map = {
            "promptTokenCount": "total_prompt_tokens",
            "candidatesTokenCount": "total_candidate_tokens",
            "totalTokenCount": "total_tokens",
            "cachedContentTokenCount": "total_cached_tokens",
            "thoughtsTokenCount": "total_thought_tokens",
        }
        for source, target in token_map.items():
            value = usage.get(source)
            if isinstance(value, int) and not isinstance(value, bool):
                self._health[target] += max(0, value)

        if success:
            self._health["successful_calls"] += 1
            self._health["last_success_at"] = _utc_now_iso()
            self._health["last_error_code"] = None
            self._health["last_error_status"] = None
            self._health["last_provider_error_detail"] = None
        else:
            self._health["failed_calls"] += 1
            self._health["last_error_at"] = _utc_now_iso()
            self._health["last_error_code"] = error.code if error else "unknown"
            self._health["last_error_status"] = error.status if error else None
            self._health["last_provider_error_detail"] = (
                error.provider_detail if error else None
            )
            if error and error.code == "rate_limited":
                self._health["rate_limited_calls"] += 1
            if error and error.code == "daily_quota_exhausted":
                self._health["daily_quota_exhausted_calls"] += 1
            if error and error.code == "queue_full":
                self._health["queue_rejected_calls"] += 1

    async def _acquire_gate(self, *, deadline: float) -> float:
        queued_at = time.monotonic()
        if self._queued_requests >= self._max_queue:
            error = GeminiApiError(
                "Zu viele Assistentenanfragen warten bereits. Bitte die aktuelle "
                "Antwort abwarten und danach erneut senden.",
                code="queue_full",
            )
            diagnostics = {
                "attempt_count": 0,
                "retry_count": 0,
                "fallback_used": False,
                "model": self._model,
                "attempted_models": [],
                "duration_ms": 0,
                "queue_wait_ms": 0,
            }
            self._record_health(success=False, diagnostics=diagnostics, error=error)
            raise error

        self._queued_requests += 1
        self._health["max_queue_depth_seen"] = max(
            self._health["max_queue_depth_seen"], self._queued_requests
        )
        acquired = False
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 1.0:
                raise TimeoutError
            async with asyncio.timeout(remaining):
                await self._request_lock.acquire()
            acquired = True
        except TimeoutError as err:
            error = GeminiApiError(
                "Die Assistentenanfrage hat zu lange in der Warteschlange gewartet.",
                code="queue_timeout",
                retriable=True,
                model=self._model,
            )
            diagnostics = {
                "attempt_count": 0,
                "retry_count": 0,
                "fallback_used": False,
                "model": self._model,
                "attempted_models": [],
                "duration_ms": int((time.monotonic() - queued_at) * 1000),
                "queue_wait_ms": int((time.monotonic() - queued_at) * 1000),
            }
            self._record_health(success=False, diagnostics=diagnostics, error=error)
            raise error from err
        finally:
            self._queued_requests = max(0, self._queued_requests - 1)

        if not acquired:  # defensive; kept for type/narrowing clarity
            raise GeminiApiError("Assistenten-Warteschlange konnte nicht geöffnet werden")
        self._active_requests = 1
        try:
            wait_until = max(
                self._last_dispatch_monotonic + self._min_request_interval,
                self._cooldown_until_monotonic,
            )
            delay = max(0.0, wait_until - time.monotonic())
            if delay:
                remaining = deadline - time.monotonic()
                if remaining <= delay + 2.0:
                    error = GeminiApiError(
                        "Gemini befindet sich noch in einer Schutzpause. Bitte nach "
                        "Ablauf der angezeigten Wartezeit erneut versuchen.",
                        code="cooldown_active",
                        retriable=True,
                        retry_after=delay,
                        model=self._model,
                    )
                    diagnostics = {
                        "attempt_count": 0,
                        "retry_count": 0,
                        "fallback_used": False,
                        "model": self._model,
                        "attempted_models": [],
                        "duration_ms": int((time.monotonic() - queued_at) * 1000),
                        "queue_wait_ms": int((time.monotonic() - queued_at) * 1000),
                    }
                    self._record_health(
                        success=False, diagnostics=diagnostics, error=error
                    )
                    raise error
                await asyncio.sleep(delay)
            self._last_dispatch_monotonic = time.monotonic()
            return (self._last_dispatch_monotonic - queued_at) * 1000
        except BaseException:
            # _post() has not entered its release-finally yet. Always release the
            # shared gate here, including task cancellation during cooldown wait.
            self._active_requests = 0
            if self._request_lock.locked():
                self._request_lock.release()
            raise

    def _release_gate(self) -> None:
        self._active_requests = 0
        self._last_completed_monotonic = time.monotonic()
        if self._request_lock.locked():
            self._request_lock.release()

    async def _post(
        self,
        body_or_factory: dict[str, Any] | BodyFactory,
        *,
        search_requested: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.configured:
            raise GeminiApiError(
                "Der Roadplanner-Assistent ist noch nicht eingerichtet. "
                "In den Integrationsoptionen fehlt der Gemini API-Schlüssel.",
                code="not_configured",
            )

        started = time.monotonic()
        deadline = started + self._request_timeout
        queue_wait_ms = await self._acquire_gate(deadline=deadline)
        models = [self._model]
        if self._fallback_model:
            models.append(self._fallback_model)
        attempt_count = 0
        retry_count = 0
        compatibility_fallback_count = 0
        attempted_models: list[str] = []
        last_error: GeminiApiError | None = None
        last_body: dict[str, Any] = {}
        last_mode = "default"

        try:
            for model_index, model in enumerate(models):
                attempted_models.append(model)
                produced = (
                    body_or_factory(model)
                    if callable(body_or_factory)
                    else body_or_factory
                )
                if isinstance(produced, list):
                    variants = produced
                else:
                    variants = [("default", produced)]
                preferred = self._preferred_request_modes.get(model)
                if preferred:
                    variants = sorted(
                        variants, key=lambda item: 0 if item[0] == preferred else 1
                    )

                stop_model = False
                for variant_index, (request_mode, body) in enumerate(variants):
                    last_mode = request_mode
                    last_body = body
                    attempts_for_variant = (
                        self._retry_attempts + 1
                        if model_index == 0 and variant_index == 0
                        else 1
                    )
                    for local_attempt in range(1, attempts_for_variant + 1):
                        remaining = deadline - time.monotonic()
                        if remaining < 3.0:
                            stop_model = True
                            break
                        attempt_count += 1
                        try:
                            payload = await self._post_once(
                                body,
                                model=model,
                                timeout_seconds=min(40.0, remaining),
                            )
                            usage = self._usage(payload)
                            self._preferred_request_modes[model] = request_mode
                            diagnostics = {
                                "attempt_count": attempt_count,
                                "retry_count": retry_count,
                                "compatibility_fallback_count": compatibility_fallback_count,
                                "fallback_used": model_index > 0,
                                "model": model,
                                "attempted_models": list(attempted_models),
                                "duration_ms": int((time.monotonic() - started) * 1000),
                                "queue_wait_ms": int(queue_wait_ms),
                                "search_requested": bool(search_requested),
                                "search_used": bool(body.get("tools")),
                                "structured_output": bool(
                                    body.get("generationConfig", {}).get("responseJsonSchema")
                                ),
                                "request_mode": request_mode,
                            }
                            self._record_health(
                                success=True, diagnostics=diagnostics, usage=usage
                            )
                            return payload, diagnostics
                        except GeminiApiError as err:
                            last_error = err
                            self._set_cooldown(err)
                            if err.code == "invalid_request":
                                # HTTP 400 is never retried unchanged. Move to the
                                # next, more compatible request shape exactly once.
                                if variant_index < len(variants) - 1:
                                    compatibility_fallback_count += 1
                                    break
                                stop_model = True
                                break
                            if (
                                err.code == "timeout"
                                and model_index == 0
                                and len(models) > 1
                                and err.allow_fallback
                            ):
                                # A second long wait against the same primary model
                                # rarely improves an interactive chat. Preserve the
                                # remaining deadline for the configured fallback.
                                stop_model = True
                                break
                            if not err.retriable:
                                stop_model = True
                                break
                            if local_attempt >= attempts_for_variant:
                                stop_model = True
                                break
                            delay = self._backoff(local_attempt, err)
                            if deadline - time.monotonic() <= delay + 3.0:
                                stop_model = True
                                break
                            retry_count += 1
                            await asyncio.sleep(delay)
                    if stop_model:
                        break
                    if last_error and last_error.code == "invalid_request":
                        continue
                    if last_error is None:
                        break

                if last_error is None:
                    break
                can_use_fallback = (
                    model_index == 0
                    and len(models) > 1
                    and last_error.allow_fallback
                    and deadline - time.monotonic() >= 5.0
                )
                if can_use_fallback:
                    continue
                break

            diagnostics = {
                "attempt_count": attempt_count,
                "retry_count": retry_count,
                "compatibility_fallback_count": compatibility_fallback_count,
                "fallback_used": len(attempted_models) > 1,
                "model": attempted_models[-1] if attempted_models else self._model,
                "attempted_models": attempted_models,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "queue_wait_ms": int(queue_wait_ms),
                "search_requested": bool(search_requested),
                "search_used": bool(last_body.get("tools")),
                "structured_output": bool(
                    last_body.get("generationConfig", {}).get("responseJsonSchema")
                ),
                "request_mode": last_mode,
            }
            final_error = last_error or GeminiApiError(
                "Gemini hat innerhalb des konfigurierten Zeitlimits nicht geantwortet.",
                code="deadline_exceeded",
                retriable=True,
                model=self._model,
            )
            self._record_health(
                success=False, diagnostics=diagnostics, error=final_error
            )
            raise final_error
        finally:
            self._release_gate()

    @staticmethod
    def _candidate(payload: dict[str, Any]) -> dict[str, Any]:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = payload.get("promptFeedback")
            reason = ""
            if isinstance(feedback, dict):
                reason = str(feedback.get("blockReason") or "")
            if reason:
                raise GeminiApiError(
                    f"Gemini hat die Anfrage aus Sicherheitsgründen blockiert ({reason}).",
                    code="safety_block",
                )
            raise GeminiApiError(
                "Gemini hat keinen Antwortkandidaten geliefert",
                code="empty_response",
            )
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise GeminiApiError(
                "Gemini hat einen ungültigen Antwortkandidaten geliefert",
                code="invalid_response",
            )
        return candidate

    @staticmethod
    def _text(candidate: dict[str, Any]) -> str:
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        texts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    texts.append(part["text"])
        text = "\n".join(texts).strip()
        if not text:
            finish_reason = str(candidate.get("finishReason") or "")
            raise GeminiApiError(
                "Gemini hat keinen Text geliefert"
                + (f" ({finish_reason})" if finish_reason else ""),
                code="empty_response",
            )
        return text

    @staticmethod
    def _sources(candidate: dict[str, Any]) -> list[AssistantSource]:
        metadata = candidate.get("groundingMetadata")
        chunks = metadata.get("groundingChunks") if isinstance(metadata, dict) else None
        if not isinstance(chunks, list):
            return []
        result: list[AssistantSource] = []
        seen: set[str] = set()
        for chunk in chunks:
            web = chunk.get("web") if isinstance(chunk, dict) else None
            if not isinstance(web, dict):
                continue
            uri = str(web.get("uri") or "").strip()
            title = str(web.get("title") or uri).strip()
            if not uri.startswith("https://") or uri in seen:
                continue
            seen.add(uri)
            result.append(AssistantSource(title=title[:300], url=uri[:2_000]))
            if len(result) >= 12:
                break
        return result

    @staticmethod
    def _supported_schema(value: Any) -> Any:
        """Remove constraints outside Gemini's supported JSON Schema subset."""
        if isinstance(value, dict):
            return {
                key: GeminiClient._supported_schema(child)
                for key, child in value.items()
                if key not in {"maxLength", "minLength", "pattern"}
            }
        if isinstance(value, list):
            return [GeminiClient._supported_schema(child) for child in value]
        return value

    @staticmethod
    def _usage(payload: dict[str, Any]) -> dict[str, Any]:
        raw = payload.get("usageMetadata")
        if not isinstance(raw, dict):
            return {}
        allowed = {
            "promptTokenCount",
            "candidatesTokenCount",
            "totalTokenCount",
            "cachedContentTokenCount",
            "thoughtsTokenCount",
        }
        return {key: value for key, value in raw.items() if key in allowed}

    async def async_generate_text(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        enable_search: bool,
        max_output_tokens: int = 4096,
        temperature: float = 0.35,
    ) -> AssistantTextResult:
        def body_for_model(model: str) -> list[BodyVariant]:
            generation_config: dict[str, Any] = {
                "maxOutputTokens": max(256, min(int(max_output_tokens), 16_384)),
            }
            if not model.casefold().startswith("gemini-3"):
                generation_config["temperature"] = max(
                    0.0, min(float(temperature), 2.0)
                )
            base: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": self._contents(messages),
                "generationConfig": generation_config,
            }
            variants: list[BodyVariant] = []
            if enable_search:
                with_search = dict(base)
                with_search["tools"] = [{"google_search": {}}]
                variants.append(("text_search", with_search))
            variants.append(("text_no_search", base))
            return variants

        payload, diagnostics = await self._post(
            body_for_model, search_requested=enable_search
        )
        candidate = self._candidate(payload)
        return AssistantTextResult(
            text=self._text(candidate),
            sources=self._sources(candidate),
            model_version=str(payload.get("modelVersion") or "") or None,
            usage=self._usage(payload),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _merge_usage(*items: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    result[key] = int(result.get(key, 0)) + value
        return result

    async def _repair_structured_output(
        self,
        *,
        invalid_text: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str | None]:
        """Ask Gemini once to repair one malformed structured response."""
        sanitized = str(invalid_text or "")[:24_000]

        def body_for_model(model: str) -> list[BodyVariant]:
            max_tokens = max(256, min(int(max_output_tokens), 16_384))
            config: dict[str, Any] = {
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
                "responseJsonSchema": self._supported_schema(schema),
            }
            if not model.casefold().startswith("gemini-3"):
                config["temperature"] = 0.0
            base = {
                "systemInstruction": {
                    "parts": [{
                        "text": (
                            "Repariere die bereitgestellte Modellantwort. Gib ausschließlich "
                            "ein gültiges JSON-Objekt zurück, das dem vorgegebenen Schema entspricht. "
                            "Erfinde keine fachlichen Daten. Übernimm nur Inhalte aus der fehlerhaften Antwort."
                        )
                    }]
                },
                "contents": self._contents([{
                    "role": "user",
                    "content": (
                        "Fehlerhafte Antwort:\n---\n"
                        + sanitized
                        + "\n---\nGib jetzt nur das reparierte JSON-Objekt zurück."
                    ),
                }]),
            }
            return [
                ("repair_schema", {**base, "generationConfig": config}),
                (
                    "repair_json_mime",
                    {
                        **base,
                        "generationConfig": {
                            key: value
                            for key, value in config.items()
                            if key != "responseJsonSchema"
                        },
                    },
                ),
            ]

        payload, diagnostics = await self._post(
            body_for_model,
            search_requested=False,
        )
        candidate = self._candidate(payload)
        repaired_text = self._text(candidate)
        value, normalization = parse_structured_object(repaired_text, schema)
        repair_diagnostics = {
            **diagnostics,
            "structured_output_repaired": True,
            "structured_output_normalization": normalization,
        }
        return (
            value,
            repair_diagnostics,
            self._usage(payload),
            str(payload.get("modelVersion") or "") or None,
        )

    async def async_generate_json_result(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        enable_search: bool = False,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
    ) -> AssistantJsonResult:
        def body_for_model(model: str) -> list[BodyVariant]:
            max_tokens = max(256, min(int(max_output_tokens), 32_768))
            base_config: dict[str, Any] = {"maxOutputTokens": max_tokens}
            if not model.casefold().startswith("gemini-3"):
                base_config["temperature"] = max(
                    0.0, min(float(temperature), 2.0)
                )
            base: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": self._contents(messages),
            }
            structured_config = {
                **base_config,
                "responseMimeType": "application/json",
                "responseJsonSchema": self._supported_schema(schema),
            }
            variants: list[BodyVariant] = []
            if enable_search and self._supports_structured_tools(model):
                with_search = {
                    **base,
                    "generationConfig": structured_config,
                    "tools": [{"google_search": {}}],
                }
                variants.append(("schema_search", with_search))
            variants.append((
                "schema_no_search",
                {**base, "generationConfig": structured_config},
            ))
            plain_messages = list(messages)
            plain_messages.append({
                "role": "user",
                "content": (
                    "Antworte ausschließlich mit genau einem gültigen JSON-Objekt. "
                    "Keine Markdown-Codeblöcke und kein Text außerhalb des JSON."
                ),
            })
            plain_base = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": self._contents(plain_messages),
                "generationConfig": {
                    **base_config,
                    "responseMimeType": "application/json",
                },
            }
            variants.append(("json_mime_only", plain_base))
            return variants

        payload, diagnostics = await self._post(
            body_for_model, search_requested=enable_search
        )
        candidate = self._candidate(payload)
        text = self._text(candidate)
        usage = self._usage(payload)
        model_version = str(payload.get("modelVersion") or "") or None
        try:
            value, normalization = parse_structured_object(text, schema)
            diagnostics = {
                **diagnostics,
                "structured_output_repaired": False,
                "structured_output_normalization": normalization,
            }
        except StructuredOutputError as err:
            _LOGGER.debug(
                "Gemini structured output required repair (%s, %s characters)",
                type(err).__name__,
                len(text),
            )
            try:
                value, repair_diagnostics, repair_usage, repair_model = (
                    await self._repair_structured_output(
                        invalid_text=text,
                        schema=schema,
                        max_output_tokens=max_output_tokens,
                    )
                )
            except (StructuredOutputError, GeminiApiError) as repair_err:
                raise GeminiApiError(
                    "Gemini hat kein zuverlässig lesbares JSON-Objekt geliefert.",
                    code="invalid_structured_output",
                    provider_detail=str(repair_err)[:500],
                ) from repair_err
            diagnostics = {
                **diagnostics,
                "structured_output_repaired": True,
                "repair_attempt_count": repair_diagnostics.get("attempt_count", 0),
                "repair_duration_ms": repair_diagnostics.get("duration_ms", 0),
                "structured_output_normalization": repair_diagnostics.get(
                    "structured_output_normalization", "repair_object"
                ),
            }
            usage = self._merge_usage(usage, repair_usage)
            model_version = repair_model or model_version
        return AssistantJsonResult(
            value=value,
            sources=self._sources(candidate),
            model_version=model_version,
            usage=usage,
            diagnostics=diagnostics,
        )


    async def async_analyze_binary(
        self,
        *,
        system_instruction: str,
        prompt: str,
        data: bytes,
        mime_type: str,
        filename: str,
        schema: dict[str, Any],
        max_output_tokens: int = 8192,
    ) -> AssistantJsonResult:
        """Analyze one PDF/image/text attachment using inline Gemini data.

        Roadplanner limits raw attachments to 10 MiB before this method so the
        complete JSON request remains comfortably below Gemini's documented
        20 MiB inline-request boundary. The original file is never logged.
        """
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValidationError("Für die Dokumentanalyse fehlt der Dateiinhalt")
        encoded = base64.b64encode(bytes(data)).decode("ascii")
        safe_mime = str(mime_type or "application/octet-stream").strip()
        safe_filename = str(filename or "document").strip()[:200]

        def body_for_model(model: str) -> list[BodyVariant]:
            max_tokens = max(256, min(int(max_output_tokens), 32_768))
            base_config: dict[str, Any] = {
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            }
            if not model.casefold().startswith("gemini-3"):
                base_config["temperature"] = 0.1
            contents = [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                f"Dateiname: {safe_filename}\nMIME-Type: {safe_mime}\n\n"
                                + str(prompt or "")
                            )
                        },
                        {
                            "inlineData": {
                                "mimeType": safe_mime,
                                "data": encoded,
                            }
                        },
                    ],
                }
            ]
            structured = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": contents,
                "generationConfig": {
                    **base_config,
                    "responseJsonSchema": self._supported_schema(schema),
                },
            }
            plain = {
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    f"Dateiname: {safe_filename}\nMIME-Type: {safe_mime}\n\n"
                                    + str(prompt or "")
                                    + "\n\nAntworte ausschließlich mit einem gültigen JSON-Objekt."
                                )
                            },
                            {
                                "inlineData": {
                                    "mimeType": safe_mime,
                                    "data": encoded,
                                }
                            },
                        ],
                    }
                ],
                "generationConfig": base_config,
            }
            return [("binary_schema", structured), ("binary_json_mime", plain)]

        payload, diagnostics = await self._post(body_for_model, search_requested=False)
        candidate = self._candidate(payload)
        text = self._text(candidate)
        try:
            value, normalization = parse_structured_object(text, schema)
        except StructuredOutputError as err:
            raise GeminiApiError(
                "Gemini hat für das Dokument kein zuverlässig lesbares JSON-Objekt geliefert.",
                code="invalid_structured_output",
            ) from err
        diagnostics = {
            **diagnostics,
            "structured_output_repaired": False,
            "structured_output_normalization": normalization,
        }
        return AssistantJsonResult(
            value=value,
            sources=[],
            model_version=str(payload.get("modelVersion") or "") or None,
            usage=self._usage(payload),
            diagnostics=diagnostics,
        )

    async def async_generate_json(
        self,
        *,
        system_instruction: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        enable_search: bool = False,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        result = await self.async_generate_json_result(
            system_instruction=system_instruction,
            messages=messages,
            schema=schema,
            enable_search=enable_search,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return result.value
