"""Where a confirmed character picture lives, and what may reach it.

Same shape as the crew portrait store and for the same reasons, with one
difference that matters: a portrait is *derived* from a photograph and
can always be re-made from it, while a generated illustration cannot. Ask
the model twice and you get two different vehicles. So this folder is not
a cache - losing it means paying again and getting something else.

That is also why writing is a two-step affair. A generated picture lands
as a **candidate**; only when somebody has looked at it does it become
the confirmed asset a film may use. Nothing generated goes straight into
a film: the whole point of the asset is that it stays the same, and that
is a promise only a human can make on the model's behalf.

Filenames are built from a fingerprint of what the picture was made from,
never from anything typed. A changed reference photograph or a changed
description is a different name, so a stale asset can never be served for
a changed request and nothing needs invalidating.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .character_assets import (
    ASSET_FILENAME_RE,
    CHARACTER_KINDS,
    CHARACTER_VARIANTS,
    MAX_ASSET_BYTES,
)

_LOGGER = logging.getLogger(__name__)

CANDIDATE_SUFFIX = ".candidate.png"


class CharacterAssetStore:
    """Confirmed character pictures on disk, addressed by filename only."""

    def __init__(self, root_dir: Path) -> None:
        self._root = Path(root_dir)

    def _path_for(self, filename: str, *, candidate: bool = False) -> Path | None:
        name = str(filename or "")
        if not ASSET_FILENAME_RE.match(name):
            return None
        if candidate:
            name = name[: -len(".png")] + CANDIDATE_SUFFIX
        return self._root / name

    def exists(self, filename: str, *, candidate: bool = False) -> bool:
        path = self._path_for(filename, candidate=candidate)
        return bool(path and path.is_file())

    def read(self, filename: str, *, candidate: bool = False) -> bytes | None:
        """The bytes of one asset - blocking, and only for a valid name."""
        path = self._path_for(filename, candidate=candidate)
        if not path or not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError as err:
            _LOGGER.warning("Figurenbild nicht lesbar: %s", err)
            return None
        return data if 0 < len(data) <= MAX_ASSET_BYTES else None

    def write(self, filename: str, data: bytes, *, candidate: bool = False) -> str:
        """Store one asset, written whole or not at all.

        Returns the *asset* name in both cases, not the name on disk. A
        candidate is the same picture in a waiting room, and the caller
        that later confirms it holds one identity rather than two - the
        alternative is a caller juggling two spellings of the same thing,
        which is how a confirm ends up pointed at the wrong file.
        """
        path = self._path_for(filename, candidate=candidate)
        if not path or not data or len(data) > MAX_ASSET_BYTES:
            return ""
        try:
            self._root.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".part")
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError as err:
            _LOGGER.warning("Figurenbild nicht speicherbar: %s", err)
            return ""
        return str(filename)

    def confirm(self, filename: str) -> bool:
        """Promote a candidate to the asset films are allowed to use.

        The one place a picture becomes usable, and it is a rename rather
        than a re-generation: what somebody approved is byte for byte
        what every later film draws.
        """
        source = self._path_for(filename, candidate=True)
        target = self._path_for(filename)
        if not source or not target or not source.is_file():
            return False
        try:
            source.replace(target)
        except OSError as err:
            _LOGGER.warning("Figurenbild nicht bestätigbar: %s", err)
            return False
        return True

    def discard(self, filename: str) -> bool:
        """Throw a candidate away."""
        path = self._path_for(filename, candidate=True)
        if not path or not path.is_file():
            return False
        try:
            path.unlink()
        except OSError:
            return False
        return True

    def confirmed(self) -> list[dict[str, Any]]:
        """Every confirmed asset, newest name per (kind, variant).

        A film wants one picture per role, and the folder may hold older
        fingerprints from before the reference photograph changed. The
        newest file wins, by modification time - the same rule a person
        would apply looking at the folder.
        """
        try:
            entries = [
                path
                for path in self._root.iterdir()
                if path.is_file() and ASSET_FILENAME_RE.match(path.name)
            ]
        except OSError:
            return []
        best: dict[tuple[str, str], tuple[float, Path]] = {}
        for path in entries:
            kind, variant, _ = path.name.split("-", 2)
            if kind not in CHARACTER_KINDS or variant not in CHARACTER_VARIANTS:
                continue
            try:
                stamp = path.stat().st_mtime
            except OSError:
                continue
            current = best.get((kind, variant))
            if current is None or stamp > current[0]:
                best[(kind, variant)] = (stamp, path)
        found: list[dict[str, Any]] = []
        for (kind, variant), (_, path) in sorted(best.items()):
            found.append({"kind": kind, "variant": variant, "filename": path.name})
        return found

    def prune(self, keep: set[str]) -> int:
        """Delete confirmed assets nobody refers to any more."""
        removed = 0
        try:
            entries = list(self._root.iterdir())
        except OSError:
            return 0
        for path in entries:
            if not path.is_file() or not ASSET_FILENAME_RE.match(path.name):
                continue
            if path.name in keep:
                continue
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed


__all__ = ["CANDIDATE_SUFFIX", "CharacterAssetStore"]
