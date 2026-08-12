"""One film, three soundtracks, and only what has to be bought.

The experiment is worth running only if it compares architectures. Two
things would quietly turn it into a comparison of something else, and
both are checked here rather than hoped for:

**A second bed.** If the layered variant and the atmosphere variant each
generated their own atmosphere, they would differ by their material as
well as by their structure, and the listener's answer would mean
nothing. The bed is one purchase, used twice.

**A second style.** If each role were described in its own words, the
listener would be choosing a taste. Every request carries the same
locked style sentence, character for character.

The third thing checked is that this cannot become the product. A
prototype able to order twelve minutes of music is not a prototype, and
the guard is a refusal rather than an intention.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = "roadplanner_prototype_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

proto = importlib.import_module(f"{_PACKAGE}.music_prototype")
arch = importlib.import_module(f"{_PACKAGE}.music_architecture")
lock_module = importlib.import_module(f"{_PACKAGE}.music_style_lock")
cue_module = importlib.import_module(f"{_PACKAGE}.music_cue_sheet")
plan_module = importlib.import_module(f"{_PACKAGE}.trip_film_plan")

MODEL = "lyria-3-pro-preview"
TRACK = 184.0
PRICE = 0.08


def _scene(kind: str, seconds: float, chapter: str) -> dict:
    return {
        "type": kind,
        "frames": int(round(seconds * plan_module.FILM_FPS)),
        "chapter_id": chapter,
    }


def _film() -> dict:
    """A film shaped like a real one: framing, then days of mixed scenes.

    Long enough that the excerpt chooser has somewhere to choose FROM -
    a plan barely longer than the window would make every check here
    pass for the wrong reason.
    """
    scenes = [
        _scene(plan_module.SCENE_INTRO, 6, "intro"),
        _scene(plan_module.SCENE_MAP_START, 5, "intro"),
    ]
    for day in range(1, 9):
        chapter = f"day-{day}"
        scenes.extend(
            [
                _scene(plan_module.SCENE_CHAPTER_CARD, 3, chapter),
                _scene(plan_module.SCENE_MAP_LEG, 9, chapter),
                _scene(plan_module.SCENE_HERO, 6, chapter),
                _scene(plan_module.SCENE_COLLAGE, 7, chapter),
                _scene(plan_module.SCENE_TEXT, 5, chapter),
            ]
        )
        if day % 2 == 0:
            scenes.append(_scene(plan_module.SCENE_CLIP, 8, chapter))
    scenes.append(_scene(plan_module.SCENE_OUTRO, 7, "outro"))
    return {"fps": plan_module.FILM_FPS, "scenes": scenes}


def _chapters() -> list[dict]:
    return [
        {"chapter_id": f"day-{day}", "story_role": "reisetag", "importance": "normal"}
        for day in range(1, 9)
    ]


def _build(**extra):
    return proto.build_prototype(
        _film(),
        chapters=_chapters(),
        model=MODEL,
        track_seconds=TRACK,
        price_per_generation=PRICE,
        **extra,
    )


def verify_three_variants_cost_three_generations() -> None:
    """Three fassungen, three roles, three requests. Not six."""
    found = _build()
    assert len(found["variants"]) == 3, found["variants"]
    assert found["generation_count"] == 3, found["assets"]
    assert abs(found["estimated_cost"] - 3 * PRICE) < 0.001, found
    assert found["billed_per"] == "generation"
    assert found["cost_is_estimate"] is True


def verify_the_atmosphere_is_bought_once_and_used_twice() -> None:
    """B and C share their bed, or they are not comparable.

    Two beds would mean the layered variant and the control differ by
    their material as well as by their architecture, and no listening
    answer could separate the two.
    """
    found = _build()
    assert proto.bed_is_shared(found)
    beds = [
        layer
        for variant in found["variants"]
        for layer in variant["layers"]
        if layer["role"] == arch.ROLE_BED
    ]
    assert len(beds) == 2, beds
    assert beds[0]["cache_key"] == beds[1]["cache_key"]
    # And it really is only three purchases for those two variants plus
    # the baseline.
    assert len({asset["cache_key"] for asset in found["assets"]}) == 3


def verify_every_request_carries_the_same_locked_style() -> None:
    """Otherwise the test compares tastes, not architectures."""
    found = _build()
    sentence = found["style_sentence"]
    assert sentence
    for asset in found["assets"]:
        assert sentence in asset["prompt"], asset["role"]
    # The style lock's hash is part of what the audio is keyed on, so a
    # changed style is genuinely different audio rather than a silent
    # reuse of the old one.
    other = lock_module.build_style_lock(style="cold minimal electronic score")
    changed = _build(style_lock=other)
    assert {asset["cache_key"] for asset in changed["assets"]}.isdisjoint(
        {asset["cache_key"] for asset in found["assets"]}
    )


def verify_the_bed_is_asked_to_be_a_bed() -> None:
    """A bed that follows the film is a second score.

    The energy arc goes to the pieces that carry a statement and
    deliberately not to the atmosphere - if the bed rose and fell with
    the excerpt, the layered variant would be two scores stacked and
    the comparison would be meaningless.
    """
    found = _build()
    bed = next(asset for asset in found["assets"] if asset["role"] == arch.ROLE_BED)
    score = next(asset for asset in found["assets"] if asset["role"] == arch.ROLE_SCORE)
    assert found["energy_arc"], found
    assert found["energy_arc"] not in bed["prompt"]
    assert found["energy_arc"] in score["prompt"]
    for forbidden in ("no lead melody", "no drums"):
        assert forbidden in bed["prompt"], bed["prompt"]


def verify_the_bed_sits_under_the_accent_not_beside_it() -> None:
    """§22: summing both at the same level is not a layered mix."""
    layered = arch.variant_layers(arch.VARIANT_B)
    gains = {layer["role"]: layer["gain"] for layer in layered}
    assert gains[arch.ROLE_BED] < gains[arch.ROLE_ACCENT] / 2, gains
    # Alone, the bed may come up - but not so far that the background
    # layer has quietly become a lead track, which would be a fourth
    # architecture nobody asked to test.
    alone = arch.variant_layers(arch.VARIANT_C)[0]["gain"]
    assert gains[arch.ROLE_BED] < alone < 1.0, alone


def verify_a_prototype_cannot_become_a_soundtrack() -> None:
    """§17, as a refusal rather than as an intention.

    The window is the quality excerpt's own window. Anything longer is
    not a test of an architecture, it is the score - and that is the
    purchase this whole block exists to postpone.
    """
    found = _build()
    assert found["window_seconds"] <= proto.MAX_PROTOTYPE_SECONDS + 0.5, found
    assert found["window_seconds"] >= 55.0, found
    # Every layer is bounded by the window too, so no mix can run past it.
    for variant in found["variants"]:
        for layer in variant["layers"]:
            assert layer["seconds"] <= found["window_seconds"] + 0.01


def verify_the_excerpt_is_the_one_somebody_actually_looks_at() -> None:
    """The same chooser as the quality check, not a window of its own."""
    qa = importlib.import_module(f"{_PACKAGE}.qa_excerpt")
    found = _build()
    same = qa.excerpt_range(_film())
    assert found["excerpt"]["start_frame"] == same["start_frame"]
    assert found["excerpt"]["frames"] == same["frames"]


def verify_the_cue_sheet_describes_movements_not_cuts() -> None:
    """§11: a minute of film is a few movements, not fifteen scenes."""
    found = _build()
    sheet = found["cue_sheet"]
    cues = sheet["cues"]
    assert 1 <= len(cues) <= cue_module.MAX_WINDOW_CUES, len(cues)
    # Contiguous, in order, and covering the window exactly - a gap
    # would be a stretch of film no music was planned for.
    assert cues[0]["window_start_seconds"] == 0.0
    for earlier, later in zip(cues, cues[1:]):
        assert earlier["end_seconds"] == later["start_seconds"], (earlier, later)
    assert abs(cues[-1]["window_end_seconds"] - sheet["window_seconds"]) < 0.05
    for cue in cues:
        assert cue["seconds"] > 0
        assert cue["energy_hint"] in {
            cue_module.ENERGY_CALM,
            cue_module.ENERGY_STEADY,
            cue_module.ENERGY_LIVELY,
        }
        assert cue["transition_hint"] in {"weiter", "kapitelwechsel"}


def verify_the_cue_sheet_is_deterministic_and_carries_no_media() -> None:
    """The same plan yields the same sheet, and it names no file.

    The sheet is what a planner is shown. §10 keeps private photographs
    and videos out of that entirely - what travels is times, scene kinds
    and the words the trip already has.
    """
    import json

    first = _build()["cue_sheet"]
    second = _build()["cue_sheet"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    text = json.dumps(first, ensure_ascii=False).lower()
    for leak in (".jpg", ".jpeg", ".png", ".mp4", ".mov", "base64", "media_id", "http"):
        assert leak not in text, leak


def verify_a_planner_may_colour_a_request_but_not_time_it() -> None:
    """§9: character yes, clock no.

    Refused rather than trimmed. A number in a prompt reads as
    authority, and this one would have been invented by a model that
    cannot see the film.
    """
    assert proto.validate_director_text("  gentle fingerpicked guitar  ") == (
        "gentle fingerpicked guitar"
    )
    assert proto.validate_director_text("") == ""
    for bad in (
        "open quietly for 20 seconds then lift",
        "hold around 92 BPM throughout",
        "x" * (proto.MAX_DIRECTOR_CHARS + 1),
    ):
        try:
            proto.validate_director_text(bad)
        except proto.PrototypeError:
            continue
        raise AssertionError(f"haette abgelehnt werden muessen: {bad[:40]!r}")

    # And an accepted one really reaches the request, marked as such.
    coloured = _build(director_text_by_role={arch.ROLE_SCORE: "gentle fingerpicked guitar"})
    score = next(a for a in coloured["assets"] if a["role"] == arch.ROLE_SCORE)
    assert score["prompt"].endswith("gentle fingerpicked guitar")
    assert score["planned_by"] == "gemini"
    bed = next(a for a in coloured["assets"] if a["role"] == arch.ROLE_BED)
    assert bed["planned_by"] == "deterministisch"


def verify_what_exists_is_not_bought_again() -> None:
    """Re-mixing A/B/C after a restart must cost nothing.

    That is the whole point of §27: the user should be able to try the
    three fassungen, change the review profile, and try them again,
    without paying Lyria a second time.
    """
    first = _build()
    names = {asset["cache_key"]: f"{asset['cache_key']}.mp3" for asset in first["assets"]}
    again = _build(cached_by_key=names)
    assert again["generation_count"] == 0
    assert again["estimated_cost"] == 0.0
    assert again["reused_count"] == 3
    assert all(variant["ready"] for variant in again["variants"])
    assert "kosten nichts" in proto.describe(again)


def verify_the_price_says_what_the_unit_is() -> None:
    """"75 Sekunden Musik" invites reading seconds as the unit of price."""
    sentence = proto.describe(_build())
    assert "pro Anfrage" in sentence, sentence
    assert "Generierungen" in sentence, sentence


def verify_the_style_lock_never_claims_to_be_an_instruction() -> None:
    """Tempo and key are wishes. The provider takes neither as a field.

    A schema in which every property looked equally binding would be
    this project's oldest mistake in a new place: a figure that reads
    like a guarantee and is a guess.
    """
    lock = lock_module.build_style_lock()
    assert set(lock["influence"]) == set(lock_module.DEFAULT_STYLE_LOCK)
    assert set(lock["influence"].values()) == {lock_module.INFLUENCE_PROMPT_ONLY}
    for wish in ("tempo_bpm", "key"):
        assert wish in lock["not_measured"], lock["not_measured"]

    report = lock_module.requested_versus_measured(lock, {"duration_seconds": 81.4})
    assert report["measured"] == {"duration_seconds": 81.4}
    # The two that were never checked are still named. An absent answer
    # rendered as a state is this project's most repeated failure.
    assert "tempo_bpm" in report["not_measured"]
    assert "loudness_lufs" in report["not_measured"]

    try:
        lock_module.build_style_lock(tempo="fast")
    except lock_module.StyleLockError:
        pass
    else:
        raise AssertionError("ein unbekanntes Feld haette abgelehnt werden muessen")


def verify_no_architecture_is_the_default_yet() -> None:
    """§18: the code must not already assume every film wants a bed."""
    assert set(arch.ARCHITECTURES) == {
        arch.ARCH_SINGLE_SCORE,
        arch.ARCH_LAYERED_BED_ACCENT,
        arch.ARCH_ATMOSPHERE_ONLY,
    }
    # §19: the product word is "atmosphere". "Drone" names one technique
    # for producing it, and tying the vocabulary to an implementation is
    # how a technique becomes a requirement.
    #
    # Read from the syntax tree: identifiers and the strings that reach a
    # prompt or a screen. Docstrings are excluded deliberately - the
    # first version searched the text and failed on the sentence
    # EXPLAINING why the word is avoided, which is this project's
    # third-favourite way to write a useless check.
    import ast

    for module in ("music_architecture.py", "music_prototype.py", "music_style_lock.py"):
        tree = ast.parse(
            (ROOT / "custom_components" / "roadplanner_mcp" / module).read_text(
                encoding="utf-8"
            )
        )
        documented = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        }
        assert ast.get_docstring(tree)  # the module really is documented
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                assert "drone" not in node.id.lower(), f"{module}: {node.id}"
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in documented:
                    continue
                assert "drone" not in node.value.lower(), f"{module}: {node.value[:70]}"


def verify_nothing_here_reaches_a_provider() -> None:
    """Read from the syntax tree, not from the prose about it."""
    import ast

    for name in ("music_prototype", "music_architecture", "music_style_lock"):
        path = ROOT / "custom_components" / "roadplanner_mcp" / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await)), name
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not {"aiohttp", "requests", "urllib", "http", "socket"} & imported, (
            name,
            imported,
        )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music prototype tests passed.")


if __name__ == "__main__":
    main()
