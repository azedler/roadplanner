"""Crew and vehicle portraits are stored locally, already cropped.

Live request: "Ich fände es auch sinnvoll wenn die Bilder der Crew permanent
lokal gespeichert werden." Before this, a portrait was a live reference into
OneDrive - resolved again on every panel render and downloaded again for
every export - so a face depended on a cloud account staying reachable and
on the source photo never being moved.

Storing it already cropped also removes the crop maths from rendering
entirely, which is what let the shown region drift from the picked one.
"""
from __future__ import annotations

import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile

MODULE_PATH = Path("custom_components/roadplanner_mcp/crew_portraits.py")
spec = spec_from_file_location("roadplanner_crew_portraits_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _photo(width: int = 800, height: int = 600) -> bytes:
    from PIL import Image

    image = Image.new("RGB", (width, height))
    for x in range(width):
        for y in range(0, height, 200):
            image.putpixel((x, y), (x % 256, y % 256, 128))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def verify_the_crop_is_part_of_the_portrait_identity() -> None:
    """Re-crop the same photo and it MUST be a different file.

    Otherwise a stale portrait could be served for a changed selection -
    exactly the drift the local copy is meant to end.
    """
    crop_a = {"x": 0.2, "y": 0.1, "w": 0.3, "h": 0.4}
    crop_b = {"x": 0.25, "y": 0.1, "w": 0.3, "h": 0.4}
    first = module.portrait_key("person-1", "media-1", crop_a)
    assert first == module.portrait_key("person-1", "media-1", dict(crop_a))
    assert first != module.portrait_key("person-1", "media-1", crop_b)
    assert first != module.portrait_key("person-1", "media-2", crop_a)
    assert first != module.portrait_key("person-2", "media-1", crop_a)
    # A person and a vehicle never collide even with identical inputs.
    assert first != module.portrait_key("person-1", "media-1", crop_a, kind="vehicle")
    # Nothing to store without a source.
    assert module.portrait_key("", "media-1", crop_a) == ""
    assert module.portrait_key("person-1", "", crop_a) == ""
    assert module.PORTRAIT_FILENAME_RE.match(first)


def verify_the_stored_file_is_already_cropped_and_bounded() -> None:
    from PIL import Image

    photo = _photo(800, 600)
    rendered = module.render_portrait(photo, {"x": 0.5, "y": 0.25, "w": 0.25, "h": 0.5})
    assert rendered
    image = Image.open(io.BytesIO(rendered))
    # 25% of 800 x 50% of 600 - the crop, not the whole picture.
    assert image.size == (200, 300), image.size

    # A huge source is shrunk to a sane portrait.
    big = module.render_portrait(_photo(4000, 3000), None)
    assert big
    assert max(Image.open(io.BytesIO(big)).size) == module.PORTRAIT_MAX_EDGE

    # Unreadable bytes are a reason to keep the old file, not to crash.
    assert module.render_portrait(b"not-an-image", None) is None
    assert module.render_portrait(b"", None) is None
    # A crop too small to be meaningful is ignored rather than obeyed.
    tiny = module.render_portrait(photo, {"x": 0, "y": 0, "w": 0.001, "h": 0.001})
    assert tiny and Image.open(io.BytesIO(tiny)).size == (800, 600)


def verify_a_requested_filename_cannot_escape_the_folder() -> None:
    """The name arrives off an HTTP URL, so it is validated, not trusted."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "portraits"
        store = module.CrewPortraitStore(root)
        name = module.portrait_key("person-1", "media-1", None)
        assert store.write(name, module.render_portrait(_photo(), None)) == name
        assert store.exists(name)
        assert store.path_for(name) is not None

        outside = Path(tmp) / "secret.txt"
        outside.write_text("nicht für die Kamera")
        for hostile in (
            "../secret.txt",
            "..%2Fsecret.txt",
            "/etc/passwd",
            "portrait.jpg",
            "",
            None,
        ):
            assert store.path_for(hostile) is None, hostile
            assert store.write(hostile, b"x") == ""
        assert outside.read_text() == "nicht für die Kamera"


def verify_orphaned_portraits_are_pruned() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = module.CrewPortraitStore(Path(tmp))
        keep = module.portrait_key("person-1", "media-1", None)
        drop = module.portrait_key("person-9", "media-9", None)
        data = module.render_portrait(_photo(80, 60), None)
        store.write(keep, data)
        store.write(drop, data)
        assert store.prune({keep}) == 1
        assert store.exists(keep)
        assert not store.exists(drop)


def verify_the_panel_and_the_pdf_read_the_local_copy() -> None:
    root = Path("custom_components/roadplanner_mcp")
    avatar = (root / "frontend/features/crew.js").read_text(encoding="utf-8")
    assert "portrait_url" in avatar, "the panel must prefer the stored portrait"
    # A vehicle gets the same treatment (live request: "Vielleicht auch vom
    # Fahrzeug ein Bild?").
    assert "_crewVehicleAvatar" in avatar
    assert "_renderCrewReferencePhotoPicker(vehicle" in avatar

    store = (root / "crew_store.py").read_text(encoding="utf-8")
    vehicle_block = store.split("def normalize_vehicle", 1)[1].split("class ", 1)[0]
    assert "reference_media_id" in vehicle_block and "reference_crop" in vehicle_block

    exporter = (root / "trip_pdf_export.py").read_text(encoding="utf-8")
    assert "_local_portrait" in exporter
    assert "_async_vehicle_photo" in exporter

    view = (root / "crew_portrait_http.py").read_text(encoding="utf-8")
    # This assertion used to demand the opposite - "a portrait is shown to
    # a logged-in browser, no reason to weaken auth" - and so held the bug
    # in place with a premise that was simply untrue. A browser does not
    # attach a bearer token to an <img src>. Every portrait answered 401,
    # the pictures stayed blank, and Home Assistant counts a 401 from any
    # endpoint as a failed login: visiting the crew page banned the
    # household's own IP address from its own Home Assistant.
    assert "requires_auth = False" in view, (
        "ein <img> sendet kein Token - Sitzungsauth hier heisst 401 und IP-Sperre"
    )
    assert "unguessable filename is the capability" in view, (
        "der Ersatz fuer die Authentifizierung muss benannt sein"
    )


verify_the_crop_is_part_of_the_portrait_identity()
verify_the_stored_file_is_already_cropped_and_bounded()
verify_a_requested_filename_cannot_escape_the_folder()
verify_orphaned_portraits_are_pruned()
verify_the_panel_and_the_pdf_read_the_local_copy()
print("Crew portrait tests passed.")
