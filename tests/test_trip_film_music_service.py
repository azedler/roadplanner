"""The music service, where the money is actually spent.

The plan module is arithmetic and easy to be sure about. This is the
part that can be wrong in expensive ways: an offer that quotes a price
the generate call does not honour, a cache key that never matches so a
second render pays a second time, or a film whose length wobbles by two
seconds and buys a whole new soundtrack.

So this drives the real service against a fake session that counts how
often it was asked to generate. Nothing reaches a network, and the
counter is the assertion that matters.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


def _module(name: str, **attributes) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)
    return module


_module("homeassistant").__path__ = []
_module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
_module("homeassistant.exceptions", HomeAssistantError=type("E", (Exception,), {}))
_module("homeassistant.helpers").__path__ = []
_module("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_module("homeassistant.helpers.storage", Store=object)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
)

_PACKAGE = "roadplanner_music_service_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.trip_film_music_service")

MANIFEST = {
    "trip": {"title": "Ostsee-Runde 2026"},
    "narrative": {
        "arc": "23 Tage und eine große Runde um die Ostsee",
        "opening": "Seen und Lagerfeuer",
        "closing": "Tierische Begegnungen",
    },
}


class _Hass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _StoryContext:
    async def async_manifest(self, trip_id):
        assert trip_id, "die Reise-ID muss ankommen"
        return MANIFEST


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return ""


class _Session:
    """Counts every request that would have cost eight cents."""

    def __init__(self):
        self.calls = 0
        self.prompts: list[str] = []

    def post(self, url, *, json=None, headers=None, timeout=None):
        self.calls += 1
        self.prompts.append(str(json))
        blob = base64.b64encode(b"ID3fake-audio-bytes").decode("ascii")
        return _Response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"inlineData": {"mimeType": "audio/mpeg", "data": blob}}
                            ]
                        }
                    }
                ]
            }
        )


def _service(root, session, *, api_key="k"):
    return service_module.TripFilmMusicService(
        _Hass(),
        _StoryContext(),
        lambda: session,
        api_key_provider=lambda: api_key,
        music_root=root,
    )


FILM = 7 * 60 + 12.0


def verify_the_offer_costs_nothing_and_orders_nothing() -> None:
    """The only thing the panel may do before somebody agrees to a price."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        offer = asyncio.run(_service(root, session).async_offer("t1", film_seconds=FILM))
        assert session.calls == 0, "ein Angebot darf nichts erzeugen"
        assert offer["sections"] == 4, offer
        assert offer["new_generations"] == 4
        assert abs(offer["estimated_cost"] - 0.32) < 0.001, offer
        assert offer["available"] is True
        assert not any(Path(root).iterdir()), "ein Angebot schreibt nichts"


def verify_generating_pays_once_and_a_second_render_is_free() -> None:
    """The whole reason the cache is the music folder."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        first = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        assert first["generated"] == 4, first
        assert session.calls == 4
        assert len(first["timeline"]) == 4
        assert [entry["name"] for entry in first["timeline"]] == [
            entry["cached_name"] for entry in first["sections"]
        ]

        again = asyncio.run(service.async_generate("t1", film_seconds=FILM))
        assert again["generated"] == 0, again
        assert again["reused"] == 4
        assert session.calls == 4, "ein zweiter Lauf darf nichts kosten"

        offer = asyncio.run(service.async_offer("t1", film_seconds=FILM))
        assert offer["new_generations"] == 0
        assert offer["estimated_cost"] == 0.0
        assert offer["reused"] is True


def verify_a_film_that_lost_a_photo_does_not_buy_a_new_soundtrack() -> None:
    """The estimate wobbles by seconds. The plan must not.

    An unfetchable picture shortens its day. If that moved the plan, the
    next render would be four fresh generations at full price for a film
    nobody would hear a difference in.
    """
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        asyncio.run(service.async_generate("t1", film_seconds=FILM))
        assert session.calls == 4
        for drift in (-6.0, -2.0, +3.0, +11.0):
            offer = asyncio.run(
                service.async_offer("t1", film_seconds=FILM + drift)
            )
            assert offer["new_generations"] == 0, (drift, offer)
        # A film a full minute longer is a different film, and it is
        # allowed to need different music.
        longer = asyncio.run(service.async_offer("t1", film_seconds=FILM + 90))
        assert longer["new_generations"] > 0, longer


def verify_the_music_is_never_shorter_than_the_film() -> None:
    """Rounded up, so the last seconds are not silent."""
    for seconds in (61.0, 200.0, 431.0, 7 * 60 + 12.0):
        planned = service_module.quantized_seconds(seconds)
        assert planned >= seconds, (seconds, planned)
        assert planned - seconds < service_module.LENGTH_QUANTUM_SECONDS
    assert service_module.quantized_seconds(0) == 0.0


def verify_without_a_key_nothing_is_promised() -> None:
    """The offer still works - it just says the button will not."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session, api_key="")
        offer = asyncio.run(service.async_offer("t1", film_seconds=FILM))
        assert offer["available"] is False
        try:
            asyncio.run(service.async_generate("t1", film_seconds=FILM))
        except Exception as err:  # noqa: BLE001 - the message is the assertion
            assert "Schlüssel" in str(err), err
        else:  # pragma: no cover - a failure path that must not vanish
            raise AssertionError("ohne Schlüssel darf nicht erzeugt werden")
        assert session.calls == 0


def verify_a_film_without_length_orders_nothing() -> None:
    """No length is not "one track by default"; it is nothing."""
    with tempfile.TemporaryDirectory() as root:
        session = _Session()
        service = _service(root, session)
        offer = asyncio.run(service.async_offer("t1", film_seconds=0))
        assert offer["sections"] == 0, offer
        assert offer["estimated_cost"] == 0.0
        result = asyncio.run(service.async_generate("t1", film_seconds=0))
        assert result["generated"] == 0
        assert result["timeline"] == []
        assert session.calls == 0


for check in (
    verify_the_offer_costs_nothing_and_orders_nothing,
    verify_generating_pays_once_and_a_second_render_is_free,
    verify_a_film_that_lost_a_photo_does_not_buy_a_new_soundtrack,
    verify_the_music_is_never_shorter_than_the_film,
    verify_without_a_key_nothing_is_promised,
    verify_a_film_without_length_orders_nothing,
):
    check()

print("Trip film music service tests passed.")
