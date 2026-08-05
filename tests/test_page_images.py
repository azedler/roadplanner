"""Tests: photos are taken over from a user-shared place page.

Live request: "Könnte er aus dieser Anweisung nicht auch die Bilder
mitnehmen?" - the user shared a naturkartan.se link for a stop; the
page's own photos should become planning-image candidates. Extraction is
deterministic (og:image/twitter:image, JSON-LD, <img> tags), reference-
only with the page as attribution, and always fails open.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_page_images_test"
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


load("const")
load("destination_intelligence")
module = load("page_images")

PAGE_URL = "https://www.naturkartan.se/sv/dalarnas-lan/lumsenkojan"

HTML = """
<meta property="og:title" content="Lumsenkojan">
<meta property="og:image" content="https://cdn.naturkartan.se/photos/lumsen-1.jpg">
<meta content="https://cdn.naturkartan.se/photos/lumsen-2.jpg" name="twitter:image">
<script type="application/ld+json">
{"@type": "Place", "name": "Lumsenkojan",
 "image": ["https://cdn.naturkartan.se/photos/lumsen-3.webp",
           {"url": "https://cdn.naturkartan.se/photos/lumsen-4.png"}]}
</script>
<img src="/assets/logo-naturkartan.png" alt="Naturkartan">
<img src="https://cdn.naturkartan.se/icons/tent.svg" alt="Zelt">
<img src="/photos/lumsen-5.jpeg" alt="Kojan am See">
<img src="data:image/png;base64,AAAA" alt="inline">
<img src="https://cdn.naturkartan.se/photos/tiny.jpg" width="48" height="48">
<img src="https://cdn.naturkartan.se/photos/lumsen-1.jpg" alt="Duplikat vom og:image">
"""


def verify_extraction_shapes_and_filters() -> None:
    images = module.extract_page_images(HTML, page_url=PAGE_URL)
    urls = [image["image_url"] for image in images]
    assert urls == [
        "https://cdn.naturkartan.se/photos/lumsen-1.jpg",
        "https://cdn.naturkartan.se/photos/lumsen-2.jpg",
        "https://cdn.naturkartan.se/photos/lumsen-3.webp",
        "https://cdn.naturkartan.se/photos/lumsen-4.png",
        "https://www.naturkartan.se/photos/lumsen-5.jpeg",
    ], urls
    first = images[0]
    assert first["provider"] == "shared_link"
    assert first["source_url"] == PAGE_URL
    assert first["attribution"] == "www.naturkartan.se"
    assert first["id"].startswith("shared-link-")
    # Stable id: same URL, same id (no process-randomized hash).
    again = module.extract_page_images(HTML, page_url=PAGE_URL)
    assert again[0]["id"] == first["id"]
    relative = next(i for i in images if i["image_url"].endswith("lumsen-5.jpeg"))
    assert relative["alt"] == "Kojan am See"
    assert module.extract_page_images("", page_url=PAGE_URL) == []
    assert module.extract_page_images(HTML, page_url=PAGE_URL, limit=2) == images[:2]


def verify_source_hint_fetch_skips_covered_providers() -> None:
    fetched: list[str] = []

    async def fake_fetch(hass, url, *, limit):
        fetched.append(url)
        return module.extract_page_images(HTML, page_url=url)[:limit]

    module.async_fetch_page_images = fake_fetch
    hints = [
        {"provider": "google_maps", "url": "https://maps.google.com/?cid=1"},
        {"provider": "wikipedia", "url": "https://de.wikipedia.org/wiki/X"},
        {"provider": "link", "url": PAGE_URL},
        {"provider": "park4night", "url": "https://park4night.com/lieu/110490/"},
        {"provider": "link", "url": "https://example.org/three"},
    ]
    images = asyncio.run(
        module.async_images_from_source_hints(None, hints, limit=4)
    )
    assert fetched[0] == PAGE_URL, "maps/wiki pages are never fetched for photos"
    assert len(fetched) <= 2, "at most two pages are read"
    assert len(images) == 4
    assert all(image["provider"] == "shared_link" for image in images)
    assert asyncio.run(module.async_images_from_source_hints(None, [])) == []


PLACE_HTML = """
<meta property="og:title" content="Rastplats Storbergsudden | Naturkartan">
<script type="application/ld+json">
{"@type": "Place", "name": "Rastplats Storbergsudden",
 "geo": {"@type": "GeoCoordinates", "latitude": 59.924128, "longitude": 15.284795}}
</script>
"""


def verify_page_place_extraction() -> None:
    place = module.extract_page_place(PLACE_HTML)
    assert place == {
        "latitude": 59.924128,
        "longitude": 15.284795,
        "name": "Rastplats Storbergsudden",
    }, place
    # meta geo.position fallback, og:title suffix split.
    meta_only = (
        '<meta name="geo.position" content="59.995313;15.359499">'
        '<meta property="og:title" content="Lumsenkojan | Naturkartan">'
    )
    place = module.extract_page_place(meta_only)
    assert place["latitude"] == 59.995313 and place["longitude"] == 15.359499
    assert place["name"] == "Lumsenkojan"
    assert module.extract_page_place("<p>nichts</p>") == {}


def verify_map_app_coordinates_are_found_and_never_guessed() -> None:
    """A map page that ships its marker only as script data still resolves.

    Live report: a shared naturkartan.se link came back "Die Seite konnte
    nicht gelesen werden oder enthält keine eindeutige GPS-Angabe" although
    the page shows the place on a map. Such pages carry the position in
    their own JSON payload, not in JSON-LD.
    """
    og_place = (
        '<meta property="place:location:latitude" content="59.30721">'
        '<meta property="place:location:longitude" content="15.11544">'
    )
    place = module.extract_page_place(og_place)
    assert (place["latitude"], place["longitude"]) == (59.30721, 15.11544)

    next_data = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"site":{"id":41234,"name":"Dammtorp",'
        '"lat":59.183422,"lng":15.229781}}}</script>'
        '<meta property="og:title" content="Dammtorp | Naturkartan">'
    )
    place = module.extract_page_place(next_data)
    assert (place["latitude"], place["longitude"]) == (59.183422, 15.229781)
    assert place["name"] == "Dammtorp"

    # Reversed key order (GeoJSON-style payloads) is read just as well.
    place = module.extract_page_place(
        '<script>var m = {"longitude": 15.229781, "latitude": 59.183422};</script>'
    )
    assert (place["latitude"], place["longitude"]) == (59.183422, 15.229781)

    # An embedded map link counts too.
    place = module.extract_page_place(
        '<iframe src="https://www.google.com/maps?q=59.183422,15.229781&z=15">'
    )
    assert (place["latitude"], place["longitude"]) == (59.183422, 15.229781)

    # A page listing SEVERAL places must produce nothing: picking one of
    # them would put the stop kilometres away from what the user shared.
    ambiguous = (
        '<script>{"places":['
        '{"lat":59.183422,"lng":15.229781},'
        '{"lat":58.771003,"lng":14.998112}]}</script>'
    )
    assert "latitude" not in module.extract_page_place(ambiguous)
    # Nearly identical readings of the SAME place still resolve.
    agreeing = (
        '<script>{"lat":59.183422,"lng":15.229781}</script>'
        '<script>{"lat":59.183430,"lng":15.229790}</script>'
    )
    assert module.extract_page_place(agreeing)["latitude"] == 59.183422
    # Version numbers and prices never look like coordinates.
    assert "latitude" not in module.extract_page_place(
        '<script>{"version":"4.15.2","price":12.5}</script>'
    )


def verify_shared_page_resolver_contract() -> None:
    async def fake_fetch_place(hass, url):
        assert "naturkartan" in url
        return {"latitude": 59.924128, "longitude": 15.284795, "name": "Rastplats Storbergsudden"}

    module.async_fetch_page_place = fake_fetch_place
    info = asyncio.run(
        module.async_resolve_shared_page_place(
            None,
            "Nimm den hier: https://www.naturkartan.se/de/orebro-lan/rastplats-storbergsudden",
        )
    )
    assert info["place_query"] == "59.924128,15.284795"
    assert info["name"] == "Rastplats Storbergsudden"
    # Google links are NOT handled here - the maps resolver owns them.
    assert asyncio.run(
        module.async_resolve_shared_page_place(None, "https://maps.app.goo.gl/xyz")
    ) is None
    # A shared-page pin counts as a user-confirmed point like a maps pin.
    assistant_source = (PACKAGE_ROOT / "assistant.py").read_text(encoding="utf-8")
    assert "async_resolve_shared_page_place" in assistant_source
    assert '"user_shared_link"' in assistant_source
    plugins_source = (PACKAGE_ROOT / "assistant_plugins.py").read_text(encoding="utf-8")
    assert 'origin in ("user_google_maps_link", "user_shared_link")' in plugins_source
    # Manual link lookup: deterministic page metadata beats the AI reader.
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "page_place = await async_fetch_page_place(hass, url)" in panel_source
    assert panel_source.index("async_fetch_page_place(hass, url)") < panel_source.index(
        "async_lookup_page(url, hint_name=hint)"
    )
    # The stop card offers the user-shared links for lookup.
    stop_card_source = (PACKAGE_ROOT / "frontend/features/trip-day-stop.js").read_text(encoding="utf-8")
    assert "_stopSharedLinks" in stop_card_source


def verify_failure_reason_is_named_and_a_readable_name_survives() -> None:
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    # A page whose name was readable must prefill the form instead of
    # leaving the user with a red banner and empty fields.
    assert '"name": page_name,' in panel_source
    assert "_place_link_failure_message(" in panel_source
    assert (
        "Die Seite konnte nicht gelesen werden oder enthält keine "
        "eindeutige GPS-Angabe" not in panel_source
    ), "the blanket message that named neither cause is gone"

    # panel.py cannot be imported without Home Assistant - assert on the
    # branches instead, one per read_status the fetch can report.
    for expected in (
        "nennt aber keine eindeutige GPS-Position",
        "abgewiesen",
        "nicht rechtzeitig",
        "nicht erreichbar",
    ):
        assert expected in panel_source, expected


def verify_every_lookup_reports_what_it_did() -> None:
    """A lookup that "runs without an error but does nothing" must explain itself.

    Live report: after the failure message was fixed the lookup came back
    silent - no error, no filled field, and no way to tell from the phone
    whether the page was unreachable, empty, or simply position-less.
    """
    panel_source = (PACKAGE_ROOT / "panel.py").read_text(encoding="utf-8")
    assert "def _place_link_diagnosis(" in panel_source
    # Every exit of the lookup carries it: hit, AI hit, name-only, failure.
    assert panel_source.count('"diagnosis": ') == 3
    assert '"diagnosis": diagnosis}}' in panel_source
    assert 'f" [{diagnosis}]"' in panel_source, (
        "even the hard failure names what was actually read"
    )
    for expected in ("GPS auf der Seite: ", "KI-Leser: ", "kB"):
        assert expected in panel_source, expected
    pitches_source = (PACKAGE_ROOT / "frontend/features/pitches.js").read_text(encoding="utf-8")
    assert "data-link-diagnosis" in pitches_source
    assert "Leseergebnis - " in pitches_source, (
        "the account stays in the form, not only in a toast that fades"
    )
    assert "nichts Übernehmbares" in pitches_source, (
        "a lookup that filled nothing must not claim it prefilled fields"
    )


def verify_shared_pages_are_requested_like_a_link_preview() -> None:
    headers = module._page_request_headers()
    # A bare product token was rejected outright by protective front-ends.
    assert headers["User-Agent"].startswith("Mozilla/5.0 (compatible;")
    assert "Roadplanner" in headers["User-Agent"], (
        "the integration must stay identifiable to the site operator"
    )
    source = (PACKAGE_ROOT / "page_images.py").read_text(encoding="utf-8")
    assert source.count("headers=_page_request_headers()") == 2, (
        "photo and place reads must send the same headers"
    )


def verify_gallery_and_enrichment_wiring() -> None:
    gallery_source = (PACKAGE_ROOT / "destination_gallery_manager.py").read_text(encoding="utf-8")
    assert "async_images_from_source_hints" in gallery_source
    assert gallery_source.index("shared_images = await async_images_from_source_hints") < gallery_source.index(
        "result = await self.image_provider.async_search"
    ), "shared-page photos are collected before (and ranked ahead of) provider search"
    enrichment_source = (PACKAGE_ROOT / "place_enrichment.py").read_text(encoding="utf-8")
    assert "async_images_from_source_hints" in enrichment_source
    media_source = (PACKAGE_ROOT / "frontend/features/media.js").read_text(encoding="utf-8")
    assert 'shared_link: "Geteilter Link"' in media_source


def verify_a_page_without_open_graph_still_yields_its_name() -> None:
    """Live Systemcheck: 67 kB read from Park4Night, "ohne Seitentitel".

    A page can carry no Open Graph tags at all and still be a perfectly
    normal document. Falling back to plain <title> tells a real page apart
    from a JavaScript shell, which is the whole point of the report line.
    """
    place = module.extract_page_place(
        "<html><head><title>Dammtorp naturnära | Park4Night</title></head>"
        "<body>kein og:title weit und breit</body></html>"
    )
    assert place.get("name") == "Dammtorp naturnära", place
    # og:title still wins where it exists.
    place = module.extract_page_place(
        '<html><head><title>Falscher Titel</title>'
        '<meta property="og:title" content="Echter Ort"></head></html>'
    )
    assert place.get("name") == "Echter Ort", place
    # A shell with no title at all still reports nothing rather than junk.
    assert "name" not in module.extract_page_place("<html><body><div id=app></div></body></html>")


if __name__ == "__main__":
    verify_extraction_shapes_and_filters()
    verify_page_place_extraction()
    verify_map_app_coordinates_are_found_and_never_guessed()
    verify_shared_page_resolver_contract()
    verify_failure_reason_is_named_and_a_readable_name_survives()
    verify_every_lookup_reports_what_it_did()
    verify_shared_pages_are_requested_like_a_link_preview()
    verify_source_hint_fetch_skips_covered_providers()
    verify_gallery_and_enrichment_wiring()
    verify_a_page_without_open_graph_still_yields_its_name()
    print("Shared-page image extraction tests passed.")
