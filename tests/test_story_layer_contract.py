"""The story layer's boundaries, seen across files.

The manifest exists so that PDF and video stop describing a trip
differently. That only holds if two things stay true, and neither is
visible in a single file: the layer must be reachable, and it must not
start doing the work that was deliberately left out of this step.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(".")
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"


def _code(path: Path) -> str:
    """Source without comments or docstrings.

    Prose about a thing is not the thing. A module whose docstring says it
    uses no random choice would otherwise fail a check for "random" - which
    has happened often enough in this repository to be worth solving once.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    # ast.unparse normalises string quotes to single ones; normalise the
    # other way so the expectations below can be written as they appear in
    # the source.
    return ast.unparse(tree).replace("'", '"')


def _imports(path: Path) -> set[str]:
    """The top-level module names a file actually imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def verify_the_story_layer_is_reachable() -> None:
    wiring = _code(INTEGRATION / "__init__.py")
    assert "StoryContextBuilder" in wiring and "story_context=story_context" in wiring
    panel = _code(INTEGRATION / "panel.py")
    assert '"story_manifest"' in panel, "die Aktion muss registriert sein"
    assert 'if action == "story_manifest"' in panel, "die Aktion braucht einen Zweig"


def verify_reading_a_manifest_is_not_an_edit() -> None:
    """It writes nothing, so it must not demand write rights."""
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"story_manifest"' not in edit_block


def verify_the_builder_only_reads() -> None:
    """A generator here would make the manifest neither deterministic nor free."""
    builder = _code(INTEGRATION / "story_context_builder.py")
    for forbidden in (
        "async_generate_text",
        "async_media_redirect_url",
        "async_get_clientsession",
        "async_download_photo",
        "async_save",
        "async_update_day",
    ):
        assert forbidden not in builder, f"der Builder darf {forbidden} nicht benutzen"


def verify_the_manifest_module_stays_pure() -> None:
    """It has to be testable without Home Assistant, and stay that way.

    Checked by what it imports rather than by what it mentions: purity is
    a property of the dependency list, and a clock or a random source
    cannot appear without one.
    """
    allowed = {"__future__", "hashlib", "json", "re", "typing"}
    actual = _imports(INTEGRATION / "travel_story_manifest.py")
    assert actual <= allowed, f"unerlaubte Importe im Manifestmodul: {sorted(actual - allowed)}"
    code = _code(INTEGRATION / "travel_story_manifest.py")
    for forbidden in ("open(", "datetime.now", "time.time"):
        assert forbidden not in code, f"das Manifestmodul darf {forbidden} nicht enthalten"


def verify_the_deferred_work_has_not_crept_in() -> None:
    """Everything this step was told to leave alone, checked as absent."""
    imported = _imports(INTEGRATION / "story_context_builder.py") | _imports(
        INTEGRATION / "travel_story_manifest.py"
    )
    relative = _code(INTEGRATION / "story_context_builder.py")
    for deferred in ("map_snapshot", "trip_music", "trip_video", "trip_pdf", "gemini_client"):
        assert deferred not in imported, f"{deferred} gehoert nicht in diesen Schritt"
        assert f"from .{deferred}" not in relative, f"{deferred} gehoert nicht in diesen Schritt"


def verify_the_existing_exports_were_not_rebuilt() -> None:
    """The manifest has no consumers yet, on purpose.

    Wiring it into the PDF or the video export in the same step would mean
    changing two working features to prove one new structure - and a
    regression there could not be told apart from a fault in the manifest.
    """
    for name in ("trip_pdf_export.py", "trip_video_export.py", "trip_day_mini_export.py"):
        source = _code(INTEGRATION / name)
        assert "travel_story_manifest" not in source, f"{name} wurde umgebaut"
        assert "story_context" not in source, f"{name} wurde umgebaut"


def verify_the_manifest_carries_a_version_and_a_hash() -> None:
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert "MANIFEST_VERSION = 1" in manifest
    assert "def content_hash" in manifest and "def validate_manifest" in manifest
    # A version that nothing refuses is not a version.
    assert "Nicht unterstützte Manifestversion" in manifest


verify_the_story_layer_is_reachable()
verify_reading_a_manifest_is_not_an_edit()
verify_the_builder_only_reads()
verify_the_manifest_module_stays_pure()
verify_the_deferred_work_has_not_crept_in()
verify_the_existing_exports_were_not_rebuilt()
verify_the_manifest_carries_a_version_and_a_hash()
print("Story layer contract tests passed.")
