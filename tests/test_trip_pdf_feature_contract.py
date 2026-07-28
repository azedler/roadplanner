"""Source-level wiring contract for the trip-summary PDF export feature.

Regression guard: every piece needed for "export_trip_pdf" to actually work
end-to-end must stay connected - the panel action, its permission allow-list
entry, the HTTP download view registration, the runtime wiring in
__init__.py, and the declared reportlab dependency in manifest.json.
"""
import json
from pathlib import Path

ROOT = Path("custom_components/roadplanner_mcp")

panel_source = (ROOT / "panel.py").read_text(encoding="utf-8")
assert '"export_trip_pdf"' in panel_source
assert 'if action == "export_trip_pdf":' in panel_source
assert "runtime.trip_pdf.async_generate(trip_id)" in panel_source
assert "runtime.trip_pdf.async_create_ticket(pdf_bytes, user_id=user_id)" in panel_source

init_source = (ROOT / "__init__.py").read_text(encoding="utf-8")
assert "from .trip_pdf_export import TripPdfExporter" in init_source
assert "from .trip_pdf_http import async_register_trip_pdf_view" in init_source
assert "trip_pdf: TripPdfExporter" in init_source
assert "trip_pdf = TripPdfExporter(hass, manager, experience)" in init_source
assert "trip_pdf=trip_pdf," in init_source
assert "async_register_trip_pdf_view(hass)" in init_source

http_source = (ROOT / "trip_pdf_http.py").read_text(encoding="utf-8")
assert 'DOWNLOAD_URL = "/api/roadplanner/trip_pdf/{token}"' in http_source
assert "requires_auth = False" in http_source
assert "runtime.trip_pdf.async_resolve_ticket(token)" in http_source

manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
assert any(
    requirement.split(">=")[0].split("==")[0] == "reportlab"
    for requirement in manifest.get("requirements", [])
)

frontend_source = (ROOT / "frontend/features/route-map.js").read_text(encoding="utf-8")
assert 'data-action="export-trip-pdf"' in frontend_source

panel_js_source = (ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
assert 'action === "export-trip-pdf"' in panel_js_source
assert "this._exportTripPdf()" in panel_js_source

print("Trip PDF feature wiring contract tests passed.")
