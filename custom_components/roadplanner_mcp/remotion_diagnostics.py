"""Read-only runtime diagnostics for the Remotion subprocess spike.

This module answers exactly one question: *could* this Home Assistant
installation run a local Node/Remotion child process? It never installs,
downloads, or changes anything - not an ``npm install``, not a browser
fetch, not a system package. A missing runtime is a **result**, not a
problem to fix behind the user's back.

That restraint is the point of the spike. The whole exercise exists to
find out whether the subprocess route is viable at all; a diagnosis that
quietly repaired its own preconditions would answer a different question.

Every check reports one of the codes in ``DiagnosisStatus``. The codes are
stable and machine-readable so the panel, the tests and the eventual
Go/No-Go write-up all speak the same language.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import platform
import shutil
from typing import Any

# --- stable status codes -------------------------------------------------

READY = "READY"
NODE_MISSING = "NODE_MISSING"
NODE_VERSION_UNSUPPORTED = "NODE_VERSION_UNSUPPORTED"
NPM_MISSING = "NPM_MISSING"
RENDERER_NOT_CONFIGURED = "RENDERER_NOT_CONFIGURED"
RENDERER_PACKAGE_MISSING = "RENDERER_PACKAGE_MISSING"
BROWSER_MISSING = "BROWSER_MISSING"
BROWSER_NOT_EXECUTABLE = "BROWSER_NOT_EXECUTABLE"
BROWSER_LIBRARIES_MISSING = "BROWSER_LIBRARIES_MISSING"
FFMPEG_MISSING = "FFMPEG_MISSING"
FFPROBE_MISSING = "FFPROBE_MISSING"
OUTPUT_NOT_WRITABLE = "OUTPUT_NOT_WRITABLE"
INSUFFICIENT_DISK_SPACE = "INSUFFICIENT_DISK_SPACE"
SUBPROCESS_FORBIDDEN = "SUBPROCESS_FORBIDDEN"
RENDER_TIMEOUT = "RENDER_TIMEOUT"
RENDER_CANCELLED = "RENDER_CANCELLED"
RENDER_FAILED = "RENDER_FAILED"
OUTPUT_INVALID = "OUTPUT_INVALID"

ALL_STATUSES = (
    READY,
    NODE_MISSING,
    NODE_VERSION_UNSUPPORTED,
    NPM_MISSING,
    RENDERER_NOT_CONFIGURED,
    RENDERER_PACKAGE_MISSING,
    BROWSER_MISSING,
    BROWSER_NOT_EXECUTABLE,
    BROWSER_LIBRARIES_MISSING,
    FFMPEG_MISSING,
    FFPROBE_MISSING,
    OUTPUT_NOT_WRITABLE,
    INSUFFICIENT_DISK_SPACE,
    SUBPROCESS_FORBIDDEN,
    RENDER_TIMEOUT,
    RENDER_CANCELLED,
    RENDER_FAILED,
    OUTPUT_INVALID,
)

# Remotion 4 requires Node 18+; 20 LTS is the realistic floor for the
# SSR APIs used here.
MIN_NODE_MAJOR = 20
# A five-second 720p test render plus the bundle needs far less, but a
# nearly-full disk is worth reporting before anything is started.
MIN_FREE_BYTES = 512 * 1024 * 1024
_PROBE_TIMEOUT_SECONDS = 20.0

# Where a Chromium-ish binary usually lives if the operator already has
# one. Nothing is downloaded; these are only looked at.
_BROWSER_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "chrome",
    "google-chrome",
    "google-chrome-stable",
    "chrome-headless-shell",
)
_BROWSER_PATH_CANDIDATES = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/opt/pw-browsers/chromium",
    "/root/.cache/remotion/chrome-headless-shell",
)

_MESSAGES = {
    READY: (
        "Die Umgebung kann einen lokalen Remotion-Prozess ausführen.",
        "Der Testrender kann gestartet werden.",
    ),
    NODE_MISSING: (
        "Node.js wurde auf diesem System nicht gefunden.",
        "Noch nichts installieren. Dieses Ergebnis im technischen Bericht "
        "festhalten - es beantwortet die Machbarkeitsfrage bereits.",
    ),
    NODE_VERSION_UNSUPPORTED: (
        "Die gefundene Node.js-Version ist zu alt für Remotion.",
        "Noch nichts installieren. Version im Bericht festhalten.",
    ),
    NPM_MISSING: (
        "npm wurde nicht gefunden.",
        "Noch nichts installieren. Für den reinen Testrender ist npm nicht "
        "zwingend, für eine spätere Paketierung aber relevant.",
    ),
    RENDERER_NOT_CONFIGURED: (
        "Es ist kein Pfad zum Remotion-Renderer hinterlegt.",
        "Den Pfad in den Roadplanner-Optionen eintragen, falls das "
        "Renderer-Paket bereits lokal vorliegt.",
    ),
    RENDERER_PACKAGE_MISSING: (
        "Am hinterlegten Pfad liegt kein installiertes Renderer-Paket.",
        "Noch nichts installieren. Der Befund gehört in den Bericht: Er ist "
        "genau die offene Paketierungsfrage des Spikes.",
    ),
    BROWSER_MISSING: (
        "Für Remotion wurde kein verwendbarer Browser gefunden.",
        "Noch nichts installieren. Dieses Ergebnis im technischen Bericht "
        "dokumentieren.",
    ),
    BROWSER_NOT_EXECUTABLE: (
        "Der gefundene Browser ist nicht ausführbar.",
        "Rechte prüfen, aber nichts nachinstallieren.",
    ),
    FFMPEG_MISSING: (
        "ffmpeg wurde nicht gefunden.",
        "Ohne ffmpeg funktioniert auch der bestehende Videoexport nicht - "
        "das ist ein eigenständiger Befund.",
    ),
    FFPROBE_MISSING: (
        "ffprobe wurde nicht gefunden.",
        "Ohne ffprobe kann das Ergebnis nicht geprüft werden.",
    ),
    OUTPUT_NOT_WRITABLE: (
        "Das Ausgabeverzeichnis ist nicht beschreibbar.",
        "Pfad und Rechte prüfen.",
    ),
    INSUFFICIENT_DISK_SPACE: (
        "Auf dem Datenträger ist zu wenig Platz frei.",
        "Platz schaffen, bevor ein Render versucht wird.",
    ),
    SUBPROCESS_FORBIDDEN: (
        "Ein einfacher Node-Unterprozess ließ sich nicht ausführen.",
        "Das ist das entscheidende Ergebnis: Der Unterprozessweg ist in "
        "dieser Umgebung nicht gangbar.",
    ),
}


def describe(status: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wrap one status code into the panel/report envelope."""
    summary, next_step = _MESSAGES.get(
        status, ("Unbekannter Zustand.", "Ergebnis im Bericht festhalten.")
    )
    return {
        "status": status,
        "ready": status == READY,
        "summary_de": summary,
        "details": details or {},
        "recommended_next_step_de": next_step,
    }


def _node_major(version: str) -> int | None:
    text = str(version or "").strip().lstrip("v")
    head = text.split(".", 1)[0]
    return int(head) if head.isdigit() else None


def _find_browser(configured: str) -> tuple[str, str]:
    """Return (path, status) for a browser - looked at, never fetched."""
    if configured:
        path = Path(configured)
        if not path.is_file():
            return "", BROWSER_MISSING
        if not os.access(path, os.X_OK):
            return str(path), BROWSER_NOT_EXECUTABLE
        return str(path), READY
    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found, READY
    for candidate in _BROWSER_PATH_CANDIDATES:
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path), READY
    return "", BROWSER_MISSING


def _renderer_state(configured: str) -> tuple[str, str]:
    """Is a usable renderer package present at the configured path?"""
    if not configured:
        return "", RENDERER_NOT_CONFIGURED
    root = Path(configured)
    script = root / "scripts" / "render.mjs"
    modules = root / "node_modules" / "@remotion" / "renderer"
    if not script.is_file() or not modules.is_dir():
        return str(root), RENDERER_PACKAGE_MISSING
    return str(script), READY


async def _probe_node_subprocess(node_path: str) -> tuple[bool, str]:
    """Run the smallest possible Node program and read its JSON back.

    This is the actual feasibility question in one call: not "is node on
    the PATH" but "can this process spawn it and read its output".
    """
    try:
        process = await asyncio.create_subprocess_exec(
            node_path,
            "-e",
            'process.stdout.write(JSON.stringify({ok:true,version:process.version}))',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as err:
        return False, f"{type(err).__name__}: {err}"[:300]
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_PROBE_TIMEOUT_SECONDS
        )
    except (TimeoutError, asyncio.TimeoutError):
        process.kill()
        await process.wait()
        return False, "Zeitüberschreitung beim Node-Testaufruf"
    if process.returncode != 0:
        return False, (stderr or b"").decode("utf-8", errors="replace")[-300:]
    try:
        payload = json.loads((stdout or b"").decode("utf-8", errors="replace"))
    except ValueError:
        return False, "Node lieferte keine gültige JSON-Ausgabe"
    return bool(payload.get("ok")), str(payload.get("version") or "")


def _inspect_output_dir(output_dir: Path, details: dict[str, Any]) -> None:
    """Record whether the export directory can actually be written to."""
    free_bytes = 0
    writable = False
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".roadplanner_write_probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        writable = True
        free_bytes = shutil.disk_usage(output_dir).free
    except OSError as err:
        details["output_error"] = f"{type(err).__name__}: {err}"[:200]
    details["output_dir"] = str(output_dir)
    details["output_writable"] = writable
    details["free_bytes"] = free_bytes


async def async_diagnose(
    *,
    output_dir: Path,
    renderer_path: str = "",
    browser_path: str = "",
    node_path: str = "",
) -> dict[str, Any]:
    """Inspect the environment and return one envelope. Changes nothing.

    Every cheap read-only check runs *before* any verdict is formed, even
    when an earlier one already decides the outcome. Returning early would
    leave the untested fields empty, and the report renders an empty field
    as "not found" - so a missing Node would have claimed this system has
    no ffmpeg, on a machine whose ffmpeg demonstrably renders trip videos.
    A feasibility report that reads as evidence has to distinguish "looked
    and it is absent" from "never looked".
    """
    details: dict[str, Any] = {
        "platform": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }

    # --- facts that cost nothing and never depend on each other ---------
    node = node_path or shutil.which("node") or ""
    details["node_path"] = node
    details["npm_path"] = shutil.which("npm") or ""
    details["ffmpeg_path"] = shutil.which("ffmpeg") or ""
    details["ffprobe_path"] = shutil.which("ffprobe") or ""

    browser, browser_status = _find_browser(browser_path)
    details["browser_path"] = browser
    details["browser_configured"] = bool(browser_path)

    renderer, renderer_status = _renderer_state(renderer_path)
    details["renderer_path"] = renderer
    details["renderer_configured"] = bool(renderer_path)

    _inspect_output_dir(output_dir, details)

    # --- the one check that cannot run without its precondition ---------
    # Spawning Node is the actual feasibility question, and it is the only
    # step here that starts a process. It stays gated on Node existing.
    node_probe_ok = False
    if node:
        node_probe_ok, version_or_error = await _probe_node_subprocess(node)
        if node_probe_ok:
            details["node_version"] = version_or_error
            details["node_major"] = _node_major(version_or_error)
        else:
            details["error"] = version_or_error
    details["node_probed"] = bool(node)

    # --- one verdict, naming the FIRST thing that blocks a render -------
    if not node:
        return describe(NODE_MISSING, details)
    if not node_probe_ok:
        return describe(SUBPROCESS_FORBIDDEN, details)
    major = details.get("node_major")
    if major is not None and major < MIN_NODE_MAJOR:
        details["required_major"] = MIN_NODE_MAJOR
        return describe(NODE_VERSION_UNSUPPORTED, details)
    if not details["output_writable"]:
        return describe(OUTPUT_NOT_WRITABLE, details)
    free_bytes = details["free_bytes"]
    if free_bytes and free_bytes < MIN_FREE_BYTES:
        details["required_bytes"] = MIN_FREE_BYTES
        return describe(INSUFFICIENT_DISK_SPACE, details)
    if not details["ffmpeg_path"]:
        return describe(FFMPEG_MISSING, details)
    if not details["ffprobe_path"]:
        return describe(FFPROBE_MISSING, details)
    if renderer_status != READY:
        return describe(renderer_status, details)
    if browser_status != READY:
        return describe(browser_status, details)
    if not details["npm_path"]:
        # npm is not needed to RUN an installed renderer, so this is the
        # last thing checked and only reported when nothing else blocks.
        return describe(NPM_MISSING, details)
    return describe(READY, details)
