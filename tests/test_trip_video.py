"""Behavioral tests for the trip video's pure asset-prep and filter-graph code.

No ffmpeg subprocess is invoked here - build_ffmpeg_filter_graph only builds
strings, and prepare_chapter_assets only does local Pillow decode/resize. The
actual ffmpeg invocation was manually verified separately (a real MP4 was
produced and inspected frame-by-frame, confirming correct resolution,
duration, and crossfade blending) before this test file was written.
"""
from __future__ import annotations

import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile

MODULE_PATH = Path("custom_components/roadplanner_mcp/trip_video.py")
spec = spec_from_file_location("roadplanner_trip_video_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _jpeg_bytes(color=(200, 100, 50), size=(1200, 800)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def verify_corrupt_photo_is_skipped_not_placeholdered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        data = module.TripVideoData(
            title="Reise",
            start_date="",
            end_date="",
            chapters=[
                module.VideoChapter(title="Tag 1", date="", photos=[b"not-an-image"]),
                module.VideoChapter(title="Tag 2", date="", photos=[_jpeg_bytes()]),
            ],
        )
        frame_paths = module.prepare_chapter_assets(data, workdir)
        assert len(frame_paths) == 1, "the corrupt photo must be skipped, not placeholdered"


def verify_missing_map_snapshot_is_tolerated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        data = module.TripVideoData(
            title="Reise",
            start_date="",
            end_date="",
            chapters=[
                module.VideoChapter(title="Tag 1", date="", map_snapshot=None, photos=[_jpeg_bytes()]),
            ],
        )
        frame_paths = module.prepare_chapter_assets(data, workdir)
        assert len(frame_paths) == 1


def verify_map_snapshot_is_the_first_frame_in_its_chapter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        data = module.TripVideoData(
            title="Reise",
            start_date="",
            end_date="",
            chapters=[
                module.VideoChapter(
                    title="Tag 1",
                    date="",
                    map_snapshot=_jpeg_bytes(color=(10, 10, 10)),
                    photos=[_jpeg_bytes(color=(250, 250, 250))],
                ),
            ],
        )
        frame_paths = module.prepare_chapter_assets(data, workdir)
        assert len(frame_paths) == 2
        from PIL import Image

        first_pixel = Image.open(frame_paths[0]).getpixel((5, 5))
        assert first_pixel[0] < 50, "the map snapshot must render before the chapter's photos"


def verify_frames_are_fitted_to_the_exact_target_resolution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        data = module.TripVideoData(
            title="Reise",
            start_date="",
            end_date="",
            chapters=[module.VideoChapter(title="Tag 1", date="", photos=[_jpeg_bytes(size=(400, 1600))])],
            resolution=(640, 360),
        )
        frame_paths = module.prepare_chapter_assets(data, workdir)
        from PIL import Image

        with Image.open(frame_paths[0]) as image:
            assert image.size == (640, 360)


def verify_no_frames_returns_empty_filter_graph() -> None:
    data = module.TripVideoData(title="Reise", start_date="", end_date="")
    input_args, filter_complex, label = module.build_ffmpeg_filter_graph(data, [])
    assert input_args == []
    assert filter_complex == ""
    assert label == ""


def verify_single_frame_needs_no_xfade() -> None:
    data = module.TripVideoData(title="Reise", start_date="", end_date="")
    input_args, filter_complex, label = module.build_ffmpeg_filter_graph(
        data, [Path("frame_0000.jpg")]
    )
    assert "xfade" not in filter_complex
    assert label == "[n0]"
    assert input_args.count("-i") == 1


def verify_multiple_frames_chain_the_expected_number_of_xfades() -> None:
    data = module.TripVideoData(
        title="Reise", start_date="", end_date="", photo_hold_seconds=2.0, crossfade_seconds=0.5
    )
    frame_paths = [Path(f"frame_{i:04d}.jpg") for i in range(4)]
    input_args, filter_complex, label = module.build_ffmpeg_filter_graph(data, frame_paths)
    assert input_args.count("-i") == 4
    assert filter_complex.count("xfade") == 3, "N frames need exactly N-1 crossfade transitions"
    assert label == "[vout]"
    # Chained-xfade offsets for equal-duration clips: (i+1) * hold_seconds.
    assert "offset=2.000" in filter_complex
    assert "offset=4.000" in filter_complex
    assert "offset=6.000" in filter_complex
    assert "duration=0.500" in filter_complex


verify_corrupt_photo_is_skipped_not_placeholdered()
verify_missing_map_snapshot_is_tolerated()
verify_map_snapshot_is_the_first_frame_in_its_chapter()
verify_frames_are_fitted_to_the_exact_target_resolution()
verify_no_frames_returns_empty_filter_graph()
verify_single_frame_needs_no_xfade()
verify_multiple_frames_chain_the_expected_number_of_xfades()

print("Trip video rendering tests passed.")
