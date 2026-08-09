"""The camper's picture, from a browser into a film - and not before.

The store already had the right shape: a picture lands as a candidate
and becomes usable only when somebody has looked at it. What was
missing was every way to *use* that shape - nothing could upload, and
the waiting room could not be listed, so a candidate could only be
found by somebody who already knew its name. A page reload lost it.

The rule these checks defend: **an unconfirmed picture is never in a
film.** The second one: bytes from a browser are re-encoded before they
become a file, and the file's name is a digest of what was stored - so
nothing anybody typed reaches a path join.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import importlib.machinery
import importlib.util
import io
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
_module("homeassistant.helpers.storage", Store=object)
_module(
    "aiohttp",
    ClientError=type("ClientError", (Exception,), {}),
    ClientSession=object,
    ClientTimeout=lambda *a, **k: None,
)

_PACKAGE = "roadplanner_character_service_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root

service_module = importlib.import_module(f"{_PACKAGE}.character_asset_service")
store_module = importlib.import_module(f"{_PACKAGE}.character_asset_store")
assets_module = importlib.import_module(f"{_PACKAGE}.character_assets")

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow ships with Home Assistant
    print("Character asset service tests skipped (no Pillow).")
    raise SystemExit(0)


class _Hass:
    async def async_add_executor_job(self, target, *args, **kwargs):
        return target(*args, **kwargs)


def _png(size: int = 300, *, colour=(180, 40, 40, 255)) -> bytes:
    """A drawing of a camper, as far as this test is concerned.

    Transparent margin on purpose: the store trims it, and the trim is
    what stops the vehicle sitting at a different size in every film.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for x in range(size // 4, size * 3 // 4):
        for y in range(size // 3, size * 2 // 3):
            image.putpixel((x, y), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _service(root):
    return service_module.CharacterAssetService(
        _Hass(), store_module.CharacterAssetStore(Path(root))
    )


def verify_an_upload_waits_and_is_not_in_a_film_yet() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        result = asyncio.run(
            service.async_upload("vehicle", "map", "data:image/png;base64," + base64.b64encode(_png()).decode())
        )
        assert result["confirmed"] is False
        assert result["preview"].startswith("data:image/png;base64,")
        assert assets_module.ASSET_FILENAME_RE.match(result["filename"]), result

        state = asyncio.run(service.async_state())
        assert state["confirmed"] == [], "unbestätigt heißt: nicht im Film"
        assert [entry["filename"] for entry in state["pending"]] == [result["filename"]]
        # And the waiting room survives a reload: it is read from the
        # folder, not remembered.
        again = asyncio.run(_service(root).async_state())
        assert len(again["pending"]) == 1


def verify_confirming_is_what_puts_it_in_the_film() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        uploaded = asyncio.run(
            service.async_upload("vehicle", "map", base64.b64encode(_png()).decode())
        )
        state = asyncio.run(service.async_confirm(uploaded["filename"]))
        assert state["pending"] == []
        assert state["confirmed"] == [
            {"kind": "vehicle", "variant": "map", "filename": uploaded["filename"]}
        ], state
        # Confirming twice is a message, not a second promotion.
        try:
            asyncio.run(service.async_confirm(uploaded["filename"]))
        except Exception as err:  # noqa: BLE001 - the message is the assertion
            assert "wartet nicht" in str(err), err
        else:  # pragma: no cover
            raise AssertionError("ein bestätigtes Bild kann nicht erneut bestätigt werden")


def verify_a_discarded_candidate_leaves_nothing_behind() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        uploaded = asyncio.run(
            service.async_upload("crew", "side", base64.b64encode(_png()).decode())
        )
        state = asyncio.run(service.async_discard(uploaded["filename"]))
        assert state["pending"] == []
        assert state["confirmed"] == []
        assert not list(Path(root).iterdir()), list(Path(root).iterdir())


def verify_the_same_picture_twice_is_the_same_file() -> None:
    """The name is a digest of the stored bytes, not of the moment."""
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        image = base64.b64encode(_png()).decode()
        first = asyncio.run(service.async_upload("vehicle", "side", image))
        second = asyncio.run(service.async_upload("vehicle", "side", image))
        assert first["filename"] == second["filename"]
        assert len(asyncio.run(service.async_state())["pending"]) == 1


def verify_what_is_not_an_image_never_becomes_a_file() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        for payload in (
            base64.b64encode(b"<?php system($_GET[0]); ?>").decode(),
            base64.b64encode(b"\x00" * 64).decode(),
            "data:image/png;base64,not-base64-at-all!!",
            "",
        ):
            try:
                asyncio.run(service.async_upload("vehicle", "map", payload))
            except Exception as err:  # noqa: BLE001 - any refusal reads the same
                assert "Bild" in str(err), err
            else:  # pragma: no cover
                raise AssertionError(f"kein Bild darf keine Datei werden: {payload[:20]}")
        assert not list(Path(root).iterdir())


def verify_an_unknown_role_is_refused_before_anything_is_decoded() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        image = base64.b64encode(_png()).decode()
        for kind, variant in (("spaceship", "map"), ("vehicle", "aerial"), ("", "")):
            try:
                asyncio.run(service.async_upload(kind, variant, image))
            except Exception as err:  # noqa: BLE001
                assert "Unbekannte" in str(err), err
            else:  # pragma: no cover
                raise AssertionError(f"{kind}/{variant} ist keine Figur")


def verify_the_refusal_names_the_thing_that_is_actually_wrong() -> None:
    """The first live upload failed with the wrong reason.

    Every refusal said "expected a PNG with a transparent background",
    including for a picture that was exactly that and merely came out
    over the size limit. Telling somebody to fix a thing that is not
    wrong is worse than saying nothing: they change the one thing that
    cannot help and conclude the feature is broken.
    """
    opaque = Image.new("RGBA", (300, 300), (200, 30, 30, 255))
    buffer = io.BytesIO()
    opaque.save(buffer, format="PNG")
    blob, reason = assets_module.prepare_asset(buffer.getvalue())
    assert blob is None and reason == "opaque", reason
    assert "transparent" in assets_module.ASSET_REFUSAL_MESSAGES["opaque"]

    blob, reason = assets_module.prepare_asset(b"HEIC-ish nonsense")
    assert blob is None and reason == "unreadable", reason
    # The most likely real cause on a phone, said by name.
    assert "HEIC" in assets_module.ASSET_REFUSAL_MESSAGES["unreadable"]

    # And the three reasons do not share a sentence, which is the whole
    # point of splitting them.
    messages = {
        assets_module.ASSET_REFUSAL_MESSAGES[key]
        for key in ("opaque", "unreadable", "too_large")
    }
    assert len(messages) == 3


def verify_a_detailed_drawing_is_reduced_rather_than_refused() -> None:
    """Over the size limit is not a reason to send somebody away.

    The asset is drawn at a third of a 1280-pixel frame at most. Nobody
    can see the difference between sixteen million colours and 256 at
    that size, so reducing is a better answer than refusing.
    """
    import random

    random.seed(7)
    noisy = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    for x in range(200, 700):
        for y in range(200, 700):
            noisy.putpixel(
                (x, y),
                (random.randrange(256), random.randrange(256), random.randrange(256), 255),
            )
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    raw = buffer.getvalue()
    # Plain PNG at film size would be well over the limit.
    blob, reason = assets_module.prepare_asset(raw)
    assert blob, reason
    assert len(blob) <= assets_module.MAX_ASSET_BYTES, len(blob)
    # Still an image, and still transparent where it was transparent.
    reopened = Image.open(io.BytesIO(blob)).convert("RGBA")
    assert reopened.getchannel("A").getextrema()[0] < 255


def verify_an_upload_larger_than_the_limit_is_refused() -> None:
    with tempfile.TemporaryDirectory() as root:
        service = _service(root)
        huge = "x" * (service_module.MAX_UPLOAD_BYTES * 2 + 10)
        try:
            asyncio.run(service.async_upload("vehicle", "map", huge))
        except Exception as err:  # noqa: BLE001
            assert "Bild" in str(err), err
        else:  # pragma: no cover
            raise AssertionError("ein zu großer Upload muss abgelehnt werden")


for check in (
    verify_an_upload_waits_and_is_not_in_a_film_yet,
    verify_confirming_is_what_puts_it_in_the_film,
    verify_a_discarded_candidate_leaves_nothing_behind,
    verify_the_same_picture_twice_is_the_same_file,
    verify_what_is_not_an_image_never_becomes_a_file,
    verify_an_unknown_role_is_refused_before_anything_is_decoded,
    verify_an_upload_larger_than_the_limit_is_refused,
    verify_the_refusal_names_the_thing_that_is_actually_wrong,
    verify_a_detailed_drawing_is_reduced_rather_than_refused,
):
    check()

print("Character asset service tests passed.")
