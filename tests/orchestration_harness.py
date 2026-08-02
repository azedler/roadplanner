"""Boot the REAL orchestration layer (manager + handoff store + enrichment
orchestrator) on the real store, without Home Assistant.

The store simulation (simulation_harness.py) covers the canonical core;
this harness adds the layer where this week's live bugs actually lived:
ingest/apply revision pinning, direct apply with review fallback,
supersede of stale enrichment handoffs - all under deliberately injected
background writes.

Everything is real production code except:
- ``FakeHass``: runs executor jobs synchronously and records bus events.
- ``FakeRouter``: routing not configured (no network).
- ``ScriptedEnrichment``: replaces PlaceEnrichmentService.resolve_selections
  with scripted operations - the resolution itself has its own tests; here
  the ORCHESTRATION of the result is under test. It can run an injected
  callback first, which is exactly the race window between reading the
  trip revision and ingesting the ChangeSet.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

# HA/aiohttp stubs must exist before manager/orchestrator modules load.
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
ha = types.ModuleType("homeassistant")
ha.__path__ = []
sys.modules.setdefault("homeassistant", ha)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", ha_util)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = lambda: datetime.now(timezone.utc)
ha_dt.utcnow = lambda: datetime.now(timezone.utc)
sys.modules.setdefault("homeassistant.util.dt", ha_dt)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)
ha_storage = types.ModuleType("homeassistant.helpers.storage")
ha_storage.Store = object
sys.modules.setdefault("homeassistant.helpers.storage", ha_storage)

from simulation_harness import (  # noqa: E402
    check_invariants,
    load,
    make_store,
    revision,
    trip_days,
)

manager_module = load("manager")
handoff_module = load("handoff")
orchestrator_module = load("place_enrichment_orchestrator")


class _FakeLoop:
    def call_later(self, _delay: float, callback: Callable, *args: Any):
        class _Handle:
            def cancel(self) -> None:  # pragma: no cover - not scheduled
                pass

        return _Handle()


class FakeHass:
    """Just enough Home Assistant for the manager/orchestrator layer."""

    def __init__(self) -> None:
        self.loop = _FakeLoop()
        self.events: list[tuple[str, dict[str, Any]]] = []
        bus = types.SimpleNamespace()
        bus.async_fire = lambda event, data=None: self.events.append(
            (event, data or {})
        )
        self.bus = bus

    async def async_add_executor_job(self, func: Callable, *args: Any) -> Any:
        return func(*args)

    def async_create_task(self, coro):  # pragma: no cover - route refresh off
        coro.close()
        return None


class FakeRouter:
    configured = False


class FakeExperienceStore:
    def __init__(self) -> None:
        self.galleries: list[tuple[str, list]] = []

    def upsert_destination_galleries(self, trip_id: str, galleries: list) -> None:
        self.galleries.append((trip_id, galleries))


class ScriptedEnrichment:
    """Stands in for PlaceEnrichmentService inside the orchestrator."""

    def __init__(self) -> None:
        self.operations: list[dict[str, Any]] = []
        self.galleries: list[dict[str, Any]] = []
        # Fired INSIDE the submit, after the trip revision was read - the
        # exact window a background write needs to hit for a stale
        # base_revision at ingest time.
        self.before_return: Callable[[], None] | None = None

    async def resolve_selections(self, **_kwargs: Any) -> tuple[list, list]:
        if self.before_return is not None:
            self.before_return()
        return [dict(op) for op in self.operations], list(self.galleries)


def make_orchestration(base_dir: Path):
    """Return (store, manager, orchestrator, fakes) on a fresh temp dir."""
    store = make_store(base_dir)
    hass = FakeHass()
    handoff_store = handoff_module.HandoffStore(base_dir / "handoff_store")
    manager = manager_module.RoadplannerManager(
        hass,
        store,
        handoff_store,
        router=FakeRouter(),
    )
    enrichment = ScriptedEnrichment()
    experience = FakeExperienceStore()

    async def get_panel_payload(_trip_id: str) -> dict[str, Any]:
        return {}

    orchestrator = orchestrator_module.PlaceEnrichmentOrchestrator(
        hass,
        experience,
        manager,
        enrichment,
        get_panel_payload=get_panel_payload,
    )
    return store, manager, orchestrator, enrichment


def enrichment_update_operation(
    day_id: str, stop_id: str, latitude: float, longitude: float
) -> dict[str, Any]:
    """The exact operation shape resolve_selections emits for a confirmation."""
    return {
        "operation_id": f"place-enrich-{stop_id}-{str(latitude).replace('.', '-')}",
        "action": "update",
        "entity_type": "stop",
        "entity_id": stop_id,
        "day_id": day_id,
        "changes": {
            "location": {
                "label": "Bestätigter Ort",
                "latitude": latitude,
                "longitude": longitude,
            },
            "details": {
                "geocoding": {
                    "provider": "manual",
                    "status": "manual_confirmed",
                    "provider_verified": False,
                }
            },
        },
        "reason": "Vom Benutzer bestätigt.",
    }


def stop_location(store, stop_id: str) -> dict[str, Any]:
    for day in trip_days(store):
        for stop in day["stops"]:
            if stop["id"] == stop_id:
                return stop.get("location") or {}
    raise AssertionError(f"stop {stop_id} not found")


def pending_enrichment_handoffs(manager) -> list[dict[str, Any]]:
    listing = manager.handoff_store.list_pending(limit=100)
    return [
        item
        for item in listing.get("handoffs", [])
        if str(item.get("source") or "") == "roadplanner_place_enrichment"
    ]


def check_orchestration_invariants(store, manager, submit_result) -> None:
    """A confirmed enrichment must never be silently swallowed.

    Either it was applied directly, or it is VISIBLE as a pending/conflict
    handoff the user can act on. The store invariants must hold either way.
    """
    check_invariants(store)
    if submit_result.get("applied"):
        return
    handoff = submit_result.get("handoff") or {}
    assert handoff.get("id"), "no direct apply and no handoff - result swallowed"
    listing = manager.handoff_store.list_pending(limit=100)
    listed_ids = {str(item.get("id")) for item in listing.get("handoffs", [])}
    assert str(handoff["id"]) in listed_ids, (
        "the fallback handoff is not visible under pending handoffs"
    )


def use_ticking_handoff_clock() -> None:
    """Strictly increasing handoff timestamps for deterministic ordering.

    Production received_at has SECOND resolution: two submits within the
    same second tie on the timestamp and the supersede ordering falls back
    to the random handoff id - deliberately, so two genuinely racing
    submits never archive each other. Simulations run many submits per
    second, so they pin a ticking clock to test the sequential ordering.
    """
    ticks = iter(range(100_000))

    def ticking() -> str:
        value = next(ticks)
        return f"2026-08-02T{10 + value // 3600:02d}:{(value // 60) % 60:02d}:{value % 60:02d}Z"

    handoff_module.utc_now_iso = ticking


__all__ = [
    "FakeHass",
    "check_invariants",
    "check_orchestration_invariants",
    "enrichment_update_operation",
    "make_orchestration",
    "pending_enrichment_handoffs",
    "revision",
    "stop_location",
    "trip_days",
    "use_ticking_handoff_clock",
]
