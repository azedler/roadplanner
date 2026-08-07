"""Build the smallest package that can carry one real trip day to the app.

This is the module where real travel data leaves Roadplanner's own
process, so its job is subtraction. A trip day in Roadplanner carries far
more than a video needs: internal ids, coordinates, provider references,
media records, planning state. None of that has any business crossing
into ``/share``, which is a **trust channel and not a security boundary** -
every app with the same mount can read it.

What crosses is therefore decided here, explicitly, and nothing else:

- the day's date, title and stored summary;
- the names of its stops;
- distance and driving time **only if Roadplanner already has them
  locally** - nothing is looked up to fill this package;
- between one and five photo copies, downscaled and stripped of metadata.

Three properties are worth stating because they are what makes the package
safe to hand over:

**Photos are re-encoded, never copied.** The bytes that leave are produced
by decoding the original and encoding a new JPEG from the pixels. That is
what removes EXIF - including the GPS position a phone writes into every
holiday photo, which would otherwise travel into a shared directory with
no way to take it back. Re-encoding also removes the orientation tag, so
the rotation it described is applied to the pixels first; skipping that
step is how photos end up sideways.

**No filename in the package reaches a path.** Images are numbered, and
the reader builds ``photo-<n>.jpg`` from the number itself. There is no
attacker-controlled string anywhere near a path join, because the string
never exists.

**Every limit is a number here, not a hope.** Count, per-image size, total
size, text lengths. A package that cannot be built within them is an error
before anything is written, not a surprise on the other side.

The module is pure: it takes data and returns data. Pillow work is
CPU-bound and belongs in an executor, which is the caller's job.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

PACKAGE_VERSION = 1
PACKAGE_FILENAME = "package.json"

# --- limits -------------------------------------------------------------

# Five photos at ~1280 px carry a day without turning the package into a
# media library. Fewer is fine and reported; zero is refused, because a
# photo video with no photos does not demonstrate the thing being proven.
MAX_IMAGES = 5
MIN_IMAGES = 1
# The video is 1280x720, so a longer edge than this is detail nobody will
# ever see - it would only cost bytes and encoding time.
IMAGE_MAX_EDGE = 1280
JPEG_QUALITY = 82
MAX_IMAGE_BYTES = 400 * 1024
MAX_PACKAGE_BYTES = 3 * 1024 * 1024

MAX_STOPS = 12
MAX_TITLE_LENGTH = 120
MAX_STOP_NAME_LENGTH = 80
MAX_SUMMARY_LENGTH = 400

_WHITESPACE_RE = re.compile(r"\s+")


class RenderPackageError(ValueError):
    """The package cannot be built, and no part of it may be written."""


# --- text ---------------------------------------------------------------


def clean_line(value: Any, *, limit: int) -> str:
    """One line, bounded. Control characters become spaces, not boxes."""
    text = _WHITESPACE_RE.sub(" ", str(value or "").replace("\x00", " ")).strip()
    return text[:limit]


# --- photos -------------------------------------------------------------


def metadata_markers(data: bytes) -> list[str]:
    """Name the metadata segments a JPEG still carries.

    Used by the builder to refuse a photo it failed to strip, and by the
    tests to prove the stripping is real rather than assumed. Walking the
    segment table is the only way to answer this from the bytes; asking
    Pillow would only report what Pillow chose to parse.
    """
    if data[:2] != b"\xff\xd8":
        return []
    found: list[str] = []
    index = 2
    names = {
        0xE1: "APP1 (Exif/XMP)",
        0xE2: "APP2 (ICC)",
        0xED: "APP13 (IPTC)",
        0xEE: "APP14",
        0xFE: "Kommentar",
    }
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            break
        marker = data[index + 1]
        # Start of scan: everything after this is entropy-coded image data.
        if marker == 0xDA:
            break
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2:
            break
        if marker in names:
            found.append(names[marker])
        index += 2 + length
    return found


def shrink_photo(data: bytes) -> bytes | None:
    """Decode, straighten, downscale, re-encode. Metadata does not survive.

    Returns ``None`` for anything that cannot be decoded - a photo that
    Home Assistant cannot open is simply left out of the package, exactly
    as the PDF and video exports already handle it. A broken photo must
    never sink a whole export.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow is a manifest requirement
        _LOGGER.warning("Pillow fehlt - für das Renderpaket können keine Bilder verkleinert werden")
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        # Applied to the pixels BEFORE the tag is dropped. Dropping it
        # first would leave every portrait photo lying on its side.
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)
        buffer = io.BytesIO()
        # A new file from pixel data: no exif, no icc_profile, no comment.
        image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    except Exception as err:  # noqa: BLE001 - any decode failure just skips
        _LOGGER.debug("Foto fürs Renderpaket nicht verwendbar: %s", type(err).__name__)
        return None
    shrunk = buffer.getvalue()
    if len(shrunk) > MAX_IMAGE_BYTES:
        # Re-encoding a busy photo can stay above the cap at this quality.
        # One lower-quality attempt, then it is left out rather than
        # blowing the package budget.
        buffer = io.BytesIO()
        try:
            image.save(buffer, format="JPEG", quality=68, optimize=True)
        except Exception:  # noqa: BLE001
            return None
        shrunk = buffer.getvalue()
        if len(shrunk) > MAX_IMAGE_BYTES:
            _LOGGER.debug("Foto bleibt über der Größengrenze und wird ausgelassen")
            return None
    remaining = metadata_markers(shrunk)
    if remaining:
        # Refusing beats shipping: the whole point of this step is that
        # nothing describing where and when the photo was taken leaves.
        _LOGGER.warning("Foto trägt nach dem Neucodieren noch %s", ", ".join(remaining))
        return None
    return shrunk


# --- package ------------------------------------------------------------


def build_package(
    *,
    job_id: str,
    trip_title: str,
    day: dict[str, Any],
    stops: list[dict[str, Any]],
    photos: list[bytes],
    day_number: int | None = None,
    day_count: int | None = None,
) -> tuple[dict[str, Any], list[bytes]]:
    """Assemble the manifest and the image bytes that belong to it.

    Returns ``(package, images)`` where ``images[i]`` is the file the
    manifest's ``images[i]`` describes. They are returned together and
    never separately, so the two cannot drift apart between here and the
    write.
    """
    title = clean_line(day.get("title"), limit=MAX_TITLE_LENGTH)
    date = clean_line(day.get("date"), limit=40)
    day_id = clean_line(day.get("id"), limit=80)
    if not day_id:
        raise RenderPackageError("Der Reisetag hat keine ID")

    prepared: list[bytes] = []
    for photo in photos:
        if len(prepared) >= MAX_IMAGES:
            break
        shrunk = shrink_photo(photo)
        if shrunk:
            prepared.append(shrunk)
    if len(prepared) < MIN_IMAGES:
        raise RenderPackageError(
            "Für diesen Tag konnte kein verwendbares Foto vorbereitet werden - "
            "ohne Bild ergibt der Mini-Export keinen Nachweis"
        )

    images = [
        {
            # Deliberately no filename: the reader derives photo-<n>.jpg
            # from this number, so no string from here reaches a path.
            "index": position,
            "size_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        for position, blob in enumerate(prepared, start=1)
    ]

    package = {
        "package_version": PACKAGE_VERSION,
        "job_id": job_id,
        "trip_title": clean_line(trip_title, limit=MAX_TITLE_LENGTH),
        "day": {
            "day_id": day_id,
            "date": date,
            "title": title,
            "summary": clean_line(_stored_summary(day), limit=MAX_SUMMARY_LENGTH),
            "number": int(day_number) if day_number else None,
            "count": int(day_count) if day_count else None,
            # Only what Roadplanner already holds. Nothing is fetched, and
            # a day without these values simply shows none.
            "distance_km": _optional_number(day.get("distance_km")),
            "duration_minutes": _optional_number(
                day.get("drive_minutes", day.get("duration_minutes"))
            ),
        },
        "stops": [
            {
                "name": name,
                "time": clean_line(stop.get("arrival_time"), limit=10),
            }
            for stop in stops[:MAX_STOPS]
            if isinstance(stop, dict)
            and (name := clean_line(stop.get("name"), limit=MAX_STOP_NAME_LENGTH))
        ],
        "images": images,
    }
    total = sum(len(blob) for blob in prepared)
    if total > MAX_PACKAGE_BYTES:
        raise RenderPackageError(
            f"Das Renderpaket ist mit {total // 1024} kB zu groß"
        )
    package["total_image_bytes"] = total
    return package, prepared


def _stored_summary(day: dict[str, Any]) -> str:
    """The summary a day already carries, or nothing.

    Nothing is generated here. Writing a summary is a provider call that
    belongs to the summary service; a mini export that quietly triggered
    one would be doing work nobody asked it for.
    """
    details = day.get("details")
    if not isinstance(details, dict):
        return ""
    for key in ("generated_summary", "summary"):
        text = clean_line(details.get(key), limit=MAX_SUMMARY_LENGTH)
        if text:
            return text
    return ""


def _optional_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return round(number, 1)


def image_filename(index: Any) -> str:
    """The one place an image filename is built, from a number only."""
    try:
        position = int(index)
    except (TypeError, ValueError) as err:
        raise RenderPackageError("Bildindex ist keine Zahl") from err
    if not 1 <= position <= MAX_IMAGES:
        raise RenderPackageError("Bildindex liegt außerhalb des erlaubten Bereichs")
    return f"photo-{position}.jpg"


def validate_package(payload: Any) -> dict[str, Any]:
    """Re-read a package with the rules that produced it.

    Roadplanner checks what it is about to hand over; the app validates
    again with its own implementation. Two independent readers of one
    document is the same arrangement the job protocol already uses, and
    for the same reason: a disagreement shows up as a failing test rather
    than being absorbed by shared code.
    """
    if not isinstance(payload, dict):
        raise RenderPackageError("Renderpaket ist kein Objekt")
    if payload.get("package_version") != PACKAGE_VERSION:
        raise RenderPackageError("Nicht unterstützte Paketversion")
    day = payload.get("day")
    if not isinstance(day, dict) or not clean_line(day.get("day_id"), limit=80):
        raise RenderPackageError("Renderpaket ohne Tagesangaben")
    images = payload.get("images")
    if not isinstance(images, list) or not MIN_IMAGES <= len(images) <= MAX_IMAGES:
        raise RenderPackageError("Renderpaket enthält keine gültige Bildliste")
    seen: set[int] = set()
    total = 0
    for entry in images:
        if not isinstance(entry, dict):
            raise RenderPackageError("Bildeintrag ist kein Objekt")
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise RenderPackageError("Bildeintrag ohne Index")
        image_filename(index)
        if index in seen:
            raise RenderPackageError("Doppelter Bildindex")
        seen.add(index)
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_IMAGE_BYTES:
            raise RenderPackageError("Bildeintrag mit ungültiger Größe")
        if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or "")):
            raise RenderPackageError("Bildeintrag ohne gültigen SHA-256")
        total += size
    if total > MAX_PACKAGE_BYTES:
        raise RenderPackageError("Renderpaket überschreitet die Größengrenze")
    stops = payload.get("stops")
    if not isinstance(stops, list) or len(stops) > MAX_STOPS:
        raise RenderPackageError("Renderpaket enthält keine gültige Stoppliste")
    return payload


__all__ = [
    "MAX_IMAGES",
    "MAX_IMAGE_BYTES",
    "MAX_PACKAGE_BYTES",
    "MIN_IMAGES",
    "PACKAGE_FILENAME",
    "PACKAGE_VERSION",
    "RenderPackageError",
    "build_package",
    "clean_line",
    "image_filename",
    "metadata_markers",
    "shrink_photo",
    "validate_package",
]
