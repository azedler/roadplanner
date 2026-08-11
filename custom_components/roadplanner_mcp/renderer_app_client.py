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

from .render_profiles import (
    DEFAULT_RENDER_PROFILE,
    DEFAULT_REVIEW_PROFILE,
    RENDER_PROFILES,
    REVIEW_COPY_PROFILES,
)
from .renderer_app_protocol import (
    ACTION_CREATE_REVIEW_COPY,
    ACTION_CREATE_TEST_ARTIFACT,
    ACTION_RENDER_REMOTION_TEST,
    ACTION_RENDER_TRIP_DAY,
    ACTION_RENDER_TRIP_FILM,
    ARTIFACT_REVIEW_COPY,
    ARTIFACT_TRIP_FILM_VIDEO,
    FILM_JOB_TTL_SECONDS,
    artifact_limit,
    ARTIFACT_IMAGE,
    ARTIFACT_TEXT,
    ARTIFACT_TRIP_DAY_VIDEO,
    ARTIFACT_VIDEO,
    INPUTS_DIR,
    MAX_VIDEO_ARTIFACT_BYTES,
    TEXT_ARTIFACT_KINDS,
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
from .trip_day_render_package import (
    PACKAGE_FILENAME,
    image_filename,
    validate_package,
)
from .trip_film_package import (
    FILM_MANIFEST_FILENAME,
    validate_film_package,
)

_LOGGER = logging.getLogger(__name__)

HEARTBEAT_FILENAME = "renderer-status.json"
JOBS_DIR = "jobs"
PROCESSING_DIR = "processing"
STATUS_DIR = "status"
RESULTS_DIR = "results"
_SUBDIRS = (JOBS_DIR, PROCESSING_DIR, STATUS_DIR, RESULTS_DIR, INPUTS_DIR)

# How many status files a single look is willing to open. The folder is
# written by another container; the panel's cost must not depend on how
# many files it decides to leave there.
_MAX_STATUS_SCAN = 60

# The kinds the panel distinguishes. They are the panel's vocabulary, not
# the protocol's, which is why the mapping lives here and not in the
# protocol module.
_KIND_BY_ACTION = {
    ACTION_CREATE_TEST_ARTIFACT: "test",
    ACTION_RENDER_REMOTION_TEST: "render",
    ACTION_RENDER_TRIP_DAY: "trip_day",
    ACTION_RENDER_TRIP_FILM: "trip_film",
    ACTION_CREATE_REVIEW_COPY: "review_copy",
}
_KIND_BY_ARTIFACT = {
    ARTIFACT_REVIEW_COPY: "review_copy",
    ARTIFACT_TRIP_FILM_VIDEO: "trip_film",
    ARTIFACT_TRIP_DAY_VIDEO: "trip_day",
    ARTIFACT_VIDEO: "render",
    ARTIFACT_IMAGE: "test",
}

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

    async def async_submit_test_job(
        self,
        *,
        message: str = "Hallo Renderer",
        action: str = ACTION_CREATE_TEST_ARTIFACT,
    ) -> dict[str, Any]:
        """Write one job atomically and return what was written."""
        job_id = new_job_id()
        job = build_job(
            job_id=job_id,
            action=action,
            message=message,
            now=utc_now(),
            ttl_seconds=JOB_TTL_SECONDS,
        )
        await self._hass.async_add_executor_job(self._write_job, job)
        return {"job_id": job_id, "state": JOB_QUEUED, "submitted_at": job["created_at"]}

    async def async_submit_trip_day_job(
        self,
        *,
        package: dict[str, Any],
        images: list[bytes],
        title: str,
    ) -> dict[str, Any]:
        """Hand over one day: its package first, the job last.

        The order is the whole correctness argument. The worker claims a
        job by finding its file; if that file existed before the images
        did, a fast worker would claim a job whose inputs are still being
        written. Writing the job last means the job file's existence
        implies a complete package - the same reasoning that makes
        ``result.json`` the last file the app writes.
        """
        validate_package(package)
        if len(images) != len(package["images"]):
            raise RendererProtocolError(
                "Renderpaket und Bilddateien passen nicht zusammen"
            )
        job_id = new_job_id()
        package = {**package, "job_id": job_id}
        job = build_job(
            job_id=job_id,
            action=ACTION_RENDER_TRIP_DAY,
            message=title or "Reisetag",
            now=utc_now(),
            ttl_seconds=JOB_TTL_SECONDS,
        )
        written = await self._hass.async_add_executor_job(
            self._write_trip_day_job, job, package, images
        )
        return {
            "job_id": job_id,
            "state": JOB_QUEUED,
            "submitted_at": job["created_at"],
            "package_bytes": written,
            "image_count": len(images),
        }

    def _write_trip_day_job(
        self, job: dict[str, Any], package: dict[str, Any], images: list[bytes]
    ) -> int:
        folder = self._dir / INPUTS_DIR / validate_job_id(job["job_id"])
        folder.mkdir(parents=True, exist_ok=True)
        total = 0
        for declared, blob in zip(package["images"], images, strict=True):
            # The name is built from the declared integer index, checked by
            # image_filename - never from anything in the package text.
            self._write_atomic(folder / image_filename(declared["index"]), blob)
            total += len(blob)
        manifest = json.dumps(package, ensure_ascii=False).encode("utf-8")
        self._write_atomic(folder / PACKAGE_FILENAME, manifest)
        total += len(manifest)
        self._write_job(job)
        return total

    async def async_submit_review_copy_job(
        self,
        *,
        source_job_id: str,
        profile_id: str = DEFAULT_REVIEW_PROFILE,
        title: str = "Review-Kopie",
    ) -> dict[str, Any]:
        """Ask for a small copy of a film that has already been rendered.

        The cheapest job in this protocol: no package is written, nothing
        is downloaded, no photograph is opened and no service is called.
        The whole request is one job file naming an earlier job whose
        result is still in the exchange folder.

        Nothing is checked here about whether that film exists. The app
        answers ``PACKAGE_MISSING`` if it does not, and a check here would
        only be a second, older opinion about a folder the app owns.
        """
        source = validate_job_id(source_job_id)
        if profile_id not in REVIEW_COPY_PROFILES:
            raise RendererProtocolError(f"Unbekanntes Reviewprofil: {profile_id!r}")
        job_id = new_job_id()
        job = build_job(
            job_id=job_id,
            action=ACTION_CREATE_REVIEW_COPY,
            message=title or "Review-Kopie",
            now=utc_now(),
            # Minutes rather than an hour, but still far past the default
            # five, and the copy of a long film is not a quick job.
            ttl_seconds=FILM_JOB_TTL_SECONDS,
            render_profile=profile_id,
            source_job_id=source,
        )
        await self._hass.async_add_executor_job(self._write_job, job)
        return {
            "job_id": job_id,
            "state": JOB_QUEUED,
            "submitted_at": job["created_at"],
            "source_job_id": source,
            "render_profile": profile_id,
        }

    async def async_submit_trip_film_job(
        self,
        *,
        package: dict[str, Any],
        files: dict[str, bytes],
        title: str,
        profile_id: str = DEFAULT_RENDER_PROFILE,
    ) -> dict[str, Any]:
        """Hand over a whole trip: its photos first, the job last.

        Same ordering argument as the day video, and it matters more here:
        seventy files take a moment to write, and a worker that found the
        job file first would start on half a trip.
        """
        validate_film_package(package)
        # Matched against the table HERE, at the last point that can still
        # refuse: the app falls back to its default for an id it does not
        # know, and a film that came out at another size than the one
        # somebody picked looks like a working render until the file is
        # opened.
        if profile_id not in RENDER_PROFILES:
            raise RendererProtocolError(f"Unbekanntes Renderprofil: {profile_id!r}")
        job_id = new_job_id()
        package = {**package, "job_id": job_id}
        job = build_job(
            job_id=job_id,
            action=ACTION_RENDER_TRIP_FILM,
            message=title or "Reisefilm",
            now=utc_now(),
            # A trip takes minutes. The default five-minute TTL would mark
            # the job stale while it was still being rendered.
            ttl_seconds=FILM_JOB_TTL_SECONDS,
            render_profile=profile_id,
        )
        written = await self._hass.async_add_executor_job(
            self._write_trip_film_job, job, package, files
        )
        return {
            "job_id": job_id,
            "state": JOB_QUEUED,
            "submitted_at": job["created_at"],
            "package_bytes": written,
            "image_count": len(files),
            "render_profile": profile_id,
        }

    def _write_trip_film_job(
        self, job: dict[str, Any], package: dict[str, Any], files: dict[str, bytes]
    ) -> int:
        folder = self._dir / INPUTS_DIR / validate_job_id(job["job_id"])
        folder.mkdir(parents=True, exist_ok=True)
        total = 0
        for relative, blob in files.items():
            target = self._safe_child(folder, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_atomic(target, blob)
            total += len(blob)
        manifest = json.dumps(package, ensure_ascii=False).encode("utf-8")
        self._write_atomic(folder / FILM_MANIFEST_FILENAME, manifest)
        total += len(manifest)
        self._write_job(job)
        return total

    @staticmethod
    def _safe_child(folder: Path, relative: str) -> Path:
        """Resolve a package-relative path, refusing anything that escapes.

        The paths are built from integers by ``photo_filename`` and cannot
        contain anything else. This checks it anyway: the cost is one
        comparison, and the failure it prevents is writing outside the job
        directory.
        """
        candidate = (folder / relative).resolve()
        root = folder.resolve()
        if not str(candidate).startswith(f"{root}/"):
            raise RendererProtocolError(f"Pfad verlässt den Auftragsordner: {relative!r}")
        return candidate

    async def async_discard_job_inputs(self, job_id: str) -> None:
        """Remove a job's package.

        The app deletes its own inputs when it is done with them. This is
        for the case where the app never got that far - a job that was
        never claimed would otherwise leave real travel data lying in a
        shared directory indefinitely.
        """
        validate_job_id(job_id)
        await self._hass.async_add_executor_job(self._discard_inputs, job_id)

    def _discard_inputs(self, job_id: str) -> None:
        folder = self._dir / INPUTS_DIR / job_id
        try:
            if folder.is_symlink() or not folder.is_dir():
                return
            # A film package has a photos/ subdirectory, so this has to go
            # a level deeper than the day video's flat folder did.
            shutil.rmtree(folder)
        except OSError as err:
            _LOGGER.debug("Renderpaket %s nicht aufräumbar: %s", job_id, err)

    @staticmethod
    def _write_atomic(target: Path, payload: bytes) -> None:
        temporary = target.with_name(f"{target.name}.part")
        temporary.write_bytes(payload)
        os.replace(temporary, target)

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

    async def async_recent_jobs(self, *, limit: int = 6) -> list[dict[str, Any]]:
        """Which jobs the exchange folder knows about, newest first.

        The panel needs this because the browser is not where a render
        lives. A trip film takes a quarter of an hour; in that time a phone
        locks, Home Assistant reloads its page, and every variable the card
        kept is gone - while the job itself runs on, entirely unbothered,
        in another container. Without a way to ask "what is going on", the
        render becomes invisible the moment you look away, and its result
        unreachable. So the question is answered from disk, which is the
        only place that survived.

        The kind of job is not in the status file. It is read from the job
        file while the job is alive and from the artefact names once it has
        finished - never guessed, because announcing a trip film as a test
        render would be worse than saying nothing.
        """
        entries = await self._hass.async_add_executor_job(self._recent_jobs, limit)
        return entries

    def _recent_jobs(self, limit: int) -> list[dict[str, Any]]:
        folder = self._dir / STATUS_DIR
        try:
            names = [entry.name for entry in os.scandir(folder) if entry.is_file()]
        except OSError:
            return []
        found: list[dict[str, Any]] = []
        # Bounded before any file is opened: the folder is written by
        # another container, and a thousand status files must not turn a
        # panel refresh into a thousand reads.
        for name in sorted(names)[:_MAX_STATUS_SCAN]:
            if not name.endswith(".json"):
                continue
            job_id = name[: -len(".json")]
            try:
                validate_job_id(job_id)
            except RendererProtocolError:
                continue
            raw = self._read_bounded(folder / name, MAX_JSON_BYTES)
            if raw is None:
                continue
            try:
                status = validate_status(decode_json(raw), job_id=job_id)
            except RendererProtocolError:
                # A single unreadable status must not hide the others.
                continue
            found.append({**status, "kind": self._job_kind(job_id)})
        found.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return found[: max(1, limit)]

    def _job_kind(self, job_id: str) -> str:
        """What a job was asked to do, read rather than remembered."""
        for directory in (PROCESSING_DIR, JOBS_DIR):
            raw = self._read_bounded(
                self._dir / directory / f"{job_id}.json", MAX_JSON_BYTES
            )
            if raw is None:
                continue
            try:
                action = decode_json(raw).get("action")
            except RendererProtocolError:
                continue
            kind = _KIND_BY_ACTION.get(str(action or ""))
            if kind:
                return kind
        # The job file is removed when the job ends, so a finished job is
        # identified by what it produced.
        for filename, kind in _KIND_BY_ARTIFACT.items():
            if (self._dir / RESULTS_DIR / job_id / filename).is_file():
                return kind
        return ""

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
            is_text = declared["kind"] in TEXT_ARTIFACT_KINDS
            data = self._read_bounded(
                folder / declared["filename"], artifact_limit(declared["filename"])
            )
            if data is None:
                raise RendererProtocolError(
                    f"Angekündigtes Artefakt fehlt: {declared['filename']}"
                )
            verify_artifact(data, declared)
            # A video is verified but never decoded: it is megabytes of
            # binary, it is not displayable as text, and pushing it through
            # the websocket would stall the panel for no benefit. The panel
            # shows what ffprobe measured; the file stays on disk.
            if is_text:
                contents[declared["filename"]] = data.decode("utf-8", errors="replace")
        # Either video artefact counts - the test render and the trip-day
        # render produce different filenames and the panel shows whichever
        # one this job actually made.
        video_path = ""
        for candidate in (
            ARTIFACT_REVIEW_COPY,
            ARTIFACT_TRIP_FILM_VIDEO,
            ARTIFACT_TRIP_DAY_VIDEO,
            ARTIFACT_VIDEO,
        ):
            if (folder / candidate).is_file():
                video_path = str(folder / candidate)
                break
        return {
            **result,
            "text": contents.get(ARTIFACT_TEXT, ""),
            "svg": contents.get(ARTIFACT_IMAGE, ""),
            "video_path": video_path,
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
