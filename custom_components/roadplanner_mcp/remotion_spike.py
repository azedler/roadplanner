"""Run the Remotion test render as a supervised child process.

This is the Home Assistant half of the spike. It reuses the shape the
video export already established - start a background job, poll a status,
keep a durable artefact - rather than inventing a second mechanism next to
it. What is genuinely new here is only the protocol: the child speaks
JSONL on stdout instead of being a black box that either exits 0 or not.

Deliberate boundaries, all of them load-bearing for a feasibility test:

- **Nothing is installed or downloaded.** If the runtime is absent, the
  diagnosis says so and the run stops. A spike that fixed its own
  preconditions would answer a different question than the one asked.
- **Never ``shell=True``**, never a composed command line. The executable
  and the script are absolute paths that were verified to exist.
- **The output path is built here**, never taken from the client, and is
  proven to resolve inside the Roadplanner export directory.
- **The result is written to a temporary name** and only becomes the
  artefact after ffprobe confirms what it is.
- **The production video export is untouched.** This has its own status,
  its own folder and its own library entry, so a test render can never be
  mistaken for - or overwrite - "Letztes Video".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import logging
import shutil
from typing import Any

from homeassistant.core import HomeAssistant

from .experience_store import utc_now_iso
from .remotion_diagnostics import (
    OUTPUT_INVALID,
    READY,
    RENDER_CANCELLED,
    RENDER_FAILED,
    RENDER_TIMEOUT,
    async_diagnose,
    describe,
)
from .remotion_job import (
    MAX_STDERR_BYTES,
    RemotionJobError,
    build_job,
    clean_detail,
    parse_event_line,
    progress_of,
    resolve_output_path,
)
from .roadplanner import RoadplannerError, ValidationError

_LOGGER = logging.getLogger(__name__)

# A five-second 720p render took a few seconds on a developer machine; a
# small Home Assistant box is much slower, and the bundle step dominates
# the first run. Generous, but bounded - this is a test, not a service.
RENDER_TIMEOUT_SECONDS = 900.0
_TERMINATE_GRACE_SECONDS = 10.0
_EXPECTED = {"width": 1280, "height": 720, "duration_seconds": 5.0}


class RemotionSpikeService:
    """Diagnose the runtime and, if asked, render one test video."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        output_dir: Path,
        renderer_path: str = "",
        browser_path: str = "",
    ) -> None:
        self.hass = hass
        self.output_dir = output_dir
        self.renderer_path = str(renderer_path or "")
        self.browser_path = str(browser_path or "")
        self._status: dict[str, Any] = {"state": "idle"}
        self._task: Any = None
        self._process: Any = None

    # --- diagnosis ----------------------------------------------------

    async def async_diagnose(self) -> dict[str, Any]:
        return await async_diagnose(
            output_dir=self.output_dir,
            renderer_path=self.renderer_path,
            browser_path=self.browser_path,
        )

    # --- status -------------------------------------------------------

    async def async_status(self) -> dict[str, Any]:
        status = dict(self._status)
        # A job that was running when Home Assistant went down cannot still
        # be running now: nothing survives a restart. Reporting it as
        # running forever is the trap the video export already fell into.
        if status.get("state") == "running" and (
            self._task is None or self._task.done()
        ):
            status = {
                **status,
                "state": "error",
                "status": RENDER_CANCELLED,
                "error": "Der Lauf wurde durch einen Neustart unterbrochen.",
            }
            self._status = status
        return status

    def _set(self, **values: Any) -> None:
        self._status.update(values)

    # --- run ----------------------------------------------------------

    def async_start(self, *, job_id: str) -> dict[str, Any]:
        if self._task is not None and not self._task.done():
            raise ValidationError(
                "Es läuft bereits ein Remotion-Testrender - bitte warten."
            )
        self._status = {
            "state": "running",
            "status": "RUNNING",
            "job_id": job_id,
            "stage": "Vorbereitung",
            "progress": 0.0,
            "started_at": utc_now_iso(),
        }
        self._task = self.hass.async_create_task(self._async_run(job_id))
        return dict(self._status)

    async def async_cancel(self) -> dict[str, Any]:
        task = self._task
        if task is None or task.done():
            return await self.async_status()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return await self.async_status()

    async def _async_run(self, job_id: str) -> None:
        try:
            result = await self._async_render(job_id)
        except asyncio.CancelledError:
            self._set(
                state="error",
                status=RENDER_CANCELLED,
                error="Der Testrender wurde abgebrochen.",
                finished_at=utc_now_iso(),
            )
            await self._async_kill()
            raise
        except (RoadplannerError, ValidationError, RemotionJobError) as err:
            # A specific code set on the way down (BROWSER_MISSING,
            # RENDER_TIMEOUT, OUTPUT_INVALID) is the finding; a generic
            # RENDER_FAILED here would erase exactly what the spike is
            # supposed to learn.
            current = str(self._status.get("status") or "")
            self._set(
                state="error",
                status=current if current not in ("", "RUNNING") else RENDER_FAILED,
                error=clean_detail(err),
                finished_at=utc_now_iso(),
            )
        except Exception as err:  # noqa: BLE001 - the status must always resolve
            _LOGGER.exception("Remotion spike crashed")
            self._set(
                state="error",
                status=RENDER_FAILED,
                error=f"Unerwarteter Fehler ({type(err).__name__})",
                finished_at=utc_now_iso(),
            )
        else:
            self._set(**result, state="ready", finished_at=utc_now_iso())

    async def _async_render(self, job_id: str) -> dict[str, Any]:
        diagnosis = await self.async_diagnose()
        if not diagnosis.get("ready"):
            # The diagnosis IS the result. Nothing gets installed to make
            # the render possible.
            raise RoadplannerError(diagnosis.get("summary_de") or "Umgebung nicht bereit")

        node = str(diagnosis["details"].get("node_path") or "")
        script = str(diagnosis["details"].get("renderer_path") or "")
        browser = str(diagnosis["details"].get("browser_path") or "")
        if not node or not script:
            raise RoadplannerError("Node oder Renderer-Skript fehlen")

        output_path = resolve_output_path(self.output_dir, job_id)
        job = build_job(
            job_id=job_id,
            output_path=output_path,
            browser_executable=browser or None,
        )
        job_path = output_path.with_suffix(".job.json")
        await self.hass.async_add_executor_job(
            job_path.write_text, json.dumps(job, ensure_ascii=False, indent=2), "utf-8"
        )

        self._set(stage="Renderer wird gestartet")
        try:
            return await self._async_supervise(node, script, job_path, output_path)
        finally:
            await self.hass.async_add_executor_job(_unlink_quietly, job_path)

    async def _async_supervise(
        self, node: str, script: str, job_path: Path, output_path: Path
    ) -> dict[str, Any]:
        # A minimal, controlled environment: nothing from the Home
        # Assistant process leaks into the child beyond what it needs.
        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
            "NODE_ENV": "production",
            # Remotion phones home unless told otherwise; a spike must not
            # send anything about a private trip anywhere.
            "REMOTION_DISABLE_TELEMETRY": "1",
            "CI": "1",
        }
        process = await asyncio.create_subprocess_exec(
            node,
            script,
            str(job_path),
            cwd=str(Path(script).parent.parent),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group, so a timeout can end the browser too and
            # not just the Node parent.
            start_new_session=True,
        )
        self._process = process
        failure: dict[str, Any] | None = None
        completed_path = ""

        async def pump_stdout() -> None:
            nonlocal failure, completed_path
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    return
                try:
                    event = parse_event_line(raw.decode("utf-8", errors="replace"))
                except RemotionJobError as err:
                    failure = {"code": RENDER_FAILED, "message": str(err)}
                    return
                if event is None:
                    continue
                kind = event.get("event")
                progress = progress_of(event)
                if progress is not None:
                    self._set(progress=round(progress, 3))
                if kind == "phase":
                    self._set(stage=str(event.get("name") or "")[:60])
                elif kind == "completed":
                    completed_path = str(event.get("output_path") or "")
                elif kind == "failed":
                    failure = {
                        "code": clean_detail(event.get("code"), limit=60) or RENDER_FAILED,
                        "message": clean_detail(event.get("message")),
                        "technical_detail": clean_detail(
                            event.get("technical_detail"), limit=600
                        ),
                    }

        async def drain_stderr() -> bytes:
            assert process.stderr is not None
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await process.stderr.read(4096)
                if not chunk:
                    break
                if total < MAX_STDERR_BYTES:
                    chunks.append(chunk[: MAX_STDERR_BYTES - total])
                    total += len(chunk)
            return b"".join(chunks)

        stdout_task = asyncio.ensure_future(pump_stdout())
        stderr_task = asyncio.ensure_future(drain_stderr())
        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, process.wait()),
                timeout=RENDER_TIMEOUT_SECONDS,
            )
        except (TimeoutError, asyncio.TimeoutError):
            await self._async_kill()
            stdout_task.cancel()
            stderr_task.cancel()
            await self.hass.async_add_executor_job(_unlink_quietly, output_path)
            self._set(status=RENDER_TIMEOUT)
            raise RoadplannerError(
                "Der Remotion-Testrender hat zu lange gedauert und wurde abgebrochen."
            ) from None
        finally:
            self._process = None

        stderr_text = clean_detail(
            (await stderr_task if not stderr_task.done() else stderr_task.result())
            .decode("utf-8", errors="replace"),
            limit=1_200,
        )

        if failure is not None:
            self._set(status=failure.get("code") or RENDER_FAILED)
            raise RoadplannerError(
                failure.get("message") or "Der Render ist fehlgeschlagen."
            )
        if process.returncode != 0:
            self._set(status=RENDER_FAILED, stderr_tail=stderr_text)
            raise RoadplannerError(
                f"Der Renderer endete mit Code {process.returncode}."
            )
        if not completed_path:
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError("Der Renderer meldete keinen Abschluss.")

        probe = await self._async_validate(output_path)
        return {
            "status": READY,
            "progress": 1.0,
            "stage": "Fertig",
            "output": probe,
            "stderr_tail": stderr_text[:400],
        }

    async def _async_kill(self) -> None:
        """End the whole process GROUP - the browser is a child too."""
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            os.killpg(os.getpgid(process.pid), 15)
        try:
            await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(os.getpgid(process.pid), 9)
            with contextlib.suppress(Exception):
                await process.wait()

    async def _async_validate(self, output_path: Path) -> dict[str, Any]:
        """Exit code 0 is a claim; ffprobe is the check."""
        root = Path(self.output_dir).resolve()
        resolved = output_path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as err:
            raise RoadplannerError("Die Ausgabe liegt außerhalb des Exportordners") from err
        if not resolved.is_file():
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError("Es wurde keine Ausgabedatei gefunden.")
        size = resolved.stat().st_size
        if size < 1024:
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError("Die Ausgabedatei ist unplausibel klein.")

        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            raise RoadplannerError("ffprobe fehlt - das Ergebnis ist nicht prüfbar.")
        process = await asyncio.create_subprocess_exec(
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-show_entries",
            "format=duration,format_name",
            "-of",
            "json",
            str(resolved),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout=60)
        if process.returncode != 0:
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError("ffprobe konnte die Datei nicht lesen.")
        try:
            payload = json.loads((stdout or b"{}").decode("utf-8", errors="replace"))
        except ValueError as err:
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError("ffprobe lieferte keine gültige Antwort.") from err

        streams = payload.get("streams") or [{}]
        stream = streams[0] if isinstance(streams, list) and streams else {}
        fmt = payload.get("format") or {}
        codec = str(stream.get("codec_name") or "")
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        try:
            duration = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        container = str(fmt.get("format_name") or "")

        problems = []
        if codec != "h264":
            problems.append(f"Codec {codec or 'unbekannt'}")
        if "mp4" not in container:
            problems.append(f"Container {container or 'unbekannt'}")
        if width != _EXPECTED["width"] or height != _EXPECTED["height"]:
            problems.append(f"Auflösung {width}x{height}")
        if abs(duration - _EXPECTED["duration_seconds"]) > 0.75:
            problems.append(f"Dauer {duration:.2f}s")
        if problems:
            self._set(status=OUTPUT_INVALID)
            raise RoadplannerError(
                "Die erzeugte Datei entspricht nicht der Erwartung: "
                + ", ".join(problems)
            )
        return {
            "path": str(resolved),
            "filename": resolved.name,
            "size_bytes": size,
            "codec": codec,
            "width": width,
            "height": height,
            "duration_seconds": round(duration, 3),
            "container": container,
        }


def _unlink_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)
