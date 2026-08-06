"""Cross-file wiring for the Remotion spike, and the boundaries it must keep.

Each half of a feature can look fine on its own while the feature is
unreachable or, worse, while a boundary has quietly moved. These checks
pin the things that are only visible across files: that the spike is
reachable from the panel, that it stays out of the production video path,
and that nothing in it installs anything.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(".")
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"


def verify_the_spike_is_reachable_from_the_panel() -> None:
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    frontend = (INTEGRATION / "frontend" / "roadplanner-panel.js").read_text(
        encoding="utf-8"
    )
    feature = (INTEGRATION / "frontend" / "features" / "remotion-spike.js").read_text(
        encoding="utf-8"
    )
    for action in (
        "remotion_diagnose",
        "remotion_test_render",
        "remotion_status",
        "remotion_cancel",
    ):
        assert f'"{action}"' in panel, f"{action} must be a registered panel action"
        assert f'if action == "{action}"' in panel, f"{action} needs a branch"
    for click in (
        "remotion-diagnose",
        "remotion-test-render",
        "remotion-cancel",
        "remotion-copy-report",
    ):
        assert f'action === "{click}"' in frontend, f"{click} must be dispatched"
        assert f'data-action="{click}"' in feature, f"{click} needs a button"
    assert "remotionSpikeMixin" in frontend, "the mixin must be registered"


def verify_the_render_button_waits_for_a_ready_diagnosis() -> None:
    feature = (INTEGRATION / "frontend" / "features" / "remotion-spike.js").read_text(
        encoding="utf-8"
    )
    assert "diagnosis?.ready" in feature, (
        "rendering must not be offered before the environment says it can"
    )
    assert "_remotionDiagnosis?.ready" in feature


def verify_starting_a_render_needs_edit_rights() -> None:
    panel = (INTEGRATION / "panel.py").read_text(encoding="utf-8")
    edit_block = panel.split("_EDIT_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"remotion_test_render"' in edit_block
    assert '"remotion_cancel"' in edit_block
    provider_block = panel.split("_PROVIDER_CALL_ACTIONS = {", 1)[1].split("}", 1)[0]
    assert '"remotion_test_render"' in provider_block, (
        "a dropped connection must not orphan the child process"
    )


def verify_the_production_video_path_is_untouched() -> None:
    """The spike must not be able to overwrite the trip's video."""
    spike = (INTEGRATION / "remotion_spike.py").read_text(encoding="utf-8")
    for foreign in ("trip_video", "TripVideoExporter", "last_video", "library_dir"):
        assert foreign not in spike, f"the spike must not touch {foreign}"
    wiring = (INTEGRATION / "__init__.py").read_text(encoding="utf-8")
    assert 'archive_root / "remotion_spike"' in wiring, (
        "the spike writes into its own folder, not the video library"
    )


def verify_nothing_is_installed_on_the_user_system() -> None:
    for name in ("remotion_spike.py", "remotion_diagnostics.py", "remotion_job.py"):
        source = (INTEGRATION / name).read_text(encoding="utf-8")
        # Comments are prose; strip them and look at what could run.
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        for forbidden in ("npm ci", "apt-get", "apk add", "pip install"):
            assert forbidden not in code, f"{name} must never run {forbidden}"
    renderer = (ROOT / "remotion_renderer" / "scripts" / "render.mjs").read_text(
        encoding="utf-8"
    )
    assert "allow_downloads" in renderer, "the renderer must refuse download mode"


def verify_the_dependencies_are_pinned_with_a_lockfile() -> None:
    package = json.loads(
        (ROOT / "remotion_renderer" / "package.json").read_text(encoding="utf-8")
    )
    assert (ROOT / "remotion_renderer" / "package-lock.json").is_file(), (
        "the lockfile is what makes a CI render reproducible"
    )
    versions = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }
    assert versions, "the renderer has dependencies"
    for name, spec in versions.items():
        assert spec[0].isdigit(), f"{name} is not pinned exactly: {spec}"
    remotion_versions = {
        spec for name, spec in versions.items()
        if name == "remotion" or name.startswith("@remotion/")
    }
    assert len(remotion_versions) == 1, (
        f"all Remotion packages must share one version: {remotion_versions}"
    )


def verify_the_ci_render_is_a_separate_workflow() -> None:
    """An experiment must never be able to block an ordinary release."""
    workflow = ROOT / ".github" / "workflows" / "remotion-spike.yml"
    assert workflow.is_file(), "the render belongs in its own workflow"
    text = workflow.read_text(encoding="utf-8")
    assert "ffprobe" in text, "CI must validate the produced file, not just exit 0"
    assert "npx remotion browser ensure" in text, (
        "the browser is provisioned in CI only - never on a user's system"
    )
    validation = (ROOT / ".github" / "workflows" / "roadplanner-validation.yml").read_text(
        encoding="utf-8"
    )
    assert "remotion" not in validation.casefold(), (
        "the normal validation job must not depend on the experiment"
    )


def verify_installed_dependencies_are_excluded_from_repo_validation() -> None:
    """node_modules is a working copy, not repository content."""
    validator = (ROOT / "tools" / "validate_repository.py").read_text(encoding="utf-8")
    assert "IGNORED_DIRECTORY_NAMES" in validator
    assert "node_modules" in validator
    assert 'if ".git" in path.parts' not in validator, (
        "every walk must use the shared exclusion, not a git-only check"
    )
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore


def verify_the_options_default_to_installing_nothing() -> None:
    const = (INTEGRATION / "const.py").read_text(encoding="utf-8")
    assert 'DEFAULT_REMOTION_RENDERER_PATH = ""' in const
    assert 'DEFAULT_REMOTION_BROWSER_PATH = ""' in const, (
        "an empty default is what keeps the spike opt-in"
    )


verify_the_spike_is_reachable_from_the_panel()
verify_the_render_button_waits_for_a_ready_diagnosis()
verify_starting_a_render_needs_edit_rights()
verify_the_production_video_path_is_untouched()
verify_nothing_is_installed_on_the_user_system()
verify_the_dependencies_are_pinned_with_a_lockfile()
verify_the_ci_render_is_a_separate_workflow()
verify_installed_dependencies_are_excluded_from_repo_validation()
verify_the_options_default_to_installing_nothing()
print("Remotion spike contract tests passed.")
