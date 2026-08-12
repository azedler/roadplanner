"""What Lyria costs, and what guarantees it never costs anything by accident.

This is the first thing in Roadplanner that spends money when it runs.
Two properties therefore matter more than anything about the music
itself, and neither is something a docstring can promise.

**Nothing renders its way into a bill.** There must be no path from
"export a film" to "call Lyria". The film reads a cached file; if none
exists it has no music. Generation is a separate, deliberate action.

**The same trip asks for the same file.** A cache key derived from the
brief is what makes a second render free - and what makes two renders of
the same trip sound the same, for the same reason the camper is stored
rather than regenerated.

The response parsing is checked here too, because it is the one part
written from published documentation rather than from a call that was
actually made: it has to survive the nesting being one level different
from what was expected.
"""

from __future__ import annotations

import ast
import base64
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_spec = importlib.util.spec_from_file_location("lyria", SOURCE / "trip_film_lyria.py")
lyria = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lyria)


def _code(path: Path) -> str:
    """Source with comments and docstrings removed.

    A rule about what the code does must not be satisfiable by prose that
    happens to mention the forbidden name.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def verify_the_film_export_never_reaches_lyria() -> None:
    """The export may read a cached track and nothing else.

    Checked as a rule about the source rather than by calling anything,
    because the failure it guards against is a future edit: somebody
    wiring "generate if missing" into the export would make every render
    of every trip cost money, and it would look like a convenience.
    """
    export = _code(SOURCE / "trip_film_export.py")
    package = _code(SOURCE / "trip_film_package.py")
    for name in ("trip_film_lyria", "LYRIA", "lyria"):
        assert name not in export, (
            f"Der Filmexport darf {name} nicht kennen - sonst kostet jeder Render Geld"
        )
        assert name not in package, f"Das Filmpaket darf {name} nicht kennen"


def verify_generating_is_its_own_deliberate_action() -> None:
    """The panel action for music generation is separate from rendering."""
    panel = _code(SOURCE / "panel.py")
    assert "story_film_music_generate" in panel, "Musik erzeugen braucht eine eigene Aktion"
    # And it must not be reachable from the action that submits a film.
    submit = panel.split("story_film_submit", 1)
    if len(submit) > 1:
        window = submit[1][:1500]
        assert "music_generate" not in window, (
            "Der Filmauftrag darf keine Musikgenerierung ausloesen"
        )


def verify_the_same_trip_asks_for_the_same_file() -> None:
    """A second render must be free, and must sound the same."""
    trip = {"title": "Skandinavien 2026", "chapter_count": 23}
    narrative = {"motifs": ["Wasser", "lange Abende"]}
    brief = lyria.brief_from_trip(trip, narrative)
    again = lyria.brief_from_trip(trip, narrative)
    assert brief == again

    key = lyria.cache_key(brief, lyria.build_prompt(brief))
    assert key == lyria.cache_key(again, lyria.build_prompt(again))
    assert lyria.track_filename(key) == lyria.track_filename(key)

    # A different journey is different music, which is the point of
    # deriving the brief from the trip rather than from a mood list.
    other = lyria.brief_from_trip(trip, {"motifs": ["Regen"]})
    assert lyria.cache_key(other, lyria.build_prompt(other)) != key

    # Nothing typed reaches a path.
    for bad in ("../../etc/passwd", "", "zzzz", "0" * 15):
        try:
            lyria.track_filename(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} haette abgelehnt werden muessen")


def verify_the_brief_stays_instrumental_and_quiet() -> None:
    """The style is a property of the film, not a preference."""
    brief = lyria.brief_from_trip({"title": "x", "chapter_count": 3}, {"motifs": ["Wald"]})
    prompt = lyria.build_prompt(brief)
    for wanted in ("warm", "nordisch", "verspielt", "zurückhaltend", "Instrumentale"):
        assert wanted in prompt, f"Der Musikbrief muss {wanted} verlangen"
    for refused in ("Gesang", "Trailer", "Schlagzeug im Vordergrund"):
        assert refused in prompt, f"Der Musikbrief muss {refused} ausschliessen"
    # The trip's own motifs travel, so the music is about this journey.
    assert "Wald" in prompt


def verify_the_cost_is_named_before_anything_is_generated() -> None:
    """Asking somebody to approve a cost without naming one is not asking."""
    brief = lyria.brief_from_trip({"title": "x", "chapter_count": 3}, {})
    notice = lyria.cost_notice(brief)
    assert notice["estimated_cost_eur"] > 0
    assert notice["model"] == lyria.LYRIA_MODEL
    assert notice["seconds"] > 0
    assert "Schätzwert" in notice["note"], "Der Preis muss als Schaetzung gekennzeichnet sein"
    assert notice["reusable"] is True
    assert notice["prompt_preview"], "Der Nutzer soll sehen, was bestellt wird"


def verify_the_audio_is_found_wherever_it_is_nested() -> None:
    """Written from documentation, so it must not depend on the nesting.

    The one part of this integration that could not be tried against the
    live service. A fixed path would be a guess; searching for a block
    that says it is audio is not, and nothing else in a response claims
    to be.
    """
    blob = b"ID3\x04payload"
    encoded = base64.b64encode(blob).decode()
    shapes = [
        {"steps": [{"model_output": {"content": [
            {"type": "text", "text": "lyrics"},
            {"type": "audio", "mime_type": "audio/mp3", "data": encoded},
        ]}}]},
        {"output": [{"type": "audio", "mimeType": "audio/wav", "data": encoded}]},
        {"a": {"b": {"c": [{"type": "audio", "data": encoded}]}}},
    ]
    for shape in shapes:
        found = lyria.audio_from_response(shape)
        assert found is not None, f"Audio nicht gefunden in {shape}"
        assert found[0] == blob

    # And nothing invented when there is none.
    for empty in ({}, {"steps": []}, {"steps": [{"model_output": {"content": []}}]}, None):
        assert lyria.audio_from_response(empty) is None
    # A block that is not audio is never mistaken for one.
    assert lyria.audio_from_response({"type": "image", "data": encoded}) is None


def verify_the_request_targets_vertex_where_lyria_lives() -> None:
    """The endpoint, and the assumption that used to be enforced here.

    This check previously read "Gemini rather than Vertex: one
    authentication story, not two", and asserted that `aiplatform` did
    NOT appear in the URL. It was not merely agreeing with a mistake, it
    was holding it in place: Lyria is a Vertex model and the Gemini
    Developer API does not serve it, so the request this test protected
    could never have succeeded. It had simply never been made.

    Convenience is not a reason an API works. Vertex does need a project
    and a service account, and that is the price of the feature.
    """
    url, body = lyria.build_request("warm und ruhig", project="reise-film-2026")
    assert "aiplatform.googleapis.com" in url, url
    assert "generativelanguage" not in url, (
        "die Gemini-API bedient Lyria nicht - dieser Aufruf kann nicht gelingen"
    )
    assert url.endswith(f"/{lyria.LYRIA_MODEL}:predict"), url
    assert "/projects/reise-film-2026/" in url, url
    # The Vertex prediction shape, not the Gemini one.
    assert body["instances"][0]["prompt"] == "warm und ruhig"
    assert body["parameters"]["sampleCount"] == 1
    # A section asks for its own length, so a track cannot come back
    # shorter than the plan expects and leave its end silent.
    _url, sized = lyria.build_request("x", project="reise-film-2026", seconds=149)
    assert sized["instances"][0]["duration_seconds"] == 149
    # Never more than one call can deliver, whatever is asked for.
    _url, capped = lyria.build_request("x", project="reise-film-2026", seconds=9999)
    assert capped["instances"][0]["duration_seconds"] == lyria.LYRIA_TRACK_SECONDS

    try:
        lyria.build_request("   ", project="reise-film-2026")
    except lyria.LyriaError:
        pass
    else:
        raise AssertionError("Ein leerer Prompt haette abgelehnt werden muessen")
    # A project id ends up in a URL. It is not somewhere for free text.
    for bad in ("", "X", "../etc", "a" * 40, "Projekt Name"):
        try:
            lyria.build_request("warm", project=bad)
        except lyria.LyriaError:
            continue
        raise AssertionError(f"{bad!r} wurde als Projekt-ID akzeptiert")


def verify_a_vertex_answer_is_read_at_all() -> None:
    """The shape the endpoint this module now calls actually returns.

    The reader knew `data`, which is the Gemini name. Vertex answers
    `:predict` with `predictions` carrying `bytesBase64Encoded` - so
    against the real endpoint it would have found nothing, thrown away a
    generation that had already been billed, and reported "keine
    Audiodaten zurückgegeben".
    """
    import base64 as _base64

    blob = b"ID3 fake audio"
    encoded = _base64.b64encode(blob).decode("ascii")
    found = lyria.audio_from_response(
        {"predictions": [{"bytesBase64Encoded": encoded, "mimeType": "audio/mp3"}]}
    )
    assert found is not None, "eine Vertex-Antwort wird nicht gelesen"
    assert found[0] == blob
    # Vertex often omits the mime type; there is nothing else in that
    # response those bytes could be.
    bare = lyria.audio_from_response({"predictions": [{"bytesBase64Encoded": encoded}]})
    assert bare is not None and bare[0] == blob


for check in (
    verify_the_film_export_never_reaches_lyria,
    verify_generating_is_its_own_deliberate_action,
    verify_the_same_trip_asks_for_the_same_file,
    verify_the_brief_stays_instrumental_and_quiet,
    verify_the_cost_is_named_before_anything_is_generated,
    verify_the_audio_is_found_wherever_it_is_nested,
    verify_the_request_targets_vertex_where_lyria_lives,
    verify_a_vertex_answer_is_read_at_all,
):
    check()

print("Lyria music tests passed.")
