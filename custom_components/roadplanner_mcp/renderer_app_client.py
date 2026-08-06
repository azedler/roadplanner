"""The Home Assistant half of the renderer-app proof of concept.

Everything that touches the shared directory lives here, so the rest of
Roadplanner never handles raw exchange files. Blocking filesystem work runs
through the executor - the exchange directory may sit on a slow USB disk,
and stalling the event loop on it would be felt everywhere in the panel.

Two properties are worth stating plainly because they are what the PoC is
supposed to prove:

- **A missing app is not an error.** If the directory does not exist, if no
  heartbeat was ever written, or if the app was never installed, this
  reports that calmly and Roadplanner carries on. Nothing here may raise
  into the integration's startup path.
- **A restart cannot produce a hanging or a duplicate job.** The job id is
  the filename, so re-submitting the same id cannot create a second job,
  and every state Roadplanner shows is read from disk rather than kept in
  memory. Home Assistant restarting mid-job loses nothing.

The environment probe answers the question that decides the whole
approach: apps only exist under Home Assistant OS or Supervised. On a
Container or Core installation there is no Supervisor and no app can ever
be installed - which is a result, not a fault, and has to be reported as
such rather than surfacing later as "the app never appeared".
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import platform
import shutil
from typing import Any

from homeassistant.core import HomeAssistant

from .renderer_app_protocol import (
    ACTION_CREATE_TEST_ARTIFACT,
    ARTIFACT_IMAGE,
    ARTIFACT_TEXT,
    JOB_QUEUED,
    JOB_TTL_SECONDS,
    MAX_ARTIFACT_BYTES,
    MAX_JSON_BYTES,
    RendererProtocolError,
    build_job,
    clean_text,
    decode_json,
    job_filename,
    new_job_id,
    utc_now,
    validate_heartbeat,
    validate_job_id,
    validate_result,
    validate_status,
    verify_artifact,
)

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_FILENAME = "renderer-status.json"
JOBS_DIR = "jobs"
PROCESSING_DIR = "processing"
STATUS_DIR = "status"
RESULTS_DIR = "results"
_SUBDIRS = (JOBS_DIR, PROCESSING_DIR, STATUS_DIR, RESULTS_DIR)

# Environment codes, stable so panel, tests and the report agree.
ENV_READY = "READY"
ENV_NO_SUPERVISOR = "NO_SUPERVISOR"
ENV_SHARE_MISSING = "SHARE_MISSING"
ENV_EXCHANGE_NOT_WRITABLE = "EXCHANGE_NOT_WRITABLE"

_ENV_MESSAGES = {
    ENV_READY: (
        "Der gemeinsame Austauschordner ist erreichbar und beschreibbar.",
        "Die Renderer-App kann installiert und geprüft werden.",
    ),
    ENV_NO_SUPERVISOR: (
        "Diese Home-Assistant-Installation hat keinen Supervisor.",
        "Apps gibt es nur unter Home Assistant OS oder Supervised. Dieses "
        "Ergebnis im Bericht festhalten - es beantwortet die Machbarkeitsfrage "
        "bereits.",
    ),
    ENV_SHARE_MISSING: (
        "Das freigegebene Verzeichnis /share ist nicht vorhanden.",
        "Ohne gemeinsamen Ordner gibt es keinen Austauschkanal. Ergebnis im "
        "Bericht festhalten, nichts anlegen.",
    ),
    ENV_EXCHANGE_NOT_WRITABLE: (
        "Der Austauschordner ist nicht beschreibbar.",
        "Pfad und Rechte prüfen; keine weitergehenden Berechtigungen vergeben.",
    ),
}


def _describe_env(status: str, details: dict[str, Any]) -> dict[str, Any]:
    summary, next_step = _ENV_MESSAGES.get(
        status, ("Unbekannter Zustand.", "Ergebnis im Bericht festhalten.")
    )
    return {
        "status": status,
        "ready": status == ENV_READY,
        "summary_de": summary,
        "details": details,
        "recommended_next_step_de": next_step,
    }


def _supervisor_present(hass: HomeAssistant) -> bool:
    """Is this a Supervisor-managed installation?

    ``is_hassio(hass)`` is the supported answer. The environment variable is
    the fallback for the case where that helper moves - Supervisor passes a
    token into the Home Assistant container and nothing else does. Both are
    read-only, and a wrong answer here would be reported as a capability the
    system does not have, so neither is guessed at.
    """
    try:
        from homeassistant.components.hassio import is_hassio  # noqa: PLC0415
    except ImportError:  # pragma: no cover - hassio ships with core
        return bool(os.environ.get("SUPERVISOR_TOKEN"))
    try:
        return bool(is_hassio(hass))
    except Exception:  # noqa: BLE001 - a probe must never raise
        return bool(os.environ.get("SUPERVISOR_TOKEN"))


class RendererAppClient:
    """Talk to the renderer app through one shared directory."""

    def __init__(self, hass: HomeAssistant, *, exchange_dir: Path) -> None:
        self._hass = hass
        self._dir = Path(exchange_dir)

    @property
    def exchange_dir(self) -> Path:
        return self._dir

    # --- environment ----------------------------------------------------

    async def async_environment(self) -> dict[str, Any]:
        """Read-only probe. Creates the exchange directory, nothing else."""
        return await self._hass.async_add_executor_job(self._environment)

    def _environment(self) -> dict[str, Any]:
        machine = platform.machine()
        details: dict[str, Any] = {
            "platform": platform.system(),
            "machine": machine,
            # The app image is published per architecture, so the report has
            # to name the one Home Assistant actually runs on.
            "arch": "amd64" if machine in ("x86_64", "AMD64") else machine,
            "exchange_dir": str(self._dir),
            "supervisor": _supervisor_present(self._hass),
            "share_exists": Path("/share").is_dir(),
        }

        writable = False
        free_bytes = 0
        try:
            for name in ("",) + _SUBDIRS:
                (self._dir / name if name else self._dir).mkdir(parents=True, exist_ok=True)
            probe = self._dir / ".roadplanner_write_probe"
            probe.write_bytes(b"ok")
            probe.unlink()
            writable = True
            free_bytes = shutil.disk_usage(self._dir).free
        except OSError as err:
            details["exchange_error"] = clean_text(f"{type(err).__name__}: {err}", limit=200)
        details["exchange_writable"] = writable
        details["free_bytes"] = free_bytes

        # Order matters: name the first thing that actually blocks.
        if not details["supervisor"]:
            return _describe_env(ENV_NO_SUPERVISOR, details)
        if not details["share_exists"]:
            return _describe_env(ENV_SHARE_MISSING, details)
        if not writable:
            return _describe_env(ENV_EXCHANGE_NOT_WRITABLE, details)
        return _describe_env(ENV_READY, details)

    # --- heartbeat ------------------------------------------------------

    async def async_app_status(self) -> dict[str, Any]:
        """What the app last said about itself, judged against the clock."""
        raw = await self._hass.async_add_executor_job(
            self._read_bounded, self._dir / HEARTBEAT_FILENAME, MAX_JSON_BYTES
        )
        if raw is None:
            return {
                "installed": False,
                "online": False,
                "state": None,
                "reason": "Kein Heartbeat gefunden - App vermutlich nicht installiert.",
            }
        try:
            heartbeat = validate_heartbeat(decode_json(raw), now=utc_now())
        except RendererProtocolError as err:
            return {
                "installed": True,
                "online": False,
                "state": None,
                "reason": clean_text(str(err), limit=200),
            }
        return {"installed": True, **heartbeat}

    # --- jobs -----------------------------------------------------------

    async def async_submit_test_job(self, *, message: str = "Hallo Renderer") -> dict[str, Any]:
        """Write one job atomically and return what was written."""
        job_id = new_job_id()
        job = build_job(
            job_id=job_id,
            action=ACTION_CREATE_TEST_ARTIFACT,
            message=message,
            now=utc_now(),
            ttl_seconds=JOB_TTL_SECONDS,
        )
        await self._hass.async_add_executor_job(self._write_job, job)
        return {"job_id": job_id, "state": JOB_QUEUED, "submitted_at": job["created_at"]}

    def _write_job(self, job: dict[str, Any]) -> None:
        target = self._dir / JOBS_DIR / job_filename(job["job_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same directory, so the rename stays within one filesystem and is
        # therefore atomic: the worker never sees a partial job.
        temporary = target.with_suffix(".json.part")
        temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)

    async def async_job_status(self, job_id: str) -> dict[str, Any]:
        """Where a job stands, read from disk every time.

        Nothing is cached in memory on purpose: after a Home Assistant
        restart the panel must be able to find a job it did not start.
        """
        validate_job_id(job_id)
        raw = await self._hass.async_add_executor_job(
            self._read_bounded, self._dir / STATUS_DIR / f"{job_id}.json", MAX_JSON_BYTES
        )
        if raw is None:
            queued = await self._hass.async_add_executor_job(
                (self._dir / JOBS_DIR / f"{job_id}.json").is_file
            )
            return {
                "job_id": job_id,
                "state": JOB_QUEUED if queued else None,
                "terminal": False,
                "reason": None
                if queued
                else "Kein Status gefunden - Auftrag unbekannt oder aufgeräumt.",
            }
        try:
            return validate_status(decode_json(raw), job_id=job_id)
        except RendererProtocolError as err:
            return {
                "job_id": job_id,
                "state": None,
                "terminal": False,
                "reason": clean_text(str(err), limit=200),
            }

    async def async_result(self, job_id: str) -> dict[str, Any] | None:
        """Read and verify a finished job's artefacts, or None if absent."""
        validate_job_id(job_id)
        return await self._hass.async_add_executor_job(self._result, job_id)

    def _result(self, job_id: str) -> dict[str, Any] | None:
        folder = self._dir / RESULTS_DIR / job_id
        raw = self._read_bounded(folder / "result.json", MAX_JSON_BYTES)
        if raw is None:
            return None
        result = validate_result(decode_json(raw), job_id=job_id)
        contents: dict[str, Any] = {}
        for declared in result["artifacts"]:
            # The filename came from a fixed whitelist in validate_result,
            # so this join cannot be steered anywhere.
            data = self._read_bounded(folder / declared["filename"], MAX_ARTIFACT_BYTES)
            if data is None:
                raise RendererProtocolError(
                    f"Angekündigtes Artefakt fehlt: {declared['filename']}"
                )
            verify_artifact(data, declared)
            contents[declared["filename"]] = data.decode("utf-8", errors="replace")
        return {
            **result,
            "text": contents.get(ARTIFACT_TEXT, ""),
            "svg": contents.get(ARTIFACT_IMAGE, ""),
        }

    # --- shared plumbing ------------------------------------------------

    @staticmethod
    def _read_bounded(path: Path, limit: int) -> bytes | None:
        """Read a file, refusing anything oversized. Missing is None.

        A symlink is refused rather than followed: the exchange directory is
        writable by another container, and following a link out of it is the
        one way a file there could reach something it should not.
        """
        try:
            if path.is_symlink():
                raise RendererProtocolError(f"Symlink im Austauschordner: {path.name}")
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size > limit:
            raise RendererProtocolError(f"{path.name} überschreitet die Größengrenze")
        try:
            return path.read_bytes()
        except OSError:
            return None


def default_exchange_dir() -> Path:
    """Where the two sides meet.

    ``/share`` is mounted into both the Home Assistant container and any app
    that asks for it, which is what makes it the smallest workable channel.
    The extra ``poc-v1`` level keeps this experiment separate from anything
    a later, real renderer would use.
    """
    return Path("/share/roadplanner-renderer/poc-v1")


__all__ = [
    "ENV_EXCHANGE_NOT_WRITABLE",
    "ENV_NO_SUPERVISOR",
    "ENV_READY",
    "ENV_SHARE_MISSING",
    "RendererAppClient",
    "default_exchange_dir",
]
