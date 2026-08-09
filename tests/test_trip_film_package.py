"""What travels when a whole trip becomes a film.

The film is the manifest's first consumer, so the interesting property is
that it is a *translation* and not a second story layer: every word in the
package has to be traceable to the manifest it came from. The other half is
scale - twenty-five days is where limits stop being decorative.
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


import types  # noqa: E402

# The film package imports its sibling relatively, so both are loaded
# inside one package. Loading them separately would give the shared error
# class two identities, and `except` would stop catching it.
package = types.ModuleType("film_pkg")
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules["film_pkg"] = package


def load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"film_pkg.{name}", PACKAGE_ROOT / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


film = load("trip_film_package")
day_package = sys.modules["film_pkg.trip_day_render_package"]

from PIL import Image  # noqa: E402


def _photo(width: int = 1600, height: int = 1200) -> bytes:
    image = Image.new("RGB", (width, height))
    for x in range(0, width, 8):
        for y in range(0, height, 8):
            image.paste((x % 256, y % 256, (x + y) % 256), (x, y, x + 8, y + 8))
    buffer = io.BytesIO()
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (60.0, 10.0, 0.0)}
    image.save(buffer, format="JPEG", quality=92, exif=exif.tobytes())
    return buffer.getvalue()


def _manifest(chapter_count: int = 3, *, photos_per: int = 2):
    return {
        "manifest_version": 1,
        "content_hash": "a" * 64,
        "trip": {
            "title": "Finnland 2026",
            "start_date": "2026-07-17",
            "end_date": "2026-08-06",
        },
        "facts": {"distance_km": 2140.5, "photo_count": 812},
        "chapters": [
            {
                "chapter_id": f"day-{index + 1}",
                "index": index,
                "date": f"2026-07-{17 + index:02d}",
                "title": f"Etappe {index + 1}",
                "story": {"text": f"Am Tag {index + 1} ging es weiter.", "source": "composed"},
                "facts": {
                    "day_number": index + 1,
                    "distance_km": 100.0 + index,
                    "duration_minutes": 90,
                    "stop_count": 2,
                    "photo_count": 40,
                },
                "stops": [{"stop_id": "s1", "name": "Tampere"}, {"stop_id": "s2", "name": "Vaasa"}],
                "media": [{"media_id": f"m{index}-{n}"} for n in range(photos_per)],
            }
            for index in range(chapter_count)
        ],
    }


def _photos_for(manifest, count: int):
    blob = film.shrink_film_photo(_photo())
    assert blob is not None
    return {
        chapter["chapter_id"]: [blob] * count for chapter in manifest["chapters"]
    }


def verify_the_photo_budget_shrinks_as_the_trip_grows() -> None:
    """A longer trip gets a thinner film, never a bigger package."""
    # Derived from the constants rather than repeated, so raising the
    # budget does not silently make this test measure the old numbers.
    assert film.photos_per_chapter(10) == film.MAX_PHOTOS_PER_CHAPTER
    assert film.photos_per_chapter(30) == film.MAX_FILM_IMAGES // 30
    assert film.photos_per_chapter(45) == film.MAX_FILM_IMAGES // 45
    assert film.photos_per_chapter(0) == 0
    # A longer trip really does thin out.
    assert film.photos_per_chapter(45) < film.photos_per_chapter(10)
    # And it never drops to zero for a trip that has days.
    assert film.photos_per_chapter(1000) == 1


def verify_a_film_photo_carries_no_metadata() -> None:
    original = _photo()
    assert any("APP1" in marker for marker in day_package.metadata_markers(original))
    shrunk = film.shrink_film_photo(original)
    assert shrunk is not None
    assert day_package.metadata_markers(shrunk) == []
    assert max(Image.open(io.BytesIO(shrunk)).size) == film.FILM_IMAGE_MAX_EDGE
    assert len(shrunk) <= film.MAX_FILM_IMAGE_BYTES


def verify_every_word_comes_from_the_manifest() -> None:
    """A translation, not a second story layer."""
    manifest = _manifest(3)
    package, _files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 2)
    )
    for source, built in zip(manifest["chapters"], package["chapters"], strict=True):
        assert built["title"] == source["title"]
        assert built["story"] == source["story"]["text"]
        assert built["story_source"] == source["story"]["source"]
        assert built["distance_km"] == source["facts"]["distance_km"]
        assert built["photo_count"] == source["facts"]["photo_count"]
    assert package["trip"]["title"] == manifest["trip"]["title"]
    assert package["manifest_content_hash"] == manifest["content_hash"]


def verify_a_day_without_photos_travels_as_a_day_without_photos() -> None:
    """The gap is the finding. Skipping the day would hide it."""
    manifest = _manifest(3)
    photos = _photos_for(manifest, 2)
    photos["day-2"] = []
    package, files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=photos
    )
    empty = package["chapters"][1]
    assert empty["images"] == []
    assert empty["chapter_id"] == "day-2"
    # The day still says how many photos it HAS, which is what makes the
    # gap legible rather than merely blank.
    assert empty["photo_count"] == 40
    assert len(package["chapters"]) == 3
    assert not any(path.startswith("photos/c01-") for path in files)


def verify_the_paths_are_built_from_numbers_only() -> None:
    assert film.photo_filename(0, 1) == "photos/c00-1.jpg"
    assert film.photo_filename(24, 3) == "photos/c24-3.jpg"
    for bad in (-1, film.MAX_CHAPTERS, "0; rm -rf /", None, 1.5):
        try:
            film.photo_filename(bad, 1)
        except day_package.RenderPackageError:
            continue
        raise AssertionError(f"Kapitelindex {bad!r} akzeptiert")
    # One past the cap, read from the module rather than written as a
    # literal - the cap moved from three to four when a major highlight
    # earned a fourth picture, and a hard-coded 4 would have started
    # asserting the wrong thing without failing.
    for bad in (0, film.MAX_PHOTOS_PER_CHAPTER + 1, "1", None):
        try:
            film.photo_filename(0, bad)
        except day_package.RenderPackageError:
            continue
        raise AssertionError(f"Bildposition {bad!r} akzeptiert")


def verify_a_whole_trip_fits_and_a_bigger_one_is_refused() -> None:
    manifest = _manifest(25)
    package, files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 3)
    )
    assert len(package["chapters"]) == 25
    assert len(files) == 75
    assert package["total_image_bytes"] <= film.MAX_FILM_PACKAGE_BYTES
    film.validate_film_package(package)

    too_long = _manifest(film.MAX_CHAPTERS + 1)
    try:
        film.build_film_package(
            job_id="job", manifest=too_long, photos_by_chapter={}
        )
    except day_package.RenderPackageError as err:
        assert "Kapitel" in str(err)
        return
    raise AssertionError("Eine zu lange Reise wurde akzeptiert")


def verify_a_trip_without_chapters_is_refused() -> None:
    try:
        film.build_film_package(
            job_id="job", manifest={"chapters": []}, photos_by_chapter={}
        )
    except day_package.RenderPackageError:
        return
    raise AssertionError("Eine Reise ohne Kapitel wurde akzeptiert")


def verify_the_validator_agrees_with_the_builder() -> None:
    manifest = _manifest(4)
    package, _files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 2)
    )
    assert film.validate_film_package(package) is package

    broken = {**package, "film_package_version": 2}
    for case in (
        broken,
        {**package, "chapters": []},
        {**package, "chapters": "keine Liste"},
        "kein Objekt",
    ):
        try:
            film.validate_film_package(case)
        except day_package.RenderPackageError:
            continue
        raise AssertionError(f"Ungültiges Filmpaket akzeptiert: {case!r:.60}")


def verify_a_moved_image_path_is_refused() -> None:
    """The path must match the position it claims, or it is not read."""
    manifest = _manifest(2)
    package, _files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 2)
    )
    package["chapters"][0]["images"][0]["path"] = "photos/c01-1.jpg"
    try:
        film.validate_film_package(package)
    except day_package.RenderPackageError as err:
        assert "Bildpfad" in str(err)
        return
    raise AssertionError("Ein verschobener Bildpfad wurde akzeptiert")


def verify_no_media_id_or_internal_reference_travels() -> None:
    """The film needs pictures, not the ids they were chosen by."""
    manifest = _manifest(3)
    package, _files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 2)
    )
    serialised = repr(package)
    for internal in ("media_id", "m0-0", "stop_id", "s1"):
        assert internal not in serialised, internal


verify_the_photo_budget_shrinks_as_the_trip_grows()
verify_a_film_photo_carries_no_metadata()
verify_every_word_comes_from_the_manifest()
verify_a_day_without_photos_travels_as_a_day_without_photos()

def verify_the_film_takes_the_caption_when_there_is_one() -> None:
    """A card is on screen for three seconds. A paragraph is not readable
    there, however good it is - so the manifest carries a second, shorter
    version written for exactly this, and the film prefers it.

    Falls back to the story, so a trip nobody has edited renders exactly
    as it did before this field existed.
    """
    manifest = _manifest(2)
    manifest["chapters"][0]["video_caption"] = "Kurz, fuer den Film."
    manifest["chapters"][0]["importance"] = "major_highlight"
    package, _files = film.build_film_package(
        job_id="job", manifest=manifest, photos_by_chapter=_photos_for(manifest, 2)
    )
    assert package["chapters"][0]["story"] == "Kurz, fuer den Film."
    assert package["chapters"][0]["importance"] == "major_highlight"
    # The unedited chapter keeps the long text.
    assert package["chapters"][1]["story"] == "Am Tag 2 ging es weiter."
    assert package["chapters"][1]["importance"] == "normal"


verify_the_film_takes_the_caption_when_there_is_one()
verify_the_paths_are_built_from_numbers_only()
verify_a_whole_trip_fits_and_a_bigger_one_is_refused()
verify_a_trip_without_chapters_is_refused()
verify_the_validator_agrees_with_the_builder()
verify_a_moved_image_path_is_refused()
verify_no_media_id_or_internal_reference_travels()
def verify_the_package_ceiling_never_undercuts_the_plan() -> None:
    """The silent loss that has now happened twice, as an assertion.

    The scene planner asks for up to fourteen pictures on a major
    highlight. When the package's own per-chapter ceiling is lower, the
    extra ones vanish where nobody looks: the plan is valid, the film
    renders, and the only trace is a count nobody compares. It happened
    at four, and again at ten - the second time it would have thinned a
    25-day trip to seven a day and made a whole curation rewrite
    invisible in the film.

    Two bounds, because they fail differently: the ceiling is what a
    short trip hits, the total budget is what a long one hits.
    """
    plan = load("trip_film_plan")
    assert film.MAX_PHOTOS_PER_CHAPTER >= max(plan.PHOTO_WEIGHTS.values()), (
        film.MAX_PHOTOS_PER_CHAPTER,
        plan.PHOTO_WEIGHTS,
    )
    assert film.photos_per_chapter(25) >= plan.PHOTO_WEIGHTS["highlight"], (
        film.photos_per_chapter(25),
        plan.PHOTO_WEIGHTS,
    )


verify_the_package_ceiling_never_undercuts_the_plan()
print("Trip film package tests passed.")
