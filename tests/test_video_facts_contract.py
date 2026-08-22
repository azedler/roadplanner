"""Every measured fact the integration reads survives the crossing.

Live finding M-1 (#378), measured on 4.120.0 / add-on 0.30.0: the mux
wrote `has_audible_audio` into its result, `player_film` read exactly
that name back - and the card still said "ohne Musik" over a soundtrack
peaking at -7,1 dBFS. Between writer and reader sits `_video_facts()`, a
strict whitelist, and the field was not in it. Everything the renderer
adds that the whitelist does not name is dropped silently.

    renderer_app_result.video -> has_audible_audio: MISSING

The test that was supposed to cover this read the writer and the reader
and never the pipe between them - the project's own failure pattern 1,
"one thing in two deployables, one side raised", with a field instead of
a number. So this test does not check a field. It checks the BOUNDARY:

  * every fact the renderer writes is either carried or deliberately
    dropped, with the reason written down here, and
  * every key the integration reads off a video-facts dict is one the
    whitelist actually carries.

A new renderer fact therefore fails this test until somebody decides
which of the two it is.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "roadplanner_mcp"
RENDER = ROOT / "apps" / "roadplanner_renderer" / "src" / "render.mjs"

PROTOCOL_SOURCE = (PACKAGE / "renderer_app_protocol.py").read_text(encoding="utf-8")
RENDER_SOURCE = RENDER.read_text(encoding="utf-8")

# Facts the renderer measures that deliberately do NOT cross, each with
# the reason. Being on this list is a decision, not an oversight - which
# is the whole point of the list.
DELIBERATELY_DROPPED = {
    "has_audio": (
        "a stream exists, which is true of every Remotion render including "
        "the silent ones; the audible answer is has_audible_audio"
    ),
    "has_music": (
        "the renderer's flag means 'music travelled in the package', not "
        "'the file can be heard' - two questions, one name, and the panel "
        "asks the second one"
    ),
    "package_bytes": "how much arrived, not what came out",
    "mapped_chapters": "render-internal bookkeeping",
    "crew_count": "render-internal bookkeeping",
    "character_assets": "render-internal bookkeeping",
    "clip_count": "render-internal bookkeeping",
    "music_sections": "the mux reports its own workings; the ledger keeps those",
    "music_volume": "as above",
    "music_variant": "read from the result by the client, not from video facts",
    "music_target_lufs": "as above",
    "music_premix_lufs": "as above",
    "music_gain_db": "as above",
    "music_measured_lufs": "as above",
    "music_loudness_matched": "as above",
    "music_measured_lra": "as above",
    "music_true_peak_dbfs": "as above",
    "measured_seconds": (
        "the mux's own note of the length it fitted the score to; the same "
        "number crosses as duration_seconds, measured on the finished file"
    ),
}


def whitelist_keys() -> set[str]:
    """The keys `_video_facts()` actually returns, read off the function."""
    tree = ast.parse(PROTOCOL_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_video_facts":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Dict):
                    return {
                        key.value
                        for key in inner.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
    raise AssertionError("_video_facts hat kein Rueckgabe-Dictionary mehr")


def renderer_keys() -> set[str]:
    """Every key the renderer writes into a file's video facts.

    Three shapes, because the renderer has three of them: what `probe()`
    returns, what each job assigns onto `result.facts`, and the spread
    literals the review copy and the mux build.
    """
    keys: set[str] = set()

    probe_body = RENDER_SOURCE.split("export async function probe(file) {", 1)[1]
    probe_return = probe_body.split("return {", 1)[1].split("\n}", 1)[0]
    keys |= set(re.findall(r"^\s{4}([a-z_][a-z0-9_]*):", probe_return, re.MULTILINE))

    keys |= set(re.findall(r"\bresult\.facts\.([a-z_][a-z0-9_]*)\s*=", RENDER_SOURCE))

    for literal in re.findall(
        r"\n {4}facts: \{\n(.*?)\n {4}\},", RENDER_SOURCE, re.DOTALL
    ):
        keys |= set(re.findall(r"^ {6}([a-z_][a-z0-9_]*):", literal, re.MULTILINE))

    assert len(keys) > 15, f"die Faktenschluessel wurden nicht gefunden: {sorted(keys)}"
    return keys


class _Reads(ast.NodeVisitor):
    """`.get("x")` on anything that is a video-facts dict, per function."""

    def __init__(self) -> None:
        self.keys: set[str] = set()
        self._names: set[str] = set()

    def visit_Assign(self, node: ast.Assign) -> None:
        if _is_video_block(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._names.add(target.id)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            receiver = func.value
            named = isinstance(receiver, ast.Name) and receiver.id in self._names
            if named or _is_video_block(receiver):
                self.keys.add(node.args[0].value)
        self.generic_visit(node)


def _is_video_block(node: ast.AST) -> bool:
    """`(result or {}).get("video") or {}` in any of its spellings."""
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        return any(_is_video_block(value) for value in node.values)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "video"
    ):
        return True
    return False


def integration_reads() -> dict[str, set[str]]:
    """What each consumer reads out of a video-facts dict.

    Found rather than listed: a function that opens the video block, or
    one that asks `_result_facts()` for it - that helper IS the block,
    read straight off the renderer's result file.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if '.get("video")' not in source and "_result_facts(" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            segment = ast.get_source_segment(source, node) or ""
            opens_block = '.get("video")' in segment
            asks_helper = "_result_facts(" in segment and "def _result_facts" not in segment
            if not (opens_block or asks_helper):
                continue
            reader = _Reads()
            if asks_helper:
                for inner in ast.walk(node):
                    if (
                        isinstance(inner, ast.Assign)
                        and isinstance(inner.value, ast.Call)
                        and isinstance(inner.value.func, ast.Attribute)
                        and inner.value.func.attr == "_result_facts"
                    ):
                        for target in inner.targets:
                            if isinstance(target, ast.Name):
                                reader._names.add(target.id)
            reader.visit(node)
            reader.keys.discard("video")
            if reader.keys:
                found[f"{path.name}:{node.name}"] = reader.keys
    assert found, "kein Leser der Videofakten gefunden - der Scan greift nicht mehr"
    return found


def verify_every_renderer_fact_is_carried_or_dropped_on_purpose() -> None:
    written = renderer_keys()
    carried = whitelist_keys()
    unaccounted = written - carried - set(DELIBERATELY_DROPPED)
    assert not unaccounted, (
        "Der Renderer schreibt Felder, ueber die niemand entschieden hat: "
        f"{sorted(unaccounted)}. Entweder in _video_facts() aufnehmen oder "
        "hier mit Grund als bewusst verworfen eintragen."
    )


def verify_the_dropped_list_has_no_stale_entries() -> None:
    written = renderer_keys()
    stale = set(DELIBERATELY_DROPPED) - written
    assert not stale, f"nicht mehr geschrieben, aber noch als verworfen gefuehrt: {sorted(stale)}"
    both = set(DELIBERATELY_DROPPED) & whitelist_keys()
    assert not both, f"gleichzeitig getragen und verworfen: {sorted(both)}"


def verify_everything_the_integration_reads_actually_crosses() -> None:
    """The bug itself: a reader naming a key the whitelist throws away."""
    carried = whitelist_keys()
    missing: dict[str, list[str]] = {}
    for where, keys in integration_reads().items():
        gone = sorted(keys - carried)
        if gone:
            missing[where] = gone
    assert not missing, (
        "Diese Leser fragen nach Feldern, die _video_facts() verwirft - sie "
        f"koennen nie ankommen: {missing}"
    )


def verify_the_audible_measurement_is_one_of_them() -> None:
    """The finding, named. The two above would catch it; this one says so."""
    carried = whitelist_keys()
    assert "has_audible_audio" in carried, (
        "genau das Feld, dessen Fehlen einen -7,1-dBFS-Film als 'ohne Musik' meldete"
    )
    assert "audio_peak_dbfs" in carried, "die Zahl hinter der Antwort faehrt mit"
    assert "has_audio" not in carried, (
        "die blosse Existenz einer Tonspur darf nicht mitfahren - sie ist bei "
        "jedem stummen Remotion-Render wahr"
    )


def verify_an_unmeasured_film_is_not_reported_as_silent() -> None:
    """`bool(None)` is `False`, and False here is a claim about the file."""
    protocol = PROTOCOL_SOURCE
    assert 'return value if isinstance(value, bool) else None' in protocol, (
        "die Weitergabe macht aus 'nicht gemessen' wieder ein 'nein'"
    )
    player = (PACKAGE / "player_film.py").read_text(encoding="utf-8")
    assert 'bool(facts.get("has_audible_audio"))' not in player, (
        "bool(None) ist False - eine ungemessene Datei wuerde wieder als stumm gemeldet"
    )
    assert 'isinstance(audible, bool)' in player, (
        "der dritte Wert muss beim Leser ankommen duerfen"
    )


CHECKS = [
    verify_every_renderer_fact_is_carried_or_dropped_on_purpose,
    verify_the_dropped_list_has_no_stale_entries,
    verify_everything_the_integration_reads_actually_crosses,
    verify_the_audible_measurement_is_one_of_them,
    verify_an_unmeasured_film_is_not_reported_as_silent,
]


def verify_every_check_in_this_module_is_registered() -> None:
    declared = {
        name
        for name, value in globals().items()
        if name.startswith("verify_") and callable(value)
        and name != "verify_every_check_in_this_module_is_registered"
    }
    registered = {check.__name__ for check in CHECKS}
    assert declared == registered, f"nicht ausgefuehrt: {sorted(declared - registered)}"


if __name__ == "__main__":
    verify_every_check_in_this_module_is_registered()
    for check in CHECKS:
        check()
        print(f"ok - {check.__name__}")
    print(f"\n{len(CHECKS)} checks passed")
