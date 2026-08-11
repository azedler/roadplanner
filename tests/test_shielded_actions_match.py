"""One list, two deployables - so read both and compare them.

The panel shields long actions server-side: they run to completion even
when the browser's WebSocket dies, because a phone that locks its screen
must not orphan work that costs money. The frontend has to know which
actions those are, or it turns a survivable disconnect into a failure
dialog.

It had a copy of the list with a comment calling it a "Mirror of
panel.py's _PROVIDER_CALL_ACTIONS". The copy had drifted by seven
entries, all in the same direction - the longest actions in the product
were shielded on the server and unknown to the panel:

    Die Videoanalyse ist fehlgeschlagen
    Connection lost
    Aktion: media_video_analyze

That run was still going, and it finished. The user was told it had
failed, while the sensible reaction - press it again - would have paid
for the same Gemini calls a second time.

This is the project's first failure pattern, and its only real remedy:
a test that reads both files rather than writing the same list down a
third time.
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"

PANEL = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
FRONTEND = (
    INTEGRATION / "frontend" / "roadplanner-panel.js"
).read_text(encoding="utf-8")


def _backend() -> set[str]:
    match = re.search(r"_PROVIDER_CALL_ACTIONS = \{(.*?)\n\}", PANEL, re.S)
    assert match, "panel.py hat kein _PROVIDER_CALL_ACTIONS mehr"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _frontend() -> set[str]:
    match = re.search(
        r"SERVER_CONTINUING_ACTIONS = new Set\(\[(.*?)\]\)", FRONTEND, re.S
    )
    assert match, "der Panel-Code hat kein SERVER_CONTINUING_ACTIONS mehr"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def verify_both_sides_name_the_same_actions() -> None:
    """Neither direction is harmless, so both are checked."""
    backend = _backend()
    frontend = _frontend()

    # Shielded on the server, unknown to the panel: a survivable
    # disconnect is reported as a failure, and the user retries paid work.
    assert not (backend - frontend), (
        "serverseitig abgeschirmt, aber im Panel unbekannt - ein Abbruch "
        f"wird als Fehler gemeldet: {sorted(backend - frontend)}"
    )

    # Promised by the panel, not shielded on the server: the panel says
    # "Roadplanner arbeitet auf dem Server weiter" for something that was
    # cancelled with the connection. A false reassurance is worse than
    # the error dialog it replaced.
    assert not (frontend - backend), (
        "das Panel verspricht Weiterlaufen, der Server bricht ab: "
        f"{sorted(frontend - backend)}"
    )


def verify_the_longest_actions_are_covered() -> None:
    """The ones a person actually waits minutes for.

    Named individually because they are the reason the list exists, and
    because an empty intersection would otherwise satisfy the comparison
    above perfectly.
    """
    backend = _backend()
    for action in (
        "media_video_analyze",
        "story_director_run",
        "story_film_render",
        "story_film_music_generate",
        "media_curate_days",
        "export_trip_video",
    ):
        assert action in backend, action


def verify_the_analysis_reports_that_it_is_running() -> None:
    """A long action with no sign of life reads as a broken button.

    Live report: "Nach einem Klick passiert ich denke nichts." It was
    running - twenty windows, each a download, a cut and a Gemini call.
    """
    editor = (
        INTEGRATION / "frontend" / "features" / "story-editor.js"
    ).read_text(encoding="utf-8")
    assert "_storyVideoRunning" in editor, "die Karte kennt keinen Laufzustand"
    assert "data-story-video-progress" in editor, "es gibt keinen Fortschritt"
    # The progress is read from the free offer, not counted up locally:
    # a number the browser invents can disagree with what was stored.
    assert "_storyVideoWatch" in editor
    assert "media_video_offer" in editor
    # And the button is gone while the run is going, so nobody pays twice.
    assert "&& !running" in editor, "der Startknopf bleibt während des Laufs sichtbar"


for check in (
    verify_both_sides_name_the_same_actions,
    verify_the_longest_actions_are_covered,
    verify_the_analysis_reports_that_it_is_running,
):
    check()

print("Shielded action list tests passed.")
