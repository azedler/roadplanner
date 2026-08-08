"""What the story director may and may not bring back.

The prompt is where fact fidelity is asked for; this file is where it is
enforced for everything a filter can still catch. The division matters:
no amount of validation can tell a true sentence from a plausible one, so
these tests cover the failures that ARE mechanical - an id that does not
exist, an enum the model invented, a stop name attached to the wrong day,
an answer that is not an object at all.

The one thing they cannot prove is that the text is true. That is the
prompt's job, and the reason the merge below is narrow rather than clever.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_story_director_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


story = load("travel_story_manifest")
director = load("story_director")


def _chapter(index: int = 0, **overrides):
    arguments = {
        "day_id": f"day-{index + 1}",
        "index": index,
        "date": "2026-07-23",
        "title": "Nuuksio Nationalpark",
        "stops": [
            {"stop_id": "stop-a", "name": "Krumhermsdorf Neuhäuser 40", "kind": "camp"},
            {"stop_id": "stop-b", "name": "Spielplatz am See", "kind": "stop"},
        ],
        "media": [{"media_id": "media-1", "role": "day_cover", "stop_id": None}],
        "facts": {"distance_km": 314.0, "duration_minutes": 284, "photo_count": 7},
    }
    arguments.update(overrides)
    return story.build_chapter(**arguments)


def _manifest(chapters=None, **overrides):
    arguments = {
        "trip_id": "trip-1",
        "title": "Finnland 2026",
        "start_date": "2026-07-17",
        "end_date": "2026-08-06",
        "revision": 42,
        "chapters": chapters if chapters is not None else [_chapter(0), _chapter(1)],
    }
    arguments.update(overrides)
    return story.build_manifest(**arguments)


def verify_the_brief_carries_facts_and_not_photographs() -> None:
    """A model cannot see a picture, and a list of uuids invites invention."""
    brief = director.chapter_brief(_chapter())
    assert brief["chapter_id"] == "day-1"
    assert brief["distance_km"] == 314.0
    assert brief["photo_count"] == 7
    assert "media-1" not in str(brief), "Medien-IDs gehören nicht in den Prompt"
    assert "media" not in brief


def verify_a_hand_written_chapter_is_shown_as_context_and_marked() -> None:
    """The neighbours should match its voice; it must not be rewritten."""
    brief = director.chapter_brief(
        _chapter(overrides={"story": "Wir haben die Fähre knapp verpasst."})
    )
    assert brief["written_by_hand"] == "Wir haben die Fähre knapp verpasst."


def verify_the_trip_brief_names_the_crew_but_carries_no_records() -> None:
    manifest = _manifest(crew={"people": ["Aron", "Mila"], "vehicle": "Der Bulli"})
    brief = director.trip_brief(manifest)
    assert brief["crew"] == ["Aron", "Mila"]
    assert brief["vehicle"] == "Der Bulli"


def verify_the_trip_is_edited_in_a_handful_of_calls_not_one_per_day() -> None:
    """Twenty-three separate calls would produce twenty-three separate trips."""
    chapters = [_chapter(index) for index in range(23)]
    batches = director.chapter_batches(chapters)
    assert len(batches) == 4, [len(batch) for batch in batches]
    assert sum(len(batch) for batch in batches) == 23
    # Order is preserved, or the arc would not line up with the days.
    assert batches[0][0]["chapter_id"] == "day-1"
    assert batches[-1][-1]["chapter_id"] == "day-23"


def verify_an_invented_chapter_id_is_dropped_not_repaired() -> None:
    """Guessing which day was meant would move one day's text onto another."""
    merged = director.merge_arc(
        {
            "chapters": [
                {"chapter_id": "day-1", "importance": "highlight", "story_role": "opening",
                 "visual_style": "hero"},
                {"chapter_id": "tag-der-elche", "importance": "major_highlight",
                 "story_role": "finale", "visual_style": "hero"},
            ]
        },
        known_ids={"day-1", "day-2"},
    )
    assert set(merged["weights"]) == {"day-1"}


def verify_an_invented_enum_falls_back_rather_than_travels() -> None:
    merged = director.merge_arc(
        {"chapters": [{"chapter_id": "day-1", "importance": "weltbewegend",
                       "story_role": "prolog", "visual_style": "imax"}]},
        known_ids={"day-1"},
    )
    assert merged["weights"]["day-1"] == {
        "importance": "normal",
        "story_role": "journey",
        "visual_style": "normal",
    }


def verify_a_story_name_for_a_foreign_stop_is_refused() -> None:
    """Attaching one place's name to another day is exactly the quiet
    wrongness this layer exists to prevent."""
    edited = director.merge_chapters(
        {
            "chapters": [
                {
                    "chapter_id": "day-1",
                    "title": "Abfahrt",
                    "story": "Los ging es.",
                    "video_caption": "Los ging es.",
                    "stop_story_names": [
                        {"stop_id": "stop-a", "story_name": "unser Waldsee"},
                        {"stop_id": "stop-fremd", "story_name": "gestohlen"},
                    ],
                }
            ]
        },
        known_ids={"day-1"},
        stop_ids_by_chapter={"day-1": {"stop-a", "stop-b"}},
    )
    assert edited["day-1"]["stop_story_names"] == {"stop-a": "unser Waldsee"}


def verify_an_empty_edit_is_not_stored_as_an_edit() -> None:
    """A chapter the model returned nothing for must keep its old text."""
    edited = director.merge_chapters(
        {"chapters": [{"chapter_id": "day-1", "title": "", "story": "", "video_caption": ""}]},
        known_ids={"day-1"},
        stop_ids_by_chapter={"day-1": set()},
    )
    assert edited == {}


def verify_a_nonsense_answer_is_refused_rather_than_guessed() -> None:
    for bad in ("kein Objekt", None, 42, []):
        for call in (
            lambda value: director.merge_arc(value, known_ids=set()),
            lambda value: director.merge_chapters(
                value, known_ids=set(), stop_ids_by_chapter={}
            ),
        ):
            try:
                call(bad)
            except director.StoryDirectorError:
                continue
            raise AssertionError(f"Unsinn akzeptiert: {bad!r}")


def verify_the_stored_pass_names_the_material_it_was_written_for() -> None:
    """Without the hash, reuse would be a guess."""
    direction = director.build_direction(
        context_hash="a" * 64,
        narrative={"subtitle": "Drei Wochen Norden"},
        weights={"day-1": {"importance": "highlight", "story_role": "opening",
                           "visual_style": "hero"}},
        chapters={"day-1": {"title": "Abfahrt", "story": "Los.", "video_caption": "Los."}},
        model="gemini-test",
        calls=5,
    )
    assert direction["story_context_hash"] == "a" * 64
    assert direction["calls"] == 5
    # Weight and text for the same day end up in one record.
    entry = direction["chapters"]["day-1"]
    assert entry["importance"] == "highlight"
    assert entry["title"] == "Abfahrt"


def verify_the_directed_text_reaches_the_manifest_but_loses_to_a_person() -> None:
    """The whole trust order, checked where the two layers meet."""
    edit = {
        "title": "Der Tag mit dem verpassten Kaffee",
        "story": "Ein langer Anlauf nach Norden.",
        "video_caption": "Ein langer Anlauf nach Norden.",
        "importance": "transition",
        "story_role": "opening",
    }
    directed = _chapter(direction=edit)
    assert directed["story"]["source"] == "directed"
    assert directed["title"] == "Der Tag mit dem verpassten Kaffee"
    assert directed["video_caption"] == "Ein langer Anlauf nach Norden."
    assert directed["importance"] == "transition"

    with_person = _chapter(
        direction=edit, overrides={"title": "Meiner", "story": "Meine Fassung."}
    )
    assert with_person["story"]["source"] == "override"
    assert with_person["story"]["text"] == "Meine Fassung."
    assert with_person["title"] == "Meiner"
    # The caption is not a story field, so it survives - it is the film's
    # text, and nobody typed a competing one.
    assert with_person["video_caption"] == "Ein langer Anlauf nach Norden."


def verify_the_prompt_forbids_what_no_filter_could_catch() -> None:
    """The prompt is the only defence against a plausible invention.

    A validator can drop an unknown id; it cannot tell that "es regnete"
    was made up. So the rules have to be in the instruction, and this
    checks they are still there.
    """
    for prompt in (director.ARC_SYSTEM_PROMPT, director.CHAPTER_SYSTEM_PROMPT):
        assert "erfindest nichts" in prompt
        for forbidden in ("Wetter", "Gespräche", "Gefühle", "Mahlzeiten", "Bewertungen"):
            assert forbidden in prompt, forbidden
        assert "Es gibt keine weitere Quelle" in prompt
        # And the tone the trip is supposed to have.
        assert "augenzwinkernd" in prompt
        # Plus the two phrasings film v0 actually produced.
        assert "auf dem Plan" in prompt
        assert "Fotos sind an diesem Tag entstanden" in prompt


verify_the_brief_carries_facts_and_not_photographs()
verify_a_hand_written_chapter_is_shown_as_context_and_marked()
verify_the_trip_brief_names_the_crew_but_carries_no_records()
verify_the_trip_is_edited_in_a_handful_of_calls_not_one_per_day()
verify_an_invented_chapter_id_is_dropped_not_repaired()
verify_an_invented_enum_falls_back_rather_than_travels()
verify_a_story_name_for_a_foreign_stop_is_refused()
verify_an_empty_edit_is_not_stored_as_an_edit()
verify_a_nonsense_answer_is_refused_rather_than_guessed()
verify_the_stored_pass_names_the_material_it_was_written_for()
verify_the_directed_text_reaches_the_manifest_but_loses_to_a_person()
verify_the_prompt_forbids_what_no_filter_could_catch()
print("Story director tests passed.")

def verify_a_stop_says_what_it_was_to_the_day() -> None:
    """The lake was a stop on the way, and the story parked there for the night.

    First real day of the first real trip: three stops - home, a lake
    where the family swam and ate, then the actual overnight place - and
    313.8 km for the whole day. What came back was "after the first 313.8
    kilometres we parked the Nugget at the lake", which makes the middle
    stop the destination and puts the entire day's driving before it.
    Both facts were in the data. Neither was in a form a reader could
    use: the brief was a list of names, and a list of names does not say
    which of them the day ended at.

    So the position - which is authoritative, because the stops are in
    travel order - is now stated rather than left to be inferred.
    """
    chapter = {
        "chapter_id": "day-1",
        "date": "2026-07-14",
        "base": {"title": "Abfahrt"},
        "facts": {"day_number": 1, "stop_count": 3, "distance_km": 313.8},
        "stops": [
            {"name": "Krumhermsdorf", "kind": "", "arrival_time": "16:01"},
            {"name": "Spielplatz am See", "kind": "Wegpunkt", "arrival_time": ""},
            {"name": "Uebernachtungsplatz", "kind": "wildcamp", "arrival_time": ""},
        ],
    }
    stops = director.chapter_brief(chapter)["stops"]
    assert "Start" in stops[0], stops
    assert "unterwegs" in stops[1], stops
    assert "Tagesziel" in stops[2], stops
    # The kind still travels: "wildcamp" says something a position cannot.
    assert "wildcamp" in stops[2], stops
    assert "16:01" in stops[0], stops

    # A day with a single stop ends there; it is not a stop on the way to
    # nowhere.
    single = director.chapter_brief({**chapter, "stops": [chapter["stops"][1]]})["stops"]
    assert "Tagesziel" in single[0], single
    assert "unterwegs" not in single[0], single


def verify_the_prompt_explains_what_a_stop_on_the_way_means() -> None:
    """Naming the role is only half of it; the reader has to know the word.

    A rule that says "do not invent" cannot prevent this mistake, because
    nothing was invented - the model read a real list and drew the wrong
    conclusion from it. What stops it is saying, in the instructions,
    that a stop in the middle is not a destination and that the day's
    kilometres belong to the day.
    """
    prompt = director.CHAPTER_SYSTEM_PROMPT
    assert "unterwegs" in prompt
    assert "Tagesziel" in prompt
    assert "weitergegangen" in prompt, "der Prompt muss sagen, dass es danach weitergeht"
    assert "ganzen Tag" in prompt, "die Tageskilometer duerfen keinem Einzelstopp zufallen"


verify_a_stop_says_what_it_was_to_the_day()
verify_the_prompt_explains_what_a_stop_on_the_way_means()
