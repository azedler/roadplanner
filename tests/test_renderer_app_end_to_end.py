"""The two implementations, talking to each other through a real folder.

The protocol tests check the rules; this checks that the two independent
implementations actually agree. They share no code on purpose - the Python
side and the Node side each parse the JSON themselves - so only an
end-to-end run can prove the agreement holds.

The worker is started as a real subprocess against a real directory,
because the properties under test are filesystem properties: an atomic
rename, a claim that cannot happen twice, a restart that does not leave a
job running forever. A mocked filesystem would assert the design rather
than the behaviour.
"""
from __future__ import annotations

from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
WORKER = Path("apps/roadplanner_renderer/src/index.mjs").resolve()


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


protocol = load("renderer_app_protocol")

NODE = shutil.which("node")


class Worker:
    """Start and stop the real app the way Home Assistant would."""

    def __init__(self, exchange: Path) -> None:
        self._exchange = exchange
        self._process: subprocess.Popen | None = None

    def __enter__(self) -> "Worker":
        self._process = subprocess.Popen(
            [NODE, str(WORKER)],
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "ROADPLANNER_EXCHANGE_DIR": str(self._exchange),
                "ROADPLANNER_APP_VERSION": "0.1.0-poc.1",
                "ROADPLANNER_POLL_MS": "150",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def stop(self, *, signal_only: bool = False) -> int | None:
        if self._process is None or self._process.poll() is not None:
            return None if self._process is None else self._process.returncode
        # SIGTERM is what Home Assistant sends; a container that needs to be
        # killed would look like a crash in the restart tests.
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
            raise AssertionError("Die App hat auf SIGTERM nicht reagiert")
        return self._process.returncode


def wait_for(predicate, *, what: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Zeitüberschreitung beim Warten auf {what}")


def drained(folder: Path):
    """A predicate for a folder that has to become empty.

    The worker writes the terminal status *before* it removes the claimed
    file, and that order is deliberate: the file left in `processing/` is
    exactly what lets the orphan sweep fail a job whose worker died. So a
    test that proceeds the moment a result appears can legitimately catch
    the file still lying there - the emptiness is eventual, not immediate,
    and asserting it on the spot is a race, not a check.
    """

    def empty():
        # A truthy return value is what wait_for treats as success, so the
        # empty list cannot be returned directly.
        return not list(folder.iterdir()) or None

    return empty


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def wait_until_ready(exchange: Path) -> dict:
    """Wait for a heartbeat that actually says ready.

    The first beat is written while the worker is still starting up, so
    waiting merely for the file to appear makes every test that follows a
    race against the app's own startup.
    """

    def ready():
        beat = read_json(exchange / "renderer-status.json")
        if not beat:
            return None
        parsed = protocol.validate_heartbeat(beat, now=protocol.utc_now())
        return parsed if parsed["online"] else None

    return wait_for(ready, what="einsatzbereiten Heartbeat")


def wait_for_terminal(exchange: Path, job_id: str) -> dict:
    """Wait for a status that has actually settled.

    A job passes through claimed and running on its way to a terminal
    state, so reading the first status file that appears asserts against
    whichever moment the test happened to catch.
    """

    def settled():
        raw = read_json(exchange / "status" / f"{job_id}.json")
        if not raw:
            return None
        parsed = protocol.validate_status(raw, job_id=job_id)
        return parsed if parsed["terminal"] else None

    return wait_for(settled, what=f"terminalen Status von {job_id[:8]}")


def submit(exchange: Path, job: dict) -> None:
    """Write a job the way the client does - temp file, then rename."""
    target = exchange / "jobs" / protocol.job_filename(job["job_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(job), encoding="utf-8")
    temporary.replace(target)


def verify_a_job_travels_all_the_way_and_back() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exchange = Path(tmp)
        with Worker(exchange) as worker:
            heartbeat = wait_until_ready(exchange)
            assert heartbeat["app_version"] == "0.1.0-poc.1", heartbeat

            job = protocol.build_job(
                job_id=protocol.new_job_id(),
                message="Hallo Renderer",
                now=protocol.utc_now(),
            )
            submit(exchange, job)
            job_id = job["job_id"]

            result_file = exchange / "results" / job_id / "result.json"
            wait_for(lambda: read_json(result_file), what="Ergebnis")

            status = protocol.validate_status(
                read_json(exchange / "status" / f"{job_id}.json"), job_id=job_id
            )
            assert status["state"] == "completed", status
            assert status["terminal"] is True

            result = protocol.validate_result(read_json(result_file), job_id=job_id)
            assert len(result["artifacts"]) == 2, result

            # The declared hashes must match the bytes actually written -
            # the two sides computed them independently.
            for declared in result["artifacts"]:
                data = (exchange / "results" / job_id / declared["filename"]).read_bytes()
                protocol.verify_artifact(data, declared)

            svg = (exchange / "results" / job_id / protocol.ARTIFACT_IMAGE).read_text(
                encoding="utf-8"
            )
            assert svg.startswith("<svg"), svg[:60]
            assert job_id[:8] in svg, "das Testbild nennt den Auftrag"

            # The job file is gone: nothing is left to be picked up twice.
            assert not (exchange / "jobs" / f"{job_id}.json").exists()
            wait_for(drained(exchange / "processing"), what="geleertes processing/")

            assert worker.stop() == 0, "sauberes Ende auf SIGTERM"


def verify_a_broken_job_file_does_not_take_the_app_down() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exchange = Path(tmp)
        with Worker(exchange) as worker:
            wait_until_ready(exchange)
            (exchange / "jobs").mkdir(parents=True, exist_ok=True)
            broken_id = protocol.new_job_id()
            (exchange / "jobs" / f"{broken_id}.json").write_text("{kaputt", encoding="utf-8")

            parsed = wait_for_terminal(exchange, broken_id)
            assert parsed["state"] == "failed", parsed
            assert parsed["error"]["code"] == protocol.ERROR_INVALID_JOB, parsed

            # And the app is still alive and still takes work.
            good = protocol.build_job(job_id=protocol.new_job_id(), now=protocol.utc_now())
            submit(exchange, good)
            wait_for(
                lambda: read_json(exchange / "results" / good["job_id"] / "result.json"),
                what="Ergebnis nach kaputter Datei",
            )
            assert worker.stop() == 0


def verify_an_expired_job_is_reported_not_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        exchange = Path(tmp)
        with Worker(exchange) as worker:
            wait_until_ready(exchange)
            job_id = protocol.new_job_id()
            stale = protocol.build_job(
                job_id=job_id,
                now=protocol.utc_now() - timedelta(seconds=600),
                ttl_seconds=60,
            )
            submit(exchange, stale)
            parsed = wait_for_terminal(exchange, job_id)
            assert parsed["state"] == "expired", parsed
            assert not (exchange / "results" / job_id).exists(), (
                "ein abgelaufener Auftrag darf nichts produzieren"
            )
            assert worker.stop() == 0


def verify_a_restart_does_not_leave_a_job_running_forever() -> None:
    """A job interrupted by a restart must resolve, not hang.

    This is the failure the PoC exists to rule out: the worker dies
    mid-job, and Roadplanner is left polling a status that nobody will ever
    write again.
    """
    with tempfile.TemporaryDirectory() as tmp:
        exchange = Path(tmp)
        job_id = protocol.new_job_id()
        for name in ("jobs", "processing", "status", "results"):
            (exchange / name).mkdir(parents=True, exist_ok=True)
        # Exactly what a killed worker leaves behind: a claimed job.
        (exchange / "processing" / f"{job_id}.json").write_text(
            json.dumps(protocol.build_job(job_id=job_id, now=protocol.utc_now())),
            encoding="utf-8",
        )
        (exchange / "status" / f"{job_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "protocol_version": 1,
                    "job_id": job_id,
                    "state": "running",
                    "progress": 0.5,
                    "updated_at": protocol.format_time(protocol.utc_now()),
                }
            ),
            encoding="utf-8",
        )

        with Worker(exchange) as worker:
            parsed = wait_for_terminal(exchange, job_id)
            assert parsed["terminal"] is True, parsed
            assert parsed["error"]["code"] == "INTERRUPTED", parsed
            # Same order as in the normal path: the status is written
            # first, the claimed file disappears a moment later.
            wait_for(
                drained(exchange / "processing"),
                what="aufgeräumten übernommenen Auftrag",
            )
            assert worker.stop() == 0


def verify_the_same_job_id_cannot_produce_two_jobs() -> None:
    """Re-submitting an id after a Home Assistant restart is idempotent.

    The id IS the filename, so a second write replaces the first rather
    than queueing another job. That is the property that makes a restart
    safe without any bookkeeping on the Home Assistant side.
    """
    with tempfile.TemporaryDirectory() as tmp:
        exchange = Path(tmp)
        with Worker(exchange) as worker:
            wait_until_ready(exchange)
            job_id = protocol.new_job_id()
            job = protocol.build_job(job_id=job_id, now=protocol.utc_now())
            submit(exchange, job)
            submit(exchange, job)
            wait_for(
                lambda: read_json(exchange / "results" / job_id / "result.json"),
                what="Ergebnis",
            )
            time.sleep(0.6)
            results = [p.name for p in (exchange / "results").iterdir()]
            assert results == [job_id], results
            assert worker.stop() == 0


if NODE is None:
    print("Renderer app end-to-end tests skipped (no node).")
else:
    verify_a_job_travels_all_the_way_and_back()
    verify_a_broken_job_file_does_not_take_the_app_down()
    verify_an_expired_job_is_reported_not_run()
    verify_a_restart_does_not_leave_a_job_running_forever()
    verify_the_same_job_id_cannot_produce_two_jobs()
    print("Renderer app end-to-end tests passed.")
