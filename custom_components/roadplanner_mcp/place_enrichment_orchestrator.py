"""Orchestrate the place-completion preview/submission flow.

The actual place resolution (geocoding candidates, AI text cleanup) is
delegated to PlaceEnrichmentService; this collaborator owns building the
review-only ChangeSet from confirmed selections and ingesting it through
the normal handoff/review pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Awaitable, Callable

from homeassistant.core import HomeAssistant

from .const import EVENT_ROADPLANNER_UPDATED
from .experience_helpers import _all_days
from .experience_store import ExperienceStore, new_id, utc_now_iso
from .manager import RoadplannerManager
from .place_enrichment import PlaceEnrichmentService
from .roadplanner import ValidationError


_ENRICHMENT_SOURCE = "roadplanner_place_enrichment"

_LOGGER = logging.getLogger(__name__)


def _stop_ids_of_operations(operations: Any) -> set[str]:
    return {
        str(operation.get("entity_id"))
        for operation in (operations if isinstance(operations, list) else [])
        if isinstance(operation, dict)
        and operation.get("entity_type") == "stop"
        and operation.get("entity_id")
    }


def find_superseded_enrichment_handoffs(
    pending_handoffs: list[dict[str, Any]],
    envelopes_by_id: dict[str, dict[str, Any]],
    new_stop_ids: set[str],
    *,
    exclude_id: str = "",
    newer_than: tuple[str, str] | None = None,
) -> list[str]:
    """Return pending place-enrichment handoff ids replaced by a new submit.

    Confirming a stop's place profile again (easy to do: the "Ortsprofil
    vervollständigen" badge stays visible while the first handoff sits
    unapplied under Übergaben) used to just add a second pending handoff
    for the same stop. The older one then went stale as the trip revision
    advanced, showing a conflict that was pure noise - the newer handoff
    already contains the fresher confirmation of the same place.
    """
    if not new_stop_ids:
        return []
    superseded: list[str] = []
    for item in pending_handoffs:
        if not isinstance(item, dict):
            continue
        if str(item.get("source") or "") != _ENRICHMENT_SOURCE:
            continue
        handoff_id = str(item.get("id") or "")
        if exclude_id and handoff_id == exclude_id:
            # Never archive the replacement itself.
            continue
        if newer_than is not None and newer_than[0]:
            # Only archive handoffs strictly OLDER than the replacement.
            # Without this ordering, two racing submits that both finished
            # ingesting would each see the other and archive it - leaving
            # none. ISO-8601 timestamps compare lexicographically; the id
            # breaks exact-timestamp ties deterministically.
            candidate_marker = (str(item.get("received_at") or ""), handoff_id)
            if candidate_marker >= newer_than:
                continue
        envelope = envelopes_by_id.get(handoff_id)
        if not isinstance(envelope, dict):
            continue
        changeset = envelope.get("changeset")
        operations = changeset.get("operations") if isinstance(changeset, dict) else []
        if _stop_ids_of_operations(operations) & new_stop_ids:
            superseded.append(handoff_id)
    return superseded


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

    async def _async_supersede_stale_enrichment_handoffs(
        self,
        new_stop_ids: set[str],
        *,
        new_handoff_id: str = "",
        new_handoff_received_at: str = "",
    ) -> None:
        """Archive older pending enrichment handoffs for the same stops.

        Runs strictly AFTER the replacement was ingested successfully - the
        old handoff must never be archived for a replacement that does not
        exist (an ingest can still fail on a racing trip switch or stop
        edit; archiving first silently destroyed the user's only pending
        confirmation in that case). Best-effort: a failure here must never
        block the submit - worst case the old duplicate stays visible,
        which is exactly the pre-supersede behavior.
        """
        if not new_stop_ids:
            return
        try:
            pending = await self.manager.async_list_handoffs(limit=100)
            candidates = [
                item
                for item in pending.get("handoffs", [])
                if isinstance(item, dict)
                and str(item.get("source") or "") == _ENRICHMENT_SOURCE
            ]
            envelopes_by_id: dict[str, dict[str, Any]] = {}
            for item in candidates:
                handoff_id = str(item.get("id") or "")
                try:
                    envelopes_by_id[handoff_id] = await self.manager.async_get_handoff(
                        handoff_id
                    )
                except ValidationError:
                    continue
            for handoff_id in find_superseded_enrichment_handoffs(
                candidates,
                envelopes_by_id,
                new_stop_ids,
                exclude_id=new_handoff_id,
                newer_than=(new_handoff_received_at, new_handoff_id),
            ):
                await self.manager.async_archive_handoff(
                    handoff_id=handoff_id,
                    resolution="superseded",
                    note=(
                        "Durch eine neuere Ortsprofil-Übergabe für denselben "
                        "Stopp ersetzt."
                    ),
                )
        except Exception:  # noqa: BLE001 - cleanup must never block the submit
            return

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
        new_handoff = (
            ingest.get("handoff") if isinstance(ingest.get("handoff"), dict) else {}
        )
        clean_ingest = not ingest.get("duplicate") and str(
            new_handoff.get("status") or ""
        ) not in ("conflict", "failed")
        if clean_ingest:
            # Only now - the replacement provably exists - archive older
            # pending enrichment handoffs for the same stops.
            await self._async_supersede_stale_enrichment_handoffs(
                _stop_ids_of_operations(operations),
                new_handoff_id=str(new_handoff.get("id") or ""),
                new_handoff_received_at=str(new_handoff.get("received_at") or ""),
            )
        applied_handoff: dict[str, Any] | None = None
        apply_error: str | None = None
        if clean_ingest:
            # Every operation in this ChangeSet was individually confirmed by
            # the user in the enrichment preview - a second confirmation under
            # Übergaben is redundant, and worse: background writes (automatic
            # route refresh, photo assignment, ...) bump the trip revision
            # within seconds, turning the parked handoff into a stale-revision
            # conflict that silently never changes the coordinates. Apply
            # directly; only a genuine race falls back to the review handoff.
            try:
                apply_outcome = await self.manager.async_apply_handoff(
                    handoff_id=str(new_handoff.get("id") or ""),
                    actor=actor,
                    expected_revision=revision,
                )
                applied_handoff = (
                    apply_outcome.get("handoff")
                    if isinstance(apply_outcome.get("handoff"), dict)
                    else None
                )
            except Exception as err:  # noqa: BLE001 - fall back to review
                apply_error = str(err)[:500]
                _LOGGER.warning(
                    "Direct apply of place enrichment failed, handoff stays "
                    "under review: %s",
                    apply_error,
                )
        if galleries and clean_ingest:
            # Galleries stay a submit-time side effect only for a cleanly
            # ingested handoff - a duplicate or conflict ingest must not
            # publish media for a profile that may never be applied.
            await self.hass.async_add_executor_job(
                self.store.upsert_destination_galleries,
                trip_id,
                galleries,
            )
        else:
            galleries = []
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
            "applied": applied_handoff is not None,
            "apply_error": apply_error,
            "handoff": applied_handoff or ingest.get("handoff"),
            "preview": ingest.get("preview"),
            "experience": await self._get_panel_payload(trip_id),
        }
