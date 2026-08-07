"""The story layer's boundaries, seen across files.

The manifest exists so that PDF and video stop describing a trip
differently. That only holds if two things stay true, and neither is
visible in a single file: the layer must be reachable, and it must not
start doing the work that was deliberately left out of this step.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re

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


def _js_code(path: Path) -> str:
    """JavaScript without its comments.

    `_code` parses Python; handing it a .js file raises. Comments are
    stripped for the same reason as there: a rule about a thing must not be
    satisfied by prose describing the thing.
    """
    text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


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


def load_editable_fields() -> set[str]:
    """The real constant's keys, read from the module rather than its text.

    Only the keys are literals - the values are the detail-key names, which
    are imported constants - so the keys are what is read here.
    """
    for node in ast.parse(
        (INTEGRATION / "story_override_service.py").read_text(encoding="utf-8")
    ).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "EDITABLE_FIELDS"
            for target in node.targets
        ):
            return {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError("EDITABLE_FIELDS nicht gefunden")


def verify_the_story_editor_is_wired_end_to_end() -> None:
    panel = _code(INTEGRATION / "panel.py")
    assert '"story_set_override"' in panel and 'if action == "story_set_override"' in panel
    edit_block = (INTEGRATION / "panel.py").read_text(encoding="utf-8").split(
        "_EDIT_ACTIONS = {", 1
    )[1].split("}", 1)[0]
    assert '"story_set_override"' in edit_block, "Schreiben braucht Schreibrechte"

    dispatcher = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")
    feature = (INTEGRATION / "frontend" / "features" / "story-editor.js").read_text(
        encoding="utf-8"
    )
    assert "storyEditorMixin" in dispatcher, "das Mixin muss registriert sein"
    assert '"story"' in dispatcher and "_renderStory()" in dispatcher, "der Tab fehlt"
    for hook in ("story-save", "story-reset", "story-select", "story-chapter-image"):
        assert f'action === "{hook}"' in dispatcher, f"{hook} wird nicht verteilt"
        assert f'data-action="{hook}"' in feature, f"{hook} braucht einen Knopf"


def verify_only_two_fields_are_editable() -> None:
    """The editorial layer's boundary, on both sides of the wire."""
    editable = load_editable_fields()
    assert editable == {"title", "story"}, editable
    service = _code(INTEGRATION / "story_override_service.py")
    # Whatever the client sends is checked against that set, not merged in.
    assert "set(changes) - set(EDITABLE_FIELDS)" in service
    # The editor DISPLAYS facts like distance and stops, so their names
    # appear in it. What matters is what it sends: the set of actions it
    # can call, and the two fields it may put in a change.
    feature = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    called = set(re.findall(r'_runAction\(\s*"([a-z_]+)"', feature))
    # The film lives here because it is what the story layer is FOR, but
    # the list stays closed: an action that appears without being named
    # here is a boundary being crossed quietly.
    assert called <= {
        "story_manifest",
        "story_set_override",
        "media_update_assignment",
        "story_film_preview",
        "story_film_render",
    }, f"der Editor ruft unerwartete Aktionen auf: {sorted(called)}"
    # The film takes a trip and nothing else - it cannot carry an edit.
    film_calls = re.findall(r'"story_film_\w+",\s*\{([^}]*)\}', feature)
    assert film_calls, "die Filmaufrufe wurden nicht gefunden"
    for payload in film_calls:
        assert "trip_id" in payload
        for forbidden in ("changes", "day_id", "patch"):
            assert forbidden not in payload, f"der Filmaufruf darf {forbidden} nicht senden"
    assert set(re.findall(r"changes\.([a-z_]+)\s*=", feature)) <= {"title", "story"}


def verify_typing_does_not_re_render() -> None:
    """A render per keystroke moves the caret to the end of the field."""
    feature = (INTEGRATION / "frontend" / "features" / "story-editor.js").read_text(
        encoding="utf-8"
    )
    handler = feature.split("_handleStoryInput(event) {", 1)[1].split("\n  },", 1)[0]
    assert "_render(" not in handler, "Tippen darf kein Neuzeichnen auslösen"
    assert "_storyDrafts" in handler, "der Entwurf muss ausserhalb des DOM leben"


def verify_unsaved_work_defers_a_background_refresh() -> None:
    """A refresh mid-sentence would replace the shadow DOM under the caret."""
    dispatcher = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(
        encoding="utf-8"
    )
    assert "_storyAnyDirty()" in dispatcher
    assert "this._refreshQueued && !this._dialog && !this._storyAnyDirty()" in dispatcher


def verify_the_chapter_image_reuses_the_existing_cover() -> None:
    """No second cover concept: Roadplanner already has one per day."""
    feature = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "media_update_assignment" in feature, "der vorhandene Weg wird benutzt"
    assert "is_day_cover" in feature
    for invented in ("story_cover", "chapter_cover", "preferred_media_id"):
        assert invented not in feature, f"{invented} waere eine zweite Coverlogik"
    # And no new field appears on the manifest side either.
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert "story_cover" not in manifest


def verify_the_manifest_carries_a_version_and_a_hash() -> None:
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert "MANIFEST_VERSION = 1" in manifest
    assert "def content_hash" in manifest and "def validate_manifest" in manifest
    # A version that nothing refuses is not a version.
    assert "Nicht unterstützte Manifestversion" in manifest


def verify_a_running_film_survives_a_page_reload() -> None:
    """The render outlives the page; the page has to be able to find it.

    A trip film takes a quarter of an hour, and a phone reloads Home
    Assistant whenever it feels like it. Everything the browser kept about
    the job is gone at that moment - so the job has to be re-askable from
    the exchange folder, or a render in progress becomes invisible and its
    result unreachable.
    """
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    assert "renderer_app_recent_jobs" in renderer, "der Weg zurueck zum Auftrag fehlt"
    assert "_rendererAppAdoptRunningJob" in renderer
    # The story card is where the film is started, so it is where a
    # running film has to be visible - and on the closed card too, which
    # is what a reload shows first.
    editor = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "_rendererAppAdoptOnce()" in editor
    assert editor.count("_renderStoryFilmJobLine()") >= 2
    # And the panel has to offer the action at all.
    panel = _code(INTEGRATION / "panel.py")
    assert '"renderer_app_recent_jobs"' in panel


def verify_the_poll_outlasts_a_whole_film() -> None:
    """A number of attempts is a duration wearing a disguise.

    150 attempts at two seconds looked generous until a film took fourteen
    minutes and the card stopped watching after five - a render that was
    going perfectly well, shown as abandoned.
    """
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    poll = renderer.split("_pollRendererAppJob(jobId) {", 1)[1].split("\n  },", 1)[0]
    assert "Date.now() < deadline" in poll, "die Schleife muss an der Uhr haengen"
    assert "attempt" not in poll, "ein Versuchszaehler ist keine Dauer"
    assert "trip_film" in poll, "der Film braucht die laengere Frist"


def verify_a_progress_tick_does_not_rebuild_the_page() -> None:
    """Live report: the view flew back to the top every two seconds.

    The panel replaces its whole shadow DOM on render and restores the
    scroll offset against a document that has not finished laying out, so
    a percentage that ticked up threw the reader back to the top of a long
    settings page. A number changing is not a structural change and must
    be written into the node that already shows it.
    """
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    poll = renderer.split("_pollRendererAppJob(jobId) {", 1)[1].split("\n  },", 1)[0]
    assert "_rendererAppPatchProgress()" in poll, "der Fortschritt muss punktuell gesetzt werden"
    assert "structural" in poll, "nur ein Zustandswechsel rechtfertigt ein Neuzeichnen"
    # The nodes the patch writes into have to exist in both cards.
    assert 'data-renderer-progress="card"' in renderer
    editor = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert 'data-renderer-progress="story"' in editor


def verify_the_finished_video_can_be_fetched() -> None:
    """A film that renders and cannot be downloaded is not a film.

    The result lives in the exchange folder, which no Home Assistant view
    serves. It is copied into the existing video library rather than given
    a second download path with its own capability rules.
    """
    panel = _code(INTEGRATION / "panel.py")
    assert '"renderer_app_download"' in panel
    assert "async_adopt_video" in panel
    exporter = _code(INTEGRATION / "trip_video_export.py")
    assert "def async_adopt_video" in exporter
    # The atomic rename is what keeps a download from catching half a file.
    adopt = exporter.split("def _adopt_file", 1)[1].split("\n    def ", 1)[0]
    assert "os.replace" in adopt, "eine halb kopierte Datei darf nicht abholbar sein"
    assert "_prune_library()" in adopt, "die Bibliothek muss begrenzt bleiben"
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    assert "_rendererAppDownload" in renderer
    dispatcher = _js_code(INTEGRATION / "frontend" / "roadplanner-panel.js")
    assert '"renderer-app-download"' in dispatcher


verify_the_story_layer_is_reachable()
verify_reading_a_manifest_is_not_an_edit()
verify_the_builder_only_reads()
verify_the_manifest_module_stays_pure()
verify_the_deferred_work_has_not_crept_in()
verify_the_existing_exports_were_not_rebuilt()
verify_the_story_editor_is_wired_end_to_end()
verify_only_two_fields_are_editable()
verify_typing_does_not_re_render()
verify_unsaved_work_defers_a_background_refresh()
verify_the_chapter_image_reuses_the_existing_cover()
verify_the_manifest_carries_a_version_and_a_hash()
verify_a_running_film_survives_a_page_reload()
verify_the_poll_outlasts_a_whole_film()
verify_a_progress_tick_does_not_rebuild_the_page()
verify_the_finished_video_can_be_fetched()
print("Story layer contract tests passed.")
