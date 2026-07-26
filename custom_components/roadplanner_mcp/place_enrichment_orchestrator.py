"""Orchestrate the place-completion preview/submission flow.

The actual place resolution (geocoding candidates, AI text cleanup) is
delegated to PlaceEnrichmentService; this collaborator owns building the
review-only ChangeSet from confirmed selections and ingesting it through
the normal handoff/review pipeline.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant

from .const import EVENT_ROADPLANNER_UPDATED
from .experience_helpers import _all_days
from .experience_store import ExperienceStore, new_id, utc_now_iso
from .manager import RoadplannerManager
from .place_enrichment import PlaceEnrichmentService
from .roadplanner import ValidationError


class PlaceEnrichmentOrchestrator:
    """Prepare and submit reviewable place-enrichment ChangeSets."""

    def __init__(
        self,
        hass: HomeAssistant,
        store: ExperienceStore,
        manager: RoadplannerManager,
        place_enrichment: PlaceEnrichmentService | None,
        *,
        get_panel_payload: Callable[[str], Awaitable[dict[str, Any]]],
    ) -> None:
        self.hass = hass
        self.store = store
        self.manager = manager
        self.place_enrichment = place_enrichment
        self._get_panel_payload = get_panel_payload

    async def async_prepare_place_enrichment(
        self,
        *,
        user_id: str,
        trip_id: str,
        day_id: str | None = None,
        stop_id: str | None = None,
        limit: int = 20,
        use_ai_cleanup: bool = False,
    ) -> dict[str, Any]:
        """Return a reviewable full-place preview for incomplete stops."""
        if self.place_enrichment is None:
            raise ValidationError(
                "Die Ortsvervollständigung ist nicht aktiviert. Bitte Geocoding in den "
                "Roadplanner-Optionen einschalten."
            )
        payload = await self.manager.async_get_assistant_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "Ortsprofile können nur für die aktive Reise vorbereitet werden"
            )
        preview = await self.place_enrichment.async_prepare(
            user_id=user_id,
            trip_id=trip_id,
            days=_all_days(payload),
            day_id=day_id,
            stop_id=stop_id,
            limit=limit,
            use_ai_cleanup=use_ai_cleanup,
        )
        return {
            "preview": preview,
            "experience": await self._get_panel_payload(trip_id),
        }

    async def async_submit_place_enrichment(
        self,
        *,
        user_id: str,
        actor: str,
        trip_id: str,
        preview_id: str,
        selections: dict[str, str],
        manual_entries: dict[str, dict[str, Any]] | None = None,
        cleanup_confirmations: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Create one concrete, review-only ChangeSet from selected places."""
        if self.place_enrichment is None:
            raise ValidationError(
                "Die Ortsvervollständigung ist nicht aktiviert."
            )
        payload = await self.manager.async_get_assistant_payload(trip_id)
        if not payload.get("selected_is_active"):
            raise ValidationError(
                "Ortsprofile können nur für die aktive Reise übernommen werden"
            )
        operations, galleries = await self.place_enrichment.resolve_selections(
            user_id=user_id,
            trip_id=trip_id,
            preview_id=preview_id,
            selections={str(key): str(value) for key, value in selections.items()},
            manual_entries=manual_entries,
            cleanup_confirmations=cleanup_confirmations,
        )
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        revision = summary.get("revision")
        canonical_trip_id = str(trip.get("id") or payload.get("selected_trip_id") or "")
        if (
            not canonical_trip_id
            or isinstance(revision, bool)
            or not isinstance(revision, int)
        ):
            raise ValidationError(
                "Aktuelle Reise-ID oder Revision konnte nicht gelesen werden"
            )
        changeset_id = new_id("changeset")
        title = "Ortsprofile vervollständigen"
        count = len(operations)
        changeset: dict[str, Any] = {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": changeset_id,
            "trip_id": canonical_trip_id,
            "base_revision": revision,
            "created_at": utc_now_iso(),
            "title": title,
            "summary": (
                f"{count} bestätigte Ortsprofile mit Kartenpunkt, Adresse und "
                "verfügbaren Kontaktdaten ergänzen."
            ),
            "apply_mode": "review",
            "operations": operations,
            "open_questions": [],
            "assumptions": [],
            "research_notes": [
                "Die ausgewählten Ortsprofile wurden durch den Benutzer in der "
                "Roadplanner-Vorschau bestätigt."
            ],
            "metadata": {
                "created_by": "roadplanner_place_enrichment",
                "user_id": user_id,
                "actor": actor,
                "preview_id": preview_id,
                "review_only": True,
            },
        }
        source_digest = hashlib.sha256(
            json.dumps(
                changeset,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        ingest = await self.manager.async_ingest_external_changeset(
            changeset=changeset,
            title=title,
            source="roadplanner_place_enrichment",
            external_id=changeset_id,
            metadata={
                "place_enrichment": {
                    "user_id": user_id,
                    "actor": actor,
                    "preview_id": preview_id,
                    "review_only": True,
                }
            },
            source_payload_sha256=source_digest,
        )
        if galleries:
            await self.hass.async_add_executor_job(
                self.store.upsert_destination_galleries,
                trip_id,
                galleries,
            )
        self.hass.bus.async_fire(
            EVENT_ROADPLANNER_UPDATED,
            {
                "experience_changed": bool(galleries),
                "source": "place_enrichment",
                "trip_id": trip_id,
            },
        )
        return {
            "request_id": preview_id,
            "changeset_id": changeset_id,
            "operation_count": count,
            "handoff": ingest.get("handoff"),
            "preview": ingest.get("preview"),
            "experience": await self._get_panel_payload(trip_id),
        }
