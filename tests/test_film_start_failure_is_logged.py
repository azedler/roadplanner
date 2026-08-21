"""RP-415: a failed film start writes the line the panel promises.

The card told people the reason was in the Home Assistant log under
"roadplanner". A full-text search of an untouched log found no single
line - the reason went down the WebSocket to a browser that discarded
it, and nowhere else. Somebody then spent three days on the renderer
app, which was the only component behaving correctly.

An absent answer rendered as a state: this project's second most
repeated fault, and the promise makes it worse than silence.
"""
from __future__ import annotations

import ast
from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "custom_components" / "roadplanner_mcp" / "panel.py"
STORY = (
    ROOT
    / "custom_components"
    / "roadplanner_mcp"
    / "frontend"
    / "features"
    / "story-editor.js"
)


def _tree() -> ast.Module:
    return ast.parse(PANEL.read_text(encoding="utf-8"))


def _logged_actions() -> set[str]:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_LOGGED_FAILURE_ACTIONS"
            for target in node.targets
        ):
            return {
                element.value
                for element in ast.walk(node.value)
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
    raise AssertionError("_LOGGED_FAILURE_ACTIONS gibt es nicht")


def verify_the_film_start_actions_are_logged() -> None:
    """Every action whose card points at the log has to write to it."""
    logged = _logged_actions()
    for action in ("story_film_render", "story_film_qa_render"):
        assert action in logged, (
            f"{action} verweist die Nutzer aufs Protokoll und schreibt nichts hinein"
        )


def verify_the_handler_actually_warns() -> None:
    """The declaration is not the behaviour - find the warning call.

    Inside the RoadplannerError branch of the websocket handler, gated by
    the table, at warning level. `_LOGGER.debug` would satisfy a laxer
    check and still leave a default Home Assistant log empty.
    """
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = {
            inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)
        }
        if "RoadplannerError" not in names or "_LOGGED_FAILURE_ACTIONS" not in names:
            continue
        calls = [
            inner
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "warning"
            and isinstance(inner.func.value, ast.Name)
            and inner.func.value.id == "_LOGGER"
        ]
        assert calls, "der Zweig prüft die Tabelle, protokolliert aber nichts"
        # The message has to carry the reason, not just the fact.
        arguments = ast.unparse(calls[0])
        assert "err" in arguments, f"die Warnung nennt den Grund nicht: {arguments}"
        assert "action" in arguments, f"die Warnung nennt die Aktion nicht: {arguments}"
        return
    raise AssertionError(
        "kein RoadplannerError-Zweig, der die protokollierten Aktionen kennt"
    )


def verify_the_card_no_longer_promises_the_log_as_the_only_answer() -> None:
    """The pointer to the log stays, but only as the last resort.

    It is correct exactly when there was no message to show - which is
    the branch it now lives in.
    """
    story = STORY.read_text(encoding="utf-8")
    assert "_storyFilmStartReason(" in story, (
        "der Filmstart erklärt sich wieder pauschal statt mit der Servermeldung"
    )
    # Both start paths route through it.
    assert story.count("this._storyFilmStartError = this._storyFilmStartReason(") == 2, (
        "ein Startpfad zeigt weiter die geratene Ursache"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Film start logging tests passed.")


if __name__ == "__main__":
    main()
