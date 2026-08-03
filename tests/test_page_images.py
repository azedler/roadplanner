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


if __name__ == "__main__":
    verify_extraction_shapes_and_filters()
    verify_source_hint_fetch_skips_covered_providers()
    verify_gallery_and_enrichment_wiring()
    print("Shared-page image extraction tests passed.")
