"""Async manager for Roadplanner travel documents, expenses, and todos."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re
import secrets
import time
from typing import Any

from homeassistant.core import HomeAssistant

from .assistant_provider import AssistantProvider
from .roadplanner import ValidationError
from .travel_archive import (
    DOCUMENT_TYPES,
    EXPENSE_CATEGORIES,
    TravelArchiveStore,
    normalize_expense_category,
)

MAX_INLINE_ANALYSIS_BYTES = 10 * 1024 * 1024
UPLOAD_TICKET_SECONDS = 120
DOWNLOAD_TICKET_SECONDS = 300

_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "classification",
        "confidence",
        "title",
        "document_type",
        "provider",
        "summary",
        "booking_reference",
        "status",
        "start_at",
        "end_at",
        "check_in",
        "check_out",
        "address",
        "required_items",
        "important_notes",
        "suggested_links",
        "expense",
        "todos",
        "warnings",
    ],
    "properties": {
        "classification": {"type": "string", "enum": ["document", "expense", "document_expense"]},
        "confidence": {"type": "number"},
        "title": {"type": "string"},
        "document_type": {"type": "string"},
        "provider": {"type": "string"},
        "summary": {"type": "string"},
        "booking_reference": {"type": "string"},
        "status": {"type": "string"},
        "start_at": {"type": "string"},
        "end_at": {"type": "string"},
        "check_in": {"type": "string"},
        "check_out": {"type": "string"},
        "address": {"type": "string"},
        "required_items": {"type": "array", "items": {"type": "string"}},
        "important_notes": {"type": "array", "items": {"type": "string"}},
        "suggested_links": {
            "type": "object",
            "additionalProperties": False,
            "required": ["day_date", "day_title_hint", "stop_name_hint"],
            "properties": {
                "day_date": {"type": "string"},
                "day_title_hint": {"type": "string"},
                "stop_name_hint": {"type": "string"},
            },
        },
        "expense": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "present", "amount", "currency", "merchant", "category", "date",
                "payment_status", "payment_method", "notes",
            ],
            "properties": {
                "present": {"type": "boolean"},
                "amount": {"type": "number"},
                "currency": {"type": "string"},
                "merchant": {"type": "string"},
                "category": {"type": "string"},
                "date": {"type": "string"},
                "payment_status": {"type": "string"},
                "payment_method": {"type": "string"},
                "notes": {"type": "string"},
            },
        },
        "todos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "due_at", "priority", "notes"],
                "properties": {
                    "title": {"type": "string"},
                    "due_at": {"type": "string"},
                    "priority": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

_ANALYSIS_SYSTEM_PROMPT = """
Du analysierst ein Reisedokument oder einen Ausgabenbeleg für Roadplanner.

Liefere ausschließlich das geforderte strukturierte JSON. Erfinde nichts.
Wenn ein Feld nicht sicher erkennbar ist, verwende eine leere Zeichenkette,
false oder 0. Extrahiere nur Daten, die für Reiseplanung, Buchungen, Tagesaufgaben
oder Ausgaben relevant sind.

Wichtige Sicherheitsregeln:
- Keine vollständigen Kreditkarten-, Konto-, Pass- oder Ausweisnummern ausgeben.
- Buchungsnummern, Check-in-Codes und Ticketreferenzen dürfen extrahiert werden.
- Klassifiziere als document, expense oder document_expense.
- Ein normaler Kassenbon ist meist expense; eine Buchungsbestätigung mit Preis
  ist meist document_expense.
- Dokumenttypen müssen möglichst einer Roadplanner-Kategorie entsprechen.
- Ausgabenkategorie muss eine dieser Kategorien sein: fuel, charging, campsite,
  motorhome_site, parking, restaurant, snack, groceries, ferry, transport, other.
- Campingplätze als campsite, Wohnmobil-Stellplätze als motorhome_site, Imbiss
  oder Fast Food als snack und Lebensmittel als groceries klassifizieren.
- Datum im Format YYYY-MM-DD, Zeitpunkte möglichst ISO-8601.
- Todos nur aus klaren Fristen oder notwendigen Handlungen ableiten, z. B.
  Check-in, Boarding, Ausweisdokumente, Zahlung oder Stornofrist.
- Keine Aufgabe nur aus Werbetext oder unverbindlichen Empfehlungen erzeugen.
- Ordne den Inhalt anhand des beigefügten Roadbook-Index einem Datum oder Stopp
  nur dann zu, wenn die Zuordnung plausibel ist.
""".strip()


@dataclass(slots=True)
class UploadTicket:
    token: str
    user_id: str
    actor: str
    trip_id: str
    source: str
    keep_original: bool
    links: dict[str, Any]
    expires_monotonic: float


@dataclass(slots=True)
class DownloadTicket:
    token: str
    user_id: str
    trip_id: str
    document_id: str
    expires_monotonic: float
    remaining_uses: int = 16


def _clean(value: Any, maximum: int = 4_000) -> str:
    return str(value or "").strip()[:maximum]


def _safe_list(value: Any, maximum: int = 50) -> list[str]:
    source = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result: list[str] = []
    for item in source[:maximum]:
        text = _clean(item, 2_000)
        if text and text not in result:
            result.append(text)
    return result


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _redact_sensitive(text: str) -> str:
    """Remove common long digit sequences while preserving booking references."""
    text = re.sub(r"\b(?:\d[ -]?){13,19}\b", "[Zahlungsdaten entfernt]", text)
    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", "[Kontodaten entfernt]", text, flags=re.I)
    return text


class TravelArchiveManager:
    """Serialize archive mutations and issue short-lived transfer tickets."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: TravelArchiveStore,
        roadplanner_manager: Any,
        *,
        provider: AssistantProvider | None,
        max_upload_bytes: int,
        analysis_enabled: bool,
        default_currency: str,
    ) -> None:
        self.hass = hass
        self.store = store
        self.roadplanner_manager = roadplanner_manager
        self.provider = provider
        self.max_upload_bytes = max(1 * 1024 * 1024, min(int(max_upload_bytes), 100 * 1024 * 1024))
        self.analysis_enabled = bool(analysis_enabled)
        currency = str(default_currency or "EUR").strip().upper()
        self.default_currency = currency if len(currency) == 3 and currency.isalpha() else "EUR"
        self._lock = asyncio.Lock()
        self._tickets_lock = asyncio.Lock()
        self._upload_tickets: dict[str, UploadTicket] = {}
        self._download_tickets: dict[str, DownloadTicket] = {}

    @property
    def configured(self) -> bool:
        return bool(self.analysis_enabled and self.provider and self.provider.configured)

    async def async_initialize(self) -> None:
        await self.hass.async_add_executor_job(self.store.initialize)

    async def _trip_catalog(self, trip_id: str) -> dict[str, Any]:
        payload = await self.roadplanner_manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError("Die Dokumentreise konnte nicht geladen werden")
        day_map: dict[str, dict[str, Any]] = {}
        stop_map: dict[str, dict[str, Any]] = {}
        for day in payload.get("days", {}).get("days", []):
            if not isinstance(day, dict) or not day.get("id"):
                continue
            day_map[str(day["id"])] = day
            for stop in day.get("stops", []):
                if isinstance(stop, dict) and stop.get("id"):
                    stop_map[f"{day['id']}/{stop['id']}"] = stop
        return {"payload": payload, "days": day_map, "stops": stop_map}

    async def _validate_links(self, trip_id: str, links: dict[str, Any] | None) -> dict[str, Any]:
        links = dict(links or {})
        catalog = await self._trip_catalog(trip_id)
        valid_days: list[str] = []
        for raw in links.get("day_ids", []):
            day_id = str(raw or "").strip()
            if day_id and day_id in catalog["days"] and day_id not in valid_days:
                valid_days.append(day_id)
        valid_stops: list[dict[str, str]] = []
        for raw in links.get("stop_links", []):
            if not isinstance(raw, dict):
                continue
            day_id = str(raw.get("day_id") or "").strip()
            stop_id = str(raw.get("stop_id") or "").strip()
            if f"{day_id}/{stop_id}" in catalog["stops"]:
                item = {"day_id": day_id, "stop_id": stop_id}
                if item not in valid_stops:
                    valid_stops.append(item)
                if day_id not in valid_days:
                    valid_days.append(day_id)
        return {
            "day_ids": valid_days,
            "stop_links": valid_stops,
            "people": [str(item).strip()[:200] for item in links.get("people", []) if str(item).strip()][:100],
        }

    async def async_create_upload_ticket(
        self,
        *,
        user_id: str,
        actor: str,
        trip_id: str,
        source: str,
        keep_original: bool,
        links: dict[str, Any] | None,
    ) -> dict[str, Any]:
        await self._trip_catalog(trip_id)
        normalized_links = await self._validate_links(trip_id, links)
        token = secrets.token_urlsafe(36)
        ticket = UploadTicket(
            token=token,
            user_id=user_id,
            actor=actor,
            trip_id=trip_id,
            source=_clean(source, 100) or "upload",
            keep_original=bool(keep_original),
            links=normalized_links,
            expires_monotonic=time.monotonic() + UPLOAD_TICKET_SECONDS,
        )
        async with self._tickets_lock:
            self._cleanup_tickets_locked()
            self._upload_tickets[token] = ticket
        return {
            "upload_url": f"/api/roadplanner/archive/upload/{token}",
            "expires_in": UPLOAD_TICKET_SECONDS,
            "max_bytes": self.max_upload_bytes,
        }

    async def async_claim_upload_ticket(self, token: str) -> UploadTicket:
        async with self._tickets_lock:
            self._cleanup_tickets_locked()
            ticket = self._upload_tickets.pop(token, None)
        if ticket is None or ticket.expires_monotonic < time.monotonic():
            raise ValidationError("Upload-Ticket ist ungültig oder abgelaufen")
        return ticket

    async def async_finalize_upload(
        self,
        *,
        ticket: UploadTicket,
        temp_path: Path,
        original_filename: str,
        declared_mime: str,
    ) -> dict[str, Any]:
        size = await self.hass.async_add_executor_job(lambda: temp_path.stat().st_size)
        if size <= 0:
            await self.hass.async_add_executor_job(lambda: temp_path.unlink(missing_ok=True))
            raise ValidationError("Die hochgeladene Datei ist leer")
        if size > self.max_upload_bytes:
            await self.hass.async_add_executor_job(lambda: temp_path.unlink(missing_ok=True))
            raise ValidationError(
                f"Die Datei ist größer als das konfigurierte Limit von {self.max_upload_bytes // (1024 * 1024)} MB"
            )
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.create_uploaded_document(
                    trip_id=ticket.trip_id,
                    temp_path=temp_path,
                    original_filename=original_filename,
                    declared_mime=declared_mime,
                    source=ticket.source,
                    created_by=ticket.actor,
                    keep_original=ticket.keep_original,
                    links=ticket.links,
                )
            )

    async def async_create_download_ticket(
        self,
        *,
        user_id: str,
        trip_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        async with self._lock:
            await self.hass.async_add_executor_job(self.store.get_document_file, trip_id, document_id)
        token = secrets.token_urlsafe(36)
        ticket = DownloadTicket(
            token=token,
            user_id=user_id,
            trip_id=trip_id,
            document_id=document_id,
            expires_monotonic=time.monotonic() + DOWNLOAD_TICKET_SECONDS,
        )
        async with self._tickets_lock:
            self._cleanup_tickets_locked()
            self._download_tickets[token] = ticket
        return {
            "download_url": f"/api/roadplanner/archive/file/{token}",
            "expires_in": DOWNLOAD_TICKET_SECONDS,
        }

    async def async_resolve_download_ticket(self, token: str) -> tuple[Path, dict[str, Any]]:
        async with self._tickets_lock:
            self._cleanup_tickets_locked()
            ticket = self._download_tickets.get(token)
            if ticket is None or ticket.expires_monotonic < time.monotonic():
                raise ValidationError("Dokumentlink ist ungültig oder abgelaufen")
            ticket.remaining_uses -= 1
            if ticket.remaining_uses <= 0:
                self._download_tickets.pop(token, None)
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self.store.get_document_file,
                ticket.trip_id,
                ticket.document_id,
            )

    def _cleanup_tickets_locked(self) -> None:
        now = time.monotonic()
        self._upload_tickets = {
            token: item for token, item in self._upload_tickets.items() if item.expires_monotonic >= now
        }
        self._download_tickets = {
            token: item for token, item in self._download_tickets.items() if item.expires_monotonic >= now and item.remaining_uses > 0
        }

    @staticmethod
    def _roadbook_index(payload: dict[str, Any]) -> dict[str, Any]:
        days = []
        for day in payload.get("days", {}).get("days", [])[:180]:
            if not isinstance(day, dict):
                continue
            days.append(
                {
                    "id": day.get("id"),
                    "date": day.get("date"),
                    "title": day.get("title"),
                    "start": day.get("start"),
                    "end": day.get("end"),
                    "stops": [
                        {"id": stop.get("id"), "name": stop.get("name"), "type": stop.get("type")}
                        for stop in day.get("stops", [])[:100]
                        if isinstance(stop, dict)
                    ],
                }
            )
        return {
            "trip_id": payload.get("selected_trip_id"),
            "trip_title": payload.get("summary", {}).get("trip", {}).get("title"),
            "days": days,
        }

    @staticmethod
    def _normalize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
        classification = str(raw.get("classification") or "document")
        if classification not in {"document", "expense", "document_expense"}:
            classification = "document"
        document_type = str(raw.get("document_type") or "other")
        if document_type not in DOCUMENT_TYPES:
            document_type = "other"
        expense_raw = raw.get("expense") if isinstance(raw.get("expense"), dict) else {}
        amount = max(0.0, _safe_float(expense_raw.get("amount"), 0.0))
        currency = _clean(expense_raw.get("currency"), 3).upper()
        if len(currency) != 3 or not currency.isalpha():
            currency = ""
        category = normalize_expense_category(expense_raw.get("category"))
        payment_status = str(expense_raw.get("payment_status") or "unknown")
        if payment_status not in {"planned", "paid", "refundable", "refunded", "cancelled", "unknown"}:
            payment_status = "unknown"
        todos = []
        for item in list(raw.get("todos") or [])[:100]:
            if not isinstance(item, dict):
                continue
            title = _clean(item.get("title"), 1000)
            if not title:
                continue
            priority = str(item.get("priority") or "normal")
            if priority not in {"low", "normal", "high"}:
                priority = "normal"
            todos.append(
                {
                    "title": title,
                    "due_at": _clean(item.get("due_at"), 100),
                    "priority": priority,
                    "notes": _clean(item.get("notes"), 4000),
                }
            )
        suggested = raw.get("suggested_links") if isinstance(raw.get("suggested_links"), dict) else {}
        extracted = {
            "booking_reference": _redact_sensitive(_clean(raw.get("booking_reference"), 500)),
            "status": _clean(raw.get("status"), 100),
            "start_at": _clean(raw.get("start_at"), 100),
            "end_at": _clean(raw.get("end_at"), 100),
            "check_in": _clean(raw.get("check_in"), 500),
            "check_out": _clean(raw.get("check_out"), 500),
            "address": _clean(raw.get("address"), 1000),
            "required_items": [_redact_sensitive(item) for item in _safe_list(raw.get("required_items"), 100)],
            "important_notes": [_redact_sensitive(item) for item in _safe_list(raw.get("important_notes"), 100)],
        }
        return {
            "classification": classification,
            "confidence": max(0.0, min(_safe_float(raw.get("confidence"), 0.0), 1.0)),
            "title": _clean(raw.get("title"), 500),
            "document_type": document_type,
            "provider": _clean(raw.get("provider"), 500),
            "summary": _redact_sensitive(_clean(raw.get("summary"), 8000)),
            "suggested_links": {
                "day_date": _clean(suggested.get("day_date"), 10),
                "day_title_hint": _clean(suggested.get("day_title_hint"), 500),
                "stop_name_hint": _clean(suggested.get("stop_name_hint"), 500),
            },
            "expense": {
                "present": bool(expense_raw.get("present") and amount >= 0),
                "amount": round(amount, 2),
                "currency": currency,
                "merchant": _clean(expense_raw.get("merchant"), 500),
                "category": category,
                "date": _clean(expense_raw.get("date"), 10),
                "payment_status": payment_status,
                "payment_method": _clean(expense_raw.get("payment_method"), 100),
                "notes": _redact_sensitive(_clean(expense_raw.get("notes"), 4000)),
            },
            "todos": todos,
            "warnings": [_redact_sensitive(item) for item in _safe_list(raw.get("warnings"), 100)],
            "extracted": extracted,
        }

    @staticmethod
    def _resolve_link_suggestions(analysis: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        suggestion = analysis.get("suggested_links", {})
        day_date = str(suggestion.get("day_date") or "")
        day_hint = str(suggestion.get("day_title_hint") or "").casefold()
        stop_hint = str(suggestion.get("stop_name_hint") or "").casefold()
        days = payload.get("days", {}).get("days", [])
        day_candidates: list[dict[str, Any]] = []
        for day in days:
            if not isinstance(day, dict):
                continue
            score = 0
            if day_date and day.get("date") == day_date:
                score += 5
            if day_hint:
                haystack = " ".join(str(day.get(key) or "") for key in ("title", "start", "end")).casefold()
                if day_hint in haystack or haystack in day_hint:
                    score += 2
            if score:
                day_candidates.append({"day": day, "score": score})
        day_candidates.sort(key=lambda item: item["score"], reverse=True)
        resolved_days = []
        resolved_stops = []
        if day_candidates and (
            len(day_candidates) == 1 or day_candidates[0]["score"] > day_candidates[1]["score"]
        ):
            selected = day_candidates[0]["day"]
            resolved_days.append(str(selected.get("id")))
            if stop_hint:
                stop_candidates = []
                for stop in selected.get("stops", []):
                    if not isinstance(stop, dict):
                        continue
                    name = str(stop.get("name") or "").casefold()
                    if stop_hint in name or name in stop_hint:
                        stop_candidates.append(stop)
                if len(stop_candidates) == 1:
                    resolved_stops.append(
                        {"day_id": str(selected.get("id")), "stop_id": str(stop_candidates[0].get("id"))}
                    )
        return {
            "day_ids": resolved_days,
            "stop_links": resolved_stops,
            "people": [],
        }

    async def async_analyze_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        actor: str,
    ) -> dict[str, Any]:
        if not self.analysis_enabled:
            raise ValidationError("Die KI-Dokumentanalyse ist in den Roadplanner-Optionen deaktiviert")
        provider = self.provider
        if provider is None or not provider.configured:
            raise ValidationError("Für die Dokumentanalyse ist ein konfigurierter Gemini-Provider erforderlich")
        async with self._lock:
            path, document = await self.hass.async_add_executor_job(
                self.store.get_document_file,
                trip_id,
                document_id,
            )
        if int(document.get("size_bytes") or 0) > MAX_INLINE_ANALYSIS_BYTES:
            raise ValidationError(
                "Die Datei wurde sicher gespeichert, ist für die direkte KI-Analyse aber größer als 10 MB. "
                "Bitte die Zuordnung manuell ergänzen oder eine kleinere PDF-/Bildversion verwenden."
            )
        data = await self.hass.async_add_executor_job(path.read_bytes)
        payload = await self.roadplanner_manager.async_get_assistant_payload(trip_id)
        prompt = (
            "Analysiere die angehängte Datei für die folgende Reise. Nutze den Index nur zur "
            "Zuordnung, nicht um fehlende Dokumentdaten zu erfinden.\n\nROADBOOK_INDEX:\n"
            + json.dumps(self._roadbook_index(payload), ensure_ascii=False, separators=(",", ":"))
        )
        analyzer = getattr(provider, "async_analyze_binary", None)
        if not callable(analyzer):
            raise ValidationError("Der konfigurierte Assistentenprovider unterstützt noch keine Dokumentanalyse")
        result = await analyzer(
            system_instruction=_ANALYSIS_SYSTEM_PROMPT,
            prompt=prompt,
            data=data,
            mime_type=str(document.get("mime_type") or "application/octet-stream"),
            filename=str(document.get("original_filename") or "document"),
            schema=_ANALYSIS_SCHEMA,
            max_output_tokens=8_192,
        )
        raw_value = result.value if hasattr(result, "value") else result
        if not isinstance(raw_value, dict):
            raise ValidationError("Gemini hat keine verwertbare Dokumentanalyse geliefert")
        analysis = self._normalize_analysis(raw_value)
        analysis["resolved_links"] = self._resolve_link_suggestions(analysis, payload)
        if hasattr(result, "model_version"):
            analysis["model_version"] = result.model_version
        if hasattr(result, "usage"):
            analysis["usage"] = result.usage
        async with self._lock:
            document = await self.hass.async_add_executor_job(
                lambda: self.store.set_document_analysis(
                    trip_id=trip_id,
                    document_id=document_id,
                    analysis=analysis,
                    actor=actor,
                )
            )
        return {"document": document, "analysis": analysis}

    async def async_confirm_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        patch: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        links = await self._validate_links(trip_id, patch.get("links"))
        normalized_patch = dict(patch)
        normalized_patch["links"] = links
        expense = normalized_patch.get("expense")
        if isinstance(expense, dict):
            day_id = str(expense.get("day_id") or "").strip()
            stop_id = str(expense.get("stop_id") or "").strip()
            if day_id:
                extra_links = await self._validate_links(
                    trip_id,
                    {
                        "day_ids": [day_id],
                        "stop_links": ([{"day_id": day_id, "stop_id": stop_id}] if stop_id else []),
                    },
                )
                if day_id not in extra_links["day_ids"]:
                    raise ValidationError("Die Ausgabenzuordnung verweist auf einen unbekannten Reisetag")
                if stop_id and not extra_links["stop_links"]:
                    raise ValidationError("Die Ausgabenzuordnung verweist auf einen unbekannten Stopp")
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.confirm_document(
                    trip_id=trip_id,
                    document_id=document_id,
                    patch=normalized_patch,
                    actor=actor,
                    default_currency=self.default_currency,
                )
            )

    async def async_panel_payload(self, trip_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.panel_payload, trip_id)

    async def async_assistant_context(self, trip_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.assistant_context, trip_id)

    async def async_get_document(self, trip_id: str, document_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(self.store.get_document, trip_id, document_id)

    async def async_update_document(self, *, trip_id: str, document_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        if "links" in patch:
            patch = {**patch, "links": await self._validate_links(trip_id, patch.get("links"))}
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.update_document(trip_id=trip_id, document_id=document_id, patch=patch, actor=actor)
            )

    async def async_discard_document(self, *, trip_id: str, document_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.discard_document(trip_id=trip_id, document_id=document_id)
            )

    async def async_delete_document(self, *, trip_id: str, document_id: str, delete_linked_records: bool) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.delete_document(
                    trip_id=trip_id,
                    document_id=document_id,
                    delete_linked_records=delete_linked_records,
                )
            )

    async def _validate_record_target(self, trip_id: str, value: dict[str, Any]) -> None:
        day_id = str(value.get("day_id") or "").strip()
        stop_id = str(value.get("stop_id") or "").strip()
        if not day_id and stop_id:
            raise ValidationError("Ein Stopp kann nur zusammen mit seinem Reisetag zugeordnet werden")
        if day_id:
            links = await self._validate_links(
                trip_id,
                {"day_ids": [day_id], "stop_links": ([{"day_id": day_id, "stop_id": stop_id}] if stop_id else [])},
            )
            if day_id not in links["day_ids"]:
                raise ValidationError("Unbekannter Reisetag für diesen Eintrag")
            if stop_id and not links["stop_links"]:
                raise ValidationError("Unbekannter Stopp für diesen Eintrag")

    async def async_create_expense(self, *, trip_id: str, value: dict[str, Any], actor: str) -> dict[str, Any]:
        await self._validate_record_target(trip_id, value)
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.create_expense(
                    trip_id=trip_id,
                    value=value,
                    actor=actor,
                    default_currency=self.default_currency,
                )
            )

    async def async_update_expense(self, *, trip_id: str, expense_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        await self._validate_record_target(trip_id, patch)
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.update_expense(
                    trip_id=trip_id,
                    expense_id=expense_id,
                    patch=patch,
                    actor=actor,
                    default_currency=self.default_currency,
                )
            )

    async def async_delete_expense(self, *, trip_id: str, expense_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.delete_expense(trip_id=trip_id, expense_id=expense_id)
            )

    async def async_create_todo(self, *, trip_id: str, value: dict[str, Any], actor: str) -> dict[str, Any]:
        await self._validate_record_target(trip_id, value)
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.create_todo(trip_id=trip_id, value=value, actor=actor)
            )

    async def async_update_todo(self, *, trip_id: str, todo_id: str, patch: dict[str, Any], actor: str) -> dict[str, Any]:
        await self._validate_record_target(trip_id, patch)
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.update_todo(trip_id=trip_id, todo_id=todo_id, patch=patch, actor=actor)
            )

    async def async_delete_todo(self, *, trip_id: str, todo_id: str) -> dict[str, Any]:
        async with self._lock:
            return await self.hass.async_add_executor_job(
                lambda: self.store.delete_todo(trip_id=trip_id, todo_id=todo_id)
            )
