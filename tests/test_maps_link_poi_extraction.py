"""Regression test: shared POI Maps links resolve to the precise marker.

Live report: a shared POI link (maps.app.goo.gl, "?g_st=ic") was
completely ignored - the stop was created without any GPS/address. The
canonical URLs of such shares often carry NO @lat,lng segment and NO
/maps/place/<name>; the precise marker position sits in the data blob as
!3d<lat>!4d<lon>, or the link resolves to a q=/query= parameter form.
The extractor now understands all of these, prefers the precise marker
over the viewport center, and validates coordinate ranges.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_maps_poi_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
sys.modules.setdefault("aiohttp", aiohttp_stub)
ha = types.ModuleType("homeassistant")
ha.__path__ = []
sys.modules.setdefault("homeassistant", ha)
ha_core = types.ModuleType("homeassistant.core")
ha_core.HomeAssistant = object
sys.modules.setdefault("homeassistant.core", ha_core)
ha_helpers = types.ModuleType("homeassistant.helpers")
ha_helpers.__path__ = []
sys.modules.setdefault("homeassistant.helpers", ha_helpers)
ha_aiohttp = types.ModuleType("homeassistant.helpers.aiohttp_client")
ha_aiohttp.async_get_clientsession = lambda *a, **k: None
sys.modules.setdefault("homeassistant.helpers.aiohttp_client", ha_aiohttp)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


load("destination_intelligence")
module = load("google_maps_link")
extract = module._extract_place_query_from_url


def verify_marker_position_beats_viewport_center() -> None:
    url = (
        "https://www.google.com/maps/place/ICA+Supermarket/"
        "@60.1000000,15.9000000,12z/"
        "data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d60.0912345!4d15.8712345!16s"
    )
    assert extract(url) == "60.0912345,15.8712345", (
        "the !3d/!4d marker is the place - the @center is only the viewport"
    )


def verify_data_blob_without_place_name_or_at_segment() -> None:
    url = "https://www.google.com/maps/place//data=!4m2!3m1!8m2!3d60.0912345!4d15.8712345"
    assert extract(url) == "60.0912345,15.8712345"


def verify_query_parameter_forms() -> None:
    assert extract("https://maps.google.com/?q=60.09,15.87") == "60.09,15.87"
    assert extract(
        "https://www.google.com/maps/search/?api=1&query=ICA+Supermarket+J%C3%A4draas"
    ) == "ICA Supermarket Jädraas"
    assert extract("https://maps.google.com/?q=ICA+Supermarket") == "ICA Supermarket"


def verify_search_path_names_work() -> None:
    assert extract("https://www.google.com/maps/search/Apotheke+Lindesberg") == "Apotheke Lindesberg"


def verify_existing_shapes_still_work() -> None:
    assert extract("https://www.google.com/maps/@60.5,15.5,12z") == "60.5,15.5"
    assert extract("https://www.google.com/maps/place/Skuleskogen/") == "Skuleskogen"


def verify_garbage_stays_none() -> None:
    assert extract("https://www.google.com/maps") is None
    assert extract("https://maps.google.com/?cid=1234567890") is None, (
        "a cid-only link carries no readable position - fail open"
    )
    assert extract("https://maps.google.com/?q=0.0,0.0") is None, "0,0 is never a place"


def verify_link_preview_metadata_fallback() -> None:
    preview = module._extract_place_query_from_preview_html
    # The og:image static map encodes the place position - and the rich
    # preview keeps position AND name together.
    html = (
        '<meta property="og:title" content="ICA Supermarket Jädraås · Google Maps">'
        '<meta property="og:image" content="https://maps.google.com/maps/api/staticmap'
        '?center=60.8412345%2C16.5812345&zoom=15&markers=60.8412345%2C16.5812345">'
    )
    assert preview(html) == "60.8412345,16.5812345"
    assert module._extract_place_preview(html) == {
        "latitude": 60.8412345,
        "longitude": 16.5812345,
        "name": "ICA Supermarket Jädraås",
    }
    # Without a static map the cleaned og:title is used as a text query.
    assert preview(
        '<meta content="Apotheket Hjärtat - Google Maps" property="og:title">'
    ) == "Apotheket Hjärtat"
    # A consent page or generic title yields nothing - fail open.
    assert preview('<meta property="og:title" content="Google Maps">') is None
    assert preview("") is None


def verify_url_info_carries_name_and_coordinates_together() -> None:
    info = module._extract_place_info_from_url(
        "https://www.google.com/maps/place/Ravintola+Kappeli/"
        "@60.1670000,24.9500000,17z/data=!3m1!4b1!8m2!3d60.1673456!4d24.9512345"
    )
    assert info == {
        "latitude": "60.1673456",
        "longitude": "24.9512345",
        "name": "Ravintola Kappeli",
    }, info
    assert module._extract_place_info_from_url("https://www.google.com/maps") == {}


def verify_rich_resolver_fills_missing_name_from_preview() -> None:
    # Live report: "wir haben hier gegessen" + shared restaurant link - the
    # marker gave coordinates, but the POI NAME was lost and the stop was
    # created only as generic "Essen". The resolver now fetches the
    # link-preview metadata whenever the URL lacks name OR coordinates.
    import asyncio

    canonical = (
        "https://www.google.com/maps/place//data=!8m2!3d61.5012345!4d23.7612345"
    )

    async def fake_redirects(hass, url):
        return canonical

    async def fake_preview(hass, url):
        assert url == canonical
        return {"name": "Ravintola Näsinneula"}

    module._async_follow_redirects = fake_redirects
    module._async_link_preview = fake_preview
    info = asyncio.run(
        module.async_resolve_google_maps_place(
            None, "wir haben hier gegessen https://maps.app.goo.gl/zHxYqvPmQk"
        )
    )
    assert info["place_query"] == "61.5012345,23.7612345"
    assert info["name"] == "Ravintola Näsinneula"
    assert info["latitude"] == 61.5012345 and info["longitude"] == 23.7612345

    # The string wrapper (webhook path) still yields just the place_query.
    query = asyncio.run(
        module.async_resolve_google_maps_place_query(
            None, "https://maps.app.goo.gl/zHxYqvPmQk"
        )
    )
    assert query == "61.5012345,23.7612345"


def verify_poi_name_adoption_is_wired() -> None:
    # assistant.py: a NEW stop from a user-shared link adopts the POI name;
    # the model's own label survives in the notes. Updates never rename.
    source = Path("custom_components/roadplanner_mcp/assistant.py").read_text(encoding="utf-8")
    assert "async_resolve_google_maps_place(" in source
    assert 'sanitized.get("action") == "add"' in source
    assert 'sanitized.get("entity_type") == "stop"' in source
    assert 'changes["name"] = resolved_poi_name[:500]' in source
    # panel.py: the manual link lookup returns name AND coordinates together
    # so the Stellplatz/stop forms are prefilled completely.
    panel_source = Path("custom_components/roadplanner_mcp/panel.py").read_text(encoding="utf-8")
    assert "await async_resolve_google_maps_place(hass, url)" in panel_source
    assert '"name": str(resolved.get("name") or "")[:200]' in panel_source


if __name__ == "__main__":
    verify_link_preview_metadata_fallback()
    verify_url_info_carries_name_and_coordinates_together()
    verify_rich_resolver_fills_missing_name_from_preview()
    verify_poi_name_adoption_is_wired()
    verify_marker_position_beats_viewport_center()
    verify_data_blob_without_place_name_or_at_segment()
    verify_query_parameter_forms()
    verify_search_path_names_work()
    verify_existing_shapes_still_work()
    verify_garbage_stays_none()
    print("Maps POI link extraction tests passed.")
