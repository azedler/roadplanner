"""The layer between the film and any decision about its music.

Before this existed the planner cut the film into equal shares by
duration and never looked at what was in them: a musical section could
begin in the middle of a map drive, and the "Aufbruch" could be four
minutes of somebody's seventh day. The sheet is what makes a plan able
to know what it is planning over.

Two properties carry money, so both are checked rather than assumed.

**Deterministic.** The sheet is part of the cache key for music that is
paid for per generation. A sheet that wobbled by a rounding step would
buy a second soundtrack for a film nobody changed.

**Free.** It is arithmetic over a plan. Nothing here may reach a
provider, and nothing here may need one to be checked.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"

_pkg = types.ModuleType("roadplanner_cue_pkg")
_pkg.__path__ = [str(PACKAGE_ROOT)]
sys.modules["roadplanner_cue_pkg"] = _pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_cue_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plan_module = load("trip_film_plan")
cue_module = load("music_cue_sheet")


def _plan() -> tuple[dict, list[dict]]:
    """A film shaped like a real one: framing, then days of mixed kinds."""
    scenes = [
        {"type": "intro", "chapter_id": "", "frames": 150},
        {"type": "crew", "chapter_id": "", "frames": 120},
        {"type": "map_start", "chapter_id": "", "frames": 120},
    ]
    chapters = []
    shapes = [
        # A long drive with little to look at.
        [("map_leg", 320), ("chapter_card", 90), ("collage", 140)],
        # A day of photographs and no map at all.
        [("chapter_card", 90)] + [("collage", 140)] * 4 + [("photo", 80)] * 2,
        # An ordinary day that also has a clip.
        [("map_leg", 200), ("chapter_card", 90)]
        + [("collage", 140)] * 3
        + [("clip", 180)],
        [("map_leg", 200), ("chapter_card", 90)] + [("collage", 140)] * 3,
    ]
    for index, shape in enumerate(shapes):
        chapter_id = f"d{index}"
        chapters.append(
            {
                "chapter_id": chapter_id,
                "story_role": "journey",
                "importance": "highlight" if index == 2 else "normal",
                "day_number": index + 1,
            }
        )
        for kind, frames in shape:
            scenes.append({"type": kind, "chapter_id": chapter_id, "frames": frames})
    scenes.append({"type": "outro_collage", "chapter_id": "", "frames": 180})
    scenes.append({"type": "outro", "chapter_id": "", "frames": 150})
    return (
        {
            "plan_version": 1,
            "fps": 30,
            "total_frames": sum(int(s["frames"]) for s in scenes),
            "scenes": scenes,
        },
        chapters,
    )


def verify_the_same_film_always_yields_the_same_sheet() -> None:
    """It is part of a cache key for something that costs money."""
    plan, chapters = _plan()
    first = json.dumps(cue_module.build_cue_sheet(plan, chapters=chapters), sort_keys=True)
    second = json.dumps(cue_module.build_cue_sheet(plan, chapters=chapters), sort_keys=True)
    assert first == second


def verify_the_sheet_covers_the_whole_film_without_gaps() -> None:
    """Music planned over a sheet with a hole would have a silent hole."""
    plan, chapters = _plan()
    sheet = cue_module.build_cue_sheet(plan, chapters=chapters)
    cues = sheet["cues"]
    assert cues[0]["start_seconds"] == 0.0
    assert abs(cues[-1]["end_seconds"] - sheet["film_seconds"]) < 0.05
    for earlier, later in zip(cues, cues[1:]):
        assert abs(later["start_seconds"] - earlier["end_seconds"]) < 0.05, (
            earlier,
            later,
        )
    assert abs(sum(cue["seconds"] for cue in cues) - sheet["film_seconds"]) < 0.1


def verify_a_cue_never_straddles_a_day() -> None:
    """A section that changed inside a day is what §41 forbids."""
    plan, chapters = _plan()
    sheet = cue_module.build_cue_sheet(plan, chapters=chapters)
    names = [cue["chapter_id"] for cue in sheet["cues"]]
    assert len(names) == len(set(names)) or names.count("") > 1, names
    # Every day appears exactly once, as one contiguous cue.
    for chapter in chapters:
        assert names.count(chapter["chapter_id"]) == 1, (chapter, names)


def verify_every_energy_level_can_actually_occur() -> None:
    """A word no film reaches is an absent answer wearing a name.

    The first bands were 0.15 and 0.50 moving share, and nothing real
    ever passed 0.50 - measured across a transition day, an ordinary day,
    a day with a clip, a highlight with two, a photo day with no map and
    a long drive, the share runs 0.00 to 0.58 and clusters at 0.3-0.45.
    """
    plan, chapters = _plan()
    sheet = cue_module.build_cue_sheet(plan, chapters=chapters)
    used = {cue["energy_hint"] for cue in sheet["cues"]}
    assert used == {
        cue_module.ENERGY_CALM,
        cue_module.ENERGY_STEADY,
        cue_module.ENERGY_LIVELY,
    }, f"nicht jede Stufe kommt vor: {sorted(used)}"


def verify_the_sheet_says_what_is_there_and_invents_nothing() -> None:
    """Absent is absent - a story role is not made up for a prompt."""
    plan, chapters = _plan()
    sheet = cue_module.build_cue_sheet(plan, chapters=chapters)
    framing = [cue for cue in sheet["cues"] if not cue["chapter_id"]]
    assert framing, "ein Film hat eine Rahmung"
    for cue in framing:
        assert cue["story_role"] == "", cue
        assert cue["importance"] == "", cue
        assert cue["day_number"] is None, cue
    # And what IS there is reported from the plan rather than guessed.
    with_video = [cue for cue in sheet["cues"] if cue["has_video"]]
    assert with_video, "der Testfilm hat einen Clip"
    for cue in with_video:
        assert "clip" in cue["scene_types"], cue


def verify_the_opening_and_the_close_are_recognised() -> None:
    plan, chapters = _plan()
    cues = cue_module.build_cue_sheet(plan, chapters=chapters)["cues"]
    assert cues[0]["narrative_function"] == cue_module.FUNCTION_OPENING
    assert cues[0]["is_intro"] is True
    assert cues[-1]["narrative_function"] == cue_module.FUNCTION_CLOSING
    assert cues[-1]["is_outro"] is True


def verify_nothing_here_can_reach_a_provider() -> None:
    """Free by construction, not by intention."""
    import ast
    import re

    text = (PACKAGE_ROOT / "music_cue_sheet.py").read_text(encoding="utf-8")
    # Docstrings out, not just `#` lines. The module EXPLAINS that it
    # knows nothing about Gemini or Lyria, and a check that read its own
    # explanation as a violation would fail on the sentence promising the
    # thing it is checking. This project has made exactly that mistake
    # before, with a place name in a comment.
    tree = ast.parse(text)
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                first = node.body[0]
                spans.append((first.lineno, first.end_lineno))
    lines = text.splitlines()
    keep = [
        line
        for number, line in enumerate(lines, start=1)
        if not any(start <= number <= end for start, end in spans)
        and not line.strip().startswith("#")
    ]
    code = re.sub(r"#.*$", "", "\n".join(keep), flags=re.M)
    for forbidden in (
        "gemini",
        "lyria",
        "vertex",
        "http",
        "session",
        "api_key",
        "async ",
    ):
        assert forbidden.lower() not in code.lower(), (
            f"music_cue_sheet.py erwähnt {forbidden!r} - die Schicht muss "
            "providerneutral und kostenlos bleiben"
        )


def verify_a_film_without_scenes_is_refused() -> None:
    try:
        cue_module.build_cue_sheet({"fps": 30, "scenes": []})
    except cue_module.CueSheetError:
        return
    raise AssertionError("ein leerer Plan haette abgelehnt werden muessen")


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music cue sheet tests passed.")


if __name__ == "__main__":
    main()
