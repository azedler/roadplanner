"""Who is travelling, prepared for a film and nothing more.

The crew record holds a great deal: notes, generated summaries, source
photographs, portrait URLs, activity flags. A film needs two things from
it - a name to read and a face to show - and this module exists so that
the difference is enforced in one place rather than remembered in
several.

Two rules it makes true by construction.

**Only display names travel.** Not ids, not notes, not anything a model
wrote about somebody. A film that named a person's "role" would be
inventing a fact about a real human being, which is the one thing the
story layer is not allowed to do anywhere else either.

**Portraits travel as bytes, never as links.** The crew portrait route is
guarded by an unguessable filename. That is a bearer secret, not a
session: whoever holds the URL can fetch the face, and it does not
expire. A render package is written into a shared folder and read by
another container, so a link in it would be a copy of that secret
sitting on disk. The picture itself is copied instead - smaller, and it
carries no capability at all.
"""

from __future__ import annotations

import hashlib
import io
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

CREW_DIR = "crew"
# A face on a 1280-pixel canvas is a circle a couple of hundred pixels
# across. Twice that is plenty and keeps the package small.
PORTRAIT_EDGE = 420
PORTRAIT_QUALITY = 78
MAX_PORTRAIT_BYTES = 120 * 1024
# Enough for a family and a dog. Beyond this the line-up stops being
# readable in the seconds the scene runs.
MAX_CREW_MEMBERS = 6
MAX_NAME_LENGTH = 40


def crew_filename(index: int) -> str:
    """The one place a crew filename is built, and it is built from a number.

    Same reasoning as the photo paths: nothing anybody typed reaches a
    path join, because the string never exists.
    """
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("Crewkennung ist keine Ganzzahl")
    if not 0 <= index < MAX_CREW_MEMBERS:
        raise ValueError("Crewkennung liegt außerhalb des erlaubten Bereichs")
    return f"{CREW_DIR}/member-{index:02d}.jpg"


def shrink_portrait(data: bytes) -> bytes | None:
    """Re-encode a stored portrait at film size, stripped of metadata.

    Re-encoded from pixels rather than copied: a stored portrait came
    from somebody's camera roll, and a camera roll photograph carries
    where and when it was taken. None of that belongs in a package that
    is written to a shared folder.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow ships with Home Assistant
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((PORTRAIT_EDGE, PORTRAIT_EDGE), Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=PORTRAIT_QUALITY, optimize=True)
    except Exception as err:  # noqa: BLE001 - a broken portrait is skipped
        _LOGGER.debug("Crewbild für den Film nicht verwendbar: %s", type(err).__name__)
        return None
    shrunk = buffer.getvalue()
    return shrunk if len(shrunk) <= MAX_PORTRAIT_BYTES else None


def build_crew_package(members: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Turn (name, portrait bytes) pairs into the film's crew section.

    ``members`` is a list of ``{"name", "portrait"}`` where ``portrait``
    is the already-read bytes of a locally stored portrait, or None. A
    member without a usable picture still travels: the film shows the
    name, because leaving somebody out of the crew because their photo
    failed to open would be the worst possible way to handle that.
    """
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for member in members[:MAX_CREW_MEMBERS]:
        name = " ".join(str(member.get("name") or "").split())[:MAX_NAME_LENGTH]
        if not name:
            continue
        index = len(entries)
        entry: dict[str, Any] = {"name": name, "path": "", "sha256": "", "size_bytes": 0}
        raw = member.get("portrait")
        blob = shrink_portrait(raw) if isinstance(raw, (bytes, bytearray)) and raw else None
        if blob:
            path = crew_filename(index)
            files[path] = blob
            entry.update(
                {
                    "path": path,
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "size_bytes": len(blob),
                }
            )
        entries.append(entry)
    if not entries:
        return {}, {}
    return {"members": entries}, files


def validate_crew(payload: Any) -> dict[str, Any] | None:
    """Read a crew section with the rules that produced it."""
    if payload in (None, {}):
        return None
    if not isinstance(payload, dict):
        raise ValueError("Crewangaben sind kein Objekt")
    members = payload.get("members")
    if not isinstance(members, list) or not members:
        raise ValueError("Crewangaben ohne Mitglieder")
    if len(members) > MAX_CREW_MEMBERS:
        raise ValueError("Zu viele Crewmitglieder für den Film")
    for index, member in enumerate(members):
        if not isinstance(member, dict) or not str(member.get("name") or "").strip():
            raise ValueError("Crewmitglied ohne Namen")
        path = str(member.get("path") or "")
        if path and path != crew_filename(index):
            raise ValueError("Crewbild an der falschen Stelle")
        # The check that matters most: nothing here may be a link. A URL
        # in this section would be a copy of a bearer secret written to a
        # shared folder.
        for value in member.values():
            text = str(value)
            if "://" in text or text.startswith("/api/"):
                raise ValueError("Crewangaben dürfen keine Adressen enthalten")
    return payload


__all__ = [
    "CREW_DIR",
    "MAX_CREW_MEMBERS",
    "build_crew_package",
    "crew_filename",
    "shrink_portrait",
    "validate_crew",
]
