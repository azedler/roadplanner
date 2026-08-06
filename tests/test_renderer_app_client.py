"""The Home Assistant side: what it reports when the app is not there.

Almost every user will never install this app, so the interesting
behaviour is the absence cases. Three of them look similar in the folder
and mean entirely different things, and conflating them would either alarm
people who have nothing wrong or hide a real result.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def _install_homeassistant_stub() -> None:
    """The client imports HomeAssistant for typing only; stub it."""
    if "homeassistant" in sys.modules:
        return
    homeassistant = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")

    class HomeAssistant:  # noqa: D401 - stand-in
        """Stand-in for the real class."""

    core.HomeAssistant = HomeAssistant
    components = types.ModuleType("homeassistant.components")
    hassio = types.ModuleType("homeassistant.components.hassio")
    hassio.is_hassio = lambda hass: bool(getattr(hass, "supervisor", False))
    homeassistant.core = core
    homeassistant.components = components
    components.hassio = hassio
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.core": core,
            "homeassistant.components": components,
            "homeassistant.components.hassio": hassio,
        }
    )


_install_homeassistant_stub()


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sys.modules["roadplanner_renderer_app_protocol"] = load("renderer_app_protocol")
protocol = sys.modules["roadplanner_renderer_app_protocol"]

# The client imports its protocol as a relative module; give it that name.
spec = importlib.util.spec_from_file_location(
    "renderer_pkg.renderer_app_client", PACKAGE_ROOT / "renderer_app_client.py"
)
package = types.ModuleType("renderer_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["renderer_pkg"] = package
sys.modules["renderer_pkg.renderer_app_protocol"] = protocol
client_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = client_module
spec.loader.exec_module(client_module)


class FakeHass:
    def __init__(self, *, supervisor: bool) -> None:
        self.supervisor = supervisor

    async def async_add_executor_job(self, func, *args):
        return func(*args)


def make_client(tmp: str, *, supervisor: bool = True):
    return client_module.RendererAppClient(
        FakeHass(supervisor=supervisor), exchange_dir=Path(tmp) / "poc-v1"
    )


def verify_no_supervisor_is_the_answer_not_a_fault() -> None:
    """Apps exist only under Home Assistant OS or Supervised.

    On Container or Core no app can ever be installed. Reporting that
    plainly is the whole point - discovering it later as "the app never
    showed up" would waste the user's evening.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = asyncio.run(make_client(tmp, supervisor=False).async_environment())
    assert result["status"] == client_module.ENV_NO_SUPERVISOR, result
    assert result["ready"] is False
    assert "Supervisor" in result["summary_de"]
    assert "installier" not in result["recommended_next_step_de"].casefold()[:30]


def verify_a_writable_exchange_folder_reports_ready() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp, supervisor=True)
        result = asyncio.run(client.async_environment())
        details = result["details"]
        assert details["exchange_writable"] is True, details
        # The subdirectories both sides rely on must exist afterwards.
        for name in ("jobs", "processing", "status", "results"):
            assert (client.exchange_dir / name).is_dir(), name
        # On a machine without /share the probe stops there, which is
        # correct - it is the next thing that blocks.
        assert result["status"] in {
            client_module.ENV_READY,
            client_module.ENV_SHARE_MISSING,
        }, result["status"]


def verify_a_missing_app_is_not_an_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        status = asyncio.run(make_client(tmp).async_app_status())
    assert status["installed"] is False, status
    assert status["online"] is False
    assert status["reason"], "die Abwesenheit wird erklärt"


def verify_a_corrupt_heartbeat_is_reported_not_raised() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        client.exchange_dir.mkdir(parents=True, exist_ok=True)
        (client.exchange_dir / "renderer-status.json").write_text("{kaputt", encoding="utf-8")
        status = asyncio.run(client.async_app_status())
    assert status["installed"] is True, status
    assert status["online"] is False
    assert "JSON" in status["reason"], status


def verify_a_job_is_written_atomically_and_only_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job = asyncio.run(client.async_submit_test_job())
        jobs = sorted((client.exchange_dir / "jobs").iterdir())
        assert [p.name for p in jobs] == [f"{job['job_id']}.json"], jobs
        # No temporary file may survive: a leftover .part would be read as
        # nothing by the worker but would clutter a shared folder forever.
        assert not any(p.suffix == ".part" for p in jobs), jobs
        written = json.loads(jobs[0].read_text(encoding="utf-8"))
        protocol.validate_job(written, now=protocol.utc_now())


def verify_an_unknown_job_is_reported_as_unknown() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        status = asyncio.run(client.async_job_status(protocol.new_job_id()))
    assert status["state"] is None, status
    assert status["terminal"] is False
    assert status["reason"], status


def verify_a_forged_artifact_is_refused() -> None:
    """The hash check has to look at the bytes, not at the claim."""
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        job_id = protocol.new_job_id()
        folder = client.exchange_dir / "results" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        honest = b"echte Bytes"
        (folder / protocol.ARTIFACT_TEXT).write_bytes(b"andere Bytes")
        (folder / "result.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_version": 1,
                    "job_id": job_id,
                    "state": "completed",
                    "artifacts": [
                        {
                            "kind": "text",
                            "filename": protocol.ARTIFACT_TEXT,
                            "size_bytes": len(honest),
                            "sha256": hashlib.sha256(honest).hexdigest(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        try:
            asyncio.run(client.async_result(job_id))
        except protocol.RendererProtocolError:
            return
    raise AssertionError("Ein manipuliertes Artefakt wurde übernommen")


def verify_a_symlink_in_the_exchange_folder_is_not_followed() -> None:
    """Another container can write there; a link out of it must not work."""
    with tempfile.TemporaryDirectory() as tmp:
        client = make_client(tmp)
        asyncio.run(client.async_environment())
        secret = Path(tmp) / "secret.txt"
        secret.write_text("geheim", encoding="utf-8")
        link = client.exchange_dir / "renderer-status.json"
        try:
            link.symlink_to(secret)
        except OSError:
            return  # no symlink support here; nothing to prove
        try:
            asyncio.run(client.async_app_status())
        except protocol.RendererProtocolError as err:
            assert "Symlink" in str(err), err
            return
    raise AssertionError("Einem Symlink wurde gefolgt")


verify_no_supervisor_is_the_answer_not_a_fault()
verify_a_writable_exchange_folder_reports_ready()
verify_a_missing_app_is_not_an_error()
verify_a_corrupt_heartbeat_is_reported_not_raised()
verify_a_job_is_written_atomically_and_only_once()
verify_an_unknown_job_is_reported_as_unknown()
verify_a_forged_artifact_is_refused()
verify_a_symlink_in_the_exchange_folder_is_not_followed()
print("Renderer app client tests passed.")
