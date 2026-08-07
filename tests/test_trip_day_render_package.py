"""What may leave Roadplanner when one real trip day is handed over.

The package is the point where real travel data crosses into ``/share``,
which every app with the same mount can read. These tests are therefore
about subtraction: that the photo which leaves is not the photo that was
taken, that the metadata describing where and when it was taken is gone,
and that nothing in the manifest can name a path.

The EXIF check reads the JPEG segment table directly rather than asking
Pillow. Asking the same library that wrote the file whether it wrote
anything unwanted proves very little.
"""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys

# Loading integration modules by path would otherwise leave a
# __pycache__ inside the shipped integration directory, which the
# repository validator rightly refuses.
sys.dont_write_bytecode = True

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"roadplanner_{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pkg = load("trip_day_render_package")

from PIL import Image  # noqa: E402 - after the loader shim above


def _photo(width: int = 2400, height: int = 1600, *, exif: bytes | None = None) -> bytes:
    """A JPEG that looks like a phone photo: large, and full of metadata."""
    image = Image.new("RGB", (width, height))
    # Not a flat colour - a flat image compresses to almost nothing and
    # would make the size limits untestable.
    for x in range(0, width, 8):
        for y in range(0, height, 8):
            image.paste((x % 256, y % 256, (x + y) % 256), (x, y, x + 8, y + 8))
    buffer = io.BytesIO()
    if exif is None:
        exif_obj = Image.Exif()
        exif_obj[0x0112] = 1  # Orientation
        exif_obj[0x010F] = "Roadplanner Testkamera"  # Make
        exif_obj[0x9003] = "2026:08:05 11:22:33"  # DateTimeOriginal
        # The GPS block is the reason this whole step exists.
        exif_obj[0x8825] = {1: "N", 2: (60.0, 10.0, 0.0), 3: "E", 4: (24.0, 56.0, 0.0)}
        exif = exif_obj.tobytes()
    image.save(buffer, format="JPEG", quality=92, exif=exif)
    return buffer.getvalue()


def _day(**overrides):
    day = {
        "id": "day-7",
        "date": "2026-08-05",
        "title": "Von Tampere nach Vaasa",
        "details": {"generated_summary": "Ein langer Fahrtag am See entlang."},
        "distance_km": 214.6,
        "drive_minutes": 195,
    }
    day.update(overrides)
    return day


def verify_the_original_really_carries_what_must_not_travel() -> None:
    """Without this the stripping test could pass on an empty input."""
    markers = pkg.metadata_markers(_photo())
    assert any("APP1" in marker for marker in markers), markers


def verify_the_photo_that_leaves_carries_no_metadata() -> None:
    shrunk = pkg.shrink_photo(_photo())
    assert shrunk is not None
    assert pkg.metadata_markers(shrunk) == [], pkg.metadata_markers(shrunk)
    # And the bytes themselves do not contain the tell-tale header, which
    # catches a segment walk that stopped early for the wrong reason.
    assert b"Exif\x00\x00" not in shrunk
    assert b"Roadplanner Testkamera" not in shrunk
    assert b"2026:08:05" not in shrunk


def verify_the_photo_is_downscaled_and_bounded() -> None:
    shrunk = pkg.shrink_photo(_photo(4032, 3024))
    assert shrunk is not None
    assert max(Image.open(io.BytesIO(shrunk)).size) == pkg.IMAGE_MAX_EDGE
    assert len(shrunk) <= pkg.MAX_IMAGE_BYTES


def verify_rotation_is_applied_before_the_tag_is_dropped() -> None:
    """Dropping the tag first leaves every portrait photo on its side."""
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90° clockwise when displaying"
    rotated = pkg.shrink_photo(_photo(1200, 600, exif=exif.tobytes()))
    assert rotated is not None
    width, height = Image.open(io.BytesIO(rotated)).size
    assert height > width, (width, height)


def verify_something_that_is_not_an_image_is_skipped_not_fatal() -> None:
    assert pkg.shrink_photo(b"<html>Anmeldeseite</html>") is None
    assert pkg.shrink_photo(b"") is None


def verify_the_package_describes_exactly_the_bytes_it_ships() -> None:
    import hashlib

    package, images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[{"name": "Tampere", "arrival_time": "09:00"}, {"name": "Vaasa"}],
        photos=[_photo(), _photo(1800, 1200)],
        day_number=7,
        day_count=21,
    )
    assert len(images) == 2
    for declared, blob in zip(package["images"], images, strict=True):
        assert declared["size_bytes"] == len(blob)
        assert declared["sha256"] == hashlib.sha256(blob).hexdigest()
    assert [entry["index"] for entry in package["images"]] == [1, 2]
    assert package["total_image_bytes"] == sum(len(blob) for blob in images)


def verify_no_filename_travels_in_the_manifest() -> None:
    """A name in the package would be a string that reaches a path join."""
    package, _images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[],
        photos=[_photo()],
    )
    for entry in package["images"]:
        assert "filename" not in entry, entry
        assert "path" not in entry, entry
    assert pkg.image_filename(1) == "photo-1.jpg"
    for bad in (0, -1, pkg.MAX_IMAGES + 1, "1; rm -rf /", "../../etc/passwd", None):
        try:
            pkg.image_filename(bad)
        except pkg.RenderPackageError:
            continue
        raise AssertionError(f"Bildindex {bad!r} wurde akzeptiert")


def verify_only_locally_present_numbers_are_carried() -> None:
    with_values, _ = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[],
        photos=[_photo()],
    )
    assert with_values["day"]["distance_km"] == 214.6
    assert with_values["day"]["duration_minutes"] == 195

    # A day without them shows none - nothing is looked up to fill it in.
    without, _ = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(distance_km=None, drive_minutes=0),
        stops=[],
        photos=[_photo()],
    )
    assert without["day"]["distance_km"] is None
    assert without["day"]["duration_minutes"] is None


def verify_a_day_without_a_usable_photo_is_refused() -> None:
    """A photo video with no photos would prove nothing."""
    try:
        pkg.build_package(
            job_id="11111111-2222-3333-4444-555555555555",
            trip_title="Finnland 2026",
            day=_day(),
            stops=[],
            photos=[b"<html>kein Bild</html>"],
        )
    except pkg.RenderPackageError as err:
        assert "Foto" in str(err)
        return
    raise AssertionError("Ein Tag ohne Foto wurde akzeptiert")


def verify_the_package_stays_within_its_bounds() -> None:
    package, images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="F" * 400,
        day=_day(title="T" * 400, details={"generated_summary": "S" * 2000}),
        stops=[{"name": f"Stopp {index}"} for index in range(40)],
        photos=[_photo() for _ in range(12)],
    )
    assert len(images) == pkg.MAX_IMAGES
    assert len(package["stops"]) == pkg.MAX_STOPS
    assert len(package["trip_title"]) <= pkg.MAX_TITLE_LENGTH
    assert len(package["day"]["title"]) <= pkg.MAX_TITLE_LENGTH
    assert len(package["day"]["summary"]) <= pkg.MAX_SUMMARY_LENGTH
    assert package["total_image_bytes"] <= pkg.MAX_PACKAGE_BYTES


def verify_a_stop_without_a_name_is_dropped_not_shipped_empty() -> None:
    package, _images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[{"name": "  "}, {"name": "Vaasa"}, {"not_a_stop": True}, "kein Objekt"],
        photos=[_photo()],
    )
    assert [stop["name"] for stop in package["stops"]] == ["Vaasa"]


def verify_no_internal_identifier_travels_with_a_stop() -> None:
    """Live lesson from the PDF: an internal id showed up in a stop name."""
    package, _images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[
            {
                "name": "Vaasa",
                "id": "stop-abc-123",
                "location": {"lat": 63.09, "lon": 21.61},
                "provider_place_id": "ChIJgeheim",
            }
        ],
        photos=[_photo()],
    )
    assert package["stops"] == [{"name": "Vaasa", "time": ""}]
    serialised = repr(package)
    for secret in ("stop-abc-123", "ChIJgeheim", "63.09", "21.61"):
        assert secret not in serialised, secret


def verify_the_validator_agrees_with_the_builder() -> None:
    package, _images = pkg.build_package(
        job_id="11111111-2222-3333-4444-555555555555",
        trip_title="Finnland 2026",
        day=_day(),
        stops=[{"name": "Vaasa"}],
        photos=[_photo()],
    )
    assert pkg.validate_package(package) is package


def verify_the_validator_refuses_what_must_not_be_read() -> None:
    def broken(**changes):
        package, _images = pkg.build_package(
            job_id="11111111-2222-3333-4444-555555555555",
            trip_title="Finnland 2026",
            day=_day(),
            stops=[{"name": "Vaasa"}],
            photos=[_photo()],
        )
        package.update(changes)
        return package

    cases = [
        broken(package_version=2),
        broken(images=[]),
        broken(images=[{"index": 1, "size_bytes": 10, "sha256": "nope"}]),
        broken(images=[{"index": 1, "size_bytes": 0, "sha256": "a" * 64}]),
        broken(
            images=[
                {"index": 1, "size_bytes": 10, "sha256": "a" * 64},
                {"index": 1, "size_bytes": 10, "sha256": "b" * 64},
            ]
        ),
        broken(images=[{"index": 99, "size_bytes": 10, "sha256": "a" * 64}]),
        broken(
            images=[
                {
                    "index": 1,
                    "size_bytes": pkg.MAX_IMAGE_BYTES + 1,
                    "sha256": "a" * 64,
                }
            ]
        ),
        broken(day={}),
        broken(stops=[{"name": "x"}] * (pkg.MAX_STOPS + 1)),
        broken(stops="keine Liste"),
        "kein Objekt",
    ]
    for case in cases:
        try:
            pkg.validate_package(case)
        except pkg.RenderPackageError:
            continue
        raise AssertionError(f"Ungültiges Paket wurde akzeptiert: {case!r:.80}")


verify_the_original_really_carries_what_must_not_travel()
verify_the_photo_that_leaves_carries_no_metadata()
verify_the_photo_is_downscaled_and_bounded()
verify_rotation_is_applied_before_the_tag_is_dropped()
verify_something_that_is_not_an_image_is_skipped_not_fatal()
verify_the_package_describes_exactly_the_bytes_it_ships()
verify_no_filename_travels_in_the_manifest()
verify_only_locally_present_numbers_are_carried()
verify_a_day_without_a_usable_photo_is_refused()
verify_the_package_stays_within_its_bounds()
verify_a_stop_without_a_name_is_dropped_not_shipped_empty()
verify_no_internal_identifier_travels_with_a_stop()
verify_the_validator_agrees_with_the_builder()
verify_the_validator_refuses_what_must_not_be_read()
print("Trip day render package tests passed.")
