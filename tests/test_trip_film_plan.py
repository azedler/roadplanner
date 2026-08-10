"""The dramaturgy, checked as behaviour rather than as taste.

Whether a film is beautiful is not testable. Whether a major highlight
gets more screen time than a transfer day is - and so is every rule that
would quietly stop being true: the fixed scene library, the fallbacks for
values nobody implemented, the promise that a thin day is not padded, and
the one that a day without photographs never shows a diagnostic message.

Determinism is checked too, because the whole reason the plan lives in
Python is that the same package must render to the same film.
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


# Loaded AS A PACKAGE rather than by file path. The plan now imports a
# sibling (`stop_relevance`), and a module loaded by path has no parent
# package, so a relative import fails with a message that says nothing
# about the real cause. This has bitten the CI film step once already.
_PACKAGE = "roadplanner_film_plan_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(PACKAGE_ROOT.resolve())]
sys.modules[_PACKAGE] = _root


def load(name: str):
    return importlib.import_module(f"{_PACKAGE}.{name}")


plan_module = load("trip_film_plan")


def _chapter(index=0, *, importance="normal", style="normal", role="journey", photos=2, story="Ein Satz."):
    return {
        "chapter_id": f"day-{index + 1}",
        "index": index,
        "date": "2026-07-23",
        "title": f"Tag {index + 1}",
        "story": story,
        "importance": importance,
        "visual_style": style,
        "story_role": role,
        "images": [{"path": f"photos/c{index:02d}-{n + 1}.jpg"} for n in range(photos)],
    }


def _plan(chapters, **kwargs):
    return plan_module.build_scene_plan(
        trip={"title": "Finnland", "chapter_count": len(chapters)},
        chapters=chapters,
        **kwargs,
    )


def _chapter_frames(plan, index):
    return sum(
        scene["frames"] for scene in plan["scenes"] if scene["chapter_index"] == index
    )


def verify_importance_decides_how_long_a_day_lasts() -> None:
    """The monotony finding, expressed as a number.

    Film v0 gave every day the same length, so a transfer and the reason
    for the whole trip were indistinguishable on screen.

    The numbers moved when the media budget stopped being the time
    budget. A day is now worth its importance in seconds and fills them
    with whatever it has; before, each picture bought its own seconds, so
    these ranges were really measuring how many photographs the fixture
    happened to give each day.
    """
    chapters = [
        # Roughly what each importance is now given: 2/4/6/8.
        _chapter(0, importance="transition", photos=2),
        _chapter(1, importance="normal", photos=4),
        _chapter(2, importance="highlight", style="hero", photos=6),
        _chapter(3, importance="major_highlight", style="hero", photos=8),
    ]
    plan = _plan(chapters)
    seconds = [_chapter_frames(plan, index) / plan_module.FILM_FPS for index in range(4)]
    assert seconds[0] < seconds[1] < seconds[2] < seconds[3], seconds
    # Widened after the tempo pass. The reported complaint was that
    # everything changed too fast, so every floor went up: a photograph
    # holds 2.6 s instead of 1.7, a group 4.7 s instead of 3.2, and a
    # title card is paid for by how long its title takes to read. The
    # ordering by importance is what this check is really about; the
    # absolute seconds are a sanity band, not a target.
    assert 8 <= seconds[0] <= 14, seconds[0]
    assert 12 <= seconds[1] <= 20, seconds[1]
    assert 16 <= seconds[2] <= 26, seconds[2]
    assert 20 <= seconds[3] <= 34, seconds[3]


def verify_more_pictures_do_not_buy_more_minutes() -> None:
    """The separation of media budget from time budget, as a measurement.

    This is the whole point of the rewrite and the property the planned
    length control depends on. Doubling what a day shows must not double
    how long it runs: the pictures get shorter and more of them are
    grouped, which is how a day shows more memories inside the same
    minute.

    Without this, "a more detailed film" could only ever mean "the same
    film, slower", and the film it produced was the opposite complaint:
    day after day of announcement, one photograph, next day.
    """
    few = _plan([_chapter(0, importance="major_highlight", photos=3)])
    many = _plan([_chapter(0, importance="major_highlight", photos=9)])
    few_seconds = _chapter_frames(few, 0)
    many_seconds = _chapter_frames(many, 0)
    shown_few = sum(len(scene["photos"]) for scene in few["scenes"])
    shown_many = sum(len(scene["photos"]) for scene in many["scenes"])

    assert shown_many >= shown_few * 2, (shown_few, shown_many)
    # Three times the pictures, nowhere near three times the length.
    assert many_seconds < few_seconds * 1.7, (few_seconds, many_seconds)
    # And the extra memories are really on screen, not silently dropped.
    assert shown_many == 9, shown_many


def verify_a_thin_transfer_day_needs_no_card_of_its_own() -> None:
    """Twenty-three identical announcements are the monotony, not the cure.

    A transfer day's map already carries the day number, the date, the
    country and the title. A separate card in front of it is a second
    title for the same thing - so it is dropped when there is a map to
    carry it, and kept when there is not.
    """
    mapped = _plan(
        [_chapter(0, importance="transition", photos=2)],
        map_context={"chapters": [{"chapter_id": "day-1", "index": 0}]},
    )
    assert not [
        scene for scene in mapped["scenes"] if scene["type"] == plan_module.SCENE_CHAPTER_CARD
    ], "ein Tag mit Karte braucht keine zweite Titelkarte"
    assert [scene for scene in mapped["scenes"] if scene["type"] == plan_module.SCENE_MAP_LEG]

    # Without a map the card is the only thing that names the day.
    plain = _plan([_chapter(0, importance="transition", photos=2)])
    assert [
        scene for scene in plain["scenes"] if scene["type"] == plan_module.SCENE_CHAPTER_CARD
    ], "ohne Karte muss der Tag benannt bleiben"


def verify_a_thin_day_is_not_padded_to_look_important() -> None:
    """A highlight with one photo stays short. Inflating it would be a lie
    told with screen time."""
    rich = _plan([_chapter(0, importance="major_highlight", style="hero", photos=4)])
    thin = _plan([_chapter(0, importance="major_highlight", style="hero", photos=1)])
    assert _chapter_frames(thin, 0) < _chapter_frames(rich, 0)


def verify_visual_style_picks_only_scenes_that_exist() -> None:
    for style in ("compact", "normal", "hero", "collage", "map_focus", "erfunden"):
        plan = _plan([_chapter(0, style=style, photos=3)])
        types = {scene["type"] for scene in plan["scenes"]}
        assert types <= set(plan_module.SCENE_TYPES), (style, types)
    # And each real style produces a recognisably different shape.
    shapes = {
        style: [scene["type"] for scene in _plan([_chapter(0, style=style, photos=3)])["scenes"]]
        for style in ("compact", "normal", "hero", "collage")
    }
    # "compact" is now about restraint rather than about exactly one
    # picture: it opens on a single shot and keeps the rest brief. Pinning
    # it to one photograph was pinning the old model, where a picture and
    # a scene were the same thing.
    assert shapes["compact"].count("collage") == 0, shapes["compact"]
    assert shapes["compact"].count("hero") == 1, shapes["compact"]
    # A day with real material opens on one shot rather than starting
    # straight into a sequence, so three pictures are a hero and two
    # stills rather than three identical stills.
    assert shapes["normal"].count("hero") == 1, shapes["normal"]
    assert shapes["normal"].count("photo") == 2, shapes["normal"]
    assert sum(
        len(scene["photos"])
        for scene in _plan([_chapter(0, style="normal", photos=3)])["scenes"]
    ) == 3, "alle drei Bilder muessen vorkommen"
    assert "hero" in shapes["hero"], shapes["hero"]
    assert "collage" in shapes["collage"], shapes["collage"]


def verify_an_unknown_style_or_importance_falls_back_quietly() -> None:
    """A value the model invented must not be able to break a render."""
    plan = _plan([_chapter(0, style="kinoformat", importance="weltbewegend", role="prolog")])
    normal = _plan([_chapter(0)])
    assert [scene["type"] for scene in plan["scenes"]] == [
        scene["type"] for scene in normal["scenes"]
    ]
    assert _chapter_frames(plan, 0) == _chapter_frames(normal, 0)


def verify_map_focus_falls_back_visibly_rather_than_silently() -> None:
    """There is no map yet. The substitution is a hero image, on purpose."""
    plan = _plan([_chapter(0, style="map_focus", photos=3)])
    types = [scene["type"] for scene in plan["scenes"]]
    assert "hero" in types, types


def verify_a_day_without_photos_is_written_not_reported() -> None:
    """"Für diesen Tag gibt es keine Fotos" is a diagnostic. It reached a
    real film, and it must not be able to again."""
    plan = _plan([_chapter(0, photos=0, story="Ein ruhiger Tag am See.")])
    types = [scene["type"] for scene in plan["scenes"]]
    assert "text" in types, types
    assert "photo" not in types, types
    # Nothing anywhere in the plan talks about missing photographs.
    serialised = json.dumps(plan, ensure_ascii=False).casefold()
    for forbidden in ("keine fotos", "kein foto", "nicht vorhanden", "fehlt"):
        assert forbidden not in serialised, forbidden


def verify_story_role_changes_the_arrival_and_nothing_else() -> None:
    """Two sizing systems fighting over the same thing is unexplainable."""
    lengths = set()
    enters = set()
    for role in ("opening", "journey", "transition", "highlight", "finale"):
        plan = _plan([_chapter(0, role=role, photos=2)])
        lengths.add(_chapter_frames(plan, 0))
        enters.update(scene["enter"] for scene in plan["scenes"] if scene["chapter_index"] == 0)
    assert len(lengths) == 1, f"story_role darf keine Laenge veraendern: {lengths}"
    assert len(enters) >= 4, enters


def verify_the_intro_and_outro_use_the_arc_only_when_there_is_one() -> None:
    bare = _plan([_chapter(0)])
    assert bare["scenes"][0]["type"] == "intro"
    assert bare["scenes"][-1]["type"] == "outro"

    with_arc = _plan(
        [_chapter(0)],
        narrative={"opening": "Es begann mit einem verpassten Kaffee.", "closing": "Sand im Auto."},
    )
    # A sentence to read needs longer than a title.
    assert with_arc["scenes"][0]["frames"] > bare["scenes"][0]["frames"]
    assert with_arc["scenes"][-1]["frames"] > bare["scenes"][-1]["frames"]


def verify_the_film_ends_rather_than_stopping() -> None:
    chapters = [_chapter(index, photos=2) for index in range(6)]
    photos = [{"path": f"photos/c0{index}-1.jpg"} for index in range(4)]
    plan = _plan(chapters, outro_photos=photos)
    assert plan["scenes"][-1]["type"] == "outro_collage"
    assert len(plan["scenes"][-1]["paths"]) == 4
    # With fewer than two pictures there is no collage to make.
    assert _plan(chapters, outro_photos=photos[:1])["scenes"][-1]["type"] == "outro"


def verify_a_manifest_without_any_direction_still_plans() -> None:
    """Nobody has run the editor. The film must still be a film."""
    plain = [
        {
            "chapter_id": "day-1",
            "index": 0,
            "date": "2026-07-17",
            "title": "Tag 1",
            "story": "Ein zusammengesetzter Satz.",
            "images": [{"path": "photos/c00-1.jpg"}],
        }
    ]
    plan = plan_module.validate_scene_plan(_plan(plain))
    assert plan["total_frames"] > 0
    assert [scene["type"] for scene in plan["scenes"]][:2] == ["intro", "chapter_card"]


def verify_the_same_input_produces_the_same_plan() -> None:
    chapters = [
        _chapter(0, importance="transition", photos=1),
        _chapter(1, importance="major_highlight", style="collage", photos=4),
        _chapter(2, photos=0),
    ]
    first = json.dumps(_plan(chapters), sort_keys=True)
    second = json.dumps(_plan(chapters), sort_keys=True)
    assert first == second


def verify_the_photo_budget_is_weighted_not_flat() -> None:
    """A transfer day and a major highlight used to get the same three.

    The weights were raised (2/4/6/8) after a real film turned out to be
    "announcement, one photograph, next day" over and over while the
    library held hundreds of curated pictures. The fixture therefore has
    to offer enough material for the weights to be visible at all - with
    four photographs each, three of the four days are simply capped by
    what they have, which measures the fixture rather than the rule.
    """
    chapters = [
        {"chapter_id": "a", "importance": "transition", "media": list(range(16))},
        {"chapter_id": "b", "importance": "normal", "media": list(range(16))},
        {"chapter_id": "c", "importance": "highlight", "media": list(range(16))},
        {"chapter_id": "d", "importance": "major_highlight", "media": list(range(16))},
    ]
    budget = plan_module.allocate_photos(chapters, total_budget=140, per_chapter_cap=16)
    assert budget["a"] < budget["b"] < budget["c"] < budget["d"], budget
    assert budget == {
        "a": plan_module.PHOTO_WEIGHTS["transition"],
        "b": plan_module.PHOTO_WEIGHTS["normal"],
        "c": plan_module.PHOTO_WEIGHTS["highlight"],
        "d": plan_module.PHOTO_WEIGHTS["major_highlight"],
    }, budget
    # And the weights really are the richer ones the brief asked for.
    assert budget["d"] >= 8, budget


def verify_a_day_is_never_given_more_pictures_than_it_has() -> None:
    chapters = [{"chapter_id": "a", "importance": "major_highlight", "media": [1]}]
    assert plan_module.allocate_photos(chapters, total_budget=90, per_chapter_cap=4) == {"a": 1}


def verify_a_tight_budget_takes_from_the_least_important_first() -> None:
    chapters = [
        {"chapter_id": f"t{n}", "importance": "transition", "media": [1, 2]} for n in range(4)
    ] + [
        {"chapter_id": "big", "importance": "major_highlight", "media": [1, 2, 3, 4]},
    ]
    budget = plan_module.allocate_photos(chapters, total_budget=6, per_chapter_cap=4)
    assert sum(budget.values()) <= 6, budget
    assert budget["big"] >= max(budget[f"t{n}"] for n in range(4)), budget


def verify_the_whole_trip_stays_inside_the_photo_budget() -> None:
    chapters = [
        {"chapter_id": f"d{n}", "importance": "major_highlight", "media": [1, 2, 3, 4]}
        for n in range(45)
    ]
    budget = plan_module.allocate_photos(chapters, total_budget=90, per_chapter_cap=4)
    assert sum(budget.values()) <= 90, sum(budget.values())


def verify_a_technical_stop_name_does_not_reach_a_title_card() -> None:
    """The real string that reached a real film."""
    assert (
        plan_module.readable_place({"name": "park4night - (595 50) Mjölby - 24 Vetagatan"})
        == "Mjölby"
    )
    # A name that is already fine is left alone.
    assert plan_module.readable_place({"name": "Nuuksio Nationalpark"}) == "Nuuksio Nationalpark"
    # The editor's name wins outright.
    assert (
        plan_module.readable_place(
            {"name": "park4night - (595 50) Mjölby", "story_name": "unser Waldsee"}
        )
        == "unser Waldsee"
    )
    # A trailing house number says nothing on screen.
    assert plan_module.readable_place({"name": "Krumhermsdorf Neuhäuser 40"}) == (
        "Krumhermsdorf Neuhäuser"
    )
    assert plan_module.readable_place({"name": ""}) == ""


def verify_the_route_line_is_short_and_without_repeats() -> None:
    stops = [
        {"name": "Tampere"},
        {"name": "Tampere"},
        {"name": "park4night - Vaasa - 12 Hamngatan"},
        {"name": "Oulu"},
        {"name": "Rovaniemi"},
    ]
    assert plan_module.readable_places(stops, limit=3) == ["Tampere", "Vaasa", "Oulu"]


def verify_a_broken_plan_is_refused() -> None:
    good = _plan([_chapter(0)])
    cases = [
        "kein Objekt",
        {**good, "plan_version": 99},
        {**good, "fps": 25},
        {**good, "scenes": []},
        {**good, "total_frames": 1},
        {**good, "scenes": [{"type": "kinoformat", "frames": 30}]},
        {**good, "scenes": [{"type": "intro", "frames": 0}]},
    ]
    for case in cases:
        try:
            plan_module.validate_scene_plan(case)
        except plan_module.FilmPlanError:
            continue
        raise AssertionError(f"Ungueltiger Plan akzeptiert: {case!r:.60}")


verify_importance_decides_how_long_a_day_lasts()
verify_more_pictures_do_not_buy_more_minutes()
verify_a_thin_transfer_day_needs_no_card_of_its_own()
verify_a_thin_day_is_not_padded_to_look_important()
verify_visual_style_picks_only_scenes_that_exist()
verify_an_unknown_style_or_importance_falls_back_quietly()
verify_map_focus_falls_back_visibly_rather_than_silently()
verify_a_day_without_photos_is_written_not_reported()
verify_story_role_changes_the_arrival_and_nothing_else()
verify_the_intro_and_outro_use_the_arc_only_when_there_is_one()
verify_the_film_ends_rather_than_stopping()
verify_a_manifest_without_any_direction_still_plans()
verify_the_same_input_produces_the_same_plan()
verify_the_photo_budget_is_weighted_not_flat()
verify_a_day_is_never_given_more_pictures_than_it_has()
verify_a_tight_budget_takes_from_the_least_important_first()
verify_the_whole_trip_stays_inside_the_photo_budget()
verify_a_technical_stop_name_does_not_reach_a_title_card()
verify_the_route_line_is_short_and_without_repeats()
verify_a_broken_plan_is_refused()
def verify_a_text_is_shown_long_enough_to_read_it() -> None:
    """The reported complaint, as arithmetic.

    "Das gesamte Tempo ist zu hoch: Bilder, Text und Karte wechseln zu
    schnell." A text that changes before it is finished is the worst
    case of that, because the viewer knows exactly what they missed.

    So a text's duration comes from the text, at a deliberately
    unhurried German on-screen reading pace, with a floor for noticing
    it and a ceiling past which a still frame stops being a scene.
    """
    short = plan_module.reading_frames("Erster Halt am See.")
    long = plan_module.reading_frames(
        "Nach unserem Start bogen wir ab zur Älgsafari, wo uns mächtige "
        "Elche und Bisons direkt auf dem Weg begegneten."
    )
    assert short < long, (short, long)
    assert short >= plan_module.READING_MIN_FRAMES
    assert long <= plan_module.READING_MAX_FRAMES
    # Roughly four seconds for a sentence of that length, not two.
    assert long / plan_module.FILM_FPS >= 4.0, long / plan_module.FILM_FPS
    assert plan_module.reading_frames("") == 0
    assert plan_module.reading_frames("   ") == 0


def verify_a_long_caption_gets_its_own_scene() -> None:
    """The other half of "Text läuft unten aus dem Bild".

    A paragraph over a photograph needs a scrim across half the frame to
    stay legible, and then the photograph is gone anyway. The fix for
    text that does not fit is not a smaller font - it is a different
    scene.
    """
    long_caption = (
        "Nach unserem Start bogen wir ab zur Älgsafari, wo uns mächtige Elche "
        "und Bisons direkt auf dem Weg begegneten. Am Abend machten wir es uns "
        "am Lagerfeuer gemütlich."
    )
    assert len(long_caption) > plan_module.CAPTION_OVER_PHOTO_CHARS
    # A package chapter carries its final text in `story` - the choice
    # between caption and story was made when the package was built.
    chapter = _chapter(0, importance="normal", photos=4, story=long_caption)
    plan = _plan([chapter])
    kinds = [scene["type"] for scene in plan["scenes"]]
    assert plan_module.SCENE_TEXT in kinds, kinds
    text_scene = next(s for s in plan["scenes"] if s["type"] == plan_module.SCENE_TEXT)
    assert text_scene["frames"] >= plan_module.reading_frames(long_caption)
    # And the pictures are still there - the text did not replace them.
    assert any(s["type"] in {plan_module.SCENE_PHOTO, plan_module.SCENE_HERO,
                             plan_module.SCENE_COLLAGE} for s in plan["scenes"]), kinds

    # A short caption stays over the photograph, where it belongs.
    brief = _chapter(1, importance="normal", photos=4, story="Erster Halt am See.")
    kinds = [scene["type"] for scene in _plan([brief])["scenes"]]
    assert plan_module.SCENE_TEXT not in kinds, kinds


verify_a_text_is_shown_long_enough_to_read_it()
verify_a_long_caption_gets_its_own_scene()
def verify_an_important_day_is_never_worn_down_to_one_picture() -> None:
    """The Wolfsschanze case, generically.

    Reported from the abnahme: an important place was "visuell zu
    schwach vertreten" although matching photographs existed. The
    reduction takes from the least important days first, which is right,
    but without a floor it kept going - a long trip can wear a highlight
    down to a single picture, and one picture of a place that mattered
    is the same as none.

    Nothing here knows about the Wolfsschanze. What it knows is that
    importance plus available material has to survive a tight budget.
    """
    # `allocate_photos` reads `media` - the manifest shape - rather than
    # the package's `images`.
    def day(number, importance, photos):
        return {
            "chapter_id": f"day-{number}",
            "importance": importance,
            "media": list(range(photos)),
        }

    chapters = [day(index + 1, "transition", 6) for index in range(20)]
    chapters.append(day(21, "major_highlight", 8))
    chapters.append(day(22, "highlight", 8))
    # A budget far below what everybody wants, so the reduction really bites.
    budget = plan_module.allocate_photos(chapters, total_budget=40, per_chapter_cap=14)
    assert sum(budget.values()) <= 40, budget
    assert budget["day-21"] >= 5, ("major highlight", budget["day-21"])
    assert budget["day-22"] >= 4, ("highlight", budget["day-22"])
    # The transfer days gave way first, which is the intended order.
    assert budget["day-1"] < budget["day-22"], budget

    # The floor never invents pictures a day does not have.
    thin = [
        day(1, "major_highlight", 2),
        *[day(index + 1, "normal", 6) for index in range(1, 12)],
    ]
    budget = plan_module.allocate_photos(thin, total_budget=20, per_chapter_cap=14)
    assert budget["day-1"] == 2, budget["day-1"]


verify_an_important_day_is_never_worn_down_to_one_picture()
print("Trip film plan tests passed.")

def verify_no_curated_picture_is_ever_dropped() -> None:
    """A photograph somebody kept must reach the screen, or nothing works.

    This failed twice in one evening, both times silently. The map-focus
    shape took the first four of what followed its hero and discarded the
    rest - under a comment saying "rather than thrown away". The collage
    component did the same at the other end, showing four of however many
    the plan gave it.

    Both were invisible: the plan was valid, the film rendered, and the
    only trace was a count nobody was looking at. So the count is looked
    at here, for every style and every plausible number of pictures - the
    cheapest possible check for the most expensive possible failure.
    """
    for style in ("normal", "compact", "hero", "collage", "map_focus", "erfunden"):
        for count in range(0, 11):
            chapter = _chapter(0, style=style, photos=count)
            for mapped in (False, True):
                plan = _plan(
                    [chapter],
                    map_context=(
                        {"chapters": [{"chapter_id": "day-1", "index": 0}]}
                        if mapped
                        else None
                    ),
                )
                shown = [
                    position
                    for scene in plan["scenes"]
                    if scene.get("chapter_index") == 0
                    for position in scene["photos"]
                ]
                assert sorted(shown) == list(range(count)), (
                    style,
                    count,
                    mapped,
                    sorted(shown),
                )


verify_no_curated_picture_is_ever_dropped()


def verify_a_long_sentence_never_lands_on_a_collage() -> None:
    """A collage is four pictures at a quarter of the frame each.

    Whatever room is left for a sentence is a quarter of what a single
    photograph offers, so text that reads comfortably over one picture
    comes out too small over four - reported from the acceptance of
    reise(8). A collage is primarily something to look at; anything
    longer than a short caption gets a scene of its own, with the whole
    frame, before the pictures.
    """
    chapter = {
        "chapter_id": "d1",
        "importance": "highlight",
        "visual_style": "collage",
        "media": [{"media_id": f"m{index}"} for index in range(8)],
        "images": [""] * 8,
        "story": (
            "Am Morgen ging es durch den Nationalpark, mittags standen wir "
            "am Wasserfall und abends fanden wir einen Platz direkt am See."
        ),
    }
    scenes = plan_module._chapter_scenes(chapter, photo_count=8, index=0)
    kinds = [scene["type"] for scene in scenes]
    assert plan_module.SCENE_COLLAGE in kinds, kinds
    assert plan_module.SCENE_TEXT in kinds, kinds
    assert kinds.index(plan_module.SCENE_TEXT) < kinds.index(plan_module.SCENE_COLLAGE)
    # A short caption still rides along on the pictures - the rule is
    # about long text, not about forbidding words near a collage.
    short = plan_module._chapter_scenes(
        {**chapter, "story": "Am See."}, photo_count=8, index=0
    )
    assert plan_module.SCENE_TEXT not in [scene["type"] for scene in short]


verify_a_long_sentence_never_lands_on_a_collage()
