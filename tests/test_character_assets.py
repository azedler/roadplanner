"""What a character asset promises, checked against the real module.

Two promises carry the whole feature, and both are the kind that a
comment can make and only a test can keep.

**A picture is made once and then kept.** Not because generating is slow,
though it is, and not because it costs money, though it does - because a
generative model does not return the same image twice, and a record of a
journey that looks different every time it is exported is not a record.
So the name an asset is stored under must be a function of what it was
made *from*, and confirming must move bytes rather than make new ones.

**Nothing generated is used until somebody has looked at it.** A
candidate is not an asset. A film may only ever read what a human
approved, and "may only" is not a property of a docstring.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True


# Loaded as a real package, because character_asset_store imports its
# sibling relatively - and running the store against a temporary folder is
# the only way to check "a candidate is not an asset" as behaviour rather
# than as prose.
_PACKAGE = "roadplanner_mcp_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(ROOT / "custom_components" / "roadplanner_mcp")]
sys.modules[_PACKAGE] = _root


def _load(name: str):
    return importlib.import_module(f"{_PACKAGE}.{name}")


assets = _load("character_assets")


def verify_the_name_follows_the_source() -> None:
    """Change what a picture was made from and it is a different file.

    This is the whole cache-invalidation story: there is none. A new
    reference photograph or a new description produces a new name, so a
    stale illustration can never be served for a changed request.
    """
    first = assets.fingerprint("photo-abc", "blaugrau, schwarzes Hochdach")
    again = assets.fingerprint("photo-abc", "blaugrau, schwarzes Hochdach")
    other = assets.fingerprint("photo-abc", "weiss, Aufstelldach")
    changed_photo = assets.fingerprint("photo-xyz", "blaugrau, schwarzes Hochdach")
    assert first == again, "derselbe Ursprung muss denselben Namen ergeben"
    assert first != other, "eine andere Beschreibung ist ein anderes Bild"
    assert first != changed_photo, "ein anderes Foto ist ein anderes Bild"

    name = assets.asset_filename("vehicle", "map", first)
    assert assets.ASSET_FILENAME_RE.match(name), name
    assert name == assets.asset_filename("vehicle", "map", first)


def verify_nothing_typed_can_reach_a_path() -> None:
    """The filename is built from a hex digest and refuses anything else."""
    for bad in ("../../etc/passwd", "", "vehicle", "z" * 16, "/abs", "../x"):
        try:
            assets.asset_filename("vehicle", "map", bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} haette abgelehnt werden muessen")
    for kind, variant in (("bus", "map"), ("vehicle", "front"), ("", "")):
        try:
            assets.asset_filename(kind, variant, "0" * 16)
        except ValueError:
            continue
        raise AssertionError(f"{kind}/{variant} haette abgelehnt werden muessen")


def verify_a_reference_photo_changes_what_is_asked_for() -> None:
    """Without a photo the prompt may not claim to know the vehicle.

    A prompt that says "our camper" with nothing attached is asking a
    model to invent a fact about a real object. With the photograph
    attached the same sentence is an instruction about something the
    model can actually see.
    """
    with_photo = assets.build_prompt("vehicle", "map", "Nugget Plus", from_photo=True)
    without = assets.build_prompt("vehicle", "map", "Nugget Plus", from_photo=False)
    assert "beigefügten Foto" in with_photo
    assert "beigefügten Foto" not in without
    # The style is not optional in either case: the asset has to sit on a
    # dark map next to a route line, which is a constraint of the film.
    for prompt in (with_photo, without):
        assert "transparent" in prompt, "ein Hintergrund wuerde den Camper als Kasten zeigen"
        assert "Kein Text" in prompt


def verify_the_package_refuses_anything_that_looks_like_a_link() -> None:
    """Same rule as the crew portraits, and for the same reason."""
    ok = {
        "assets": [
            {
                "kind": "vehicle",
                "variant": "map",
                "path": "characters/vehicle-map.png",
                "sha256": "a" * 64,
                "size_bytes": 12,
            }
        ]
    }
    assert assets.validate_characters(ok) is ok
    assert assets.validate_characters(None) is None
    assert assets.validate_characters({}) is None

    for broken in (
        {"assets": [{**ok["assets"][0], "path": "characters/vehicle-side.png"}]},
        {"assets": [{**ok["assets"][0], "kind": "lorry"}]},
        {"assets": [{**ok["assets"][0], "sha256": "https://example.invalid/x"}]},
        {"assets": [{**ok["assets"][0], "size_bytes": "/api/roadplanner/x"}]},
        {"assets": []},
    ):
        try:
            assets.validate_characters(broken)
        except ValueError:
            continue
        raise AssertionError(f"{broken} haette abgelehnt werden muessen")


def verify_a_candidate_is_not_an_asset() -> None:
    """A film may read only what somebody confirmed.

    The store is exercised for real rather than described: writing a
    candidate must leave `confirmed()` empty, and confirming must be a
    move of the same bytes rather than anything new.
    """
    store_module = _load("character_asset_store")
    with tempfile.TemporaryDirectory() as folder:
        store = store_module.CharacterAssetStore(Path(folder))
        name = assets.asset_filename("vehicle", "map", "0" * 16)
        blob = b"\x89PNG\r\n\x1a\n" + b"x" * 64

        assert store.write(name, blob, candidate=True) == name
        assert store.confirmed() == [], "ein Kandidat darf nicht im Film landen"
        assert store.read(name) is None, "ein Kandidat darf nicht lesbar sein"
        assert store.read(name, candidate=True) == blob

        assert store.confirm(name) is True
        assert store.read(name) == blob, "Bestaetigen muss dieselben Bytes behalten"
        assert store.confirmed() == [
            {"kind": "vehicle", "variant": "map", "filename": name}
        ]
        # Confirming twice is not a way to get a second asset.
        assert store.confirm(name) is False

        # And a name that is not a character asset never becomes a path.
        assert store.write("../escape.png", blob) == ""
        assert store.read("../escape.png") is None


for check in (
    verify_the_name_follows_the_source,
    verify_nothing_typed_can_reach_a_path,
    verify_a_reference_photo_changes_what_is_asked_for,
    verify_the_package_refuses_anything_that_looks_like_a_link,
    verify_a_candidate_is_not_an_asset,
):
    check()

print("Character asset tests passed.")
