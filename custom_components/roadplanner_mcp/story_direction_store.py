"""Where one editing pass is kept, so it is paid for once.

A Gemini pass over a three-week trip is five calls. Keeping the result
only in memory would mean a Home Assistant restart quietly buys it again,
and a panel that reloads on every phone unlock would buy it several times
a day. So it goes on disk, beside the trip, in its own sidecar.

What is stored is the *edit*, not the manifest. The manifest stays
derived and is rebuilt from the roadbook every time; this file holds only
what the model added - titles, texts, captions, weights, an arc - keyed
by the ``story_context_hash`` of the material it was written about. That
key is what makes reuse safe: if the trip's material has moved, the hash
no longer matches and the edit is stale by construction rather than by
guesswork.

It is deliberately not a version history. One current edit per trip,
overwritten when a new one is made, deleted when somebody discards it.
Keeping every pass would need a UI to choose between them, and nobody
asked to choose between two machine drafts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any

from .roadplanner import StorageError, validate_identifier

STORE_SCHEMA_VERSION = 1
_FILENAME = "story_direction.json"
# A whole trip's edited prose. Generous, but not unbounded: a file larger
# than this is not an edit, it is a mistake or a manipulation.
MAX_DIRECTION_BYTES = 512 * 1024


class StoryDirectionStore:
    """Synchronous per-trip storage for the story director's output."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.trips_dir = root_dir / "trips"
        self._lock = RLock()

    def initialize(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.trips_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, trip_id: str) -> Path:
        return self.trips_dir / validate_identifier(trip_id, "trip_id") / _FILENAME

    def load(self, trip_id: str) -> dict[str, Any] | None:
        """The stored edit, or None. A broken file is None, not an error.

        A story is decoration on top of a trip. If this file cannot be
        read, the manifest still describes the journey correctly using the
        composed sentences - so refusing to load the trip over it would
        trade a cosmetic loss for a real one.
        """
        path = self._path(trip_id)
        with self._lock:
            try:
                if path.stat().st_size > MAX_DIRECTION_BYTES:
                    return None
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return None
        if not isinstance(raw, dict) or raw.get("trip_id") != trip_id:
            return None
        if int(raw.get("schema_version") or 0) != STORE_SCHEMA_VERSION:
            return None
        direction = raw.get("direction")
        return direction if isinstance(direction, dict) else None

    def write(self, trip_id: str, direction: dict[str, Any]) -> None:
        path = self._path(trip_id)
        payload = {
            "schema_version": STORE_SCHEMA_VERSION,
            "trip_id": validate_identifier(trip_id, "trip_id"),
            "direction": direction,
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_DIRECTION_BYTES:
            raise StorageError("Die Reiseredaktion ist zu groß zum Speichern")
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path: Path | None = None
            try:
                with NamedTemporaryFile(
                    mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
                ) as handle:
                    temp_path = Path(handle.name)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_path, path)
            except OSError as err:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                raise StorageError(
                    f"Die Reiseredaktion konnte nicht gespeichert werden: {path}"
                ) from err

    def delete(self, trip_id: str) -> bool:
        """Throw the edit away. The trip is untouched by design."""
        path = self._path(trip_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            except OSError as err:
                raise StorageError("Die Reiseredaktion konnte nicht gelöscht werden") from err
        return True


__all__ = ["MAX_DIRECTION_BYTES", "STORE_SCHEMA_VERSION", "StoryDirectionStore"]
