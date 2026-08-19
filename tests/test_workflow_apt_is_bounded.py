"""No workflow step may fail by never finishing.

`sudo apt-get update && sudo apt-get install ... ffmpeg` stood at 43
minutes without printing a line and blocked a release; the retry took 8
minutes and the next run 31 - three times in one day, on a step that
normally takes seconds. Unbounded, its failure mode is silence, which
looks exactly like slowness and is the one state nobody can act on.

The command lived in THREE workflow files, identical and copied. That is
this project's most expensive shape - one value in several deployables,
one of them changed - and fixing two of three would have left the
release path hanging while the other two looked repaired. So the rule is
checked against every workflow that installs a package, found by reading
the directory rather than from a list written down here: a list would go
stale the moment somebody adds a fourth workflow, which is the same bug
in the test.
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _steps_installing_packages() -> list[tuple[Path, str]]:
    """Every workflow step whose script calls apt-get, as raw text."""
    found: list[tuple[Path, str]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        # Split on the step marker rather than parsing YAML: what matters
        # is the text of one step, and a parsed tree would hand back the
        # script without the keys that bound it.
        for chunk in text.split("\n      - name: ")[1:]:
            if "apt-get install" in chunk:
                found.append((path, chunk))
    return found


def verify_every_package_install_has_a_deadline() -> None:
    steps = _steps_installing_packages()
    assert steps, "kein Workflow installiert mehr Pakete - dann stimmt der Test nicht mehr"
    for path, chunk in steps:
        head = chunk.split("\n        run:", 1)[0]
        assert "timeout-minutes:" in head, (
            f"{path.name}: ein apt-Schritt ohne timeout-minutes kann ewig hängen "
            "und genau das hat einen Release blockiert"
        )
        # A deadline per attempt as well. The step backstop alone would
        # spend its whole budget on one stuck mirror and never retry.
        assert "timeout " in chunk, (
            f"{path.name}: die einzelnen apt-Aufrufe haben keine eigene Zeitgrenze"
        )
        assert "for attempt in" in chunk, (
            f"{path.name}: ein einmaliger Versuch macht aus einem kurzen Aussetzer "
            "einen roten Lauf"
        )


def verify_the_copies_still_agree() -> None:
    """The same step in every file, or the difference is deliberate.

    Compared by their command text, because that is the part that
    drifted: three copies of one line, and a fix applied to one of them
    would have read as repaired everywhere.
    """
    scripts = {
        path.name: chunk.split("\n        run:", 1)[1]
        for path, chunk in _steps_installing_packages()
    }
    assert len(scripts) >= 2, scripts
    distinct = {" ".join(script.split()) for script in scripts.values()}
    assert len(distinct) == 1, (
        "die apt-Schritte unterscheiden sich wieder zwischen den Workflows: "
        f"{sorted(scripts)}"
    )


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Workflow apt bounds tests passed.")


if __name__ == "__main__":
    main()
