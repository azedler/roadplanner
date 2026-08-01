"""The Vision-chosen trip cover is sticky.

Live report: the trip hero image changed again and again ("bringt mir
immer wieder ein anderes Bild"). The trip-cover curation was re-run
whenever its fingerprint changed - and the fingerprint includes the
candidate photo list and the model name, so every OneDrive photo batch
(and every model change) triggered a fresh Vision pick. A once-chosen
ready cover now stays as long as its photo is still among the candidates;
only an explicit force re-evaluation (or the photo disappearing) changes
the trip image. Stop/day highlights keep their existing behavior.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_cover_sticky_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

# Stub HA/aiohttp and the collaborator modules media_vision_curation imports.
aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
aiohttp_stub.ClientTimeout = lambda *args, **kwargs: None
aiohttp_stub.ClientSession = object
sys.modules.setdefault("aiohttp", aiohttp_stub)
ha = types.ModuleType("homeassistant")
ha.__path__ = []
sys.modules.setdefault("homeassistant", ha)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)

for name, attrs in (
    ("assistant_provider", {"AssistantImageInput": object, "AssistantProvider": object}),
    ("experience_helpers", {"_clean": lambda value, maximum=1000: str(value or "")[:maximum]}),
    ("onedrive_media", {"OneDrivePersonalClient": object}),
    ("roadplanner", {
        "RoadplannerError": type("RoadplannerError", (Exception,), {}),
        "ValidationError": type("ValidationError", (Exception,), {}),
    }),
):
    stub = types.ModuleType(f"{PACKAGE_NAME}.{name}")
    for key, value in attrs.items():
        setattr(stub, key, value)
    sys.modules[stub.__name__] = stub

experience_store_stub = types.ModuleType(f"{PACKAGE_NAME}.experience_store")
experience_store_stub.ExperienceStore = object
experience_store_stub.utc_now_iso = lambda: "2026-08-01T21:00:00Z"
sys.modules[experience_store_stub.__name__] = experience_store_stub

for name in ("media_vision",):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)

spec = spec_from_file_location(
    f"{PACKAGE_NAME}.media_vision_curation",
    PACKAGE_ROOT / "media_vision_curation.py",
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

engine = module.VisionCurationEngine(None, None, None, None)

DAY = {"id": "trip-cover-day", "title": "Finnland / Baltikum 2026"}
STOP = {"id": "trip-cover", "name": "Finnland / Baltikum 2026", "type": "trip", "location": {}}


def candidates(ids: list[str]) -> list[dict]:
    return [
        {"id": value, "file_hash": value, "selection_score": 90, "width": 4000, "height": 3000}
        for value in ids
    ]


def curate(existing, ids, force=False):
    return asyncio.run(
        engine.async_curate(
            trip_id="trip-1",
            kind="trip",
            day=DAY,
            stop=STOP,
            candidates=candidates(ids),
            existing=existing,
            force=force,
        )
    )


READY = {
    "stop_id": "trip-cover",
    "kind": "trip",
    "status": "ready",
    "fingerprint": "old-fingerprint",
    "cover_id": "photo-bakery",
    "highlight_ids": ["photo-bakery"],
}

# New photos arrived -> fingerprint changed -> the chosen cover STAYS.
sticky = curate(READY, ["photo-new-1", "photo-bakery", "photo-new-2"])
assert sticky["status"] == "ready"
assert sticky["cover_id"] == "photo-bakery", "the once-chosen trip cover must not churn"
assert sticky["fingerprint"] != "old-fingerprint", "the fingerprint is refreshed so later runs skip early"

# The chosen photo disappeared from the candidates -> re-evaluation happens
# (falls back to the local pick here, no provider in this test).
gone = curate(READY, ["photo-new-1", "photo-new-2"])
assert gone["cover_id"] != "photo-bakery"

# A forced run re-evaluates deliberately.
forced = curate(READY, ["photo-new-1", "photo-bakery"], force=True)
assert forced["status"] == "local", "force bypasses stickiness and re-runs the pipeline"

# Stop/day curation keeps its behavior: a changed fingerprint re-runs.
travel = asyncio.run(
    engine.async_curate(
        trip_id="trip-1",
        kind="travel",
        day={"id": "day-1"},
        stop={"id": "stop-1", "name": "Stopp", "location": {}},
        candidates=candidates(["a", "photo-bakery", "b"]),
        existing={**READY, "kind": "travel", "stop_id": "stop-1"},
    )
)
assert travel["status"] == "local", "travel curation is not sticky"

print("Sticky trip cover tests passed.")
