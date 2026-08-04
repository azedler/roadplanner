"""Bounded local cache for already-downloaded personal photo previews.

The same OneDrive photos are needed again and again: every PDF export,
every video render, every crew portrait. Each fetch costs TWO Microsoft
Graph round trips (resolve the short-lived URL, then download the bytes),
so re-downloading them on every run is pure waste - a full video export
alone can pull well over a hundred images (live question: "Wollen wir die
Bilder dann nicht auch lokal speichern?").

What is cached: only the user's OWN photos, and only the JPEG previews
Graph renders for them. Provider stock imagery is deliberately NOT cached
- Google's terms allow storing the durable reference, never the photo
itself (see destination_gallery_manager._strip_ephemeral_google_url).

The cache is a plain directory of JPEG files, bounded by total size and
pruned least-recently-used. Every operation fails open: a missing,
unreadable or unwritable cache simply means the photo is downloaded, and
the export continues as before.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_CACHE_BYTES = 400 * 1024 * 1024
_MIN_USABLE_BYTES = 256
# Bumped whenever previously written entries can no longer be trusted. v1
# keys did not include the request KIND, so an undecodable HEIC original
# stored under size "large" was served to the thumbnail request that was
# introduced precisely to avoid HEIC - the exports stayed empty even after
# the fix (live report: "Für diese Reise wurden keine Fotos für das Video
# gefunden" on a trip with 261 memories).
_CACHE_NAMESPACE = "v2"

# Formats Pillow can actually decode in every Home Assistant install. HEIC
# is deliberately absent: that is the whole point of asking Graph for a
# rendered preview.
_DECODABLE_MAGIC = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
)


def is_decodable_image(data: bytes) -> bool:
    """True for byte streams Pillow can open without extra codecs.

    Caching an image nobody can decode is worse than not caching at all: it
    turns one failed download into a permanently failing export.
    """
    if len(data) < 12:
        return False
    if data.startswith(_DECODABLE_MAGIC):
        return True
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def cache_key(media_id: str, provider_item_id: str, size: str, kind: str = "") -> str:
    """Stable file name for one photo in one rendered size.

    The provider item id is part of the key so a re-imported photo that
    reuses a Roadplanner media id can never serve stale bytes; the kind
    keeps a rendered thumbnail and the untouched original apart even when
    they are requested under the same size name.
    """
    digest = hashlib.sha256(
        f"{_CACHE_NAMESPACE}|{media_id}|{provider_item_id}|{size}|{kind}".encode("utf-8")
    ).hexdigest()
    return f"{digest}.jpg"


class MediaCache:
    """Least-recently-used file cache for personal photo previews."""

    def __init__(self, root: Path, *, max_bytes: int = DEFAULT_MAX_CACHE_BYTES) -> None:
        self.root = root
        self.max_bytes = max(0, int(max_bytes))

    @property
    def enabled(self) -> bool:
        return self.max_bytes > 0

    def read(self, key: str) -> bytes | None:
        """Return cached bytes and refresh their LRU stamp - blocking."""
        if not self.enabled:
            return None
        path = self.root / key
        try:
            data = path.read_bytes()
        except (OSError, ValueError):
            return None
        if len(data) < _MIN_USABLE_BYTES:
            return None
        try:
            path.touch()
        except OSError:
            pass
        return data

    def write(self, key: str, data: bytes) -> None:
        """Store bytes and prune the cache back under its limit - blocking."""
        if not self.enabled or not data or len(data) < _MIN_USABLE_BYTES:
            return
        if not is_decodable_image(data):
            # An iPhone original (HEIC) downloads fine and then fails in
            # every renderer. Keeping it would make that failure permanent.
            _LOGGER.debug("Media cache skipped an undecodable image (%d bytes)", len(data))
            return
        if len(data) > self.max_bytes:
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary = self.root / f"{key}.part"
            temporary.write_bytes(data)
            temporary.replace(self.root / key)
        except OSError as err:
            _LOGGER.debug("Media cache write failed: %s", type(err).__name__)
            return
        self.prune()

    def prune(self) -> None:
        """Drop the oldest files until the cache fits its limit - blocking."""
        try:
            entries = [
                (path, path.stat()) for path in self.root.glob("*.jpg") if path.is_file()
            ]
        except OSError:
            return
        total = sum(stat.st_size for _, stat in entries)
        if total <= self.max_bytes:
            return
        for path, stat in sorted(entries, key=lambda item: item[1].st_mtime):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= stat.st_size
            if total <= self.max_bytes:
                return

    def stats(self) -> dict[str, Any]:
        """Bounded, non-secret cache metrics for diagnostics."""
        try:
            sizes = [
                path.stat().st_size for path in self.root.glob("*.jpg") if path.is_file()
            ]
        except OSError:
            return {"enabled": self.enabled, "files": 0, "bytes": 0}
        return {
            "enabled": self.enabled,
            "files": len(sizes),
            "bytes": sum(sizes),
            "max_bytes": self.max_bytes,
        }
