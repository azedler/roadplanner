"""Prepare frame assets and build the ffmpeg filter graph for the trip video.

Pure(-ish): this module does local file I/O into a caller-supplied working
directory (writing prepared frame images) but never touches the network and
never invokes ffmpeg itself - the actual subprocess call lives in
ffmpeg_runner.py / trip_video_export.py. Keeping the filter-graph string
building here, separate from subprocess execution, makes it fully testable
without ever running ffmpeg.

First pass deliberately uses simple crossfades only - no Ken-Burns pan/zoom
yet. Motion is a planned follow-up once the end-to-end pipeline (this
module, ffmpeg_runner.py, and the orchestration in trip_video_export.py) is
proven to actually produce a playable video.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import io
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# Last concrete frame-decoding failure. Downloading and decoding are two
# separate steps: bytes can pass every download check and still not open,
# and that second failure had no record anywhere.
LAST_FRAME_ERROR: dict[str, str] = {}


def _frame_format_name(data: bytes) -> str:
    """Name the arriving format without importing the async photo module.

    This module stays free of package-internal imports so it can be loaded
    and tested on its own; the sniffing itself is a two-line fallback.
    """
    try:
        from .trip_export_photos import _format_name
    except ImportError:
        return "unbekanntes Format"
    return _format_name(data)


def _record_frame_error(reason: str) -> None:
    LAST_FRAME_ERROR["reason"] = reason[:300]
    _LOGGER.warning("Video frame could not be decoded: %s", reason[:300])


VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
DEFAULT_FPS = 30
DEFAULT_PHOTO_HOLD_SECONDS = 3.5
DEFAULT_CROSSFADE_SECONDS = 0.8
MAX_CHAPTERS_RENDERED = 40


@dataclass
class VideoChapter:
    title: str
    date: str
    narrative: str = ""
    map_snapshot: bytes | None = None
    photos: list[bytes] = field(default_factory=list)


@dataclass
class TripVideoData:
    title: str
    start_date: str
    end_date: str
    chapters: list[VideoChapter] = field(default_factory=list)
    resolution: tuple[int, int] = (VIDEO_WIDTH, VIDEO_HEIGHT)
    fps: int = DEFAULT_FPS
    photo_hold_seconds: float = DEFAULT_PHOTO_HOLD_SECONDS
    crossfade_seconds: float = DEFAULT_CROSSFADE_SECONDS


def _decode_and_fit(image_bytes: bytes | None, target_w: int, target_h: int):
    """Decode, validate, and "cover"-fit an image onto the target canvas.

    Returns a Pillow RGB Image sized exactly target_w x target_h, or None if
    the bytes are missing/corrupt/truncated - the same defensive contract as
    trip_pdf.py's _decode_photo. A frame that fails to decode is simply
    skipped by the caller, never a crash and never a filler placeholder.
    """
    from PIL import Image

    if not image_bytes:
        return None
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        image = image.convert("RGB")
    except Exception as err:  # noqa: BLE001 - a corrupt/unsupported/truncated frame must not abort the render
        # The DOWNLOAD errors were already reported; a failure here happens
        # afterwards and had no record at all, so the export could only say
        # "kein konkreter Fehler erfasst" while every single frame failed
        # (live report). Name the format, the size and the exception.
        _record_frame_error(
            f"{_frame_format_name(image_bytes)}, {len(image_bytes)} Bytes: "
            f"{type(err).__name__}: {err}"
        )
        return None
    if image.width <= 0 or image.height <= 0:
        return None
    scale = max(target_w / image.width, target_h / image.height)
    new_w, new_h = max(1, round(image.width * scale)), max(1, round(image.height * scale))
    image = image.resize((new_w, new_h))
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return image.crop((left, top, left + target_w, top + target_h))


def prepare_chapter_assets(data: TripVideoData, workdir: Path) -> list[Path]:
    """Decode/fit every usable frame into numbered JPEGs in ``workdir``.

    Iterates chapters in order; within a chapter, the map snapshot (if any)
    comes first, then its photos. A chapter that contributes zero usable
    frames (no snapshot, no decodable photo) simply produces nothing - the
    same "no filler" philosophy already used for the PDF's day pages.
    Returns the ordered list of frame file paths ffmpeg will consume.
    """
    target_w, target_h = data.resolution
    frame_paths: list[Path] = []
    index = 0
    for chapter in data.chapters[:MAX_CHAPTERS_RENDERED]:
        candidates: list[bytes] = []
        if chapter.map_snapshot:
            candidates.append(chapter.map_snapshot)
        candidates.extend(chapter.photos)
        for raw in candidates:
            fitted = _decode_and_fit(raw, target_w, target_h)
            if fitted is None:
                continue
            path = workdir / f"frame_{index:04d}.jpg"
            fitted.save(path, format="JPEG", quality=90)
            frame_paths.append(path)
            index += 1
    return frame_paths


def build_ffmpeg_filter_graph(
    data: TripVideoData, frame_paths: list[Path]
) -> tuple[list[str], str, str]:
    """Return (input_args, filter_complex, output_video_label).

    Each still is looped for ``photo_hold_seconds`` and crossfades into the
    next over ``crossfade_seconds`` via ffmpeg's ``xfade`` filter, chained
    pairwise. With equal-duration clips of ``hold + crossfade`` seconds, the
    standard chained-xfade offset for the transition after the (i+1)-th clip
    is simply ``(i+1) * hold`` seconds - each clip's own extra ``crossfade``
    tail is exactly what the next transition consumes.

    Returns ``([], "", "")`` if there are no frames at all - the caller must
    handle a chapter-less video as an explicit error, not by invoking
    ffmpeg with zero inputs.
    """
    if not frame_paths:
        return [], "", ""

    fps = data.fps
    hold = data.photo_hold_seconds
    xfade = data.crossfade_seconds
    count = len(frame_paths)
    clip_duration = hold + xfade if count > 1 else hold

    input_args: list[str] = []
    for path in frame_paths:
        input_args += ["-loop", "1", "-t", f"{clip_duration:.3f}", "-i", str(path)]

    normalize_parts = [
        f"[{i}:v]format=yuv420p,fps={fps}[n{i}]" for i in range(count)
    ]
    if count == 1:
        return input_args, ";".join(normalize_parts), "[n0]"

    chain_parts = list(normalize_parts)
    prev_label = "n0"
    for i in range(1, count):
        offset = i * hold
        out_label = f"v{i}" if i < count - 1 else "vout"
        chain_parts.append(
            f"[{prev_label}][n{i}]xfade=transition=fade:"
            f"duration={xfade:.3f}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
    return input_args, ";".join(chain_parts), f"[{prev_label}]"
