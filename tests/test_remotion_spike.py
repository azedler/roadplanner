"""Diagnosis and supervision of the Remotion child process.

The supervision tests drive a REAL child process (a few lines of Python
pretending to be the renderer) rather than a mock, because everything that
is hard here happens at the process boundary: partial reads, a chatty
stream, a process that exits 0 without writing anything, a timeout that has
to end a whole process group.

Nothing in this file installs, downloads or renders anything.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import types

PACKAGE_NAME = "roadplanner_remotion_spike_test_pkg"
PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

for name, attrs in (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object}),
):
    stub = types.ModuleType(name)
    stub.__path__ = []
    for key, value in attrs.items():
        setattr(stub, key, value)
    sys.modules.setdefault(name, stub)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


diagnostics = load("remotion_diagnostics")
spike = load("remotion_spike")
RoadplannerError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].RoadplannerError


class FakeHass:
    def async_create_task(self, coro):
        return asyncio.ensure_future(coro)

    async def async_add_executor_job(self, func, *args):
        return func(*args)


# --- diagnosis ----------------------------------------------------------


def verify_a_missing_node_is_a_result_not_an_error() -> None:
    """The whole spike exists to learn this - it must report, not repair."""
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(
            diagnostics.async_diagnose(
                output_dir=Path(tmp), node_path="/nonexistent/node"
            )
        )
    assert result["status"] == diagnostics.SUBPROCESS_FORBIDDEN, result["status"]
    assert result["ready"] is False
    assert result["summary_de"]
    # The advice must never be "install something".
    assert "installier" not in result["recommended_next_step_de"].casefold()[:20]


def verify_a_blocking_finding_does_not_suppress_the_other_facts() -> None:
    """Live report: NODE_MISSING, and with it "ffmpeg/ffprobe: nein / nein"
    plus "Ausgabeordner beschreibbar: nein" - on a Home Assistant that
    renders trip videos with that very ffmpeg every week.

    The early return was the cause: no other check ever ran, and the report
    renders an empty field as "not found". A feasibility report is read as
    evidence, so an unchecked field must never look like a negative one.
    """
    import shutil as real_shutil

    real_which = real_shutil.which
    ffmpeg = real_which("ffmpeg")
    if not ffmpeg:
        return  # nothing to prove on a host without ffmpeg

    def which_without_node(name, *args, **kwargs):
        return None if name == "node" else real_which(name, *args, **kwargs)

    original = diagnostics.shutil.which
    diagnostics.shutil.which = which_without_node
    try:
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(diagnostics.async_diagnose(output_dir=Path(tmp)))
    finally:
        diagnostics.shutil.which = original

    assert result["status"] == diagnostics.NODE_MISSING, result["status"]
    details = result["details"]
    assert details["ffmpeg_path"] == ffmpeg, (
        f"ffmpeg was on this system all along, reported: {details.get('ffmpeg_path')!r}"
    )
    assert details["output_writable"] is True, (
        "a writable temp directory must not be reported as unwritable"
    )
    for key in ("npm_path", "ffprobe_path", "browser_path", "renderer_path"):
        assert key in details, f"{key} was never checked, so the report cannot state it"


def verify_every_status_has_a_message_and_an_envelope() -> None:
    for status in diagnostics.ALL_STATUSES:
        envelope = diagnostics.describe(status, {"x": 1})
        assert envelope["status"] == status
        assert envelope["ready"] is (status == diagnostics.READY)
        assert envelope["summary_de"]
        assert envelope["recommended_next_step_de"]
        assert envelope["details"] == {"x": 1}


def verify_a_real_node_is_probed_by_actually_running_it() -> None:
    """"On the PATH" is not the question - "can we spawn it" is."""
    import shutil

    node = shutil.which("node")
    if not node:
        return
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(diagnostics.async_diagnose(output_dir=Path(tmp)))
    details = result["details"]
    assert details["node_version"].startswith("v"), details
    assert details["output_writable"] is True
    # No renderer is configured in a test environment, so that is the
    # first thing that blocks - and it must be named precisely.
    assert result["status"] in {
        diagnostics.RENDERER_NOT_CONFIGURED,
        diagnostics.RENDERER_PACKAGE_MISSING,
        diagnostics.FFMPEG_MISSING,
        diagnostics.FFPROBE_MISSING,
        diagnostics.READY,
    }, result["status"]


def verify_an_unwritable_output_directory_is_named() -> None:
    result = asyncio.run(
        diagnostics.async_diagnose(output_dir=Path("/proc/roadplanner_denied"))
    )
    assert result["status"] in {
        diagnostics.OUTPUT_NOT_WRITABLE,
        diagnostics.NODE_MISSING,
        diagnostics.SUBPROCESS_FORBIDDEN,
    }, result["status"]


# --- supervision --------------------------------------------------------

_FAKE_RENDERER = '''
import json, sys, time
job = json.load(open(sys.argv[1]))
mode = job["input"]["subtitle"]
out = job["output_path"]
print(json.dumps({"event": "started", "job_id": job["job_id"]}), flush=True)
print("chatty log line that is not protocol", flush=True)
print(json.dumps({"event": "progress", "progress": 0.5}), flush=True)
if mode == "hang":
    time.sleep(600)
if mode == "fail":
    print(json.dumps({"event": "failed", "code": "BROWSER_MISSING",
                      "message": "Kein Browser."}), flush=True)
    sys.exit(1)
if mode == "silent-zero":
    sys.exit(0)
if mode == "nofile":
    print(json.dumps({"event": "completed", "output_path": out}), flush=True)
    sys.exit(0)
open(out, "wb").write(b"x" * 4096)
print(json.dumps({"event": "completed", "output_path": out}), flush=True)
'''


def _service(tmp: Path, renderer: Path, subtitle: str):
    service = spike.RemotionSpikeService(FakeHass(), output_dir=tmp)

    async def fake_diagnose():
        return diagnostics.describe(
            diagnostics.READY,
            {
                "node_path": sys.executable,
                "renderer_path": str(renderer),
                "browser_path": "/fake/browser",
            },
        )

    service.async_diagnose = fake_diagnose
    original = spike.build_job

    def build(**kwargs):
        return original(**{**kwargs, "subtitle": subtitle})

    spike.build_job = build
    return service, original


def _run(tmp: Path, subtitle: str, *, timeout: float | None = None):
    renderer_root = tmp / "renderer" / "scripts"
    renderer_root.mkdir(parents=True, exist_ok=True)
    renderer = renderer_root / "render.py"
    renderer.write_text(_FAKE_RENDERER, encoding="utf-8")
    service, original = _service(tmp, renderer, subtitle)
    if timeout is not None:
        spike.RENDER_TIMEOUT_SECONDS = timeout
    try:
        asyncio.run(service._async_run("job1"))
    finally:
        spike.build_job = original
        spike.RENDER_TIMEOUT_SECONDS = 900.0
    return service


def verify_a_chatty_renderer_still_reports_progress() -> None:
    """A non-protocol line in the middle must be ignored, not fatal."""
    with tempfile.TemporaryDirectory() as tmp:
        service = _run(Path(tmp), "ok")
    status = service._status
    # ffprobe will reject the 4 KB of "x" as not a video - which is the
    # point: exit code 0 is a claim, the probe is the check.
    assert status["state"] == "error"
    assert status["status"] == diagnostics.OUTPUT_INVALID or "ffprobe" in str(
        status.get("error", "")
    ), status
    assert status.get("progress", 0) >= 0.5, "the progress event was read"


def verify_a_failure_event_names_its_code() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _run(Path(tmp), "fail")
    assert service._status["state"] == "error"
    assert service._status["status"] == "BROWSER_MISSING", service._status
    assert "Browser" in service._status["error"]


def verify_exit_zero_without_a_completion_is_refused() -> None:
    """A renderer that just stops must not count as a success."""
    with tempfile.TemporaryDirectory() as tmp:
        service = _run(Path(tmp), "silent-zero")
    assert service._status["state"] == "error"
    assert service._status["status"] == diagnostics.OUTPUT_INVALID


def verify_a_claimed_output_that_does_not_exist_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _run(Path(tmp), "nofile")
    assert service._status["state"] == "error"
    assert service._status["status"] == diagnostics.OUTPUT_INVALID


def verify_a_hanging_renderer_is_killed_and_reported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = _run(Path(tmp), "hang", timeout=2.0)
    assert service._status["state"] == "error"
    assert service._status["status"] == diagnostics.RENDER_TIMEOUT, service._status


def verify_a_second_run_is_refused_while_one_is_active() -> None:
    ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError
    service = spike.RemotionSpikeService(FakeHass(), output_dir=Path("/tmp"))

    async def scenario():
        async def forever():
            await asyncio.sleep(30)

        service._task = asyncio.ensure_future(forever())
        try:
            service.async_start(job_id="job2")
        except ValidationError:
            pass
        else:
            raise AssertionError("a parallel run must be refused")
        finally:
            service._task.cancel()

    asyncio.run(scenario())


def verify_a_restart_does_not_leave_a_job_running_forever() -> None:
    service = spike.RemotionSpikeService(FakeHass(), output_dir=Path("/tmp"))
    # Exactly the state a restart leaves behind: status says running, the
    # task that would have finished it is gone.
    service._status = {"state": "running", "job_id": "job1"}
    service._task = None
    status = asyncio.run(service.async_status())
    assert status["state"] == "error"
    assert status["status"] == diagnostics.RENDER_CANCELLED


def verify_the_wiring_never_uses_a_shell() -> None:
    """Checked on the parsed CODE, not on the prose.

    A text search trips over this file's own comments explaining that
    ``shell=True`` is forbidden - which would make the test pass or fail
    for the wrong reason.
    """
    import ast

    for name in ("remotion_spike.py", "remotion_diagnostics.py"):
        tree = ast.parse((PACKAGE_ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    assert keyword.arg != "shell", f"shell= used in {name}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "create_subprocess_shell", name

    source = (PACKAGE_ROOT / "remotion_spike.py").read_text(encoding="utf-8")
    assert "start_new_session=True" in source, (
        "a timeout must be able to end the browser, not only the Node parent"
    )
    assert "killpg" in source, "the whole process group has to go"

    # No string the diagnosis could ever EXECUTE mentions installing
    # anything. Comments and docstrings are prose - this module's own
    # docstring says it never runs `npm install`, and that sentence must
    # not be what the test finds.
    tree = ast.parse((PACKAGE_ROOT / "remotion_diagnostics.py").read_text(encoding="utf-8"))
    docstrings = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    literals = [
        node.value.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
    joined = " ".join(literals)
    for forbidden in ("npm install", "npm ci", "ensurebrowser", "apt-get", "apk add"):
        assert forbidden not in joined, forbidden


verify_a_missing_node_is_a_result_not_an_error()
verify_a_blocking_finding_does_not_suppress_the_other_facts()
verify_every_status_has_a_message_and_an_envelope()
verify_a_real_node_is_probed_by_actually_running_it()
verify_an_unwritable_output_directory_is_named()
verify_a_chatty_renderer_still_reports_progress()
verify_a_failure_event_names_its_code()
verify_exit_zero_without_a_completion_is_refused()
verify_a_claimed_output_that_does_not_exist_is_refused()
verify_a_hanging_renderer_is_killed_and_reported()
verify_a_second_run_is_refused_while_one_is_active()
verify_a_restart_does_not_leave_a_job_running_forever()
verify_the_wiring_never_uses_a_shell()
print("Remotion spike tests passed.")
