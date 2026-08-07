"""The file-based contract between Roadplanner and the renderer app.

Pure: no Home Assistant, no filesystem, no clock of its own. Every function
takes the data and, where time matters, the current time. That keeps the
part which decides *what the two sides are allowed to say to each other*
testable without either side running.

The two processes never call each other. They share one directory and
nothing else - no port, no socket, no token, no Supervisor API. A shared
directory is the smallest thing that can carry a job, and it is the only
channel that needs no permission beyond the mount itself.

What makes a directory a usable channel is that both sides agree on when a
file is *finished*:

- **Writers never write in place.** Every file is written to a temporary
  name in the same directory and then renamed onto its final name.
  ``rename`` within one filesystem is atomic, so a reader sees either the
  old file or the complete new one, never a half-written one. This is why
  the exchange directory must be a single filesystem.
- **A job is claimed by moving it**, not by writing a flag. Two workers
  cannot both move the same file; the loser gets an error rather than a
  second copy of the job. The PoC runs one worker, but the property is
  what makes the design extensible, and it costs nothing to have now.
- **Terminal states are final.** ``completed``/``failed``/``expired`` never
  go back to ``running``. A late write from a process that was restarted
  cannot resurrect a finished job.

**The exchange directory is a trust channel, not a security boundary.**
That distinction decides what may be done with what comes out of it.

``/share`` is writable by Home Assistant and by every app that asks for the
same mount. Nothing there is authenticated: the SHA-256 in ``result.json``
sits in the same file as the artefact it describes, so whoever can forge
one can forge both. **The hash proves that the bytes arrived intact, never
that they came from the renderer.**

What follows from that, and what must survive any later change:

- a returned file is treated as untrusted input. It is bounded, its type is
  checked, and it is never interpreted as markup, code or a path;
- **for production video, only a file that ffprobe confirms to be the
  expected MP4 may be taken over** - container, codec, resolution and
  duration, read back from the bytes rather than believed from a manifest;
- the channel is adequate because both ends are local and under the same
  operator. It would not be adequate across machines or across trust
  domains, and turning it into one would need a different mechanism -
  signatures or a transport with identity - not a stricter schema.

The security posture, which is narrower than it may look:

- filenames are derived from a server-generated UUID and nothing else. No
  part of any path comes from user text, so there is no traversal to
  defend against - the defence is that the input never exists. This holds
  for the input package too: ``inputs/<job_id>/`` is named after the job,
  and the images inside it are numbered, so the reader builds
  ``photo-<n>.jpg`` from an integer rather than from anything it was told;
- the job carries data, never a path, a command or a URL;
- an unknown protocol version is refused rather than interpreted;
- artefacts are bounded in size and verified by SHA-256 before Roadplanner
  looks at their content.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
import uuid

SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1

RENDERER_NAME = "roadplanner-renderer-poc"

# --- states -------------------------------------------------------------

APP_STARTING = "starting"
APP_READY = "ready"
APP_DEGRADED = "degraded"
APP_STOPPING = "stopping"
APP_STATES = (APP_STARTING, APP_READY, APP_DEGRADED, APP_STOPPING)

JOB_QUEUED = "queued"
JOB_CLAIMED = "claimed"
JOB_RUNNING = "running"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_EXPIRED = "expired"
JOB_STATES = (
    JOB_QUEUED,
    JOB_CLAIMED,
    JOB_RUNNING,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_EXPIRED,
)
TERMINAL_JOB_STATES = frozenset({JOB_COMPLETED, JOB_FAILED, JOB_EXPIRED})

# --- what may be asked for ----------------------------------------------

ACTION_PING = "ping"
ACTION_CREATE_TEST_ARTIFACT = "create_test_artifact"
ACTION_RENDER_REMOTION_TEST = "render_remotion_test"
# One real trip day. The job still carries no data beyond a title: the day
# itself travels as a package in ``inputs/<job_id>/``, which the app finds
# from the job id it already has. A directory named by a server-generated
# UUID is the only kind of path either side ever builds.
ACTION_RENDER_TRIP_DAY = "render_trip_day"
ALLOWED_ACTIONS = (
    ACTION_PING,
    ACTION_CREATE_TEST_ARTIFACT,
    ACTION_RENDER_REMOTION_TEST,
    ACTION_RENDER_TRIP_DAY,
)

ARTIFACT_TEXT = "roadplanner-renderer-poc.txt"
ARTIFACT_IMAGE = "roadplanner-renderer-poc.svg"
ARTIFACT_VIDEO = "roadplanner-remotion-test.mp4"
ARTIFACT_TRIP_DAY_VIDEO = "roadplanner-trip-day.mp4"
ALLOWED_ARTIFACTS = {
    ARTIFACT_TEXT: "text",
    ARTIFACT_IMAGE: "image",
    ARTIFACT_VIDEO: "video",
    ARTIFACT_TRIP_DAY_VIDEO: "video",
}
# Where a job's input package lives. Named after the job, never after
# anything a user typed.
INPUTS_DIR = "inputs"
# Text artefacts are read into the panel; a video never is. Keeping the
# limits apart means the small ones stay small - a 64 MiB text file would
# be nonsense, and a 256 KiB cap would reject every real video.
TEXT_ARTIFACT_KINDS = frozenset({"text", "image"})

# --- limits -------------------------------------------------------------

# A heartbeat older than this means "not reachable", never "ready". The app
# writes one every 5 s; three missed beats is a clear signal without being
# jumpy about a loaded system.
HEARTBEAT_MAX_AGE_SECONDS = 20
HEARTBEAT_INTERVAL_SECONDS = 5

# A job nobody claimed within this window is stale rather than pending: the
# app was probably not running when it was written.
JOB_TTL_SECONDS = 300

MAX_JSON_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024
MAX_VIDEO_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MESSAGE_LENGTH = 120
MAX_ERROR_LENGTH = 300

ERROR_INVALID_JOB = "INVALID_JOB"
ERROR_UNSUPPORTED_PROTOCOL = "UNSUPPORTED_PROTOCOL"
ERROR_EXPIRED = "EXPIRED"
ERROR_INTERNAL = "INTERNAL"

JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class RendererProtocolError(ValueError):
    """A message that must not be passed on."""


# --- time ---------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_time(moment: datetime) -> str:
    """One spelling of a timestamp, so both sides parse the same thing."""
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_time(value: Any) -> datetime | None:
    """Read a timestamp, or None when it is missing or malformed.

    Malformed is not fatal here: the caller decides what an unreadable
    timestamp means, and for a heartbeat it simply means "not fresh".
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --- small helpers ------------------------------------------------------


def new_job_id() -> str:
    """A job id is generated here and never accepted from anywhere else."""
    return str(uuid.uuid4())


def validate_job_id(value: Any) -> str:
    text = str(value or "")
    if not JOB_ID_RE.match(text):
        raise RendererProtocolError("Ungültige Job-ID")
    return text


def _envelope(payload: Any) -> dict[str, Any]:
    """Check the two version fields every message carries."""
    if not isinstance(payload, dict):
        raise RendererProtocolError("Nachricht ist kein Objekt")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RendererProtocolError(
            f"Schemaversion {payload.get('schema_version')!r} wird nicht unterstützt"
        )
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise RendererProtocolError(
            f"Protokollversion {payload.get('protocol_version')!r} wird nicht unterstützt"
        )
    return payload


def clean_text(value: Any, *, limit: int = MAX_MESSAGE_LENGTH) -> str:
    """Collapse whitespace and bound length - for anything we store or show."""
    return " ".join(str(value or "").split())[:limit]


def decode_json(raw: bytes | str, *, limit: int = MAX_JSON_BYTES) -> Any:
    """Parse bounded JSON. Oversized or malformed input is a protocol error.

    Size is checked before parsing: a hostile or broken writer must not be
    able to make the reader allocate an arbitrary amount of memory.
    """
    data = raw.encode("utf-8", errors="replace") if isinstance(raw, str) else bytes(raw)
    if len(data) > limit:
        raise RendererProtocolError("Nachricht überschreitet die Größengrenze")
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise RendererProtocolError("Nachricht ist kein gültiges JSON") from err


# --- heartbeat ----------------------------------------------------------


def validate_heartbeat(payload: Any, *, now: datetime) -> dict[str, Any]:
    """Read the app's heartbeat and decide whether it counts as online.

    Three things have to hold before Roadplanner may show "bereit": the
    file parses, the protocol version is one we speak, and the beat is
    recent. Showing a stale heartbeat as ready is the specific failure this
    guards against - a stopped app leaves its last file lying there.
    """
    data = _envelope(payload)
    state = str(data.get("state") or "")
    if state not in APP_STATES:
        raise RendererProtocolError(f"Unbekannter App-Zustand: {state!r}")
    beat = parse_time(data.get("heartbeat_at"))
    age = None if beat is None else (now - beat).total_seconds()
    # A clock that runs ahead is as untrustworthy as one that lags, so the
    # age is compared on its magnitude.
    fresh = age is not None and abs(age) <= HEARTBEAT_MAX_AGE_SECONDS
    capabilities = data.get("capabilities")
    return {
        "renderer": clean_text(data.get("renderer"), limit=60),
        "app_version": clean_text(data.get("app_version"), limit=40),
        "state": state,
        "heartbeat_at": data.get("heartbeat_at"),
        "started_at": data.get("started_at"),
        "age_seconds": None if age is None else round(age, 1),
        "fresh": fresh,
        "online": fresh and state in (APP_READY, APP_DEGRADED),
        "capabilities": [
            clean_text(item, limit=40)
            for item in (capabilities if isinstance(capabilities, list) else [])
        ][:20],
    }


# --- job ----------------------------------------------------------------


def build_job(
    *,
    job_id: str,
    action: str = ACTION_CREATE_TEST_ARTIFACT,
    message: str = "Hallo Renderer",
    now: datetime,
    ttl_seconds: int = JOB_TTL_SECONDS,
) -> dict[str, Any]:
    """Build a job. Everything in it is either fixed or server-generated."""
    validate_job_id(job_id)
    if action not in ALLOWED_ACTIONS:
        raise RendererProtocolError(f"Unbekannte Aktion: {action!r}")
    text = clean_text(message)
    if not text:
        raise RendererProtocolError("message darf nicht leer sein")
    if not 1 <= int(ttl_seconds) <= 3600:
        raise RendererProtocolError("ttl_seconds liegt außerhalb des erlaubten Bereichs")
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "job_id": job_id,
        "action": action,
        "created_at": format_time(now),
        "expires_at": format_time(now + timedelta(seconds=int(ttl_seconds))),
        "input": {"message": text},
    }


def validate_job(payload: Any, *, now: datetime) -> dict[str, Any]:
    """Re-read a job with the same rules that produced it.

    The app validates with its own implementation; this exists so
    Roadplanner can check what it is about to hand over, and so the rules
    have one readable definition on the Python side too.
    """
    data = _envelope(payload)
    validate_job_id(data.get("job_id"))
    if data.get("action") not in ALLOWED_ACTIONS:
        raise RendererProtocolError("Unbekannte Aktion")
    payload_input = data.get("input")
    if not isinstance(payload_input, dict):
        raise RendererProtocolError("input fehlt")
    if not clean_text(payload_input.get("message")):
        raise RendererProtocolError("input.message fehlt")
    expires = parse_time(data.get("expires_at"))
    if expires is None:
        raise RendererProtocolError("expires_at fehlt oder ist ungültig")
    if expires <= now:
        raise RendererProtocolError("Auftrag ist abgelaufen")
    return data


def job_filename(job_id: str) -> str:
    """The only place a job filename is ever built."""
    return f"{validate_job_id(job_id)}.json"


# --- status -------------------------------------------------------------


def validate_status(payload: Any, *, job_id: str) -> dict[str, Any]:
    """Read one status file and check it belongs to the job we asked about."""
    data = _envelope(payload)
    if data.get("job_id") != validate_job_id(job_id):
        raise RendererProtocolError("Status gehört zu einem anderen Auftrag")
    state = str(data.get("state") or "")
    if state not in JOB_STATES:
        raise RendererProtocolError(f"Unbekannter Jobzustand: {state!r}")
    progress = data.get("progress")
    if isinstance(progress, bool) or not isinstance(progress, (int, float)):
        progress = None
    error = data.get("error") if isinstance(data.get("error"), dict) else None
    return {
        "job_id": data["job_id"],
        "state": state,
        "terminal": state in TERMINAL_JOB_STATES,
        "progress": None if progress is None else max(0.0, min(1.0, float(progress))),
        "updated_at": data.get("updated_at"),
        "error": None
        if error is None
        else {
            "code": clean_text(error.get("code"), limit=60) or ERROR_INTERNAL,
            "message": clean_text(error.get("message"), limit=MAX_ERROR_LENGTH),
            "retryable": bool(error.get("retryable")),
        },
    }


def state_transition_allowed(previous: str, current: str) -> bool:
    """Terminal is terminal.

    Without this a restarted worker could write ``running`` over a finished
    job and the panel would poll a job that will never report again. An
    unchanged terminal state is allowed, so re-reading the same file is not
    mistaken for a violation.
    """
    if previous not in JOB_STATES or current not in JOB_STATES:
        return False
    if previous in TERMINAL_JOB_STATES:
        return previous == current
    return True


# --- result -------------------------------------------------------------


def validate_result(payload: Any, *, job_id: str) -> dict[str, Any]:
    """Read result.json and the artefact list it declares.

    Only known filenames are accepted, so nothing here can name a path.
    Sizes and hashes are carried in the declaration and checked against the
    bytes separately (``verify_artifact``) - the declaration alone proves
    nothing.
    """
    data = _envelope(payload)
    if data.get("job_id") != validate_job_id(job_id):
        raise RendererProtocolError("Ergebnis gehört zu einem anderen Auftrag")
    if data.get("state") != JOB_COMPLETED:
        raise RendererProtocolError("Ergebnis ohne abgeschlossenen Zustand")
    raw_artifacts = data.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise RendererProtocolError("Ergebnis enthält keine Artefakte")
    artifacts: list[dict[str, Any]] = []
    for entry in raw_artifacts:
        if not isinstance(entry, dict):
            raise RendererProtocolError("Artefakt ist kein Objekt")
        filename = str(entry.get("filename") or "")
        if filename not in ALLOWED_ARTIFACTS:
            raise RendererProtocolError(f"Unerwartetes Artefakt: {filename!r}")
        digest = str(entry.get("sha256") or "").lower()
        if not _SHA256_RE.match(digest):
            raise RendererProtocolError(f"Artefakt {filename} ohne gültigen SHA-256")
        try:
            size = int(entry.get("size_bytes"))
        except (TypeError, ValueError) as err:
            raise RendererProtocolError(
                f"Artefakt {filename} ohne gültige Größe"
            ) from err
        kind = ALLOWED_ARTIFACTS[filename]
        limit = MAX_ARTIFACT_BYTES if kind in TEXT_ARTIFACT_KINDS else MAX_VIDEO_ARTIFACT_BYTES
        if not 0 < size <= limit:
            raise RendererProtocolError(f"Artefakt {filename} überschreitet die Größengrenze")
        artifacts.append(
            {
                "filename": filename,
                "kind": kind,
                "size_bytes": size,
                "sha256": digest,
            }
        )
    seen = {item["filename"] for item in artifacts}
    if len(seen) != len(artifacts):
        raise RendererProtocolError("Doppeltes Artefakt im Ergebnis")
    return {
        "job_id": data["job_id"],
        "state": JOB_COMPLETED,
        "completed_at": data.get("completed_at"),
        "artifacts": artifacts,
        "video": _video_facts(data.get("video")),
        "timings": _timings(data.get("timings")),
    }


def _video_facts(raw: Any) -> dict[str, Any] | None:
    """What ffprobe measured about the produced file, bounded and typed."""
    if not isinstance(raw, dict):
        return None
    def number(key: str) -> float | None:
        value = raw.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
    return {
        "codec": clean_text(raw.get("codec"), limit=40),
        "container": clean_text(raw.get("container"), limit=40),
        "width": number("width"),
        "height": number("height"),
        "fps": number("fps"),
        "duration_seconds": number("duration_seconds"),
        "size_bytes": number("size_bytes"),
        # Only a trip-day render reports these; they say how much of the
        # package actually made it into the picture.
        "photo_count": number("photo_count"),
        "stop_count": number("stop_count"),
    }


def _timings(raw: Any) -> dict[str, float]:
    """Measured durations, in seconds. Unknown keys are dropped."""
    if not isinstance(raw, dict):
        return {}
    allowed = ("package", "browser_start", "render", "probe", "total")
    out: dict[str, float] = {}
    for key in allowed:
        value = raw.get(key)
        if not isinstance(value, bool) and isinstance(value, (int, float)):
            out[key] = round(float(value), 3)
    return out


def verify_artifact(data: bytes, declared: dict[str, Any]) -> bytes:
    """Check bytes against what the result claimed about them."""
    if len(data) != declared["size_bytes"]:
        raise RendererProtocolError(
            f"{declared['filename']}: Größe weicht von der Ankündigung ab"
        )
    digest = hashlib.sha256(data).hexdigest()
    if digest != declared["sha256"]:
        raise RendererProtocolError(f"{declared['filename']}: SHA-256 stimmt nicht")
    return data
