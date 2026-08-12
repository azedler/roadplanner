"""What gets bought, and what somebody is told before it is.

The first real charge for music should be small: sixty to ninety seconds
of the actual film with actual music, made through the actual path, and
judged - before twelve minutes of it are paid for. That is a question
about SCOPE, and the risk of answering it with a second code path is
that the prototype then proves nothing about the thing it stands in for.
So the same plan is narrowed to a window here rather than replaced.

The other half is honesty about the unit. The provider bills per request
and delivers a piece of its own length. "75 Sekunden Musik" invites
reading seconds as the thing being paid for, and they are not. Every
check below that looks at wording is there because a number without its
unit has already misled somebody in this project once.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "custom_components" / "roadplanner_mcp"

_pkg = types.ModuleType("roadplanner_genplan_pkg")
_pkg.__path__ = [str(PACKAGE_ROOT)]
sys.modules["roadplanner_genplan_pkg"] = _pkg


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_genplan_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = load("music_generation_plan")

TRACK = 180.0
PRICE = 0.08


def _plan(film_seconds: float = 743.0) -> dict:
    """A plan shaped like the real one: sections covering the film."""
    count = 5
    length = film_seconds / count
    return {
        "film_seconds": film_seconds,
        "sections": [
            {
                "section": f"cue-{index}",
                "label": f"Abschnitt {index + 1}",
                "prompt": f"warm, nordisch, ruhig ({index})",
                "mood": "weit",
                "start_seconds": round(index * length, 2),
                "end_seconds": round((index + 1) * length, 2),
                "seconds": round(length, 2),
            }
            for index in range(count)
        ],
    }


def verify_a_prototype_buys_one_piece_not_a_soundtrack() -> None:
    """§10: the first real charge is as small as an answer can be.

    A seventy-five-second window of a twelve-minute film touches one
    section. Everything else is left unbought - not because it is
    cheaper to pretend it does not exist, but because nothing has been
    learned yet that would justify it.
    """
    found = gen.build_generation_plan(
        _plan(),
        scope=gen.SCOPE_PROTOTYPE,
        window=(0.0, 75.0),
        track_seconds=TRACK,
        price_per_generation=PRICE,
        model="lyria-3-pro-preview",
    )
    assert found["generation_count"] == 1, found["sections"]
    assert abs(found["estimated_cost"] - PRICE) < 0.001, found
    assert found["window_seconds"] == 75.0


def verify_the_whole_film_buys_every_section() -> None:
    """Same plan, same code, wider window. That is the entire difference."""
    plan = _plan()
    whole = gen.build_generation_plan(
        plan,
        scope=gen.SCOPE_FULL_FILM,
        track_seconds=TRACK,
        price_per_generation=PRICE,
        model="m",
    )
    assert whole["generation_count"] == len(plan["sections"])
    assert abs(whole["estimated_cost"] - len(plan["sections"]) * PRICE) < 0.001


def verify_the_price_is_never_quoted_per_second() -> None:
    """§14, as a property of the answer rather than a hope.

    The provider charges for a REQUEST. A dialog that reports seconds
    beside a price invites reading one as the unit of the other, and
    this project has already shown somebody a figure that was a third of
    the real one for exactly that reason.
    """
    found = gen.build_generation_plan(
        _plan(),
        scope=gen.SCOPE_PROTOTYPE,
        window=(0.0, 75.0),
        track_seconds=TRACK,
        price_per_generation=PRICE,
        model="m",
    )
    assert found["billed_per"] == "generation"
    # The two numbers are both present and are NOT the same thing.
    assert found["requested_seconds"] > found["window_seconds"], found
    assert found["provider_track_seconds"] == round(TRACK)
    # And the estimate says it is one.
    assert found["cost_is_estimate"] is True
    sentence = gen.describe(found)
    assert "pro Anfrage" in sentence, sentence
    assert "Generierung" in sentence, sentence


def verify_what_already_exists_is_not_bought_again() -> None:
    """The cache decides the price, so it decides before the request."""
    plan = _plan()
    names = {section["section"]: f"{section['section']}.mp3" for section in plan["sections"]}
    found = gen.build_generation_plan(
        plan,
        scope=gen.SCOPE_FULL_FILM,
        cached_by_section=names,
        track_seconds=TRACK,
        price_per_generation=PRICE,
        model="m",
    )
    assert found["generation_count"] == 0
    assert found["estimated_cost"] == 0.0
    assert len(found["reused"]) == len(plan["sections"])
    assert "kostet nichts" in gen.describe(found)


def verify_a_section_longer_than_a_generation_is_refused() -> None:
    """The silence this area already produced once, priced this time.

    A section of 186 s against a generation that delivers 180 plays its
    track and then goes quiet. Refused before the charge rather than
    discovered after it.
    """
    plan = _plan(film_seconds=1200.0)
    try:
        gen.build_generation_plan(
            plan,
            scope=gen.SCOPE_FULL_FILM,
            track_seconds=TRACK,
            price_per_generation=PRICE,
            model="m",
        )
    except gen.GenerationPlanError as err:
        assert "still" in str(err), err
        return
    raise AssertionError("ein zu langer Abschnitt haette abgelehnt werden muessen")


def verify_a_window_with_no_music_is_named_rather_than_charged() -> None:
    """An empty answer is a sentence, not a zero-cost purchase."""
    plan = _plan()
    try:
        gen.build_generation_plan(
            plan,
            scope=gen.SCOPE_PROTOTYPE,
            window=(5000.0, 5075.0),
            track_seconds=TRACK,
            price_per_generation=PRICE,
            model="m",
        )
    except gen.GenerationPlanError as err:
        assert "Zeitfenster" in str(err), err
        return
    raise AssertionError("ein Fenster ohne Musik haette benannt werden muessen")


def verify_the_prototype_window_matches_the_quality_excerpt() -> None:
    """The music is judged under the film somebody actually looks at.

    A window of its own would mean the prototype proved something about
    a piece nobody is going to ship - the same argument the quality
    excerpt already rests on.
    """
    qa = load("qa_excerpt")
    assert gen.PROTOTYPE_MIN_SECONDS == qa.QA_MIN_SECONDS
    assert gen.PROTOTYPE_MAX_SECONDS == qa.QA_MAX_SECONDS


def verify_the_same_plan_hashes_the_same_way() -> None:
    """Whether a re-render pays again is decided here."""
    plan = _plan()
    assert gen.plan_hash(plan, model="m") == gen.plan_hash(_plan(), model="m")
    assert gen.plan_hash(plan, model="m") != gen.plan_hash(plan, model="other")
    moved = _plan()
    moved["sections"][2]["prompt"] = "etwas anderes"
    assert gen.plan_hash(moved, model="m") != gen.plan_hash(plan, model="m")


def verify_nothing_here_reaches_a_provider() -> None:
    """Arithmetic over a plan, a cache listing and a limit. Nothing else.

    Read from the syntax tree, not from the text. The first version
    searched the source for words like "requests" and tripped over the
    docstring sentence "how many requests, at what price" - a check that
    fails on the prose explaining the code is worse than no check, and
    this is the third time today that shape has appeared.
    """
    import ast

    tree = ast.parse((PACKAGE_ROOT / "music_generation_plan.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        # Nothing here may be asynchronous: an await is where a network
        # call would hide.
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await)), ast.dump(node)[:120]
    # A pure arithmetic module. Anything beyond this list is a new
    # dependency that has to be argued for.
    assert imported <= {"__future__", "hashlib", "typing"}, imported


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music generation plan tests passed.")


if __name__ == "__main__":
    main()
