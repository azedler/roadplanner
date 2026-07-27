"""Source-level contract for the cross-trip crew (people/vehicle) feature."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "roadplanner_mcp"
STORE = (ROOT / "crew_store.py").read_text(encoding="utf-8")
MANAGER = (ROOT / "crew_manager.py").read_text(encoding="utf-8")
INIT = (ROOT / "__init__.py").read_text(encoding="utf-8")
PANEL = (ROOT / "panel.py").read_text(encoding="utf-8")
PANEL_JS = (ROOT / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8") + (
    ROOT / "frontend" / "features" / "crew.js"
).read_text(encoding="utf-8")

# Retiring a person/vehicle must never delete it - only the active flag flips,
# so historical trips that already embedded a snapshot stay resolvable.
assert "def set_person_active" in STORE
assert "def set_vehicle_active" in STORE
assert '"active": bool(value.get("active", True))' in STORE

ACTIONS = [
    "crew_person_add",
    "crew_person_update",
    "crew_person_retire",
    "crew_person_reactivate",
    "crew_vehicle_add",
    "crew_vehicle_update",
    "crew_vehicle_retire",
    "crew_vehicle_reactivate",
]
for action in ACTIONS:
    assert f'"{action}"' in PANEL, f"panel.py is missing action wiring for {action}"

assert "crew: CrewManager" in INIT
assert "CrewStore(archive_root" in INIT
assert "await crew.async_initialize()" in INIT

assert "async def async_panel_payload" in MANAGER
assert '"crew": crew_state' in PANEL

assert "crewMixin" in PANEL_JS or "crew.js" in PANEL_JS
assert "crew_person_add" in PANEL_JS
assert "crew_vehicle_add" in PANEL_JS

print("Crew management contract tests passed.")
