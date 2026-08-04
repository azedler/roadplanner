"""Stops without GPS that link a Park4Night page fill themselves.

Live request: a wildcamp stop showed "Ort fehlt" while carrying three
Park4Night links - everything needed to place it on the map was already
there. The automation reads those pages deterministically and parks the
coordinates as a REVIEW ChangeSet, never applying anything by itself.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_p4n_autofill_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
ha_core.callback = lambda func: func
sys.modules.setdefault("homeassistant.core", ha_core)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_event = types.ModuleType("homeassistant.helpers.event")
ha_event.async_call_later = lambda *a, **k: None
ha_event.async_track_time_interval = lambda *a, **k: (lambda: None)
sys.modules.setdefault("homeassistant.helpers.event", ha_event)
ha_util = types.ModuleType("homeassistant.util")
ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", ha_util)
ha_dt = types.ModuleType("homeassistant.util.dt")
ha_dt.now = None
ha_dt.utcnow = None
sys.modules.setdefault("homeassistant.util.dt", ha_dt)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


load("destination_intelligence")
load("canonical_day")
load("experience_store")
module = load("park4night_autofill")

DAYS = [
    {
        "id": "day-19",
        "stops": [
            {
                "id": "stop-a",
                "name": "Angelsee mit Shelter (Plan A)",
                "type": "wildcamp",
                "notes": "Plan A https://park4night.com/de/place/603309",
                "location": {},
            },
            {
                "id": "stop-b",
                "name": "Hat schon GPS",
                "type": "stellplatz",
                "notes": "https://park4night.com/lieu/110490/",
                "location": {"latitude": 59.9, "longitude": 15.2},
                "details": {"geocoding": {"status": "resolved"}},
            },
            {
                "id": "stop-c",
                "name": "Ohne jeden Link",
                "type": "waypoint",
                "location": {},
            },
            {
                # Live report: a stop that has long since become a different
                # place still carried the Park4Night ids of an earlier
                # enrichment run in its place_profile. Filling coordinates
                # from that page would move it somewhere the user never named.
                "id": "stop-d",
                "name": "Übernachtungsplatz",
                "type": "overnight",
                "notes": "laut Google Maps: https://maps.app.goo.gl/kbHKXdHxaekk",
                "location": {},
                "details": {
                    "place_profile": {
                        "source_hints": [
                            {
                                "provider": "park4night",
                                "url": "https://park4night.com/lieu/129424/",
                            }
                        ]
                    }
                },
            },
        ],
    }
]


def verify_only_gps_less_stops_with_a_link_are_candidates() -> None:
    candidates = module.find_autofill_candidates(DAYS)
    assert [item["stop_id"] for item in candidates] == ["stop-a"], (
        "historical Park4Night hints in place_profile are provenance, not a "
        f"reference to the stop's current place: {candidates}"
    )
    assert candidates[0]["place_id"] == "603309"
    assert candidates[0]["url"] == "https://park4night.com/lieu/603309/"
    assert module.find_autofill_candidates([]) == []
    assert module.find_autofill_candidates([{"stops": "kaputt"}]) == []


def verify_operations_are_reviewable_and_never_invent_gps() -> None:
    operations = module.build_autofill_operations(
        [
            {
                "day_id": "day-19",
                "stop_id": "stop-a",
                "stop_name": "Angelsee",
                "place_id": "603309",
                "url": "https://park4night.com/lieu/603309/",
                "latitude": 58.7123,
                "longitude": 14.5987,
            }
        ]
    )
    assert len(operations) == 1
    operation = operations[0]
    assert operation["action"] == "update" and operation["entity_type"] == "stop"
    assert operation["entity_id"] == "stop-a" and operation["day_id"] == "day-19"
    location = operation["changes"]["location"]
    assert location["latitude"] == 58.7123 and location["longitude"] == 14.5987
    geocoding = operation["changes"]["details"]["geocoding"]
    assert geocoding["provider"] == "park4night"
    assert geocoding["provider_verified"] is False, (
        "a page-read coordinate is never marked provider-verified"
    )
    assert "603309" in operation["reason"]
    # The operation id is stable for the same facts (no duplicate handoffs).
    again = module.build_autofill_operations(
        [
            {
                "day_id": "day-19",
                "stop_id": "stop-a",
                "stop_name": "Angelsee",
                "place_id": "603309",
                "url": "https://park4night.com/lieu/603309/",
                "latitude": 58.7123,
                "longitude": 14.5987,
            }
        ]
    )
    assert again[0]["operation_id"] == operation["operation_id"]


class FakeLookup:
    def __init__(self, result) -> None:
        self.result = result
        self.calls: list[str] = []

    async def async_lookup(self, url: str, *, hint_name: str = ""):
        self.calls.append(url)
        return self.result


class FakeManager:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.ingested: list[dict] = []

    async def async_get_assistant_payload(self, trip_id=None) -> dict:
        return self.payload

    async def async_ingest_external_changeset(self, *, changeset, **kwargs) -> dict:
        self.ingested.append(changeset)
        return {"handoff": {"id": "handoff-1", "status": "pending"}}


PAYLOAD = {
    "selected_is_active": True,
    "selected_trip_id": "trip-1",
    "summary": {"trip": {"id": "trip-1"}, "revision": 338},
    "days": {"days": DAYS},
}


def verify_run_parks_a_review_handoff_and_never_applies() -> None:
    manager = FakeManager(PAYLOAD)
    lookup = FakeLookup({"latitude": 58.7123, "longitude": 14.5987, "name": "Angelsee"})
    autofill = module.Park4NightAutofillManager(None, manager, lookup)
    status = asyncio.run(autofill.async_run())
    assert status["state"] == "ready" and status["filled"] == 1
    assert lookup.calls == ["https://park4night.com/lieu/603309/"]
    changeset = manager.ingested[0]
    assert changeset["apply_mode"] == "review", "nothing is applied unattended"
    assert changeset["metadata"]["review_only"] is True
    assert changeset["base_revision"] == 338
    assert len(changeset["operations"]) == 1

    # The same stop is not attempted again on the next run - a page without
    # usable GPS could otherwise loop forever.
    status = asyncio.run(autofill.async_run())
    assert status["filled"] == 0
    assert len(manager.ingested) == 1
    # ... unless the run is forced.
    asyncio.run(autofill.async_run(force=True))
    assert len(manager.ingested) == 2


def verify_unusable_pages_never_produce_a_handoff() -> None:
    for result in (None, {"name": "ohne GPS"}, {"latitude": 999, "longitude": 0}):
        manager = FakeManager(PAYLOAD)
        autofill = module.Park4NightAutofillManager(None, manager, FakeLookup(result))
        status = asyncio.run(autofill.async_run())
        assert status["filled"] == 0, result
        assert manager.ingested == [], result

    # An inactive trip is never touched.
    manager = FakeManager({**PAYLOAD, "selected_is_active": False})
    autofill = module.Park4NightAutofillManager(None, manager, FakeLookup({}))
    assert asyncio.run(autofill.async_run())["state"] == "inactive_trip"
    assert manager.ingested == []


def verify_wiring() -> None:
    init_source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "Park4NightAutofillManager(" in init_source
    assert "await park4night_autofill.async_initialize()" in init_source
    assert "await entry.runtime_data.park4night_autofill.async_shutdown()" in init_source
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert '"park4night_autofill_run",' in panel_source
    assert 'if action == "park4night_autofill_run":' in panel_source


if __name__ == "__main__":
    verify_only_gps_less_stops_with_a_link_are_candidates()
    verify_operations_are_reviewable_and_never_invent_gps()
    verify_run_parks_a_review_handoff_and_never_applies()
    verify_unusable_pages_never_produce_a_handoff()
    verify_wiring()
    print("Park4Night autofill tests passed.")
