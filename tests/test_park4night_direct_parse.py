"""Behavior tests for the deterministic (AI-free) Park4Night page parser.

Live report: the p4n adoption always failed with "Die Seite konnte nicht
gelesen werden" while the Gemini pay-as-you-go credit was exhausted - the
page read depended entirely on the AI provider. The place page itself
states the GPS position in its HTML, so it is now fetched directly and
parsed deterministically; the AI reader remains only a fallback.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE = "roadplanner_p4n_direct_test"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE] = package

assistant_provider_stub = types.ModuleType(f"{PACKAGE}.assistant_provider")
assistant_provider_stub.AssistantProvider = object
sys.modules[f"{PACKAGE}.assistant_provider"] = assistant_provider_stub

spec = spec_from_file_location(
    f"{PACKAGE}.park4night_lookup", PACKAGE_ROOT / "park4night_lookup.py"
)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

parse = module.parse_park4night_page
URL = "https://park4night.com/lieu/51373/"


def verify_visible_text_coordinates_are_parsed() -> None:
    page = """<title>( Sweden - 893 91 ) Skuleskogen NP, Unnamed Road - park4night</title>
    <div>4.2/5</div>
    <p>63.1353, 18.5161 (lat, lng)<br>N 63&deg; 8' 7" E 18&deg; 30' 58" (lat lng)</p>"""
    facts = parse(page, url=URL)
    assert facts is not None
    assert facts["latitude"] == 63.1353
    assert facts["longitude"] == 18.5161
    assert facts["provider"] == "park4night_page"
    assert facts["name"] == "( Sweden - 893 91 ) Skuleskogen NP, Unnamed Road"
    assert facts["rating_text"] == "4.2/5"


def verify_embedded_json_coordinates_are_parsed() -> None:
    page = '<script>{"place":{"id":51373,"lat":"63.1353","lng":"18.5161","name":"Skuleskogen NP"}}</script>'
    facts = parse(page, url=URL)
    assert facts is not None
    assert facts["latitude"] == 63.1353
    assert facts["longitude"] == 18.5161


def verify_meta_tag_coordinates_are_parsed() -> None:
    page = (
        '<meta property="place:location:latitude" content="63.1353">'
        '<meta property="place:location:longitude" content="18.5161">'
    )
    facts = parse(page, url=URL)
    assert facts is not None
    assert facts["latitude"] == 63.1353


def verify_pages_without_gps_return_none() -> None:
    assert parse("<html><title>park4night</title></html>", url=URL) is None
    assert parse("", url=URL) is None
    assert parse('{"lat":"0","lng":"0"}', url=URL) is None, "0,0 is never a place"
    assert parse('{"lat":"999","lng":"18.5"}', url=URL) is None, "range-checked"


def verify_direct_fetch_precedes_ai_and_falls_back() -> None:
    class FakeResponse:
        status = 200

        def __init__(self, body: bytes) -> None:
            self._body = body
            self.content = types.SimpleNamespace(
                iter_chunked=self._iter_chunked, read=self._read
            )

        async def _iter_chunked(self, _size: int):
            # Chunked exactly like aiohttp: a single read() would only
            # return the first chunk and silently truncate the page.
            for start in range(0, len(self._body), 256):
                yield self._body[start:start + 256]

        async def _read(self, _limit: int = -1) -> bytes:
            return self._body[:256]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeSession:
        def __init__(self, body: bytes) -> None:
            self._body = body
            self.requests: list[str] = []

        def get(self, url: str, **kwargs):
            self.requests.append(url)
            return FakeResponse(self._body)

    class FakeProvider:
        configured = True
        calls: list[str] = []

        async def async_generate_json_result(self, **kwargs):
            self.calls.append("ai")
            raise RuntimeError("AI must not be reached when the page parses")

    session = FakeSession('63.1353, 18.5161 (lat, lng)'.encode())
    service = module.Park4NightLookupService(FakeProvider(), session=session)
    facts = asyncio.run(service.async_lookup(URL))
    assert facts is not None and facts["provider"] == "park4night_page"
    assert session.requests == [URL]
    assert FakeProvider.calls == [], "direct parse succeeded - no AI call"

    # Unparseable page -> the AI fallback IS attempted (and its failure
    # still yields None instead of raising).
    session2 = FakeSession(b"<html>no coordinates here</html>")
    provider2 = FakeProvider()
    provider2.calls = []
    service2 = module.Park4NightLookupService(provider2, session=session2)
    assert asyncio.run(service2.async_lookup(URL)) is None
    assert provider2.calls == ["ai"], "fallback to the AI reader must happen"

    # Without any provider the service is still available thanks to the
    # session - exactly the exhausted-quota scenario from the live report.
    service3 = module.Park4NightLookupService(None, session=session)
    assert service3.available is True
    facts3 = asyncio.run(service3.async_lookup(URL))
    assert facts3 is not None and facts3["latitude"] == 63.1353


if __name__ == "__main__":
    verify_visible_text_coordinates_are_parsed()
    verify_embedded_json_coordinates_are_parsed()
    verify_meta_tag_coordinates_are_parsed()
    verify_pages_without_gps_return_none()
    verify_direct_fetch_precedes_ai_and_falls_back()
    print("Park4Night direct-parse tests passed.")
