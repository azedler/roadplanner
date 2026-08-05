"""One button that probes every live interface from inside Home Assistant.

Live request: "Können wir zu einem Testsystem kommen mit Schnittstellen
gegen livesysteme wie OneDrive und P4n und google? Das ist mir aktuell echt
zu hakelig." Every integration failure so far was found by the user on the
road and diagnosed backwards from a single error message, because the
credentials, the network and the real photos only exist in that Home
Assistant. The check runs there and walks the same code paths the exports
use, so a failure names itself instead of being guessed at.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_system_check_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def _stub(name: str, **attributes) -> None:
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


_stub(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda **kwargs: kwargs,
)
_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda *a, **k: object())
_stub("homeassistant.helpers.storage", Store=object)
_stub("homeassistant.util")
_stub("homeassistant.util.dt", now=None, utcnow=None)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("system_check")

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 4096


class FakeOneDrive:
    def __init__(self, status: dict) -> None:
        self._status = status
        self.requests: list[str] = []

    def status(self) -> dict:
        return self._status

    async def _graph_json(self, path, **kwargs) -> dict:
        self.requests.append(path)
        return {"id": "drive-1"}


class FakeExperience:
    def __init__(self, onedrive, media, *, thumbnail_error: Exception | None = None) -> None:
        self.onedrive = onedrive
        self._media = media
        self._thumbnail_error = thumbnail_error
        self.sizes: list[str] = []

    async def async_panel_payload(self, trip_id, **kwargs) -> dict:
        return {"media": self._media}

    async def async_media_redirect_url(self, trip_id, media_id, kind, *, size="large") -> str:
        self.sizes.append(size)
        if self._thumbnail_error is not None:
            raise self._thumbnail_error
        return "https://cdn.example/photo.jpg"


class FakeManager:
    async def async_get_assistant_payload(self, trip_id=None) -> dict:
        return {"selected_trip_id": trip_id or "trip-1"}


class FakeAssistant:
    configured = True
    model = "gemini-test"

    async def async_test(self, *, user_id: str, trip_id: str) -> dict:
        return {"ok": True, "model": "gemini-test"}


class FakeRuntime:
    def __init__(self, experience, *, google_key=None, map_provider="openstreetmap") -> None:
        self.experience = experience
        self.manager = FakeManager()
        self.assistant = FakeAssistant()
        self.trip_pdf = types.SimpleNamespace(
            _google_maps_api_key=google_key,
            _map_snapshot_provider=map_provider,
        )


def _patch(**functions) -> None:
    for name, value in functions.items():
        setattr(module, name, value)


def _run(runtime) -> dict:
    return asyncio.run(module.async_run_system_check(object(), runtime, trip_id="trip-1"))


def _states(result: dict) -> dict[str, str]:
    return {check["id"]: check["state"] for check in result["checks"]}


def verify_a_healthy_system_reports_ok_for_every_reachable_interface() -> None:
    async def fake_download(session, url):
        return JPEG

    async def fake_place(hass, url):
        assert "park4night" in url
        return {"read_status": "ok", "latitude": 59.9, "longitude": 15.2}

    async def fake_snapshot(session, provider, key, **kwargs):
        return JPEG

    _patch(
        async_download_photo=fake_download,
        async_fetch_page_place=fake_place,
        async_fetch_snapshot=fake_snapshot,
    )
    onedrive = FakeOneDrive({"configured": True, "connected": True, "account_email": "a@b.c"})
    experience = FakeExperience(
        onedrive, [{"id": "m1", "provider_item_id": "drive-item-1"}]
    )
    result = _run(FakeRuntime(experience))
    states = _states(result)
    assert states["onedrive_auth"] == "ok"
    assert states["onedrive_thumb_custom"] == "ok"
    assert states["onedrive_thumb_large"] == "ok"
    assert states["park4night"] == "ok"
    assert states["map_openstreetmap"] == "ok"
    assert states["map_google"] == "skipped", "no key means skipped, not failed"
    assert states["gemini"] == "ok"
    # The probe walks the REAL export path: the high-resolution size first.
    assert experience.sizes[0] == "c1920x1440"
    assert "JPEG" in result["report"], "the report names what actually arrived"


def verify_each_interface_fails_on_its_own() -> None:
    """One broken interface must not hide the state of the others."""

    async def failing_download(session, url):
        return None

    async def fake_place(hass, url):
        return {"read_status": "http_403"}

    async def failing_snapshot(session, provider, key, **kwargs):
        return None

    _patch(
        async_download_photo=failing_download,
        async_fetch_page_place=fake_place,
        async_fetch_snapshot=failing_snapshot,
    )
    onedrive = FakeOneDrive({"configured": True, "connected": True})
    result = _run(
        FakeRuntime(FakeExperience(onedrive, [{"id": "m1", "provider_item_id": "x"}]))
    )
    states = _states(result)
    assert states["onedrive_auth"] == "ok", "the connection itself is fine"
    assert states["onedrive_thumb_custom"] == "fail"
    assert states["park4night"] == "fail"
    assert states["map_openstreetmap"] == "fail"
    assert states["gemini"] == "ok"
    assert result["summary"]["fail"] >= 3
    # A failing probe carries the actionable hint, a passing one does not.
    failed = next(c for c in result["checks"] if c["id"] == "map_openstreetmap")
    assert failed["hint"]
    assert not next(c for c in result["checks"] if c["id"] == "gemini")["hint"]


def verify_a_raising_probe_becomes_a_result_not_a_crash() -> None:
    async def fake_place(hass, url):
        raise RuntimeError("Netzwerk weg")

    async def fake_snapshot(session, provider, key, **kwargs):
        return JPEG

    async def fake_download(session, url):
        return JPEG

    _patch(
        async_download_photo=fake_download,
        async_fetch_page_place=fake_place,
        async_fetch_snapshot=fake_snapshot,
    )
    onedrive = FakeOneDrive({"configured": True, "connected": True})
    experience = FakeExperience(
        onedrive,
        [{"id": "m1", "provider_item_id": "x"}],
        thumbnail_error=RuntimeError("Graph kaputt"),
    )
    result = _run(FakeRuntime(experience))
    states = _states(result)
    assert states["park4night"] == "fail"
    assert states["onedrive_thumb_custom"] == "fail"
    assert "Graph kaputt" in result["report"]
    assert "Netzwerk weg" in result["report"]
    # Everything else still ran.
    assert states["map_openstreetmap"] == "ok"


def verify_unconfigured_interfaces_are_skipped_not_failed() -> None:
    async def fake_place(hass, url):
        return {"read_status": "ok", "latitude": 1.0, "longitude": 2.0}

    async def fake_snapshot(session, provider, key, **kwargs):
        return JPEG

    _patch(async_fetch_page_place=fake_place, async_fetch_snapshot=fake_snapshot)
    onedrive = FakeOneDrive({"configured": False})
    runtime = FakeRuntime(FakeExperience(onedrive, []))
    runtime.assistant = types.SimpleNamespace(configured=False)
    states = _states(_run(runtime))
    assert states["onedrive_auth"] == "skipped"
    assert states["gemini"] == "skipped"
    assert states["onedrive_thumb_custom"] == "skipped"


def verify_wiring_and_read_only_contract() -> None:
    source = (PACKAGE_ROOT / "system_check.py").read_text(encoding="utf-8")
    for forbidden in (".post(", ".put(", ".delete(", ".patch("):
        assert forbidden not in source, f"the check must stay read-only: {forbidden}"
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert '"run_system_check",' in panel_source
    assert 'if action == "run_system_check":' in panel_source
    assert panel_source.count('"run_system_check",') == 2, (
        "the check calls Gemini and must survive a dropped connection like "
        "the other provider actions"
    )
    ui_source = (PACKAGE_ROOT / "frontend/features/trip-day-stop.js").read_text(encoding="utf-8")
    assert 'data-action="run-system-check"' in ui_source
    assert 'data-action="copy-system-check"' in ui_source, (
        "the whole point is pasting the result into a message"
    )
    panel_js = (PACKAGE_ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
    assert 'action === "run-system-check"' in panel_js
    assert "_copyToClipboard(text, successMessage)" in panel_js


def verify_a_failing_map_probe_reports_the_recorded_cause() -> None:
    """Live Systemcheck: "kein Kartenbild erhalten" said nothing usable.

    The map module records WHY it failed. The check has to pass that on,
    and an unused provider must not be shouted about as a failure.
    """
    async def fake_download(session, url):
        return JPEG

    async def fake_place(hass, url):
        return {"read_status": "ok", "latitude": 59.9, "longitude": 15.2}

    async def failing_snapshot(session, provider, key, **kwargs):
        module.LAST_SNAPSHOT_ERROR["reason"] = (
            "Google Static Maps antwortete mit HTTP 403: This API project is "
            "not authorized to use this API."
        )
        return None

    _patch(
        async_download_photo=fake_download,
        async_fetch_page_place=fake_place,
        async_fetch_snapshot=failing_snapshot,
    )
    onedrive = FakeOneDrive({"configured": True, "connected": True, "account_email": "a@b.c"})
    experience = FakeExperience(onedrive, [{"id": "m1", "provider_item_id": "d1"}])

    result = _run(FakeRuntime(experience, google_key="key-1"))
    states = _states(result)
    assert states["map_google"] == "warn", (
        "Google is not the selected provider - that is not a broken system"
    )
    assert "not authorized to use this API" in result["report"], result["report"]

    # With Google actually selected, the same failure IS a failure.
    result = _run(
        FakeRuntime(experience, google_key="key-1", map_provider="google_static_maps")
    )
    assert _states(result)["map_google"] == "fail"


def verify_a_park4night_page_without_gps_says_what_arrived() -> None:
    """A consent wall and the real page both read "ok" - the size tells them apart."""
    async def fake_download(session, url):
        return JPEG

    async def fake_place(hass, url):
        return {"read_status": "ok", "page_bytes": 4096, "name": "Dammtorp"}

    async def fake_snapshot(session, provider, key, **kwargs):
        return JPEG

    _patch(
        async_download_photo=fake_download,
        async_fetch_page_place=fake_place,
        async_fetch_snapshot=fake_snapshot,
    )
    onedrive = FakeOneDrive({"configured": True, "connected": True, "account_email": "a@b.c"})
    result = _run(FakeRuntime(FakeExperience(onedrive, [{"id": "m1", "provider_item_id": "d1"}])))
    assert _states(result)["park4night"] == "warn"
    assert "4 kB gelesen" in result["report"], result["report"]
    assert "Dammtorp" in result["report"]


if __name__ == "__main__":
    verify_a_healthy_system_reports_ok_for_every_reachable_interface()
    verify_each_interface_fails_on_its_own()
    verify_a_raising_probe_becomes_a_result_not_a_crash()
    verify_unconfigured_interfaces_are_skipped_not_failed()
    verify_wiring_and_read_only_contract()
    verify_a_failing_map_probe_reports_the_recorded_cause()
    verify_a_park4night_page_without_gps_says_what_arrived()
    print("System check tests passed.")
