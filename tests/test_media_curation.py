"""The moose that was never in the film, as a set of checks.

Reported live: the day at the moose park came out of the film without a
moose, and the photographs were there the whole time. Four things went
wrong together, and each is a check here.

The point of writing them generically is that "Elchpark" must not become
a special case. Nothing in the module knows about moose; it knows that
German glues a generic tail onto a specific head, that a run of
photographs is one moment rather than one photograph, and that a day is
about whatever its stops are called. A wildlife park, a ferry, a
waterfall and a cathedral all come out of the same three facts, which is
why they are all tested here.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_curation_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

curation = importlib.import_module(f"{_PACKAGE}.media_curation")
vision = importlib.import_module(f"{_PACKAGE}.media_curation_vision")


def _photo(media_id: str, *, seconds: int, lat: float = 63.4, lon: float = 18.4, **extra):
    base = {
        "id": media_id,
        "media_type": "photo",
        "name": f"{media_id}.jpg",
        "taken_at": f"2026-07-21T14:{seconds // 60:02d}:{seconds % 60:02d}+02:00",
        "location": {"latitude": lat, "longitude": lon},
        "width": 4032,
        "height": 3024,
        "size_bytes": 3_000_000,
    }
    base.update(extra)
    return base


def _analysis(*, story: int, quality: int, motifs: list[str]):
    return {"story_value": story, "visual_quality": quality, "motifs": motifs}


def verify_a_series_keeps_every_member() -> None:
    """The first of the four mistakes, and the one that ate the moose.

    Six shutter presses over eleven seconds are ONE moment and SIX
    photographs. The old burst logic collapsed that to one before
    anything had looked at any of them - which meant the best moose
    picture had a one-in-six chance of surviving on file size.
    """
    burst = [_photo(f"moose-{index}", seconds=index * 2) for index in range(6)]
    series = curation.group_series(burst)
    assert len(series) == 1, series
    assert series[0]["size"] == 6, series
    assert series[0]["media_ids"] == [f"moose-{index}" for index in range(6)]

    # Chained rather than windowed: the first and last are eleven seconds
    # apart, which no fixed four-second window would have joined.
    assert len(burst) == 6


def verify_a_separate_moment_is_a_separate_series() -> None:
    """Grouping is still grouping - the lunch photo is not the moose."""
    items = [
        _photo("moose-1", seconds=0),
        _photo("moose-2", seconds=3),
        _photo("lunch", seconds=1_800),
        _photo("far-away", seconds=1_803, lat=63.9),
    ]
    series = curation.group_series(items)
    assert [record["size"] for record in series] == [2, 1, 1], series


def verify_the_pool_is_wider_than_the_film() -> None:
    """A day with eighty photographs must offer far more than twelve.

    The old pool was a fixed twelve and it was the *same* twelve the
    local score had already chosen, so the semantic stage could only
    reorder what the content-blind stage had picked.
    """
    assert curation.pool_size(photo_count=80, series_count=14, stop_count=3) >= 15
    assert curation.pool_size(photo_count=80, series_count=14, stop_count=3) <= 30
    # A small day offers everything it has rather than a fraction of it.
    assert curation.pool_size(photo_count=6, series_count=3, stop_count=2) == 6
    assert curation.pool_size(photo_count=0, series_count=0, stop_count=1) == 0


def verify_every_moment_reaches_the_pool() -> None:
    """One long series must not crowd out the rest of the day.

    Sixteen photographs of one animal and one photograph each of four
    other moments: the pool has to contain all four of those, or the day
    is a wildlife documentary by accident.
    """
    animals = [_photo(f"moose-{index}", seconds=index * 2) for index in range(16)]
    others = [
        _photo("hut", seconds=3_600),
        _photo("lake", seconds=7_200),
        _photo("road", seconds=10_800),
        _photo("camp", seconds=14_400),
    ]
    items = animals + others
    series = curation.group_series(items)
    pool = curation.candidate_pool(items, series=series, stop_count=3)
    chosen = set(pool["media_ids"])
    for media_id in ("hut", "lake", "road", "camp"):
        assert media_id in chosen, (media_id, pool)
    # And the animal is represented by several attempts, not one.
    assert len([value for value in chosen if value.startswith("moose")]) >= 2, pool


def verify_only_broken_files_are_thrown_away_locally() -> None:
    """Conservative, because this stage cannot see the picture."""
    assert curation.technical_verdict(_photo("ok", seconds=0))["usable"]
    # A small old photograph is a memory, not a defect.
    assert curation.technical_verdict(
        _photo("old", seconds=0, width=640, height=480)
    )["usable"]
    for item, reason in (
        (_photo("shot", seconds=0, name="Screenshot_2026.png"), curation.REJECT_SCREENSHOT),
        (_photo("icon", seconds=0, width=64, height=64), curation.REJECT_TINY),
        (_photo("strip", seconds=0, width=12_000, height=800), curation.REJECT_EXTREME_RATIO),
        (_photo("stub", seconds=0, size_bytes=900), curation.REJECT_BROKEN),
    ):
        verdict = curation.technical_verdict(item)
        assert not verdict["usable"], item["id"]
        assert verdict["reason"] == reason, verdict


def verify_a_day_knows_what_it_is_about() -> None:
    """The fourth mistake: nothing knew a moose was expected.

    Derived from the roadbook - the stop is called "Elchpark" - and not
    from a model. The compound split is a fact about German, not about
    this trip, which is why the same code answers a waterfall and a
    cathedral.
    """
    cases = {
        "Elchpark Smaland": "elch",
        "Storforsen Wasserfall": "wasser",
        "Nidarosdom Trondheim": "nidaros",
        "Rentiergehege Inari": "rentier",
    }
    for name, expected in cases.items():
        brief = curation.visual_brief({"title": "", "stops": [{"name": name}]})
        assert len(brief["must_cover"]) == 1, (name, brief)
        # One name is one requirement, and any of its words answers it.
        # The label is the first specific word; the word a photograph can
        # actually show is often a different one - "Storforsen" is a name
        # no picture can confirm, "Wasserfall" is what is in the frame.
        alternatives = brief["alternatives"][brief["must_cover"][0]]
        assert expected in alternatives, (name, brief)

    # A category is worth showing but never required: a day is not
    # incomplete because nobody photographed the car park.
    brief = curation.visual_brief(
        {"title": "Etappe 12", "stops": [{"name": "Parkplatz"}, {"name": "Tankstelle"}]}
    )
    assert brief["must_cover"] == [], brief


def verify_the_story_answers_what_a_place_name_cannot() -> None:
    """"Smålandet Markaryds Älgsafari" gives a requirement of proper names.

    No photograph confirms a proper name. The story of that same day
    says "mächtige Elche und Bisons" - the words somebody would actually
    use about the picture. So story words never create a requirement
    (that is the title problem again, one paragraph longer) and always
    help answer one.

    Only the capitalised words, because that is how German marks a noun:
    the same paragraph otherwise contributes "unserem" and "bogen".
    """
    chapter = {
        "title": "Begegnung mit den Riesen des Waldes",
        "story": (
            "Nach unserem Start bogen wir ab zur Smålandet Markaryds Älgsafari, "
            "wo uns mächtige Elche und Bisons direkt auf dem Weg begegneten."
        ),
        "stops": [{"name": "Smålandet Markaryds Älgsafari", "kind": "sight"}],
    }
    brief = curation.visual_brief(chapter)
    assert brief["must_cover"] == ["smalandet"], brief
    assert "elche" in brief["story_motifs"], brief
    for verb_or_pronoun in ("unserem", "bogen", "machtige"):
        assert verb_or_pronoun not in brief["story_motifs"], brief

    photo = {"p1": _analysis(story=5, quality=4, motifs=["Elch", "Bison"])}
    assert curation.coverage(brief, photo, ["p1"])["complete"], brief


def verify_a_category_word_is_never_evidence() -> None:
    """A photograph of a Parkplatz satisfied a day about an Elchpark.

    "Elchpark" splits into "elch" + "park", and both were treated as
    ways to answer the requirement. But "park" matches "Parkplatz",
    "Nationalpark" and half a camera roll - a category word is not
    evidence of a subject, and letting it count put the day's coverage
    at green while the moose was still missing.
    """
    brief = curation.visual_brief({"title": "", "stops": [{"name": "Elchpark Smaland"}]})
    assert brief["alternatives"]["elch"] == ["elch", "smaland"], brief
    analyses = {"carpark": _analysis(story=5, quality=5, motifs=["Parkplatz"])}
    assert curation.coverage(brief, analyses, ["carpark"])["unmet"] == ["elch"]


def verify_a_nordic_name_survives_being_read() -> None:
    """Reported from the real trip: "Smålandet" became "landet".

    The word was split on non-ASCII letters before it was folded, so
    every ring, slash and accent was a word boundary. On a journey
    through Sweden and Norway that is most of the map - and the day at
    the moose safari ended up requiring a photograph of "landet".
    """
    assert curation.motif_tokens("Smålandet Markaryds Älgsafari") == [
        "smalandet",
        "markaryds",
        "algsafari",
    ]
    assert curation.motif_tokens("Tromsø Ålesund Ærø") == ["tromso", "alesund", "aero"]
    brief = curation.visual_brief(
        {"title": "", "stops": [{"name": "Smålandet Markaryds Älgsafari"}]}
    )
    assert brief["must_cover"] == ["smalandet"], brief
    assert "algsafari" in brief["alternatives"]["smalandet"], brief


def verify_a_chapter_title_is_prose_not_a_place() -> None:
    """"Begegnung mit den Riesen des Waldes" is a heading, not a subject.

    It is also a real one, from the moose-safari day. Nobody photographs
    a Begegnung, so requiring it would leave that day permanently
    incomplete for a reason that says nothing about the pictures. What a
    day must be seen to contain comes from where it went - the part that
    was measured rather than written.
    """
    brief = curation.visual_brief(
        {
            "title": "Begegnung mit den Riesen des Waldes",
            "stops": [{"name": "Asmoarp Natur"}],
        }
    )
    assert "begegnung" not in brief["must_cover"], brief
    assert brief["must_cover"] == ["asmoarp"], brief
    assert "begegnung" in brief["nice_to_cover"], brief


def verify_a_motif_answers_in_the_words_a_model_would_use() -> None:
    """"Elch", "Elche" and "Elchgehege" all answer a day about "elch"."""
    for seen in ("Elch", "Elche", "Elchgehege", "elch im gehege"):
        assert curation.motif_matches("elch", [seen]), seen
    assert not curation.motif_matches("elch", ["Wasserfall", "Camper"])
    # Too short to mean anything is never a match: "see" must not answer
    # every word that happens to contain it.
    assert not curation.motif_matches("see", ["Seehund"])


def verify_the_required_motif_is_chosen_before_a_prettier_picture() -> None:
    """The whole point, stated as one assertion.

    The moose photograph is technically the weaker picture. The day is
    about a moose. It goes in.
    """
    brief = curation.visual_brief({"title": "", "stops": [{"name": "Elchpark"}]})
    analyses = {
        "trees-1": _analysis(story=4, quality=5, motifs=["Wald", "Bäume"]),
        "trees-2": _analysis(story=4, quality=5, motifs=["Wald", "Licht"]),
        "moose-1": _analysis(story=3, quality=3, motifs=["Elch"]),
    }
    result = curation.select_for_chapter(
        candidates=list(analyses),
        analyses=analyses,
        brief=brief,
        target=2,
    )
    assert "moose-1" in result["media_ids"], result
    assert result["coverage"]["complete"], result
    assert result["reasons"]["moose-1"] == "zeigt elch"


def verify_a_day_without_the_picture_is_honest_about_it() -> None:
    """Coverage may report a gap. It may never invent one being filled.

    "Nobody photographed that" is a real answer, and the day is still the
    day. What must not happen is a quiet claim that it was covered.
    """
    brief = curation.visual_brief({"title": "", "stops": [{"name": "Elchpark"}]})
    analyses = {"road": _analysis(story=3, quality=4, motifs=["Straße", "Wald"])}
    result = curation.select_for_chapter(
        candidates=["road"], analyses=analyses, brief=brief, target=3
    )
    assert result["media_ids"] == ["road"], result
    assert result["coverage"]["unmet"] == ["elch"], result
    assert result["coverage"]["complete"] is False


def verify_the_final_set_spreads_across_moments() -> None:
    """Not five nearly identical photographs, however well each scores."""
    analyses = {
        f"moose-{index}": _analysis(story=5, quality=5, motifs=["Elch"])
        for index in range(5)
    }
    analyses["hut"] = _analysis(story=3, quality=3, motifs=["Hütte"])
    analyses["lake"] = _analysis(story=3, quality=3, motifs=["See"])
    series_by_media = {f"moose-{index}": "s000" for index in range(5)}
    series_by_media.update({"hut": "s001", "lake": "s002"})
    result = curation.select_for_chapter(
        candidates=list(analyses),
        analyses=analyses,
        brief={"must_cover": [], "nice_to_cover": []},
        series_by_media=series_by_media,
        target=4,
    )
    chosen = result["media_ids"]
    assert "hut" in chosen and "lake" in chosen, chosen
    assert len([value for value in chosen if value.startswith("moose")]) <= 2, chosen


def verify_one_animal_encounter_may_still_be_the_whole_day() -> None:
    """No rigid scheme: if the day IS the moose, show several of it."""
    analyses = {
        f"moose-{index}": _analysis(story=5, quality=5, motifs=["Elch"])
        for index in range(6)
    }
    series_by_media = {media_id: "s000" for media_id in analyses}
    result = curation.select_for_chapter(
        candidates=list(analyses),
        analyses=analyses,
        brief=curation.visual_brief({"title": "", "stops": [{"name": "Elchpark"}]}),
        series_by_media=series_by_media,
        target=4,
    )
    assert len(result["media_ids"]) == 4, result


def verify_the_person_outranks_every_rule() -> None:
    """A pin survives, an exclusion stays gone, and neither costs a call."""
    analyses = {
        "weak": _analysis(story=1, quality=1, motifs=["Parkplatz"]),
        "strong": _analysis(story=5, quality=5, motifs=["Elch"]),
        "banned": _analysis(story=5, quality=5, motifs=["Elch"]),
    }
    result = curation.select_for_chapter(
        candidates=list(analyses),
        analyses=analyses,
        brief=curation.visual_brief({"title": "", "stops": [{"name": "Elchpark"}]}),
        target=2,
        pinned=["weak"],
        excluded=["banned"],
    )
    assert result["media_ids"][0] == "weak", result
    assert "banned" not in result["media_ids"], result

    # And an excluded photograph never even reaches the paid stage.
    items = [_photo("weak", seconds=0), _photo("banned", seconds=5)]
    pool = curation.candidate_pool(
        items, series=curation.group_series(items), excluded=["banned"]
    )
    assert "banned" not in pool["media_ids"], pool
    assert any(entry["media_id"] == "banned" for entry in pool["rejected"])


def verify_every_dropped_photo_can_say_why() -> None:
    """A count nobody can account for is worse than no count."""
    items = [
        _photo("good", seconds=0),
        _photo("shot", seconds=5, name="Screenshot.png"),
        _photo("copy-a", seconds=10, file_hash="abc"),
        _photo("copy-b", seconds=40, file_hash="abc"),
    ]
    pool = curation.candidate_pool(items, series=curation.group_series(items))
    reasons = {entry["media_id"]: entry["reason"] for entry in pool["rejected"]}
    assert reasons["shot"] == curation.REJECT_SCREENSHOT
    assert len([value for value in ("copy-a", "copy-b") if value in reasons]) == 1
    assert all(entry.get("reason") for entry in pool["rejected"])


def verify_the_model_answers_about_the_picture_and_nothing_else() -> None:
    """Nouns, not a caption - and nothing about the journey.

    The old contract asked "pick the best few" and got back an order.
    An order cannot answer "does any of these show a moose?", which is
    why the coverage question had nowhere to look.
    """
    assert "motive" in vision.SYSTEM_PROMPT
    assert "Erfinde nichts" in vision.SYSTEM_PROMPT
    assert "Reisetagebuch" in vision.SYSTEM_PROMPT
    assert "Identifiziere keine Personen" in vision.SYSTEM_PROMPT
    blob = repr(vision.build_prompt(7))
    for leak in ("day-", "2026-", "Elch"):
        assert leak not in blob, leak


def verify_an_invented_id_is_discarded_not_trusted() -> None:
    """The one way a curation could point at a photograph that is not there."""
    parsed = vision.parse_analyses(
        {
            "photos": [
                {"image_id": "real", "motifs": ["Elch", "Elch", "Wald"], "visual_quality": 4, "story_value": 9},
                {"image_id": "invented", "motifs": ["Gold"], "visual_quality": 5, "story_value": 5},
            ]
        },
        known_ids={"real"},
    )
    assert set(parsed) == {"real"}, parsed
    assert parsed["real"]["motifs"] == ["Elch", "Wald"], "Dubletten fallen weg"
    assert parsed["real"]["story_value"] == 5, "ausserhalb der Skala wird begrenzt"
    assert vision.parse_analyses("nope", known_ids={"real"}) == {}


def verify_a_batch_is_never_a_sample_of_one() -> None:
    """Uniqueness cannot be judged against nothing.

    25 photographs split as 24+1 asks the second call to rate how
    different one picture is from the others it was shown - and it was
    shown none.
    """
    split = vision.batches([str(index) for index in range(25)], size=24)
    assert len(split) == 2, split
    assert min(len(part) for part in split) >= 10, split
    assert sum(len(part) for part in split) == 25
    assert vision.batches([]) == []


def verify_an_unchanged_photo_is_never_paid_for_twice() -> None:
    prints = {"a": "hash-a", "b": "hash-b"}
    first = vision.analysis_key(["a", "b"], prints)
    assert first == vision.analysis_key(["b", "a"], prints), "Reihenfolge ist keine Frage"
    assert first != vision.analysis_key(["a", "b"], {**prints, "a": "changed"})
    assert first != vision.analysis_key(["a"], prints)


def verify_the_same_principle_answers_a_ferry_and_a_waterfall() -> None:
    """The generic case, on the days the brief named.

    None of these is a rule about ferries or waterfalls. Each is the same
    three facts - the stop has a name, the name has a subject, a picture
    either shows it or does not - which is the whole reason "Elchpark"
    must not become a special case.
    """
    cases = (
        ("Fähre Trelleborg → Rostock", "Fähre", "Schiff auf See"),
        ("Storforsen Wasserfall", "Wasserfall", "Fluss"),
        ("Nidarosdom", "Nidarosdom", "Häuserzeile"),
        ("Rentiergehege Inari", "Rentier", "Wald"),

    )
    for stop_name, shown, other in cases:
        brief = curation.visual_brief({"title": "", "stops": [{"name": stop_name}]})
        assert len(brief["must_cover"]) == 1, (stop_name, brief)
        analyses = {
            "right": _analysis(story=2, quality=2, motifs=[shown]),
            "pretty": _analysis(story=5, quality=5, motifs=[other]),
        }
        result = curation.select_for_chapter(
            candidates=["pretty", "right"], analyses=analyses, brief=brief, target=1
        )
        assert result["media_ids"] == ["right"], (stop_name, brief, result)


def verify_a_subject_survives_a_language_boundary() -> None:
    """"Santa Claus Village" is not what anybody calls the picture.

    Textual matching between a place name and a model's German answer
    fails, and a translation table would grow forever. So the day's
    terms are asked about directly - "is any of this visible?" - and the
    answer comes back as `shows`. The prompt says outright that being
    asked is not evidence, and only terms that were actually asked about
    are kept.
    """
    brief = curation.visual_brief({"title": "", "stops": [{"name": "Santa Claus Village"}]})
    assert "santa" in brief["must_cover"], brief
    analyses = {
        "sign": {
            "story_value": 2,
            "visual_quality": 2,
            "motifs": ["Weihnachtsmann", "Schnee"],
            "shows": ["santa"],
        },
        "pretty": _analysis(story=5, quality=5, motifs=["Wald"]),
    }
    result = curation.select_for_chapter(
        candidates=["pretty", "sign"], analyses=analyses, brief=brief, target=1
    )
    assert result["media_ids"] == ["sign"], result
    assert result["coverage"]["complete"], result

    # And a term the model was never given is not an answer.
    parsed = vision.parse_analyses(
        {"photos": [{"image_id": "sign", "motifs": [], "visual_quality": 3,
                     "story_value": 3, "shows": ["santa", "elch"]}]},
        known_ids={"sign"},
        checks=["santa"],
    )
    assert parsed["sign"]["shows"] == ["santa"], parsed


def verify_a_day_with_nothing_to_photograph_is_not_a_failure() -> None:
    """A transfer day has no subject, so nothing can be missing from it."""
    brief = curation.visual_brief(
        {"title": "Etappe 9", "stops": [{"name": "Rastplatz"}, {"name": "Tankstelle"}]}
    )
    analyses = {"road": _analysis(story=2, quality=4, motifs=["Straße"])}
    result = curation.select_for_chapter(
        candidates=["road"], analyses=analyses, brief=brief, target=3
    )
    assert result["coverage"]["complete"], result
    assert result["media_ids"] == ["road"]


for check in (
    verify_a_series_keeps_every_member,
    verify_a_separate_moment_is_a_separate_series,
    verify_the_pool_is_wider_than_the_film,
    verify_every_moment_reaches_the_pool,
    verify_only_broken_files_are_thrown_away_locally,
    verify_a_day_knows_what_it_is_about,
    verify_a_nordic_name_survives_being_read,
    verify_a_chapter_title_is_prose_not_a_place,
    verify_the_story_answers_what_a_place_name_cannot,
    verify_a_category_word_is_never_evidence,
    verify_a_motif_answers_in_the_words_a_model_would_use,
    verify_the_required_motif_is_chosen_before_a_prettier_picture,
    verify_a_day_without_the_picture_is_honest_about_it,
    verify_the_final_set_spreads_across_moments,
    verify_one_animal_encounter_may_still_be_the_whole_day,
    verify_the_person_outranks_every_rule,
    verify_every_dropped_photo_can_say_why,
    verify_the_model_answers_about_the_picture_and_nothing_else,
    verify_an_invented_id_is_discarded_not_trusted,
    verify_a_batch_is_never_a_sample_of_one,
    verify_an_unchanged_photo_is_never_paid_for_twice,
    verify_the_same_principle_answers_a_ferry_and_a_waterfall,
    verify_a_subject_survives_a_language_boundary,
    verify_a_day_with_nothing_to_photograph_is_not_a_failure,
):
    check()

print("Media curation tests passed.")
