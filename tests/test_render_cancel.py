"""Stopping a render, and why it is not a failure.

A film render runs for about an hour. Until now the only way out was
restarting the add-on - which works, and which nobody finds. Worse, a
Home Assistant restart looks like it should help and does nothing at
all: the render is in another container and never hears about it.

Two things decide whether this feature is any good, and both are checked
here rather than assumed:

**A cancel carries no data.** It is a file named after the job and
nothing else. There is no path, no filename, no free text - so there is
nothing in the request that could be wrong except the name, and the name
is matched against the same job-id pattern as everything else in this
protocol. That is a stronger guarantee than sanitising a string.

**A cancel is its own outcome.** Reporting "I stopped it" as "it failed"
sends somebody looking for a cause that does not exist, and makes the
reasonable next move - press it again - look like the fix for a bug.
This project has already paid for that shape twice: an absent answer
rendered as a state.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer" / "src"


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", INTEGRATION / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("renderer_app_protocol")
WORKER = (APP / "index.mjs").read_text(encoding="utf-8")
RENDER = (APP / "render.mjs").read_text(encoding="utf-8")
APP_PROTOCOL = (APP / "protocol.mjs").read_text(encoding="utf-8")


def verify_both_sides_know_the_cancelled_state() -> None:
    """A state one side can write and the other cannot read is a hang."""
    assert protocol.JOB_CANCELLED == "cancelled"
    assert protocol.JOB_CANCELLED in protocol.JOB_STATES
    assert protocol.JOB_CANCELLED in protocol.TERMINAL_JOB_STATES

    states = re.search(r"export const JOB_STATES = \[(.*?)\];", APP_PROTOCOL, re.S)
    assert states and '"cancelled"' in states.group(1), (
        "die App kennt den Zustand nicht - ein abgebrochener Auftrag bliebe "
        "für das Panel für immer 'läuft'"
    )
    terminal = re.search(
        r"export const TERMINAL_JOB_STATES = new Set\(\[(.*?)\]\)", APP_PROTOCOL, re.S
    )
    assert terminal and '"cancelled"' in terminal.group(1), (
        "abgebrochen muss terminal sein, sonst kann ein später Statusschreiber "
        "den Auftrag wiederbeleben"
    )


def verify_cancelling_is_not_reported_as_a_failure() -> None:
    """The distinction this whole file exists for."""
    assert 'err.code === "CANCELLED"' in WORKER, (
        "der Worker unterscheidet Abbruch nicht von Fehlschlag"
    )
    assert 'state = "cancelled"' in WORKER
    # And the panel says it as a decision rather than a defect.
    editor = (
        INTEGRATION / "frontend" / "features" / "story-editor.js"
    ).read_text(encoding="utf-8")
    assert 'job.state === "cancelled"' in editor, (
        "die Filmkarte behandelt einen Abbruch wie einen Fehlschlag"
    )
    assert "Es ist nichts kaputt" in editor


def verify_the_request_carries_nothing_but_a_job_id() -> None:
    """Nothing in a cancel can point anywhere."""
    client = (INTEGRATION / "renderer_app_client.py").read_text(encoding="utf-8")
    body = client.split("def _write_cancel(", 1)
    assert len(body) == 2, "_write_cancel gibt es nicht mehr"
    body = body[1].split("\n    def ", 1)[0]
    # The id is validated before the path is built - in the caller, which
    # is the only place that has it.
    caller = client.split("async def async_cancel_job(", 1)[1].split("\n    def ", 1)[0]
    assert "validate_job_id(job_id)" in caller, (
        "die Job-ID wird nicht geprüft, bevor daraus ein Pfad wird"
    )
    # Nothing else travels. An empty JSON object is the whole payload.
    assert 'b"{}\\n"' in body, body


def verify_a_cancel_cannot_name_anything_else() -> None:
    """Every non-job-id is refused before a path exists."""
    for bad in ("", "..", "../../etc/passwd", "roadplanner-trip-film.mp4", "x" * 36):
        try:
            protocol.validate_job_id(bad)
        except protocol.RendererProtocolError:
            continue
        raise AssertionError(f"{bad!r} wurde als Job-ID akzeptiert")


def verify_the_worker_asks_between_frames() -> None:
    """Polled where the work is, not watched from somewhere else."""
    assert "async function cancelRequested(" in WORKER
    assert "() => cancelRequested(jobId)" in WORKER, (
        "die laufenden Aufträge bekommen die Abbruchprüfung nicht übergeben"
    )
    # The render takes the exit that already exists for the timeout, so
    # there is one cleanup path rather than a second one to keep correct.
    assert 'new RenderError("CANCELLED"' in RENDER
    assert "failDeadline" in RENDER


def verify_a_cancelled_render_leaves_nothing_behind() -> None:
    """Temporary files, inputs and the marker itself all go."""
    # The partial file is removed on every throw out of the render.
    assert "await fs.rm(partial, { force: true })" in RENDER
    # The job's photographs go when the job ends, whatever ended it.
    assert "await discardInputs(jobId);" in WORKER
    assert "await discardIncompleteResult(jobId);" in WORKER
    # And the request itself, so it cannot stop anything later.
    assert "await clearCancel(jobId);" in WORKER, (
        "der Abbruchmarker überlebt seinen Auftrag - der nächste Blick darauf "
        "würde als 'stopp' gelesen"
    )
    assert "async function cleanupOldCancels(" in WORKER


def verify_the_renderer_stays_usable_afterwards() -> None:
    """A cancel ends one job, not the app."""
    # The browser is closed in the same `finally` every other ending uses,
    # and the worker's loop is not touched: nothing here exits the process.
    assert "await browser.close().catch(() => {});" in RENDER
    cancel_block = WORKER.split('err.code === "CANCELLED"', 1)[1][:600]
    for forbidden in ("process.exit", "running = false"):
        assert forbidden not in cancel_block, (
            f"ein Abbruch darf die App nicht beenden: {forbidden}"
        )


def verify_the_review_copy_can_be_cancelled_too() -> None:
    """It re-encodes a whole film and takes minutes of its own."""
    assert "isCancelled" in RENDER
    copy_block = RENDER.split("export async function createReviewCopy(", 1)[1]
    assert 'child.kill("SIGKILL")' in copy_block, (
        "ffmpeg läuft weiter, wenn niemand es beendet"
    )
    assert 'new RenderError("CANCELLED", "Die Review-Kopie wurde abgebrochen.")' in copy_block


def verify_the_panel_can_reach_it() -> None:
    """A path that exists and cannot be pressed is not a path."""
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    assert '"renderer_app_cancel"' in panel
    assert 'if action == "renderer_app_cancel"' in panel
    frontend = (
        INTEGRATION / "frontend" / "roadplanner-panel.js"
    ).read_text(encoding="utf-8")
    assert 'action === "renderer-app-cancel"' in frontend
    editor = (
        INTEGRATION / "frontend" / "features" / "story-editor.js"
    ).read_text(encoding="utf-8")
    assert 'data-action="renderer-app-cancel"' in editor, (
        "es gibt keinen Knopf, der das auslöst"
    )
    # Ending somebody's work is a change, so it needs the same right as
    # any other change - and it must survive a browser that goes away.
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"renderer_app_cancel"' in edit_block


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Render cancel tests passed.")


if __name__ == "__main__":
    main()
