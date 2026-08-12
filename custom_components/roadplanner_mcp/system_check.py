"""End-to-end probe of every live interface Roadplanner depends on.

Live request: "Können wir zu einem Testsystem kommen mit Schnittstellen
gegen livesysteme wie OneDrive und P4n und google? Das ist mir aktuell echt
zu hakelig." Every integration failure in this project so far was found by
the user on the road, reported as a screenshot, and diagnosed by reasoning
backwards from one error message - because the credentials, the network and
the real data all live in the user's Home Assistant, not in any developer
environment.

This module closes that loop. It runs INSIDE that Home Assistant and walks
the same code paths the exports use - it resolves a real thumbnail URL for
a real photo and downloads its first bytes, reads a real Park4Night page,
fetches a real map tile - and reports per interface what happened, with the
concrete status, reason and duration. One button, one screenshot, no
guessing.

Every probe is bounded and read-only: nothing is created, changed or
deleted anywhere, and a failing probe is a result, never an exception.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .map_snapshot import (
    LAST_SNAPSHOT_ERROR,
    async_fetch_google_static_map,
    async_fetch_snapshot,
)
from .page_images import async_fetch_page_place
from .trip_export_photos import _format_name, async_download_photo

_LOGGER = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 20.0
# A public Park4Night place that exists and publishes coordinates. Only ever
# read, never written to.
_PARK4NIGHT_PROBE_URL = "https://park4night.com/lieu/110490/"
# A single low-zoom tile request over Sweden - well within the OSM tile
# usage policy.
_TILE_PROBE_ZOOM = 6

OK = "ok"
WARN = "warn"
FAIL = "fail"
SKIPPED = "skipped"


@dataclass(slots=True)
class ProbeResult:
    """One interface's verdict, safe to show and to paste into a report."""

    id: str
    label: str
    state: str
    detail: str = ""
    duration_ms: int = 0
    hint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "detail": self.detail[:400],
            "duration_ms": self.duration_ms,
            "hint": self.hint[:300],
        }


@dataclass
class _Recorder:
    results: list[ProbeResult] = field(default_factory=list)

    async def run(
        self, probe_id: str, label: str, coro_factory: Any, *, hint: str = ""
    ) -> ProbeResult:
        """Run one probe with a hard timeout; never raise."""
        started = monotonic()
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                state, detail = await coro_factory()
        except TimeoutError:
            state, detail = FAIL, f"Zeitüberschreitung nach {PROBE_TIMEOUT_SECONDS:.0f} s"
        except Exception as err:  # noqa: BLE001 - a probe result is never an exception
            _LOGGER.debug("System check probe %s failed: %s", probe_id, err)
            state, detail = FAIL, f"{type(err).__name__}: {err}"
        result = ProbeResult(
            id=probe_id,
            label=label,
            state=state,
            detail=str(detail or ""),
            duration_ms=int((monotonic() - started) * 1000),
            hint=hint if state in {FAIL, WARN} else "",
        )
        self.results.append(result)
        return result


def _pillow_report() -> tuple[str, str]:
    """Which image formats this Home Assistant can actually decode."""
    try:
        from PIL import Image
    except ImportError:
        return FAIL, "Pillow ist nicht installiert - ohne sie gibt es weder PDF noch Video"
    supported = set(Image.registered_extensions().values())
    has_heic = bool({"HEIF", "HEIC"} & supported)
    # Missing HEIC is the normal case and NOT a fault: Roadplanner asks
    # OneDrive for rendered JPEG previews precisely because of it. It is
    # reported because it explains why originals are never used directly.
    return OK, (
        f"{len(supported)} Formate lesbar, HEIC "
        + ("dabei" if has_heic else "nicht dabei (erwartet - daher JPEG-Vorschauen)")
    )


async def _probe_onedrive_photo(
    session: Any, experience: Any, trip_id: str, media_item: dict[str, Any], size: str
) -> tuple[str, str]:
    """Resolve a real photo URL and fetch its first bytes - the export path."""
    media_id = str(media_item.get("id") or "")
    url = await experience.async_media_redirect_url(
        trip_id, media_id, "thumbnail", size=size
    )
    if not str(url or "").startswith("https://"):
        return FAIL, "OneDrive lieferte keine https-URL"
    photo = await async_download_photo(session, url)
    if not photo:
        return FAIL, "URL aufgelöst, aber der Download lieferte kein brauchbares Bild"
    return OK, f"{_format_name(photo)}, {len(photo) // 1024} kB geladen"


def _snapshot_reason(fallback: str) -> str:
    """The map module records WHY it failed - report that, not the symptom."""
    reason = str(LAST_SNAPSHOT_ERROR.get("reason") or "").strip()
    return reason or fallback


async def async_run_system_check(
    hass: Any, runtime: Any, *, trip_id: str = ""
) -> dict[str, Any]:
    """Probe every live interface and return a report."""
    session = async_get_clientsession(hass)
    recorder = _Recorder()
    experience = runtime.experience

    # --- Bildverarbeitung (lokal, keine Schnittstelle) -------------------
    await recorder.run(
        "pillow",
        "Bildverarbeitung (Pillow)",
        lambda: asyncio.sleep(0, result=_pillow_report()),
    )

    try:
        from .ffmpeg_runner import ffmpeg_available

        ffmpeg_state = (OK, "gefunden") if ffmpeg_available() else (
            WARN,
            "nicht gefunden - der Video-Export ist auf diesem Host nicht verfügbar",
        )
    except ImportError:
        ffmpeg_state = (FAIL, "ffmpeg_runner nicht ladbar")
    await recorder.run(
        "ffmpeg", "Video-Encoder (ffmpeg)", lambda: asyncio.sleep(0, result=ffmpeg_state)
    )

    # --- OneDrive -------------------------------------------------------
    onedrive = getattr(experience, "onedrive", None)
    status = onedrive.status() if onedrive is not None else {}
    onedrive_ready = bool(status.get("configured") and status.get("connected"))
    if not status.get("configured"):
        await recorder.run(
            "onedrive_auth",
            "OneDrive-Anmeldung",
            lambda: asyncio.sleep(0, result=(SKIPPED, "keine Anwendungs-ID hinterlegt")),
        )
    elif not status.get("connected"):
        await recorder.run(
            "onedrive_auth",
            "OneDrive-Anmeldung",
            lambda: asyncio.sleep(
                0,
                result=(
                    FAIL,
                    f"nicht verbunden ({status.get('last_error') or 'keine Autorisierung'})",
                ),
            ),
            hint="Unter Erinnerungen → OneDrive neu autorisieren.",
        )
    else:
        async def _profile() -> tuple[str, str]:
            await onedrive._graph_json("/me/drive", params={"$select": "id,driveType"})
            return OK, f"verbunden als {status.get('account_email') or status.get('account_name') or 'unbekannt'}"

        await recorder.run("onedrive_auth", "OneDrive-Anmeldung", _profile)

    # The exact path that produced photo-less exports: resolve a real
    # thumbnail (custom size first, then the standard one) and download its
    # first bytes. Both probes are ALWAYS reported, so the report has the
    # same shape whatever the setup - a missing line is easy to overlook.
    media_item, resolved_trip_id = (
        await _pick_probe_photo(runtime, experience, trip_id)
        if onedrive_ready
        else (None, trip_id)
    )
    thumbnail_probes = (
        ("onedrive_thumb_custom", "OneDrive-Vorschau (hohe Auflösung)", "c1920x1440",
         "PDF und Video nutzen genau diesen Weg für eigene Fotos."),
        ("onedrive_thumb_large", "OneDrive-Vorschau (Standard)", "large",
         "Fällt auch diese aus, kommt kein eigenes Foto in die Exporte."),
    )
    for probe_id, label, size, hint in thumbnail_probes:
        if media_item is None:
            reason = (
                "OneDrive nicht verbunden"
                if not onedrive_ready
                else "keine synchronisierten Fotos in der Reise"
            )
            await recorder.run(
                probe_id,
                label,
                lambda reason=reason: asyncio.sleep(0, result=(SKIPPED, reason)),
            )
            continue
        await recorder.run(
            probe_id,
            label,
            lambda size=size: _probe_onedrive_photo(
                session, experience, resolved_trip_id, media_item, size
            ),
            hint=hint,
        )

    # --- Park4Night -----------------------------------------------------
    async def _park4night() -> tuple[str, str]:
        place = await async_fetch_page_place(hass, _PARK4NIGHT_PROBE_URL)
        read_status = str(place.get("read_status") or "")
        if "latitude" in place:
            return OK, f"Seite gelesen, GPS gefunden ({place['latitude']}, {place['longitude']})"
        if read_status == "ok":
            # WHAT came back matters: the real page is hundreds of
            # kilobytes and names the place, a consent wall or a
            # JavaScript shell is small and names nothing. "ohne
            # GPS-Angabe" alone did not distinguish the two.
            detail = f"{int(place.get('page_bytes') or 0) // 1024} kB gelesen"
            name = str(place.get("name") or "").strip()
            detail += f", Seitentitel „{name[:60]}“" if name else ", ohne Seitentitel"
            # The decisive distinction: a page with NO coordinates needs a
            # different answer than one with a dozen that disagree.
            scan = str(place.get("coordinate_scan") or "").strip()
            if scan:
                detail += f", {scan}"
            return WARN, f"Seite gelesen, aber ohne GPS-Angabe im Seitenquelltext ({detail})"
        return FAIL, f"Seite nicht lesbar ({read_status or 'unbekannt'})"

    await recorder.run(
        "park4night",
        "Park4Night-Seitenabruf",
        _park4night,
        hint="Ohne Seitenabruf können Stellplatz-Links keine Koordinaten liefern.",
    )

    # --- Kartenbilder ---------------------------------------------------
    async def _osm_tile() -> tuple[str, str]:
        # Cleared first: a reason left over from an earlier probe would be
        # reported as the cause of THIS one.
        LAST_SNAPSHOT_ERROR.clear()
        snapshot = await async_fetch_snapshot(
            session,
            "openstreetmap",
            None,
            center_lat=59.3,
            center_lon=15.2,
            markers=[(59.3, 15.2)],
            zoom=_TILE_PROBE_ZOOM,
            width_px=256,
            height_px=256,
        )
        if not snapshot:
            return FAIL, _snapshot_reason("keine Kartenkachel erhalten")
        return OK, f"{_format_name(snapshot)}, {len(snapshot) // 1024} kB"

    await recorder.run(
        "map_openstreetmap",
        "Kartenkacheln (OpenStreetMap)",
        _osm_tile,
        hint="Ohne sie bleiben PDF-Routenkarte und Video-Kartenbilder leer.",
    )

    google_key = getattr(runtime.trip_pdf, "_google_maps_api_key", None)
    # Google Static Maps only matters when it is the SELECTED provider.
    # With OpenStreetMap configured and working, a red "FEHL" here reads
    # like the exports are broken when nothing of the sort is true.
    selected_provider = str(
        getattr(runtime.trip_pdf, "_map_snapshot_provider", "") or ""
    )
    severity = FAIL if selected_provider == "google_static_maps" else WARN
    if not google_key:
        await recorder.run(
            "map_google",
            "Kartenbild (Google Static Maps)",
            lambda: asyncio.sleep(0, result=(SKIPPED, "kein Google-API-Schlüssel hinterlegt")),
        )
    else:
        async def _google_map() -> tuple[str, str]:
            LAST_SNAPSHOT_ERROR.clear()
            # The GOOGLE branch directly, not async_fetch_snapshot: that one
            # falls back to OpenStreetMap now, which would let the probe
            # report a cheerful OK while Google keeps rejecting every call.
            snapshot = await async_fetch_google_static_map(
                session,
                google_key,
                center_lat=59.3,
                center_lon=15.2,
                markers=[(59.3, 15.2)],
                zoom=6,
                width_px=320,
                height_px=240,
            )
            if not snapshot:
                # The reason was recorded a moment ago and then dropped on
                # the floor - "kein Kartenbild erhalten" was the entire
                # diagnosis in the live Systemcheck.
                return severity, _snapshot_reason("kein Kartenbild erhalten")
            return OK, f"{_format_name(snapshot)}, {len(snapshot) // 1024} kB"

        await recorder.run(
            "map_google",
            "Kartenbild (Google Static Maps)",
            _google_map,
            hint=(
                "Prüfe, ob die Static Maps API für den Schlüssel freigeschaltet ist."
                if severity == FAIL
                else "Nur wichtig, wenn du als Kartenquelle Google statt "
                "OpenStreetMap wählst - die Exporte nutzen aktuell "
                "OpenStreetMap."
            ),
        )

    # --- Reisebegleiter (Gemini) ---------------------------------------
    assistant = runtime.assistant
    if not getattr(assistant, "configured", False):
        await recorder.run(
            "gemini",
            "Reisebegleiter (Gemini)",
            lambda: asyncio.sleep(0, result=(SKIPPED, "nicht konfiguriert")),
        )
    else:
        async def _gemini() -> tuple[str, str]:
            result = await assistant.async_test(user_id="system_check", trip_id=trip_id)
            if result.get("ok") is False:
                return FAIL, str(result.get("error") or "Antwort fehlgeschlagen")
            return OK, str(result.get("model") or assistant.model or "erreichbar")

        await recorder.run("gemini", "Reisebegleiter (Gemini)", _gemini)

    # --- Filmmusik (Lyria) ---------------------------------------------
    #
    # Asked by READING the model listing, never by generating: a system
    # check that quietly spends eight cents is a system check nobody can
    # run twice. The listing is a GET with the key that is already
    # configured, and it answers the one question that has been open -
    # whether Lyria is reachable from AI Studio at all, or whether this
    # needs a Google-Cloud project and a service account.
    async def _lyria() -> tuple[str, str]:
        service = getattr(runtime, "film_music", None)
        if service is None:
            return SKIPPED, "Filmmusik ist nicht eingerichtet"
        found = await service.async_probe_models()
        state = str(found.get("state") or FAIL)
        detail = str(found.get("detail") or "")
        return {"ok": OK, "warn": WARN, "skipped": SKIPPED}.get(state, FAIL), detail

    await recorder.run(
        "lyria",
        "Filmmusik (Lyria)",
        _lyria,
        hint=(
            "Liest nur die Modell-Liste. Es wird keine Musik erzeugt und "
            "nichts abgerechnet."
        ),
    )

    results = [item.as_dict() for item in recorder.results]
    return {
        "checks": results,
        "summary": {
            state: sum(1 for item in results if item["state"] == state)
            for state in (OK, WARN, FAIL, SKIPPED)
        },
        "report": format_report(results),
    }


async def _pick_probe_photo(
    runtime: Any, experience: Any, trip_id: str
) -> tuple[dict[str, Any] | None, str]:
    """One real synced photo of the (selected) trip, or None."""
    try:
        payload = await runtime.manager.async_get_assistant_payload(trip_id or None)
        resolved = str(payload.get("selected_trip_id") or trip_id or "")
        if not resolved:
            return None, ""
        state = await experience.async_panel_payload(resolved)
    except Exception as err:  # noqa: BLE001 - the probe must not depend on this
        _LOGGER.debug("System check could not read media: %s", err)
        return None, ""
    for item in state.get("media") or []:
        if (
            isinstance(item, dict)
            and str(item.get("provider") or "onedrive") != "upload"
            and item.get("provider_item_id")
        ):
            return item, resolved
    return None, resolved


_STATE_SYMBOLS = {OK: "OK  ", WARN: "WARN", FAIL: "FEHL", SKIPPED: "----"}


def format_report(checks: list[dict[str, Any]]) -> str:
    """Plain-text report, made to be copied into a message."""
    lines = []
    for check in checks:
        symbol = _STATE_SYMBOLS.get(str(check.get("state")), "?   ")
        line = f"[{symbol}] {check.get('label')}: {check.get('detail')}"
        duration = int(check.get("duration_ms") or 0)
        if duration:
            line += f" ({duration} ms)"
        lines.append(line)
        if check.get("hint"):
            lines.append(f"         → {check['hint']}")
    return "\n".join(lines)
