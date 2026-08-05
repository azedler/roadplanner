"""Prepare frame assets and build the ffmpeg filter graph for the trip video.

Pure(-ish): this module does local file I/O into a caller-supplied working
directory (writing prepared frame images) but never touches the network and
never invokes ffmpeg itself - the actual subprocess call lives in
ffmpeg_runner.py / trip_video_export.py. Keeping the filter-graph string
building here, separate from subprocess execution, makes it fully testable
without ever running ffmpeg.

Stills crossfade into each other and the first frame of every chapter
carries its title, date and story line. A slideshow of photos with nothing
written on it is what made the first version feel like a screensaver (live
report: "Das Video war jetzt ziemlich langweilig") - the narrative Gemini
writes per chapter was computed and then thrown away.

Ken-Burns motion was built and then removed again: zoompan re-renders every
output frame, and a three-chapter test took over six minutes, which would
run into the export's ffmpeg timeout on a Home Assistant box.
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
DEFAULT_PHOTO_HOLD_SECONDS = 2.8
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


_FONT_PATH = Path(__file__).parent / "assets" / "fonts" / "DejaVuSans-Bold.ttf"


def _caption_for(chapter: VideoChapter) -> str:
    """The line burned onto a chapter's first frame.

    The narrative Gemini writes per chapter was computed and then thrown
    away - the video showed photos and nothing else, which is exactly why
    it felt like a screensaver (live report: "Das Video war jetzt ziemlich
    langweilig").
    """
    title = " ".join(str(chapter.title or "").split())[:70]
    date = str(chapter.date or "")
    narrative = " ".join(str(chapter.narrative or "").split())[:110]
    head = " · ".join(part for part in (title, date) if part)
    return "\n".join(part for part in (head, narrative) if part)


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
        first_of_chapter = True
        for raw in candidates:
            fitted = _decode_and_fit(raw, target_w, target_h)
            if fitted is None:
                continue
            if first_of_chapter:
                _draw_caption(fitted, _caption_for(chapter))
                first_of_chapter = False
            path = workdir / f"frame_{index:04d}.jpg"
            fitted.save(path, format="JPEG", quality=90)
            frame_paths.append(path)
            index += 1
    return frame_paths


def _draw_caption(image, caption: str) -> None:
    """Burn the chapter caption onto a frame - readable over any photo.

    Done with Pillow while the frames are prepared, not with ffmpeg's
    drawtext: the text then needs no escaping at all, and a caption from
    Gemini can contain anything.
    """
    if not caption:
        return
    try:
        from PIL import ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - Pillow is a hard requirement
        return
    lines = [line for line in caption.split("\n") if line.strip()]
    if not lines:
        return
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        title_font = ImageFont.truetype(str(_FONT_PATH), size=max(28, width // 30))
        body_font = ImageFont.truetype(str(_FONT_PATH), size=max(20, width // 46))
    except OSError:
        title_font = body_font = ImageFont.load_default()
    fonts = [title_font] + [body_font] * (len(lines) - 1)
    heights = [
        draw.textbbox((0, 0), line, font=font)[3] for line, font in zip(lines, fonts)
    ]
    padding = max(16, width // 60)
    block = sum(heights) + padding * (len(lines) + 1)
    # A soft dark band, not a hard box: the photo stays visible through it.
    draw.rectangle((0, height - block, width, height), fill=(15, 46, 61, 170))
    y = height - block + padding
    for line, font, line_height in zip(lines, fonts, heights):
        draw.text((padding * 2, y), line, font=font, fill=(255, 255, 255, 235))
        y += line_height + padding


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

    # NO Ken-Burns. It was built and measured: zoompan re-renders every
    # output frame, and a three-chapter test took over six minutes here -
    # straight into the export's ffmpeg timeout on a Home Assistant box.
    # The liveliness comes from content instead (chapter captions, a title
    # card, shorter holds), which costs nothing at render time.
    normalize_parts = [
        f"[{i}:v]format=yuv420p,fps={fps},setsar=1[n{i}]" for i in range(count)
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
