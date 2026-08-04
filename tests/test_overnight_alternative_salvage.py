"""An overnight alternative the user named must never vanish from the draft.

Live report: "Das ist die Alternative Übernachtungsoption heute Nacht" plus
a Google-Maps link. The assistant acknowledged it, the draft applied
cleanly - and nothing showed up under "Stellplätze". Every earlier salvage
lived INSIDE the day's overnight_plan branch and could therefore only
repair a plan the model had already emitted. This batch-level check runs
after the whole draft is compiled and notices that the request produced no
overnight option at all.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_overnight_alt_test"
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


sanitizer = load("assistant_operation_sanitizer")

DAY_IDS = {"day-19", "day-20"}
# Verbatim from the live report, phone typo included.
REQUEST = [
    {
        "id": "b-1",
        "text": (
            "Das ist die Alternative übernachrungsoption heute Nacht "
            "https://maps.app.goo.gl/zHxYqvPmQkLnPw2VA"
        ),
    }
]


def verify_a_named_alternative_without_an_option_is_reported() -> None:
    # The model answered with a stop update instead of an overnight option.
    operations = [
        {
            "action": "update",
            "entity_type": "stop",
            "entity_id": "stop-a",
            "day_id": "day-19",
            "changes": {"notes": "Alternative geprüft"},
        }
    ]
    gap = sanitizer.overnight_alternative_gap(
        operations, basket=REQUEST, day_ids=DAY_IDS
    )
    assert gap is not None, "the request must not disappear silently"
    day_id, candidates = gap
    assert day_id == "day-19", "the day the draft touches is the target"
    assert candidates[0]["url"].startswith("https://maps.app.goo.gl/")


def verify_a_model_that_did_its_job_is_left_alone() -> None:
    operations = [
        {
            "action": "update",
            "entity_type": "day",
            "entity_id": "day-19",
            "changes": {
                "details": {
                    "overnight_plan": {
                        "options": [{"name": "Dammtorp", "url": "https://x.test/a"}]
                    }
                }
            },
        }
    ]
    assert (
        sanitizer.overnight_alternative_gap(
            operations, basket=REQUEST, day_ids=DAY_IDS
        )
        is None
    ), "no second option is added next to the one the model already parked"


def verify_the_day_is_never_guessed() -> None:
    # Two days touched, no way to tell which one the alternative belongs to.
    ambiguous = [
        {"action": "update", "entity_type": "stop", "day_id": "day-19", "changes": {}},
        {"action": "update", "entity_type": "stop", "day_id": "day-20", "changes": {}},
    ]
    assert (
        sanitizer.overnight_alternative_gap(
            ambiguous, basket=REQUEST, day_ids=DAY_IDS
        )
        is None
    )
    # ... unless the draft touches no day at all: then "heute Nacht" means
    # the current travel day, which the context states outright.
    gap = sanitizer.overnight_alternative_gap(
        [], basket=REQUEST, day_ids=DAY_IDS, current_day_id="day-19"
    )
    assert gap is not None and gap[0] == "day-19"
    assert (
        sanitizer.overnight_alternative_gap([], basket=REQUEST, day_ids=DAY_IDS)
        is None
    ), "without a stated current day nothing is invented"


def verify_only_a_real_overnight_alternative_triggers_it() -> None:
    for text in (
        # An alternative, but not about sleeping.
        "Als Alternative zum Museum https://maps.app.goo.gl/abc",
        # About sleeping, but not an alternative.
        "Heute Nacht schlafen wir hier https://maps.app.goo.gl/abc",
        # The right intent, but nothing to park.
        "Das ist die Alternative Übernachtungsoption heute Nacht",
    ):
        assert (
            sanitizer.overnight_alternative_gap(
                [], basket=[{"text": text}], day_ids=DAY_IDS, current_day_id="day-19"
            )
            is None
        ), text
    # Coordinates count as a candidate just like a link.
    gap = sanitizer.overnight_alternative_gap(
        [],
        basket=[{"text": "Alternative Übernachtung heute Nacht: 58.712345, 14.598712"}],
        day_ids=DAY_IDS,
        current_day_id="day-19",
    )
    assert gap is not None
    assert gap[1][0]["location"]["latitude"] == 58.712345


def verify_wiring() -> None:
    source = (PACKAGE_ROOT / "assistant.py").read_text(encoding="utf-8")
    assert "_async_overnight_alternative_operation(" in source
    assert "if salvaged_option is not None:" in source
    assert '"apply_mode": "review"' in source, (
        "the salvaged option is reviewed like every other draft, never applied"
    )
    assert "async_resolve_google_maps_place(self.manager.hass, url)" in source, (
        "the user's link is resolved the same way as anywhere else - "
        "no coordinates are ever invented"
    )


if __name__ == "__main__":
    verify_a_named_alternative_without_an_option_is_reported()
    verify_a_model_that_did_its_job_is_left_alone()
    verify_the_day_is_never_guessed()
    verify_only_a_real_overnight_alternative_triggers_it()
    verify_wiring()
    print("Overnight alternative salvage tests passed.")
