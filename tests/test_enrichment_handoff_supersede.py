"""Behavioral tests: a new place-enrichment submit supersedes stale duplicates.

Live bug report (screenshots): confirming a stop's place profile twice left
TWO pending "Ortsprofile vervollständigen" handoffs for the same stop under
Übergaben - easy to do, because the "Ortsprofil vervollständigen" badge on
the stop card stays visible while the first handoff sits unapplied. The
older handoff then went stale as the trip revision advanced (Basis 195 vs
Revision 196), showing a conflict warning that was pure noise: the newer
handoff already contained the fresher confirmation of the same place.

Submitting a new enrichment now archives older PENDING enrichment handoffs
that touch any of the same stops (resolution "superseded"), before the new
handoff is ingested. Handoffs from other sources, or enrichment handoffs
for other stops, are never touched.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_enrichment_supersede_test"

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
    # The orchestrator imports manager (heavy). Stub the modules it names.
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


def _envelope(operations):
    return {"changeset": {"operations": operations}}


def verify_only_same_stop_enrichment_handoffs_are_superseded() -> None:
    pending = [
        {"id": "h-old-same-stop", "source": "roadplanner_place_enrichment"},
        {"id": "h-other-stop", "source": "roadplanner_place_enrichment"},
        {"id": "h-assistant", "source": "roadplanner_assistant"},
    ]
    envelopes = {
        "h-old-same-stop": _envelope(
            [{"entity_type": "stop", "entity_id": "stop-1", "action": "update"}]
        ),
        "h-other-stop": _envelope(
            [{"entity_type": "stop", "entity_id": "stop-9", "action": "update"}]
        ),
        "h-assistant": _envelope(
            [{"entity_type": "stop", "entity_id": "stop-1", "action": "update"}]
        ),
    }
    result = module.find_superseded_enrichment_handoffs(
        pending, envelopes, {"stop-1"}
    )
    assert result == ["h-old-same-stop"], (
        "only enrichment handoffs touching the same stop may be superseded - "
        f"got {result}"
    )


def verify_no_new_stops_means_nothing_is_touched() -> None:
    pending = [{"id": "h-1", "source": "roadplanner_place_enrichment"}]
    envelopes = {"h-1": _envelope([{"entity_type": "stop", "entity_id": "stop-1"}])}
    assert module.find_superseded_enrichment_handoffs(pending, envelopes, set()) == []


def verify_missing_envelope_or_garbage_is_skipped() -> None:
    pending = [
        {"id": "h-no-envelope", "source": "roadplanner_place_enrichment"},
        {"id": "h-garbage", "source": "roadplanner_place_enrichment"},
        "kein objekt",
    ]
    envelopes = {"h-garbage": {"changeset": "kaputt"}}
    assert module.find_superseded_enrichment_handoffs(
        pending, envelopes, {"stop-1"}
    ) == []


def verify_multi_stop_overlap_matches_on_any_shared_stop() -> None:
    pending = [{"id": "h-multi", "source": "roadplanner_place_enrichment"}]
    envelopes = {
        "h-multi": _envelope(
            [
                {"entity_type": "stop", "entity_id": "stop-1"},
                {"entity_type": "stop", "entity_id": "stop-2"},
                {"entity_type": "day", "entity_id": "day-1"},
            ]
        )
    }
    assert module.find_superseded_enrichment_handoffs(
        pending, envelopes, {"stop-2", "stop-7"}
    ) == ["h-multi"]


def verify_submit_path_calls_supersede_before_ingest() -> None:
    source = (PACKAGE_ROOT / "place_enrichment_orchestrator.py").read_text(
        encoding="utf-8"
    )
    supersede_at = source.index("_async_supersede_stale_enrichment_handoffs(\n            _stop_ids_of_operations")
    ingest_at = source.index("async_ingest_external_changeset(", supersede_at)
    assert supersede_at < ingest_at, (
        "stale duplicates must be archived BEFORE the new handoff is ingested"
    )
    assert 'resolution="superseded"' in source


if __name__ == "__main__":
    verify_only_same_stop_enrichment_handoffs_are_superseded()
    verify_no_new_stops_means_nothing_is_touched()
    verify_missing_envelope_or_garbage_is_skipped()
    verify_multi_stop_overlap_matches_on_any_shared_stop()
    verify_submit_path_calls_supersede_before_ingest()
    print("Enrichment handoff supersede tests passed.")
