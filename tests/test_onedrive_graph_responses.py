"""A Graph answer that is not JSON must not sink a photo.

Live report: every export came back "keines davon ließ sich als Bild
öffnen ... OneDrive-Link für Foto <id> (thumbnail/c1920x1440) nicht
auflösbar: OneDrive hat eine unlesbare Antwort geliefert". The client read
every response with ``response.json()`` while redirects were switched off -
a redirect has an empty body, so the perfectly good answer "the image is
over there" turned into an unreadable-response error, and the whole photo
was lost even though the plain "large" thumbnail was available all along.
"""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")
PACKAGE_NAME = "roadplanner_onedrive_test"
package = types.ModuleType(PACKAGE_NAME)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PACKAGE_NAME] = package


def _stub(name: str, **attributes) -> None:
    module = types.ModuleType(name)
    module.__path__ = []
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


_stub("aiohttp", ClientError=type("ClientError", (Exception,), {}), ClientSession=object)
_stub("homeassistant")
_stub("homeassistant.helpers")
_stub("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda *a, **k: None)
_stub("homeassistant.helpers.storage", Store=object)


def load(name: str):
    spec = spec_from_file_location(f"{PACKAGE_NAME}.{name}", PACKAGE_ROOT / f"{name}.py")
    assert spec and spec.loader
    loaded = module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


module = load("onedrive_media")
ValidationError = sys.modules[f"{PACKAGE_NAME}.roadplanner"].ValidationError


class FakeResponse:
    def __init__(self, status: int, body: str, location: str = "") -> None:
        self.status = status
        self._body = body
        self.headers = {"Location": location} if location else {}

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.requested.append(url)
        for suffix, response in self.responses.items():
            if url.endswith(suffix):
                return response
        return FakeResponse(404, '{"error": {"message": "nicht gefunden"}}')


def _client(session: FakeSession):
    client = object.__new__(module.OneDrivePersonalClient)
    client._session = session
    client._data = {"expires_at_epoch": 9_999_999_999, "access_token": "token"}
    client._last_error = None

    async def _token() -> str:
        return "token"

    client._ensure_access_token = _token
    return client


CUSTOM = "/thumbnails/0/c1920x1440"
STANDARD = "/thumbnails"
IMAGE = "https://cdn.example/rendered.jpg"


def verify_a_redirect_is_the_thumbnail_url_not_an_error() -> None:
    session = FakeSession({CUSTOM: FakeResponse(302, "", location=IMAGE)})
    client = _client(session)
    assert (
        asyncio.run(client.async_thumbnail_url("item-1", "c1920x1440")) == IMAGE
    ), "an empty-bodied redirect IS the answer, not an unreadable response"


def verify_json_shaped_answers_still_work() -> None:
    session = FakeSession(
        {CUSTOM: FakeResponse(200, '{"url": "https://cdn.example/json.jpg"}')}
    )
    assert (
        asyncio.run(_client(session).async_thumbnail_url("item-1", "c1920x1440"))
        == "https://cdn.example/json.jpg"
    )


def verify_a_failing_custom_size_falls_back_to_large() -> None:
    # The nicer size is a bonus; losing the photo over it is the worse
    # trade, because "large" is a perfectly usable JPEG.
    for failure in (
        FakeResponse(404, '{"error": {"message": "Größe nicht verfügbar"}}'),
        FakeResponse(200, "<html>kein JSON</html>"),
        FakeResponse(302, "", location="http://unsicher.example/x.jpg"),
    ):
        session = FakeSession(
            {
                CUSTOM: failure,
                STANDARD: FakeResponse(
                    200, '{"value": [{"large": {"url": "https://cdn.example/large.jpg"}}]}'
                ),
            }
        )
        assert (
            asyncio.run(_client(session).async_thumbnail_url("item-1", "c1920x1440"))
            == "https://cdn.example/large.jpg"
        ), failure.status


def verify_a_non_json_body_names_what_arrived() -> None:
    session = FakeSession({"/me/drive": FakeResponse(200, "<html>Wartung</html>")})
    try:
        asyncio.run(_client(session)._graph_json("/me/drive"))
    except module.OneDriveError as err:
        assert "statt JSON" in str(err), err
        assert "Wartung" in str(err), "the actual body is quoted, not hidden"
    else:
        raise AssertionError("a non-JSON 200 must still be an error")


if __name__ == "__main__":
    verify_a_redirect_is_the_thumbnail_url_not_an_error()
    verify_json_shaped_answers_still_work()
    verify_a_failing_custom_size_falls_back_to_large()
    verify_a_non_json_body_names_what_arrived()
    print("OneDrive Graph response tests passed.")
