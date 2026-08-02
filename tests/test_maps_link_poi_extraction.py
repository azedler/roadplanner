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


if __name__ == "__main__":
    verify_marker_position_beats_viewport_center()
    verify_data_blob_without_place_name_or_at_segment()
    verify_query_parameter_forms()
    verify_search_path_names_work()
    verify_existing_shapes_still_work()
    verify_garbage_stays_none()
    print("Maps POI link extraction tests passed.")
