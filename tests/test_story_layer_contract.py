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
        # A small copy of a film that has already been rendered. It names
        # a JOB, never a file: the source is a job id, matched against
        # the job-id pattern before either side builds a path from it.
        # Reads one local MP4, calls nothing, costs nothing, and cannot
        # touch the roadbook.
        "story_film_review_copy",
        # The editorial pass: read its state, run it, throw it away. None
        # of the three can carry a roadbook change.
        "story_director_status",
        "story_director_run",
        "story_director_discard",
        # Fetching the finished film where it was started. "It is in the
        # other card" is a signpost, not an answer.
        "renderer_app_download",
        # Stopping a render that is running. It names a JOB and writes a
        # marker into the exchange folder; it carries no trip, no day and
        # no text, so it cannot reach the roadbook at all.
        "renderer_app_cancel",
        # What could play under the film. It returns NAMES of files in one
        # fixed folder, never paths - see trip_film_music.
        "story_film_music",
        # Generated music, in two steps that are separate on purpose: the
        # offer only counts and prices, and it is the only one the panel
        # may reach on its own. The generate call is the single place in
        # the story layer that spends money, and it carries nothing but a
        # trip - never a prompt, never a file name.
        "story_film_music_offer",
        "story_film_music_generate",
        # Which photographs tell each day, and the person's power to
        # overrule that. Both write a derivation or a pin - neither can
        # touch a roadbook fact, a stop or a photograph itself.
        "media_curate_days",
        "media_set_film_pin",
        # Reads the stored derivation and counts. It cannot write, and it
        # cannot call a model - which is what makes it safe to offer next
        # to a paid button without a confirmation.
        "media_diagnose_day",
        "media_diagnose_trip",
        # Arithmetic over the same stored derivation: what a different
        # photo allocation WOULD select. It decides nothing, writes
        # nothing and calls no model - the report exists so a threshold
        # is chosen against real scores instead of a guess.
        "media_simulate_allocation",
        # The videos of the trip, in two steps that are separate on
        # purpose: the offer counts and prices and is the only one the
        # panel may reach on its own; the analyse call is the one that
        # spends money, and it carries nothing but a trip and a force
        # flag - never a path, never a file, never a story.
        "media_video_offer",
        "media_video_analyze",
    }, f"der Editor ruft unerwartete Aktionen auf: {sorted(called)}"
    # And the director calls take a trip and a force flag - nothing that
    # could reach a day, a stop or a text somebody typed.
    director_calls = re.findall(r'"story_director_\w+",\s*\{([^}]*)\}', feature)
    assert director_calls, "die Redaktionsaufrufe wurden nicht gefunden"
    for payload in director_calls:
        assert "trip_id" in payload
        for forbidden in ("changes", "day_id", "patch", "story"):
            assert forbidden not in payload, f"der Redaktionsaufruf darf {forbidden} nicht senden"
    # The film calls carry a trip and, for the render, the NAME of a
    # track. Never an edit, and never a path: the music name is matched
    # against the folder listing on the other side, so nothing sent from
    # a browser can become a file location.
    film_calls = re.findall(r'"(story_film_\w+)",\s*\{([^}]*)\}', feature)
    assert film_calls, "die Filmaufrufe wurden nicht gefunden"
    for action, payload in film_calls:
        if action == "story_film_review_copy":
            # A copy is about a FILM, not about a trip - and the film is
            # named by the job that produced it. A job id is checked
            # against a fixed pattern before either side builds a path
            # from it, which is why this call can carry no location at
            # all rather than a sanitised one.
            assert "job_id" in payload, payload
            assert "trip_id" not in payload, payload
        elif action != "story_film_music":
            # Listing what could be played is not about one trip.
            assert "trip_id" in payload
        for forbidden in ("changes", "day_id", "patch", "path", "/"):
            assert forbidden not in payload, f"{action} darf {forbidden} nicht senden"
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
    # The number moves with the schema; what has to stay true is that
    # there IS one and that it is an integer, not that it is still 1.
    assert re.search(r"^MANIFEST_VERSION = \d+$", manifest, re.M), manifest[:200]
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
    # Both cards that show a job have to look for one. Hanging this on the
    # environment probe alone left a finished film invisible until
    # somebody pressed a button nobody presses when hunting for a video.
    card = renderer.split("_renderRendererApp() {", 1)[1].split("\n  },", 1)[0]
    assert "_rendererAppAdoptOnce()" in card
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


def verify_nobody_reads_a_field_the_provider_results_do_not_have() -> None:
    """The same wrong assumption was made twice, months apart.

    ``AssistantJsonResult`` carries ``value``. Two call sites read
    ``data``: the story director, where it broke every run, and the day
    summary, where it silently threw away a paid-for Vision answer and
    fell through to the text-only prompt. Neither test caught it, because
    both fakes had been given the shape their author assumed.

    So the field name is checked against the real dataclasses, and the
    wrong one is checked as absent across the integration.
    """
    provider = (INTEGRATION / "assistant_provider.py").read_text(encoding="utf-8")
    assert "value: dict[str, Any]" in provider, "AssistantJsonResult traegt value"
    assert "text: str" in provider, "AssistantTextResult traegt text"

    offenders = []
    for path in sorted(INTEGRATION.glob("*.py")):
        source = _code(path)
        for pattern in (
            'getattr(result, "data"',
            'getattr(answer, "data"',
            'getattr(res, "data"',
        ):
            if pattern in source:
                offenders.append(f"{path.name}: {pattern}")
    assert not offenders, offenders


def verify_not_knowing_is_not_reported_as_not_working() -> None:
    """The third time this shape of bug appeared in one session.

    The story card read `_rendererAppStatus?.online`, a field only ever
    filled by pressing a button in a DIFFERENT card, and announced "die
    Renderer-App ist nicht erreichbar" whenever it was missing. So every
    freshly loaded page declared the app dead and disabled the film
    button while the app was running and had just finished a film.

    Silence is not an answer. The card now asks, and distinguishes three
    states rather than two.
    """
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    assert "_rendererAppEnsureStatus" in renderer, "die Karte muss selbst fragen"
    editor = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    film = editor.split("_renderStoryFilm() {", 1)[1].split("\n  },", 1)[0]
    assert "noch nicht bekannt" in film, "unbekannt braucht einen eigenen Satz"
    # And an unanswered question may not disable the button - only a real
    # "no" may.
    assert "(online || !status)" in film, "Schweigen darf den Knopf nicht sperren"


def verify_the_director_has_no_route_to_the_roadbook() -> None:
    """It edits prose. It must not be able to move a stop.

    Checked structurally rather than by intent: the service is never given
    the trip manager, so there is no object in it that could write a day -
    and it imports no store but its own.
    """
    source = _code(INTEGRATION / "story_director_service.py")
    for forbidden in ("async_update_day", "async_update_stop", "async_add_stop", "manager"):
        assert forbidden not in source, f"der Redakteur darf {forbidden} nicht kennen"
    imported = _imports(INTEGRATION / "story_director_service.py")
    assert "experience_store" not in imported
    assert "trip_summary_service" not in imported


def verify_the_director_module_stays_testable_without_home_assistant() -> None:
    """The rules about what may come back must be provable offline."""
    allowed = {"__future__", "typing", "travel_story_manifest"}
    actual = _imports(INTEGRATION / "story_director.py")
    assert actual <= allowed, f"unerlaubte Importe: {sorted(actual - allowed)}"


def verify_the_builder_still_cannot_spend_money() -> None:
    """A panel refresh rebuilds the manifest. It must never call a model."""
    source = _code(INTEGRATION / "story_context_builder.py")
    for forbidden in ("async_generate_text", "async_generate_json", "provider"):
        assert forbidden not in source, f"der Builder darf {forbidden} nicht enthalten"
    # And the status action, which the panel calls on every open, must be
    # answered from stored hashes rather than from the model.
    status = _code(INTEGRATION / "story_director_service.py").split(
        "async def async_status", 1
    )[1].split("\n    async def ", 1)[0]
    assert "async_generate" not in status


def verify_an_ai_text_is_never_stored_where_a_human_one_belongs() -> None:
    """Otherwise "who wrote this?" becomes permanently unanswerable."""
    service = _code(INTEGRATION / "story_director_service.py")
    # The keys are constants, so the literal only lives in the builder;
    # what matters is that the director knows neither the names nor the
    # constants that carry them.
    for override_key in (
        "story_override",
        "story_title_override",
        "OVERRIDE_STORY_KEY",
        "OVERRIDE_TITLE_KEY",
    ):
        assert override_key not in service, (
            f"der Redakteur darf {override_key} nicht schreiben"
        )
    # The service that DOES write them still does, or the editor is broken.
    editor = _code(INTEGRATION / "story_override_service.py")
    assert "OVERRIDE_STORY_KEY" in editor and "OVERRIDE_TITLE_KEY" in editor
    # And the director's own output lands under its own source name.
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert 'STORY_FROM_DIRECTED = "directed"' in manifest


def verify_the_manifest_still_carries_no_coordinates() -> None:
    """The map is a later decision, and half of it must not be made here."""
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    for forbidden in ('"lat"', '"lon"', '"latitude"', '"longitude"'):
        assert forbidden not in manifest, f"{forbidden} gehoert nicht ins Manifest"


def verify_the_map_is_built_beside_the_manifest_and_not_inside_it() -> None:
    """The film needs geography. The description of a journey does not.

    So the map has its own layer, it asks for its data with the manifest's
    own identifiers, and it reads the routing Roadplanner already stored
    rather than deriving a second geography. All three are checkable from
    the source, and each one of them is a rule this step was given.
    """
    builder = _code(INTEGRATION / "trip_map_builder.py")
    # It looks the day up by the chapter's id - the manifest is the index,
    # the roadbook is the truth.
    assert "chapter_id" in builder and "async_get_assistant_payload" in builder
    # No second geography: nothing here routes, geocodes or fetches.
    for forbidden in ("async_get_clientsession", "geocod", "routing_helpers", "requests"):
        assert forbidden not in builder, f"{forbidden} gehoert nicht in den Kartenaufbau"

    context = _code(INTEGRATION / "trip_map_context.py")
    # The ferry distinction is read, never guessed. If the mode were
    # derived here from distance or straightness, the film would be
    # inventing crossings.
    assert 'segment.get("mode")' in context
    assert "details" in context and "routing" in context

    # And the map still does not travel in the manifest: the film package
    # carries it, beside the story rather than inside it.
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert "map_context" not in manifest
    package = _code(INTEGRATION / "trip_film_package.py")
    assert "map_context" in package


def verify_a_portrait_url_never_leaves_the_panel() -> None:
    """An unguessable filename is a bearer secret, not a session.

    It is a fine trade for a picture inside the household's own panel and
    a bad one everywhere else, because it does not expire and anyone
    holding it can fetch the face. So the four places it must never reach
    are checked here rather than remembered: a model context, a render
    package, a log line and exported story data.
    """
    # The story layer takes names. Not entries, not payloads - names.
    builder = _code(INTEGRATION / "story_context_builder.py")
    assert "portrait" not in builder, "die Story-Ebene darf kein Portrait sehen"
    for consumer in ("story_director.py", "story_director_service.py"):
        source = _code(INTEGRATION / consumer)
        assert "portrait" not in source, f"{consumer} darf kein Portrait an ein Modell geben"
    # The manifest is what a model and an export both read.
    manifest = _code(INTEGRATION / "travel_story_manifest.py")
    assert "portrait" not in manifest

    # The film DOES show faces now, so the rule is about the URL rather
    # than about the word: what must never travel is the address, because
    # that address is the capability. Reading the stored file is the
    # correct behaviour and the reason the exporter may touch the
    # portrait store at all.
    for consumer in ("trip_film_package.py", "trip_film_export.py", "trip_map_builder.py"):
        source = _code(INTEGRATION / consumer)
        for forbidden in ("portrait_url", "PORTRAIT_URL", "api/roadplanner/crew_portrait"):
            assert forbidden not in source, f"{consumer} darf {forbidden} nicht mitschicken"
    # The portrait reaches the film as bytes, from the local store.
    export = _code(INTEGRATION / "trip_film_export.py")
    assert "portrait_key" in export and "store.read" in export

    # And the crew section itself refuses anything address-shaped, so a
    # future writer cannot put one there by accident.
    crew = _code(INTEGRATION / "trip_film_crew.py")
    assert '"://" in text' in crew and 'text.startswith("/api/")' in crew

    # And the route itself does not log the name it was asked for.
    view = (INTEGRATION / "crew_portrait_http.py").read_text(encoding="utf-8")
    body = view.split('"""', 2)[-1]
    assert "_LOGGER" not in body, "die Portraitroute darf keinen Dateinamen protokollieren"


def verify_opening_the_tab_is_the_request() -> None:
    """A click whose only possible answer is yes is not a question.

    The story card used to open with a button. Building a manifest calls
    no model and costs nothing - it is a cached read of the roadbook - so
    the button asked the reader to confirm the thing they had already
    asked for by opening the tab.

    Two things make the automatic load safe, and both are checked because
    both were nearly got wrong: it happens once per trip rather than on
    every render, and it is deferred by a microtask, because `_storyLoad`
    renders before its first await and this is called FROM a render.
    """
    feature = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "_storyLoadOnce()" in feature, "die Karte muss sich selbst laden"
    assert "_storyLoadTriedFor" in feature, "einmal pro Reise, nicht pro Render"
    once = feature.split("_storyLoadOnce()", 1)[1].split("},", 1)[0]
    assert "Promise.resolve().then" in once, "der Aufruf muss aufgeschoben werden"
    # And a failure still has a way back: the automatic attempt happens
    # once, so without this the card would be a dead end.
    assert "_storyLoadFailed" in feature
    assert 'data-action="story-load"' in feature


def verify_a_story_belongs_to_exactly_one_trip() -> None:
    """Nothing cleared it, so switching trips kept the old chapters.

    Worse than a stale display: the drafts came along too, one save away
    from being written onto a day belonging to a different journey.
    """
    feature = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "_storyResetForTrip()" in feature
    reset = feature.split("_storyResetForTrip()", 1)[1].split("},", 1)[0]
    for cleared in ("_storyManifest", "_storyChapterId", "_storyDrafts"):
        assert cleared in reset, f"{cleared} muss beim Reisewechsel fallen"

    dispatcher = _js_code(INTEGRATION / "frontend" / "roadplanner-panel.js")
    # Every place the selected trip can change, including the one where
    # the backend changes it rather than the user.
    assert dispatcher.count("_storyResetForTrip()") >= 4, (
        "jeder Reisewechsel muss die Geschichte verwerfen"
    )


def verify_an_automatic_read_does_not_shout() -> None:
    """Live report: a red banner saying "Unbekannter Roadplanner-Fehler".

    Three reads fire by themselves when a card opens - the story, the
    renderer status, the adoption of a running film. A phone coming back
    from standby reaches them mid-reconnect, and a dropped WebSocket
    rejects with a bare `{code: 3}` and no text, so the generic fallback
    produced a sentence naming nothing over a page that was working.

    Two rules, both checked: an automatic read reports its failure into
    its own card rather than into a banner, and a lost connection is
    called a lost connection.
    """
    panel = _js_code(INTEGRATION / "frontend" / "roadplanner-panel.js")
    assert 'errorMode === "silent"' in panel, "stille Fehler brauchen einen Modus"
    # The definition, not the first call site.
    body = panel.split("_errorMessage(error) {", 1)[1].split("\n  }", 1)[0]
    assert "_isConnectionLostError(error)" in body, (
        "ein Verbindungsabbruch darf nicht als unbekannt gelten"
    )

    story = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    once = story.split("_storyLoadOnce()", 1)[1].split("},", 1)[0]
    assert "quiet: true" in once, "die automatische Ladung muss still sein"
    # The button press is NOT silent: somebody asked, so somebody is told.
    load = story.split("async _storyLoad(", 1)[1].split("\n  },", 1)[0]
    assert 'quiet ? "silent" : "toast"' in load

    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    assert renderer.count('errorMode: "silent"') >= 2, (
        "auch Status und Übernahme fragen ungefragt"
    )


def verify_the_preview_says_whether_there_is_a_map() -> None:
    """Live question: "wir haben keine Karte gesehen" - and no way to know why.

    A film without a map has two very different causes: the trip has no
    stored routes, or the version that rendered it did not have the
    feature. The preview exists to answer "what would be in the film?",
    so it has to answer that too - and it answers it with the same
    builder the render uses, because a second counter would eventually
    disagree with what actually gets drawn.
    """
    export = _code(INTEGRATION / "trip_film_export.py")
    preview = export.split("async def async_preview", 1)[1].split("async def ", 1)[0]
    assert "_map.async_build" in preview, "die Vorschau muss den echten Aufbau nutzen"
    assert "mapped_chapters" in preview
    # "No route was ever calculated" is a different answer from "here is
    # the route", and the film draws it differently, so the preview names
    # it rather than counting it as a map like any other.
    assert "estimated_map_chapters" in preview

    card = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "mapped_chapters" in card, "die Karte muss in der Vorschau auftauchen"
    assert "Karte: keine" in card, "auch das Fehlen muss dastehen"


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


def verify_a_render_in_progress_never_rebuilds_an_open_dialog() -> None:
    """Live report: a vehicle form jumped every two seconds during a film.

    A render replaces the whole shadow DOM, so doing it under an open
    dialog tears that dialog down and builds a new one - visibly, and
    with whatever was typed into it at risk. The rule already existed
    for background refreshes; the progress poll had not been told.
    """
    renderer = _js_code(INTEGRATION / "frontend" / "features" / "renderer-app.js")
    guard = renderer.split("_rendererAppRedraw() {", 1)[1].split("\n  },", 1)[0]
    assert "this._dialog" in guard, "ein offener Dialog darf nicht neu gebaut werden"
    assert "_storyAnyDirty" in guard, "ungespeicherter Text ebenso wenig"
    # And the poll has to go through it rather than around it.
    poll = renderer.split("_pollRendererAppJob(jobId) {", 1)[1].split("\n  },", 1)[0]
    assert "_rendererAppRedraw()" in poll
    assert "this._render(" not in poll, "die Abfrage darf nicht direkt zeichnen"
    # And the tick must not redraw merely because the percentage is not on
    # screen. That fallback fired on every tab except the two that show
    # the card, which is how the Erinnerungen tab twitched every two
    # seconds. Nothing to update is a reason NOT to rebuild the page.
    assert "if (structural) this._rendererAppRedraw();" in poll, poll[:400]
    assert "!this._rendererAppPatchProgress()" not in poll


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
    # And reachable from the card the film was started in, not only from
    # the one behind a menu.
    editor = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert 'data-action="renderer-app-download"' in editor


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
verify_a_render_in_progress_never_rebuilds_an_open_dialog()
verify_nobody_reads_a_field_the_provider_results_do_not_have()
verify_not_knowing_is_not_reported_as_not_working()
verify_the_director_has_no_route_to_the_roadbook()
verify_the_director_module_stays_testable_without_home_assistant()
verify_the_builder_still_cannot_spend_money()
verify_an_ai_text_is_never_stored_where_a_human_one_belongs()
verify_the_manifest_still_carries_no_coordinates()
verify_the_map_is_built_beside_the_manifest_and_not_inside_it()
verify_a_portrait_url_never_leaves_the_panel()
verify_opening_the_tab_is_the_request()
verify_a_story_belongs_to_exactly_one_trip()
verify_an_automatic_read_does_not_shout()
verify_the_preview_says_whether_there_is_a_map()
print("Story layer contract tests passed.")
