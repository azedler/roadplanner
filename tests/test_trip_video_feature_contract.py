"""Source-level wiring contract for the trip video export feature.

Regression guard: every piece needed for "export_trip_video" to actually
work end-to-end must stay connected - the panel action, its provider-call
shielding (Gemini + a long ffmpeg encode both need to survive a dropped
mobile connection), the HTTP download view registration, the runtime wiring
in __init__.py, the map-snapshot-provider config option, and both CI
workflows installing ffmpeg (the same category of gap this project already
hit and fixed once for reportlab not being installed - must not repeat it
for this new binary dependency).

Pillow is deliberately NOT declared as its own manifest.json requirement -
see the "no separate Pillow pin" test below for why.
"""
import json
from pathlib import Path

ROOT = Path("custom_components/roadplanner_mcp")

panel_source = (ROOT / "panel.py").read_text(encoding="utf-8")
assert '"export_trip_video"' in panel_source
assert 'if action == "export_trip_video":' in panel_source
assert "runtime.trip_video.async_generate(trip_id, style=style)" in panel_source
assert "runtime.trip_video.async_create_ticket(video_bytes, user_id=user_id)" in panel_source
assert '"export_trip_video",' in panel_source, (
    "export_trip_video must be in _PROVIDER_CALL_ACTIONS - it calls Gemini "
    "and runs a long ffmpeg encode, both of which must survive a dropped "
    "mobile connection"
)

init_source = (ROOT / "__init__.py").read_text(encoding="utf-8")
assert "from .trip_video_export import TripVideoExporter" in init_source
assert "from .trip_video_http import async_register_trip_video_view" in init_source
assert "trip_video: TripVideoExporter" in init_source
assert "trip_video = TripVideoExporter(" in init_source
assert "trip_video=trip_video," in init_source
assert "async_register_trip_video_view(hass)" in init_source
assert "CONF_MAP_SNAPSHOT_PROVIDER" in init_source

http_source = (ROOT / "trip_video_http.py").read_text(encoding="utf-8")
assert 'DOWNLOAD_URL = "/api/roadplanner/trip_video/{token}"' in http_source
assert "requires_auth = False" in http_source
assert "runtime.trip_video.async_resolve_ticket(token)" in http_source

const_source = (ROOT / "const.py").read_text(encoding="utf-8")
assert 'CONF_MAP_SNAPSHOT_PROVIDER = "map_snapshot_provider"' in const_source
assert 'MAP_SNAPSHOT_PROVIDERS = ("openstreetmap", "google_static_maps")' in const_source

config_flow_source = (ROOT / "config_flow.py").read_text(encoding="utf-8")
assert "CONF_MAP_SNAPSHOT_PROVIDER" in config_flow_source
assert "vol.In(MAP_SNAPSHOT_PROVIDERS)" in config_flow_source

manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
requirement_names = {
    requirement.split(">=")[0].split("==")[0] for requirement in manifest.get("requirements", [])
}
assert "reportlab" in requirement_names
assert "Pillow" not in requirement_names, (
    "a separate, narrowly-pinned Pillow requirement previously broke setup "
    "entirely on hosts whose Home Assistant Core install already pins an "
    "incompatible Pillow version outside our range - reportlab's own "
    "dependency chain already guarantees a usable Pillow is installed, so "
    "map_snapshot.py/trip_video.py must keep relying on that instead of "
    "redeclaring their own version constraint"
)

for workflow in ("release.yml", "roadplanner-validation.yml"):
    workflow_source = (Path(".github/workflows") / workflow).read_text(encoding="utf-8")
    assert "install -y --no-install-recommends ffmpeg" in workflow_source, (
        f"{workflow} must install the ffmpeg binary before running tests - "
        "test_ffmpeg_runner.py and test_trip_video_export.py genuinely "
        "invoke it, and it cannot be declared as a manifest.json requirement "
        "(system binary, not a pip package)"
    )

frontend_source = (ROOT / "frontend/features/route-map.js").read_text(encoding="utf-8")
assert 'data-action="export-trip-video"' in frontend_source
assert 'data-action="select-video-style"' in frontend_source

panel_js_source = (ROOT / "frontend/roadplanner-panel.js").read_text(encoding="utf-8")
assert 'action === "export-trip-video"' in panel_js_source
assert "this._exportTripVideo()" in panel_js_source
assert 'select.dataset.action === "select-video-style"' in panel_js_source

music_readme = (ROOT / "assets/music/README.md").read_text(encoding="utf-8")
assert "royalty-free" in music_readme.casefold() or "cc0" in music_readme.casefold()

print("Trip video feature wiring contract tests passed.")
