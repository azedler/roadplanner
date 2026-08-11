"""A rotated recording is not the shape its file header claims.

A phone held upright whose camera writes a rotation matrix stores
1920x1080 and displays 1080x1920. Both describe the same rectangle; only
one of them is what anybody sees.

Measured here with a real ffmpeg before anything was changed, because
the expected failure and the actual one were not the same:

    source  1920x1080 with rotation=90
    proxy   202x360

**ffmpeg applies the matrix itself.** The proxies were right all along.
What was wrong were the numbers stored beside them - the technical
prefilter judged `height`, which for such a file names the picture's
WIDTH, and the render package copied the library record's dimensions into
a clip entry describing a file of the opposite shape.

So there are two rules here, and neither of them parses metadata:

- resolution is judged on the SHORTER edge, which is the same number
  whichever way round the file was written;
- the package states what the cut file measures, rather than what the
  record remembered.

Both are checked against a real encoder where one is available.
"""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "roadplanner_mcp"
sys.dont_write_bytecode = True

_PACKAGE = "roadplanner_rotation_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

prefilter = importlib.import_module(f"{_PACKAGE}.video_prefilter")
proxy = importlib.import_module(f"{_PACKAGE}.video_proxy")

EXPORT = (INTEGRATION / "trip_film_export.py").read_text(encoding="utf-8")


def _tools() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _source(target: Path, width: int, height: int, rotation: int = 0) -> None:
    """A recording, optionally carrying a display matrix like a phone's."""
    plain = target.with_name(f"plain-{target.name}")
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size={width}x{height}:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain),
        ],
        check=True, capture_output=True,
    )
    if not rotation:
        plain.replace(target)
        return
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-display_rotation", str(rotation),
            "-i", str(plain), "-c", "copy", str(target),
        ],
        check=True, capture_output=True,
    )


def _shape(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    width, height = (int(part) for part in out.split(",")[:2])
    return width, height


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", *args], capture_output=True, text=True)


# --- the rule that needs no metadata at all -----------------------------


def verify_resolution_is_judged_on_the_shorter_edge() -> None:
    """The same picture, written both ways round, gets the same verdict."""
    upright = {"duration_seconds": 8.0, "width": 1080, "height": 1920}
    rotated_header = {"duration_seconds": 8.0, "width": 1920, "height": 1080}
    landscape = {"duration_seconds": 8.0, "width": 1920, "height": 1080}
    for asset in (upright, rotated_header, landscape):
        assert prefilter.technical_verdict(asset)["usable"] is True, asset

    # And something genuinely too small is still refused, either way up.
    for asset in (
        {"duration_seconds": 8.0, "width": 640, "height": 360},
        {"duration_seconds": 8.0, "width": 360, "height": 640},
    ):
        found = prefilter.technical_verdict(asset)
        assert found["usable"] is False, asset
        assert found["reason"] == prefilter.REASON_TOO_SMALL, found


def verify_the_short_side_helper_survives_missing_numbers() -> None:
    """A record with no dimensions must not be refused for having none."""
    assert prefilter.short_side({}) is None
    assert prefilter.short_side({"width": 0, "height": 0}) is None
    assert prefilter.short_side({"height": 720}) == 720
    assert prefilter.short_side({"width": 1920, "height": 1080}) == 1080
    # A boolean is not a dimension.
    assert prefilter.short_side({"width": True, "height": 1080}) == 1080


def verify_the_package_states_what_it_measured() -> None:
    """Not what the library remembered about the original."""
    assert "async_probe_shape(target)" in EXPORT
    assert '"width": clip_width' in EXPORT
    assert '"height": clip_height' in EXPORT
    assert 'int(record.get("width") or 0)' not in EXPORT


# --- with a real encoder ------------------------------------------------


def verify_every_orientation_including_rotated_cuts_correctly() -> None:
    """Four shapes through both production proxy calls."""
    cases = [
        ("landscape", 1920, 1080, 0, False),
        ("portrait", 1080, 1920, 0, True),
        ("landscape + rotation", 1920, 1080, 180, False),
        ("portrait via rotation", 1920, 1080, 90, True),
    ]
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for label, width, height, rotation, expect_upright in cases:
            source = tmp / f"{label.replace(' ', '_').replace('+', '')}.mp4"
            _source(source, width, height, rotation)

            for name, build in (
                ("Analyse", proxy.analysis_args),
                ("Render", proxy.render_args),
            ):
                target = tmp / f"{name}-{source.name}"
                result = _run(build(source, target, start=0.0, end=2.0))
                assert result.returncode == 0, (label, name, result.stderr[-500:])

                out_w, out_h = _shape(target)
                assert out_w % 2 == 0 and out_h % 2 == 0, (label, out_w, out_h)
                # The picture anybody sees, not the rectangle on disk.
                assert (out_h > out_w) is expect_upright, (
                    f"{label} -> {out_w}x{out_h}: Ausrichtung stimmt nicht"
                )


def verify_the_probe_reports_what_the_file_really_is() -> None:
    """The measurement the package now relies on, against real files."""
    import asyncio

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        rotated = tmp / "rotated.mp4"
        _source(rotated, 1920, 1080, 90)
        cut = tmp / "cut.mp4"
        assert _run(proxy.render_args(rotated, cut, start=0.0, end=2.0)).returncode == 0

        measured = asyncio.run(proxy.async_probe_shape(cut))
        assert measured == _shape(cut), measured
        # The point of the whole exercise: upright, while the record that
        # produced it says 1920x1080.
        assert measured[1] > measured[0], measured


def verify_the_probe_answers_zero_rather_than_guessing() -> None:
    """A dimension nobody could read is better absent than invented."""
    import asyncio

    with tempfile.TemporaryDirectory() as raw:
        missing = Path(raw) / "nothing.mp4"
        assert asyncio.run(proxy.async_probe_shape(missing)) == (0, 0)


for check in (
    verify_resolution_is_judged_on_the_shorter_edge,
    verify_the_short_side_helper_survives_missing_numbers,
    verify_the_package_states_what_it_measured,
):
    check()

if not _tools():
    print(
        "Video rotation tests: ffmpeg/ffprobe fehlen - nur die Regeln geprüft, "
        "NICHT der Encoder. Auf einer Maschine mit ffmpeg erneut laufen lassen."
    )
else:
    verify_every_orientation_including_rotated_cuts_correctly()
    verify_the_probe_reports_what_the_file_really_is()
    verify_the_probe_answers_zero_rather_than_guessing()
    print("Video rotation tests passed (mit echtem ffmpeg).")
