"""Regression tests for the four confirmed handoff-transactionality findings.

[9]  Older enrichment handoffs were archived BEFORE the replacement was
     ingested - a failing ingest silently destroyed the user's only pending
     confirmation. The supersede now runs strictly after a successful
     ingest (source-order contract lives in
     test_enrichment_handoff_supersede.py) and never archives the
     replacement itself.
[11] Two racing submits could archive EACH OTHER (both scans ran before
     either ingest, or after both). Ordering by (received_at, id) archives
     only strictly older handoffs, so exactly one survives.
[10] mark_applied persisted the FULL coordinator payload ("trip"), which
     can exceed the 256-KiB envelope bound - the validation then raised
     AFTER the trip commit was durable, wedging an applied handoff as
     "pending" forever. The stored result is now compacted.
[12] Destination galleries were persisted at submit time even for a
     duplicate or conflict ingest; now only a cleanly ingested handoff
     publishes its galleries (source contract).
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_handoff_audit_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

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
ha_storage = types.ModuleType("homeassistant.helpers.storage")
ha_storage.Store = object
sys.modules.setdefault("homeassistant.helpers.storage", ha_storage)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def _load_orchestrator():
    for stub_name, attributes in (
        ("const", {"EVENT_ROADPLANNER_UPDATED": "roadplanner_updated"}),
        ("experience_helpers", {"_all_days": lambda payload: []}),
        (
            "experience_store",
            {
                "ExperienceStore": object,
                "new_id": lambda prefix: f"{prefix}-x",
                "utc_now_iso": lambda: "2026-07-30T12:00:00+00:00",
            },
        ),
        ("manager", {"RoadplannerManager": object}),
        ("place_enrichment", {"PlaceEnrichmentService": object}),
        ("roadplanner", {"ValidationError": type("ValidationError", (Exception,), {})}),
    ):
        stub = types.ModuleType(f"{PACKAGE_NAME}.{stub_name}")
        for key, value in attributes.items():
            setattr(stub, key, value)
        sys.modules[f"{PACKAGE_NAME}.{stub_name}"] = stub
    return load("place_enrichment_orchestrator")


module = _load_orchestrator()


def _envelope(stop_id):
    return {"changeset": {"operations": [
        {"entity_type": "stop", "entity_id": stop_id, "action": "update"}
    ]}}


PENDING = [
    {"id": "h-old", "source": "roadplanner_place_enrichment", "received_at": "2026-07-30T10:00:00Z"},
    {"id": "h-new", "source": "roadplanner_place_enrichment", "received_at": "2026-07-30T12:00:00Z"},
]
ENVELOPES = {"h-old": _envelope("stop-1"), "h-new": _envelope("stop-1")}


# --- [9] the replacement itself is never archived ------------------------

def verify_replacement_is_never_archived() -> None:
    result = module.find_superseded_enrichment_handoffs(
        PENDING, ENVELOPES, {"stop-1"},
        exclude_id="h-new",
        newer_than=("2026-07-30T12:00:00Z", "h-new"),
    )
    assert result == ["h-old"], f"only the OLDER handoff may be archived, got {result}"


# --- [11] racing submits archive only strictly older handoffs ------------

def verify_racing_submits_leave_exactly_one_survivor() -> None:
    # Simulate both submits running their scan after both ingested:
    # the OLDER one sees the newer but must not archive it.
    from_old = module.find_superseded_enrichment_handoffs(
        PENDING, ENVELOPES, {"stop-1"},
        exclude_id="h-old",
        newer_than=("2026-07-30T10:00:00Z", "h-old"),
    )
    assert from_old == [], (
        "the older submit must not archive the newer handoff - otherwise "
        "two racing submits archive each other and none survives"
    )
    from_new = module.find_superseded_enrichment_handoffs(
        PENDING, ENVELOPES, {"stop-1"},
        exclude_id="h-new",
        newer_than=("2026-07-30T12:00:00Z", "h-new"),
    )
    assert from_new == ["h-old"]


def verify_equal_timestamps_tie_break_deterministically() -> None:
    pending = [
        {"id": "h-a", "source": "roadplanner_place_enrichment", "received_at": "2026-07-30T12:00:00Z"},
        {"id": "h-b", "source": "roadplanner_place_enrichment", "received_at": "2026-07-30T12:00:00Z"},
    ]
    envelopes = {"h-a": _envelope("stop-1"), "h-b": _envelope("stop-1")}
    from_a = module.find_superseded_enrichment_handoffs(
        pending, envelopes, {"stop-1"}, exclude_id="h-a",
        newer_than=("2026-07-30T12:00:00Z", "h-a"),
    )
    from_b = module.find_superseded_enrichment_handoffs(
        pending, envelopes, {"stop-1"}, exclude_id="h-b",
        newer_than=("2026-07-30T12:00:00Z", "h-b"),
    )
    assert (from_a, from_b) == ([], ["h-a"]), (
        "exact-timestamp ties must resolve deterministically by id - one "
        f"survivor, got {from_a} / {from_b}"
    )


# --- [10] the stored apply result is bounded -----------------------------

def verify_apply_result_is_compacted_before_persisting() -> None:
    manager_source = (PACKAGE_ROOT / "manager.py").read_text(encoding="utf-8")
    assert manager_source.count("result=_compact_apply_result(apply_result)") == 2, (
        "BOTH mark_applied call sites (async apply + auto-apply) must "
        "persist the compacted result"
    )
    namespace: dict = {}
    compact_src = manager_source[
        manager_source.index("def _compact_apply_result"):
        manager_source.index("class RoadplannerManager")
    ]
    exec("from typing import Any\n" + compact_src, namespace)
    compact = namespace["_compact_apply_result"](
        {
            "changed": True,
            "revision": 208,
            "trip": {"id": "trip-1", "days": [{"notes": "x" * 400_000}]},
        }
    )
    assert compact == {"changed": True, "revision": 208, "trip_id": "trip-1"}
    assert len(json.dumps(compact)) < 1_000, "the stored result must stay tiny"


# --- [12] galleries only on a clean ingest (source contract) -------------

def verify_galleries_gated_on_clean_ingest() -> None:
    source = (PACKAGE_ROOT / "place_enrichment_orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "if galleries and clean_ingest:" in source, (
        "gallery persistence must be gated on a cleanly ingested handoff - "
        "a duplicate/conflict ingest must not publish media"
    )


if __name__ == "__main__":
    verify_replacement_is_never_archived()
    verify_racing_submits_leave_exactly_one_survivor()
    verify_equal_timestamps_tie_break_deterministically()
    verify_apply_result_is_compacted_before_persisting()
    verify_galleries_gated_on_clean_ingest()
    print("Handoff transactionality audit fix tests passed.")
