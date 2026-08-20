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


def verify_every_paid_entry_point_has_a_declared_action() -> None:
    """An undeclared paid action is a paid button that looks free.

    The live audit found a dozen of them at once, so the rule is now
    written at the level that cannot drift: the provider ENTRY POINTS.
    Every `if action == ...` branch in the dispatcher whose body reaches
    one of these methods must have its action declared with COST_MODEL.
    A new paid feature then fails this test until its line exists in
    panel_action_costs.py - which is the moment its button gets honest.
    """
    # Methods that bill a provider when awaited. `async_start` is NOT
    # here although the video export uses it: remotion_test_render owns a
    # free method of the same name, so the export is pinned by action
    # name below instead.
    paid_methods = {
        "async_chat",
        "async_prepare_review",
        "async_briefing",
        "async_test",
        "async_add_location_drafts",
        "async_add_trip_location_drafts",
        "async_lookup",
        "async_lookup_page",
        "async_prepare_place_enrichment",
        "async_analyze",
        "async_run_system_check",
        "async_generate",
    }
    paid_by_name = {"export_trip_video"}

    tree = ast.parse((SOURCE / "panel.py").read_text(encoding="utf-8"))
    dispatcher = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_action"
    )
    model_declared = {
        action
        for action, entry in costs.ACTION_COSTS.items()
        if entry["cost"] == costs.COST_MODEL
    }
    problems = []
    for node in ast.walk(dispatcher):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)):
            continue
        actions = {
            constant.value
            for constant in ast.walk(node.test)
            if isinstance(constant, ast.Constant) and isinstance(constant.value, str)
        }
        if not actions:
            continue
        used = {
            attr.attr for attr in ast.walk(node) if isinstance(attr, ast.Attribute)
        } | {name.id for name in ast.walk(node) if isinstance(name, ast.Name)}
        paid = bool(used & paid_methods) or bool(actions & paid_by_name)
        if not paid:
            continue
        for action in actions:
            if action not in model_declared:
                problems.append(
                    f"{action} erreicht einen bezahlten Provider "
                    f"({sorted(used & paid_methods) or 'per Namensliste'}), "
                    "ist aber nicht als COST_MODEL deklariert - der Knopf "
                    "sieht gratis aus"
                )
    assert not problems, "\n".join(problems)


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
    verify_every_paid_entry_point_has_a_declared_action,
    verify_the_free_rebuild_button_is_gone,
):
    check()

print("Panel action cost tests passed.")
