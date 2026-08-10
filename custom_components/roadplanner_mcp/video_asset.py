"""A video as the library stores it, beside the photographs.

The film already knows what a photograph is. A video is the same kind of
thing - something somebody recorded on a day of the journey - and the
whole point of this module is that it stays the same kind of thing: one
media library, one assignment to a day, one curation, one scene plan.
A parallel "videos" world would mean every rule about days, stops and
story coverage written twice, and the second copy drifting.

What is genuinely different is that a video has an **inside**. A
photograph is chosen or not; a video is four minutes of which six
seconds are worth watching. So an asset carries two analysis states and
a list of chosen segments, and those are the only concepts a photograph
does not have.

Two accounts, from the start
----------------------------

Not because a second OneDrive account exists today, but because
retrofitting an account onto stored records is a migration and adding a
field now is a field. `source` and `source_account_id` are always
written, even when there is exactly one account and its id is a
constant - a shape that can only express one account is the thing that
has to be undone later.

What this module does not do
----------------------------

It does not download, transcode, analyse or call anything. It normalises
what a provider listing says into the shape the library stores, and it
refuses what cannot be stored. All of that is testable without a
network, a file or a provider.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

MEDIA_TYPE_VIDEO = "video"

# What the technical pass has done with this asset. Kept apart from the
# semantic one because they fail for different reasons and cost
# different things: the technical pass is local and free, the semantic
# pass is a paid call to a model.
STATE_PENDING = "pending"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_SKIPPED = "skipped"
ANALYSIS_STATES = (STATE_PENDING, STATE_DONE, STATE_FAILED, STATE_SKIPPED)

SOURCE_ONEDRIVE = "onedrive"
# The one account there is today. A constant rather than an absence, so
# that a second one is a new value and not a new field.
DEFAULT_ACCOUNT_ID = "default"

# A clip longer than this is not one moment, it is a recording somebody
# forgot to stop. It still travels; it is simply expected to be cut.
LONG_CLIP_SECONDS = 90.0
# Beyond this the file is refused: no family moment is an hour long, and
# a download of that size on a Home Assistant box is a different kind of
# problem than a bad clip.
MAX_DURATION_SECONDS = 3600.0
MAX_SOURCE_BYTES = 4 * 1024 * 1024 * 1024

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".3gp", ".webm", ".mts")
_ID_RE = re.compile(r"^[A-Za-z0-9._!~%$@()+,;=:\-]{1,256}$")


class VideoAssetError(ValueError):
    """A listing entry that cannot become an asset, with the reason."""


def is_video(name: str, mime_type: str = "") -> bool:
    """Whether a library entry is a video at all.

    Both signals, because neither is reliable alone: a provider may
    report `application/octet-stream` for a perfectly good `.mov`, and a
    file called `urlaub.mp4.txt` is not a video whatever its name
    suggests.
    """
    mime = str(mime_type or "").casefold()
    if mime.startswith("video/"):
        return True
    lowered = str(name or "").casefold()
    return any(lowered.endswith(extension) for extension in _VIDEO_EXTENSIONS)


def content_key(*parts: Any) -> str:
    """A stable id for the same file seen again.

    Derived from what the provider says about the CONTENT - its own hash
    where there is one, otherwise size and capture time - so that the
    same recording re-listed after a folder move is the same asset
    rather than a second one that pays for its own analysis.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part if part is not None else "").encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()[:32]


def orientation_of(width: Any, height: Any, rotation: Any = 0) -> str:
    """Portrait or landscape, after the rotation the camera recorded.

    A phone writes a landscape frame plus "rotate 90", and a pipeline
    that ignores that produces a film with a sideways clip in it - which
    is exactly the sort of defect that only shows up in the finished
    video.
    """
    try:
        w = int(width or 0)
        h = int(height or 0)
        turn = abs(int(rotation or 0)) % 180
    except (TypeError, ValueError):
        return "landscape"
    if turn == 90:
        w, h = h, w
    if not w or not h:
        return "landscape"
    return "portrait" if h > w else "landscape"


def build_asset(
    item: dict[str, Any],
    *,
    source: str = SOURCE_ONEDRIVE,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> dict[str, Any]:
    """One provider listing entry as the library stores it.

    Raises `VideoAssetError` for anything that cannot be stored, because
    a half-formed asset in the library is worse than a skipped file: it
    would be counted, shown, and eventually sent somewhere.
    """
    if not isinstance(item, dict):
        raise VideoAssetError("Eintrag ist kein Objekt")
    item_id = str(item.get("id") or "").strip()
    if not item_id or not _ID_RE.match(item_id):
        raise VideoAssetError("Eintrag ohne brauchbare Quell-ID")
    name = " ".join(str(item.get("name") or "").split())[:200]
    if not is_video(name, str(item.get("mime_type") or item.get("mimeType") or "")):
        raise VideoAssetError("Eintrag ist kein Video")

    duration = _number(item.get("duration") or item.get("duration_seconds"))
    if duration > MAX_DURATION_SECONDS:
        raise VideoAssetError("Video ist zu lang für die Verarbeitung")
    size_bytes = int(_number(item.get("size") or item.get("size_bytes")))
    if size_bytes > MAX_SOURCE_BYTES:
        raise VideoAssetError("Videodatei ist zu groß")

    width = int(_number(item.get("width")))
    height = int(_number(item.get("height")))
    rotation = int(_number(item.get("rotation")))
    provider_hash = str(item.get("content_hash") or item.get("sha256") or "").strip()
    capture_time = str(item.get("capture_time") or item.get("taken_at") or "").strip()

    return {
        "media_type": MEDIA_TYPE_VIDEO,
        # Always both, even with one account. A shape that can only
        # express one account is what has to be undone later.
        "source": str(source or SOURCE_ONEDRIVE),
        "source_account_id": str(account_id or DEFAULT_ACCOUNT_ID),
        "source_item_id": item_id,
        "name": name,
        # The provider's own hash when it has one, because it survives a
        # move; otherwise something derived from the content's shape.
        "content_hash": provider_hash
        or content_key(name, size_bytes, capture_time, duration),
        "size_bytes": size_bytes,
        "capture_time": capture_time,
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "rotation": rotation % 360,
        "orientation": orientation_of(width, height, rotation),
        # Assignment happens the same way it does for a photograph -
        # named here so nothing has to guess whether the field exists.
        "day_id": str(item.get("day_id") or ""),
        "stop_id": str(item.get("stop_id") or ""),
        # The two states a photograph does not have. Separate because
        # one is local and free and the other is a paid call, and a
        # single state could not say "looked at locally, never sent".
        "technical_analysis_state": STATE_PENDING,
        "semantic_analysis_state": STATE_PENDING,
        "selected_segments": [],
        # What made this asset, so a changed pipeline can be told from a
        # changed file without re-deriving either.
        "asset_version": ASSET_VERSION,
        "needs_segmentation": duration > LONG_CLIP_SECONDS,
    }


ASSET_VERSION = 1


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 and number == number else 0.0


def cache_key(asset: dict[str, Any], *, model: str, schema_version: int, sampling: str = "") -> str:
    """What an analysis of this asset was keyed on.

    Content, model, schema and sampling - the four things that change
    the answer. Anything else moving (a rename, a new folder, another
    render) must not, or every film would pay again.

    "Content" has to be read from the record the LIBRARY actually stores,
    not from the shape this module builds. Those are two different dicts:
    a video that arrived through the media library carries `file_hash`
    and `provider_item_id` and no `content_hash` at all. Reading only the
    field this module invents gave every such video the same key - one
    cached answer for the whole camera roll, which is worse than no cache
    because it is silent and wrong.
    """
    asset = asset if isinstance(asset, dict) else {}
    identity = (
        asset.get("content_hash")
        or asset.get("file_hash")
        or asset.get("provider_item_id")
        or asset.get("id")
    )
    if not identity:
        raise VideoAssetError("Video ohne Identität kann nicht zwischengespeichert werden")
    return content_key(identity, model, schema_version, sampling)


__all__ = [
    "ANALYSIS_STATES",
    "ASSET_VERSION",
    "DEFAULT_ACCOUNT_ID",
    "LONG_CLIP_SECONDS",
    "MAX_DURATION_SECONDS",
    "MAX_SOURCE_BYTES",
    "MEDIA_TYPE_VIDEO",
    "SOURCE_ONEDRIVE",
    "STATE_DONE",
    "STATE_FAILED",
    "STATE_PENDING",
    "STATE_SKIPPED",
    "VideoAssetError",
    "build_asset",
    "cache_key",
    "content_key",
    "is_video",
    "orientation_of",
]
