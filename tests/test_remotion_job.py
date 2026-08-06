"""The Remotion job and event contract - the code that faces a foreign process.

Everything this module produces leaves Python and reaches a rendering
engine, so the rules are stricter than anywhere else: the composition comes
from a whitelist, the output path is built server-side and proven to stay
inside the export folder, and downloads are pinned off for the whole spike.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile

MODULE_PATH = Path("custom_components/roadplanner_mcp/remotion_job.py")
spec = spec_from_file_location("roadplanner_remotion_job_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
JobError = module.RemotionJobError


def verify_the_output_path_cannot_leave_the_export_folder() -> None:
    """The job id becomes a filename, so it is validated, never trusted."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "exports"
        root.mkdir()
        good = module.resolve_output_path(root, "remotion_spike_abc123")
        assert good.parent == root.resolve()
        assert good.name == "remotion_spike_abc123.mp4"

        for hostile in (
            "../escape",
            "../../etc/passwd",
            "a/b",
            "with space",
            "semi;colon",
            "",
            "x" * 200,
        ):
            try:
                module.resolve_output_path(root, hostile)
            except JobError:
                continue
            raise AssertionError(f"accepted a hostile job id: {hostile!r}")


def verify_a_symlinked_export_folder_is_still_contained() -> None:
    """A path that only LOOKS contained can point elsewhere through a link."""
    with tempfile.TemporaryDirectory() as tmp:
        real = Path(tmp) / "real"
        real.mkdir()
        link = Path(tmp) / "link"
        link.symlink_to(real)
        # Resolving both sides means the link is followed consistently
        # rather than compared as text.
        resolved = module.resolve_output_path(link, "job1")
        assert resolved.parent == real.resolve()


def verify_only_whitelisted_work_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "job.mp4"
        job = module.build_job(job_id="job1", output_path=output)
        assert job["schema_version"] == module.JOB_SCHEMA_VERSION
        assert job["composition"] in module.ALLOWED_COMPOSITIONS
        # Downloads are pinned OFF for the whole spike: the renderer must
        # never fetch a browser onto the user's machine by itself.
        assert job["runtime"]["allow_downloads"] is False

        for kwargs in (
            {"composition": "etwas-anderes"},
            {"codec": "prores"},
            {"duration_seconds": 0},
            {"duration_seconds": 999},
            {"width": 4096},
            {"height": 4},
            {"fps": 0},
            {"fps": 240},
            {"title": "   "},
            {"job_id": "bad id"},
        ):
            try:
                module.build_job(
                    **{"job_id": "job1", "output_path": output, **kwargs}
                )
            except JobError:
                continue
            raise AssertionError(f"accepted out-of-contract input: {kwargs}")

        # Long text is bounded, not rejected - it reaches a renderer.
        long_job = module.build_job(
            job_id="job1", output_path=output, title="T" * 500
        )
        assert len(long_job["input"]["title"]) <= module.MAX_TEXT_LENGTH


def verify_a_job_read_back_is_validated_again() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        job = module.build_job(job_id="job1", output_path=Path(tmp) / "j.mp4")
        assert module.validate_job(job) is job
        for broken in (
            {**job, "schema_version": 2},
            {**job, "composition": "fremd"},
            {**job, "render": {**job["render"], "codec": "vp9"}},
            {**job, "runtime": {"allow_downloads": True}},
            "kein objekt",
        ):
            try:
                module.validate_job(broken)
            except JobError:
                continue
            raise AssertionError(f"accepted a broken job: {broken}")


def verify_event_parsing_survives_a_chatty_renderer() -> None:
    """A stray log line must not corrupt the protocol or kill the run."""
    assert module.parse_event_line('{"event":"started","job_id":"j"}')["job_id"] == "j"
    assert module.parse_event_line('{"event":"progress","progress":0.5}')["progress"] == 0.5
    # Not protocol: ignored, not fatal.
    for noise in (
        "",
        "   ",
        "Bundling 42%",
        "{not json",
        '{"event":"unbekannt"}',
        '["a","list"]',
        '"a string"',
    ):
        assert module.parse_event_line(noise) is None, noise
    # An over-long line IS a protocol violation - it could exhaust memory.
    try:
        module.parse_event_line('{"event":"progress","x":"' + "y" * 20_000 + '"}')
    except JobError:
        pass
    else:
        raise AssertionError("an unbounded event line must be refused")


def verify_progress_is_clamped_and_optional() -> None:
    assert module.progress_of({"progress": 0.5}) == 0.5
    assert module.progress_of({"progress": -3}) == 0.0
    assert module.progress_of({"progress": 12}) == 1.0
    assert module.progress_of({}) is None
    assert module.progress_of({"progress": "halb"}) is None
    # bool is an int in Python - it must not pass as progress.
    assert module.progress_of({"progress": True}) is None


def verify_stored_detail_is_stripped_and_bounded() -> None:
    coloured = "\x1b[31mFehler\x1b[0m im\nRenderer"
    assert module.clean_detail(coloured) == "Fehler im Renderer"
    assert len(module.clean_detail("x" * 5_000)) == 600
    assert module.clean_detail(None) == ""


verify_the_output_path_cannot_leave_the_export_folder()
verify_a_symlinked_export_folder_is_still_contained()
verify_only_whitelisted_work_is_accepted()
verify_a_job_read_back_is_validated_again()
verify_event_parsing_survives_a_chatty_renderer()
verify_progress_is_clamped_and_optional()
verify_stored_detail_is_stripped_and_bounded()
print("Remotion job contract tests passed.")
