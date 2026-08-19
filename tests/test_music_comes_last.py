"""Music goes ONTO the film, never INTO the render package - and no
executor call may pass a function where data belongs.

Two call sites read, for months::

    await self._hass.async_add_executor_job(
        build_music_timeline_package,
        build_music_variant_package, timeline
    )

A careless edit during the A/B/C work had inserted a FUNCTION as the
first data argument, so ``build_music_timeline_package`` received a
callable as its timeline and a list as its music root. Every attempt
crashed with "'function' object is not iterable" - and it stayed broken
for a week, which also proved that the render-time branch had no users:
the mux path is how music actually reaches a film.

So this file checks two things:

1. **The architecture decision, at the boundary.** "Music comes last."
   ``_async_music`` with the generated score renders silent and builds
   nothing; ``build_music_timeline_package`` is called exactly once, in
   ``async_add_music``, with the timeline as its only data argument.

2. **The failure shape, generalised.** For every
   ``async_add_executor_job`` call in the integration, no argument after
   the callable may itself be a function imported from a sibling module.
   A function passed as data does not fail at the call site - it fails
   inside the callee, later, with a message that names neither.
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
EXPORT = INTEGRATION / "trip_film_export.py"


def _local_function_names(tree: ast.Module) -> set[str]:
    """Names this module binds that are known to be callables.

    Functions imported from sibling modules (``from .x import y``) and
    top-level ``def``s. Constants are imported too, but the convention
    holds throughout this codebase: UPPER_CASE is data, snake_case from a
    relative import is a callable. Classes are CamelCase and excluded the
    same way.
    """
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            for alias in node.names:
                name = alias.asname or alias.name
                if name != name.upper() and name[:1].islower():
                    found.add(name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add(node.name)
    return found


def _executor_calls(tree: ast.Module) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_add_executor_job"
        ):
            calls.append(node)
    return calls


def verify_no_executor_call_passes_a_function_as_data() -> None:
    problems: list[str] = []
    for path in sorted(INTEGRATION.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = _local_function_names(tree)
        for call in _executor_calls(tree):
            for arg in call.args[1:]:
                if isinstance(arg, ast.Name) and arg.id in functions:
                    problems.append(
                        f"{path.name}:{arg.lineno}: {arg.id} ist eine Funktion "
                        "und wird als Datenargument an den Executor gereicht - "
                        "genau so wurde der Musik-Mux eine Woche lang unbenutzbar"
                    )
    assert not problems, "\n".join(problems)


def verify_the_timeline_package_is_built_only_for_the_mux() -> None:
    """Once, in async_add_music, with the timeline and nothing else."""
    tree = ast.parse(EXPORT.read_text(encoding="utf-8"))
    sites: list[tuple[str, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for call in _executor_calls(node):
            first = call.args[0] if call.args else None
            if isinstance(first, ast.Name) and first.id == "build_music_timeline_package":
                sites.append((node.name, call))
    assert len(sites) == 1, (
        f"build_music_timeline_package wird an {len(sites)} Stellen gebaut - "
        "die Musik gehört auf den fertigen Film, nicht ins Renderpaket"
    )
    owner, call = sites[0]
    assert owner == "async_add_music", (
        f"das Musikpaket entsteht in {owner} statt im Mux-Pfad"
    )
    rest = call.args[1:]
    assert (
        len(rest) == 1 and isinstance(rest[0], ast.Name) and rest[0].id == "timeline"
    ), "der Mux baut das Paket nicht mehr aus der Timeline"


def verify_the_generated_score_renders_silent() -> None:
    """Choosing KI-Musik at render time must build no music package.

    The silent film is the master: the score is muxed on afterwards,
    fitted to the measured length. A branch here that assembled music
    into the package would be the architecture decision being undone in
    the one place nobody looks.
    """
    tree = ast.parse(EXPORT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_music":
            names = {
                inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)
            }
            assert "build_music_timeline_package" not in names, (
                "_async_music baut wieder ein Timeline-Paket ins Renderpaket"
            )
            assert "build_music_variant_package" not in names, names
            # The plain-file branch stays: an own MP3 from the folder has
            # always travelled with the package and is not the generated
            # score.
            assert "build_music_package" in names, (
                "die eigene Musikdatei aus dem Ordner spielt nicht mehr"
            )
            return
    raise AssertionError("_async_music gibt es nicht mehr")


def verify_the_panel_muxes_after_the_render() -> None:
    """The other half of the promise: silent render, then the score.

    Without this, "Mit KI-Musik" would quietly mean "ohne Musik" - the
    absent second step rendered as a finished state, which is this
    project's second-most repeated mistake.
    """
    story = (INTEGRATION / "frontend" / "features" / "story-editor.js").read_text(
        encoding="utf-8"
    )
    app = (INTEGRATION / "frontend" / "features" / "renderer-app.js").read_text(
        encoding="utf-8"
    )
    assert "this._storyFilmMuxAfterJobId =" in story and "GENERATED_MUSIC ? result.renderer_app_job.job_id" in story, (
        "die Mux-Absicht wird beim Einreichen nicht mehr an den Auftrag gebunden"
    )
    assert "this._storyFilmMuxAfterTripId = this._selectedTripId" in story, (
        "die Mux-Absicht merkt sich die Reise nicht mehr - ein Reisewechsel "
        "während des Renders würde die falsche Reise vertonen"
    )
    assert "_storyFilmMaybeAutoMux" in app, (
        "der Job-Poll löst den Mux nach dem Render nicht mehr aus"
    )
    # One shot: the flag falls on EVERY terminal outcome, so a failed or
    # cancelled render cannot leave a primed mux behind.
    marker = "_storyFilmMaybeAutoMux(job) {"
    assert marker in story, "der Auto-Mux-Haken fehlt"
    body = story.split(marker, 1)[1].split("\n  },", 1)[0]
    cleared = body.index('this._storyFilmMuxAfterJobId = ""')
    checked = body.index('job.state !== "completed"')
    assert cleared < checked, (
        "die Mux-Absicht wird erst nach der Erfolgsprüfung gelöscht - ein "
        "gescheiterter Render liesse sie für einen späteren Auftrag scharf"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Music comes last tests passed.")


if __name__ == "__main__":
    main()
