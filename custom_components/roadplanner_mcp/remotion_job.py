"""The versioned Python→Node job contract and the Node→Python event parser.

Pure: no Home Assistant, no subprocess, no filesystem writes. That makes
the part that decides *what a renderer is allowed to be asked to do*
testable on its own - which matters more here than anywhere else in the
codebase, because everything this module produces is handed to a process
outside Python.

The security posture is deliberate and narrow:

- the composition comes from a fixed whitelist, never from the client;
- the output path is built server-side and must resolve inside the
  Roadplanner export directory - a caller cannot name it;
- there are no user scripts, no shell arguments, no URLs, no React
  components in the payload. The job is data, never code;
- ``allow_downloads`` is pinned to ``False`` for the whole spike, so the
  renderer cannot quietly fetch a browser on the user's machine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

JOB_SCHEMA_VERSION = 1

# One composition exists. A whitelist rather than a free string because the
# name reaches a rendering engine.
ALLOWED_COMPOSITIONS = ("roadplanner-test",)
DEFAULT_COMPOSITION = "roadplanner-test"

MIN_DURATION_SECONDS = 1
MAX_DURATION_SECONDS = 30
ALLOWED_CODECS = ("h264",)
MIN_DIMENSION = 128
MAX_DIMENSION = 1920
MIN_FPS = 1
MAX_FPS = 60
MAX_TEXT_LENGTH = 120

JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
# One event line must not be able to exhaust memory.
MAX_EVENT_LINE_BYTES = 16 * 1024
MAX_STDERR_BYTES = 64 * 1024


class RemotionJobError(ValueError):
    """A job or event that must not be passed on."""


def _text(value: Any, *, field: str) -> str:
    cleaned = " ".join(str(value or "").split())[:MAX_TEXT_LENGTH]
    if not cleaned:
        raise RemotionJobError(f"{field} darf nicht leer sein")
    return cleaned


def _int_in(value: Any, *, field: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as err:
        raise RemotionJobError(f"{field} muss eine ganze Zahl sein") from err
    if not low <= number <= high:
        raise RemotionJobError(f"{field} muss zwischen {low} und {high} liegen")
    return number


def resolve_output_path(output_dir: Path, job_id: str) -> Path:
    """Build the output path SERVER-side and prove it stays inside the box.

    Traversal and symlink escapes are checked with ``resolve()`` on both
    sides, because a path that merely *looks* contained can still point
    somewhere else through a link.
    """
    if not JOB_ID_RE.match(str(job_id or "")):
        raise RemotionJobError("Ungültige Job-ID")
    root = Path(output_dir).resolve()
    candidate = (root / f"{job_id}.mp4").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as err:
        raise RemotionJobError(
            "Der Ausgabepfad liegt außerhalb des Roadplanner-Verzeichnisses"
        ) from err
    return candidate


def build_job(
    *,
    job_id: str,
    output_path: Path,
    title: str = "Roadplanner Remotion Test",
    subtitle: str = "Lokaler Unterprozess",
    duration_seconds: int = 5,
    composition: str = DEFAULT_COMPOSITION,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    codec: str = "h264",
    crf: int = 20,
    x264_preset: str = "veryfast",
    browser_executable: str | None = None,
) -> dict[str, Any]:
    """Return a fully validated job payload."""
    if composition not in ALLOWED_COMPOSITIONS:
        raise RemotionJobError(f"Unbekannte Komposition: {composition}")
    if codec not in ALLOWED_CODECS:
        raise RemotionJobError(f"Nicht unterstützter Codec: {codec}")
    if not JOB_ID_RE.match(str(job_id or "")):
        raise RemotionJobError("Ungültige Job-ID")
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "composition": composition,
        "output_path": str(output_path),
        "input": {
            "title": _text(title, field="title"),
            "subtitle": _text(subtitle, field="subtitle"),
            "duration_seconds": _int_in(
                duration_seconds,
                field="duration_seconds",
                low=MIN_DURATION_SECONDS,
                high=MAX_DURATION_SECONDS,
            ),
        },
        "render": {
            "width": _int_in(width, field="width", low=MIN_DIMENSION, high=MAX_DIMENSION),
            "height": _int_in(height, field="height", low=MIN_DIMENSION, high=MAX_DIMENSION),
            "fps": _int_in(fps, field="fps", low=MIN_FPS, high=MAX_FPS),
            "codec": codec,
            "crf": _int_in(crf, field="crf", low=0, high=51),
            "x264_preset": _text(x264_preset, field="x264_preset"),
        },
        "runtime": {
            "browser_executable": str(browser_executable) if browser_executable else None,
            # Pinned for the whole spike: the renderer must never fetch a
            # browser onto the user's machine by itself.
            "allow_downloads": False,
        },
    }


def validate_job(payload: Any) -> dict[str, Any]:
    """Re-validate a job read back from disk - the same rules, again."""
    if not isinstance(payload, dict):
        raise RemotionJobError("Job ist kein Objekt")
    if payload.get("schema_version") != JOB_SCHEMA_VERSION:
        raise RemotionJobError(
            f"Job-Schemaversion {payload.get('schema_version')!r} wird nicht unterstützt"
        )
    render = payload.get("render") if isinstance(payload.get("render"), dict) else {}
    runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else {}
    if payload.get("composition") not in ALLOWED_COMPOSITIONS:
        raise RemotionJobError("Unbekannte Komposition")
    if render.get("codec") not in ALLOWED_CODECS:
        raise RemotionJobError("Nicht unterstützter Codec")
    if runtime.get("allow_downloads"):
        raise RemotionJobError("Downloads sind im Spike nicht erlaubt")
    return payload


# --- Node → Python ------------------------------------------------------

KNOWN_EVENTS = ("started", "phase", "progress", "completed", "failed")


def parse_event_line(line: str) -> dict[str, Any] | None:
    """Turn ONE stdout line into an event, or None if it is not one.

    A renderer that prints something unexpected must not take the run down
    with it, so anything unparseable is ignored rather than raised - except
    an over-long line, which is a protocol violation worth stopping for.
    """
    raw = str(line or "").strip()
    if not raw:
        return None
    if len(raw.encode("utf-8", errors="ignore")) > MAX_EVENT_LINE_BYTES:
        raise RemotionJobError("Ereigniszeile überschreitet die Größengrenze")
    if not raw.startswith("{"):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    event = str(payload.get("event") or "")
    if event not in KNOWN_EVENTS:
        return None
    return payload


def progress_of(event: dict[str, Any]) -> float | None:
    """Clamped 0..1 progress, or None when the event carries none."""
    value = event.get("progress")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return max(0.0, min(1.0, float(value)))


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def clean_detail(text: Any, *, limit: int = 600) -> str:
    """Strip ANSI codes and bound the length of anything we store or show."""
    stripped = _ANSI_RE.sub("", str(text or ""))
    collapsed = " ".join(stripped.split())
    return collapsed[:limit]
