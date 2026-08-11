"""The proxy has to be encodable, which is a fact about ffmpeg.

Live report, and the reason this file runs the real encoder rather than
reading the argument list:

    [libx264] height not divisible by 2 (202x359)
    Conversion failed!

Eleven windows, every one a portrait phone recording, every one refused
before a single frame was encoded. The filter said
`scale=-2:360:force_original_aspect_ratio=decrease`, and `-2` is ffmpeg's
way of saying "auto width, and even" - but `force_original_aspect_ratio`
treats width and height as a BOX and recomputes both, which throws that
promise away. 1080x1920 came out 202x359, and libx264 does not encode an
odd dimension.

A test that read the argument string would have been satisfied by the
broken version: it contains `-2`, it contains the height, it looks right.
The only check that catches this is the encoder itself, so these use the
production functions to build the call and then run it.
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

_PACKAGE = "roadplanner_proxy_dimensions_under_test"
_root = importlib.util.module_from_spec(
    importlib.machinery.ModuleSpec(_PACKAGE, None, is_package=True)
)
_root.__path__ = [str(INTEGRATION)]
sys.modules[_PACKAGE] = _root

proxy = importlib.import_module(f"{_PACKAGE}.video_proxy")

# What a phone actually produces, plus the shapes that break naive maths.
# The first entry is the one from the live failure.
SIZES = [
    (1080, 1920),  # portrait, the eleven that failed
    (720, 1280),  # portrait, older phone
    (886, 1920),  # portrait, cropped - odd width at the source
    (3840, 2160),  # 4K landscape
    (1920, 1080),
    (1920, 886),  # ultra-wide
    (640, 480),  # small and square-ish
]


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _make_source(target: Path, width: int, height: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi",
            "-i", f"testsrc=size={width}x{height}:rate=30:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
    )


def _dimensions(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    width, height = (int(part) for part in out.split(",")[:2])
    return width, height


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["ffmpeg", *args], capture_output=True, text=True)


def verify_every_shape_a_phone_produces_can_actually_be_encoded() -> None:
    """Both proxies, the real encoder, one temporary directory."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for width, height in SIZES:
            source = tmp / f"src-{width}x{height}.mp4"
            _make_source(source, width, height)

            for name, build in (
                ("Analyse", proxy.analysis_args),
                ("Render", proxy.render_args),
            ):
                target = tmp / f"{name}-{width}x{height}.mp4"
                result = _run(build(source, target, start=0.0, end=2.0))
                assert result.returncode == 0, (
                    f"{name}-Proxy für {width}x{height} scheiterte:\n"
                    + result.stderr[-800:]
                )
                assert target.exists() and target.stat().st_size > 0, (name, width, height)

                out_w, out_h = _dimensions(target)
                # The actual constraint libx264 enforces, checked as such.
                assert out_w % 2 == 0 and out_h % 2 == 0, (
                    f"{name}-Proxy für {width}x{height} ergab {out_w}x{out_h} - "
                    "libx264 lehnt ungerade Kantenlängen ab"
                )
                # And the aspect ratio survived, or the proxy shows a
                # different picture than the original.
                source_ratio = width / height
                out_ratio = out_w / out_h
                assert abs(source_ratio - out_ratio) < 0.02, (
                    f"{width}x{height} -> {out_w}x{out_h}: verzerrt"
                )


def verify_the_height_is_capped_and_nothing_is_blown_up() -> None:
    """A proxy larger than the original is bytes nobody needs."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        source = tmp / "src.mp4"
        _make_source(source, 1080, 1920)
        target = tmp / "analysis.mp4"
        assert _run(proxy.analysis_args(source, target, start=0.0, end=2.0)).returncode == 0
        _out_w, out_h = _dimensions(target)
        assert out_h <= proxy.ANALYSIS_HEIGHT, out_h


def verify_the_filter_states_the_divisibility_itself() -> None:
    """Belt and braces, for a machine without ffmpeg.

    The runtime check above is the real one. This exists so that removing
    the flag still fails somewhere.
    """
    args = proxy.analysis_args(Path("a.mp4"), Path("b.mp4"), start=0.0, end=5.0)
    joined = " ".join(args)
    assert "force_divisible_by=2" in joined, joined
    render = " ".join(proxy.render_args(Path("a.mp4"), Path("b.mp4"), start=0.0, end=5.0))
    assert "force_divisible_by=2" in render, render
    # The combination that caused it, so it cannot come back as a pair.
    assert "scale=-2:" not in joined and "scale=-2:" not in render


def verify_both_deployables_cut_video_the_same_way() -> None:
    """The renderer already knew. The integration had to find out again.

    `apps/roadplanner_renderer/src/videoproxy.mjs` cuts its proxies with a
    plain `scale=-2:<even height>` and carries the reason in a comment
    right above it: "Even height, because H.264 cannot encode an odd one
    and the error it gives says nothing about the cause." The Python side
    added `force_original_aspect_ratio` on top of `-2`, which silently
    revokes exactly that guarantee - and eleven of a family's recordings
    were refused by the encoder.

    Two deployables cutting the same video, one of them holding the
    lesson. So this reads both and requires each to state the constraint
    in whichever way it states it.
    """
    renderer = (
        ROOT / "apps" / "roadplanner_renderer" / "src" / "videoproxy.mjs"
    ).read_text(encoding="utf-8")
    source = (INTEGRATION / "video_proxy.py").read_text(encoding="utf-8")

    # The renderer's way: round the height to an even number itself.
    assert "Math.round(height / 2) * 2" in renderer, (
        "der Renderer erzwingt die gerade Höhe nicht mehr selbst"
    )
    # The integration's way: let the scaler do it, since it also fits the
    # picture into a box. Either is fine; having neither is what happened.
    assert "force_divisible_by=2" in source

    # And the combination that revokes the guarantee is gone from both -
    # checked on the CODE, with comments stripped, because the comment
    # explaining the bug necessarily contains the broken filter and an
    # unfiltered search flags the explanation instead of the mistake.
    def code_only(body: str, marker: str) -> str:
        return "\n".join(
            line for line in body.splitlines() if not line.strip().startswith(marker)
        )

    for name, body, marker in (
        ("renderer", renderer, "//"),
        ("integration", source, "#"),
    ):
        stripped = code_only(body, marker)
        assert not (
            "force_original_aspect_ratio" in stripped and "scale=-2:" in stripped
        ), (
            f"{name}: `-2` zusammen mit force_original_aspect_ratio - "
            "die Zusicherung 'gerade Kantenlänge' gilt dann nicht mehr"
        )


verify_the_filter_states_the_divisibility_itself()
verify_both_deployables_cut_video_the_same_way()

if _ffmpeg() is None:
    print(
        "Video proxy dimension tests: ffmpeg fehlt - nur die Argumente geprüft, "
        "NICHT der Encoder. Auf einer Maschine mit ffmpeg erneut laufen lassen."
    )
else:
    verify_every_shape_a_phone_produces_can_actually_be_encoded()
    verify_the_height_is_capped_and_nothing_is_blown_up()
    print("Video proxy dimension tests passed (mit echtem ffmpeg).")
