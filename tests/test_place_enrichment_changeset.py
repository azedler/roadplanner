"""Validate the concrete place-enrichment operation with the real ChangeSet engine."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE = "roadplanner_place_changeset_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package

for name in ("stop_ordering", "canonical_day", "roadplanner", "changeset"):
    spec = spec_from_file_location(f"{PACKAGE}.{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

changeset = sys.modules[f"{PACKAGE}.changeset"]
raw = {
    "kind": "roadplanner_changeset",
    "version": 1,
    "changeset_id": "changeset-place-001",
    "trip_id": "trip-1",
    "base_revision": 42,
    "created_at": "2026-07-23T12:00:00Z",
    "title": "Ortsprofile vervollständigen",
    "summary": "Ein bestätigtes Ortsprofil ergänzen.",
    "apply_mode": "review",
    "operations": [
        {
            "operation_id": "place-enrich-001",
            "action": "update",
            "entity_type": "stop",
            "entity_id": "stop-terminal",
            "day_id": "day-6",
            "changes": {
                "location": {
                    "label": "Tallinn Terminal D",
                    "address": "Lootsi 13, Tallinn, Estonia",
                    "city": "Tallinn",
                    "country_code": "EE",
                    "latitude": 59.44327,
                    "longitude": 24.76154,
                },
                "details": {
                    "geocoding": {
                        "provider": "nominatim",
                        "status": "resolved",
                        "source_url": "https://www.openstreetmap.org/node/12345",
                        "confirmed_at": "2026-07-23T12:00:00Z",
                    },
                    "place_profile": {
                        "provider": "nominatim",
                        "name": "Tallinn Terminal D",
                        "category": "Fährterminal",
                        "website": "https://www.ts.ee/en/old-city-harbour/",
                        "opening_hours": "24/7",
                        "confidence": 96,
                        "confirmed_at": "2026-07-23T12:00:00Z",
                    },
                },
            },
            "reason": "Vom Benutzer ausgewähltes Ortsprofil.",
        }
    ],
    "open_questions": [],
    "assumptions": [],
    "research_notes": [],
    "metadata": {"created_by": "roadplanner_place_enrichment"},
}

normalized = changeset.normalize_changeset(raw)
assert normalized["trip_id"] == "trip-1"
assert normalized["base_revision"] == 42
assert normalized["apply_mode"] == "review"
assert len(normalized["operations"]) == 1
operation = normalized["operations"][0]
assert operation["op"] == "update_stop"
assert operation["day_id"] == "day-6"
assert operation["stop_id"] == "stop-terminal"
assert operation["patch"]["location"]["latitude"] == 59.44327
assert operation["patch"]["details"]["place_profile"]["name"] == "Tallinn Terminal D"
assert operation["patch"]["details"]["place_profile"]["confidence"] == 96

print("Place enrichment ChangeSet tests passed.")
