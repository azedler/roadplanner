"""Source-level contract for the cross-trip crew (people/vehicle) feature."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "roadplanner_mcp"
STORE = (ROOT / "crew_store.py").read_text(encoding="utf-8")
MANAGER = (ROOT / "crew_manager.py").read_text(encoding="utf-8")
INIT = (ROOT / "__init__.py").read_text(encoding="utf-8")
PANEL = (ROOT / "panel.py").read_text(encoding="utf-8")
PANEL_JS = (ROOT / "frontend" / "roadplanner-panel.js").read_text(encoding="utf-8") + (
    ROOT / "frontend" / "features" / "crew.js"
).read_text(encoding="utf-8")

# Retiring a person/vehicle must never delete it - only the active flag flips,
# so historical trips that already embedded a snapshot stay resolvable.
assert "def set_person_active" in STORE
assert "def set_vehicle_active" in STORE
assert '"active": bool(value.get("active", True))' in STORE

ACTIONS = [
    "crew_person_add",
    "crew_person_update",
    "crew_person_retire",
    "crew_person_reactivate",
    "crew_vehicle_add",
    "crew_vehicle_update",
    "crew_vehicle_retire",
    "crew_vehicle_reactivate",
]
for action in ACTIONS:
    assert f'"{action}"' in PANEL, f"panel.py is missing action wiring for {action}"

assert "crew: CrewManager" in INIT
assert "CrewStore(archive_root" in INIT
assert "await crew.async_initialize()" in INIT

assert "async def async_panel_payload" in MANAGER
assert '"crew": crew_state' in PANEL

assert "crewMixin" in PANEL_JS or "crew.js" in PANEL_JS
assert "crew_person_add" in PANEL_JS
assert "crew_vehicle_add" in PANEL_JS


def verify_an_image_view_can_be_loaded_by_an_image_tag() -> None:
    """A browser does not attach a bearer token to <img src>.

    The crew portrait view required session authentication on exactly
    that premise, and so answered 401 to every picture. The portraits
    stayed blank - and Home Assistant counts a 401 from any endpoint as a
    failed login, so a crew page with four people banned the household
    from its own Home Assistant after a few visits.

    Every view here whose bytes are fetched by the browser as a plain URL
    has to be reachable without a header the browser will never send.
    """
    for name in (
        "crew_portrait_http.py",
        "experience_http.py",
        "google_photo_http.py",
        "trip_pdf_http.py",
        "trip_pdf_library_http.py",
        "trip_video_library_http.py",
        "frontend_static_http.py",
    ):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "requires_auth = False" in source, (
            f"{name}: ein <img> oder ein Downloadlink sendet kein Token - "
            "diese Ansicht wuerde 401 antworten und IP-Sperren ausloesen"
        )
        # The capability has to be something, and it is the unguessable
        # path. A view that dropped auth without saying why would be the
        # actually dangerous version of this fix.
        named = any(
            word in source
            for word in (
                "Dateiname",
                "filename",
                "capability",
                "HMAC",
                "uuid4",
                "unguessable",
                "erratbar",
                "public-ish",
            )
        )
        assert named, (
            f"{name}: was an die Stelle der Authentifizierung tritt, muss "
            "benannt sein - eine Ansicht, die sie kommentarlos ablegt, waere "
            "die wirklich gefaehrliche Fassung dieses Fixes"
        )


verify_an_image_view_can_be_loaded_by_an_image_tag()

print("Crew management contract tests passed.")
