"""Every button that spends money has to say so, and be provable.

Live complaint: "ich finde ja es gibt zu viele Schalter für
Auffrischungen". The count was nine, spread over five cards, in six
different verbs. But the count was the smaller half of the problem: the
labels described what the code does, when what matters at the moment of
pressing is whether it costs money, whether it overwrites a decision, and
whether it will take a while.

Those three are now declared once, next to each other, where they can be
compared. This checks the declaration stays true - because the failure
mode is quiet and expensive: somebody adds a paid button, words it like a
free one, and nobody notices until a bill arrives.
"""

from __future__ import annotations

import ast
from pathlib import Path
import importlib.util
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "roadplanner_mcp"
FRONTEND = SOURCE / "frontend"
sys.dont_write_bytecode = True

_spec = importlib.util.spec_from_file_location("costs", SOURCE / "panel_action_costs.py")
costs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(costs)


def _panel_actions() -> set[str]:
    """The action names the panel really accepts."""
    tree = ast.parse((SOURCE / "panel.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_ACTIONS":
                    return {
                        element.value
                        for element in ast.walk(node.value)
                        if isinstance(element, ast.Constant) and isinstance(element.value, str)
                    }
    raise AssertionError("_ACTIONS nicht gefunden")


def verify_every_declared_action_is_a_real_action() -> None:
    """A declaration for an action that does not exist explains nothing.

    Actions are written with underscores in Python and with hyphens in
    the markup; the declaration uses the Python name, and the frontend
    helper is handed the markup name. Both spellings have to land on
    something real, or a button loses its explanation the moment it is
    renamed.
    """
    actions = _panel_actions()
    for action in costs.ACTION_COSTS:
        assert action in actions, f"{action} ist keine Panel-Aktion"


def verify_every_declaration_says_what_changes() -> None:
    for action, entry in costs.ACTION_COSTS.items():
        assert entry["cost"] in costs.COST_CLASSES, (action, entry["cost"])
        assert entry.get("effect", "").strip(), f"{action} sagt nicht, was es bewirkt"
        # A note is not optional for the two classes where pressing has a
        # consequence somebody could regret.
        if entry["cost"] in (costs.COST_MODEL, costs.COST_RECOMPUTE):
            assert entry.get("note", "").strip(), f"{action} braucht einen Hinweis"


def verify_a_paid_action_names_its_price() -> None:
    """Not "verwendet KI" - what it uses up.

    The two words that matter are the ones a user can act on: quota, or
    money. A hint that says an action is clever tells nobody anything.
    """
    for action, entry in costs.ACTION_COSTS.items():
        if entry["cost"] != costs.COST_MODEL:
            continue
        note = entry["note"].lower()
        assert "kontingent" in note or "kostet" in note, (
            f"{action} nennt seinen Preis nicht: {entry['note']}"
        )


def verify_the_panel_sends_the_table() -> None:
    """The words a user reads and the words this checks are the same words."""
    panel = (SOURCE / "panel.py").read_text(encoding="utf-8")
    assert "action_costs" in panel, "das Panel muss die Tabelle mitschicken"
    assert "panel_action_costs" in panel


def verify_paid_buttons_go_through_the_shared_helper() -> None:
    """A hand-written paid button is a paid button that looks free.

    Checked as a rule about the markup rather than by rendering it: the
    thing that must never happen is somebody writing `<button
    data-action="media-curate-trip">` again with their own wording, which
    is exactly how the six verbs happened in the first place.
    """
    paid = {
        action.replace("_", "-")
        for action, entry in costs.ACTION_COSTS.items()
        if entry["cost"] == costs.COST_MODEL
    }
    for path in FRONTEND.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        for action in paid:
            for match in re.finditer(rf'data-action="{re.escape(action)}"', text):
                window = text[max(0, match.start() - 200) : match.start()]
                assert "actionButton(" in window or "action-button.js" in window, (
                    f"{path.name}: {action} wird von Hand gezeichnet statt über actionButton"
                )


def verify_the_free_rebuild_button_is_gone() -> None:
    """The story tab loads itself; a button asking again explains nothing.

    Same argument that removed "Reisegeschichte öffnen": building the
    chapters calls no model and costs nothing, so a button in front of it
    asks whether somebody really wants what opening the tab already
    requested. A failure still offers "Erneut versuchen", which is a
    state rather than a switch.
    """
    editor = (FRONTEND / "features" / "story-editor.js").read_text(encoding="utf-8")
    assert 'data-action="story-reload"' not in editor, (
        "der freie Neuaufbau-Knopf ist wieder da"
    )


for check in (
    verify_every_declared_action_is_a_real_action,
    verify_every_declaration_says_what_changes,
    verify_a_paid_action_names_its_price,
    verify_the_panel_sends_the_table,
    verify_paid_buttons_go_through_the_shared_helper,
    verify_the_free_rebuild_button_is_gone,
):
    check()

print("Panel action cost tests passed.")
