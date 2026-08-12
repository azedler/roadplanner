"""What a model may decide about the music, and what it may not.

Letting a model place the section boundaries is worth doing: the
arithmetic planner puts them where the division falls, not where the
journey turns. But a proposal is a proposal. Everything that decides
whether the film ends up with silence in it, or with a bill for six
generations instead of three, is checked here rather than trusted.

The rule that matters most is the one this area already broke once: a
section longer than a single generation plays its track and then goes
quiet. It broke that way through arithmetic; it can break the same way
through a model returning a plausible number. Same silence, new door.

Nothing is repaired. A proposal nudged into shape is a plan nobody
chose, and the deterministic plan is a better answer than a guess about
what the model meant.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"

_pkg = types.ModuleType("roadplanner_director_pkg")
_pkg.__path__ = [str(PACKAGE_ROOT)]
sys.modules["roadplanner_director_pkg"] = _pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_director_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("trip_film_plan")
cue_module = load("music_cue_sheet")
director = load("music_plan_director")

TRACK = 180.0


def _sheet():
    scenes = [
        {"type": "intro", "chapter_id": "", "frames": 150},
        {"type": "map_start", "chapter_id": "", "frames": 120},
    ]
    chapters = []
    for day in range(8):
        chapter_id = f"d{day}"
        chapters.append(
            {
                "chapter_id": chapter_id,
                "story_role": "journey",
                "importance": "normal",
                "day_number": day + 1,
            }
        )
        scenes.append({"type": "chapter_card", "chapter_id": chapter_id, "frames": 90})
        scenes.append({"type": "map_leg", "chapter_id": chapter_id, "frames": 200})
        for _ in range(4):
            scenes.append({"type": "collage", "chapter_id": chapter_id, "frames": 140})
    scenes.append({"type": "outro", "chapter_id": "", "frames": 150})
    plan = {
        "plan_version": 1,
        "fps": 30,
        "total_frames": sum(int(s["frames"]) for s in scenes),
        "scenes": scenes,
    }
    return cue_module.build_cue_sheet(plan, chapters=chapters)


GOOD = {
    "sections": [
        {"starts_at_cue": 0, "label": "Aufbruch", "mood": "neugierig"},
        {"starts_at_cue": 4, "label": "Norden", "mood": "weit"},
        {"starts_at_cue": 7, "label": "Ankommen", "mood": "ruhig"},
    ]
}


def verify_a_usable_proposal_covers_the_film_exactly() -> None:
    sheet = _sheet()
    sections = director.validate_proposal(GOOD, sheet, track_seconds=TRACK)
    assert sections[0]["start_seconds"] == 0.0
    assert abs(sections[-1]["end_seconds"] - sheet["film_seconds"]) < 0.05
    for earlier, later in zip(sections, sections[1:]):
        assert abs(later["start_seconds"] - earlier["end_seconds"]) < 0.05


def verify_a_section_longer_than_one_generation_is_refused() -> None:
    """The failure this area already had, arriving through a model.

    Arithmetic produced it first: sections of 186 s asking for music from
    a call that returns 118, so each played its track and then went
    quiet - four and a half minutes of silence in a twelve-minute film.
    A model returning one section for a four-minute film produces the
    same silence, and would arrive after somebody had paid for it.
    """
    sheet = _sheet()
    try:
        director.validate_proposal(
            {"sections": [{"starts_at_cue": 0, "label": "a", "mood": "m"}]},
            sheet,
            track_seconds=TRACK,
        )
    except director.PlanDirectorError as err:
        assert "still" in str(err), err
        return
    raise AssertionError("ein zu langer Abschnitt haette abgelehnt werden muessen")


def verify_the_music_must_start_at_the_start() -> None:
    sheet = _sheet()
    try:
        director.validate_proposal(
            {
                "sections": [
                    {"starts_at_cue": 2, "label": "a", "mood": "m"},
                    {"starts_at_cue": 5, "label": "b", "mood": "m"},
                ]
            },
            sheet,
            track_seconds=TRACK,
        )
    except director.PlanDirectorError as err:
        assert "Anfang" in str(err), err
        return
    raise AssertionError("ein stiller Filmanfang haette abgelehnt werden muessen")


def verify_nonsense_is_refused_rather_than_repaired() -> None:
    """Every shape a model can get wrong, and none of them patched."""
    sheet = _sheet()
    broken = {
        "unsortiert": [
            {"starts_at_cue": 0, "label": "a", "mood": "m"},
            {"starts_at_cue": 6, "label": "b", "mood": "m"},
            {"starts_at_cue": 3, "label": "c", "mood": "m"},
        ],
        "doppelt": [
            {"starts_at_cue": 0, "label": "a", "mood": "m"},
            {"starts_at_cue": 0, "label": "b", "mood": "m"},
        ],
        "cue gibt es nicht": [
            {"starts_at_cue": 0, "label": "a", "mood": "m"},
            {"starts_at_cue": 99, "label": "b", "mood": "m"},
        ],
        "kein index": [{"starts_at_cue": "vier", "label": "a", "mood": "m"}],
        "bool statt zahl": [{"starts_at_cue": True, "label": "a", "mood": "m"}],
        "kein objekt": ["nein"],
        "leer": [],
    }
    for name, sections in broken.items():
        try:
            director.validate_proposal(
                {"sections": sections}, sheet, track_seconds=TRACK
            )
        except director.PlanDirectorError:
            continue
        raise AssertionError(f"{name} wurde akzeptiert")
    for shape in (None, [], "sections", {"sections": None}):
        try:
            director.validate_proposal(shape, sheet, track_seconds=TRACK)
        except director.PlanDirectorError:
            continue
        raise AssertionError(f"{shape!r} wurde akzeptiert")


def verify_a_soundtrack_does_not_become_a_playlist() -> None:
    """§33: every section is also a charge."""
    sheet = _sheet()
    too_many = {
        "sections": [
            {"starts_at_cue": index, "label": str(index), "mood": "m"}
            for index in range(director.MAX_SECTIONS + 2)
        ]
    }
    try:
        director.validate_proposal(too_many, sheet, track_seconds=TRACK)
    except director.PlanDirectorError as err:
        assert str(director.MAX_SECTIONS) in str(err), err
        return
    raise AssertionError("zu viele Abschnitte haetten abgelehnt werden muessen")


def verify_the_same_proposal_asks_for_the_same_music() -> None:
    """The hash decides whether a re-render pays again."""
    sheet = _sheet()
    first = director.validate_proposal(GOOD, sheet, track_seconds=TRACK)
    second = director.validate_proposal(GOOD, sheet, track_seconds=TRACK)
    assert director.proposal_hash(first, model="m") == director.proposal_hash(
        second, model="m"
    )
    # A different model is different music, whatever the times say.
    assert director.proposal_hash(first, model="m") != director.proposal_hash(
        first, model="other"
    )
    # And a changed mood is a changed prompt, so it is changed music.
    moved = [dict(entry) for entry in first]
    moved[1]["mood"] = "etwas anderes"
    assert director.proposal_hash(moved, model="m") != director.proposal_hash(
        first, model="m"
    )


def verify_no_photograph_is_ever_sent() -> None:
    """§28: structured facts, and the trip's own words. Nothing else."""
    sheet = _sheet()
    brief = director.build_brief(
        sheet,
        trip_title="Finnland 2026",
        narrative={"arc": "Eine grosse Runde", "opening": "los", "closing": "an"},
        motifs=["Wasser", "lange Abende"],
        max_sections=4,
        style="Instrumental, warm.",
    )
    lowered = brief.lower()
    for forbidden in (
        "base64",
        "image",
        "photo",
        "foto",
        "jpeg",
        "jpg",
        "media_id",
        "inlinedata",
        "http",
        "/share",
    ):
        assert forbidden not in lowered, f"der Auftrag enthaelt {forbidden!r}"
    # What it DOES carry: the film's own shape and the trip's words.
    assert "Finnland 2026" in brief
    assert "cues" in lowered
    assert "erfinde keine" in lowered, "die Regel gegen erfundene Fakten fehlt"


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music plan director tests passed.")


if __name__ == "__main__":
    main()
