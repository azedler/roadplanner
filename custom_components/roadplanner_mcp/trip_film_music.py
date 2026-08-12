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


class MusicPackageError(ValueError):
    """A soundtrack that cannot be handed over as it stands."""


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


# As many sections as the planner can ever produce, and not one fewer.
#
# This was 4 while the planner had already been raised to 8, and the two
# numbers never had to meet: the builder below simply SLICED the list.
# A twelve-minute film plans five sections, the fifth was dropped here,
# and the film went quiet for its last two and a half minutes - after
# somebody had paid for that fifth generation. A fifteen-minute one lost
# five minutes.
#
# The same number lives in the renderer's protocol. A test reads all
# three - here, there, and the planner's own ceiling - because two copies
# that agree with each other while disagreeing with the planner is
# exactly the shape this bug had.
MAX_MUSIC_SECTIONS = 8


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
    wanted = list(timeline or [])
    if len(wanted) > MAX_MUSIC_SECTIONS:
        # Refused rather than sliced. Dropping the tail of a soundtrack
        # produces a film that is silent at the end and says nothing
        # about why - and the sections that were dropped had already
        # been generated and paid for.
        raise MusicPackageError(
            f"{len(wanted)} Musikabschnitte - der Renderer nimmt höchstens "
            f"{MAX_MUSIC_SECTIONS} entgegen"
        )
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for index, section in enumerate(wanted):
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


def build_music_variant_package(
    variant: dict[str, Any],
    root: str | Path = DEFAULT_MUSIC_ROOT,
    *,
    volume: float = DEFAULT_VOLUME,
    target_lufs: float | None = None,
    true_peak_dbtp: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, bytes]]:
    """One fassung of the architecture comparison, as a mux package.

    The difference to a soundtrack is that these layers PLAY AT ONCE
    rather than one after another, so each carries its own level: an
    atmosphere under a piece of music is not the same thing as the two
    of them at one volume. The mux sums them and knows nothing about
    architectures.

    A layer whose file has gone missing is fatal here, unlike in a
    soundtrack. A soundtrack with a gap still plays; a layered fassung
    missing its bed IS the single-score fassung, and it would be
    compared against itself while looking like a result.
    """
    layers = list((variant or {}).get("layers") or [])
    if not layers:
        return None, {}
    if len(layers) > MAX_MUSIC_SECTIONS:
        raise MusicPackageError(
            f"{len(layers)} Ebenen - der Renderer nimmt höchstens "
            f"{MAX_MUSIC_SECTIONS} entgegen"
        )
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    for index, layer in enumerate(layers):
        name = str((layer or {}).get("cached_name") or "")
        found = read_track(name, root)
        if not found:
            raise MusicPackageError(
                f"Die Ebene „{(layer or {}).get('role') or index + 1}“ fehlt - "
                "diese Fassung wäre eine andere als die geplante"
            )
        extension, blob = found
        path = f"{MUSIC_DIR}/layer-{index + 1:02d}{extension}"
        files[path] = blob
        base = max(0.0, min(1.0, float(volume)))
        entries.append(
            {
                "path": path,
                "size_bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "role": str((layer or {}).get("role") or ""),
                # The layer's share of the mix, already multiplied out -
                # the renderer places what it is told and decides
                # nothing, which is the same rule the soundtrack
                # timeline follows.
                "volume": round(
                    max(0.0, min(1.0, base * float((layer or {}).get("gain") or 1.0))), 4
                ),
                "start_seconds": round(max(0.0, float(layer.get("start_seconds") or 0)), 2),
                "seconds": round(max(0.0, float(layer.get("seconds") or 0)), 2),
                "fade_in_seconds": round(max(0.0, float(layer.get("fade_in_seconds") or 0)), 2),
                "fade_out_seconds": round(max(0.0, float(layer.get("fade_out_seconds") or 0)), 2),
            }
        )
    package: dict[str, Any] = {
        "path": entries[0]["path"],
        "size_bytes": entries[0]["size_bytes"],
        "sha256": entries[0]["sha256"],
        "volume": max(0.0, min(1.0, float(volume))),
        "title": str((variant or {}).get("label") or "Musikvergleich")[:80],
        "sections": entries,
        "variant": str((variant or {}).get("variant") or ""),
    }
    if target_lufs is not None:
        package["target_lufs"] = float(target_lufs)
    if true_peak_dbtp is not None:
        package["true_peak_dbtp"] = float(true_peak_dbtp)
    return package, files


__all__ = [
    "ALLOWED_EXTENSIONS",
    "build_music_variant_package",
    "DEFAULT_MUSIC_ROOT",
    "DEFAULT_VOLUME",
    "MAX_MUSIC_SECTIONS",
    "MAX_TRACK_BYTES",
    "MUSIC_DIR",
    "MusicPackageError",
    "build_music_package",
    "build_music_timeline_package",
    "list_tracks",
    "read_track",
]
