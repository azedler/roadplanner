"""Fill missing stop coordinates from the Park4Night link a stop carries.

A stop that says "Ort fehlt" while linking to a Park4Night place page holds
everything needed to place it on the map - the page publishes its own GPS.
Reading it is deterministic (park4night_lookup parses the page directly; the
AI reader is only its fallback), so this runs unattended in the background.

What it never does: apply anything by itself. Every filled coordinate lands
as a REVIEW ChangeSet under "Übergaben", exactly like the enrichment
preview, so a wrong place can be rejected before it touches the roadbook.
Each stop is attempted once per run and then remembered, so a page without
usable GPS can never turn into a retry loop.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from datetime import timedelta

from .canonical_day import canonical_roadbook_stops, location_status
from .destination_intelligence import _PARK4NIGHT_ID_RE
from .experience_store import new_id, utc_now_iso
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

MAX_STOPS_PER_RUN = 5
_INITIAL_DELAY_SECONDS = 90
_INTERVAL_MINUTES = 60


def _stop_material(stop: dict[str, Any]) -> str:
    """Text that references THIS stop's place - not its history.

    The whole details blob was wrong to scan: ``place_profile`` keeps the
    source hints of an earlier enrichment run, so a stop that has long since
    become a different place still carries the Park4Night ids it once had
    (live report: three Park4Night buttons on a stop whose notes hold only a
    Google-Maps link). Filling coordinates from such a page would move the
    stop to a place the user never named.
    """
    details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
    references = [
        details.get("source"),
        details.get("source_url"),
        details.get("url"),
        details.get("links"),
    ]
    return " ".join(
        str(part) if isinstance(part, str) else json.dumps(
            part, ensure_ascii=False, default=str
        )
        for part in (stop.get("name"), stop.get("notes"), *references)
        if part
    )


def find_autofill_candidates(days: Any) -> list[dict[str, Any]]:
    """Stops without a map point that carry a Park4Night place reference."""
    candidates: list[dict[str, Any]] = []
    for day in days or []:
        if not isinstance(day, dict):
            continue
        for stop in canonical_roadbook_stops(day):
            if not isinstance(stop, dict):
                continue
            if location_status(stop) == "resolved":
                continue
            match = _PARK4NIGHT_ID_RE.search(_stop_material(stop))
            if match is None:
                continue
            candidates.append(
                {
                    "day_id": str(day.get("id") or ""),
                    "stop_id": str(stop.get("id") or ""),
                    "stop_name": str(stop.get("name") or ""),
                    "place_id": match.group("id"),
                    "url": f"https://park4night.com/lieu/{match.group('id')}/",
                }
            )
    return [item for item in candidates if item["stop_id"] and item["day_id"]]


def build_autofill_operations(
    resolved: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Turn resolved Park4Night facts into reviewable stop operations."""
    operations: list[dict[str, Any]] = []
    for item in resolved:
        location = {
            "latitude": float(item["latitude"]),
            "longitude": float(item["longitude"]),
            "label": str(item.get("stop_name") or "")[:1_000],
        }
        changes: dict[str, Any] = {
            "location": location,
            "details": {
                "geocoding": {
                    "provider": "park4night",
                    "status": "manual_confirmed",
                    "query": item["url"],
                    "mode": "provider_page",
                    "requires_confirmation": False,
                    "provider_verified": False,
                    "confirmed_by": "park4night_page",
                    "candidates": [],
                }
            },
        }
        material = f"{item['stop_id']}:{item['place_id']}:{location['latitude']}:{location['longitude']}"
        operations.append(
            {
                "operation_id": "p4n-autofill-"
                + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16],
                "action": "update",
                "entity_type": "stop",
                "entity_id": item["stop_id"],
                "day_id": item["day_id"],
                "changes": changes,
                "reason": (
                    f"Kartenpunkt aus der Park4Night-Seite #{item['place_id']} "
                    "gelesen, weil dem Stopp die GPS-Angabe fehlte."
                ),
            }
        )
    return operations


class Park4NightAutofillManager:
    """Background job filling missing coordinates from Park4Night links."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: Any,
        lookup: Any,
    ) -> None:
        self.hass = hass
        self.manager = manager
        self.lookup = lookup
        self._unsub: Any = None
        self._attempted: set[str] = set()
        self._last_was_duplicate = False
        self._status: dict[str, Any] = {"state": "idle", "filled": 0}

    @property
    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def async_initialize(self) -> None:
        self._unsub = async_track_time_interval(
            self.hass, self._periodic, timedelta(minutes=_INTERVAL_MINUTES)
        )
        async_call_later(self.hass, _INITIAL_DELAY_SECONDS, self._initial)

    async def async_shutdown(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _initial(self, _now: Any) -> None:
        self.hass.async_create_task(self._async_run_guarded())

    @callback
    def _periodic(self, _now: Any) -> None:
        self.hass.async_create_task(self._async_run_guarded())

    async def _async_run_guarded(self) -> None:
        try:
            await self.async_run()
        except (RoadplannerError, ValidationError) as err:
            self._status = {"state": "error", "error": str(err)[:300]}
            _LOGGER.debug("Park4Night autofill skipped: %s", err)
        except Exception:  # noqa: BLE001 - a background job must never bubble
            self._status = {"state": "error", "error": "Unerwarteter Fehler"}
            _LOGGER.exception("Park4Night autofill crashed")

    async def async_run(self, *, force: bool = False) -> dict[str, Any]:
        """Resolve up to ``MAX_STOPS_PER_RUN`` stops into one review handoff."""
        if self.lookup is None:
            return {"state": "unavailable", "filled": 0}
        payload = await self.manager.async_get_assistant_payload()
        if not payload.get("selected_is_active"):
            return {"state": "inactive_trip", "filled": 0}
        days = (payload.get("days") or {}).get("days") or []
        candidates = [
            item
            for item in find_autofill_candidates(days)
            if force or item["stop_id"] not in self._attempted
        ]
        if not candidates:
            self._status = {"state": "idle", "filled": 0, "checked_at": utc_now_iso()}
            return self._status
        resolved: list[dict[str, Any]] = []
        for item in candidates[:MAX_STOPS_PER_RUN]:
            self._attempted.add(item["stop_id"])
            try:
                facts = await self.lookup.async_lookup(
                    item["url"], hint_name=item["stop_name"]
                )
            except (RoadplannerError, ValidationError) as err:
                _LOGGER.debug(
                    "Park4Night page for stop %s unreadable: %s", item["stop_id"], err
                )
                continue
            if not isinstance(facts, dict):
                continue
            try:
                latitude = float(facts["latitude"])
                longitude = float(facts["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                continue
            resolved.append({**item, "latitude": latitude, "longitude": longitude})
        if not resolved:
            self._status = {
                "state": "idle",
                "filled": 0,
                "candidates": len(candidates),
                "checked_at": utc_now_iso(),
            }
            return self._status
        handoff = await self._async_submit(payload, resolved)
        self._status = {
            # "duplicate" means the identical proposal is already waiting
            # under Übergaben - nothing new was created.
            "state": "duplicate" if self._last_was_duplicate else "ready",
            "filled": 0 if self._last_was_duplicate else len(resolved),
            "candidates": len(candidates),
            "handoff_id": handoff,
            "checked_at": utc_now_iso(),
        }
        return self._status

    async def _async_submit(
        self, payload: dict[str, Any], resolved: list[dict[str, Any]]
    ) -> str:
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        trip = summary.get("trip") if isinstance(summary.get("trip"), dict) else {}
        revision = summary.get("revision")
        trip_id = str(trip.get("id") or payload.get("selected_trip_id") or "")
        if not trip_id or isinstance(revision, bool) or not isinstance(revision, int):
            raise ValidationError(
                "Aktuelle Reise-ID oder Revision konnte nicht gelesen werden"
            )
        operations = build_autofill_operations(resolved)
        changeset_id = new_id("changeset")
        names = ", ".join(item["stop_name"] or "Stopp" for item in resolved[:5])
        title = "Kartenpunkte aus Park4Night-Links"
        changeset = {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": changeset_id,
            "trip_id": trip_id,
            "base_revision": revision,
            "created_at": utc_now_iso(),
            "title": title,
            "summary": (
                f"{len(operations)} Stopp(s) ohne GPS erhalten den Kartenpunkt "
                f"ihrer verlinkten Park4Night-Seite: {names}."
            ),
            "apply_mode": "review",
            "operations": operations,
            "open_questions": [],
            "assumptions": [],
            "research_notes": [
                "Die Koordinaten stammen unverändert von der jeweils "
                "verlinkten Park4Night-Seite und wurden nicht geschätzt."
            ],
            "metadata": {
                "created_by": "roadplanner_park4night_autofill",
                "review_only": True,
            },
        }
        # The identity is the CONTENT, not the run: the same coordinates for
        # the same stop must be recognised as the proposal that is already
        # waiting, however often the hourly job asks. Using a per-run id
        # here piled up four identical handoffs for one stop, each of them
        # stale against a trip that had moved on (live report). Operation
        # ids are stable hashes of stop + place + coordinates, so different
        # facts still produce a new proposal.
        identity = hashlib.sha256(
            "|".join(sorted(str(item["operation_id"]) for item in operations)).encode(
                "utf-8"
            )
        ).hexdigest()
        ingest = await self.manager.async_ingest_external_changeset(
            changeset=changeset,
            title=title,
            source="roadplanner_park4night_autofill",
            external_id=f"p4n-autofill-{identity[:40]}",
            metadata={"park4night_autofill": {"review_only": True}},
            source_payload_sha256=identity,
        )
        handoff = ingest.get("handoff") if isinstance(ingest.get("handoff"), dict) else {}
        self._last_was_duplicate = bool(ingest.get("duplicate"))
        if self._last_was_duplicate:
            _LOGGER.debug(
                "Park4Night autofill proposal already waiting for review: %s",
                handoff.get("id"),
            )
        return str(handoff.get("id") or "")
