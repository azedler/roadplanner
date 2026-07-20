"""Universal file import orchestration for Roadplanner.

Uploads remain private travel-archive documents.  This manager adds a semantic
import preview and can either place coarse intentions in the assistant change
basket or enqueue a genuine Roadplanner ChangeSet in the existing review inbox.
It never applies a change directly.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .assistant_provider import AssistantProvider
from .roadplanner import ValidationError
from .travel_archive_manager import MAX_INLINE_ANALYSIS_BYTES, TravelArchiveManager
from .universal_import import MAX_IMPORT_DRAFTS, ParsedImport, parse_import_file

_IMPORT_VALUE_PROPERTIES: dict[str, Any] = {
    "title": {"type": "string", "maxLength": 500},
    "status": {"type": "string", "maxLength": 100},
    "start_date": {"type": "string", "maxLength": 20},
    "end_date": {"type": "string", "maxLength": 20},
    "date": {"type": "string", "maxLength": 20},
    "start": {"type": "string", "maxLength": 500},
    "end": {"type": "string", "maxLength": 500},
    "distance_km": {"type": "number", "minimum": 0},
    "drive_minutes": {"type": "integer", "minimum": 0},
    "notes": {"type": "string", "maxLength": 8_000},
    "name": {"type": "string", "maxLength": 500},
    "type": {"type": "string", "maxLength": 100},
    "arrival_time": {"type": "string", "maxLength": 20},
    "departure_time": {"type": "string", "maxLength": 20},
    "category": {"type": "string", "maxLength": 200},
    "text": {"type": "string", "maxLength": 2_000},
}

_IMPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "summary",
        "detected_type",
        "preview_items",
        "basket_delta",
        "open_questions",
        "warnings",
    ],
    "properties": {
        "title": {"type": "string", "maxLength": 500},
        "summary": {"type": "string", "maxLength": 8_000},
        "detected_type": {"type": "string", "maxLength": 100},
        "preview_items": {
            "type": "array",
            "maxItems": 100,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "title", "subtitle"],
                "properties": {
                    "kind": {"type": "string", "maxLength": 100},
                    "title": {"type": "string", "maxLength": 500},
                    "subtitle": {"type": "string", "maxLength": 1_000},
                },
            },
        },
        "basket_delta": {
            "type": "object",
            "additionalProperties": False,
            "required": ["add_or_update", "remove_ids", "note"],
            "properties": {
                "add_or_update": {
                    "type": "array",
                    "maxItems": MAX_IMPORT_DRAFTS,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "entity_type", "summary", "reason", "values"],
                        "properties": {
                            "action": {"type": "string", "enum": ["add", "update", "remove", "plan"]},
                            "entity_type": {"type": "string", "enum": ["trip", "day", "stop", "preference"]},
                            "summary": {"type": "string", "maxLength": 500},
                            "target_id": {"type": "string", "maxLength": 200},
                            "day_id": {"type": "string", "maxLength": 200},
                            "day_date": {"type": "string", "maxLength": 20},
                            "position": {"type": "integer", "minimum": 1, "maximum": 500},
                            "place_query": {"type": "string", "maxLength": 500},
                            "reason": {"type": "string", "maxLength": 1_000},
                            "values": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": _IMPORT_VALUE_PROPERTIES,
                            },
                        },
                    },
                },
                "remove_ids": {"type": "array", "maxItems": 0, "items": {"type": "string"}},
                "note": {"type": "string", "maxLength": 1_000},
            },
        },
        "open_questions": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 2_000}},
        "warnings": {"type": "array", "maxItems": 50, "items": {"type": "string", "maxLength": 2_000}},
    },
}

_IMPORT_SYSTEM_PROMPT = """
Du analysierst eine vom Benutzer ausdrücklich hochgeladene Reiseplanung oder
Übergabedatei für Roadplanner. Home Assistant ist die einzige verbindliche
Datenquelle. Vergleiche die Datei mit dem beigefügten aktuellen Roadbook.

Ziel ist eine verständliche Vorschau und ein grober Änderungskorb, noch kein
fertiges ChangeSet. Erzeuge ausschließlich bestätigte Inhalte aus der Datei und
nur solche Inhalte, die im Roadbook fehlen oder abweichen.

Regeln:
- Bestehende präzisere Roadbook-Daten, echte GPS-Punkte und tatsächliche
  Übernachtungen haben Vorrang.
- Vorhandene Tage und Stopps nicht erneut anlegen.
- Bestehende IDs nur verwenden, wenn sie im Roadbook-Index exakt vorhanden sind.
- Keine IDs, Koordinaten, Preise, Buchungszustände oder Orte erfinden.
- Unklare oder ausdrücklich offene Orte als Notiz beziehungsweise offene Frage
  behandeln; keinen konkreten Stopp daraus erfinden.
- Reise, Tag, Stopp und Präferenz sind die einzigen Entity-Typen.
- Buchungen, Fähren und Unterkünfte als Notizen/Details der passenden Planung
  beschreiben; keine Entity-Typen booking, transport oder activity erzeugen.
- Für neue Tage day_date verwenden. Für konkrete Orte place_query verwenden.
- remove_ids bleibt immer leer. Roadbook-Löschungen werden als action=remove mit
  einer exakten target_id aus dem Roadbook ausgedrückt.
- Maximal 50 konkrete, sinnvoll gebündelte Vormerkungen erzeugen. Bei großen
  Dokumenten Tagespakete oder zusammengehörige Inhalte sinnvoll bündeln.
- Der Text einer vollständigen Projektübergabe gilt als Quelle, aber nicht als
  Nachweis, dass der Inhalt bereits gespeichert wurde.
- Keine trip_id, base_revision oder changeset_id ausgeben.
""".strip()


def _clean(value: Any, maximum: int = 8_000) -> str:
    return str(value or "").strip()[:maximum]


def _safe_string_list(value: Any, maximum: int = 50) -> list[str]:
    source = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result: list[str] = []
    for item in source[:maximum]:
        text = _clean(item, 2_000)
        if text and text not in result:
            result.append(text)
    return result


class UniversalImportManager:
    """Analyze private uploaded files and bridge them into existing review flows."""

    def __init__(
        self,
        hass: HomeAssistant,
        travel_archive: TravelArchiveManager,
        roadplanner_manager: Any,
        assistant: Any,
        *,
        provider: AssistantProvider | None,
    ) -> None:
        self.hass = hass
        self.travel_archive = travel_archive
        self.roadplanner_manager = roadplanner_manager
        self.assistant = assistant
        self.provider = provider

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.provider.configured)

    async def _document(self, trip_id: str, document_id: str) -> tuple[Path, dict[str, Any]]:
        return await self.hass.async_add_executor_job(
            self.travel_archive.store.get_document_file,
            trip_id,
            document_id,
        )

    async def _roadbook_context(self, trip_id: str) -> dict[str, Any]:
        payload = await self.roadplanner_manager.async_get_assistant_payload(trip_id)
        if str(payload.get("selected_trip_id") or "") != trip_id:
            raise ValidationError("Die ausgewählte Importreise konnte nicht geladen werden")
        return payload

    @staticmethod
    def _roadbook_index(payload: dict[str, Any]) -> dict[str, Any]:
        trip = payload.get("summary", {}).get("trip", {})
        days: list[dict[str, Any]] = []
        for day in payload.get("days", {}).get("days", []):
            if not isinstance(day, dict):
                continue
            days.append(
                {
                    "id": day.get("id"),
                    "date": day.get("date"),
                    "title": day.get("title"),
                    "start": day.get("start"),
                    "end": day.get("end"),
                    "notes": str(day.get("notes") or "")[:1_500],
                    "stops": [
                        {
                            "id": stop.get("id"),
                            "name": stop.get("name"),
                            "type": stop.get("type"),
                            "location": stop.get("location") or {},
                        }
                        for stop in day.get("stops", [])
                        if isinstance(stop, dict)
                    ],
                }
            )
        return {
            "trip_id": payload.get("selected_trip_id"),
            "revision": payload.get("summary", {}).get("revision"),
            "trip": {
                "title": trip.get("title"),
                "status": trip.get("status"),
                "start_date": trip.get("start_date"),
                "end_date": trip.get("end_date"),
                "notes": str(trip.get("notes") or "")[:3_000],
            },
            "days": days[:180],
        }

    async def _semantic_analysis(
        self,
        *,
        parsed: ParsedImport,
        data: bytes,
        document: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self.provider
        if provider is None or not provider.configured:
            raise ValidationError("Für den Universal Import ist ein konfigurierter Assistent erforderlich")
        roadbook_index = self._roadbook_index(payload)
        prompt = (
            "Analysiere die folgende Datei als Roadplanner-Übergabe.\n\n"
            "CURRENT_ROADBOOK_INDEX:\n"
            + json.dumps(roadbook_index, ensure_ascii=False, separators=(",", ":"))
            + "\n\nSOURCE_FORMAT: "
            + parsed.format
            + "\nSOURCE_FILENAME: "
            + str(document.get("original_filename") or "import")
        )
        if parsed.format == "binary_document":
            if int(document.get("size_bytes") or 0) > MAX_INLINE_ANALYSIS_BYTES:
                raise ValidationError(
                    "Die Datei ist für die direkte Universal-Importanalyse größer als 10 MB. "
                    "Bitte eine kleinere PDF-/Bildversion oder eine Markdown-Übergabe verwenden."
                )
            analyzer = getattr(provider, "async_analyze_binary", None)
            if not callable(analyzer):
                raise ValidationError("Der Assistentenprovider unterstützt keine Dateianalyse")
            result = await analyzer(
                system_instruction=_IMPORT_SYSTEM_PROMPT,
                prompt=prompt,
                data=data,
                mime_type=str(document.get("mime_type") or "application/octet-stream"),
                filename=str(document.get("original_filename") or "import"),
                schema=_IMPORT_SCHEMA,
                max_output_tokens=16_384,
            )
        else:
            text = parsed.text
            if not text:
                text = json.dumps(parsed.as_dict(), ensure_ascii=False)
            messages = [
                {
                    "role": "user",
                    "content": prompt + "\n\nSOURCE_CONTENT:\n" + text,
                }
            ]
            result = await provider.async_generate_json_result(
                system_instruction=_IMPORT_SYSTEM_PROMPT,
                messages=messages,
                schema=_IMPORT_SCHEMA,
                enable_search=False,
                max_output_tokens=16_384,
                temperature=0.05,
            )
        value = result.value if hasattr(result, "value") else result
        if not isinstance(value, dict):
            raise ValidationError("Der Universal Importer hat keine verwertbare Analyse geliefert")
        value = deepcopy(value)
        value["model_version"] = getattr(result, "model_version", None)
        value["usage"] = getattr(result, "usage", {})
        return value

    @staticmethod
    def _normalize_import_result(
        parsed: ParsedImport,
        semantic: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if parsed.direct_changeset is not None:
            operations = parsed.direct_changeset.get("operations")
            return {
                "status": "ready",
                "mode": "changeset",
                "format": parsed.format,
                "title": parsed.title,
                "summary": parsed.summary,
                "preview_items": parsed.preview_items,
                "warnings": parsed.warnings,
                "open_questions": [],
                "direct_changeset": parsed.direct_changeset,
                "basket_delta": None,
                "counts": {"operations": len(operations) if isinstance(operations, list) else 0},
                "metadata": parsed.metadata,
            }
        if parsed.basket_delta is not None and semantic is None:
            drafts = parsed.basket_delta.get("add_or_update") if isinstance(parsed.basket_delta, dict) else []
            return {
                "status": "ready",
                "mode": "basket",
                "format": parsed.format,
                "title": parsed.title,
                "summary": parsed.summary,
                "preview_items": parsed.preview_items,
                "warnings": parsed.warnings,
                "open_questions": [],
                "direct_changeset": None,
                "basket_delta": parsed.basket_delta,
                "counts": {"drafts": len(drafts) if isinstance(drafts, list) else 0},
                "metadata": parsed.metadata,
            }
        semantic = semantic or {}
        delta = semantic.get("basket_delta") if isinstance(semantic.get("basket_delta"), dict) else {
            "add_or_update": [], "remove_ids": [], "note": ""
        }
        delta["remove_ids"] = []
        drafts = delta.get("add_or_update") if isinstance(delta.get("add_or_update"), list) else []
        return {
            "status": "ready",
            "mode": "basket",
            "format": parsed.format,
            "title": _clean(semantic.get("title"), 500) or parsed.title,
            "summary": _clean(semantic.get("summary"), 8_000) or parsed.summary,
            "detected_type": _clean(semantic.get("detected_type"), 100),
            "preview_items": [
                {
                    "kind": _clean(item.get("kind"), 100),
                    "title": _clean(item.get("title"), 500),
                    "subtitle": _clean(item.get("subtitle"), 1_000),
                }
                for item in list(semantic.get("preview_items") or [])[:100]
                if isinstance(item, dict) and _clean(item.get("title"), 500)
            ] or parsed.preview_items,
            "warnings": list(dict.fromkeys(parsed.warnings + _safe_string_list(semantic.get("warnings"), 50))),
            "open_questions": _safe_string_list(semantic.get("open_questions"), 50),
            "direct_changeset": None,
            "basket_delta": delta,
            "counts": {"drafts": len(drafts)},
            "metadata": {**parsed.metadata, "model_version": semantic.get("model_version"), "usage": semantic.get("usage") or {}},
        }

    async def _save_import_analysis(
        self,
        *,
        trip_id: str,
        document_id: str,
        document: dict[str, Any],
        import_result: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        existing = document.get("analysis") if isinstance(document.get("analysis"), dict) else {}
        analysis = deepcopy(existing)
        analysis.update(
            {
                "classification": analysis.get("classification") or "document",
                "document_type": analysis.get("document_type") or "travel_document",
                "title": import_result.get("title") or document.get("title"),
                "provider": analysis.get("provider") or "Roadplanner Universal Import",
                "summary": import_result.get("summary") or "",
                "warnings": import_result.get("warnings") or [],
                "universal_import": import_result,
            }
        )
        return await self.hass.async_add_executor_job(
            lambda: self.travel_archive.store.set_document_analysis(
                trip_id=trip_id,
                document_id=document_id,
                analysis=analysis,
                actor=actor,
            )
        )

    async def async_analyze_document(
        self,
        *,
        trip_id: str,
        document_id: str,
        actor: str,
    ) -> dict[str, Any]:
        path, document = await self._document(trip_id, document_id)
        parsed = await self.hass.async_add_executor_job(parse_import_file, path, document)
        payload = await self._roadbook_context(trip_id)
        data = b""
        semantic: dict[str, Any] | None = None
        if parsed.direct_changeset is None and parsed.basket_delta is None:
            data = await self.hass.async_add_executor_job(path.read_bytes)
            semantic = await self._semantic_analysis(
                parsed=parsed,
                data=data,
                document=document,
                payload=payload,
            )
        import_result = self._normalize_import_result(parsed, semantic)
        saved = await self._save_import_analysis(
            trip_id=trip_id,
            document_id=document_id,
            document=document,
            import_result=import_result,
            actor=actor,
        )
        return {"document": saved, "import": import_result}

    async def _load_import(self, trip_id: str, document_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        document = await self.hass.async_add_executor_job(
            self.travel_archive.store.get_document,
            trip_id,
            document_id,
        )
        analysis = document.get("analysis") if isinstance(document.get("analysis"), dict) else {}
        import_result = analysis.get("universal_import") if isinstance(analysis.get("universal_import"), dict) else None
        if not import_result:
            raise ValidationError("Für dieses Dokument liegt noch keine Universal-Importanalyse vor")
        return document, deepcopy(import_result)

    async def _mark_applied(
        self,
        *,
        trip_id: str,
        document_id: str,
        document: dict[str, Any],
        import_result: dict[str, Any],
        actor: str,
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        import_result = deepcopy(import_result)
        import_result["status"] = "transferred"
        import_result["outcome"] = outcome
        return await self._save_import_analysis(
            trip_id=trip_id,
            document_id=document_id,
            document=document,
            import_result=import_result,
            actor=actor,
        )

    async def async_transfer(
        self,
        *,
        user_id: str,
        trip_id: str,
        document_id: str,
        actor: str,
    ) -> dict[str, Any]:
        document, import_result = await self._load_import(trip_id, document_id)
        mode = str(import_result.get("mode") or "")
        if str(import_result.get("status") or "") == "transferred":
            outcome = import_result.get("outcome") if isinstance(import_result.get("outcome"), dict) else {}
            return {
                "mode": str(outcome.get("mode") or mode or "basket"),
                "already_transferred": True,
                "document": document,
                "outcome": deepcopy(outcome),
            }
        if mode == "changeset":
            changeset = import_result.get("direct_changeset")
            if not isinstance(changeset, dict):
                raise ValidationError("Das erkannte ChangeSet ist unvollständig")
            result = await self.roadplanner_manager.async_ingest_external_changeset(
                changeset=changeset,
                title=_clean(import_result.get("title"), 500) or "Universal Import",
                source="universal_import",
                external_id=f"document:{document_id}",
                metadata={
                    "document_id": document_id,
                    "filename": document.get("original_filename"),
                    "format": import_result.get("format"),
                    "actor": actor,
                },
                source_payload_sha256=str(document.get("sha256") or "") or None,
            )
            await self._mark_applied(
                trip_id=trip_id,
                document_id=document_id,
                document=document,
                import_result=import_result,
                actor=actor,
                outcome={"mode": "review", "handoff_id": result.get("handoff", {}).get("id")},
            )
            return {"mode": "review", **result}

        delta = import_result.get("basket_delta")
        if not isinstance(delta, dict):
            raise ValidationError("Die Importanalyse enthält keinen Änderungsvorschlag")
        result = await self.assistant.async_add_import_drafts(
            user_id=user_id,
            trip_id=trip_id,
            delta=delta,
            title=_clean(import_result.get("title"), 500),
            summary=_clean(import_result.get("summary"), 8_000),
            document_id=document_id,
            open_questions=_safe_string_list(import_result.get("open_questions"), 50),
        )
        await self._mark_applied(
            trip_id=trip_id,
            document_id=document_id,
            document=document,
            import_result=import_result,
            actor=actor,
            outcome={
                "mode": "basket",
                "basket_count": result.get("assistant", {}).get("basket_count"),
                "added_count": result.get("basket_result", {}).get("added_count"),
            },
        )
        return {"mode": "basket", **result}

    async def async_discuss(
        self,
        *,
        user_id: str,
        trip_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        document, import_result = await self._load_import(trip_id, document_id)
        return await self.assistant.async_add_import_context(
            user_id=user_id,
            trip_id=trip_id,
            title=_clean(import_result.get("title"), 500),
            summary=_clean(import_result.get("summary"), 8_000),
            document_id=document_id,
            preview_items=list(import_result.get("preview_items") or [])[:30],
            open_questions=_safe_string_list(import_result.get("open_questions"), 30),
        )

    async def async_discard(
        self,
        *,
        trip_id: str,
        document_id: str,
        actor: str,
    ) -> dict[str, Any]:
        document, import_result = await self._load_import(trip_id, document_id)
        import_result["status"] = "discarded"
        saved = await self._save_import_analysis(
            trip_id=trip_id,
            document_id=document_id,
            document=document,
            import_result=import_result,
            actor=actor,
        )
        return {"document": saved, "import": import_result}
