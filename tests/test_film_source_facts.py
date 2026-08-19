"""The three facts about the source film must move together.

Which job made the film, whether it is an excerpt, and whether it carries
a soundtrack are one description of one file. They were set separately,
and the soundtrack answer was written in exactly ONE place - the
restore-after-reload path. Every render started inside a session moved
the first two on and left the third describing the previous film.

The visible consequence: somebody mixed the comparison fassungen onto a
silent excerpt, then rendered a new excerpt with a track selected. The
panel still believed the source was silent, kept the three fassung
buttons up, and the renderer refused what the panel had just offered -
"Dieser Film hat bereits eine Tonspur" (live report). The refusal was
correct; the offer was not.

Two checks, and the second one is the one that matters:

1. The source is set as a unit. `_storyFilmSourceJobId` may only be
   assigned inside `_storyFilmSetSource`, which also clears the
   soundtrack answer - so a fourth caller cannot forget the third field,
   because there is nothing left to forget.

2. The soundtrack answer comes from the names the renderer REALLY writes.
   This project has written a test against invented field names before
   (`zeigt`/`motive` where the data said `motifs`/`shows`), and that test
   agreed with itself while the bug went on living. So `video` and
   `has_audio` are read out of the renderer's own source and the panel's
   own reader, and only then compared with what the browser looks for.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
FEATURES = INTEGRATION / "frontend" / "features"
STORY = FEATURES / "story-editor.js"
APP = FEATURES / "renderer-app.js"
RENDER = ROOT / "apps" / "roadplanner_renderer" / "src" / "render.mjs"
CLIENT = INTEGRATION / "renderer_app_client.py"


def _code(text: str) -> str:
    """The body without its comments.

    A check that trips over the prose EXPLAINING the fix is worse than no
    check. It has happened three times in this repository, so the comment
    below - which names both fields on purpose - must not be able to
    satisfy the checks that follow.
    """
    without_blocks = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in without_blocks.splitlines())


def _function(source: str, name: str) -> str:
    """One method body, by its name, from a feature mixin."""
    match = re.search(rf"\n  (?:async )?{re.escape(name)}\((.*?)\n  \}},", source, re.S)
    assert match, f"{name} gibt es nicht mehr"
    return match.group(1)


def verify_the_source_is_set_as_one_unit() -> None:
    """Nobody assigns the job id beside the facts that describe it."""
    story = _code(STORY.read_text(encoding="utf-8"))
    setter = _function(story, "_storyFilmSetSource")
    for field in (
        "_storyFilmSourceJobId",
        "_storyFilmSourceIsExcerpt",
        "_storyFilmSourceHasAudio",
    ):
        assert f"this.{field} =" in setter, (
            f"_storyFilmSetSource setzt {field} nicht mehr - genau so blieb "
            "eine Auskunft über den vorherigen Film stehen"
        )
    # Unknown, not inherited: a film nobody has measured is not a silent
    # film, and only the guess would offer a mix that cannot work.
    assert "this._storyFilmSourceHasAudio = undefined" in setter, setter

    # And every other assignment goes through it.
    assignments = [
        line
        for line in story.splitlines()
        if re.search(r"this\._storyFilmSourceJobId\s*=", line)
    ]
    outside = [line for line in assignments if "_storyFilmSetSource" not in line]
    inside_setter = [
        line for line in outside if re.search(r"this\._storyFilmSourceJobId = jobId", line)
    ]
    stray = [line for line in outside if line not in inside_setter]
    assert not stray, (
        "die Quelle wird an einer Stelle gesetzt, die die Tonspur-Auskunft "
        f"nicht mitzieht: {[line.strip() for line in stray]}"
    )


def verify_the_soundtrack_answer_uses_the_real_field_names() -> None:
    """`video.has_audio` - read off the renderer, not invented here."""
    # What the renderer measures.
    render = _code(RENDER.read_text(encoding="utf-8"))
    assert re.search(r"has_audio:\s*\(parsed\.streams", render), (
        "der Renderer misst die Tonspur nicht mehr als has_audio"
    )
    # Where it puts it: the result's `video` block, written by the app.
    app_source = (ROOT / "apps" / "roadplanner_renderer" / "src" / "index.mjs").read_text(
        encoding="utf-8"
    )
    assert "video: facts" in app_source, (
        "die gemessenen Fakten stehen nicht mehr unter video im Ergebnis"
    )

    # The measured one, written beside it. `has_audio` says a stream
    # exists, which is true of every Remotion render including the empty
    # ones - reading it as "this film has music" refused every silent
    # excerpt there is. The audible answer comes from a meter.
    assert "has_audible_audio" in render, (
        "der Renderer misst nicht mehr, ob der fertige Film hörbar ist"
    )
    assert "measureVolume" in render, render[:0] or "die Pegelmessung ist weg"

    # And what the integration reads back out of that same block.
    client = CLIENT.read_text(encoding="utf-8")
    assert 'found.get("has_audible_audio")' in client, (
        "der Client liest wieder die blosse Existenz eines Streams"
    )
    assert '.get("video")' in client, "der Client liest den video-Block nicht mehr"

    # Only now: what the browser looks for.
    story = _code(STORY.read_text(encoding="utf-8"))
    measured = _function(story, "_storyFilmSourceMeasured")
    assert ".video" in measured, measured
    assert "has_audio" in measured, measured
    # A boolean or nothing. `Number(null)` is 0 and `undefined` is falsy -
    # this project has already shipped an absent measurement that arrived
    # as a plausible value and was acted on.
    assert 'typeof found.has_audio !== "boolean"' in measured, (
        "eine fehlende Messung darf nicht als 'stumm' durchgehen"
    )
    # About THIS source, not about whichever job happened to finish.
    assert "this._storyFilmSourceJobId" in measured, measured


def verify_a_finished_job_actually_answers_it() -> None:
    """The measurement is taken, not merely takeable.

    A panel that never called this would pass both checks above: the
    setter clears the field, the reader is correct, and the answer stays
    unknown forever - which is exactly the state that offers a mix onto a
    film nobody measured.
    """
    app = _code(APP.read_text(encoding="utf-8"))
    poll = _function(app, "_pollRendererAppJob")
    assert "_storyFilmSourceMeasured" in poll, (
        "der fertige Auftrag wird nicht mehr nach seiner Tonspur gefragt"
    )
    assert "renderer_app_result" in poll, poll


def main() -> None:
    for name, function in sorted(globals().items()):
        if name.startswith("verify_") and callable(function):
            function()
    print("Film source facts tests passed.")


if __name__ == "__main__":
    main()
