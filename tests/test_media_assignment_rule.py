"""When Roadplanner may assign a photo to a stop without asking.

Live report: 253 photographs sat in "zu prüfen" after one afternoon at a
wildlife park. Every one of them already named the right stop and every
one of them was 799-912 m away - just past a 750 m threshold. Nobody was
in doubt; a place was simply bigger than a number.

The rule therefore asks a second question. Not only "is this close?" but
"is there anything else it could be?". These checks are about that
second question, and about the cases where the answer has to stay no.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")

# `experience_helpers` reaches for Home Assistant's date utilities, which
# are not installed in the release suite. Only two functions are used and
# both are thin wrappers, so they are supplied here rather than pulling in
# Home Assistant for a distance calculation.
_homeassistant = types.ModuleType("homeassistant")
_homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", _homeassistant)
_ha_util = types.ModuleType("homeassistant.util")
_ha_util.__path__ = []
sys.modules.setdefault("homeassistant.util", _ha_util)
_ha_dt = types.ModuleType("homeassistant.util.dt")
_ha_dt.parse_datetime = lambda text: __import__("datetime").datetime.fromisoformat(text)
_ha_dt.as_local = lambda value: value
sys.modules.setdefault("homeassistant.util.dt", _ha_dt)


def _canonical_stops(day):
    """What `canonical_day` does for a plain day, which is all that is needed."""
    return [stop for stop in (day.get("stops") or []) if isinstance(stop, dict)]


def _load_rule():
    """The real thresholds and the real decision, without Home Assistant.

    `media_library_manager` imports Home Assistant, so the decision is
    exercised through a stand-in object that carries only what the method
    touches. The constants and the method itself are the real ones -
    reading them from the module is the whole point, because a copy of
    the rule in a test proves nothing about the rule in the product.
    """
    source = (PACKAGE_ROOT / "media_library_manager.py").read_text(encoding="utf-8")
    start = source.index("    def _assignment_for(")
    end = source.index("    async def async_reassign_media(")
    body = source[start:end]

    # The helper module drags in aiohttp through its siblings, which the
    # release suite does not install. Its own source is executed with the
    # relative imports removed instead of reimplementing the helpers here:
    # a hand-written `_coordinate` in a test is exactly how a rule can
    # pass while the product reads a field name that does not exist.
    helper_source = "\n".join(
        line
        for line in (PACKAGE_ROOT / "experience_helpers.py").read_text(encoding="utf-8").splitlines()
        if not line.startswith("from .")
    )
    module = types.SimpleNamespace()
    helper_namespace: dict = {
        "canonical_roadbook_stops": _canonical_stops,
        "normalize_onedrive_folder_path": lambda value: value,
        "utc_now_iso": lambda: "",
        "ValidationError": ValueError,
    }
    exec(helper_source, helper_namespace)
    helper_names = (
        "_coordinate",
        "_distance_m",
        "_day_date",
        "_stops",
        "_media_local_date",
        "_parse_datetime",
    )
    for name in helper_names:
        setattr(module, name, helper_namespace[name])

    constants = {}
    for line in source.splitlines():
        if line.startswith(("_AUTOMATIC_RADIUS_M", "_CLEAR_", "_SUGGESTED_RADIUS_M")):
            name, _, value = line.partition(" = ")
            constants[name.strip()] = float(value.split("#")[0].strip().replace("_", ""))

    namespace: dict = {
        "_coordinate": module._coordinate,
        "_distance_m": module._distance_m,
        "_day_date": module._day_date,
        "_stops": module._stops,
        "_media_local_date": module._media_local_date,
        "_parse_datetime": module._parse_datetime,
        "dt_util": _ha_dt,
        **constants,
    }
    exec("class _Rule:\n" + body, namespace)
    return namespace["_Rule"](), constants


rule, LIMITS = _load_rule()


def _day(day_id: str, date: str, stops: list[dict]) -> dict:
    return {"id": day_id, "date": date, "stops": stops}


def _stop(stop_id: str, lat: float, lon: float) -> dict:
    return {"id": stop_id, "name": stop_id, "location": {"latitude": lat, "longitude": lon}}


def _photo(lat: float, lon: float, when: str = "2026-08-06T14:00:00+02:00") -> dict:
    return {"taken_at": when, "location": {"latitude": lat, "longitude": lon}}


# One degree of latitude is ~111 km, so metres north are easy to express.
def _north(lat: float, metres: float) -> float:
    return lat + metres / 111_320.0


BASE_LAT = 56.45
BASE_LON = 13.60
DATE = "2026-08-06"


def verify_a_place_larger_than_the_radius_is_still_certain() -> None:
    """The reported case: 800 m away, and nothing else for kilometres."""
    days = [_day("day-1", DATE, [_stop("safari", BASE_LAT, BASE_LON)])]
    photo = _photo(_north(BASE_LAT, 800), BASE_LON)
    verdict = rule._assignment_for(photo, days)
    assert verdict["assignment_status"] == "automatic", verdict
    assert verdict["linked_stop_id"] == "safari"
    assert 750 < verdict["distance_m"] < 900


def verify_being_close_still_wins_on_its_own() -> None:
    """The old rule is untouched: near enough is still enough.

    Everything that was automatic before this change stays automatic,
    even where a second stop is right beside the first - which is the
    case the separation rule would otherwise refuse.
    """
    days = [
        _day(
            "day-1",
            DATE,
            [_stop("platz", BASE_LAT, BASE_LON), _stop("kiosk", _north(BASE_LAT, 300), BASE_LON)],
        )
    ]
    verdict = rule._assignment_for(_photo(_north(BASE_LAT, 100), BASE_LON), days)
    assert verdict["assignment_status"] == "automatic", verdict
    assert verdict["linked_stop_id"] == "platz"


def verify_a_crowded_place_stays_a_question() -> None:
    """Four stops within a kilometre: which one is a genuine coin toss.

    This is the case the old rule got wrong in the other direction - it
    answered automatically because the winner happened to be inside 750 m,
    while a human looking at the map could not have told either.
    """
    days = [
        _day(
            "day-1",
            DATE,
            [
                _stop("markt", _north(BASE_LAT, 900), BASE_LON),
                _stop("kirche", _north(BASE_LAT, 1500), BASE_LON),
                _stop("hafen", _north(BASE_LAT, 1900), BASE_LON),
            ],
        )
    ]
    # 900 m to the winner, 1500 m to the runner-up: closest, but not alone.
    verdict = rule._assignment_for(_photo(BASE_LAT, BASE_LON), days)
    assert verdict["assignment_status"] == "suggested", verdict
    assert verdict["linked_stop_id"] == "markt"


def verify_a_neighbouring_day_can_take_the_certainty_away() -> None:
    """A photo near tomorrow's campsite is ambiguous about the day.

    The runner-up is any other stop, not merely another stop of the same
    day - because the doubt worth keeping here is which day it was.
    """
    days = [
        _day("day-1", DATE, [_stop("heute", _north(BASE_LAT, 900), BASE_LON)]),
        _day("day-2", "2026-08-07", [_stop("morgen", _north(BASE_LAT, 1200), BASE_LON)]),
    ]
    verdict = rule._assignment_for(_photo(BASE_LAT, BASE_LON), days)
    assert verdict["assignment_status"] == "suggested", verdict


def verify_far_is_far_however_alone_it_is() -> None:
    """Beyond the clear radius, being the only candidate is not enough.

    Four kilometres from the only stop of the day is a real distance. It
    may well belong there, and it may equally be a roadside stop nobody
    wrote down - so it is offered, not decided.
    """
    days = [_day("day-1", DATE, [_stop("einsam", BASE_LAT, BASE_LON)])]
    verdict = rule._assignment_for(_photo(_north(BASE_LAT, 4_000), BASE_LON), days)
    assert verdict["assignment_status"] == "suggested", verdict
    assert verdict["distance_m"] > LIMITS["_CLEAR_RADIUS_M"]


def verify_a_different_date_is_never_automatic() -> None:
    """Unchanged, and deliberately: midnight is a real ambiguity."""
    days = [_day("day-1", "2026-08-05", [_stop("gestern", BASE_LAT, BASE_LON)])]
    verdict = rule._assignment_for(_photo(_north(BASE_LAT, 50), BASE_LON), days)
    assert verdict["assignment_status"] == "suggested", verdict


def verify_a_photo_without_coordinates_gets_a_day_at_most() -> None:
    days = [_day("day-1", DATE, [_stop("irgendwo", BASE_LAT, BASE_LON)])]
    verdict = rule._assignment_for({"taken_at": "2026-08-06T14:00:00+02:00"}, days)
    assert verdict["assignment_status"] == "suggested"
    assert verdict.get("linked_stop_id") is None


def verify_the_thresholds_are_the_ones_that_were_argued_about() -> None:
    """The numbers are a decision, so a silent change should be visible."""
    assert LIMITS["_AUTOMATIC_RADIUS_M"] == 750.0, "die alte Schwelle bleibt"
    assert LIMITS["_CLEAR_RADIUS_M"] == 2500.0
    assert LIMITS["_CLEAR_SEPARATION"] == 2.0
    assert LIMITS["_CLEAR_MARGIN_M"] == 800.0
    assert LIMITS["_SUGGESTED_RADIUS_M"] == 5000.0


verify_a_place_larger_than_the_radius_is_still_certain()
verify_being_close_still_wins_on_its_own()
verify_a_crowded_place_stays_a_question()
verify_a_neighbouring_day_can_take_the_certainty_away()
verify_far_is_far_however_alone_it_is()
verify_a_different_date_is_never_automatic()
verify_a_photo_without_coordinates_gets_a_day_at_most()
verify_the_thresholds_are_the_ones_that_were_argued_about()
print("Media assignment rule tests passed.")
