"""Tests: "Übernachtung - Plan A/B/C" handover blocks become Stellplatz-Optionen.

Live request: a ChatGPT trip handover lists two to three overnight
candidates per day ("Plan A/B/C" with a Park4Night link and an equipment
list each). The assistant now files Plan A as the day's overnight stop and
every further candidate into day.details.overnight_plan - via a
server-side merge, because the ChangeSet merges ``details`` only one level
deep and a raw model plan would replace the day's existing options
wholesale. Model coordinates are never accepted; the option's url is the
reviewable path to real GPS.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_assistant_overnight_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)
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


pitch = load("pitch_options")
sanitizer = load("assistant_operation_sanitizer")
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError

NOW = "2026-07-31T08:00:00Z"

# The exact shape of the pasted handover: Plan B/C for the Skuleskogen day.
MODEL_PLAN = {
    "options": [
        {
            "name": "Entré Nord Skuleskogen",
            "url": "https://park4night.com/de/place/51373",
            "pros": ["Feuerstelle", "Picknicktisch", "Trockentoilette", "Mülltrennung", "Wohnmobilplätze"],
            "notes": "Plan B laut Übergabe.",
        },
        {
            "name": "Kleine Lichtung südlich des Nationalparks",
            "url": "https://park4night.com/de/place/513699",
            "pros": ["kleine Feuerstelle", "Picknicktisch"],
            "cons": ["Platz für ungefähr einen Van"],
            "location": {"latitude": 63.0, "longitude": 18.0},
        },
    ]
}


def verify_plan_bc_becomes_options_with_p4n_source() -> None:
    plan = pitch.merge_assistant_overnight_plan({}, MODEL_PLAN, now=NOW)
    assert len(plan["options"]) == 2
    first = plan["options"][0]
    assert first["name"] == "Entré Nord Skuleskogen"
    assert first["status"] == "backup"
    assert first["source"] == {
        "type": "assistant",
        "provider": "park4night",
        "provider_id": "51373",
        "url": "https://park4night.com/de/place/51373",
    }
    assert len(first["pros"]) == 4, "pros are capped at 4 like user-entered ones"
    second = plan["options"][1]
    assert second["location"] == {}, (
        "model-provided coordinates are never accepted - the url is the "
        "reviewable path to real GPS"
    )
    assert second["cons"] == ["Platz für ungefähr einen Van"]


def verify_existing_options_survive_and_dedupe() -> None:
    existing_day = {
        "details": {
            "overnight_plan": {
                "strategy": "best_first",
                "options": [
                    pitch.validate_option_input(
                        {
                            "name": "Vom Nutzer angelegt",
                            "source": {"type": "user", "url": "https://park4night.com/de/place/51373"},
                        },
                        now=NOW,
                    )
                ],
            }
        }
    }
    plan = pitch.merge_assistant_overnight_plan(existing_day, MODEL_PLAN, now=NOW)
    names = [option["name"] for option in plan["options"]]
    assert names[0] == "Vom Nutzer angelegt", "user-created options are never lost"
    assert "Entré Nord Skuleskogen" not in names, (
        "the same Park4Night place (id 51373) must not be added twice, even "
        "under a different name"
    )
    assert "Kleine Lichtung südlich des Nationalparks" in names
    assert plan["strategy"] == "best_first", "the stored strategy is kept"


def verify_cap_and_garbage_tolerance() -> None:
    many = {"options": [{"name": f"Platz {i}", "url": f"https://example.org/{i}"} for i in range(10)]}
    plan = pitch.merge_assistant_overnight_plan({}, many, now=NOW)
    assert len(plan["options"]) == pitch.MAX_OVERNIGHT_OPTIONS
    assert pitch.merge_assistant_overnight_plan({}, "kaputt", now=NOW)["options"] == []
    tolerated = pitch.merge_assistant_overnight_plan(
        {}, {"options": [{"url": "https://x"}, "kein dict", {"name": "  "}]}, now=NOW
    )["options"]
    assert [item["name"] for item in tolerated] == ["Stellplatz (Link)"], (
        "malformed candidates are skipped, never fatal - but a URL-only "
        "candidate survives with a placeholder name"
    )


FULL_DAYS = [
    {
        "id": "day-1",
        "stops": [{"id": "stop-1", "type": "start"}],
        "details": {
            "overnight_plan": {
                "strategy": "route_optimal",
                "options": [
                    pitch.validate_option_input({"name": "Bestehende Option"}, now=NOW)
                ],
            }
        },
    }
]

CONTEXT = {
    "trip": {"id": "trip-1"},
    "selected_trip_id": "trip-1",
    "id_catalog": {
        "day_ids": ["day-1"],
        "stop_ids_by_day": {"day-1": ["stop-1"]},
        "preference_ids": [],
    },
    "days": [],
}


def verify_sanitizer_merges_against_the_stored_day() -> None:
    operation = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"details": {"overnight_plan": MODEL_PLAN}},
        "reason": "Übergabe",
    }
    result = sanitizer._sanitize_operation(
        operation, index=0, context=CONTEXT, new_day_refs=set(),
        full_days=FULL_DAYS,
    )
    plan = result["changes"]["details"]["overnight_plan"]
    names = [option["name"] for option in plan["options"]]
    assert names[0] == "Bestehende Option", (
        "the sanitized plan must contain the STORED options first - details "
        "merges one level deep at apply time, so a raw model plan would "
        "have replaced them wholesale"
    )
    assert "Entré Nord Skuleskogen" in names
    assert plan["schema_version"] == pitch.PLAN_SCHEMA_VERSION
    assert all(option.get("id") for option in plan["options"]), "every option has a real id"


def verify_prompt_teaches_the_handover_shape() -> None:
    source = (PACKAGE_ROOT / "assistant_prompt.py").read_text(encoding="utf-8")
    assert "changes.details.overnight_plan" in source
    assert "Plan A/B/C" in source
    assert "Niemals einen zweiten\n  Übernachtungsstopp" in source.replace("\r", "") or "Niemals einen zweiten" in source
    # A SINGLE additional alternative must also land as an option entry -
    # live report: the model emitted overnight_plan with options: [] while
    # the chat claimed the alternative was added.
    assert "EINE einzelne zusätzliche Alternative" in source
    assert "Eine leere\n  options-Liste ist dabei IMMER falsch" in source.replace("\r", "")
    assert "gib trotzdem nur den Link als url an" in source


def verify_nameless_link_candidate_gets_a_placeholder_name() -> None:
    # Live report: "Nimm als Alternative den auf <Link>" produced a candidate
    # WITHOUT a name - it was silently dropped and the patch showed
    # options: [] while the chat claimed success.
    plan = pitch.merge_assistant_overnight_plan(
        {},
        {"options": [
            {"url": "https://park4night.com/lieu/513700/"},
            {"url": "https://maps.app.goo.gl/E86Vj7HVaotksCej8", "place_query": "60.19,17.62"},
        ]},
        now=NOW,
    )
    assert len(plan["options"]) == 2, plan["options"]
    assert plan["options"][0]["name"] == "Park4Night #513700"
    assert plan["options"][1]["name"] == "Stellplatz (Link)"
    assert plan["options"][1]["source"]["url"].startswith("https://maps.app.goo.gl/")
    # Entirely empty candidates still vanish - there is nothing to review.
    empty = pitch.merge_assistant_overnight_plan(
        {}, {"options": [{"notes": "kein Name, kein Link"}]}, now=NOW
    )
    assert empty["options"] == []


def verify_empty_plan_is_salvaged_from_reason_and_basket() -> None:
    # Live report (three times): the model emitted overnight_plan with
    # options: [] although the user named the candidate link - the chat
    # claimed success while nothing was added. The sanitizer now pulls the
    # candidate out of the raw plan/reason, or as last resort out of basket
    # decisions that talk about an alternative.
    operation = {
        "operation_id": "op-1",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"details": {"overnight_plan": {
            "schema_version": 1, "strategy": "route_optimal", "options": [],
        }}},
        "reason": "Fügt den Platz https://park4night.com/de/place/603309 als Alternative hinzu.",
    }
    result = sanitizer._sanitize_operation(
        operation, index=0, context=CONTEXT, new_day_refs=set(),
        full_days=FULL_DAYS,
    )
    plan = result["changes"]["details"]["overnight_plan"]
    names = [option["name"] for option in plan["options"]]
    assert "Park4Night #603309" in names, names
    assert "Bestehende Option" in names, "stored options still survive"

    # Last resort: nothing in plan/reason, but a basket decision names the
    # alternative link.
    basket_operation = {
        "operation_id": "op-2",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"details": {"overnight_plan": {"options": []}}},
        "reason": "Aktualisierung der Übernachtungsoptionen.",
    }
    result = sanitizer._sanitize_operation(
        basket_operation, index=0, context=CONTEXT, new_day_refs=set(),
        full_days=FULL_DAYS,
        basket=[{"text": "Als Alternative haben wir diese Übernachtung geplant: https://park4night.com/de/place/603309"}],
    )
    plan = result["changes"]["details"]["overnight_plan"]
    assert any(o["name"] == "Park4Night #603309" for o in plan["options"]), plan["options"]

    # Live case (3rd report): the alternative was a NATURKARTAN link plus
    # user-dictated coordinates - neither is a Park4Night or Maps link, so
    # it was silently dropped. Salvage now covers any https place link,
    # derives a readable name from the URL slug, and re-attaches the
    # coordinates named in the decision text as the option's reviewable
    # position.
    naturkartan_operation = {
        "operation_id": "op-nk",
        "action": "update",
        "entity_type": "day",
        "entity_id": "day-1",
        "changes": {"details": {"overnight_plan": {"options": []}}},
        "reason": (
            "Alternative Übernachtung: Koordinaten 59.924128, 15.284795 - "
            "https://www.naturkartan.se/de/orebro-lan/rastplats-storbergsudden"
        ),
    }
    result = sanitizer._sanitize_operation(
        naturkartan_operation, index=0, context=CONTEXT, new_day_refs=set(),
        full_days=FULL_DAYS,
    )
    plan = result["changes"]["details"]["overnight_plan"]
    option = next(
        (o for o in plan["options"] if o["name"] == "Rastplats Storbergsudden"),
        None,
    )
    assert option is not None, [o["name"] for o in plan["options"]]
    assert option["source"]["url"].startswith("https://www.naturkartan.se/")
    assert option["location"] == {"latitude": 59.924128, "longitude": 15.284795}, (
        "the coordinates named in the decision text are the option's position"
    )

    # A basket without alternative intent contributes nothing.
    result = sanitizer._sanitize_operation(
        {**basket_operation, "operation_id": "op-3"},
        index=0, context=CONTEXT, new_day_refs=set(), full_days=FULL_DAYS,
        basket=[{"text": "Heute schlafen wir hier: https://park4night.com/de/place/110490"}],
    )
    plan = result["changes"]["details"]["overnight_plan"]
    assert all(o["name"] != "Park4Night #110490" for o in plan["options"]), (
        "the Plan-A link must not be salvaged as a backup option"
    )


if __name__ == "__main__":
    verify_empty_plan_is_salvaged_from_reason_and_basket()
    verify_nameless_link_candidate_gets_a_placeholder_name()
    verify_plan_bc_becomes_options_with_p4n_source()
    verify_existing_options_survive_and_dedupe()
    verify_cap_and_garbage_tolerance()
    verify_sanitizer_merges_against_the_stored_day()
    verify_prompt_teaches_the_handover_shape()
    print("Assistant overnight option handover tests passed.")
