"""Which track a film plays under, and how it gets there.

The brief asks for one piece of music over the whole film, chosen by the
user from what is already on the machine. That is a small feature with
one genuinely sharp edge: a filename arriving from outside reaching a
file read. Everything here exists to blunt it.

**The user picks a name, never a path.** The panel offers what is in one
fixed folder; the chosen value is matched back against that listing
before anything is opened. A name that is not in the listing is not a
file - there is no traversal to defend against, because a path is never
built from what was sent.

**The track is copied into the job folder.** The renderer runs in another
container and cannot read Home Assistant's media directory, so the bytes
travel with the job like the photographs do. That also means the render
package is self-contained: re-rendering it later needs nothing that
might have moved.

**No music is a first-class answer.** A film with no track selected, or
whose track has since been deleted, renders exactly as before. Music is
decoration on a journey; it must never be the reason a film fails.

Where the audio is mixed
------------------------

Remotion mixes it into the video itself, so there is no second pipeline
and no ffmpeg encoder in the app image. It also leaves the volume as an
ordinary per-frame value, which is what makes ducking under a video
clip's own sound later a change of one number rather than a change of
architecture.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

MUSIC_DIR = "music"
# Home Assistant's own media folder is where a user already keeps audio,
# so nothing new has to be mounted and no new right is asked for.
DEFAULT_MUSIC_ROOT = "/media/roadplanner_music"
ALLOWED_EXTENSIONS = (".mp3", ".m4a", ".ogg", ".wav", ".flac")
MAX_TRACK_BYTES = 40 * 1024 * 1024
MAX_TRACKS_LISTED = 60
DEFAULT_VOLUME = 0.42


def list_tracks(root: str | Path = DEFAULT_MUSIC_ROOT) -> list[dict[str, Any]]:
    """The audio files in the music folder - names and sizes, blocking.

    Names only. No paths reach the panel, so no path can come back from
    it either.
    """
    folder = Path(root)
    try:
        entries = sorted(folder.iterdir())
    except OSError:
        return []
    tracks: list[dict[str, Any]] = []
    for entry in entries:
        if len(tracks) >= MAX_TRACKS_LISTED:
            break
        try:
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            size = entry.stat().st_size
        except OSError:
            continue
        if not 0 < size <= MAX_TRACK_BYTES:
            continue
        tracks.append({"name": entry.name, "size_bytes": size})
    return tracks


def read_track(name: str, root: str | Path = DEFAULT_MUSIC_ROOT) -> tuple[str, bytes] | None:
    """The bytes of one listed track, or nothing - blocking.

    The name is matched against the listing rather than joined onto the
    folder. That is the whole defence and it is deliberately not a string
    check: a name that is not one of the files actually in that directory
    simply never becomes a path.
    """
    wanted = str(name or "").strip()
    if not wanted:
        return None
    for track in list_tracks(root):
        if track["name"] != wanted:
            continue
        candidate = Path(root) / track["name"]
        try:
            return candidate.suffix.lower(), candidate.read_bytes()
        except OSError as err:
            _LOGGER.warning("Musikdatei nicht lesbar: %s", err)
            return None
    return None


def build_music_package(
    name: str,
    root: str | Path = DEFAULT_MUSIC_ROOT,
    *,
    volume: float = DEFAULT_VOLUME,
) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
    """The film's music section and the file that goes with it.

    Returns ``(None, {})`` for no selection and for a selection that can
    no longer be read - a track deleted since it was chosen must not
    stop somebody's film.
    """
    if not str(name or "").strip():
        return None, {}
    found = read_track(name, root)
    if not found:
        _LOGGER.info("Ausgewählte Musik %r ist nicht verfügbar - der Film läuft ohne", name)
        return None, {}
    extension, blob = found
    path = f"{MUSIC_DIR}/track{extension}"
    return (
        {
            "path": path,
            "size_bytes": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "volume": max(0.0, min(1.0, float(volume))),
            # Shown nowhere in the film; carried so a finished job can say
            # what it played without reaching back into the folder.
            "title": Path(name).stem[:80],
        },
        {path: blob},
    )


MAX_MUSIC_SECTIONS = 4


def build_music_timeline_package(
    timeline: list[dict[str, Any]],
    root: str | Path = DEFAULT_MUSIC_ROOT,
    *,
    volume: float = DEFAULT_VOLUME,
) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
    """A soundtrack in sections, and the files that go with it.

    Same defence as the single track: every entry names a file, the name
    is matched against the folder listing, and a name that is not in the
    listing never becomes a path. The difference is only that there are
    up to four of them and each says when it plays.

    A section whose file has gone missing is dropped rather than fatal -
    the rest of the score still plays, and a gap in the music is a much
    smaller failure than a film that will not render.
    """
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for index, section in enumerate(list(timeline or [])[:MAX_MUSIC_SECTIONS]):
        found = read_track(str((section or {}).get("name") or ""), root)
        if not found:
            _LOGGER.info("Musikabschnitt %s ist nicht verfügbar - er entfällt", index + 1)
            continue
        extension, blob = found
        path = f"{MUSIC_DIR}/section-{index + 1:02d}{extension}"
        files[path] = blob
        entries.append(
            {
                "path": path,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "start_seconds": round(max(0.0, float(section.get("start_seconds") or 0)), 2),
                "seconds": round(max(0.0, float(section.get("seconds") or 0)), 2),
                "fade_in_seconds": round(max(0.0, float(section.get("fade_in_seconds") or 0)), 2),
                "fade_out_seconds": round(
                    max(0.0, float(section.get("fade_out_seconds") or 0)), 2
                ),
            }
        )
    if not entries:
        return None, {}
    return (
        {
            # The first section doubles as the single-track field, so a
            # renderer that only knows about one file still plays music
            # rather than nothing.
            "path": entries[0]["path"],
            "size_bytes": entries[0]["size_bytes"],
            "sha256": entries[0]["sha256"],
            "volume": max(0.0, min(1.0, float(volume))),
            "title": "KI-Musik",
            "sections": entries,
        },
        files,
    )


__all__ = [
    "ALLOWED_EXTENSIONS",
    "DEFAULT_MUSIC_ROOT",
    "DEFAULT_VOLUME",
    "MAX_MUSIC_SECTIONS",
    "MAX_TRACK_BYTES",
    "MUSIC_DIR",
    "build_music_package",
    "build_music_timeline_package",
    "list_tracks",
    "read_track",
]
