"""Turn a TravelStoryManifest into a package the renderer can film.

This is the first consumer of the manifest, and it exists to answer one
question: can that description carry a whole trip as a film? So it is
deliberately a **translation, not a second story layer**. Every title,
every sentence, every fact and every media reference comes from the
manifest. Nothing is composed here, nothing is looked up, and nothing is
filled in.

What that means where the manifest is thin
------------------------------------------

A day with no photos, or with no distance, is a day the film has to show
as it is. The package therefore carries the *absence* explicitly - a
chapter says how many photos it has, and zero is a value the composition
renders as a visible gap rather than skipping. Papering over a hole would
make the film prettier and the experiment worthless: the whole point is to
see where the manifest is too thin.

Scale, which is the new problem
-------------------------------

The mini export carried one day and five photos. A trip is twenty-five
days, and the numbers stop being cosmetic:

- **photos per chapter are derived from the total budget**, so a 40-day
  trip gets fewer per day rather than an oversized package. The rule is
  arithmetic and therefore deterministic.
- **the package is bounded as a whole**, not just per image.
- **the images are smaller than the mini export's.** A film shows a photo
  for a second and a half at 720p; the extra detail is bytes nobody sees.

The images travel as files, and are read by the renderer as files. They
are deliberately not embedded: fifty photos as data URIs is a serialised
blob the browser has to hold in one piece.
"""

from __future__ import annotations

import hashlib
import io
import logging
from typing import Any

from .trip_film_crew import validate_crew
from .trip_map_context import validate_map_context
from .trip_film_plan import (
    OUTRO_COLLAGE_PHOTOS,
    build_scene_plan,
    readable_places,
    validate_scene_plan,
)
from .trip_day_render_package import (
    RenderPackageError,
    metadata_markers,
)

_LOGGER = logging.getLogger(__name__)

FILM_PACKAGE_VERSION = 1
FILM_MANIFEST_FILENAME = "film.json"
FILM_PHOTO_DIR = "photos"

# --- limits -------------------------------------------------------------

# A whole trip's worth of pictures. Ninety is three per day for a month,
# and the per-chapter number is derived from it rather than fixed, so a
# longer trip produces a thinner film instead of a bigger package.
MAX_FILM_IMAGES = 90
# Four rather than three, so a major highlight can actually look like
# one. The total budget is unchanged: a rich day now takes its extra
# picture from a transfer day, not from a bigger package.
MAX_PHOTOS_PER_CHAPTER = 4
MAX_CHAPTERS = 45
# A film frame shows a photo for well under two seconds at 720p. 900 px on
# the long edge is already more than the frame can display.
FILM_IMAGE_MAX_EDGE = 900
FILM_JPEG_QUALITY = 76
MAX_FILM_IMAGE_BYTES = 280 * 1024
MAX_FILM_PACKAGE_BYTES = 24 * 1024 * 1024

MAX_TITLE_LENGTH = 120
MAX_STORY_LENGTH = 1200


def photos_per_chapter(chapter_count: int) -> int:
    """How many photos each chapter may carry, from the total budget.

    Arithmetic rather than a table: a rule that has to be looked up is a
    rule that will disagree with itself later.
    """
    if chapter_count <= 0:
        return 0
    return max(1, min(MAX_PHOTOS_PER_CHAPTER, MAX_FILM_IMAGES // chapter_count))


def photo_filename(chapter_index: Any, position: Any) -> str:
    """The one place a film photo filename is built, from two integers."""
    # `int(1.5)` is 1, so accepting anything int() swallows would let a
    # float become a valid path. The path is built from integers, and only
    # integers are integers.
    if (
        isinstance(chapter_index, bool)
        or isinstance(position, bool)
        or not isinstance(chapter_index, int)
        or not isinstance(position, int)
    ):
        raise RenderPackageError("Bildkennung ist keine Ganzzahl")
    chapter = chapter_index
    slot = position
    if not 0 <= chapter < MAX_CHAPTERS:
        raise RenderPackageError("Kapitelindex liegt außerhalb des erlaubten Bereichs")
    if not 1 <= slot <= MAX_PHOTOS_PER_CHAPTER:
        raise RenderPackageError("Bildposition liegt außerhalb des erlaubten Bereichs")
    return f"{FILM_PHOTO_DIR}/c{chapter:02d}-{slot}.jpg"


def shrink_film_photo(data: bytes) -> bytes | None:
    """Downscale and strip a photo for the film.

    Same rules as the mini export - re-encode from pixels, apply the
    orientation first, refuse anything that still carries metadata - at a
    size the film can actually show. Kept as its own function rather than a
    parameter on the other one so the two can drift apart deliberately
    later without either changing by accident.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow ships with Home Assistant
        _LOGGER.warning("Pillow fehlt - für den Reisefilm können keine Bilder vorbereitet werden")
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((FILM_IMAGE_MAX_EDGE, FILM_IMAGE_MAX_EDGE), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=FILM_JPEG_QUALITY, optimize=True)
    except Exception as err:  # noqa: BLE001 - a broken photo is skipped, never fatal
        _LOGGER.debug("Foto für den Reisefilm nicht verwendbar: %s", type(err).__name__)
        return None
    shrunk = buffer.getvalue()
    if len(shrunk) > MAX_FILM_IMAGE_BYTES:
        buffer = io.BytesIO()
        try:
            image.save(buffer, format="JPEG", quality=62, optimize=True)
        except Exception:  # noqa: BLE001
            return None
        shrunk = buffer.getvalue()
        if len(shrunk) > MAX_FILM_IMAGE_BYTES:
            return None
    if metadata_markers(shrunk):
        _LOGGER.warning("Foto trägt nach dem Neucodieren noch Metadaten und wird ausgelassen")
        return None
    return shrunk


# What a picture is shaped like. The film needs this because "object-fit:
# cover" on a portrait photograph in a 16:9 frame cuts off the top and the
# bottom - which is where the sky and the person usually are. Derived
# here, in the rendering package, never in the story manifest: a
# description of a journey does not care about aspect ratios.
ORIENTATION_LANDSCAPE = "landscape"
ORIENTATION_PORTRAIT = "portrait"
ORIENTATION_SQUARE = "square"
# Deliberately generous. A 4:3 photograph is not "square-ish" in any way
# that changes how it should be shown; only something close to 1:1 is.
_SQUARE_LOW = 0.9
_SQUARE_HIGH = 1.1


def image_shape(data: bytes) -> tuple[int, int, str]:
    """Width, height and orientation of an already-prepared photo."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001 - an unreadable size is not fatal
        return 0, 0, ORIENTATION_LANDSCAPE
    if not width or not height:
        return 0, 0, ORIENTATION_LANDSCAPE
    ratio = width / height
    if _SQUARE_LOW <= ratio <= _SQUARE_HIGH:
        orientation = ORIENTATION_SQUARE
    elif ratio < 1:
        orientation = ORIENTATION_PORTRAIT
    else:
        orientation = ORIENTATION_LANDSCAPE
    return int(width), int(height), orientation


# What a picture is made of, in two colours. The film shows an upright
# photograph whole and has to fill the space beside it with something.
#
# The obvious answer - the same photograph, blurred and darkened - was
# the first one built, and it was measured at 210 ms per frame against
# 46 ms for the same picture shown landscape. A full-frame blur is
# re-rasterised for every frame in a software renderer, and on the CI
# film that one effect was most of the reason the render ran past its
# time limit.
#
# Sampling the colours here instead costs a few milliseconds ONCE, in
# the process that already has the pixels open. The surround is still
# derived from the photograph rather than invented, and drawing it is a
# gradient, which is free.
_BACKDROP_DARKEN = 0.42


def image_palette(data: bytes) -> tuple[str, str]:
    """Two darkened colours: the top half of the picture, then the bottom."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            small = image.convert("RGB").resize((8, 8))
            pixels = list(small.getdata())
    except Exception:  # noqa: BLE001 - a picture nobody can read gets the default
        return "#161d29", "#0d121a"

    def average(values: list[tuple[int, int, int]]) -> str:
        if not values:
            return "#161d29"
        count = len(values)
        channels = [
            min(255, max(0, int(sum(pixel[band] for pixel in values) / count * _BACKDROP_DARKEN)))
            for band in range(3)
        ]
        return "#{:02x}{:02x}{:02x}".format(*channels)

    return average(pixels[:32]), average(pixels[32:])


def build_film_package(
    *,
    job_id: str,
    manifest: dict[str, Any],
    photos_by_chapter: dict[str, list[bytes]],
    map_context: dict[str, Any] | None = None,
    crew: dict[str, Any] | None = None,
    crew_files: dict[str, bytes] | None = None,
    music: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Translate the manifest into a film package.

    ``photos_by_chapter`` maps a chapter id to the already-prepared image
    bytes for that chapter, in the order the manifest listed them. The
    caller does the fetching and shrinking, because that is I/O and CPU;
    what happens here is only the arrangement.

    Returns ``(package, files)`` where ``files`` maps a relative path to
    its bytes. They are returned together so the manifest and the files it
    describes cannot drift apart between here and the write.
    """
    chapters_in = manifest.get("chapters") or []
    if not chapters_in:
        raise RenderPackageError("Diese Reise hat keine Kapitel")
    if len(chapters_in) > MAX_CHAPTERS:
        raise RenderPackageError(
            f"Der Film unterstützt höchstens {MAX_CHAPTERS} Kapitel, "
            f"diese Reise hat {len(chapters_in)}"
        )

    files: dict[str, bytes] = {}
    chapters: list[dict[str, Any]] = []
    total_bytes = 0
    for index, source in enumerate(chapters_in):
        prepared = photos_by_chapter.get(source.get("chapter_id") or "", [])
        images = []
        for slot, blob in enumerate(prepared, start=1):
            path = photo_filename(index, slot)
            files[path] = blob
            total_bytes += len(blob)
            width, height, orientation = image_shape(blob)
            top, bottom = image_palette(blob)
            images.append(
                {
                    "path": path,
                    "size_bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "width": width,
                    "height": height,
                    "orientation": orientation,
                    # Sampled once here so the composition never has to
                    # filter a full frame. See image_palette.
                    "color_top": top,
                    "color_bottom": bottom,
                }
            )
        facts = source.get("facts") or {}
        chapters.append(
            {
                "chapter_id": source.get("chapter_id") or "",
                "index": index,
                "date": source.get("date") or "",
                # Straight from the manifest, including the override if one
                # was written. This package never composes a word.
                "title": (source.get("title") or "")[:MAX_TITLE_LENGTH],
                # The film takes the video caption when the manifest has
                # one. It is not a shortened story - it is the version
                # written for a card that is on screen for three seconds,
                # and a long paragraph rendered there is unreadable no
                # matter how good the prose is. Falls back to the story,
                # so a trip nobody has edited looks exactly as before.
                "story": (
                    (source.get("video_caption") or "")
                    or ((source.get("story") or {}).get("text") or "")
                )[:MAX_STORY_LENGTH],
                "story_source": (source.get("story") or {}).get("source") or "composed",
                # Weight, so the composition can eventually give a major
                # highlight more room. Carried now, used when film v1
                # decides what to do with it - the field costs nothing and
                # its absence would cost another package version.
                "importance": source.get("importance") or "normal",
                "story_role": source.get("story_role") or "journey",
                # The third of the director's three fields, and the one
                # that was quietly lost here: the plan is built from these
                # chapters, so a style that did not survive the
                # translation could never reach the screen. Every day came
                # out as "normal" no matter what the editor decided.
                "visual_style": source.get("visual_style") or "normal",
                "day_number": facts.get("day_number") or index + 1,
                "distance_km": facts.get("distance_km"),
                "duration_minutes": facts.get("duration_minutes"),
                "stop_count": facts.get("stop_count") or 0,
                # What the DAY has, which is not what this chapter carries.
                # The film shows the gap when the second number is zero.
                "photo_count": facts.get("photo_count") or 0,
                # Readable, not canonical. A title card that says
                # "park4night - (595 50) Mjölby - 24 Vetagatan" reads like
                # a database export and undoes a good headline; the
                # roadbook keeps that name, the film gets the one you
                # would say out loud.
                "stops": readable_places(source.get("stops") or [], limit=3),
                "images": images,
            }
        )

    if total_bytes > MAX_FILM_PACKAGE_BYTES:
        raise RenderPackageError(
            f"Das Filmpaket ist mit {total_bytes // 1024 // 1024} MB zu groß"
        )
    if len(files) > MAX_FILM_IMAGES:
        raise RenderPackageError(f"Das Filmpaket enthält mehr als {MAX_FILM_IMAGES} Bilder")

    trip = manifest.get("trip") or {}
    manifest_facts = manifest.get("facts") or {}
    package = {
        "film_package_version": FILM_PACKAGE_VERSION,
        "job_id": job_id,
        # Recorded so a film can be traced back to the exact description it
        # was made from - and so an unchanged manifest can be recognised.
        "manifest_content_hash": manifest.get("content_hash") or "",
        "manifest_version": manifest.get("manifest_version"),
        "trip": {
            "title": str(trip.get("title") or "")[:MAX_TITLE_LENGTH],
            "start_date": str(trip.get("start_date") or ""),
            "end_date": str(trip.get("end_date") or ""),
            "chapter_count": len(chapters),
            "distance_km": manifest_facts.get("distance_km"),
            "photo_count": manifest_facts.get("photo_count") or 0,
        },
        # The arc, when the editor wrote one. Absent rather than empty, so
        # the composition can tell "no arc" from "an arc that says
        # nothing" and fall back to the plain title card.
        "narrative": _narrative(manifest.get("narrative")),
        # Where the trip happened. Built from the canonical routing data
        # by its own module and carried here rather than in the manifest,
        # which answers what is told and not where.
        "map_context": validate_map_context(map_context) if map_context else None,
        # Who is travelling: display names, and portraits that arrive as
        # files rather than as links. See trip_film_crew.
        "crew": validate_crew(crew),
        # What it sounds like, or nothing at all. A film without music
        # stays a complete film.
        "music": music or None,
        "chapters": chapters,
        "total_image_bytes": total_bytes,
    }
    # The shot list. A rendering derivation, computed here and carried in
    # the package - never written back into the manifest, where frame
    # counts would have no business being.
    package["scene_plan"] = build_scene_plan(
        trip=package["trip"],
        chapters=chapters,
        narrative=package["narrative"],
        outro_photos=_outro_photos(chapters),
        map_context=package["map_context"],
        crew=package["crew"],
    )
    for path, blob in (crew_files or {}).items():
        files[path] = blob
    return package, files


def _narrative(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    arc = {
        "title_variant": str(value.get("title_variant") or "")[:MAX_TITLE_LENGTH],
        "subtitle": str(value.get("subtitle") or "")[:MAX_TITLE_LENGTH],
        "opening": str(value.get("opening") or "")[:MAX_STORY_LENGTH],
        "closing": str(value.get("closing") or "")[:MAX_STORY_LENGTH],
        "motifs": [
            str(motif or "")[:80]
            for motif in (value.get("motifs") or [])[:5]
            if str(motif or "").strip()
        ],
    }
    return arc if any(arc.values()) else None


def _outro_photos(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A few pictures for the closing collage, spread across the trip.

    Taking the last six would end on whichever day happened to be last.
    Spacing them means the final image is the journey rather than its
    tail - and the choice is a fixed stride, so it does not move between
    two renders of the same package.
    """
    with_photos = [chapter for chapter in chapters if chapter.get("images")]
    if not with_photos:
        return []
    stride = max(1, len(with_photos) // OUTRO_COLLAGE_PHOTOS)
    picked = [chapter["images"][0] for chapter in with_photos[::stride]]
    return picked[:OUTRO_COLLAGE_PHOTOS]


def validate_film_package(payload: Any) -> dict[str, Any]:
    """Re-read a film package with the rules that produced it."""
    if not isinstance(payload, dict):
        raise RenderPackageError("Filmpaket ist kein Objekt")
    if payload.get("film_package_version") != FILM_PACKAGE_VERSION:
        raise RenderPackageError("Nicht unterstützte Version des Filmpakets")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise RenderPackageError("Filmpaket ohne Kapitel")
    if len(chapters) > MAX_CHAPTERS:
        raise RenderPackageError("Filmpaket mit zu vielen Kapiteln")
    seen: set[str] = set()
    images = 0
    total = 0
    for chapter in chapters:
        if not isinstance(chapter, dict):
            raise RenderPackageError("Kapitel ist kein Objekt")
        chapter_id = str(chapter.get("chapter_id") or "")
        if not chapter_id or chapter_id in seen:
            raise RenderPackageError("Kapitel ohne eindeutige Kennung")
        seen.add(chapter_id)
        for position, image in enumerate(chapter.get("images") or [], start=1):
            if not isinstance(image, dict):
                raise RenderPackageError("Bildeintrag ist kein Objekt")
            expected = photo_filename(chapter.get("index"), position)
            if image.get("path") != expected:
                raise RenderPackageError(
                    f"Bildpfad {image.get('path')!r} entspricht nicht der Kapitelposition"
                )
            size = image.get("size_bytes")
            if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_FILM_IMAGE_BYTES:
                raise RenderPackageError("Bildeintrag mit ungültiger Größe")
            if len(str(image.get("sha256") or "")) != 64:
                raise RenderPackageError("Bildeintrag ohne gültigen SHA-256")
            images += 1
            total += size
    if images > MAX_FILM_IMAGES or total > MAX_FILM_PACKAGE_BYTES:
        raise RenderPackageError("Filmpaket überschreitet seine Grenzen")
    # The plan travels with the package, so it is checked with it. A
    # renderer that trusted an unchecked plan would happily render a
    # scene type it has no component for.
    validate_scene_plan(payload.get("scene_plan"))
    return payload


__all__ = [
    "FILM_MANIFEST_FILENAME",
    "FILM_PACKAGE_VERSION",
    "FILM_PHOTO_DIR",
    "MAX_CHAPTERS",
    "MAX_FILM_IMAGES",
    "MAX_FILM_IMAGE_BYTES",
    "MAX_FILM_PACKAGE_BYTES",
    "MAX_PHOTOS_PER_CHAPTER",
    "build_film_package",
    "photo_filename",
    "photos_per_chapter",
    "shrink_film_photo",
    "validate_film_package",
]
