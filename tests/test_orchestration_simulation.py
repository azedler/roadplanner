"""Orchestration-level simulation: submit -> ingest -> direct apply under races.

Replays exactly the failure patterns found live this week, against the
REAL manager + handoff store + enrichment orchestrator:

- clean submit applies directly (no second confirmation round),
- a background write between reading the revision and ingesting produces
  a VISIBLE conflict handoff instead of silently losing the confirmation,
- a background write between ingest and apply falls back to a visible
  review handoff with the reason reported,
- a re-submit for the same stop supersedes the stale pending handoff,
- a seeded random interleaving of user edits, background writes and
  submits never violates the store invariants and never swallows a
  confirmed enrichment.
"""
from __future__ import annotations

import asyncio
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestration_harness import (  # noqa: E402
    check_orchestration_invariants,
    use_ticking_handoff_clock,
    enrichment_update_operation,
    make_orchestration,
    pending_enrichment_handoffs,
    revision,
    stop_location,
)


def build_trip(store) -> tuple[str, str, str]:
    day = store.add_day(
        day_date="2026-08-10",
        title="Simulationstag",
        actor="sim",
        expected_revision=revision(store),
    )
    day_id = day["day"]["id"]
    stop = store.add_stop(
        day_id=day_id,
        name="Übernachtungsplatz",
        stop_type="overnight",
        location={"label": "Auto-Treffer", "latitude": 60.0, "longitude": 17.0},
        actor="sim",
        expected_revision=revision(store),
    )
    trip_id = store.load_trip()["trip"]["id"]
    return trip_id, day_id, stop["stop"]["id"]


async def submit(orchestrator, trip_id: str):
    return await orchestrator.async_submit_place_enrichment(
        user_id="user-1",
        actor="Aron",
        trip_id=trip_id,
        preview_id="preview-1",
        selections={"stop": "__manual__"},
    )


def background_write(store, day_id: str) -> None:
    """Any unrelated write that bumps the trip revision (route refresh,
    photo assignment, expense, ...)."""
    store.update_day(
        day_id=day_id,
        patch={"notes": f"hintergrund-{revision(store)}"},
        actor="background",
        expected_revision=revision(store),
    )


def scenario_clean_submit_applies_directly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store, manager, orchestrator, enrichment = make_orchestration(Path(tmp))
        trip_id, day_id, stop_id = build_trip(store)
        enrichment.operations = [
            enrichment_update_operation(day_id, stop_id, 63.1353, 18.5161)
        ]
        result = asyncio.run(submit(orchestrator, trip_id))
        assert result["applied"] is True, result
        assert result["apply_error"] is None
        assert stop_location(store, stop_id)["latitude"] == 63.1353
        assert pending_enrichment_handoffs(manager) == [], (
            "a directly applied enrichment leaves nothing pending"
        )
        check_orchestration_invariants(store, manager, result)


def scenario_race_before_ingest_yields_visible_conflict() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store, manager, orchestrator, enrichment = make_orchestration(Path(tmp))
        trip_id, day_id, stop_id = build_trip(store)
        enrichment.operations = [
            enrichment_update_operation(day_id, stop_id, 63.1353, 18.5161)
        ]
        enrichment.before_return = lambda: background_write(store, day_id)
        result = asyncio.run(submit(orchestrator, trip_id))
        assert result["applied"] is False
        handoff = result["handoff"]
        assert handoff["status"] == "conflict", handoff
        assert stop_location(store, stop_id)["latitude"] == 60.0, (
            "no partial apply on a stale base revision"
        )
        check_orchestration_invariants(store, manager, result)


def scenario_race_before_apply_falls_back_to_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store, manager, orchestrator, enrichment = make_orchestration(Path(tmp))
        trip_id, day_id, stop_id = build_trip(store)
        enrichment.operations = [
            enrichment_update_operation(day_id, stop_id, 63.1353, 18.5161)
        ]
        original_apply = manager.async_apply_handoff

        async def background_write_then_apply(**kwargs):
            # The race window: an unrelated write lands after the ChangeSet
            # was ingested but before the direct apply acquires the lock.
            background_write(store, day_id)
            return await original_apply(**kwargs)

        manager.async_apply_handoff = background_write_then_apply
        result = asyncio.run(submit(orchestrator, trip_id))
        assert result["applied"] is False
        assert result["apply_error"], "the failed direct apply must carry a reason"
        assert stop_location(store, stop_id)["latitude"] == 60.0
        assert len(pending_enrichment_handoffs(manager)) == 1, (
            "the confirmation stays visible as a review handoff"
        )
        check_orchestration_invariants(store, manager, result)


def scenario_resubmit_supersedes_stale_pending_handoff() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store, manager, orchestrator, enrichment = make_orchestration(Path(tmp))
        trip_id, day_id, stop_id = build_trip(store)
        enrichment.operations = [
            enrichment_update_operation(day_id, stop_id, 63.1353, 18.5161)
        ]
        # First submit is knocked out between ingest and apply -> parked.
        original_apply = manager.async_apply_handoff

        async def background_write_then_apply(**kwargs):
            background_write(store, day_id)
            return await original_apply(**kwargs)

        manager.async_apply_handoff = background_write_then_apply
        first = asyncio.run(submit(orchestrator, trip_id))
        assert first["applied"] is False
        manager.async_apply_handoff = original_apply

        # Second submit for the same stop succeeds and supersedes the stale one.
        enrichment.operations = [
            enrichment_update_operation(day_id, stop_id, 63.2, 18.6)
        ]
        second = asyncio.run(submit(orchestrator, trip_id))
        assert second["applied"] is True
        assert stop_location(store, stop_id)["latitude"] == 63.2
        assert pending_enrichment_handoffs(manager) == [], (
            "the stale first handoff must be archived as superseded"
        )
        check_orchestration_invariants(store, manager, second)


def scenario_seeded_interleaving_fuzz(seed: int, rounds: int = 40) -> None:
    rng = random.Random(seed)
    with tempfile.TemporaryDirectory() as tmp:
        store, manager, orchestrator, enrichment = make_orchestration(Path(tmp))
        trip_id, day_id, stop_id = build_trip(store)
        applied_count = 0
        parked_count = 0
        for round_index in range(rounds):
            action = rng.random()
            if action < 0.35:
                background_write(store, day_id)
                continue
            if action < 0.5:
                store.add_stop(
                    day_id=day_id,
                    name=f"Zwischenstopp {round_index}",
                    actor="sim",
                    expected_revision=revision(store),
                )
                continue
            latitude = round(60.0 + rng.random() * 4, 5)
            enrichment.operations = [
                enrichment_update_operation(day_id, stop_id, latitude, 18.0)
            ]
            enrichment.before_return = (
                (lambda: background_write(store, day_id))
                if rng.random() < 0.4
                else None
            )
            result = asyncio.run(submit(orchestrator, trip_id))
            check_orchestration_invariants(store, manager, result)
            if result["applied"]:
                applied_count += 1
                assert stop_location(store, stop_id)["latitude"] == latitude
            else:
                parked_count += 1
        assert applied_count, f"seed {seed}: no submit ever applied"


if __name__ == "__main__":
    use_ticking_handoff_clock()
    scenario_clean_submit_applies_directly()
    scenario_race_before_ingest_yields_visible_conflict()
    scenario_race_before_apply_falls_back_to_review()
    scenario_resubmit_supersedes_stale_pending_handoff()
    for seed in (7, 23, 42):
        scenario_seeded_interleaving_fuzz(seed)
    print("Orchestration simulation tests passed.")
