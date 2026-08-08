"""Cross-file wiring for the renderer-app PoC, and the boundaries it keeps.

Each half of this feature can look correct on its own while the feature is
unreachable, or while a permission has quietly widened. These checks pin
the things only visible across files: that the app is reachable from the
panel, that it stays out of the production paths, that one repository can
still serve both HACS and Home Assistant, and that the app asks for
nothing beyond a shared folder.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(".")
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer"
APP_FRONTEND = (
    ROOT / "custom_components" / "roadplanner_mcp" / "frontend" / "features" / "renderer-app.js"
)


def _js_code(path: Path) -> str:
    """JavaScript or TypeScript without its comments.

    A rule about a thing must not be satisfied - or broken - by prose
    describing the thing. The composition explains in a comment which
    sentence film v0 wrongly put on screen; scanning the raw file would
    make that explanation fail the check it belongs to.
    """
    text = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


def _config() -> dict:
    """Read config.yaml without a YAML dependency (it is a flat file)."""
    text = (APP / "config.yaml").read_text(encoding="utf-8")
    return {
        line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line and not line.startswith((" ", "#", "-")) and ":" in line
    }


def verify_the_poc_is_reachable_from_the_panel() -> None:
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    frontend = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")
    feature = (INTEGRATION / "frontend" / "features" / "renderer-app.js").read_text(
        encoding="utf-8"
    )
    for action in (
        "renderer_app_environment",
        "renderer_app_status",
        "renderer_app_run",
        "renderer_app_render",
        "renderer_app_job_status",
    ):
        assert f'"{action}"' in panel, f"{action} muss registriert sein"
        assert f'if action == "{action}"' in panel, f"{action} braucht einen Zweig"
    for click in (
        "renderer-app-probe",
        "renderer-app-run",
        "renderer-app-render",
        "renderer-app-copy-report",
    ):
        assert f'action === "{click}"' in frontend, f"{click} muss verteilt werden"
        assert f'data-action="{click}"' in feature, f"{click} braucht einen Knopf"
    assert "rendererAppMixin" in frontend, "das Mixin muss registriert sein"
    assert "_renderRendererApp()" in (
        INTEGRATION / "frontend" / "features" / "trip-day-stop.js"
    ).read_text(encoding="utf-8"), "die Karte muss auch gerendert werden"


def verify_submitting_a_job_needs_edit_rights() -> None:
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    for action in ('"renderer_app_run"', '"renderer_app_render"'):
        assert action in edit_block, (
            f"{action}: einen Auftrag schreiben ist eine Änderung, kein Lesevorgang"
        )


def verify_the_production_paths_are_untouched() -> None:
    """The PoC must not be able to reach the PDF or the video export."""
    for name in ("renderer_app_client.py", "renderer_app_protocol.py"):
        source = (INTEGRATION / name).read_text(encoding="utf-8")
        for foreign in ("trip_video", "trip_pdf", "last_video", "library_dir", "ffmpeg"):
            assert foreign not in source, f"{name} darf {foreign} nicht berühren"


def verify_the_untrusted_artifact_is_never_injected_as_markup() -> None:
    """The exchange folder is writable by another container.

    Its SHA-256 sits in the same file as the artefact, so whoever can forge
    one can forge both - the bytes are untrusted. Putting them into the
    panel's DOM would be a script-injection path into Home Assistant's
    frontend; an <img> with a data: URL renders SVG inert.
    """
    feature = (INTEGRATION / "frontend" / "features" / "renderer-app.js").read_text(
        encoding="utf-8"
    )
    assert "data:image/svg+xml;base64," in feature, "das SVG muss als Bild eingebettet werden"
    assert "${result.svg}" not in feature, "rohes SVG darf nicht ins Markup"
    assert "innerHTML = result" not in feature


def verify_the_channel_is_documented_as_trust_not_security() -> None:
    """The distinction decides what may be done with what comes back.

    /share is writable by every app with the same mount, and the SHA-256
    sits in the same file as the artefact it describes - so the hash proves
    transport, never origin. Anyone later tempted to treat a returned file
    as trustworthy has to meet this sentence first.
    """
    protocol = (INTEGRATION / "renderer_app_protocol.py").read_text(encoding="utf-8")
    assert "trust channel, not a security boundary" in protocol
    assert "never\nthat they came from the renderer" in protocol.replace("  ", " ") or (
        "never" in protocol and "came from the renderer" in protocol
    )
    assert "only a file that ffprobe confirms" in protocol, (
        "fuer produktive Videos zaehlt nur, was ffprobe bestaetigt"
    )
    docs = (APP / "DOCS.md").read_text(encoding="utf-8")
    assert "Vertrauenskanal, keine Sicherheitsgrenze" in docs


def verify_the_worker_has_hard_limits() -> None:
    """A renderer without limits can fill a disk or occupy the only worker."""
    worker = (APP / "src" / "index.mjs").read_text(encoding="utf-8")
    render = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
    assert "MAX_CONCURRENT_JOBS = 1" in worker, "ein Job zur Zeit bleibt der Standard"
    assert "activeJobs >= MAX_CONCURRENT_JOBS" in worker, "die Grenze muss auch greifen"
    for needed in ("MAX_JOB_DURATION_MS", "MAX_RESULT_BYTES", "RESULT_RETENTION_MS"):
        assert needed in worker, f"{needed} fehlt"
    for needed in ("renderTimeoutMs", "maxOutputBytes", "minFreeBytes"):
        assert needed in render, f"{needed} fehlt"
    assert "RENDER_TIMEOUT" in render, "eine ueberschrittene Renderzeit braucht einen Code"
    # Leftovers from a crashed render must not accumulate.
    assert '.part' in worker and "fs.rm" in worker


def verify_the_image_is_built_slim() -> None:
    """1574 MB was not necessary - it was a full browser and a full ffmpeg."""
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    assert "AS builder" in code, "der Bau bleibt in einer eigenen Stufe zurueck"
    assert "npm ci --omit=dev" in code, "die Laufzeit bekommt nur Laufzeitpakete"
    assert "npm cache clean" in code and "/root/.npm" in code
    assert "chrome-headless-shell" in code, "kein vollstaendiges Chromium"
    assert "libnss3" in code, "die Bibliotheken des Shell werden benannt statt mitgeschleppt"
    assert "apt-get install -y --no-install-recommends chromium" not in code
    assert "AS ffprobe-runtime" in code and "ldd /usr/bin/ffprobe" in code, (
        "ffprobe wird mit seinen Bibliotheken herausgeloest, statt ffmpeg mitzunehmen"
    )
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert "@remotion/bundler" in package["devDependencies"], (
        "der Bundler laeuft nur beim Bauen"
    )
    assert "@remotion/bundler" not in package["dependencies"]


def verify_the_paths_reach_the_process_that_needs_them() -> None:
    """A Dockerfile ENV is not enough: s6 does not pass it to the service.

    The worker fell back to /usr/bin/chromium and reported a missing
    browser for a browser that was in the image. Every path the runtime
    depends on has to be set by the script that execs it, and checked
    there, so a broken image says so at startup instead of at the first
    render.
    """
    run_sh = (APP / "run.sh").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in run_sh.splitlines() if not line.strip().startswith("#")
    )
    for variable in (
        "ROADPLANNER_BROWSER",
        "ROADPLANNER_FFPROBE",
        "ROADPLANNER_BUNDLE_DIR",
        "LD_LIBRARY_PATH",
    ):
        assert f"export {variable}=" in code, (
            f"{variable} muss im Startskript gesetzt werden, nicht nur als ENV"
        )
    assert "/opt/roadplanner-renderer/browser/chrome-headless-shell" in code
    assert "/opt/ffprobe/lib" in code, "ffprobe findet seine Bibliotheken sonst nicht"
    assert "nicht ausführbar" in code, "fehlende Programme brechen den Start ab"

    # The same defaults, in the module the worker actually imports.
    render = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
    assert "ROADPLANNER_BROWSER" in render and "ROADPLANNER_FFPROBE" in render


def verify_the_app_asks_for_nothing_beyond_a_shared_folder() -> None:
    # Comments are prose - this file's own commentary names the permissions
    # it deliberately does NOT request. Scan what takes effect, not what
    # explains it.
    text = "\n".join(
        line
        for line in (APP / "config.yaml").read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )
    config = _config()
    assert config["slug"] == "roadplanner_renderer_poc"
    assert config["stage"] == "experimental", "ein Experiment muss als solches markiert sein"
    assert config["boot"] == "manual", "ein Experiment startet nicht von selbst"
    assert config["host_network"] == "false"
    assert config["ingress"] == "false"
    # The mount is the entire channel; anything wider would defeat the point.
    assert "type: share" in text and "read_only: false" in text
    for forbidden in (
        "privileged",
        "docker_api",
        "hassio_api",
        "hassio_role",
        "homeassistant_api",
        "full_access",
        "host_pid",
        "host_dbus",
        "devices",
        "ports:",
        "type: config",
        "type: homeassistant_config",
        "type: all_app_configs",
        "type: ssl",
        "type: backup",
    ):
        assert forbidden not in text, f"config.yaml darf {forbidden} nicht anfordern"
    assert (APP / "apparmor.txt").is_file(), "das AppArmor-Profil erzwingt die Grenze"


def verify_nothing_is_installed_at_container_start() -> None:
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    assert "npm ci" in dockerfile, "Abhängigkeiten gehören in den Build"
    run = (APP / "run.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in run.splitlines() if not line.strip().startswith("#"))
    for forbidden in ("npm install", "npm ci", "apk add", "apt-get", "curl ", "wget "):
        assert forbidden not in code, f"run.sh darf {forbidden} nicht ausführen"
    # exec, or the shell stays the process Home Assistant signals and the
    # worker never sees SIGTERM - which reads as a crash in the restart
    # tests rather than as the clean shutdown it is.
    assert "exec " in code and "index.mjs" in code, (
        "der Worker muss das Signal selbst empfangen"
    )


def verify_the_reported_version_survives_a_local_build() -> None:
    """Live finding: the heartbeat said `0.0.0-dev`, a version that does
    not exist.

    Two builders pass two different variables. CI passes APP_VERSION; the
    Supervisor, when it builds the app on the user's own machine, passes
    BUILD_VERSION taken from config.yaml. The Dockerfile read only the
    former and fell back to a literal, so the field meant to identify the
    running build identified nothing.
    """
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    assert "ARG BUILD_VERSION" in code, "der Supervisor uebergibt BUILD_VERSION"
    assert "${APP_VERSION:-${BUILD_VERSION}}" in code, (
        "beide Builder muessen zu einer echten Version fuehren"
    )
    # Resolved by a shell, not by Dockerfile substitution: ENV supports
    # ${var:-default} but a nested ${...} inside that default is not
    # something to bet the reported version on.
    assert 'printf' in code and "/VERSION" in code, (
        "die Version wird beim Bauen in eine Datei geschrieben"
    )
    run = (APP / "run.sh").read_text(encoding="utf-8")
    assert "/opt/roadplanner-renderer/VERSION" in run, (
        "und beim Start von dort gelesen"
    )
    assert "0.0.0-dev" not in code, (
        "ein Platzhalter, der wie eine echte Version aussieht, gehoert nicht hinein"
    )
    # And the versions that describe the same app must agree.
    config_version = _config()["version"].strip('"')
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == config_version, (
        f"config.yaml sagt {config_version}, package.json sagt {package['version']}"
    )


def verify_every_dependency_is_pinned_exactly() -> None:
    """A spike is only worth anything if the same image can be rebuilt.

    Remotion, React and the compositor all ship native binaries; a floating
    range would make a later "it worked yesterday" impossible to
    investigate.
    """
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    versions = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    assert versions, "die App hat Abhängigkeiten"
    for name, spec in versions.items():
        assert spec[0].isdigit(), f"{name} ist nicht exakt gepinnt: {spec}"
    remotion = {
        spec
        for name, spec in versions.items()
        if name == "remotion" or name.startswith("@remotion/")
    }
    assert len(remotion) == 1, f"alle Remotion-Pakete müssen eine Version teilen: {remotion}"
    assert (APP / "package-lock.json").is_file(), "die Sperrdatei macht den Build reproduzierbar"


def verify_the_image_brings_its_own_runtime_and_browser() -> None:
    """Nothing may be fetched while a job runs, or at container start."""
    dockerfile = (APP / "Dockerfile").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in dockerfile.splitlines() if not line.strip().startswith("#")
    )
    # The browser is a headless shell now, and ffprobe is lifted out of
    # ffmpeg - so this checks the capabilities, not the package names of
    # the first attempt.
    for needed in (
        "chrome-headless-shell",
        "ffprobe",
        "fonts-",
        "npm ci",
        "scripts/bundle.mjs",
        "scripts/ensure-browser.mjs",
    ):
        assert needed in code, f"das Image muss {needed} enthalten"
    # Pinned base and a checksum-verified Node tarball.
    assert "amd64-base-debian:bookworm-" in code, "das Basis-Image muss exakt gepinnt sein"
    assert "sha256sum -c -" in code, "die Node-Laufzeit wird vor dem Auspacken geprüft"
    assert "REMOTION_SKIP_BROWSER_DOWNLOAD=1" in code, (
        "der Renderer darf sich niemals selbst einen Browser holen"
    )
    render = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
    render_code = "\n".join(
        line for line in render.splitlines() if not line.strip().startswith("*")
    )
    assert "ensureBrowser" not in render_code, (
        "ein fehlender Browser ist ein Befund, kein Anlass zum Nachladen"
    )


def verify_the_render_is_validated_before_it_counts() -> None:
    """Exit code 0 is a claim; ffprobe is the check."""
    render = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
    assert "ffprobe" in render
    assert "assertExpected" in render
    for expected in ("h264", "1280", "720", "30"):
        assert expected in render, f"die Erwartung {expected} muss geprüft werden"
    # Written to a temporary name first: the folder is polled, and a
    # half-written mp4 under its final name would read as a finished result.
    assert ".part.mp4" in render, "das Ergebnis wird erst nach der Prüfung sichtbar"


def verify_the_render_can_actually_be_triggered() -> None:
    """A renderer nobody can start is not a feature.

    The action, the panel branch, the button and the dispatcher have to
    line up; each half looks fine alone while the render stays
    unreachable.
    """
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    assert "ACTION_RENDER_REMOTION_TEST" in panel, (
        "der Panel-Zweig muss den Render-Auftrag anfordern, nicht den einfachen"
    )
    feature = (INTEGRATION / "frontend" / "features" / "renderer-app.js").read_text(
        encoding="utf-8"
    )
    assert 'data-action="renderer-app-render"' in feature
    frontend = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8")
    assert '_rendererAppRun("renderer_app_render")' in frontend, (
        "der Knopf muss die Render-Aktion senden"
    )
    # The poll has to outlast the render. This used to be pinned as
    # "attempt < 150", which pinned the disguise rather than the property:
    # a count is a duration, and that duration turned out to be five
    # minutes against a film that needs fourteen. What matters is that the
    # loop ends on a clock it can be reasoned about.
    assert "Date.now() < deadline" in feature, "die Abfrage muss den Render überdauern"


def verify_every_planned_scene_type_has_a_component() -> None:
    """The one thing neither side can check alone.

    The plan is written in Python and drawn in TypeScript. A scene type
    added on one side and forgotten on the other is a package that
    validates, renders, and shows a blank frame for a quarter of the
    film - the kind of failure that only shows up when somebody watches
    the result.

    So the library is compared across the language boundary: Python's
    list, the protocol's allowlist, and the composition's branches.
    """
    plan = (INTEGRATION / "trip_film_plan.py").read_text(encoding="utf-8")
    types = set(re.findall(r'^SCENE_[A-Z_]+ = "([a-z_]+)"$', plan, re.M))
    assert types, "keine Szenentypen gefunden"

    protocol = (APP / "src" / "protocol.mjs").read_text(encoding="utf-8")
    allowlist = protocol.split("FILM_SCENE_TYPES = new Set([", 1)[1].split("]);", 1)[0]
    declared = set(re.findall(r'"([a-z_]+)"', allowlist))
    assert declared == types, f"Protokoll und Plan sind uneinig: {declared ^ types}"

    composition = _js_code(APP / "src" / "remotion" / "RoadplannerTripFilm.tsx")
    drawn = set(re.findall(r'scene\.type === "([a-z_]+)"', composition))
    # "photo" is the else branch rather than a named comparison - the
    # workhorse needs no test of its own to be reached.
    missing = types - drawn - {"photo"}
    assert not missing, f"Szenentypen ohne Komponente: {sorted(missing)}"


def verify_the_world_outline_is_projected_once_per_view() -> None:
    """Live failure: the 25-day CI film ran past its 1500-second limit.

    Projecting Natural Earth is about a hundred thousand coordinate
    transforms plus a path string of similar size. A React memo does not
    save it: Remotion renders frames across several browser tabs that
    each seek to arbitrary frames, so a component rarely gets to reuse
    its own previous result and the outline was rebuilt for every one of
    four thousand frames.

    The fix is a module-level cache keyed by the view, which is checked
    here because nothing else would notice it being removed - the film
    would still be correct, just too slow to finish.
    """
    map_source = _js_code(APP / "src" / "remotion" / "TripMap.tsx")
    assert "VIEW_CACHE" in map_source, "der Kartenausschnitt braucht einen Cache"
    body = map_source.split("export const buildView", 1)[1]
    assert "VIEW_CACHE.get(" in body, "buildView muss den Cache lesen"
    assert "VIEW_CACHE.set(" in body, "buildView muss den Cache fuellen"
    # And the projection happens there and nowhere else: a second call
    # site would reintroduce the per-frame cost past the cache.
    assert map_source.count("geoPath(") == 1, "geoPath gehoert genau an eine Stelle"


def verify_no_full_frame_filter_reaches_the_film() -> None:
    """Measured, not guessed: a blur was the most expensive thing here.

    An upright photograph was shown whole with the same picture blurred
    behind it. It looked good and cost 210 ms per frame against 46 ms for
    the same picture shown landscape - a software renderer re-rasterises
    a full-frame filter for every single frame - and on the 25-day film
    that one effect was most of the reason the render passed its limit.

    The surround is now two colours sampled from the picture in the
    package, once, where the pixels are already open. This check exists
    because the blur is the obvious thing to reach for again.
    """
    composition = _js_code(APP / "src" / "remotion" / "RoadplannerTripFilm.tsx")
    assert "blur(" not in composition, "ein Vollbildfilter gehoert nicht in den Film"
    # The colours have to arrive from the package rather than be invented
    # in the composition, or the surround stops coming from the picture.
    assert "colorTop" in composition and "colorBottom" in composition
    package = (INTEGRATION / "trip_film_package.py").read_text(encoding="utf-8")
    assert "def image_palette" in package


def verify_a_finished_film_says_when_it_was_made() -> None:
    """Live request, and the reason is in this session's own history.

    A trip film takes a quarter of an hour, and the card said only "der
    zuletzt erzeugte Reisefilm ist fertig" - so a film from yesterday
    evening and one from ten minutes ago looked exactly alike. When the
    map arrived, an older film was downloaded to look for it, and the
    absence of a map was read as a fault in the map rather than as the
    age of the file.

    The timestamp was in the data the whole time: `completed_at` in the
    result, `updated_at` on the job. It simply was never shown.
    """
    story = _js_code(INTEGRATION / "frontend" / "features" / "story-editor.js")
    assert "job.updated_at" in story, "die Karte muss das Alter des Films kennen"
    assert "erstellt am" in story

    renderer = _js_code(APP_FRONTEND)
    assert "result.completed_at" in renderer
    assert "Erstellt am" in renderer


def verify_the_film_gets_the_plan_rather_than_deriving_one() -> None:
    """Two places computing a length is one place computing it wrong."""
    render = (APP / "src" / "render.mjs").read_text(encoding="utf-8")
    assert "scenes: parsed.scenes" in render
    root = (APP / "src" / "remotion" / "Root.tsx").read_text(encoding="utf-8")
    assert "filmDurationInFrames(props.scenes" in root, (
        "die Laenge muss aus dem Plan kommen"
    )


def verify_no_diagnostic_text_reaches_the_film() -> None:
    """A real film said "Für diesen Tag gibt es keine Fotos" on screen.

    True, and addressed to the wrong audience. A day without pictures is
    a written page now, so the sentence itself must be gone.
    """
    composition = _js_code(APP / "src" / "remotion" / "RoadplannerTripFilm.tsx")
    for forbidden in (
        "keine Fotos",
        "kein Foto",
        "keines im Film",
        "Keine Fahrtdaten",
        "nicht hinterlegt",
    ):
        assert forbidden not in composition, f"{forbidden!r} gehoert nicht in den Film"


def verify_the_mini_export_is_wired_end_to_end() -> None:
    """Every half of the data path, checked where only a file can see it.

    Package builder, client, panel action, worker branch, composition and
    dispatcher each look complete on their own while the export is
    unreachable. This is the only place that sees all of them at once.
    """
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    for action in ("renderer_app_trip_days", "renderer_app_trip_day"):
        assert f'"{action}"' in panel, f"{action} fehlt in panel.py"
    # Handing real photos over is an edit, listing days is not.
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"renderer_app_trip_day"' in edit_block, (
        "der Mini-Export braucht Schreibrechte"
    )
    assert '"renderer_app_trip_days"' not in edit_block, (
        "die Tagesliste ist ein Lesevorgang"
    )

    wiring = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
    assert "TripDayMiniExporter" in wiring and "trip_day_mini_export=" in wiring

    exporter = (INTEGRATION / "trip_day_mini_export.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in exporter.splitlines() if not line.strip().startswith("#")
    )
    # The mini export proves a data path; it must not quietly start doing
    # the work the assignment put out of scope.
    for forbidden in ("async_generate_text", "map_snapshot", "trip_music", "narrative"):
        assert forbidden not in code, f"der Mini-Export darf {forbidden} nicht benutzen"
    assert "async_fetch_day_photos" in code, (
        "die Fotoauswahl bleibt die gemeinsame - sie darf nicht auseinanderlaufen"
    )

    frontend = (INTEGRATION / "frontend" / "features" / "renderer-app.js").read_text(
        encoding="utf-8"
    )
    dispatcher = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(
        encoding="utf-8"
    )
    for hook in ("renderer-app-trip-day", "renderer-app-load-days", "renderer-app-day"):
        assert hook in frontend, f"der Knopf {hook} fehlt im Panel"
        assert hook in dispatcher, f"{hook} wird nicht verteilt"
    assert "_rendererAppMiniExport" in frontend and "_rendererAppMiniExport" in dispatcher

    worker = (APP / "src" / "index.mjs").read_text(encoding="utf-8")
    assert '"render_trip_day"' in worker, "der Worker kennt die Aktion nicht"
    assert "discardInputs" in worker, "das Renderpaket muss nach dem Job verschwinden"
    root = (APP / "src" / "remotion" / "Root.tsx").read_text(encoding="utf-8")
    assert "roadplanner-trip-day" in root and "calculateMetadata" in root


def verify_real_travel_data_is_minimised_before_it_leaves() -> None:
    """/share is a trust channel, so what crosses it is decided, not default."""
    package = (INTEGRATION / "trip_day_render_package.py").read_text(encoding="utf-8")
    code = "\n".join(
        line
        for line in package.splitlines()
        if not line.strip().startswith("#") and not line.strip().startswith("*")
    )
    # Re-encoded from pixels: that is what removes EXIF, and with it the
    # GPS position a phone writes into every holiday photo.
    assert "exif_transpose" in code, "die Ausrichtung wird vor dem Verwerfen angewandt"
    assert "metadata_markers" in code, "das Ergebnis wird auf Restmetadaten geprüft"
    assert 'format="JPEG"' in code, "das Bild wird neu geschrieben, nicht kopiert"
    for limit in ("MAX_IMAGES", "MAX_IMAGE_BYTES", "MAX_PACKAGE_BYTES", "MAX_STOPS"):
        assert limit in code, f"die Grenze {limit} fehlt"
    # No filename travels, so no string from the package reaches a path.
    assert "def image_filename" in code

    worker = (APP / "src" / "index.mjs").read_text(encoding="utf-8")
    assert "INPUT_RETENTION_MS" in worker, (
        "ein verwaistes Renderpaket darf nicht dauerhaft liegen bleiben"
    )


def verify_one_repository_still_serves_both_consumers() -> None:
    """HACS and the Supervisor read different files and must not collide."""
    assert (ROOT / "hacs.json").is_file(), "HACS bleibt unverändert"
    assert (ROOT / "repository.yaml").is_file(), "der Supervisor braucht diesen Deskriptor"
    repository = (ROOT / "repository.yaml").read_text(encoding="utf-8")
    assert "name:" in repository and "url:" in repository
    # The integration is still the only thing HACS ships.
    integrations = sorted(p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir())
    assert integrations == ["roadplanner_mcp"], integrations


def verify_stray_app_manifests_are_rejected_by_the_validator() -> None:
    """Home Assistant globs **/config.* across the whole checkout.

    Any config.yaml added anywhere for an unrelated reason would surface in
    the user's app store as a broken app. Nothing in the layout prevents
    that, so the validator has to.
    """
    validator = (ROOT / "tools" / "validate_repository.py").read_text(encoding="utf-8")
    assert "validate_app_configs" in validator
    assert "app manifest" in validator
    assert "validate_app_configs()" in validator.split("def main(", 1)[1], (
        "die Prüfung muss auch aufgerufen werden"
    )


def verify_the_app_ci_is_a_separate_workflow() -> None:
    """An experiment must never block an ordinary Roadplanner release."""
    workflow = ROOT / ".github" / "workflows" / "renderer-app-poc.yml"
    assert workflow.is_file(), "der App-Build gehört in einen eigenen Workflow"
    text = workflow.read_text(encoding="utf-8")
    assert "npm ci" in text
    assert "test_renderer_app_end_to_end.py" in text, (
        "CI muss den echten Worker gegen die echte Python-Seite fahren"
    )
    validation = (ROOT / ".github" / "workflows" / "roadplanner-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "renderer_renderer" not in validation
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "roadplanner-renderer-poc" not in release, (
        "das Experiment darf am normalen Release nicht hängen"
    )


verify_the_poc_is_reachable_from_the_panel()
verify_submitting_a_job_needs_edit_rights()
verify_the_production_paths_are_untouched()
verify_the_untrusted_artifact_is_never_injected_as_markup()
verify_the_channel_is_documented_as_trust_not_security()
verify_the_worker_has_hard_limits()
verify_the_image_is_built_slim()
verify_the_paths_reach_the_process_that_needs_them()
verify_the_app_asks_for_nothing_beyond_a_shared_folder()
verify_nothing_is_installed_at_container_start()
verify_the_reported_version_survives_a_local_build()
verify_every_dependency_is_pinned_exactly()
verify_the_image_brings_its_own_runtime_and_browser()
verify_the_render_is_validated_before_it_counts()
verify_the_render_can_actually_be_triggered()
verify_every_planned_scene_type_has_a_component()
verify_the_world_outline_is_projected_once_per_view()
verify_no_full_frame_filter_reaches_the_film()
verify_a_finished_film_says_when_it_was_made()
verify_the_film_gets_the_plan_rather_than_deriving_one()
verify_no_diagnostic_text_reaches_the_film()
verify_the_mini_export_is_wired_end_to_end()
verify_real_travel_data_is_minimised_before_it_leaves()
verify_one_repository_still_serves_both_consumers()
verify_stray_app_manifests_are_rejected_by_the_validator()
verify_the_app_ci_is_a_separate_workflow()
print("Renderer app contract tests passed.")
