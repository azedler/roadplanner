"""Regression tests for current-plan decision baselines."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

MODULE_PATH = Path("custom_components/roadplanner_mcp/decision_logic.py")
spec = spec_from_file_location("roadplanner_decision_logic", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
spec.loader.exec_module(module)


def stop(stop_id: str, name: str, stop_type: str) -> dict:
    return {
        "id": stop_id,
        "name": name,
        "type": stop_type,
        "position": 4,
        "notes": "Bereits als Übernachtung geplant.",
        "location": {
            "label": name,
            "city": "Pärnu",
            "country_code": "EE",
            "latitude": 58.2,
            "longitude": 24.1,
        },
    }


days = [
    {
        "id": "20260721",
        "date": "2026-07-21",
        "title": "Berg der Kreuze, Saulkrasti & Matsi Beach",
        "stops": [
            stop("stop-hill", "Berg der Kreuze", "sightseeing"),
            stop("stop-matsi", "RMK Matsi Beach", "wildcamp"),
        ],
    }
]
options = [
    {
        "id": "option-1",
        "title": "Camping Konse",
        "summary": "Campingplatz in Pärnu",
        "place_query": "Camping Konse Pärnu Estland",
        "stop_type": "campsite",
        "pros": ["Duschen"],
        "cons": ["Weniger Natur"],
        "estimated_cost": {},
        "details": {},
    },
    {
        "id": "option-2",
        "title": "Kabli RMK-Platz",
        "summary": "Naturplatz an der Küste",
        "place_query": "Kabli RMK Estland",
        "stop_type": "wildcamp",
        "pros": ["Natur"],
        "cons": [],
        "estimated_cost": {},
        "details": {},
    },
    {
        "id": "option-3",
        "title": "Uulu rand",
        "summary": "Ruhiger Platz am Meer",
        "place_query": "Uulu rand Estland",
        "stop_type": "wildcamp",
        "pros": ["Ruhig"],
        "cons": [],
        "estimated_cost": {},
        "details": {},
    },
]

result, linked_day, required, current_id = module.ensure_current_plan_option(
    assistant_message=(
        "Möchtet ihr auf dem geplanten RMK Matsi Beach bleiben oder eine "
        "Alternative wählen?"
    ),
    decision_title="Übernachtungsoptionen Region Pärnu",
    question="Matsi Beach beibehalten oder wechseln?",
    linked_day_id="20260721",
    days=days,
    options=options,
)

assert required is True
assert linked_day == "20260721"
assert current_id == "option-current"
assert len(result) == 4
assert result[0]["title"] == "RMK Matsi Beach"
assert result[0]["is_current_plan"] is True
assert result[0]["change_type"] == "keep_existing"
assert result[0]["existing_stop_id"] == "stop-matsi"
assert all(item["is_current_plan"] is False for item in result[1:])
assert all(item["change_type"] == "replace_existing" for item in result[1:])

compact = module.compact_decision_days(days)
assert compact[0]["stops"][1]["id"] == "stop-matsi"
assert compact[0]["stops"][1]["name"] == "RMK Matsi Beach"

try:
    module.ensure_current_plan_option(
        assistant_message="Soll der aktuelle Plan bleiben oder eine Alternative gewählt werden?",
        decision_title="Unklare Auswahl",
        question="Beibehalten oder wechseln?",
        linked_day_id="missing-day",
        days=days,
        options=options,
    )
except module.DecisionBaselineError as err:
    assert "nicht eindeutig" in str(err)
else:
    raise AssertionError("Missing current plan must block a misleading decision template")

store_source = Path("custom_components/roadplanner_mcp/experience_store.py").read_text(encoding="utf-8")
panel_source = Path("custom_components/roadplanner_mcp/frontend/roadplanner-panel.js").read_text(encoding="utf-8")
assert '"is_current_plan"' in store_source
assert '"current_plan_option_id"' in store_source
assert "Bestehender Plan" in panel_source
assert "Keine Änderung nötig" in panel_source

print("Decision current-plan baseline tests passed.")
