"""Behavioral tests for the isolated ffmpeg subprocess runner.

Exercises the real ffmpeg binary when available (this environment has one
installed) for the success/failure/timeout paths - no mocking of the
subprocess itself, since the whole point of this module is correct process
lifecycle management (timeout kill, nonzero-exit surfacing).
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import shutil
import sys
import types

homeassistant = types.ModuleType("homeassistant")
homeassistant.__path__ = []
sys.modules.setdefault("homeassistant", homeassistant)

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_ffmpeg_runner_test"

package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

spec = spec_from_file_location(f"{PACKAGE_NAME}.ffmpeg_runner", PACKAGE_ROOT / "ffmpeg_runner.py")
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def verify_ffmpeg_available_reflects_the_real_environment() -> None:
    assert module.ffmpeg_available() == (shutil.which("ffmpeg") is not None)


def verify_a_trivial_render_succeeds() -> None:
    async def scenario() -> None:
        if not module.ffmpeg_available():
            print("ffmpeg not installed in this environment - skipping success-path test")
            return
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.mp4"
            await module.async_run_ffmpeg(
                [
                    "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                    "-frames:v", "5", "-pix_fmt", "yuv420p", str(output),
                ],
                timeout_seconds=30,
            )
            assert output.exists() and output.stat().st_size > 0

    asyncio.run(scenario())


def verify_a_failing_invocation_raises_roadplanner_error() -> None:
    async def scenario() -> None:
        if not module.ffmpeg_available():
            print("ffmpeg not installed in this environment - skipping failure-path test")
            return
        try:
            await module.async_run_ffmpeg(
                ["-i", "/nonexistent/definitely-not-a-real-file.mp4", "/tmp/should-not-be-created.mp4"],
                timeout_seconds=30,
            )
        except module.RoadplannerError:
            return
        raise AssertionError("expected a nonzero ffmpeg exit to raise RoadplannerError")

    asyncio.run(scenario())


def verify_a_timeout_kills_the_process_and_raises() -> None:
    async def scenario() -> None:
        if not module.ffmpeg_available():
            print("ffmpeg not installed in this environment - skipping timeout-path test")
            return
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "slow.mp4"
            try:
                await module.async_run_ffmpeg(
                    [
                        "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=30",
                        "-frames:v", "10000", "-pix_fmt", "yuv420p", str(output),
                    ],
                    timeout_seconds=0.2,
                )
            except module.RoadplannerError:
                return
            raise AssertionError("expected a slow ffmpeg run to time out and raise RoadplannerError")

    asyncio.run(scenario())


verify_ffmpeg_available_reflects_the_real_environment()
verify_a_trivial_render_succeeds()
verify_a_failing_invocation_raises_roadplanner_error()
verify_a_timeout_kills_the_process_and_raises()

print("ffmpeg runner tests passed.")
