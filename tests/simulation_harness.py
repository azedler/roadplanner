"""Boot the REAL Roadplanner store without Home Assistant.

roadplanner.py is deliberately HA-free, so the complete canonical stack -
trip repository, normalization, mutations, overnight plans, changesets,
revision handling - runs against a temp directory exactly as it does in
production. The harness adds the two things every simulation needs:

- ``make_store()``: a fully initialized store with one active trip.
- ``check_invariants(store)``: the ground rules that must hold after EVERY
  mutation. A violated invariant is precisely the class of bug the live
  trip kept hitting ("Stopps sind instabil"): duplicate ids, broken stop
  order, dangling references, silently corrupted plans.

Used by test_trip_simulation.py (scripted realistic session) and
test_trip_fuzz.py (seeded random operation sequences).
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "custom_components/roadplanner_mcp"
PACKAGE_NAME = "roadplanner_simulation"

if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_ROOT)]
    sys.modules[PACKAGE_NAME] = package


def load(name: str):
    full_name = f"{PACKAGE_NAME}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = spec_from_file_location(full_name, PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[full_name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


roadplanner = load("roadplanner")
pitch_options = load("pitch_options")

RoadplannerStore = roadplanner.RoadplannerStore
ValidationError = roadplanner.ValidationError
RevisionConflictError = roadplanner.RevisionConflictError

OVERNIGHT_TYPES = {
    "overnight", "campsite", "camping", "stellplatz", "wildcamp", "accommodation",
}


def make_store(base_dir: Path) -> "roadplanner.RoadplannerStore":
    store = RoadplannerStore(
        roadbook_dir=base_dir / "roadbook",
        backup_dir=base_dir / "backups",
        handoff_dir=base_dir / "handoffs",
    )
    store.initialize(create_if_missing=True)
    return store


def revision(store) -> int:
    return int(store.load_trip()["metadata"]["revision"])


def trip_days(store) -> list[dict]:
    """Full stored day list: [{...day fields..., "stops": [...]}, ...]."""
    documents = store.load_trip()["days"]
    return [
        {**document["day"], "stops": document.get("stops") or []}
        for document in documents
        if isinstance(document, dict) and isinstance(document.get("day"), dict)
    ]


def check_invariants(store) -> None:
    """Assert the ground rules of the canonical model.

    Raises AssertionError with a precise message on the first violation.
    """
    days = trip_days(store)

    day_ids = [str(day.get("id")) for day in days]
    assert len(day_ids) == len(set(day_ids)), f"duplicate day ids: {day_ids}"

    seen_stop_ids: set[str] = set()
    for day in days:
        stops = day.get("stops") or []
        positions = []
        for stop in stops:
            stop_id = str(stop.get("id") or "")
            assert stop_id, f"stop without id on day {day.get('id')}"
            assert stop_id not in seen_stop_ids, (
                f"stop id {stop_id} exists on more than one day"
            )
            seen_stop_ids.add(stop_id)
            position = stop.get("position")
            if position is not None:
                positions.append(int(position))
            assert str(stop.get("name") or "").strip(), (
                f"stop {stop_id} lost its name"
            )
        if positions:
            assert positions == sorted(positions), (
                f"stop positions out of order on day {day.get('id')}: {positions}"
            )
            assert positions == list(range(1, len(positions) + 1)), (
                f"stop positions not gapless 1..n on day {day.get('id')}: {positions}"
            )

        details = day.get("details") if isinstance(day.get("details"), dict) else {}
        plan = details.get("overnight_plan")
        if isinstance(plan, dict):
            options = plan.get("options") or []
            assert len(options) <= pitch_options.MAX_OVERNIGHT_OPTIONS, (
                f"day {day.get('id')} has {len(options)} overnight options"
            )
            option_ids = [str(option.get("id")) for option in options]
            assert len(option_ids) == len(set(option_ids)), (
                f"duplicate option ids on day {day.get('id')}: {option_ids}"
            )
            for option in options:
                assert str(option.get("name") or "").strip(), "option without name"
                assert option.get("status") in ("backup", "rejected"), (
                    f"invalid option status: {option.get('status')}"
                )

        # Back-references from the active overnight stop must point at
        # nothing or at a real option-shaped snapshot - never at garbage.
        for stop in stops:
            stop_details = stop.get("details") if isinstance(stop.get("details"), dict) else {}
            snapshot = stop_details.get("active_overnight_option")
            if snapshot is not None:
                assert isinstance(snapshot, dict) and snapshot.get("name"), (
                    f"stop {stop.get('id')} carries a corrupt overnight snapshot"
                )

    assert revision(store) >= 1, "revision must stay positive"
