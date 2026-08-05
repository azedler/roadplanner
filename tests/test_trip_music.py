"""The trip video's music is generated, not downloaded.

Live question: "Du kannst keine lizenzfreie Musik besorgen?" - no. The
build environment cannot reach any audio source, and bundling a file found
somewhere would mean trusting a licence claim nobody here can verify.

Synthesising the bed sidesteps the question entirely: nobody else holds
rights in a chord pad this module describes, there is no attribution to
carry and no licence file to keep in sync. A real track dropped into
assets/music still wins.

Pure string building - the graph is verified without running ffmpeg, the
same way the video's filter graph is.
"""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

MODULE_PATH = Path("custom_components/roadplanner_mcp/trip_music.py")
spec = spec_from_file_location("roadplanner_trip_music_test", MODULE_PATH)
assert spec and spec.loader
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

PACKAGE_ROOT = Path("custom_components/roadplanner_mcp")


def verify_the_graph_describes_a_complete_playable_bed() -> None:
    inputs, graph, label = module.build_music_graph("trip-1", first_input_index=0)
    assert label == "[music]"
    # Four voices per chord (bass + triad), four chords.
    assert inputs.count("-i") == 16, inputs.count("-i")
    assert all(part.startswith("sine=frequency=") for part in inputs if "sine=" in part)
    assert "concat=n=4:v=0:a=1" in graph
    # Looped, so any video length is covered, and trimmed by the caller's
    # -shortest rather than by guessing the duration here.
    assert "aloop=loop=-1:size=705600" in graph, graph
    # A predictable level: background music must neither vanish nor shout.
    assert "loudnorm=I=-24:TP=-3" in graph
    assert "afade=t=in" in graph and "afade=t=out" in graph


def verify_inputs_are_numbered_from_the_callers_offset() -> None:
    # The image inputs come first; the music must not claim their indices.
    _inputs, graph, _label = module.build_music_graph("trip-1", first_input_index=7)
    assert "[7:a]volume=" in graph
    assert "[0:a]volume=" not in graph, "index 0 belongs to the first photo"


def verify_the_key_is_stable_per_trip_and_varies_between_trips() -> None:
    assert module.root_frequency("trip-1") == module.root_frequency("trip-1")
    keys = {module.root_frequency(f"trip-{index}") for index in range(40)}
    assert len(keys) > 1, "every trip would otherwise get the identical bed"
    assert keys <= set(module._ROOT_CHOICES)


def verify_a_real_track_still_wins() -> None:
    source = (PACKAGE_ROOT / "trip_video_export.py").read_text(encoding="utf-8")
    assert "if music_path is not None:" in source
    assert source.index("if music_path is not None:") < source.index(
        "build_music_graph("
    ), "a file in assets/music is used INSTEAD of the generated bed"
    # And the video always ends up with an audio stream either way.
    assert '"-map",\n                audio_map,' in source or '"-map", audio_map' in source
    assert '"-shortest"' in source


def verify_nothing_is_downloaded_for_it() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in ("http://", "https://", "requests", "aiohttp", "urlopen"):
        assert forbidden not in source, (
            f"the bed is synthesised, never fetched: {forbidden}"
        )


if __name__ == "__main__":
    verify_the_graph_describes_a_complete_playable_bed()
    verify_inputs_are_numbered_from_the_callers_offset()
    verify_the_key_is_stable_per_trip_and_varies_between_trips()
    verify_a_real_track_still_wins()
    verify_nothing_is_downloaded_for_it()
    print("Trip music generation tests passed.")
