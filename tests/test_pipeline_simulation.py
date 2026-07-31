"""Fake-provider pipeline simulation: model output -> sanitizer -> real store.

The store simulation covers the local core; this file adds the missing
layer ABOVE it. A scripted "fake Gemini" emits compile responses -
including exactly the malformed shapes the live trip produced (stray
category, kebab-case keys, string positions, coordinate objects in
location, echoed changes on remove, Plan-A/B/C handover blocks) - and the
REAL production pipeline processes them: batch preparation, per-operation
sanitizing (position bookkeeping, batch refs, full-day fallbacks), then a
review ChangeSet applied to the REAL store, with the full invariant set
checked afterwards. No external API is ever touched.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))

# HA/aiohttp stubs must exist before the assistant modules load.
homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", ha_util)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = lambda: None
ha_dt.utcnow = lambda: None
sys.modules.setdefault("homeassistant.util.dt", ha_dt)
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
sys.modules.setdefault("aiohttp", aiohttp_stub)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)

from simulation_harness import (  # noqa: E402
    ValidationError,
    check_invariants,
    load,
    make_store,
    revision,
    trip_days,
)

sanitizer = load("assistant_operation_sanitizer")
compile_module = load("assistant_compile")


def _context(store) -> dict:
    days = trip_days(store)
    return {
        "trip": {"id": store.load_trip()["trip"]["id"]},
        "selected_trip_id": store.load_trip()["trip"]["id"],
        "id_catalog": {
            "day_ids": [day["id"] for day in days],
            "stop_ids_by_day": {
                day["id"]: [stop["id"] for stop in day["stops"]] for day in days
            },
            "preference_ids": [],
        },
        # Deliberately EMPTY detail window: the pipeline must survive on the
        # full stored day list alone (the live position/dedup bugs came from
        # trusting the window).
        "days": [],
    }


def run_pipeline(store, raw_operations: list[dict]) -> dict:
    """The production path from a fake model response to an applied ChangeSet."""
    context = _context(store)
    full_days = trip_days(store)
    prepared, new_day_refs = compile_module._prepare_compiled_operation_batch(
        raw_operations
    )
    position_state = {
        day_id: [
            {"id": stop_id, "type": next(
                (str(s.get("type") or "") for d in full_days if d["id"] == day_id
                 for s in d["stops"] if s["id"] == stop_id), "waypoint")}
            for stop_id in context["id_catalog"]["stop_ids_by_day"][day_id]
        ]
        for day_id in context["id_catalog"]["day_ids"]
    }
    for day_ref in new_day_refs:
        position_state.setdefault(day_ref, [])
    batch_refs: dict = {}
    operations = [
        sanitizer._sanitize_operation(
            raw, index=index, context=context, new_day_refs=new_day_refs,
            position_state=position_state, batch_refs=batch_refs,
            full_days=full_days,
        )
        for index, raw in enumerate(prepared)
    ]
    # Production resolves place_query via the geocoding plugin into a
    # verified location and drops the field before the ChangeSet is built.
    # The fake pipeline models a geocoder that found nothing: the field is
    # dropped, the stop simply stays without GPS.
    for operation in operations:
        operation.pop("place_query", None)
    base_revision = revision(store)
    changeset = {
        "kind": "roadplanner_changeset",
        "version": 1,
        "changeset_id": f"changeset-pipe-{base_revision}",
        "trip_id": context["trip"]["id"],
        "base_revision": base_revision,
        "created_at": "2026-07-31T09:00:00Z",
        "title": "Pipeline-Simulation",
        "summary": "Fake-Provider-Antwort",
        "apply_mode": "review",
        "operations": operations,
        "open_questions": [],
        "assumptions": [],
        "research_notes": [],
        "metadata": {"created_by": "pipeline_simulation"},
    }
    preview = store.inspect_changeset_for_import(changeset)
    assert preview["status"] == "ready", preview
    result = store.apply_changeset(
        changeset=changeset, actor="pipeline", expected_revision=base_revision,
    )
    check_invariants(store)
    return result


def run_pipeline_simulation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(Path(tmp))
        day_result = store.add_day(
            actor="sim", expected_revision=revision(store),
            title="Skuleskogen", day_date="2026-07-31",
        )
        day1 = day_result["day"]["id"]
        store.add_stop(day_id=day1, name="Start Docksta", actor="sim",
                       stop_type="start", expected_revision=revision(store))

        # --- Fake response 1: every live malformation at once -----------
        run_pipeline(store, [
            {
                # kebab-case keys + stray category + string position + a
                # coordinate object in changes.location (all seen live).
                "operation-id": "op-1",
                "action": "add",
                "entity-type": "stop",
                "day-id": day1,
                "position": "2",
                "changes": {
                    "name": "Wanderung Nordrunde (p4n 277266)",
                    "type": "activity",
                    "category": "Wanderung",
                    "location": {"lat": 63.08, "lng": 18.21},
                },
                "reason": "Tagesprogramm",
            },
            {
                "operation_id": "op-2",
                "action": "add",
                "entity_type": "stop",
                "entity_id": "new-stop-camp",
                "day_id": day1,
                "place_query": "See westlich Docksta",
                "changes": {"name": "See westlich Docksta", "type": "campsite"},
                "reason": "Plan A",
            },
            {
                # Plan B/C handover block for the same day.
                "operation_id": "op-3",
                "action": "update",
                "entity_type": "day",
                "entity_id": day1,
                "changes": {
                    "details": {
                        "overnight_plan": {
                            "options": [
                                {
                                    "name": "Entré Nord Skuleskogen",
                                    "url": "https://park4night.com/de/place/51373",
                                    "pros": ["Feuerstelle", "Picknicktisch"],
                                    "location": {"latitude": 1.0, "longitude": 1.0},
                                },
                                {
                                    "name": "Kleine Lichtung",
                                    "url": "https://park4night.com/de/place/513699",
                                    "cons": ["nur 1 Van"],
                                },
                            ]
                        }
                    }
                },
                "reason": "Plan B/C",
            },
        ])
        day_state = next(d for d in trip_days(store) if d["id"] == day1)
        names = [stop["name"] for stop in day_state["stops"]]
        assert names[1] == "Wanderung Nordrunde", (
            f"string position '2' must place the hike second, p4n id stripped: {names}"
        )
        hike = day_state["stops"][1]
        assert "Kategorie: Wanderung" in str(hike.get("notes") or "")
        assert "park4night.com/lieu/277266" in str(hike.get("notes") or "")
        options = day_state["details"]["overnight_plan"]["options"]
        by_name = {option["name"]: option for option in options}
        assert by_name["Entré Nord Skuleskogen"]["source"]["provider_id"] == "51373"
        assert by_name["Entré Nord Skuleskogen"]["location"] == {}, (
            "model coordinates on options are never accepted"
        )
        assert by_name["Kleine Lichtung"]["cons"] == ["nur 1 Van"]

        # --- Fake response 2: add-then-move + echoed changes on remove ---
        run_pipeline(store, [
            {
                "operation_id": "op-1",
                "action": "add",
                "entity_type": "stop",
                "entity_id": "new-stop-lunch",
                "day_id": day1,
                "place_query": "Aussichtspunkt",
                "changes": {"name": "Mittag am Aussichtspunkt", "type": "break"},
                "reason": "Pause",
            },
            {
                "operation_id": "op-2",
                "action": "move",
                "entity_type": "stop",
                "entity_id": "new-stop-lunch",
                "day_id": day1,
                "position": 2,
                "changes": {"name": "Mittag am Aussichtspunkt"},
                "reason": "Vor die Wanderung",
            },
        ])
        day_state = next(d for d in trip_days(store) if d["id"] == day1)
        assert [s["name"] for s in day_state["stops"]][1] == "Mittag am Aussichtspunkt", (
            "moving a stop added in the same draft must work end-to-end"
        )

        # --- Fake response 3: contradiction fails CLEARLY at sanitize ----
        lunch_id = day_state["stops"][1]["id"]
        try:
            run_pipeline(store, [
                {
                    "operation_id": "op-1",
                    "action": "remove",
                    "entity_type": "stop",
                    "entity_id": lunch_id,
                    "day_id": day1,
                    "changes": {},
                    "reason": "Streichen",
                },
                {
                    "operation_id": "op-2",
                    "action": "update",
                    "entity_type": "stop",
                    "entity_id": lunch_id,
                    "day_id": day1,
                    "changes": {"notes": "Doch behalten?"},
                    "reason": "Widerspruch",
                },
            ])
        except ValidationError as err:
            assert "im selben Entwurf" in str(err)
        else:
            raise AssertionError("remove-then-reference must fail at sanitize time")
        check_invariants(store)

        # --- Fake response 4: stale day reference is auto-corrected ------
        day2 = store.add_day(
            actor="sim", expected_revision=revision(store),
            title="Liden", day_date="2026-08-01",
        )["day"]["id"]
        camp_id = next(
            stop["id"]
            for stop in next(d for d in trip_days(store) if d["id"] == day1)["stops"]
            if stop["name"] == "See westlich Docksta"
        )
        run_pipeline(store, [
            {
                "operation_id": "op-1",
                "action": "update",
                "entity_type": "stop",
                "entity_id": camp_id,
                "day_id": day2,  # wrong day - the model guessed
                "changes": {"notes": "Grillrost mitnehmen."},
                "reason": "Hinweis",
            },
        ])
        camp = next(
            stop
            for stop in next(d for d in trip_days(store) if d["id"] == day1)["stops"]
            if stop["id"] == camp_id
        )
        assert "Grillrost" in str(camp.get("notes") or ""), (
            "the stale day reference must be corrected and the note applied "
            "to the real stop"
        )
    print("Pipeline simulation passed.")


if __name__ == "__main__":
    run_pipeline_simulation()
