"""Seeded random operation sequences against the REAL store.

Complements the scripted simulation: thousands of random-but-valid
mutations (add/move/update/remove stops and days, overnight-option
save/activate/reject/delete, stale-revision attempts) hammer the canonical
stack, and after EVERY operation the full invariant set must hold. The
sequence is fully deterministic per seed: a failure prints the seed and
the exact operation log, so any find is replayable as a one-line repro.

Runtime is bounded (~4 seeds x 120 ops) to stay CI-friendly; raise SEEDS/
OPS locally for deep runs.
"""
from __future__ import annotations

from pathlib import Path
import random
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation_harness import (  # noqa: E402
    OVERNIGHT_TYPES,
    check_invariants,
    make_store,
    revision,
    trip_days,
)

SEEDS = (1, 7, 42, 2026)
OPS_PER_SEED = 120

_STOP_TYPES = ["waypoint", "activity", "restaurant", "campsite", "viewpoint", "parking"]


def _random_location(rng):
    if rng.random() < 0.5:
        return None
    return {
        "latitude": round(rng.uniform(55.0, 68.0), 5),
        "longitude": round(rng.uniform(11.0, 24.0), 5),
    }


def _pick_day(rng, store):
    days = trip_days(store)
    return rng.choice(days) if days else None


def _mutate_once(rng, store, log) -> None:
    """One random valid mutation. Expected ValidationErrors are fine."""
    from simulation_harness import ValidationError, roadplanner

    days = trip_days(store)
    choices = ["add_day"]
    if days:
        choices += ["add_stop", "add_stop", "add_stop", "save_option", "stale_revision"]
        if any(day["stops"] for day in days):
            choices += ["move_stop", "update_stop", "remove_stop"]
        if any((day.get("details") or {}).get("overnight_plan", {}).get("options") for day in days):
            choices += ["activate_option", "reject_option", "delete_option"]
    action = rng.choice(choices)
    log.append(action)
    try:
        if action == "add_day":
            store.add_day(
                actor="fuzz", expected_revision=revision(store),
                title=f"Tag {rng.randrange(1000)}",
                day_date=f"2026-08-{rng.randrange(1, 29):02d}",
            )
        elif action == "add_stop":
            day = _pick_day(rng, store)
            count = len(day["stops"])
            store.add_stop(
                day_id=day["id"], name=f"Stopp {rng.randrange(10_000)}",
                actor="fuzz", expected_revision=revision(store),
                stop_type=rng.choice(_STOP_TYPES),
                position=rng.randrange(1, count + 2) if rng.random() < 0.5 else None,
                location=_random_location(rng),
            )
        elif action == "move_stop":
            day = rng.choice([d for d in trip_days(store) if d["stops"]])
            stop = rng.choice(day["stops"])
            store.update_stop(
                day_id=day["id"], stop_id=stop["id"], patch={},
                position=rng.randrange(1, len(day["stops"]) + 1),
                actor="fuzz", expected_revision=revision(store),
            )
        elif action == "update_stop":
            day = rng.choice([d for d in trip_days(store) if d["stops"]])
            stop = rng.choice(day["stops"])
            store.update_stop(
                day_id=day["id"], stop_id=stop["id"],
                patch={"notes": f"Notiz {rng.randrange(1000)}"},
                actor="fuzz", expected_revision=revision(store),
            )
        elif action == "remove_stop":
            day = rng.choice([d for d in trip_days(store) if d["stops"]])
            stop = rng.choice(day["stops"])
            store.remove_stop(
                day_id=day["id"], stop_id=stop["id"], actor="fuzz",
                expected_revision=revision(store),
            )
        elif action == "save_option":
            day = _pick_day(rng, store)
            store.mutate_overnight_plan(
                day_id=day["id"], operation="save_option",
                payload={"option": {
                    "name": f"Option {rng.randrange(10_000)}",
                    "pros": ["Feuerstelle"] if rng.random() < 0.5 else [],
                }},
                actor="fuzz", expected_revision=revision(store),
            )
        elif action in ("activate_option", "reject_option", "delete_option"):
            candidates = [
                (day, option)
                for day in trip_days(store)
                for option in (day.get("details") or {}).get("overnight_plan", {}).get("options", [])
            ]
            day, option = rng.choice(candidates)
            if action == "activate_option":
                store.mutate_overnight_plan(
                    day_id=day["id"], operation="activate_option",
                    payload={"option_id": option["id"]},
                    actor="fuzz", expected_revision=revision(store),
                )
            elif action == "reject_option":
                store.mutate_overnight_plan(
                    day_id=day["id"], operation="set_option_status",
                    payload={"option_id": option["id"], "status": "rejected"},
                    actor="fuzz", expected_revision=revision(store),
                )
            else:
                store.mutate_overnight_plan(
                    day_id=day["id"], operation="delete_option",
                    payload={"option_id": option["id"]},
                    actor="fuzz", expected_revision=revision(store),
                )
        elif action == "stale_revision":
            day = _pick_day(rng, store)
            if revision(store) <= 1:
                return
            try:
                store.add_stop(
                    day_id=day["id"], name="Stale", actor="fuzz",
                    expected_revision=max(1, revision(store) - 1),
                )
            except roadplanner.RevisionConflictError:
                pass
            else:
                raise AssertionError("a stale revision must always be rejected")
    except ValidationError:
        # Expected business rejections (e.g. option cap reached, activating a
        # rejected option) are legal outcomes - the invariants below are not.
        pass


def run_fuzz() -> None:
    for seed in SEEDS:
        rng = random.Random(seed)
        log: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            store = make_store(Path(tmp))
            for step in range(OPS_PER_SEED):
                _mutate_once(rng, store, log)
                try:
                    check_invariants(store)
                except AssertionError as err:
                    raise AssertionError(
                        f"invariant violated at seed={seed} step={step}: {err}\n"
                        f"operation log: {log}"
                    ) from err
    print(f"Trip fuzzing passed ({len(SEEDS)} seeds x {OPS_PER_SEED} ops).")


if __name__ == "__main__":
    run_fuzz()
