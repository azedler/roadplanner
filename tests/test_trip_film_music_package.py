"""The soundtrack on its way into the render package.

Two things are being defended here and they pull in opposite directions.

The first is safety: a filename arriving from a browser must never
become a path. That is why a track is chosen by NAME and the name is
matched against the folder listing before anything is opened - and it
has to keep being true now that a generated score names up to four
files instead of one.

The second is that music is decoration. A section whose file has gone
missing must cost the film that section, not the render. A film with a
gap in its music is a film; a film that failed to render is not.
"""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
MODULE_PATH = Path("custom_components/roadplanner_mcp/trip_film_music.py")
_spec = spec_from_file_location("roadplanner_film_music_test", MODULE_PATH)
assert _spec and _spec.loader
music = module_from_spec(_spec)
sys.modules[_spec.name] = music
_spec.loader.exec_module(music)

EXPORT_SOURCE = Path("custom_components/roadplanner_mcp/trip_film_export.py").read_text(
    encoding="utf-8"
)


def _folder(names: dict[str, bytes]) -> str:
    root = tempfile.mkdtemp()
    for name, blob in names.items():
        (Path(root) / name).write_bytes(blob)
    return root


def _timeline(*names: str) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "start_seconds": index * 110.0,
            "seconds": 118.0,
            "fade_in_seconds": 2.0 if index == 0 else 4.0,
            "fade_out_seconds": 4.0,
        }
        for index, name in enumerate(names)
    ]


def verify_a_generated_score_travels_as_numbered_files() -> None:
    root = _folder({"a.mp3": b"one", "b.mp3": b"two"})
    entry, files = music.build_music_timeline_package(_timeline("a.mp3", "b.mp3"), root)
    assert entry is not None
    assert sorted(files) == ["music/section-01.mp3", "music/section-02.mp3"], files
    assert files["music/section-01.mp3"] == b"one"
    # The names in the package are derived from the POSITION, never from
    # what was sent - so no name from outside can become a path.
    for path in files:
        assert path.startswith("music/section-")
    assert len(entry["sections"]) == 2
    assert entry["sections"][1]["start_seconds"] == 110.0
    # A renderer that only understands one track still plays music.
    assert entry["path"] == "music/section-01.mp3"
    assert entry["sha256"] == entry["sections"][0]["sha256"]


def verify_a_missing_section_costs_a_section_not_the_film() -> None:
    root = _folder({"a.mp3": b"one"})
    entry, files = music.build_music_timeline_package(
        _timeline("a.mp3", "deleted-since.mp3"), root
    )
    assert entry is not None, "eine fehlende Datei darf den Film nicht kosten"
    assert len(entry["sections"]) == 1
    assert len(files) == 1


def verify_nothing_generated_means_no_music_at_all() -> None:
    root = _folder({})
    assert music.build_music_timeline_package(_timeline("a.mp3"), root) == (None, {})
    assert music.build_music_timeline_package([], root) == (None, {})


def verify_a_name_from_outside_never_becomes_a_path() -> None:
    """The whole defence, restated for the sectioned case."""
    root = _folder({"a.mp3": b"one"})
    for hostile in (
        "../../etc/passwd",
        "/etc/passwd",
        "a.mp3/../../../etc/passwd",
        "./a.mp3",
    ):
        entry, files = music.build_music_timeline_package(_timeline(hostile), root)
        assert entry is None, hostile
        assert files == {}, hostile


def verify_only_four_sections_can_ever_travel() -> None:
    root = _folder({f"{index}.mp3": b"x" for index in range(9)})
    entry, files = music.build_music_timeline_package(
        _timeline(*[f"{index}.mp3" for index in range(9)]), root
    )
    assert len(entry["sections"]) == music.MAX_MUSIC_SECTIONS
    assert len(files) == music.MAX_MUSIC_SECTIONS


def verify_the_film_can_play_a_score_but_never_order_one() -> None:
    """The rule the whole music architecture rests on.

    The exporter is handed one read-only question, not the service. If
    a future edit passed the service in "for convenience", rendering a
    film would gain a route to a paid call - which is exactly the shape
    of accident this separation exists to prevent.
    """
    assert "music_timeline" in EXPORT_SOURCE
    assert "async_generate" not in EXPORT_SOURCE, (
        "der Filmexport darf keine erzeugende Methode kennen"
    )
    assert "TripFilmMusicService" not in EXPORT_SOURCE
    # And the reserved name is compared, never joined onto a folder.
    assert 'GENERATED_MUSIC = "__generated__"' in EXPORT_SOURCE
    assert "!= GENERATED_MUSIC" in EXPORT_SOURCE


for check in (
    verify_a_generated_score_travels_as_numbered_files,
    verify_a_missing_section_costs_a_section_not_the_film,
    verify_nothing_generated_means_no_music_at_all,
    verify_a_name_from_outside_never_becomes_a_path,
    verify_only_four_sections_can_ever_travel,
    verify_the_film_can_play_a_score_but_never_order_one,
):
    check()

print("Trip film music package tests passed.")
