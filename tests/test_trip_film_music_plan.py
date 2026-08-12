"""A soundtrack laid out before anything is paid for.

Two failure modes bracket this, and the plan exists to sit between them.

One track looped across twenty-three days says the same thing over a
departure and an arrival. One short piece per day is twenty-three
generations, twenty-three prices and twenty-three audible seams - a cut
between photographs is invisible, a cut between pieces of music is not.

So: few, long sections, and everything about them computable without a
provider ever being called. That last part is what makes the cost dialog
honest and the cache trustworthy.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_musicplan_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

plan_module = importlib.import_module(f"{_PACKAGE}.trip_film_music_plan")

TRIP = {"title": "Ostsee-Runde 2026"}
NARRATIVE = {
    "arc": "23 Tage, sechs Abenteurer und eine große Runde um die Ostsee",
    "opening": "Seen und Lagerfeuer",
    "closing": "Tierische Begegnungen",
}
# The API is documented as generating up to about two minutes.
TRACK = 118.0


def _plan(seconds: float):
    return plan_module.build_plan(
        trip=TRIP, narrative=NARRATIVE, film_seconds=seconds, track_seconds=TRACK
    )


def verify_a_long_film_gets_few_long_sections_not_many_short_ones() -> None:
    """Seven minutes of film, four pieces of music - not twenty-three."""
    plan = _plan(7 * 60)
    names = [section["section"] for section in plan["sections"]]
    assert names == ["aufbruch", "reise", "rueckweg", "finale"], names
    assert plan["generation_count"] == 4
    for section in plan["sections"]:
        assert section["seconds"] >= plan_module.MIN_SECTION_SECONDS, section


def verify_a_short_film_is_one_piece_of_music() -> None:
    """Ninety seconds does not have a "Rückweg" anybody could feel."""
    plan = _plan(90)
    assert plan["generation_count"] == 1, plan["sections"]
    assert plan["sections"][0]["section"] == "reise"


def verify_the_music_covers_the_whole_film() -> None:
    """No silent tail, and no gap in the middle.

    The last section ends exactly where the film does; every other one
    hands over to its neighbour with an overlap rather than a gap.
    """
    for length in (90, 200, 7 * 60, 11 * 60):
        plan = _plan(length)
        sections = plan["sections"]
        assert abs(sections[-1]["end_seconds"] - length) < 0.5, (length, sections[-1])
        assert sections[0]["start_seconds"] == 0.0
        for earlier, later in zip(sections, sections[1:]):
            assert later["start_seconds"] < earlier["end_seconds"], (
                length,
                earlier,
                later,
            )


def verify_the_overlap_is_a_crossfade_not_a_cut() -> None:
    plan = _plan(7 * 60)
    sections = plan["sections"]
    for earlier, later in zip(sections, sections[1:]):
        overlap = earlier["end_seconds"] - later["start_seconds"]
        assert abs(overlap - plan_module.CROSSFADE_SECONDS) < 0.01, overlap
        assert later["fade_in_seconds"] == plan_module.CROSSFADE_SECONDS
    assert sections[0]["fade_in_seconds"] == plan_module.FADE_IN_SECONDS
    assert sections[-1]["fade_out_seconds"] == plan_module.FADE_OUT_SECONDS


def verify_every_section_belongs_to_one_score() -> None:
    """The same style sentence in every prompt.

    Four independently-styled pieces are a playlist. The mood changes
    between sections; the character does not.
    """
    plan = _plan(7 * 60)
    for section in plan["sections"]:
        assert plan_module.BASE_STYLE in section["prompt"], section["section"]
        assert section["mood"].split(",")[0] in section["prompt"]
    # And the style itself carries every exclusion, because the provider
    # has no `negative_prompt` - everything a piece must NOT be has to
    # travel in the same free text as everything it must be. Checked
    # against the style constant rather than against a phrase copied
    # into this file: the wording changed once and this test failed on
    # its own copy while the requirement was met.
    style = plan_module.BASE_STYLE.lower()
    for unwanted in ("gesang", "stimme", "trailer", "melodram", "comedy", "werbe"):
        assert unwanted in style, f"{unwanted!r} wird nicht ausgeschlossen"
    moods = {section["mood"] for section in plan["sections"]}
    assert len(moods) == 4, "jede Sektion hat ihre eigene Stimmung"


def verify_the_same_film_reuses_every_track() -> None:
    """A re-render must never cost anything.

    The key is what actually determines the audio - prompts, lengths,
    model, contract version. Anything else changing (the trip's title,
    a photograph, the day somebody pressed render) must not move it.
    """
    first = _plan(7 * 60)
    again = _plan(7 * 60)
    assert plan_module.plan_cache_key(first, model="m") == plan_module.plan_cache_key(
        again, model="m"
    )
    # A different model is a different sound, so a different key.
    assert plan_module.plan_cache_key(first, model="m") != plan_module.plan_cache_key(
        first, model="other"
    )
    # A materially longer film needs different music.
    longer = _plan(11 * 60)
    assert plan_module.plan_cache_key(first, model="m") != plan_module.plan_cache_key(
        longer, model="m"
    )
    # Section keys are per section, so only what changed is regenerated.
    keys = [
        plan_module.section_cache_key(section, model="m") for section in first["sections"]
    ]
    assert len(set(keys)) == len(keys), keys


def verify_the_dialog_names_a_price_and_what_is_already_paid_for() -> None:
    """"Four sections, three cached, one new" is a different decision."""
    plan = _plan(7 * 60)
    fresh = plan_module.cost_notice(plan, model="lyria", price_per_generation=0.08)
    assert fresh["new_generations"] == 4
    assert abs(fresh["estimated_cost"] - 0.32) < 0.001, fresh
    assert fresh["reused"] is False

    partly = plan_module.cost_notice(
        plan, model="lyria", price_per_generation=0.08, cached=3
    )
    assert partly["new_generations"] == 1
    assert abs(partly["estimated_cost"] - 0.08) < 0.001, partly

    done = plan_module.cost_notice(
        plan, model="lyria", price_per_generation=0.08, cached=4
    )
    assert done["new_generations"] == 0
    assert done["estimated_cost"] == 0.0
    assert done["reused"] is True


def verify_a_film_without_length_asks_for_nothing() -> None:
    assert plan_module.plan_sections(film_seconds=0, track_seconds=TRACK) == []
    empty = plan_module.build_plan(
        trip=TRIP, narrative=NARRATIVE, film_seconds=0, track_seconds=TRACK
    )
    assert empty["generation_count"] == 0
    assert (
        plan_module.cost_notice(empty, model="lyria", price_per_generation=0.08)[
            "estimated_cost"
        ]
        == 0.0
    )


def verify_only_the_films_ends_are_asked_to_sound_like_ends() -> None:
    """Six complete little pieces in a row are a playlist, not a score.

    There is no seed and no "continue the previous piece", so the only
    handle on continuity is what each section is asked to sound like at
    its edges. A section that plays under the middle of the film is never
    heard beginning - it fades up out of the one before it - and asking
    for an `[Intro]` there buys an opening gesture nobody hears as one.
    """
    tags = plan_module.structure_tags
    first = tags(80.0, position=plan_module.POSITION_FIRST)
    middle = tags(80.0, position=plan_module.POSITION_MIDDLE)
    last = tags(80.0, position=plan_module.POSITION_LAST)

    assert first[0] == "[Intro]", first
    assert "[Intro]" not in middle, middle
    assert "[Intro]" not in last, last

    # Only the film's own ending ends. Everywhere else a crossfade
    # follows, and a piece that finishes under a film that carries on is
    # the seam this whole arrangement exists to avoid.
    assert "[Outro]" not in first, first
    assert "[Outro]" not in middle, middle
    assert last[-1] == "[Final Chord]", last

    # A single section IS both ends of the film, so it gets both.
    only = tags(80.0, position=plan_module.POSITION_ONLY)
    assert only[0] == "[Intro]" and "[Outro]" in only, only


def verify_an_unknown_position_keeps_both_ends() -> None:
    """A missing answer must not silently become a middle section.

    That is the failure this project keeps having: an absent value
    rendered as a state. Read as "middle", a film would lose its opening
    and its ending; read as "the whole arc" it is at worst redundant.
    """
    found = plan_module.structure_tags(80.0, position="")
    assert found == plan_module.structure_tags(80.0, position=plan_module.POSITION_ONLY)


def verify_length_still_decides_how_many_parts_are_named() -> None:
    """Position changes the edges; the piece's length changes the middle.

    There is no duration parameter, so how many parts are named is one of
    only two handles on how long the music runs.
    """
    short = plan_module.structure_tags(30.0, position=plan_module.POSITION_MIDDLE)
    long = plan_module.structure_tags(150.0, position=plan_module.POSITION_MIDDLE)
    assert len(long) > len(short), (short, long)
    assert plan_module.TAGS_BODY_SHORT[0] in short


def verify_the_position_reaches_the_actual_request() -> None:
    """The tags are worth nothing if the caller never passes a position.

    This project has already built a module that was correct, tested and
    imported by nobody - its own test passed because it tested the
    module rather than the chain. So: the prompt really differs, and the
    service really names the argument.
    """
    import ast

    section = {"mood": "Weite, ruhige Wärme."}
    prompts = {
        position: plan_module.section_prompt(
            section, motifs=[], seconds=80.0, position=position
        )
        for position in (
            plan_module.POSITION_FIRST,
            plan_module.POSITION_MIDDLE,
            plan_module.POSITION_LAST,
        )
    }
    assert len(set(prompts.values())) == 3, prompts

    assert plan_module.section_position(0, 4) == plan_module.POSITION_FIRST
    assert plan_module.section_position(2, 4) == plan_module.POSITION_MIDDLE
    assert plan_module.section_position(3, 4) == plan_module.POSITION_LAST
    assert plan_module.section_position(0, 1) == plan_module.POSITION_ONLY

    source = (
        ROOT / "custom_components" / "roadplanner_mcp" / "trip_film_music_service.py"
    ).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "section_prompt"
    ]
    assert calls, "der Dienst ruft section_prompt gar nicht mehr auf"
    for call in calls:
        assert any(
            keyword.arg == "position" for keyword in call.keywords
        ), f"section_prompt in Zeile {call.lineno} ohne Position"


for check in (
    verify_only_the_films_ends_are_asked_to_sound_like_ends,
    verify_an_unknown_position_keeps_both_ends,
    verify_length_still_decides_how_many_parts_are_named,
    verify_the_position_reaches_the_actual_request,
    verify_a_long_film_gets_few_long_sections_not_many_short_ones,
    verify_a_short_film_is_one_piece_of_music,
    verify_the_music_covers_the_whole_film,
    verify_the_overlap_is_a_crossfade_not_a_cut,
    verify_every_section_belongs_to_one_score,
    verify_the_same_film_reuses_every_track,
    verify_the_dialog_names_a_price_and_what_is_already_paid_for,
    verify_a_film_without_length_asks_for_nothing,
):
    check()

print("Trip film music plan tests passed.")
