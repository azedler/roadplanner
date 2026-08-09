"""Which stops a film is about, and which ones merely happened.

From the live acceptance of `reise(8)`: a fast-food stop was reading as
the point of a day. The tempting fix is a string filter, and it is the
wrong one - it would leave the next petrol station, pharmacy and car park
to be discovered one live film at a time.

So the rule is about what kind of stop it is, and these checks are
written to fail if anybody ever replaces it with a list of brand names.

The asymmetry that shapes every threshold here: wrongly demoting a real
memory loses something from the film, wrongly leaving a supermarket at
normal weight merely fails to improve it. So an unrecognised stop stays
ordinary, and the evidence a person left - what they wrote, what they
photographed - can always lift a stop back out.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "roadplanner_mcp"

_spec = spec_from_file_location("stop_relevance", SOURCE / "stop_relevance.py")
assert _spec and _spec.loader
relevance = module_from_spec(_spec)
sys.modules[_spec.name] = relevance
_spec.loader.exec_module(relevance)


def verify_the_journey_and_the_logistics_are_told_apart() -> None:
    narrative = [
        {"name": "Wolfsschanze", "type": "sightseeing"},
        {"name": "Elchpark Smålandet", "type": "attraction"},
        {"name": "Fähre Puttgarden", "type": "ferry"},
        {"name": "Skuleskogen", "type": "hike"},
        {"name": "Stellplatz am See", "type": "stellplatz"},
    ]
    functional = [
        {"name": "MAX Burgers", "type": "restaurant"},
        {"name": "Circle K", "type": "fuel"},
        {"name": "Lidl Mjölby", "type": "supermarket"},
        {"name": "Parkplatz E4", "type": "parking"},
        {"name": "Apotheke", "type": "pharmacy"},
    ]
    for stop in narrative:
        assert not relevance.is_functional(stop), stop
        assert relevance.may_lead_day(stop), stop
    for stop in functional:
        assert relevance.is_functional(stop), stop
        assert not relevance.may_lead_day(stop), stop


def verify_it_works_without_a_declared_type() -> None:
    """Roadbooks are filled in by people; the type is often missing."""
    assert relevance.classify({"name": "Tankstelle Aral"}) == "service"
    assert relevance.classify({"name": "Supermarkt Netto"}) == "shopping"
    assert relevance.classify({"name": "Rastplatz Vetlanda"}) == "functional"
    # And anything not recognised keeps ordinary weight rather than being
    # demoted on suspicion.
    unknown = {"name": "Gierloz"}
    assert relevance.classify(unknown) == "unknown"
    assert relevance.may_lead_day(unknown)


def verify_it_is_not_a_list_of_brand_names() -> None:
    """The rule has to survive the next chain nobody thought of."""
    # No brand belongs in the classification data. A list of chains is
    # the string filter this module exists instead of, and it is wrong
    # the first time somebody stops at one nobody here has heard of.
    table = " ".join(
        word for words in relevance._FUNCTIONAL_WORDS.values() for word in words
    ).casefold()
    for brand in ("max", "burgers", "mcdonald", "lidl", "aldi", "shell", "circle k"):
        assert brand not in table, f"Markenname in der Klassifikation: {brand}"
    # Without a declared type, an unrecognised chain stays ordinary
    # rather than being demoted on suspicion of its name.
    assert relevance.classify({"name": "MAX Burgers"}) == "unknown"
    # A never-before-seen fast-food stop is still recognised by its type.
    assert relevance.is_functional({"name": "Frittenwerk Nord", "type": "restaurant"})
    assert relevance.is_functional({"name": "Kaufland", "type": "shopping"})


def verify_a_fragment_does_not_file_a_memory_as_logistics() -> None:
    """Short words have to stand alone; long German compounds may not.

    "Hamburger Straße" contains "burger" and "Burgruine" contains "burg".
    Filing either as a snack bar is the expensive direction of wrong.
    """
    for name in ("Hamburger Straße 4", "Burgruine an der Ostsee", "Parkinsonmuseum"):
        assert relevance.classify({"name": name}) == "unknown", name
    # But a real German compound is still recognised without spaces.
    assert relevance.classify({"name": "Tankstelle"}) == "service"
    assert relevance.classify({"name": "Autohof Nord"}) == "functional"


def verify_what_somebody_wrote_outranks_the_category() -> None:
    """A stop the day talks about was evidently not merely functional."""
    stop = {"name": "MAX Burgers", "type": "restaurant"}
    assert not relevance.may_lead_day(stop)
    # The same stop, on a day whose story is about what happened there.
    lifted = relevance.prominence(
        stop,
        story_text="Bei MAX Burgers haben wir Linus' Geburtstag gefeiert.",
        media_count=9,
    )
    assert lifted > relevance.prominence(stop), lifted
    # And an explicit decision by a person is an answer, not an input.
    pinned = {**stop, "story_prominence": "high"}
    assert relevance.prominence(pinned) == 1.0
    assert relevance.may_lead_day(pinned)
    demoted = {"name": "Wolfsschanze", "type": "sightseeing", "story_prominence": "low"}
    assert not relevance.may_lead_day(demoted)


def verify_a_routers_placeholder_is_not_a_name() -> None:
    """Better no label than a database row in a family film."""
    for name in (
        "Unnamed Rd, 12345 Municipality",
        "unnamed road",
        "Unbenannte Straße 4",
        "",
    ):
        assert relevance.has_no_real_name({"name": name}), name
        assert not relevance.may_lead_day({"name": name, "type": "sightseeing"}), name
    assert not relevance.has_no_real_name({"name": "Skuleskogen"})
    # A story name somebody typed rescues it.
    assert not relevance.has_no_real_name(
        {"name": "Unnamed Rd", "story_name": "Unser Platz am Fjord"}
    )


def verify_the_day_is_ranked_by_what_it_was_about() -> None:
    """The order is prominence, then when it happened. Never the name."""
    stops = [
        {"id": "a", "name": "Circle K", "type": "fuel"},
        {"id": "b", "name": "Wolfsschanze", "type": "sightseeing"},
        {"id": "c", "name": "MAX Burgers", "type": "restaurant"},
        {"id": "d", "name": "Stellplatz Mikołajki", "type": "stellplatz"},
    ]
    ranked = relevance.rank_stops(stops, day_importance="major_highlight")
    assert [entry["name"] for entry in ranked][:2] == [
        "Wolfsschanze",
        "Stellplatz Mikołajki",
    ], ranked
    assert ranked[0]["may_lead"] is True
    assert ranked[-1]["may_lead"] is False
    # Every stop is still there. Suppressed prominence is not deletion.
    assert len(ranked) == len(stops)
    assert {entry["story_category"] for entry in ranked} == {
        "service",
        "narrative",
        "food",
        "overnight",
    }


def verify_nothing_here_ever_raises() -> None:
    """It runs over roadbook data, which contains anything."""
    for junk in (None, "text", 42, [], {"name": None}, {"type": {"x": 1}}):
        assert relevance.classify(junk if isinstance(junk, dict) else {}) in relevance.CATEGORIES
    assert relevance.prominence(None) == 0.0
    assert relevance.rank_stops(None) == []
    assert relevance.rank_stops([None, "x", {"name": "Kiel"}])


for check in (
    verify_the_journey_and_the_logistics_are_told_apart,
    verify_it_works_without_a_declared_type,
    verify_it_is_not_a_list_of_brand_names,
    verify_a_fragment_does_not_file_a_memory_as_logistics,
    verify_what_somebody_wrote_outranks_the_category,
    verify_a_routers_placeholder_is_not_a_name,
    verify_the_day_is_ranked_by_what_it_was_about,
    verify_nothing_here_ever_raises,
):
    check()

print("Stop relevance tests passed.")
