"""Permanently stored, already-cropped portraits for crew members and vehicles.

Live request: "Ich fände es auch sinnvoll wenn die Bilder der Crew permanent
lokal gespeichert werden."

The portrait used to be a live reference into OneDrive: the panel resolved
the media id to a temporary Graph thumbnail URL on every render, and the PDF
downloaded it again for every export. That makes the faces of the crew
depend on a cloud account staying reachable, on a token still being valid,
and on the photo never being moved or deleted. A face you have already
chosen should not be able to disappear.

Two consequences beyond durability:

- The stored file is **already cropped**. The crop maths therefore stops
  existing anywhere in rendering, which is what made the shown region drift
  from the picked one in the first place (live report: "Bildausschnitt und
  tatsächlicher Ausschnitt weichen etwas ab").
- The file name carries the source media id AND the crop, so changing
  either produces a different name. A stale portrait can never be served
  for a changed selection, and no cache needs invalidating.

Nothing here is authoritative: the crew record keeps the media id and crop,
this is only a rendering of them. A missing file is regenerated on demand.
"""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
import re
from typing import Any

_LOGGER = logging.getLogger(__name__)

PORTRAIT_FILENAME_RE = re.compile(r"^[0-9a-f]{40}\.jpg$")
# Big enough for the PDF's portrait frame, small enough to keep the folder
# trivial in size even for a large crew.
PORTRAIT_MAX_EDGE = 900
PORTRAIT_QUALITY = 88


def portrait_key(
    entity_id: str, media_id: str, crop: Any, *, kind: str = "person"
) -> str:
    """Stable name for one (entity, source photo, crop) combination.

    The crop is part of the identity on purpose: re-crop the same photo and
    the portrait is a different file, so nothing can serve the old region.
    """
    if not entity_id or not media_id:
        return ""
    box = ""
    if isinstance(crop, dict):
        try:
            box = ",".join(
                f"{float(crop[axis]):.4f}" for axis in ("x", "y", "w", "h")
            )
        except (KeyError, TypeError, ValueError):
            box = ""
    digest = hashlib.sha1(
        f"{kind}|{entity_id}|{media_id}|{box}".encode("utf-8")
    ).hexdigest()
    return f"{digest}.jpg"


def portrait_url(filename: str) -> str:
    return f"/api/roadplanner/crew_portrait/{filename}" if filename else ""


def render_portrait(photo: bytes, crop: Any) -> bytes | None:
    """Crop and shrink one photo into a stored portrait - blocking.

    Executor only. Returns None when the bytes cannot be opened, which is a
    reason to keep the old file rather than to fail a save.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard requirement
        return None
    try:
        image = Image.open(io.BytesIO(photo))
        image.load()
        image = image.convert("RGB")
    except Exception as err:  # noqa: BLE001 - an unreadable photo is not fatal
        _LOGGER.debug("Crew portrait could not be decoded: %s", type(err).__name__)
        return None
    width, height = image.size
    if width < 8 or height < 8:
        return None
    if isinstance(crop, dict):
        try:
            box = {axis: float(crop[axis]) for axis in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            box = {}
        if box and box.get("w", 0) > 0.02 and box.get("h", 0) > 0.02:
            left = int(max(0.0, min(box["x"], 1.0)) * width)
            top = int(max(0.0, min(box["y"], 1.0)) * height)
            right = min(width, left + max(1, int(box["w"] * width)))
            bottom = min(height, top + max(1, int(box["h"] * height)))
            if right - left >= 16 and bottom - top >= 16:
                image = image.crop((left, top, right, bottom))
    longest = max(image.size)
    if longest > PORTRAIT_MAX_EDGE:
        scale = PORTRAIT_MAX_EDGE / longest
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        )
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=PORTRAIT_QUALITY)
    return buffer.getvalue()


class CrewPortraitStore:
    """Read/write the portrait folder. All methods block - executor only."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def path_for(self, filename: str) -> Path | None:
        """Resolve a REQUESTED name, refusing anything that is not ours.

        The name comes off an HTTP URL, so it is validated against the
        generated shape rather than trusted - no traversal, no arbitrary
        file read.
        """
        if not PORTRAIT_FILENAME_RE.match(str(filename or "")):
            return None
        candidate = self.root_dir / filename
        try:
            candidate.relative_to(self.root_dir)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def exists(self, filename: str) -> bool:
        if not PORTRAIT_FILENAME_RE.match(str(filename or "")):
            return False
        return (self.root_dir / filename).is_file()

    def read(self, filename: str) -> bytes | None:
        """The stored bytes of one portrait, for a caller that needs a copy.

        The film needs the picture itself rather than a link to it: the
        HTTP route is guarded only by the unguessable name, which is a
        bearer secret, and a render package written into a shared folder
        must not carry one. The name is validated against the generated
        shape here for the same reason `path_for` does - it may have come
        from a payload rather than from this store.
        """
        if not PORTRAIT_FILENAME_RE.match(str(filename or "")):
            return None
        try:
            return (self.root_dir / filename).read_bytes()
        except OSError:
            return None

    def write(self, filename: str, data: bytes) -> str:
        if not PORTRAIT_FILENAME_RE.match(str(filename or "")) or not data:
            return ""
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            (self.root_dir / filename).write_bytes(data)
        except OSError as err:
            _LOGGER.warning("Crew portrait could not be stored: %s", err)
            return ""
        return filename

    def prune(self, keep: set[str]) -> int:
        """Delete portraits no crew member refers to any more."""
        removed = 0
        try:
            existing = list(self.root_dir.glob("*.jpg"))
        except OSError:
            return 0
        for path in existing:
            if path.name in keep:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
