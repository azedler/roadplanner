"""A trip PDF stays retrievable after its download ticket expires.

Live question: "Sollten wir für das pdf auch einen alten Abruf
ermöglichen?" - the video export already offers "Letztes Video", while a
PDF was reachable only through a five-minute ticket. Anything built earlier
in the day had to be generated from scratch.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_trip_pdf_library_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def _stub(name: str, **attributes) -> None:
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


_stub("homeassistant")
_stub("homeassistant.core", HomeAssistant=object, callback=lambda func: func)
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda *a, **k: None)
_stub("homeassistant.util")
_stub("homeassistant.util.dt", now=None, utcnow=None)
_stub(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientTimeout=lambda **kwargs: kwargs,
)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("trip_pdf_export")


class FakeHass:
    async def async_add_executor_job(self, func, *args):
        return func(*args)


def _exporter(library_dir: Path | None):
    return module.TripPdfExporter(
        FakeHass(), None, None, library_dir=library_dir
    )


def verify_a_stored_pdf_is_retrievable_and_the_oldest_are_pruned() -> None:
    with tempfile.TemporaryDirectory() as raw:
        library = Path(raw) / "exports"
        exporter = _exporter(library)
        assert asyncio.run(exporter.async_status())["last_pdf"] is None, (
            "no PDF built yet means nothing to offer"
        )
        url = asyncio.run(exporter.async_store_in_library(b"%PDF-1.4 erstes"))
        assert url.startswith("/api/roadplanner/trip_pdf_library/")
        filename = url.rsplit("/", 1)[-1]
        assert module.PDF_FILENAME_RE.match(filename), filename
        assert (library / filename).read_bytes() == b"%PDF-1.4 erstes"

        status = asyncio.run(exporter.async_status())["last_pdf"]
        assert status["url"] == url
        assert status["size_mb"] == 0.0 and status["created_at"]

        # More exports than the retention count: the oldest go, the newest
        # is what "Letztes PDF" offers.
        for index in range(module.MAX_STORED_TRIP_PDFS + 3):
            newest = asyncio.run(
                exporter.async_store_in_library(f"%PDF-{index}".encode())
            )
        stored = list(library.glob("*.pdf"))
        assert len(stored) == module.MAX_STORED_TRIP_PDFS, stored
        assert asyncio.run(exporter.async_status())["last_pdf"]["url"] == newest


def verify_a_missing_library_never_breaks_the_export() -> None:
    exporter = _exporter(None)
    assert asyncio.run(exporter.async_store_in_library(b"%PDF")) == ""
    assert asyncio.run(exporter.async_status())["last_pdf"] is None


def verify_wiring() -> None:
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert '"trip_pdf_status",' in panel_source
    assert 'if action == "trip_pdf_status":' in panel_source
    assert "async_store_in_library(pdf_bytes)" in panel_source
    assert '"download_url": f"/api/roadplanner/trip_pdf/{token}"' in panel_source, (
        "the immediate download keeps working through its ticket as before"
    )
    init_source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "async_register_trip_pdf_library_view(hass)" in init_source
    assert "library_dir=resolve_config_path(config_dir, trip_video_library_relative)" in init_source
    view_source = (PACKAGE_ROOT / "trip_pdf_library_http.py").read_text(encoding="utf-8")
    assert "requires_auth = False" in view_source, (
        "the companion app downloads via a plain link - session auth would "
        "save the 401 body as the PDF"
    )
    assert "PDF_FILENAME_RE.match(filename)" in view_source, (
        "the unguessable filename IS the capability and must be validated"
    )
    route_map_source = (PACKAGE_ROOT / "frontend/features/route-map.js").read_text(encoding="utf-8")
    assert 'data-action="open-last-trip-pdf"' in route_map_source
    assert "_fetchTripPdfStatus(" in route_map_source
    panel_js = (PACKAGE_ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
    assert 'action === "open-last-trip-pdf"' in panel_js


if __name__ == "__main__":
    verify_a_stored_pdf_is_retrievable_and_the_oldest_are_pruned()
    verify_a_missing_library_never_breaks_the_export()
    verify_wiring()
    print("Trip PDF library tests passed.")
