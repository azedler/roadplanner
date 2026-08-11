"""What the film says a day was, and what it keeps to itself.

A fast-food stop once reached a day's TITLE. That was fixed. The next
film showed supply stops in the small route lines instead - correctly,
by the rule as written: functional names were held back and then filled
the space the narrative ones had not claimed.

But a route line is read as "this is what the day was", not as "this is
what was left over". A fuel stop in it says the wrong thing however it
got there. So functional stops are omitted from the film's own lines,
and a day whose stops are all functional simply has no route line -
nothing beats something misleading, which is the same judgement the
placeholder-name rule already makes.

Two things this must not do, both checked here: invent a new list of
kinds beside the one the project already classifies with, and touch the
roadbook. Every stop stays where the traveller put it.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_film_meta_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

plan = importlib.import_module(f"{_PACKAGE}.trip_film_plan")
relevance = importlib.import_module(f"{_PACKAGE}.stop_relevance")

PLAN_SOURCE = (INTEGRATION / "trip_film_plan.py").read_text(encoding="utf-8")


def _stop(name, kind, **extra):
    return {"name": name, "kind": kind, **extra}


def verify_a_supply_stop_is_not_in_the_film_line() -> None:
    """The report that opened this: it kept appearing in the small lines."""
    stops = [
        _stop("Ein Nationalpark", "destination"),
        _stop("Irgendein Schnellrestaurant", "food"),
    ]
    found = plan.readable_places(stops, limit=3)
    assert "Ein Nationalpark" in found, found
    assert "Irgendein Schnellrestaurant" not in found, found


def verify_the_same_holds_for_the_other_functional_kinds() -> None:
    """One rule, not a special case for the kind that was reported.

    The kinds are read out of the project's own mapping rather than
    listed here from memory - a test that invents its own field values is
    how this project has covered a bug before.
    """
    kinds = sorted(
        kind
        for kind, category in relevance._TYPE_CATEGORY.items()
        if category in relevance.FUNCTIONAL_CATEGORIES
    )
    assert len(kinds) >= 6, kinds
    for kind in kinds:
        stops = [_stop("Ein Aussichtspunkt", "scenic"), _stop("Ein Halt", kind)]
        found = plan.readable_places(stops, limit=3)
        assert found == ["Ein Aussichtspunkt"], (kind, found)


def verify_narrative_kinds_are_shown() -> None:
    """The counterpart, or "hide functional" would just mean "hide"."""
    for kind in ("destination", "sightseeing", "viewpoint", "hike", "overnight"):
        stops = [_stop("Ein Ort", kind)]
        assert plan.readable_places(stops, limit=3) == ["Ein Ort"], kind


def verify_a_day_of_only_supply_stops_says_nothing() -> None:
    """Nothing beats something misleading.

    The old rule let functional names fill the leftover space, so a
    transfer day announced itself with a filling station.
    """
    stops = [_stop("Eine Tankstelle", "fuel"), _stop("Ein Supermarkt", "shopping")]
    assert plan.readable_places(stops, limit=3) == []


def verify_a_stop_the_story_talks_about_is_not_functional_to_that_day() -> None:
    """The one exception, and it is the existing relevance rule.

    Measured rather than assumed. The gradient the project already has
    turns out to be exactly the distinction asked for - a stop is not
    lifted by being NAMED, only by being written about:

        base for a food stop                     0.20
        + named in the day's own story           0.50   still hidden
        + the traveller's own note about it      0.65   shown
        story_prominence: "high"                 1.00   shown

    with the threshold to lead at 0.55. So an incidental mention does not
    promote a supply stop, and a deliberate one does. Nothing new was
    added here; `may_lead_day` decides, as it did before.
    """
    stops = [_stop("Der Hafenkiosk", "food")]
    story = "Der Hafenkiosk war der Grund für diesen Umweg."

    assert plan.readable_places(stops, limit=3) == []
    # Named in passing is not enough.
    assert plan.readable_places(stops, limit=3, story_text=story) == []
    # Named AND written about is.
    written = [_stop("Der Hafenkiosk", "food", story_note="Deshalb der Umweg.")]
    assert plan.readable_places(written, limit=3, story_text=story) == [
        "Der Hafenkiosk"
    ]
    # And an explicit mark from the traveller settles it outright.
    pinned = [_stop("Die Tankstelle", "fuel", story_prominence="high")]
    assert plan.readable_places(pinned, limit=3) == ["Die Tankstelle"]


def verify_a_reporting_caller_can_still_see_everything() -> None:
    """Hidden from the FILM, not removed from the data."""
    stops = [_stop("Ein Museum", "destination"), _stop("Eine Tankstelle", "fuel")]
    assert plan.readable_places(stops, limit=3, include_functional=True) == [
        "Ein Museum",
        "Eine Tankstelle",
    ]


def verify_the_roadbook_is_untouched() -> None:
    """A display rule may not edit the journey.

    The list handed in is the caller's; nothing here may reorder or
    shorten it in place.
    """
    stops = [_stop("Ein Museum", "destination"), _stop("Eine Tankstelle", "fuel")]
    before = [dict(stop) for stop in stops]
    plan.readable_places(stops, limit=3)
    assert stops == before


def verify_a_placeholder_name_is_still_no_place() -> None:
    """The older rule of the same kind, still in force."""
    stops = [_stop("Unnamed Road, 12345 Irgendwo", "destination")]
    assert plan.readable_places(stops, limit=3) == []


def verify_the_label_pipeline_prefers_the_written_name() -> None:
    """The order that exists, pinned - and what does not exist, said.

    Asked for: override > story_name > display_name > place name >
    region > raw. What a stop actually carries by the time the film sees
    it is `story_name` (written by the editing pass) and `name`. There is
    no per-stop human override, no `display_name` and no region on this
    projection - so a lookup for them would be reading a field off an
    object that never had it, which is this project's most repeated bug
    and would answer "" for every stop while looking correct.

    So the pipeline is: the written name wins; otherwise the canonical
    name is reduced conservatively; and a provider placeholder is not a
    name at all.
    """
    written = {"name": "park4night - (595 50) Mjölby - 24 Vetagatan",
               "story_name": "Der Stellplatz am Fluss",
               "kind": "overnight"}
    assert plan.readable_place(written) == "Der Stellplatz am Fluss"

    raw = {"name": "park4night - (595 50) Mjölby - 24 Vetagatan", "kind": "overnight"}
    reduced = plan.readable_place(raw)
    assert "park4night" not in reduced.casefold(), reduced
    assert "595 50" not in reduced, reduced
    assert reduced, "ein brauchbarer Rest muss übrig bleiben"

    # Junk is not shown at all - the last step is nothing, not the raw string.
    assert plan.readable_places(
        [{"name": "Unnamed Road, 12345 Irgendwo", "kind": "destination"}], limit=3
    ) == []


def verify_no_brand_and_no_second_category_list() -> None:
    """The classification exists once, in stop_relevance, and is used.

    A list of kinds copied into the film module would be the project's
    first failure pattern - one thing in two places - and its rule
    against naming a company in a rule, both at once.
    """
    assert "is_functional" in PLAN_SOURCE, "die vorhandene Klassifikation wird umgangen"
    for marker in ("FUNCTIONAL_CATEGORIES = ", '"fuel"', '"pharmacy"', '"supermarket"'):
        assert marker not in PLAN_SOURCE, marker
    lowered = PLAN_SOURCE.casefold()
    for brand in ("mcdonald", "burger", "shell", "aral", "lidl", "aldi", "rewe"):
        assert brand not in lowered, brand
    # And the categories themselves live where they always did.
    assert relevance.FUNCTIONAL_CATEGORIES


for check in (
    verify_a_supply_stop_is_not_in_the_film_line,
    verify_the_same_holds_for_the_other_functional_kinds,
    verify_narrative_kinds_are_shown,
    verify_a_day_of_only_supply_stops_says_nothing,
    verify_a_stop_the_story_talks_about_is_not_functional_to_that_day,
    verify_a_reporting_caller_can_still_see_everything,
    verify_the_roadbook_is_untouched,
    verify_a_placeholder_name_is_still_no_place,
    verify_the_label_pipeline_prefers_the_written_name,
    verify_no_brand_and_no_second_category_list,
):
    check()

print("Film meta line tests passed.")
