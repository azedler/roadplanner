"""Scripted end-to-end session against the REAL store (no Home Assistant).

Replays the shape of the live trip's daily use: build days with stops,
reorder and move, manage overnight options (save/activate/Plan-B switch),
apply an assistant-style handover ChangeSet with Plan-B/C options - and
after EVERY single step, assert the full invariant set from
simulation_harness. This is the environment where "Stopps sind instabil"
class bugs surface as a named invariant violation instead of a confused
user screenshot days later.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulation_harness import (  # noqa: E402
    ValidationError,
    check_invariants,
    make_store,
    revision,
    trip_days,
)


def _add_day(store, title, day_date):
    result = store.add_day(
        actor="sim", expected_revision=revision(store),
        title=title, day_date=day_date,
    )
    check_invariants(store)
    return result["day"]["id"]


def _add_stop(store, day_id, name, stop_type="waypoint", position=None, location=None):
    result = store.add_stop(
        day_id=day_id, name=name, actor="sim",
        expected_revision=revision(store), stop_type=stop_type,
        position=position, location=location,
    )
    check_invariants(store)
    return result["stop"]["id"]


def run_simulation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(Path(tmp))
        check_invariants(store)

        # --- Day 1: normal build-up -----------------------------------
        day1 = _add_day(store, "Skuleskogen", "2026-07-31")
        start = _add_stop(store, day1, "Start Docksta", "start",
                          location={"latitude": 63.05, "longitude": 18.35})
        hike = _add_stop(store, day1, "Wanderung Nordrunde", "activity")
        camp = _add_stop(store, day1, "See westlich Docksta", "campsite",
                         location={"latitude": 63.07, "longitude": 18.20})
        assert [s["name"] for s in trip_days(store)[-1]["stops"]] == [
            "Start Docksta", "Wanderung Nordrunde", "See westlich Docksta",
        ]

        # Insert a lunch stop at position 2, then move it to 3 and back.
        lunch = _add_stop(store, day1, "Mittag am Aussichtspunkt", "break", position=2)
        store.update_stop(day_id=day1, stop_id=lunch, patch={}, position=3,
                          actor="sim", expected_revision=revision(store))
        check_invariants(store)
        store.update_stop(day_id=day1, stop_id=lunch, patch={}, position=2,
                          actor="sim", expected_revision=revision(store))
        check_invariants(store)

        # --- Overnight options: save two, activate Plan B, back to A ---
        saved = store.mutate_overnight_plan(
            day_id=day1, operation="save_option",
            payload={"option": {"name": "Entré Nord", "pros": ["Feuerstelle"]}},
            actor="sim", expected_revision=revision(store),
        )
        check_invariants(store)
        plan_b_id = saved["option"]["id"]
        store.mutate_overnight_plan(
            day_id=day1, operation="save_option",
            payload={"option": {"name": "Kleine Lichtung", "cons": ["nur 1 Van"]}},
            actor="sim", expected_revision=revision(store),
        )
        check_invariants(store)
        store.mutate_overnight_plan(
            day_id=day1, operation="activate_option",
            payload={"option_id": plan_b_id},
            actor="sim", expected_revision=revision(store),
        )
        check_invariants(store)
        day_after = trip_days(store)[-1]
        overnight = day_after["stops"][-1]
        assert overnight["name"] == "Entré Nord", "Plan B became the overnight stop"
        demoted = next(
            option for option in
            day_after["details"]["overnight_plan"]["options"]
            if option["name"] == "See westlich Docksta"
        )
        # Switch back to the demoted Plan A - the round trip must lose nothing.
        store.mutate_overnight_plan(
            day_id=day1, operation="activate_option",
            payload={"option_id": demoted["id"]},
            actor="sim", expected_revision=revision(store),
        )
        check_invariants(store)
        assert trip_days(store)[-1]["stops"][-1]["name"] == "See westlich Docksta"

        # --- Day 2 + assistant-style handover ChangeSet ----------------
        day2 = _add_day(store, "Liden", "2026-08-01")
        current_revision = revision(store)
        changeset = {
            "kind": "roadplanner_changeset",
            "version": 1,
            "changeset_id": "changeset-sim-1",
            "trip_id": store.load_trip()["trip"]["id"],
            "base_revision": current_revision,
            "created_at": "2026-07-31T08:00:00Z",
            "title": "Übergabe Tag 2",
            "summary": "Plan mit Optionen",
            "apply_mode": "review",
            "operations": [
                {
                    "operation_id": "op-1",
                    "action": "add",
                    "entity_type": "stop",
                    "entity_id": "new-stop-1",
                    "day_id": day2,
                    "changes": {"name": "Seeplatz bei Liden", "type": "campsite"},
                    "reason": "Plan A",
                },
                {
                    "operation_id": "op-2",
                    "action": "update",
                    "entity_type": "day",
                    "entity_id": day2,
                    "changes": {
                        "details": {
                            "overnight_plan": {
                                "schema_version": 1,
                                "strategy": "route_optimal",
                                "options": [
                                    {
                                        "id": "opt-sim-b",
                                        "name": "Hütte am Indalsälven",
                                        "status": "backup",
                                        "place_query": "",
                                        "location": {},
                                        "source": {"type": "assistant", "provider": None, "provider_id": None, "url": "https://park4night.com/de/place/503072"},
                                        "features": {},
                                        "price": {},
                                        "notes": "Plan B laut Übergabe.",
                                        "pros": ["Schutzhütte", "Feuerstelle"],
                                        "cons": [],
                                        "created_at": "2026-07-31T08:00:00Z",
                                        "updated_at": "2026-07-31T08:00:00Z",
                                    }
                                ],
                            }
                        }
                    },
                    "reason": "Plan B als Option",
                },
            ],
            "open_questions": [],
            "assumptions": [],
            "research_notes": [],
            "metadata": {"created_by": "simulation"},
        }
        preview = store.inspect_changeset_for_import(changeset)
        assert preview["status"] == "ready", preview
        store.apply_changeset(
            changeset=changeset, actor="sim", expected_revision=current_revision,
        )
        check_invariants(store)
        day2_state = next(d for d in trip_days(store) if d["id"] == day2)
        assert day2_state["stops"][-1]["name"] == "Seeplatz bei Liden"
        option_names = [
            option["name"]
            for option in day2_state["details"]["overnight_plan"]["options"]
        ]
        assert "Hütte am Indalsälven" in option_names

        # --- Deletions and revision safety -----------------------------
        store.remove_stop(day_id=day1, stop_id=hike, actor="sim",
                          expected_revision=revision(store))
        check_invariants(store)
        try:
            store.remove_stop(day_id=day1, stop_id=start, actor="sim",
                              expected_revision=1)
        except Exception as err:  # noqa: BLE001
            assert "Revision" in type(err).__name__ or "revision" in str(err).casefold(), err
        else:
            raise AssertionError("a stale revision must be rejected")
        check_invariants(store)

        # The whole session left a consistent roadbook.
        assert len(trip_days(store)) >= 2  # day1 + day2
    print("Trip simulation passed.")


if __name__ == "__main__":
    run_simulation()
