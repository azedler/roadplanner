"""What the TravelStoryManifest promises, checked against what it does.

Three of its four promises are the kind that quietly stop being true:
determinism dies the moment something iterates a dict from a store, "only
real facts" dies the moment a sentence template mentions a value that may
be missing, and a content hash is worthless if nobody verifies it. Each has
a test here that would fail rather than degrade.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


story = load("travel_story_manifest")


def _chapter(index: int = 0, **overrides):
    arguments = {
        "day_id": f"day-{index + 1}",
        "index": index,
        "date": "2026-07-23",
        "title": "Nuuksio Nationalpark",
        "stops": [
            {"stop_id": "stop-a", "name": "Tampere", "kind": "city", "arrival_time": "09:00"},
            {"stop_id": "stop-b", "name": "Vaasa", "kind": "camp", "arrival_time": "16:10"},
        ],
        "media": [
            {"media_id": "media-1", "role": "day_cover", "stop_id": None},
            {"media_id": "media-2", "role": "highlight", "stop_id": "stop-a"},
        ],
        "facts": {"distance_km": 214.6, "duration_minutes": 195, "photo_count": 81},
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


def verify_the_same_trip_produces_the_same_bytes() -> None:
    """Determinism is the property caching is built on."""
    first = _manifest()
    second = _manifest()
    assert story.canonical_json(first) == story.canonical_json(second)
    assert first["content_hash"] == second["content_hash"]
    # And it survives a round trip through JSON, which is how it will
    # actually be stored and compared.
    assert json.loads(json.dumps(first, sort_keys=True)) == first


def verify_the_order_chapters_arrive_in_does_not_matter() -> None:
    """A store returning days in a different order must not change the hash."""
    ordered = _manifest([_chapter(0), _chapter(1), _chapter(2)])
    shuffled = _manifest([_chapter(2), _chapter(0), _chapter(1)])
    assert ordered["content_hash"] == shuffled["content_hash"]
    assert [chapter["index"] for chapter in shuffled["chapters"]] == [0, 1, 2]


def verify_a_changed_trip_produces_a_different_hash() -> None:
    """Otherwise a cache would serve a stale description forever."""
    before = _manifest()
    after = _manifest([_chapter(0), _chapter(1, title="Ein anderer Tag")])
    assert before["content_hash"] != after["content_hash"]


def verify_a_tampered_manifest_is_refused() -> None:
    """The hash exists to be checked, or it is decoration."""
    manifest = _manifest()
    assert story.validate_manifest(manifest) is manifest
    manifest["trip"]["title"] = "Eine andere Reise"
    try:
        story.validate_manifest(manifest)
    except story.StoryManifestError as err:
        assert "Hash" in str(err)
        return
    raise AssertionError("Ein verändertes Manifest wurde akzeptiert")


def verify_the_story_prefers_what_a_person_wrote() -> None:
    composed = _chapter()
    assert composed["story"]["source"] == story.STORY_FROM_COMPOSED

    stored = _chapter(stored_summary="Ein langer Fahrtag am See entlang.")
    assert stored["story"]["source"] == story.STORY_FROM_STORED
    assert stored["story"]["text"] == "Ein langer Fahrtag am See entlang."

    typed = _chapter(
        stored_summary="Ein langer Fahrtag am See entlang.",
        overrides={"story": "So war es wirklich."},
    )
    assert typed["story"]["source"] == story.STORY_FROM_OVERRIDE
    assert typed["story"]["text"] == "So war es wirklich."


def verify_an_override_title_is_used_and_recorded() -> None:
    chapter = _chapter(overrides={"title": "Der Tag der Elche"})
    assert chapter["title"] == "Der Tag der Elche"
    assert chapter["title_overridden"] is True
    # And the composed story uses the title the reader will actually see.
    assert "Der Tag der Elche" in chapter["story"]["text"]


def verify_a_composed_story_states_nothing_it_was_not_given() -> None:
    """The one rule that makes generated prose safe to ship.

    A day with no distance, no driving time and no photos must produce a
    sentence about none of them - not a zero, not "unbekannt".
    """
    bare = _chapter(facts={}, stops=[], media=[], title="")
    text = bare["story"]["text"]
    for forbidden in ("Kilometer", "Stunden", "Minuten", "Fotos", "Foto ", "Unterwegs"):
        assert forbidden not in text, f"{forbidden!r} steht in {text!r}"
    # Only the day number may appear as a figure.
    assert re.findall(r"\d+", text) == ["1"], text


def verify_zero_is_treated_as_absent_not_as_a_fact() -> None:
    """A roadbook that records no distance says nothing, not "0 km"."""
    chapter = _chapter(facts={"distance_km": 0, "duration_minutes": 0, "photo_count": 0})
    assert chapter["facts"]["distance_km"] is None
    assert chapter["facts"]["duration_minutes"] is None
    assert "Kilometer" not in chapter["story"]["text"]


def verify_the_facts_that_are_present_reach_the_sentence() -> None:
    text = _chapter()["story"]["text"]
    assert "215 Kilometer" in text
    assert "3 Stunden 15 Minuten" in text
    assert "Tampere und Vaasa" in text
    assert "81 Fotos" in text


def verify_many_stops_are_counted_rather_than_listed() -> None:
    chapter = _chapter(
        stops=[
            {"stop_id": f"stop-{number}", "name": f"Ort {number}"}
            for number in range(7)
        ]
    )
    assert "und 4 weitere" in chapter["story"]["text"]


def verify_openings_vary_but_stay_reproducible() -> None:
    """Twenty-one chapters must not read like a table - and must not drift."""
    openings = {_chapter(index)["story"]["text"].split(".")[0] for index in range(4)}
    assert len(openings) > 1, openings
    assert _chapter(3)["story"]["text"] == _chapter(3)["story"]["text"]


def verify_only_ids_travel_never_resolved_data() -> None:
    """A manifest describes; it does not carry payloads or coordinates."""
    manifest = _manifest()
    serialised = json.dumps(manifest, ensure_ascii=False)
    for forbidden in ("http://", "https://", "data:", "base64", "lat", "lon"):
        assert forbidden not in serialised, forbidden
    chapter = manifest["chapters"][0]
    assert set(chapter["media"][0]) == {"media_id", "role", "stop_id"}
    assert set(chapter["stops"][0]) == {"stop_id", "name", "kind", "arrival_time"}


def verify_a_map_hint_says_whether_not_where() -> None:
    chapter = _chapter(
        map_hint={"kind": "day_route", "stop_ids": ["stop-a", "stop-b"], "has_coordinates": True}
    )
    assert chapter["map_hint"] == {
        "kind": "day_route",
        "stop_ids": ["stop-a", "stop-b"],
        "has_coordinates": True,
    }
    assert _chapter()["map_hint"] is None


def verify_captions_only_exist_where_a_caption_was_written() -> None:
    with_caption = _chapter(
        captions=[
            {"media_id": "media-1", "text": "Die Elchbrücke"},
            {"media_id": "media-2", "text": "   "},
            {"media_id": "", "text": "ohne Bild"},
        ]
    )
    assert with_caption["captions"] == [{"media_id": "media-1", "text": "Die Elchbrücke"}]


def verify_entries_without_an_identifier_are_dropped() -> None:
    chapter = _chapter(
        stops=[{"stop_id": "", "name": "Namenlos"}, {"stop_id": "stop-a", "name": "Tampere"}],
        media=[{"media_id": "", "role": "highlight"}, {"media_id": "media-9"}],
    )
    assert [stop["stop_id"] for stop in chapter["stops"]] == ["stop-a"]
    assert [item["media_id"] for item in chapter["media"]] == ["media-9"]
    # An unknown role falls back rather than travelling as free text.
    assert chapter["media"][0]["role"] == story.MEDIA_ROLE_HIGHLIGHT


def verify_a_chapter_needs_a_stable_identifier() -> None:
    for missing in ("", "   ", None):
        try:
            _chapter(day_id=missing)
        except story.StoryManifestError:
            continue
        raise AssertionError(f"Kapitel ohne ID akzeptiert: {missing!r}")


def verify_the_manifest_counts_where_its_prose_came_from() -> None:
    manifest = _manifest(
        [
            _chapter(0),
            _chapter(1, stored_summary="Geschrieben vom Zusammenfassungsdienst."),
            _chapter(2, overrides={"story": "Von Hand."}),
        ]
    )
    assert manifest["story_sources"] == {"override": 1, "stored": 1, "composed": 1}
    assert manifest["facts"]["chapter_count"] == 3
    assert manifest["facts"]["stop_count"] == 6
    assert manifest["facts"]["photo_count"] == 243


def verify_a_foreign_or_future_manifest_is_refused() -> None:
    cases = [
        {**_manifest(), "schema": "etwas.anderes"},
        {**_manifest(), "manifest_version": 2},
        {**_manifest(), "trip": {}},
        {**_manifest(), "chapters": "keine Liste"},
        {**_manifest(), "content_hash": "zu kurz"},
        "kein Objekt",
    ]
    for case in cases:
        try:
            story.validate_manifest(case)
        except story.StoryManifestError:
            continue
        raise AssertionError(f"Ungültiges Manifest akzeptiert: {case!r:.60}")


def verify_a_duplicate_chapter_is_refused() -> None:
    manifest = _manifest([_chapter(0), _chapter(0)])
    try:
        story.validate_manifest(manifest)
    except story.StoryManifestError as err:
        assert "Doppeltes" in str(err)
        return
    raise AssertionError("Doppeltes Kapitel akzeptiert")


verify_the_same_trip_produces_the_same_bytes()
verify_the_order_chapters_arrive_in_does_not_matter()
verify_a_changed_trip_produces_a_different_hash()
verify_a_tampered_manifest_is_refused()
verify_the_story_prefers_what_a_person_wrote()
verify_an_override_title_is_used_and_recorded()
verify_a_composed_story_states_nothing_it_was_not_given()
verify_zero_is_treated_as_absent_not_as_a_fact()
verify_the_facts_that_are_present_reach_the_sentence()
verify_many_stops_are_counted_rather_than_listed()
verify_openings_vary_but_stay_reproducible()
verify_only_ids_travel_never_resolved_data()
verify_a_map_hint_says_whether_not_where()
verify_captions_only_exist_where_a_caption_was_written()
verify_entries_without_an_identifier_are_dropped()
verify_a_chapter_needs_a_stable_identifier()
verify_the_manifest_counts_where_its_prose_came_from()
verify_a_foreign_or_future_manifest_is_refused()
verify_a_duplicate_chapter_is_refused()
print("Travel story manifest tests passed.")
