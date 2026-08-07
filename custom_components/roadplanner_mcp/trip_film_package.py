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
MAX_PHOTOS_PER_CHAPTER = 3
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


def build_film_package(
    *,
    job_id: str,
    manifest: dict[str, Any],
    photos_by_chapter: dict[str, list[bytes]],
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
            images.append(
                {
                    "path": path,
                    "size_bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
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
                "day_number": facts.get("day_number") or index + 1,
                "distance_km": facts.get("distance_km"),
                "duration_minutes": facts.get("duration_minutes"),
                "stop_count": facts.get("stop_count") or 0,
                # What the DAY has, which is not what this chapter carries.
                # The film shows the gap when the second number is zero.
                "photo_count": facts.get("photo_count") or 0,
                "stops": [
                    str(stop.get("name") or "")[:80]
                    for stop in (source.get("stops") or [])[:6]
                    if str(stop.get("name") or "").strip()
                ],
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
        "chapters": chapters,
        "total_image_bytes": total_bytes,
    }
    return package, files


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
