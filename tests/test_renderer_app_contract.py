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

ROOT = Path(".")
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
APP = ROOT / "apps" / "roadplanner_renderer"


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
        "renderer_app_job_status",
    ):
        assert f'"{action}"' in panel, f"{action} muss registriert sein"
        assert f'if action == "{action}"' in panel, f"{action} braucht einen Zweig"
    for click in ("renderer-app-probe", "renderer-app-run", "renderer-app-copy-report"):
        assert f'action === "{click}"' in frontend, f"{click} muss verteilt werden"
        assert f'data-action="{click}"' in feature, f"{click} braucht einen Knopf"
    assert "rendererAppMixin" in frontend, "das Mixin muss registriert sein"
    assert "_renderRendererApp()" in (
        INTEGRATION / "frontend" / "features" / "trip-day-stop.js"
    ).read_text(encoding="utf-8"), "die Karte muss auch gerendert werden"


def verify_submitting_a_job_needs_edit_rights() -> None:
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"renderer_app_run"' in edit_block, (
        "einen Auftrag schreiben ist eine Änderung, kein Lesevorgang"
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
    # exec, or Home Assistant's SIGTERM never reaches the worker.
    assert "exec node" in code, "der Worker muss das Signal selbst empfangen"


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
    assert "0.0.0-dev" not in code, (
        "ein Platzhalter, der wie eine echte Version aussieht, gehoert nicht hinein"
    )
    # And the versions that describe the same app must agree.
    config_version = _config()["version"].strip('"')
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == config_version, (
        f"config.yaml sagt {config_version}, package.json sagt {package['version']}"
    )


def verify_the_app_has_no_runtime_dependencies() -> None:
    package = json.loads((APP / "package.json").read_text(encoding="utf-8"))
    assert package.get("dependencies") == {}, (
        "der PoC kommt ohne Fremdcode aus - die billigste Lieferkette, die es gibt"
    )
    assert (APP / "package-lock.json").is_file(), "die Sperrdatei macht den Build reproduzierbar"


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
verify_the_app_asks_for_nothing_beyond_a_shared_folder()
verify_nothing_is_installed_at_container_start()
verify_the_reported_version_survives_a_local_build()
verify_the_app_has_no_runtime_dependencies()
verify_one_repository_still_serves_both_consumers()
verify_stray_app_manifests_are_rejected_by_the_validator()
verify_the_app_ci_is_a_separate_workflow()
print("Renderer app contract tests passed.")
